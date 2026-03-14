"""
Report Generator - Erstellt Claude-lesbare Analyse-Reports.

Generiert strukturierte Markdown-Reports mit:
- Executive Summary
- Performance-Bottlenecks
- Tool-Sequenz-Probleme
- Modell-Empfehlungen
- Konkrete Aktionen
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.pattern_detector import PatternDetector, PatternAnalysis


@dataclass
class AnalysisReport:
    """Vollstaendiger Analyse-Report."""
    generated_at: str
    period_days: int
    markdown: str
    summary: Dict[str, Any]
    recommendations: List[Dict[str, Any]]


class ReportGenerator:
    """
    Generiert strukturierte Reports fuer Claude-Analyse.

    Usage:
        generator = ReportGenerator(storage_path="./data/analytics")
        report = generator.generate(days=30)

        # Markdown ausgeben
        print(report.markdown)

        # Als Datei speichern
        generator.save_report(report, "analysis_report.md")
    """

    def __init__(self, storage_path: str):
        self.storage_path = Path(storage_path)
        self._chains: List[Dict] = []

    def generate(self, days: int = 30) -> AnalysisReport:
        """
        Generiert vollstaendigen Analyse-Report.

        Args:
            days: Anzahl der Tage fuer Analyse

        Returns:
            AnalysisReport mit Markdown und strukturierten Daten
        """
        # Daten laden
        self._load_chains(days)

        # Pattern-Analyse durchfuehren
        detector = PatternDetector(str(self.storage_path))
        patterns = detector.analyze(days=days)

        # Zusaetzliche Statistiken berechnen
        stats = self._calculate_statistics()
        tool_stats = self._calculate_tool_statistics()
        model_stats = self._calculate_model_statistics()

        # Empfehlungen generieren
        recommendations = self._generate_recommendations(
            patterns, tool_stats, model_stats
        )

        # Markdown generieren
        markdown = self._generate_markdown(
            days=days,
            stats=stats,
            tool_stats=tool_stats,
            model_stats=model_stats,
            patterns=patterns,
            recommendations=recommendations,
        )

        return AnalysisReport(
            generated_at=datetime.utcnow().isoformat(),
            period_days=days,
            markdown=markdown,
            summary=stats,
            recommendations=recommendations,
        )

    def save_report(
        self,
        report: AnalysisReport,
        filename: str = "analysis_report.md"
    ) -> Path:
        """Speichert Report als Datei."""
        report_path = self.storage_path / filename

        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report.markdown)

        return report_path

    # ═══════════════════════════════════════════════════════════════════════════
    # Daten laden
    # ═══════════════════════════════════════════════════════════════════════════

    def _load_chains(self, days: int) -> None:
        """Laedt Chain-Daten."""
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
                            self._chains.append(json.loads(line.strip()))
                        except json.JSONDecodeError:
                            continue
            except Exception:
                continue

    # ═══════════════════════════════════════════════════════════════════════════
    # Statistik-Berechnung
    # ═══════════════════════════════════════════════════════════════════════════

    def _calculate_statistics(self) -> Dict[str, Any]:
        """Berechnet Gesamt-Statistiken."""
        if not self._chains:
            return {
                "total_chains": 0,
                "success_rate": 0,
                "avg_iterations": 0,
                "avg_duration_ms": 0,
            }

        success_count = sum(
            1 for c in self._chains
            if c.get("final_status") == "resolved"
        )
        total_iterations = sum(
            c.get("total_iterations", 0) for c in self._chains
        )
        total_duration = sum(
            c.get("duration_ms", 0) for c in self._chains
        )

        return {
            "total_chains": len(self._chains),
            "success_count": success_count,
            "success_rate": round(success_count / len(self._chains) * 100, 1),
            "avg_iterations": round(total_iterations / len(self._chains), 2),
            "avg_duration_ms": total_duration // len(self._chains),
            "failed_count": len(self._chains) - success_count,
        }

    def _calculate_tool_statistics(self) -> Dict[str, Dict]:
        """Berechnet Tool-Statistiken."""
        tool_stats: Dict[str, Dict] = {}

        for chain in self._chains:
            for tool in chain.get("tool_chain", []):
                tool_name = tool.get("tool", "unknown")

                if tool_name not in tool_stats:
                    tool_stats[tool_name] = {
                        "total": 0,
                        "success": 0,
                        "errors": 0,
                        "total_duration_ms": 0,
                        "error_types": {},
                    }

                stats = tool_stats[tool_name]
                stats["total"] += 1
                stats["total_duration_ms"] += tool.get("duration_ms", 0)

                if tool.get("status") == "success":
                    stats["success"] += 1
                else:
                    stats["errors"] += 1
                    error_type = tool.get("error_type", "other")
                    stats["error_types"][error_type] = \
                        stats["error_types"].get(error_type, 0) + 1

        # Berechnete Felder hinzufuegen
        for tool_name, stats in tool_stats.items():
            stats["success_rate"] = round(
                stats["success"] / stats["total"] * 100, 1
            ) if stats["total"] > 0 else 0

            stats["avg_duration_ms"] = (
                stats["total_duration_ms"] // stats["total"]
            ) if stats["total"] > 0 else 0

        return tool_stats

    def _calculate_model_statistics(self) -> Dict[str, Dict]:
        """Berechnet Modell-Statistiken."""
        model_stats: Dict[str, Dict] = {}

        for chain in self._chains:
            model = chain.get("model", "unknown")

            if model not in model_stats:
                model_stats[model] = {
                    "total": 0,
                    "success": 0,
                    "total_iterations": 0,
                    "total_duration_ms": 0,
                }

            stats = model_stats[model]
            stats["total"] += 1
            stats["total_iterations"] += chain.get("total_iterations", 0)
            stats["total_duration_ms"] += chain.get("duration_ms", 0)

            if chain.get("final_status") == "resolved":
                stats["success"] += 1

        # Berechnete Felder
        for model, stats in model_stats.items():
            stats["success_rate"] = round(
                stats["success"] / stats["total"] * 100, 1
            ) if stats["total"] > 0 else 0

            stats["avg_iterations"] = round(
                stats["total_iterations"] / stats["total"], 2
            ) if stats["total"] > 0 else 0

            stats["avg_duration_ms"] = (
                stats["total_duration_ms"] // stats["total"]
            ) if stats["total"] > 0 else 0

        return model_stats

    # ═══════════════════════════════════════════════════════════════════════════
    # Empfehlungen
    # ═══════════════════════════════════════════════════════════════════════════

    def _generate_recommendations(
        self,
        patterns: PatternAnalysis,
        tool_stats: Dict[str, Dict],
        model_stats: Dict[str, Dict],
    ) -> List[Dict[str, Any]]:
        """Generiert priorisierte Empfehlungen."""
        recommendations = []

        # 1. Tools mit niedriger Erfolgsrate
        for tool, stats in tool_stats.items():
            if stats["total"] >= 10 and stats["success_rate"] < 70:
                recommendations.append({
                    "priority": "HIGH",
                    "category": "tool_reliability",
                    "title": f"Tool '{tool}' verbessern",
                    "issue": f"Erfolgsrate nur {stats['success_rate']}% "
                             f"bei {stats['total']} Aufrufen",
                    "action": "Fehlerbehandlung und Retry-Logik implementieren",
                    "impact": "Reduziert fehlgeschlagene Chains",
                })

        # 2. Langsame Tools
        for tool, stats in tool_stats.items():
            if stats["total"] >= 10 and stats["avg_duration_ms"] > 5000:
                recommendations.append({
                    "priority": "MEDIUM",
                    "category": "performance",
                    "title": f"Tool '{tool}' beschleunigen",
                    "issue": f"Durchschnitt {stats['avg_duration_ms']}ms",
                    "action": "Caching, Timeouts oder parallele Ausfuehrung pruefen",
                    "impact": "Schnellere Antwortzeiten",
                })

        # 3. Loop-Probleme
        for loop in patterns.loops_detected[:3]:
            recommendations.append({
                "priority": "HIGH",
                "category": "efficiency",
                "title": f"Loop bei '{loop.loop_tool}' beheben",
                "issue": f"{loop.frequency}x erkannt, {len(loop.sequence)} Wiederholungen",
                "action": loop.suggestion,
                "impact": "Weniger Iterationen pro Chain",
            })

        # 4. Modell-Optimierung
        if patterns.recommended_models:
            recommendations.append({
                "priority": "MEDIUM",
                "category": "model_selection",
                "title": "Modell-Auswahl optimieren",
                "issue": "Unterschiedliche Modelle haben unterschiedliche Staerken",
                "action": f"Empfohlene Modelle: {patterns.recommended_models}",
                "impact": "Bessere Erfolgsrate und Kosteneffizienz",
            })

        # 5. Fehler-Patterns
        for failure in patterns.failure_patterns[:2]:
            recommendations.append({
                "priority": "MEDIUM",
                "category": "error_handling",
                "title": f"Fehler '{failure.error_type}' in '{failure.tool}'",
                "issue": f"{failure.frequency}x aufgetreten",
                "action": failure.suggestion,
                "impact": "Weniger Fehler bei betroffenen Kategorien",
            })

        # Nach Prioritaet sortieren
        priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        return sorted(
            recommendations,
            key=lambda r: priority_order.get(r["priority"], 3)
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # Markdown-Generierung
    # ═══════════════════════════════════════════════════════════════════════════

    def _generate_markdown(
        self,
        days: int,
        stats: Dict[str, Any],
        tool_stats: Dict[str, Dict],
        model_stats: Dict[str, Dict],
        patterns: PatternAnalysis,
        recommendations: List[Dict[str, Any]],
    ) -> str:
        """Generiert Markdown-Report."""
        lines = []

        # Header
        lines.append("# AI-Assist Analytics Report")
        lines.append("")
        lines.append(f"**Zeitraum**: Letzte {days} Tage")
        lines.append(f"**Generiert**: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
        lines.append("")

        # Executive Summary
        lines.append("## Executive Summary")
        lines.append("")
        lines.append(f"- **Chains analysiert**: {stats['total_chains']}")
        lines.append(f"- **Erfolgsrate**: {stats['success_rate']}%")
        lines.append(f"- **Durchschnittliche Iterationen**: {stats['avg_iterations']}")
        lines.append(f"- **Durchschnittliche Dauer**: {stats['avg_duration_ms']}ms")
        lines.append("")

        if patterns.loops_detected:
            lines.append(f"**Hauptproblem**: {len(patterns.loops_detected)} Tool-Loops erkannt")
        elif patterns.failure_patterns:
            lines.append(f"**Hauptproblem**: {len(patterns.failure_patterns)} Fehlermuster")
        lines.append("")

        # Tool-Performance
        lines.append("## 1. Tool-Performance")
        lines.append("")
        lines.append("| Tool | Aufrufe | Erfolg | Avg Dauer | Status |")
        lines.append("|------|---------|--------|-----------|--------|")

        sorted_tools = sorted(
            tool_stats.items(),
            key=lambda x: -x[1]["total"]
        )

        for tool, ts in sorted_tools[:10]:
            status = "[OK]" if ts["success_rate"] >= 80 else \
                     "[!]" if ts["success_rate"] >= 50 else "[X]"
            lines.append(
                f"| {tool} | {ts['total']} | {ts['success_rate']}% | "
                f"{ts['avg_duration_ms']}ms | {status} |"
            )
        lines.append("")

        # Fehlertypen
        if patterns.error_prone_tools:
            lines.append("### Fehleranfaellige Tools")
            lines.append("")
            for tool, rate in sorted(
                patterns.error_prone_tools.items(),
                key=lambda x: -x[1]
            )[:5]:
                lines.append(f"- **{tool}**: {rate}% Fehlerrate")
            lines.append("")

        # Tool-Sequenz-Probleme
        lines.append("## 2. Tool-Sequenz-Analyse")
        lines.append("")

        if patterns.loops_detected:
            lines.append("### Erkannte Loops")
            lines.append("")
            lines.append("| Tool | Haeufigkeit | Max Wiederholungen | Empfehlung |")
            lines.append("|------|-------------|-------------------|------------|")

            for loop in patterns.loops_detected[:5]:
                lines.append(
                    f"| {loop.loop_tool} | {loop.frequency}x | "
                    f"{len(loop.sequence)} | {loop.suggestion[:40]}... |"
                )
            lines.append("")

        if patterns.frequent_sequences:
            lines.append("### Haeufige Sequenzen")
            lines.append("")

            for seq in patterns.frequent_sequences[:5]:
                if seq.is_loop:
                    continue
                seq_str = " -> ".join(seq.sequence)
                status = "[OK]" if seq.success_rate >= 80 else "[!]"
                lines.append(
                    f"- {status} `{seq_str}` ({seq.frequency}x, "
                    f"{seq.success_rate}% Erfolg, {seq.avg_duration_ms}ms)"
                )
            lines.append("")

        # Modell-Analyse
        lines.append("## 3. Modell-Performance")
        lines.append("")
        lines.append("| Modell | Chains | Erfolg | Avg Iterationen | Avg Dauer |")
        lines.append("|--------|--------|--------|-----------------|-----------|")

        sorted_models = sorted(
            model_stats.items(),
            key=lambda x: -x[1]["success_rate"]
        )

        for model, ms in sorted_models:
            lines.append(
                f"| {model} | {ms['total']} | {ms['success_rate']}% | "
                f"{ms['avg_iterations']} | {ms['avg_duration_ms']}ms |"
            )
        lines.append("")

        if patterns.recommended_models:
            lines.append("### Modell-Empfehlungen")
            lines.append("")
            for category, model in patterns.recommended_models.items():
                lines.append(f"- **{category}**: {model}")
            lines.append("")

        # Empfehlungen
        lines.append("## 4. Handlungsempfehlungen")
        lines.append("")

        high_priority = [r for r in recommendations if r["priority"] == "HIGH"]
        medium_priority = [r for r in recommendations if r["priority"] == "MEDIUM"]

        if high_priority:
            lines.append("### HOCH Prioritaet")
            lines.append("")
            for i, rec in enumerate(high_priority, 1):
                lines.append(f"**{i}. {rec['title']}**")
                lines.append(f"- Problem: {rec['issue']}")
                lines.append(f"- Aktion: {rec['action']}")
                lines.append(f"- Impact: {rec['impact']}")
                lines.append("")

        if medium_priority:
            lines.append("### MITTEL Prioritaet")
            lines.append("")
            for i, rec in enumerate(medium_priority, 1):
                lines.append(f"**{i}. {rec['title']}**")
                lines.append(f"- Problem: {rec['issue']}")
                lines.append(f"- Aktion: {rec['action']}")
                lines.append("")

        # Zusammenfassung
        lines.append("## 5. Naechste Schritte")
        lines.append("")
        lines.append("Basierend auf dieser Analyse empfehle ich:")
        lines.append("")

        if high_priority:
            lines.append(f"1. [ ] {high_priority[0]['title']}")
            if len(high_priority) > 1:
                lines.append(f"2. [ ] {high_priority[1]['title']}")
        if medium_priority:
            start = len(high_priority) + 1
            lines.append(f"{start}. [ ] {medium_priority[0]['title']}")

        lines.append("")
        lines.append("---")
        lines.append(f"*Report generiert von AI-Assist Analytics*")

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# Convenience-Funktion
# ═══════════════════════════════════════════════════════════════════════════════

def generate_analysis_report(
    storage_path: str = "./data/analytics",
    days: int = 30,
    save: bool = True,
) -> AnalysisReport:
    """
    Convenience-Funktion zum Generieren eines Reports.

    Args:
        storage_path: Pfad zu Analytics-Daten
        days: Analysezeitraum
        save: Report als Datei speichern?

    Returns:
        AnalysisReport
    """
    generator = ReportGenerator(storage_path)
    report = generator.generate(days=days)

    if save:
        generator.save_report(report)

    return report
