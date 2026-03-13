"""
Tool Budget Manager - Verwaltet und optimiert das Tool-Budget.

Trackt Tool-Nutzung und generiert Hinweise fuer das LLM,
um effizientere Tool-Entscheidungen zu treffen.

Features:
- Budget-Tracking (Iterationen, Tool-Calls)
- Level-basierte Warnungen (NORMAL, LOW, CRITICAL)
- Optimierungsvorschlaege basierend auf Nutzungsmustern
- System-Prompt-Erweiterung mit Budget-Hinweisen
"""

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class BudgetLevel(str, Enum):
    """Budget-Level fuer Warnungen."""
    CRITICAL = "critical"  # < 5 Iterationen
    LOW = "low"            # < 10 Iterationen
    NORMAL = "normal"      # >= 10 Iterationen


@dataclass
class ToolUsageStats:
    """Statistiken ueber Tool-Nutzung."""
    tool_name: str
    call_count: int = 0
    total_duration_ms: float = 0.0
    cache_hits: int = 0
    last_call: Optional[datetime] = None

    @property
    def avg_duration_ms(self) -> float:
        """Durchschnittliche Ausfuehrungszeit."""
        if self.call_count == 0:
            return 0.0
        return self.total_duration_ms / self.call_count


@dataclass
class ToolBudget:
    """
    Verwaltet das Tool-Budget fuer eine Session.

    Trackt:
    - Anzahl der Iterationen
    - Tool-Aufrufe pro Iteration
    - Tool-Nutzungsmuster
    """

    # Konfiguration
    max_iterations: int = 30
    max_tools_per_iteration: int = 10

    # Thresholds fuer Warnungen
    low_threshold: int = 10       # LOW-Warnung ab hier
    critical_threshold: int = 5   # CRITICAL-Warnung ab hier

    # Tracking
    current_iteration: int = 0
    tools_this_iteration: int = 0
    total_tools_used: int = 0

    # Historie
    tool_history: List[str] = field(default_factory=list)
    iteration_history: List[int] = field(default_factory=list)  # Tools pro Iteration

    # Detaillierte Statistiken
    tool_stats: Dict[str, ToolUsageStats] = field(default_factory=dict)

    # Cache-Tracking
    cache_hits: int = 0
    cache_misses: int = 0

    @property
    def remaining_iterations(self) -> int:
        """Verbleibende Iterationen."""
        return max(0, self.max_iterations - self.current_iteration)

    @property
    def level(self) -> BudgetLevel:
        """Aktuelles Budget-Level."""
        if self.remaining_iterations < self.critical_threshold:
            return BudgetLevel.CRITICAL
        elif self.remaining_iterations < self.low_threshold:
            return BudgetLevel.LOW
        return BudgetLevel.NORMAL

    @property
    def is_exhausted(self) -> bool:
        """Ist das Budget erschoepft?"""
        return self.remaining_iterations <= 0

    @property
    def usage_percent(self) -> float:
        """Prozent des verbrauchten Budgets."""
        if self.max_iterations == 0:
            return 100.0
        return (self.current_iteration / self.max_iterations) * 100

    def record_tool_call(
        self,
        tool_name: str,
        duration_ms: float = 0.0,
        cached: bool = False
    ) -> None:
        """
        Zeichnet einen Tool-Aufruf auf.

        Args:
            tool_name: Name des aufgerufenen Tools
            duration_ms: Ausfuehrungszeit in Millisekunden
            cached: War es ein Cache-Hit?
        """
        self.tools_this_iteration += 1
        self.total_tools_used += 1
        self.tool_history.append(tool_name)

        # Cache-Tracking
        if cached:
            self.cache_hits += 1
        else:
            self.cache_misses += 1

        # Detaillierte Stats
        if tool_name not in self.tool_stats:
            self.tool_stats[tool_name] = ToolUsageStats(tool_name=tool_name)

        stats = self.tool_stats[tool_name]
        stats.call_count += 1
        stats.total_duration_ms += duration_ms
        stats.last_call = datetime.now()
        if cached:
            stats.cache_hits += 1

        logger.debug(
            f"[Budget] Tool: {tool_name}, "
            f"Iteration: {self.current_iteration}/{self.max_iterations}, "
            f"Tools: {self.tools_this_iteration}"
        )

    def next_iteration(self) -> None:
        """Wechselt zur naechsten Iteration."""
        # Historie speichern
        self.iteration_history.append(self.tools_this_iteration)

        # Reset fuer neue Iteration
        self.current_iteration += 1
        self.tools_this_iteration = 0

        logger.debug(
            f"[Budget] Neue Iteration: {self.current_iteration}/{self.max_iterations} "
            f"(Level: {self.level.value})"
        )

    def get_top_tools(self, n: int = 5) -> List[Tuple[str, int]]:
        """Gibt die am haeufigsten verwendeten Tools zurueck."""
        counts = Counter(self.tool_history)
        return counts.most_common(n)

    def get_recent_tools(self, n: int = 5) -> List[str]:
        """Gibt die letzten N verwendeten Tools zurueck."""
        return self.tool_history[-n:] if self.tool_history else []

    def get_budget_hint(self) -> str:
        """
        Generiert einen Hinweis fuer den System-Prompt.

        Wird nur bei LOW oder CRITICAL Budget zurueckgegeben.
        """
        if self.level == BudgetLevel.NORMAL:
            return ""

        recent = self.get_recent_tools(5)
        recent_str = ", ".join(recent) if recent else "keine"

        if self.level == BudgetLevel.CRITICAL:
            return f"""
## TOOL-BUDGET KRITISCH

Verbleibende Iterationen: {self.remaining_iterations}/{self.max_iterations}
Bisherige Tool-Aufrufe: {self.total_tools_used}
Letzte Tools: {recent_str}

**WICHTIG - Optimiere sofort:**
1. Nutze `combined_search` statt einzelner search_*-Tools
2. Nutze `batch_read_files` statt mehrfachem read_file
3. Nutze `search_code(read_files=True)` um Suche+Lesen zu kombinieren
4. Fasse mehrere Fragen in einer Antwort zusammen
5. Gib eine abschliessende Antwort wenn moeglich
"""

        elif self.level == BudgetLevel.LOW:
            return f"""
## Tool-Budget Hinweis

Verbleibend: {self.remaining_iterations}/{self.max_iterations} Iterationen
Verbraucht: {self.total_tools_used} Tool-Aufrufe

**Tipp:** Nutze kombinierte Tools wie `combined_search`, `batch_read_files` und `batch_write_files` fuer effizientere Ausfuehrung.
"""

        return ""

    def get_optimization_suggestions(self) -> List[str]:
        """
        Analysiert Nutzungsmuster und gibt Optimierungsvorschlaege.

        Returns:
            Liste von Optimierungsvorschlaegen
        """
        suggestions = []
        counts = Counter(self.tool_history)

        # 1. Mehrfache Suchen -> combined_search
        search_tools = ["search_code", "search_handbook", "search_skills"]
        total_searches = sum(counts.get(t, 0) for t in search_tools)

        if total_searches >= 3:
            tools_used = [t for t in search_tools if counts.get(t, 0) > 0]
            suggestions.append(
                f"Du hast {total_searches}x Suchen ausgefuehrt ({', '.join(tools_used)}). "
                f"Nutze `combined_search(sources='code,handbook,skills')` fuer parallele Suche in einem Aufruf."
            )

        # 2. Mehrfache read_file -> batch_read_files
        read_count = counts.get("read_file", 0)
        if read_count >= 4:
            suggestions.append(
                f"Du hast {read_count}x read_file ausgefuehrt. "
                f"Nutze `batch_read_files(paths='file1,file2,file3')` um mehrere Dateien parallel zu lesen."
            )

        # 2b. Mehrfache write_file -> batch_write_files
        write_count = counts.get("write_file", 0)
        if write_count >= 2:
            suggestions.append(
                f"Du hast {write_count}x write_file ausgefuehrt. "
                f"Nutze `batch_write_files(files='[{{\"path\": \"...\", \"content\": \"...\"}}]')` "
                f"um mehrere Dateien mit EINER Bestaetigung zu schreiben."
            )

        # 3. search_code ohne read_files
        if counts.get("search_code", 0) >= 2 and counts.get("read_file", 0) >= 2:
            suggestions.append(
                "Tipp: Nutze `search_code(query='...', read_files=True)` "
                "um gefundene Dateien direkt mitzulesen (spart separate read_file Aufrufe)."
            )

        # 4. Wiederholte gleiche Tools
        for tool, count in counts.most_common(3):
            if count >= 5:
                suggestions.append(
                    f"Tool `{tool}` wurde {count}x aufgerufen. "
                    f"Prüfe ob die Aufrufe kombiniert werden koennen."
                )

        # 5. Niedrige Cache-Hit-Rate
        total_cache = self.cache_hits + self.cache_misses
        if total_cache > 5:
            hit_rate = (self.cache_hits / total_cache) * 100
            if hit_rate < 20:
                suggestions.append(
                    f"Niedrige Cache-Nutzung ({hit_rate:.0f}% Hit-Rate). "
                    f"Wiederholte aehnliche Suchen mit leicht unterschiedlichen Queries? "
                    f"Verwende konsistente, spezifische Suchbegriffe."
                )

        return suggestions

    def get_efficiency_score(self) -> float:
        """
        Berechnet einen Effizienz-Score (0-100).

        Basiert auf:
        - Cache-Hit-Rate
        - Nutzung von Meta-Tools
        - Tools pro Iteration
        """
        score = 100.0
        counts = Counter(self.tool_history)

        # Cache-Hit-Rate (max 30 Punkte)
        total_cache = self.cache_hits + self.cache_misses
        if total_cache > 0:
            hit_rate = self.cache_hits / total_cache
            score -= (1 - hit_rate) * 30

        # Meta-Tool-Nutzung (max 20 Punkte Bonus)
        meta_tools = ["combined_search", "batch_read_files", "batch_write_files"]
        meta_usage = sum(counts.get(t, 0) for t in meta_tools)
        if meta_usage > 0 and self.total_tools_used > 0:
            meta_ratio = meta_usage / self.total_tools_used
            score += meta_ratio * 20  # Bonus fuer Meta-Tool Nutzung

        # Durchschnittliche Tools pro Iteration (max 20 Punkte Abzug)
        if self.current_iteration > 0:
            avg_tools = self.total_tools_used / self.current_iteration
            if avg_tools > 5:
                score -= min(20, (avg_tools - 5) * 4)

        # Redundante Suchen (max 30 Punkte Abzug)
        search_tools = ["search_code", "search_handbook", "search_skills"]
        total_searches = sum(counts.get(t, 0) for t in search_tools)
        if total_searches > 6:
            score -= min(30, (total_searches - 6) * 5)

        return max(0, min(100, score))

    def get_summary(self) -> str:
        """Gibt eine lesbare Zusammenfassung zurueck."""
        lines = [
            "=== Tool-Budget Zusammenfassung ===",
            f"Status: {self.level.value.upper()}",
            f"Iterationen: {self.current_iteration}/{self.max_iterations} ({self.remaining_iterations} verbleibend)",
            f"Tool-Aufrufe: {self.total_tools_used}",
            f"Effizienz-Score: {self.get_efficiency_score():.0f}/100",
        ]

        # Cache-Stats
        total_cache = self.cache_hits + self.cache_misses
        if total_cache > 0:
            hit_rate = (self.cache_hits / total_cache) * 100
            lines.append(f"Cache: {self.cache_hits}/{total_cache} Hits ({hit_rate:.0f}%)")

        # Top Tools
        top = self.get_top_tools(5)
        if top:
            lines.append("")
            lines.append("Haeufigste Tools:")
            for tool, count in top:
                lines.append(f"  {tool}: {count}x")

        # Optimierungsvorschlaege
        suggestions = self.get_optimization_suggestions()
        if suggestions:
            lines.append("")
            lines.append("Optimierungsvorschlaege:")
            for s in suggestions[:3]:  # Max 3 Vorschlaege
                lines.append(f"  - {s}")

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Konvertiert zu Dictionary fuer Serialisierung."""
        return {
            "level": self.level.value,
            "current_iteration": self.current_iteration,
            "max_iterations": self.max_iterations,
            "remaining_iterations": self.remaining_iterations,
            "total_tools_used": self.total_tools_used,
            "tools_this_iteration": self.tools_this_iteration,
            "usage_percent": round(self.usage_percent, 1),
            "efficiency_score": round(self.get_efficiency_score(), 1),
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "top_tools": self.get_top_tools(5),
        }


def create_budget(
    max_iterations: int = 30,
    max_tools_per_iteration: int = 10,
    low_threshold: int = 10,
    critical_threshold: int = 5
) -> ToolBudget:
    """
    Factory-Funktion fuer ToolBudget.

    Args:
        max_iterations: Maximale Iterationen
        max_tools_per_iteration: Max Tools pro Iteration
        low_threshold: Schwelle fuer LOW-Warnung
        critical_threshold: Schwelle fuer CRITICAL-Warnung

    Returns:
        Konfiguriertes ToolBudget
    """
    return ToolBudget(
        max_iterations=max_iterations,
        max_tools_per_iteration=max_tools_per_iteration,
        low_threshold=low_threshold,
        critical_threshold=critical_threshold
    )
