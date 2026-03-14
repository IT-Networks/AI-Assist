"""
Tests fuer erweiterte Analytics-Komponenten.

Testet:
- PerformanceTracker: LLM/Tool-Timing, Token-Tracking
- PatternDetector: Sequenzen, Loops, Fehler
- ReportGenerator: Markdown-Report, Empfehlungen
"""

import asyncio
import json
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.performance_tracker import (
    PerformanceTracker,
    PerformanceMetrics,
    MODEL_PRICING,
)
from app.services.pattern_detector import (
    PatternDetector,
    PatternAnalysis,
    ToolSequence,
)
from app.services.report_generator import (
    ReportGenerator,
    AnalysisReport,
)


# ═══════════════════════════════════════════════════════════════════════════════
# PerformanceTracker Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestPerformanceTracker:
    """Tests fuer den PerformanceTracker."""

    def test_llm_call_tracking(self):
        """LLM-Aufrufe werden korrekt getrackt."""
        tracker = PerformanceTracker("test_chain")

        tracker.start_llm_call("claude-3-5-sonnet", "tool_selection")
        # Simuliere kurze Verarbeitung
        import time
        time.sleep(0.01)
        tracker.end_llm_call(input_tokens=500, output_tokens=200)

        metrics = tracker.get_metrics()

        assert metrics.llm_calls == 1
        assert metrics.input_tokens == 500
        assert metrics.output_tokens == 200
        assert metrics.total_tokens == 700
        assert metrics.llm_total_latency_ms > 0
        assert metrics.estimated_cost_usd > 0

    def test_tool_tracking(self):
        """Tool-Aufrufe werden korrekt getrackt."""
        tracker = PerformanceTracker("test_chain")

        tracker.start_tool("search_code")
        import time
        time.sleep(0.02)
        tracker.end_tool("search_code")

        tracker.log_tool("read_file", duration_ms=50)

        metrics = tracker.get_metrics()

        assert metrics.tool_calls == 2
        assert metrics.tool_total_time_ms >= 50
        assert len(metrics.tool_timing_details) == 2

    def test_parallel_tool_detection(self):
        """Parallele Tool-Aufrufe werden erkannt."""
        tracker = PerformanceTracker("test_chain")

        # Zwei Tools gleichzeitig starten
        tracker.start_tool("search_code")
        tracker.start_tool("read_file")

        # Erstes beenden
        tracker.end_tool("read_file")
        # Zweites beenden
        tracker.end_tool("search_code")

        metrics = tracker.get_metrics()

        assert metrics.parallel_tool_calls >= 1
        assert metrics.max_parallel_tools >= 2

    def test_cost_calculation(self):
        """Kostenberechnung ist korrekt."""
        tracker = PerformanceTracker("test_chain")

        # Claude 3.5 Sonnet: $3/1M input, $15/1M output
        tracker.log_llm_call(
            model="claude-3-5-sonnet",
            latency_ms=100,
            input_tokens=1_000_000,  # 1M tokens
            output_tokens=100_000,   # 100K tokens
        )

        metrics = tracker.get_metrics()

        # Erwartete Kosten: $3 + $1.5 = $4.5
        assert 4.4 <= metrics.estimated_cost_usd <= 4.6

    def test_slowest_tool_detection(self):
        """Langsamstes Tool wird erkannt."""
        tracker = PerformanceTracker("test_chain")

        tracker.log_tool("fast_tool", duration_ms=50)
        tracker.log_tool("slow_tool", duration_ms=5000)
        tracker.log_tool("medium_tool", duration_ms=500)

        metrics = tracker.get_metrics()

        assert metrics.slowest_tool == "slow_tool"
        assert metrics.slowest_tool_ms == 5000

    def test_time_distribution(self):
        """Zeit-Verteilung wird berechnet."""
        tracker = PerformanceTracker("test_chain")

        # Simuliere einige Aufrufe
        tracker.log_llm_call(
            model="test",
            latency_ms=500,
            input_tokens=100,
            output_tokens=50,
        )
        tracker.log_tool("test_tool", duration_ms=500)

        import time
        time.sleep(0.05)  # Gesamtdauer erhoehen

        metrics = tracker.get_metrics()

        # Beide sollten einen Anteil haben
        assert metrics.llm_time_percent > 0
        assert metrics.tool_time_percent > 0


