"""
Tests für den ToolProgressTracker (Stuck-Detection).
"""

import pytest
from app.agent.tool_progress import (
    ToolProgressTracker,
    StuckReason,
    get_progress_tracker,
    reset_progress_tracker,
)
from app.agent.tools import ToolResult


class TestToolProgressTracker:
    """Tests für ToolProgressTracker."""

    def setup_method(self):
        """Vor jedem Test: Neuen Tracker erstellen."""
        self.tracker = ToolProgressTracker()

    def test_init(self):
        """Tracker wird korrekt initialisiert."""
        assert self.tracker._current_iteration == 0
        assert len(self.tracker._state.call_signatures) == 0
        assert len(self.tracker._state.knowledge_gained) == 0

    def test_single_call_no_stuck(self):
        """Ein einzelner Call ist nicht stuck."""
        result = ToolResult(success=True, data="Found: class UserService")

        stuck = self.tracker.record_call(
            tool_name="search_code",
            args={"query": "UserService"},
            result=result,
            iteration=0
        )

        assert not stuck.is_stuck
        assert stuck.reason is None

    def test_repeated_call_stuck(self):
        """3x gleicher Call mit gleichem Ergebnis → STUCK."""
        result = ToolResult(success=True, data="Found: class UserService in UserService.java")
        args = {"query": "UserService"}

        # 1. Call
        stuck1 = self.tracker.record_call("search_code", args, result, 0)
        assert not stuck1.is_stuck

        # 2. Call (gleich)
        stuck2 = self.tracker.record_call("search_code", args, result, 1)
        assert not stuck2.is_stuck

        # 3. Call (gleich) → STUCK
        stuck3 = self.tracker.record_call("search_code", args, result, 2)
        assert stuck3.is_stuck
        assert stuck3.reason == StuckReason.REPEATED_CALL
        assert stuck3.repeated_count == 3

    def test_different_args_no_stuck(self):
        """Verschiedene Args → kein Stuck."""
        result = ToolResult(success=True, data="Found something")

        self.tracker.record_call("search_code", {"query": "A"}, result, 0)
        self.tracker.record_call("search_code", {"query": "B"}, result, 1)
        stuck = self.tracker.record_call("search_code", {"query": "C"}, result, 2)

        assert not stuck.is_stuck

    def test_different_results_no_stuck(self):
        """Verschiedene Results → kein Stuck."""
        args = {"query": "test"}

        self.tracker.record_call("search_code", args,
            ToolResult(success=True, data="Result A"), 0)
        self.tracker.record_call("search_code", args,
            ToolResult(success=True, data="Result B"), 1)
        stuck = self.tracker.record_call("search_code", args,
            ToolResult(success=True, data="Result C"), 2)

        assert not stuck.is_stuck

    def test_empty_results_streak(self):
        """3x leere Ergebnisse im ähnlichen Kontext → STUCK."""
        empty_result = ToolResult(success=True, data="Keine Treffer gefunden")

        # Ähnliche Suchbegriffe = gleicher Kontext → Streak wird gezählt
        # Verschiedene Args um REPEATED_CALL zu vermeiden
        self.tracker.record_call("search_code", {"query": "OrderService"}, empty_result, 0)
        self.tracker.record_call("search_code", {"query": "OrderService", "path": "src"}, empty_result, 1)
        stuck = self.tracker.record_call("search_code", {"query": "OrderService", "path": "test"}, empty_result, 2)

        assert stuck.is_stuck
        # Kann REPEATED_CALL oder EMPTY_RESULTS sein - beides ist valides Stuck
        assert stuck.reason in (StuckReason.EMPTY_RESULTS, StuckReason.REPEATED_CALL)

    def test_empty_results_different_context_no_stuck(self):
        """Leere Ergebnisse in verschiedenen Kontexten → NICHT STUCK."""
        empty_result = ToolResult(success=True, data="Keine Treffer gefunden")

        # Unterschiedliche Suchbegriffe = unterschiedliche Kontexte
        self.tracker.record_call("search_code", {"query": "OrderService"}, empty_result, 0)
        self.tracker.record_call("search_code", {"query": "PaymentService"}, empty_result, 1)
        stuck = self.tracker.record_call("search_code", {"query": "UserService"}, empty_result, 2)

        # Verschiedene Kontexte → kein Stuck
        assert not stuck.is_stuck

    def test_empty_streak_reset_on_success(self):
        """Erfolgreiche Ergebnisse resetten den Empty-Streak."""
        empty_result = ToolResult(success=True, data="Keine Treffer")
        good_result = ToolResult(success=True, data="Found: class FooBar with method test()")

        self.tracker.record_call("search_code", {"query": "X"}, empty_result, 0)
        self.tracker.record_call("search_code", {"query": "Y"}, empty_result, 1)
        # Erfolg resettet den Streak
        self.tracker.record_call("search_code", {"query": "Z"}, good_result, 2)
        # Weitere leere Ergebnisse
        self.tracker.record_call("search_code", {"query": "A"}, empty_result, 3)
        stuck = self.tracker.record_call("search_code", {"query": "B"}, empty_result, 4)

        # Nur 2 leere nach dem Reset → noch nicht stuck
        assert not stuck.is_stuck

    def test_knowledge_extraction_code(self):
        """Wissen wird aus Code-Ergebnissen extrahiert."""
        result = ToolResult(
            success=True,
            data="""
            Found in UserService.java:
            class UserService implements IUserService {
                public User getUserById(String id) {
                    return repo.findById(id);
                }
            }
            """
        )

        self.tracker.record_call("search_code", {"query": "getUserById"}, result, 0)

        knowledge = self.tracker._state.knowledge_gained
        assert any("class:UserService" in k for k in knowledge)
        assert any("file:" in k for k in knowledge)

    def test_knowledge_extraction_confluence(self):
        """Wissen wird aus Confluence-Ergebnissen extrahiert."""
        result = ToolResult(
            success=True,
            data='Found pages: ID: 123456, Title: "API Docs", ID: 789012, Title: "Setup"'
        )

        self.tracker.record_call("search_confluence", {"query": "API"}, result, 0)

        knowledge = self.tracker._state.knowledge_gained
        assert any("confluence:123456" in k for k in knowledge)
        assert any("confluence:789012" in k for k in knowledge)

    def test_no_progress_stuck(self):
        """5+ Iterationen ohne neues Wissen → STUCK."""
        # Ergebnis ohne extrahierbares Wissen
        boring_result = ToolResult(success=True, data="Some text without patterns")

        # Brauchen 6 Iterationen: 0-5, damit Iteration 5 - last_progress(0) = 5 >= Threshold
        for i in range(ToolProgressTracker.NO_PROGRESS_THRESHOLD + 1):
            stuck = self.tracker.record_call(
                "search_handbook",
                {"query": f"query_{i}"},
                boring_result,
                i
            )

        assert stuck.is_stuck
        assert stuck.reason == StuckReason.NO_PROGRESS

    def test_cyclic_pattern_detection(self):
        """A→B→A→B Muster wird erkannt."""
        # Jedes Ergebnis enthält neues Wissen damit NO_PROGRESS nicht triggert
        def make_result(i):
            return ToolResult(success=True, data=f"class Class{i} in file{i}.java")

        # Echter Zyklus: GLEICHE Tool+Args wiederholt (A→B→A→B mit identischen Args)
        # Seit v2.10.37 wird args_hash berücksichtigt - unterschiedliche Args = kein Zyklus
        calls = [
            ("search_code", {"q": "test"}),
            ("read_file", {"p": "file.java"}),
            ("search_code", {"q": "test"}),      # Gleiche Args wie #0
            ("read_file", {"p": "file.java"}),   # Gleiche Args wie #1
            ("search_code", {"q": "test"}),      # Gleiche Args wie #0, #2
            ("read_file", {"p": "file.java"}),   # Gleiche Args wie #1, #3
            ("search_code", {"q": "test"}),
            ("read_file", {"p": "file.java"}),
        ]

        stuck = None
        for i, (tool, args) in enumerate(calls):
            stuck = self.tracker.record_call(tool, args, make_result(i), i)

        assert stuck.is_stuck
        assert stuck.reason == StuckReason.CYCLIC_PATTERN

    def test_reset(self):
        """Reset setzt alle State zurück."""
        result = ToolResult(success=True, data="test")
        self.tracker.record_call("search_code", {"q": "test"}, result, 0)
        self.tracker.record_call("search_code", {"q": "test"}, result, 1)

        assert len(self.tracker._state.call_signatures) == 2

        self.tracker.reset()

        assert len(self.tracker._state.call_signatures) == 0
        assert self.tracker._current_iteration == 0

    def test_progress_summary(self):
        """get_progress_summary gibt korrekte Daten zurück."""
        result = ToolResult(success=True, data="class MyClass in file.java")

        self.tracker.record_call("search_code", {"q": "1"}, result, 0)
        self.tracker.record_call("read_file", {"p": "a"}, result, 1)

        summary = self.tracker.get_progress_summary()

        assert summary["total_calls"] == 2
        assert summary["current_iteration"] == 1
        assert "search_code" in summary["recent_tools"]
        assert "read_file" in summary["recent_tools"]

    def test_stuck_hint_format(self):
        """get_hint() gibt korrekten Format zurück."""
        result = ToolResult(success=True, data="same")
        args = {"query": "test"}

        for i in range(3):
            stuck = self.tracker.record_call("search_code", args, result, i)

        hint = stuck.get_hint()

        assert "## LOOP ERKANNT" in hint
        assert "Grund" in hint
        assert "Empfehlung" in hint
        assert "Optionen" in hint


class TestProgressTrackerSingleton:
    """Tests für die Singleton-Verwaltung."""

    def test_get_same_tracker(self):
        """Gleiche Session-ID → gleicher Tracker."""
        tracker1 = get_progress_tracker("session-1")
        tracker2 = get_progress_tracker("session-1")

        assert tracker1 is tracker2

    def test_get_different_trackers(self):
        """Verschiedene Session-IDs → verschiedene Tracker."""
        tracker1 = get_progress_tracker("session-a")
        tracker2 = get_progress_tracker("session-b")

        assert tracker1 is not tracker2

    def test_reset_tracker(self):
        """Reset setzt den Tracker zurück."""
        tracker = get_progress_tracker("session-reset-test")
        result = ToolResult(success=True, data="test")
        tracker.record_call("test", {}, result, 0)

        reset_progress_tracker("session-reset-test")

        # Hole Tracker erneut - sollte reset sein
        tracker_after = get_progress_tracker("session-reset-test")
        assert len(tracker_after._state.call_signatures) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
