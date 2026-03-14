"""
Pattern Detector - Erkennt Tool-Sequenz-Muster und Anomalien.

Analysiert:
- Haeufige Tool-Abfolgen
- Ineffiziente Schleifen (gleicher Tool mehrfach)
- Fehlermuster
- Optimierungspotenzial
"""

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ToolSequence:
    """Erkannte Tool-Abfolge."""
    sequence: Tuple[str, ...]
    frequency: int
    avg_duration_ms: int
    success_rate: float
    is_loop: bool
    loop_tool: Optional[str] = None
    optimization_potential: str = "low"  # "high" | "medium" | "low"
    suggestion: str = ""


@dataclass
class FailurePattern:
    """Erkanntes Fehlermuster."""
    tool: str
    error_type: str
    frequency: int
    preceding_tools: List[str]
    query_categories: List[str]
    suggestion: str = ""


@dataclass
class ModelCategoryPerformance:
    """Performance eines Modells pro Kategorie."""
    model: str
    category: str
    total_chains: int
    success_count: int
    success_rate: float
    avg_iterations: float
    avg_duration_ms: int


@dataclass
class PatternAnalysis:
    """Gesamtergebnis der Pattern-Analyse."""
    analyzed_chains: int
    period_days: int

    # Sequenzen
    frequent_sequences: List[ToolSequence]
    loops_detected: List[ToolSequence]

    # Fehler
    failure_patterns: List[FailurePattern]
    error_prone_tools: Dict[str, float]  # tool -> failure_rate

    # Modell-Analyse
    model_category_performance: List[ModelCategoryPerformance]
    recommended_models: Dict[str, str]  # category -> best_model

    # Effizienz
    avg_iterations_per_chain: float
    chains_with_loops: int
    optimization_suggestions: List[str]