# ═══════════════════════════════════════════════════════════════════════════════
# PatternDetector Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestPatternDetector:
    """Tests fuer den PatternDetector."""

    @pytest.fixture
    def temp_dir(self):
        """Temporaeres Verzeichnis mit Test-Daten."""
        temp = tempfile.mkdtemp()

        # Test-Chains erstellen
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        date_dir = Path(temp) / date_str
        date_dir.mkdir(parents=True)

        chains = [
            # Chain 1: Normale Sequenz
            {
                "chain_id": "c_001",
                "model": "claude-3-5-sonnet",
                "query_categories": ["code_search"],
                "final_status": "resolved",
                "total_iterations": 2,
                "duration_ms": 1000,
                "tool_chain": [
                    {"tool": "search_code", "status": "success", "duration_ms": 200},
                    {"tool": "read_file", "status": "success", "duration_ms": 100},
                ]
            },
            # Chain 2: Loop-Pattern
            {
                "chain_id": "c_002",
                "model": "claude-3-5-sonnet",
                "query_categories": ["code_search"],
                "final_status": "resolved",
                "total_iterations": 4,
                "duration_ms": 2000,
                "tool_chain": [
                    {"tool": "search_code", "status": "success", "duration_ms": 200},
                    {"tool": "search_code", "status": "success", "duration_ms": 200},
                    {"tool": "search_code", "status": "success", "duration_ms": 200},
                    {"tool": "read_file", "status": "success", "duration_ms": 100},
                ]
            },
            # Chain 3: Fehler-Pattern
            {
                "chain_id": "c_003",
                "model": "claude-3-haiku",
                "query_categories": ["api"],
                "final_status": "failed",
                "total_iterations": 3,
                "duration_ms": 5000,
                "tool_chain": [
                    {"tool": "api_call", "status": "error", "error_type": "connection", "duration_ms": 5000},
                    {"tool": "api_call", "status": "error", "error_type": "connection", "duration_ms": 5000},
                    {"tool": "api_call", "status": "error", "error_type": "connection", "duration_ms": 5000},
                ]
            },
            # Chain 4: Erfolgreiche Sequenz (fuer Haeufigkeit)
            {
                "chain_id": "c_004",
                "model": "claude-3-5-sonnet",
                "query_categories": ["code_search"],
                "final_status": "resolved",
                "total_iterations": 2,
                "duration_ms": 800,
                "tool_chain": [
                    {"tool": "search_code", "status": "success", "duration_ms": 150},
                    {"tool": "read_file", "status": "success", "duration_ms": 80},
                ]
            },
            # Chain 5: Noch eine search->read Sequenz
            {
                "chain_id": "c_005",
                "model": "claude-3-5-sonnet",
                "query_categories": ["code_search"],
                "final_status": "resolved",
                "total_iterations": 2,
                "duration_ms": 900,
                "tool_chain": [
                    {"tool": "search_code", "status": "success", "duration_ms": 180},
                    {"tool": "read_file", "status": "success", "duration_ms": 90},
                ]
            },
            # Chain 6: Weiterer Loop fuer Loop-Detection (braucht min 2)
            {
                "chain_id": "c_006",
                "model": "claude-3-5-sonnet",
                "query_categories": ["code_search"],
                "final_status": "resolved",
                "total_iterations": 3,
                "duration_ms": 1500,
                "tool_chain": [
                    {"tool": "search_code", "status": "success", "duration_ms": 200},
                    {"tool": "search_code", "status": "success", "duration_ms": 200},
                    {"tool": "read_file", "status": "success", "duration_ms": 100},
                ]
            },
            # Chain 7: Langsame Sequenz fuer Optimierungs-Vorschlaege
            {
                "chain_id": "c_007",
                "model": "claude-3-5-sonnet",
                "query_categories": ["api"],
                "final_status": "resolved",
                "total_iterations": 2,
                "duration_ms": 8000,
                "tool_chain": [
                    {"tool": "api_call", "status": "success", "duration_ms": 6000},
                    {"tool": "parse_response", "status": "success", "duration_ms": 100},
                ]
            },
        ]

        with open(date_dir / "chains.jsonl", "w") as f:
            for chain in chains:
                f.write(json.dumps(chain) + "\n")

        yield temp
        shutil.rmtree(temp, ignore_errors=True)

    def test_detect_loops(self, temp_dir):
        """Loop-Patterns werden erkannt."""
        detector = PatternDetector(temp_dir)
        analysis = detector.analyze(days=7)

        # Sollte den search_code Loop finden
        loop_tools = [l.loop_tool for l in analysis.loops_detected]
        assert "search_code" in loop_tools

    def test_detect_frequent_sequences(self, temp_dir):
        """Haeufige Sequenzen werden erkannt."""
        detector = PatternDetector(temp_dir)
        analysis = detector.analyze(days=7, min_sequence_freq=2)

        # search_code -> read_file sollte haeufig sein
        sequences = [tuple(s.sequence) for s in analysis.frequent_sequences]
        assert ("search_code", "read_file") in sequences

    def test_detect_failure_patterns(self, temp_dir):
        """Fehlermuster werden erkannt."""
        detector = PatternDetector(temp_dir)
        analysis = detector.analyze(days=7)

        # api_call mit connection error
        failure_tools = [f.tool for f in analysis.failure_patterns]
        assert "api_call" in failure_tools

        # Fehlertyp pruefen
        for fp in analysis.failure_patterns:
            if fp.tool == "api_call":
                assert fp.error_type == "connection"

    def test_model_recommendations(self, temp_dir):
        """Modell-Empfehlungen werden generiert."""
        detector = PatternDetector(temp_dir)
        analysis = detector.analyze(days=7)

        # Sollte Empfehlungen haben
        assert len(analysis.model_category_performance) > 0

        # Sonnet sollte fuer code_search empfohlen werden (hoehere Erfolgsrate)
        if "code_search" in analysis.recommended_models:
            assert "sonnet" in analysis.recommended_models["code_search"].lower()

    def test_optimization_suggestions(self, temp_dir):
        """Optimierungsvorschlaege werden generiert."""
        detector = PatternDetector(temp_dir)
        analysis = detector.analyze(days=7)

        # Sollte Vorschlaege haben (wegen Loops und Fehler)
        assert len(analysis.optimization_suggestions) > 0

    def test_empty_data(self):
        """Leere Daten werden behandelt."""
        temp = tempfile.mkdtemp()
        try:
            detector = PatternDetector(temp)
            analysis = detector.analyze(days=7)

            assert analysis.analyzed_chains == 0
            assert len(analysis.frequent_sequences) == 0
            assert len(analysis.loops_detected) == 0
        finally:
            shutil.rmtree(temp, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# ReportGenerator Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestReportGenerator:
    """Tests fuer den ReportGenerator."""

    @pytest.fixture
    def temp_dir_with_data(self):
        """Temporaeres Verzeichnis mit Test-Daten."""
        temp = tempfile.mkdtemp()

        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        date_dir = Path(temp) / date_str
        date_dir.mkdir(parents=True)

        # Mehr Chains fuer aussagekraeftigen Report
        chains = []
        for i in range(20):
            status = "resolved" if i % 5 != 0 else "failed"
            model = "claude-3-5-sonnet" if i % 3 != 0 else "claude-3-haiku"

            chains.append({
                "chain_id": f"c_{i:03d}",
                "model": model,
                "query_categories": ["code_search"] if i % 2 == 0 else ["api"],
                "final_status": status,
                "total_iterations": (i % 5) + 1,
                "duration_ms": 500 + (i * 100),
                "tool_chain": [
                    {"tool": "search_code", "status": "success", "duration_ms": 200},
                    {"tool": "read_file", "status": "success" if i % 4 != 0 else "error",
                     "error_type": "permission" if i % 4 == 0 else None,
                     "duration_ms": 100},
                ]
            })

        with open(date_dir / "chains.jsonl", "w") as f:
            for chain in chains:
                f.write(json.dumps(chain) + "\n")

        yield temp
        shutil.rmtree(temp, ignore_errors=True)

    def test_generate_report(self, temp_dir_with_data):
        """Report wird generiert."""
        generator = ReportGenerator(temp_dir_with_data)
        report = generator.generate(days=7)

        assert isinstance(report, AnalysisReport)
        assert report.period_days == 7
        assert len(report.markdown) > 0
        assert report.summary["total_chains"] == 20

    def test_report_contains_sections(self, temp_dir_with_data):
        """Report enthaelt alle Sektionen."""
        generator = ReportGenerator(temp_dir_with_data)
        report = generator.generate(days=7)

        # Pruefen ob wichtige Sektionen vorhanden sind
        assert "Executive Summary" in report.markdown
        assert "Tool-Performance" in report.markdown
        assert "Modell-Performance" in report.markdown
        assert "Handlungsempfehlungen" in report.markdown

    def test_report_has_recommendations(self, temp_dir_with_data):
        """Report enthaelt Empfehlungen."""
        generator = ReportGenerator(temp_dir_with_data)
        report = generator.generate(days=7)

        # Sollte Empfehlungen haben
        assert len(report.recommendations) > 0

        # Empfehlungen haben Struktur
        for rec in report.recommendations:
            assert "priority" in rec
            assert "title" in rec
            assert "action" in rec

    def test_save_report(self, temp_dir_with_data):
        """Report kann gespeichert werden."""
        generator = ReportGenerator(temp_dir_with_data)
        report = generator.generate(days=7)

        report_path = generator.save_report(report, "test_report.md")

        assert report_path.exists()
        assert report_path.name == "test_report.md"

        # Inhalt pruefen
        with open(report_path) as f:
            content = f.read()
        assert "AI-Assist Analytics Report" in content

    def test_report_statistics(self, temp_dir_with_data):
        """Report-Statistiken sind korrekt."""
        generator = ReportGenerator(temp_dir_with_data)
        report = generator.generate(days=7)

        summary = report.summary
        assert summary["total_chains"] == 20
        assert 0 <= summary["success_rate"] <= 100
        assert summary["avg_iterations"] > 0

    def test_empty_report(self):
        """Leerer Report wird behandelt."""
        temp = tempfile.mkdtemp()
        try:
            generator = ReportGenerator(temp)
            report = generator.generate(days=7)

            assert report.summary["total_chains"] == 0
            assert "0" in report.markdown  # Sollte 0 Chains erwaehnen
        finally:
            shutil.rmtree(temp, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Test
# ═══════════════════════════════════════════════════════════════════════════════

class TestAnalyticsExtendedIntegration:
    """Integration-Tests fuer erweiterte Analytics."""

    @pytest.fixture
    def temp_dir(self):
        temp = tempfile.mkdtemp()
        yield temp
        shutil.rmtree(temp, ignore_errors=True)

    def test_full_analysis_workflow(self, temp_dir):
        """Vollstaendiger Analyse-Workflow."""
        # 1. Performance-Daten simulieren
        tracker = PerformanceTracker("integration_test")

        tracker.log_llm_call(
            model="claude-3-5-sonnet",
            latency_ms=500,
            input_tokens=1000,
            output_tokens=500,
            purpose="tool_selection"
        )

        tracker.log_tool("search_code", duration_ms=200)
        tracker.log_tool("read_file", duration_ms=100)

        metrics = tracker.get_metrics()

        # 2. Chains speichern
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        date_dir = Path(temp_dir) / date_str
        date_dir.mkdir(parents=True)

        chains = []
        for i in range(10):
            chains.append({
                "chain_id": f"c_{i:03d}",
                "model": "claude-3-5-sonnet",
                "query_categories": ["code_search"],
                "final_status": "resolved",
                "total_iterations": 2,
                "duration_ms": metrics.total_duration_ms,
                "tool_chain": [
                    {"tool": "search_code", "status": "success", "duration_ms": 200},
                    {"tool": "read_file", "status": "success", "duration_ms": 100},
                ],
                "performance": metrics.to_dict(),
            })

        with open(date_dir / "chains.jsonl", "w") as f:
            for chain in chains:
                f.write(json.dumps(chain) + "\n")

        # 3. Pattern-Analyse
        detector = PatternDetector(temp_dir)
        patterns = detector.analyze(days=7)

        assert patterns.analyzed_chains == 10
        assert len(patterns.frequent_sequences) > 0

        # 4. Report generieren
        generator = ReportGenerator(temp_dir)
        report = generator.generate(days=7)

        assert report.summary["total_chains"] == 10
        assert "search_code" in report.markdown

        # 5. Report speichern
        report_path = generator.save_report(report)
        assert report_path.exists()

        print("\n[OK] Integration-Test erfolgreich!")
        print(f"   - Chains analysiert: {patterns.analyzed_chains}")
        print(f"   - Sequenzen gefunden: {len(patterns.frequent_sequences)}")
        print(f"   - Empfehlungen: {len(report.recommendations)}")


# ═══════════════════════════════════════════════════════════════════════════════
# Run Tests
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
