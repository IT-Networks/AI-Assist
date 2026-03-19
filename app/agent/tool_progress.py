"""
Tool Progress Tracker - Erkennt Endlosschleifen und Stuck-Situationen.

Features:
- Erkennung von wiederholten Tool-Calls mit gleichen Args/Results
- Knowledge-Tracking: Wurde neues Wissen gewonnen?
- Zyklische Pattern-Erkennung (A→B→A→B)
- Generierung von Stuck-Hinweisen für das LLM
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from app.agent.tools import ToolResult

logger = logging.getLogger(__name__)


class StuckReason(str, Enum):
    """Gründe für Stuck-Erkennung."""
    REPEATED_CALL = "repeated_call"  # Gleicher Call 3x mit gleichem Ergebnis
    NO_PROGRESS = "no_progress"  # Keine neuen Erkenntnisse über N Iterationen
    CYCLIC_PATTERN = "cyclic_pattern"  # A→B→A→B Pattern erkannt
    EMPTY_RESULTS = "empty_results"  # Mehrfach leere Ergebnisse


@dataclass
class ToolCallSignature:
    """Signatur eines Tool-Calls für Vergleiche."""
    tool_name: str
    args_hash: str  # MD5 der sortierten Args
    result_hash: str  # MD5 der ersten 500 Zeichen des Results
    result_preview: str  # Erste 100 Zeichen für Debugging
    iteration: int
    timestamp: float = 0.0

    def matches(self, other: "ToolCallSignature") -> bool:
        """Prüft ob zwei Signaturen gleich sind (Tool + Args + Result)."""
        return (
            self.tool_name == other.tool_name
            and self.args_hash == other.args_hash
            and self.result_hash == other.result_hash
        )


@dataclass
class StuckDetectionResult:
    """Ergebnis der Stuck-Prüfung."""
    is_stuck: bool
    reason: Optional[StuckReason] = None
    details: str = ""
    suggestion: str = ""
    repeated_count: int = 0

    def get_hint(self) -> str:
        """Generiert einen Hinweis für den System-Prompt."""
        if not self.is_stuck:
            return ""

        hint_parts = [
            "## LOOP ERKANNT",
            "",
            f"**Grund**: {self.details}",
            "",
            "**Empfehlung**:",
            self.suggestion,
            "",
            "**Optionen**:",
            "1. Versuche andere/spezifischere Suchbegriffe",
            "2. Nutze bereits gefundene Informationen weiter",
            "3. Fasse zusammen was du bisher weißt und frage den User",
            "4. Wenn die Aufgabe unlösbar erscheint, erkläre warum",
        ]
        return "\n".join(hint_parts)


@dataclass
class ProgressState:
    """Fortschritts-Status für eine Session."""
    call_signatures: List[ToolCallSignature] = field(default_factory=list)
    knowledge_gained: Set[str] = field(default_factory=set)  # Unique Findings
    stuck_counter: int = 0
    last_progress_iteration: int = 0
    empty_result_streak: int = 0  # Aufeinanderfolgende leere Ergebnisse


class ToolProgressTracker:
    """
    Trackt Tool-Aufrufe und erkennt Stuck-Situationen.

    Stuck-Detection Logik:
    1. Gleiche Tool + Args + Result 3x → STUCK (REPEATED_CALL)
    2. 5 Iterationen ohne neues Wissen → STUCK (NO_PROGRESS)
    3. Zyklische Pattern-Erkennung (A→B→A→B) → STUCK (CYCLIC_PATTERN)
    4. 3x leere Ergebnisse hintereinander → STUCK (EMPTY_RESULTS)
    """

    STUCK_THRESHOLD = 3  # Max gleiche Calls
    NO_PROGRESS_THRESHOLD = 5  # Max Iterationen ohne neues Wissen
    CYCLE_LENGTH = 4  # Min Länge für Zyklus-Erkennung
    EMPTY_STREAK_THRESHOLD = 3  # Max leere Ergebnisse hintereinander

    def __init__(self):
        self._state = ProgressState()
        self._current_iteration = 0

    def reset(self) -> None:
        """Setzt den Tracker für eine neue Anfrage zurück."""
        self._state = ProgressState()
        self._current_iteration = 0
        logger.debug("[ToolProgress] Reset")

    def record_call(
        self,
        tool_name: str,
        args: Dict[str, Any],
        result: ToolResult,
        iteration: int
    ) -> StuckDetectionResult:
        """
        Zeichnet einen Tool-Call auf und prüft auf Stuck.

        Args:
            tool_name: Name des aufgerufenen Tools
            args: Argumente des Tool-Calls
            result: Ergebnis des Tool-Calls
            iteration: Aktuelle Iteration im Agent-Loop

        Returns:
            StuckDetectionResult mit Stuck-Status und ggf. Hinweisen
        """
        import time

        self._current_iteration = iteration

        # Signatur erstellen
        args_hash = self._hash_args(args)
        result_content = result.to_context() if result.success else (result.error or "")
        result_hash = self._hash_content(result_content)
        result_preview = result_content[:100] if result_content else "[leer]"

        signature = ToolCallSignature(
            tool_name=tool_name,
            args_hash=args_hash,
            result_hash=result_hash,
            result_preview=result_preview,
            iteration=iteration,
            timestamp=time.time()
        )

        self._state.call_signatures.append(signature)

        # Leere Ergebnisse tracken
        is_empty = self._is_empty_result(result)
        if is_empty:
            self._state.empty_result_streak += 1
        else:
            self._state.empty_result_streak = 0

        # Wissen extrahieren und tracken
        new_knowledge = self._extract_knowledge(tool_name, result)
        knowledge_before = len(self._state.knowledge_gained)
        self._state.knowledge_gained.update(new_knowledge)
        knowledge_after = len(self._state.knowledge_gained)

        if knowledge_after > knowledge_before:
            self._state.last_progress_iteration = iteration
            logger.debug(
                f"[ToolProgress] Neues Wissen: +{knowledge_after - knowledge_before} "
                f"(gesamt: {knowledge_after})"
            )

        # Stuck-Checks durchführen
        stuck_result = self._check_stuck(signature, iteration)

        if stuck_result.is_stuck:
            self._state.stuck_counter += 1
            logger.warning(
                f"[ToolProgress] STUCK erkannt: {stuck_result.reason.value} - "
                f"{stuck_result.details}"
            )

        return stuck_result

    def _check_stuck(
        self,
        current_sig: ToolCallSignature,
        iteration: int
    ) -> StuckDetectionResult:
        """Führt alle Stuck-Checks durch."""

        # Check 1: Wiederholte identische Calls
        repeated = self._check_repeated_calls(current_sig)
        if repeated.is_stuck:
            return repeated

        # Check 2: Leere Ergebnisse in Folge
        empty = self._check_empty_streak()
        if empty.is_stuck:
            return empty

        # Check 3: Kein Fortschritt über mehrere Iterationen
        no_progress = self._check_no_progress(iteration)
        if no_progress.is_stuck:
            return no_progress

        # Check 4: Zyklische Patterns
        cyclic = self._check_cyclic_pattern()
        if cyclic.is_stuck:
            return cyclic

        return StuckDetectionResult(is_stuck=False)

    def _check_repeated_calls(
        self,
        current_sig: ToolCallSignature
    ) -> StuckDetectionResult:
        """Prüft auf wiederholte identische Calls."""
        # Zähle wie oft dieser exakte Call schon vorkam
        matching_count = sum(
            1 for sig in self._state.call_signatures
            if sig.matches(current_sig)
        )

        if matching_count >= self.STUCK_THRESHOLD:
            return StuckDetectionResult(
                is_stuck=True,
                reason=StuckReason.REPEATED_CALL,
                details=(
                    f"Tool '{current_sig.tool_name}' wurde {matching_count}x "
                    f"mit identischen Argumenten und gleichem Ergebnis aufgerufen"
                ),
                suggestion=(
                    f"Der Aufruf von '{current_sig.tool_name}' liefert immer das gleiche Ergebnis. "
                    f"Versuche:\n"
                    f"- Andere Suchbegriffe oder Parameter\n"
                    f"- Ein anderes Tool für diese Aufgabe\n"
                    f"- Die bereits erhaltenen Informationen zu nutzen"
                ),
                repeated_count=matching_count
            )

        return StuckDetectionResult(is_stuck=False)

    def _check_empty_streak(self) -> StuckDetectionResult:
        """Prüft auf aufeinanderfolgende leere Ergebnisse."""
        if self._state.empty_result_streak >= self.EMPTY_STREAK_THRESHOLD:
            return StuckDetectionResult(
                is_stuck=True,
                reason=StuckReason.EMPTY_RESULTS,
                details=(
                    f"{self._state.empty_result_streak} Tool-Aufrufe in Folge "
                    f"haben keine Ergebnisse geliefert"
                ),
                suggestion=(
                    "Mehrere Suchen waren erfolglos. Mögliche Ursachen:\n"
                    "- Die gesuchte Information existiert nicht in den Quellen\n"
                    "- Die Suchbegriffe sind zu spezifisch oder falsch geschrieben\n"
                    "- Versuche allgemeinere Begriffe oder frage den User nach Details"
                ),
                repeated_count=self._state.empty_result_streak
            )

        return StuckDetectionResult(is_stuck=False)

    def _check_no_progress(self, iteration: int) -> StuckDetectionResult:
        """Prüft ob über mehrere Iterationen kein neues Wissen gewonnen wurde."""
        iterations_without_progress = iteration - self._state.last_progress_iteration

        if iterations_without_progress >= self.NO_PROGRESS_THRESHOLD:
            return StuckDetectionResult(
                is_stuck=True,
                reason=StuckReason.NO_PROGRESS,
                details=(
                    f"Seit {iterations_without_progress} Iterationen wurden "
                    f"keine neuen Erkenntnisse gewonnen"
                ),
                suggestion=(
                    "Die letzten Tool-Aufrufe haben keine neuen Informationen gebracht. "
                    "Optionen:\n"
                    "- Fasse zusammen was du bereits weißt\n"
                    "- Stelle dem User eine Klärungsfrage\n"
                    "- Versuche einen komplett anderen Ansatz"
                ),
                repeated_count=iterations_without_progress
            )

        return StuckDetectionResult(is_stuck=False)

    def _check_cyclic_pattern(self) -> StuckDetectionResult:
        """Erkennt zyklische Patterns wie A→B→A→B."""
        if len(self._state.call_signatures) < self.CYCLE_LENGTH * 2:
            return StuckDetectionResult(is_stuck=False)

        # Prüfe auf 2er-Zyklen (A→B→A→B)
        recent = self._state.call_signatures[-self.CYCLE_LENGTH * 2:]
        tool_sequence = [s.tool_name for s in recent]

        # Extrahiere potentielle Zyklen
        for cycle_len in [2, 3]:
            if self._has_cycle(tool_sequence, cycle_len):
                cycle_tools = tool_sequence[:cycle_len]
                return StuckDetectionResult(
                    is_stuck=True,
                    reason=StuckReason.CYCLIC_PATTERN,
                    details=(
                        f"Zyklisches Muster erkannt: {' → '.join(cycle_tools)} "
                        f"wiederholt sich"
                    ),
                    suggestion=(
                        f"Du wechselst zwischen {', '.join(set(cycle_tools))} hin und her. "
                        "Das deutet auf einen Deadlock hin. Versuche:\n"
                        "- Einen komplett anderen Ansatz\n"
                        "- Die bisherigen Ergebnisse zusammenzufassen\n"
                        "- Den User um Klärung zu bitten"
                    ),
                    repeated_count=cycle_len
                )

        return StuckDetectionResult(is_stuck=False)

    def _has_cycle(self, sequence: List[str], cycle_len: int) -> bool:
        """Prüft ob eine Sequenz einen Zyklus der gegebenen Länge hat."""
        if len(sequence) < cycle_len * 2:
            return False

        # Prüfe ob die letzten N*2 Elemente einen Zyklus bilden
        recent = sequence[-cycle_len * 2:]
        first_half = recent[:cycle_len]
        second_half = recent[cycle_len:]

        return first_half == second_half

    def _extract_knowledge(
        self,
        tool_name: str,
        result: ToolResult
    ) -> Set[str]:
        """
        Extrahiert "Wissen" aus einem Tool-Ergebnis.

        Für search_code: gefundene Dateipfade
        Für confluence: Seiten-IDs
        Für read_file: Funktions-/Klassennamen
        """
        if not result.success:
            return set()

        content = result.to_context()
        knowledge = set()

        # Dateipfade extrahieren
        if tool_name in ("search_code", "read_file", "batch_read_files"):
            # Pattern: pfad/zu/datei.java oder pfad/zu/datei.py
            paths = re.findall(r'[\w/\\.-]+\.(java|py|sql|xml|json|yaml|yml)', content)
            knowledge.update(f"file:{p}" for p in paths[:20])

        # Confluence Seiten-IDs
        if tool_name in ("search_confluence", "read_confluence_page"):
            # Pattern: ID: 12345 oder id": "12345
            ids = re.findall(r'[Ii][Dd]["\s:]+["\s]*(\d{5,})', content)
            knowledge.update(f"confluence:{id}" for id in ids[:10])

        # Klassen- und Methodennamen
        if tool_name in ("search_code", "read_file"):
            # Java/Python Klassen
            classes = re.findall(r'class\s+(\w+)', content)
            knowledge.update(f"class:{c}" for c in classes[:10])

            # Java/Python Methoden
            methods = re.findall(r'(?:def|public|private|protected)\s+\w+\s+(\w+)\s*\(', content)
            knowledge.update(f"method:{m}" for m in methods[:10])

        # Jira Tickets
        if tool_name in ("search_jira", "get_jira_issue"):
            tickets = re.findall(r'([A-Z]{2,}-\d+)', content)
            knowledge.update(f"jira:{t}" for t in tickets[:10])

        return knowledge

    def _is_empty_result(self, result: ToolResult) -> bool:
        """Prüft ob ein Ergebnis leer oder nicht hilfreich ist."""
        if not result.success:
            return True

        content = result.to_context()
        if not content:
            return True

        # Typische "keine Treffer" Muster
        empty_patterns = [
            "keine treffer",
            "keine ergebnisse",
            "nicht gefunden",
            "no results",
            "not found",
            "0 treffer",
            "0 ergebnisse",
        ]

        content_lower = content.lower()
        return any(p in content_lower for p in empty_patterns) and len(content) < 200

    def _hash_args(self, args: Dict[str, Any]) -> str:
        """Erstellt einen Hash der Tool-Argumente."""
        try:
            # Sortiere für konsistente Hashes
            sorted_args = json.dumps(args, sort_keys=True, default=str)
            return hashlib.md5(sorted_args.encode()).hexdigest()[:12]
        except (TypeError, ValueError):
            return hashlib.md5(str(args).encode()).hexdigest()[:12]

    def _hash_content(self, content: str) -> str:
        """Erstellt einen Hash des Result-Contents."""
        # Nur erste 500 Zeichen für Performance
        truncated = content[:500] if content else ""
        return hashlib.md5(truncated.encode()).hexdigest()[:12]

    def get_progress_summary(self) -> Dict[str, Any]:
        """Gibt eine Zusammenfassung des Fortschritts zurück."""
        return {
            "total_calls": len(self._state.call_signatures),
            "unique_knowledge": len(self._state.knowledge_gained),
            "stuck_count": self._state.stuck_counter,
            "current_iteration": self._current_iteration,
            "last_progress_iteration": self._state.last_progress_iteration,
            "empty_streak": self._state.empty_result_streak,
            "recent_tools": [
                s.tool_name for s in self._state.call_signatures[-5:]
            ]
        }


# Singleton-Instanzen pro Session (in Orchestrator verwaltet)
_trackers: Dict[str, ToolProgressTracker] = {}


def get_progress_tracker(session_id: str) -> ToolProgressTracker:
    """Gibt den ProgressTracker für eine Session zurück."""
    if session_id not in _trackers:
        _trackers[session_id] = ToolProgressTracker()
    return _trackers[session_id]


def reset_progress_tracker(session_id: str) -> None:
    """Setzt den ProgressTracker für eine Session zurück."""
    if session_id in _trackers:
        _trackers[session_id].reset()