class PatternDetector:
    """
    Analysiert Analytics-Daten auf Muster und Anomalien.

    Usage:
        detector = PatternDetector(storage_path="./data/analytics")
        analysis = detector.analyze(days=30)

        print(f"Loops gefunden: {len(analysis.loops_detected)}")
        for seq in analysis.frequent_sequences[:5]:
            print(f"  {seq.sequence} - {seq.frequency}x")
    """

    def __init__(self, storage_path: str):
        self.storage_path = Path(storage_path)
        self._chains: List[Dict] = []

    def analyze(self, days: int = 30, min_sequence_freq: int = 3) -> PatternAnalysis:
        """
        Fuehrt vollstaendige Pattern-Analyse durch.

        Args:
            days: Anzahl der Tage fuer Analyse
            min_sequence_freq: Mindest-Haeufigkeit fuer Sequenzen

        Returns:
            PatternAnalysis mit allen erkannten Mustern
        """
        # Daten laden
        self._load_chains(days)

        if not self._chains:
            return self._empty_analysis(days)

        # Analysen durchfuehren
        sequences = self._analyze_sequences(min_sequence_freq)
        loops = self._detect_loops()
        failure_patterns = self._analyze_failures()
        error_prone = self._calculate_error_rates()
        model_perf = self._analyze_model_performance()
        recommendations = self._generate_model_recommendations(model_perf)
        suggestions = self._generate_optimization_suggestions(
            sequences, loops, failure_patterns
        )

        # Statistiken
        total_iterations = sum(
            c.get("total_iterations", 0) for c in self._chains
        )
        avg_iterations = (
            total_iterations / len(self._chains)
            if self._chains else 0
        )
        chains_with_loops = sum(
            1 for c in self._chains
            if self._has_loop(c.get("tool_chain", []))
        )

        return PatternAnalysis(
            analyzed_chains=len(self._chains),
            period_days=days,
            frequent_sequences=sequences,
            loops_detected=loops,
            failure_patterns=failure_patterns,
            error_prone_tools=error_prone,
            model_category_performance=model_perf,
            recommended_models=recommendations,
            avg_iterations_per_chain=round(avg_iterations, 2),
            chains_with_loops=chains_with_loops,
            optimization_suggestions=suggestions,
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # Daten laden
    # ═══════════════════════════════════════════════════════════════════════════

    def _load_chains(self, days: int) -> None:
        """Laedt Chain-Daten der letzten N Tage."""
        self._chains = []

        for i in range(days):
            date = datetime.utcnow() - timedelta(days=i)
            date_str = date.strftime("%Y-%m-%d")
            log_file = self.storage_path / date_str / "chains.jsonl"

            if not log_file.exists():
                continue

            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            chain = json.loads(line.strip())
                            self._chains.append(chain)
                        except json.JSONDecodeError:
                            continue
            except Exception:
                continue

    def _empty_analysis(self, days: int) -> PatternAnalysis:
        """Leere Analyse wenn keine Daten vorhanden."""
        return PatternAnalysis(
            analyzed_chains=0,
            period_days=days,
            frequent_sequences=[],
            loops_detected=[],
            failure_patterns=[],
            error_prone_tools={},
            model_category_performance=[],
            recommended_models={},
            avg_iterations_per_chain=0,
            chains_with_loops=0,
            optimization_suggestions=[],
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # Sequenz-Analyse
    # ═══════════════════════════════════════════════════════════════════════════

    def _analyze_sequences(self, min_freq: int) -> List[ToolSequence]:
        """Findet haeufige Tool-Sequenzen."""
        # Alle 2er und 3er Sequenzen zaehlen
        sequence_stats: Dict[Tuple[str, ...], Dict] = defaultdict(
            lambda: {"count": 0, "durations": [], "successes": 0}
        )

        for chain in self._chains:
            tool_chain = chain.get("tool_chain", [])
            tools = [t.get("tool", "unknown") for t in tool_chain]
            is_success = chain.get("final_status") == "resolved"

            # 2er Sequenzen
            for i in range(len(tools) - 1):
                seq = tuple(tools[i:i+2])
                sequence_stats[seq]["count"] += 1
                if is_success:
                    sequence_stats[seq]["successes"] += 1

                # Dauer der Sequenz
                duration = sum(
                    t.get("duration_ms", 0)
                    for t in tool_chain[i:i+2]
                )
                sequence_stats[seq]["durations"].append(duration)

            # 3er Sequenzen
            for i in range(len(tools) - 2):
                seq = tuple(tools[i:i+3])
                sequence_stats[seq]["count"] += 1
                if is_success:
                    sequence_stats[seq]["successes"] += 1

                duration = sum(
                    t.get("duration_ms", 0)
                    for t in tool_chain[i:i+3]
                )
                sequence_stats[seq]["durations"].append(duration)

        # Zu ToolSequence konvertieren
        sequences = []
        for seq, stats in sequence_stats.items():
            if stats["count"] < min_freq:
                continue

            avg_duration = (
                sum(stats["durations"]) // len(stats["durations"])
                if stats["durations"] else 0
            )
            success_rate = (
                stats["successes"] / stats["count"] * 100
                if stats["count"] > 0 else 0
            )

            # Loop erkennen
            is_loop = len(set(seq)) < len(seq)
            loop_tool = None
            if is_loop:
                # Welches Tool wiederholt sich?
                for tool in seq:
                    if seq.count(tool) > 1:
                        loop_tool = tool
                        break

            # Optimierungspotenzial
            potential = "low"
            suggestion = ""

            if is_loop:
                potential = "high"
                suggestion = f"Tool '{loop_tool}' wird wiederholt - pruefe ob Ergebnis gecacht werden kann"
            elif success_rate < 50:
                potential = "high"
                suggestion = f"Sequenz hat niedrige Erfolgsrate ({success_rate:.0f}%) - Reihenfolge optimieren"
            elif avg_duration > 5000:
                potential = "medium"
                suggestion = f"Sequenz ist langsam ({avg_duration}ms) - parallele Ausfuehrung pruefen"

            sequences.append(ToolSequence(
                sequence=seq,
                frequency=stats["count"],
                avg_duration_ms=avg_duration,
                success_rate=round(success_rate, 1),
                is_loop=is_loop,
                loop_tool=loop_tool,
                optimization_potential=potential,
                suggestion=suggestion,
            ))

        # Nach Haeufigkeit sortieren
        return sorted(sequences, key=lambda s: -s.frequency)

    def _detect_loops(self) -> List[ToolSequence]:
        """Findet spezifische Loop-Muster."""
        loop_patterns: Dict[str, Dict] = defaultdict(
            lambda: {"count": 0, "max_repeats": 0, "chains": []}
        )

        for chain in self._chains:
            tool_chain = chain.get("tool_chain", [])
            tools = [t.get("tool", "unknown") for t in tool_chain]

            # Aufeinanderfolgende Wiederholungen finden
            current_tool = None
            repeat_count = 0

            for tool in tools:
                if tool == current_tool:
                    repeat_count += 1
                else:
                    if repeat_count >= 2:
                        loop_patterns[current_tool]["count"] += 1
                        loop_patterns[current_tool]["max_repeats"] = max(
                            loop_patterns[current_tool]["max_repeats"],
                            repeat_count
                        )
                    current_tool = tool
                    repeat_count = 1

            # Letztes Tool pruefen
            if repeat_count >= 2:
                loop_patterns[current_tool]["count"] += 1
                loop_patterns[current_tool]["max_repeats"] = max(
                    loop_patterns[current_tool]["max_repeats"],
                    repeat_count
                )

        # Zu ToolSequence konvertieren
        loops = []
        for tool, stats in loop_patterns.items():
            if stats["count"] < 2:
                continue

            suggestion = ""
            if tool in ["search_code", "search"]:
                suggestion = "Suche zu unspezifisch - Query-Optimierung empfohlen"
            elif tool in ["api_call", "http_request"]:
                suggestion = "API-Retries - Exponential Backoff implementieren"
            elif tool in ["read_file", "read"]:
                suggestion = "Mehrfaches Lesen - Caching aktivieren"
            else:
                suggestion = f"Tool '{tool}' wiederholt sich - Ursache analysieren"

            loops.append(ToolSequence(
                sequence=tuple([tool] * stats["max_repeats"]),
                frequency=stats["count"],
                avg_duration_ms=0,
                success_rate=0,
                is_loop=True,
                loop_tool=tool,
                optimization_potential="high",
                suggestion=suggestion,
            ))

        return sorted(loops, key=lambda l: -l.frequency)

    def _has_loop(self, tool_chain: List[Dict]) -> bool:
        """Prueft ob eine Chain Loops enthaelt."""
        tools = [t.get("tool", "") for t in tool_chain]
        for i in range(len(tools) - 1):
            if tools[i] == tools[i + 1]:
                return True
        return False

    # ═══════════════════════════════════════════════════════════════════════════
    # Fehler-Analyse
    # ═══════════════════════════════════════════════════════════════════════════

    def _analyze_failures(self) -> List[FailurePattern]:
        """Analysiert Fehlermuster."""
        failure_stats: Dict[Tuple[str, str], Dict] = defaultdict(
            lambda: {"count": 0, "preceding": [], "categories": []}
        )

        for chain in self._chains:
            tool_chain = chain.get("tool_chain", [])
            categories = chain.get("query_categories", [])

            for i, tool in enumerate(tool_chain):
                if tool.get("status") != "error":
                    continue

                tool_name = tool.get("tool", "unknown")
                error_type = tool.get("error_type", "other")
                key = (tool_name, error_type)

                failure_stats[key]["count"] += 1
                failure_stats[key]["categories"].extend(categories)

                # Vorherige Tools
                if i > 0:
                    prev_tool = tool_chain[i-1].get("tool", "unknown")
                    failure_stats[key]["preceding"].append(prev_tool)

        # Zu FailurePattern konvertieren
        patterns = []
        for (tool, error_type), stats in failure_stats.items():
            if stats["count"] < 2:
                continue

            # Haeufigste vorherige Tools
            preceding_counter = Counter(stats["preceding"])
            top_preceding = [t for t, _ in preceding_counter.most_common(3)]

            # Haeufigste Kategorien
            category_counter = Counter(stats["categories"])
            top_categories = [c for c, _ in category_counter.most_common(3)]

            # Suggestion generieren
            suggestion = self._get_failure_suggestion(tool, error_type)

            patterns.append(FailurePattern(
                tool=tool,
                error_type=error_type,
                frequency=stats["count"],
                preceding_tools=top_preceding,
                query_categories=top_categories,
                suggestion=suggestion,
            ))

        return sorted(patterns, key=lambda p: -p.frequency)

    def _get_failure_suggestion(self, tool: str, error_type: str) -> str:
        """Generiert Verbesserungsvorschlag fuer Fehler."""
        suggestions = {
            ("connection",): "Timeout erhoehen, Retry-Logik mit Backoff",
            ("permission",): "Berechtigungen pruefen vor Aufruf",
            ("validation",): "Input-Validierung verbessern",
            ("not_found",): "Existenz-Check vor Zugriff",
            ("rate_limit",): "Rate-Limiting implementieren, Anfragen drosseln",
        }

        for error_types, suggestion in suggestions.items():
            if error_type in error_types:
                return suggestion

        return f"Fehlerbehandlung fuer '{error_type}' in '{tool}' verbessern"

    def _calculate_error_rates(self) -> Dict[str, float]:
        """Berechnet Fehlerraten pro Tool."""
        tool_stats: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"total": 0, "errors": 0}
        )

        for chain in self._chains:
            for tool in chain.get("tool_chain", []):
                tool_name = tool.get("tool", "unknown")
                tool_stats[tool_name]["total"] += 1
                if tool.get("status") == "error":
                    tool_stats[tool_name]["errors"] += 1

        return {
            tool: round(stats["errors"] / stats["total"] * 100, 1)
            for tool, stats in tool_stats.items()
            if stats["total"] >= 5  # Mindestens 5 Aufrufe
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # Modell-Analyse
    # ═══════════════════════════════════════════════════════════════════════════

    def _analyze_model_performance(self) -> List[ModelCategoryPerformance]:
        """Analysiert Modell-Performance pro Kategorie."""
        stats: Dict[Tuple[str, str], Dict] = defaultdict(
            lambda: {"chains": 0, "successes": 0, "iterations": [], "durations": []}
        )

        for chain in self._chains:
            model = chain.get("model", "unknown")
            categories = chain.get("query_categories", ["general"])
            is_success = chain.get("final_status") == "resolved"
            iterations = chain.get("total_iterations", 0)
            duration = chain.get("duration_ms", 0)

            for category in categories:
                key = (model, category)
                stats[key]["chains"] += 1
                if is_success:
                    stats[key]["successes"] += 1
                stats[key]["iterations"].append(iterations)
                stats[key]["durations"].append(duration)

        # Zu ModelCategoryPerformance konvertieren
        performances = []
        for (model, category), data in stats.items():
            if data["chains"] < 3:  # Mindestens 3 Chains
                continue

            avg_iterations = (
                sum(data["iterations"]) / len(data["iterations"])
                if data["iterations"] else 0
            )
            avg_duration = (
                sum(data["durations"]) // len(data["durations"])
                if data["durations"] else 0
            )

            performances.append(ModelCategoryPerformance(
                model=model,
                category=category,
                total_chains=data["chains"],
                success_count=data["successes"],
                success_rate=round(data["successes"] / data["chains"] * 100, 1),
                avg_iterations=round(avg_iterations, 2),
                avg_duration_ms=avg_duration,
            ))

        return sorted(
            performances,
            key=lambda p: (-p.success_rate, p.avg_iterations)
        )

    def _generate_model_recommendations(
        self,
        performances: List[ModelCategoryPerformance]
    ) -> Dict[str, str]:
        """Generiert Modell-Empfehlungen pro Kategorie."""
        recommendations = {}

        # Gruppieren nach Kategorie
        by_category: Dict[str, List[ModelCategoryPerformance]] = defaultdict(list)
        for perf in performances:
            by_category[perf.category].append(perf)

        # Bestes Modell pro Kategorie (hoechste Erfolgsrate, niedrigste Iterationen)
        for category, perfs in by_category.items():
            if not perfs:
                continue

            # Score: success_rate - (avg_iterations * 5)
            best = max(
                perfs,
                key=lambda p: p.success_rate - (p.avg_iterations * 5)
            )
            recommendations[category] = best.model

        return recommendations

    # ═══════════════════════════════════════════════════════════════════════════
    # Optimierungs-Vorschlaege
    # ═══════════════════════════════════════════════════════════════════════════

    def _generate_optimization_suggestions(
        self,
        sequences: List[ToolSequence],
        loops: List[ToolSequence],
        failures: List[FailurePattern],
    ) -> List[str]:
        """Generiert priorisierte Optimierungsvorschlaege."""
        suggestions = []

        # Loop-basierte Vorschlaege
        if loops:
            loop_tools = [l.loop_tool for l in loops[:3]]
            suggestions.append(
                f"[HOCH] Tool-Loops reduzieren: {', '.join(loop_tools)} "
                f"werden wiederholt aufgerufen"
            )

        # Fehler-basierte Vorschlaege
        high_error_tools = [
            f.tool for f in failures
            if f.frequency >= 5
        ][:3]
        if high_error_tools:
            suggestions.append(
                f"[HOCH] Fehlerbehandlung verbessern fuer: {', '.join(high_error_tools)}"
            )

        # Sequenz-basierte Vorschlaege
        slow_sequences = [
            s for s in sequences
            if s.avg_duration_ms > 5000 and not s.is_loop
        ][:3]
        if slow_sequences:
            seq_str = " -> ".join(slow_sequences[0].sequence)
            suggestions.append(
                f"[MITTEL] Langsame Sequenz optimieren: {seq_str} "
                f"({slow_sequences[0].avg_duration_ms}ms)"
            )

        # Niedrige Erfolgsrate
        low_success = [
            s for s in sequences
            if s.success_rate < 50 and s.frequency >= 5
        ][:2]
        if low_success:
            seq_str = " -> ".join(low_success[0].sequence)
            suggestions.append(
                f"[MITTEL] Sequenz mit niedriger Erfolgsrate: {seq_str} "
                f"({low_success[0].success_rate}%)"
            )

        return suggestions
