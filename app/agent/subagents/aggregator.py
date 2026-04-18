"""
Result Aggregator - Merges multiple SubAgentResults into a cohesive response.

Phase 5 MVP: Structured (Markdown sections) or narrative (plain text) aggregation
without LLM synthesis. Pure string composition — deterministic, fast, testable.

Future enhancement: LLM-based synthesis for smoother narrative output.
"""

from dataclasses import dataclass
from typing import List

from app.agent.subagents.models import SubAgentResult


@dataclass
class AggregatedResponse:
    """Merged result of a multi-worker coordination."""

    response: str
    total_tasks: int
    successful_tasks: int
    failed_tasks: int
    total_elapsed_seconds: float


class ResultAggregator:
    """
    Combines sub-agent results into a single response for the user.

    Two aggregation styles:
    - "structured": Markdown with headings per subtask (best for reports)
    - "narrative": Plain text with separators (best for short answers)
    """

    def aggregate(
        self,
        results: List[SubAgentResult],
        style: str = "structured",
    ) -> AggregatedResponse:
        """
        Combine results into a single response.

        Args:
            results: SubAgentResults from coordinator
            style: "structured" or "narrative"

        Returns:
            AggregatedResponse with merged text and metrics
        """
        if not results:
            return AggregatedResponse(
                response="(No subtasks were executed.)",
                total_tasks=0,
                successful_tasks=0,
                failed_tasks=0,
                total_elapsed_seconds=0.0,
            )

        successful = [r for r in results if r.is_success]
        failed = [r for r in results if not r.is_success]
        total_elapsed = sum(r.elapsed_seconds for r in results)

        if style == "narrative":
            text = self._narrative(results)
        else:
            text = self._structured(results)

        return AggregatedResponse(
            response=text,
            total_tasks=len(results),
            successful_tasks=len(successful),
            failed_tasks=len(failed),
            total_elapsed_seconds=total_elapsed,
        )

    def _structured(self, results: List[SubAgentResult]) -> str:
        """
        Markdown-formatted aggregation with per-task sections.

        Example output:
            # Ergebnisse der parallelen Sub-Tasks

            ## 1. [✓] Analyze the build pipeline
            (response content...)

            ## 2. [✗] Optimize Docker layers
            Fehler: timeout after 60s
            ...
        """
        lines: List[str] = []
        lines.append("# Ergebnisse der parallelen Sub-Tasks")
        lines.append("")

        for idx, r in enumerate(results, start=1):
            marker = "✓" if r.is_success else "✗"
            desc = r.description.strip().rstrip(".")
            # Truncate very long task descriptions for heading
            if len(desc) > 100:
                desc = desc[:97] + "..."
            lines.append(f"## {idx}. [{marker}] {desc}")
            lines.append("")

            if r.is_success and r.response:
                lines.append(r.response.strip())
            elif not r.is_success:
                reason = f"Status: {r.status.value}"
                if r.error:
                    reason += f" — {r.error}"
                lines.append(f"**Sub-Task fehlgeschlagen.** {reason}")
            else:
                lines.append("_(Sub-Task lieferte keine Ausgabe)_")

            lines.append("")
            lines.append(f"_Dauer: {r.elapsed_seconds:.1f}s · Tool-Calls: {r.tool_calls_count}_")
            lines.append("")

        # Summary footer
        success_count = sum(1 for r in results if r.is_success)
        lines.append("---")
        lines.append(
            f"**Zusammenfassung:** {success_count}/{len(results)} Sub-Tasks erfolgreich "
            f"(gesamt {sum(r.elapsed_seconds for r in results):.1f}s, parallel)."
        )

        return "\n".join(lines)

    def _narrative(self, results: List[SubAgentResult]) -> str:
        """Plain-text concatenation with simple separators."""
        parts: List[str] = []
        for idx, r in enumerate(results, start=1):
            if r.is_success and r.response:
                parts.append(f"[Task {idx}] {r.response.strip()}")
            elif not r.is_success:
                error_msg = r.error or r.status.value
                parts.append(f"[Task {idx} fehlgeschlagen: {error_msg}]")

        if not parts:
            return "(Keine erfolgreichen Sub-Task-Ergebnisse.)"

        return "\n\n---\n\n".join(parts)


_singleton: ResultAggregator = None


def get_aggregator() -> ResultAggregator:
    """Get singleton ResultAggregator instance."""
    global _singleton
    if _singleton is None:
        _singleton = ResultAggregator()
    return _singleton
