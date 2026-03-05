import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# WLP log formats:
# [DD/MM/YY HH:MM:SS:mmm ZZZ] threadid component   LEVEL message
# or: [YYYY-MM-DD HH:MM:SS.mmm] threadid component LEVEL message
LOG_LINE_RE = re.compile(
    r"^\[(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\s+\d{2}:\d{2}:\d{2}[.:\d]*\s*\S*)\]\s+"
    r"(\S+)\s+(\S+)\s+([A-Z])\s+(.*)"
)

LEVEL_MAP = {
    "E": "ERROR",
    "W": "WARNING",
    "I": "INFO",
    "A": "AUDIT",
    "F": "FATAL",
    "D": "DEBUG",
    "O": "STDOUT",
    "S": "SYSTEM",
}

# IBM CWWK error codes
IBM_ERROR_CODE_RE = re.compile(r"\b(CWWK[A-Z]\d{4}[EWIAF])\b")


@dataclass
class LogEntry:
    timestamp: str
    thread_id: str
    component: str
    level: str
    message: str
    stack_trace: List[str] = field(default_factory=list)
    error_codes: List[str] = field(default_factory=list)


@dataclass
class ParsedLog:
    total_lines: int
    entries: List[LogEntry]
    error_count: int
    warning_count: int


class WLPLogParser:
    def parse(self, log_content: str) -> ParsedLog:
        lines = log_content.splitlines()
        entries: List[LogEntry] = []
        current_entry: Optional[LogEntry] = None
        total_lines = len(lines)

        for line in lines:
            match = LOG_LINE_RE.match(line)
            if match:
                if current_entry:
                    entries.append(current_entry)
                ts, thread_id, component, level_char, message = match.groups()
                level = LEVEL_MAP.get(level_char, level_char)
                codes = IBM_ERROR_CODE_RE.findall(line)
                current_entry = LogEntry(
                    timestamp=ts.strip(),
                    thread_id=thread_id,
                    component=component,
                    level=level,
                    message=message.strip(),
                    error_codes=codes,
                )
            else:
                # Continuation line (e.g., stack trace)
                if current_entry and (line.startswith("\t") or line.startswith("  ") or "at " in line):
                    current_entry.stack_trace.append(line.rstrip())

        if current_entry:
            entries.append(current_entry)

        error_count = sum(1 for e in entries if e.level in ("ERROR", "FATAL"))
        warning_count = sum(1 for e in entries if e.level == "WARNING")

        return ParsedLog(
            total_lines=total_lines,
            entries=entries,
            error_count=error_count,
            warning_count=warning_count,
        )

    def get_errors(self, parsed: ParsedLog) -> List[Dict]:
        """Return structured list of error/warning entries."""
        results = []
        for entry in parsed.entries:
            if entry.level in ("ERROR", "FATAL", "WARNING"):
                results.append({
                    "timestamp": entry.timestamp,
                    "level": entry.level,
                    "component": entry.component,
                    "message": entry.message,
                    "error_codes": entry.error_codes,
                    "stack_trace": entry.stack_trace[:20],  # cap lines
                })
        return results

    def format_for_context(self, parsed: ParsedLog) -> str:
        """Create a compact summary for LLM context."""
        lines = [
            f"Log-Analyse: {parsed.total_lines} Zeilen gesamt",
            f"Fehler: {parsed.error_count} | Warnungen: {parsed.warning_count}",
            "",
        ]

        # Deduplicate errors by message prefix
        seen_messages = set()
        error_entries = [e for e in parsed.entries if e.level in ("ERROR", "FATAL", "WARNING")]

        lines.append("Relevante Einträge:")
        for entry in error_entries[:30]:  # cap at 30 entries
            msg_key = entry.message[:80]
            if msg_key in seen_messages:
                continue
            seen_messages.add(msg_key)

            codes_str = f" [{', '.join(entry.error_codes)}]" if entry.error_codes else ""
            lines.append(f"\n[{entry.level}] {entry.timestamp}{codes_str}")
            lines.append(f"  {entry.component}: {entry.message}")
            if entry.stack_trace:
                # Show first relevant stack frame
                for frame in entry.stack_trace[:5]:
                    lines.append(f"  {frame}")

        return "\n".join(lines)
