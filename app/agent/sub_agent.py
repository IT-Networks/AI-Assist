"""
Sub-Agent-System – Parallele Erkundung von Datenquellen.

Architektur (analog zu Claude Code):
- Spezialisierte Sub-Agenten je Datenquelle (Code, Wiki, Jira, DB, Knowledge)
- Laufen parallel via asyncio.gather
- Haben eigenen Mini-LLM-Loop (max 5 Iterationen, isolierter Kontext)
- Geben komprimierte SubAgentResult-Zusammenfassungen zurück
- Main-Orchestrator synthetisiert nur noch – kein Rohdaten-Overflow im Haupt-Kontext

Routing:
- LLM-basiertes Intent-Routing: tool_model (qwen-7b o.ä.) klassifiziert die Anfrage
- Fallback auf Keyword-Matching wenn LLM-Routing fehlschlägt
"""

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.services.llm_client import (
    llm_client as default_llm_client,
    TIMEOUT_QUICK,
    TIMEOUT_TOOL,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Pre-compiled Regex Patterns (Performance: avoid re-compilation on each call)
# ══════════════════════════════════════════════════════════════════════════════
_RE_MISTRAL_COMPACT = re.compile(r'\[TOOL_CALLS\](\w+)(\{.*?\}|\[.*?\])', re.DOTALL)
_RE_MISTRAL_STANDARD = re.compile(r'\[TOOL_CALLS\]\s*(\[.*?\])', re.DOTALL)
_RE_XML_TOOL_CALL = re.compile(r'<(?:tool_call|functioncall)>(.*?)</(?:tool_call|functioncall)>', re.DOTALL | re.IGNORECASE)
_RE_JSON_BLOCK = re.compile(r'```(?:json)?\s*(\{.*?\})\s*```', re.DOTALL)
_RE_JSON_EXTRACT = re.compile(r"```(?:json)?\s*([\s\S]*?)```")


# ══════════════════════════════════════════════════════════════════════════════
# Text-basierter Tool-Call-Parser (Fallback für Modelle ohne natives Tool-Calling)
# ══════════════════════════════════════════════════════════════════════════════

def _parse_text_tool_calls(content: str, allowed_tools: List[str]) -> List[Dict]:
    """
    Parst Tool-Calls aus dem Text-Content von Modellen ohne natives Tool-Calling.

    Unterstützte Formate:
    1. Mistral Compact: [TOOL_CALLS]funcname{"arg": "val"}
    2. Mistral Standard: [TOOL_CALLS] [{"name": "func", "arguments": {...}}]
    3. XML: <tool_call>{"name": "func", "arguments": {...}}</tool_call>
    4. JSON-Block: ```json\n{"name": "func", ...}\n```
    """
    if not content:
        return []

    tool_names = set(allowed_tools) if allowed_tools else set()
    parsed_calls = []

    # Format 1a: Mistral Compact Format (pre-compiled pattern)
    mistral_compact_matches = _RE_MISTRAL_COMPACT.findall(content)
    if mistral_compact_matches:
        for name, args_str in mistral_compact_matches:
            if not tool_names or name in tool_names:
                try:
                    args = json.loads(args_str)
                    parsed_calls.append({
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(args) if isinstance(args, dict) else args_str
                        }
                    })
                except json.JSONDecodeError:
                    parsed_calls.append({
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {"name": name, "arguments": args_str}
                    })
        if parsed_calls:
            return parsed_calls

    # Format 1b: Mistral Standard Format (pre-compiled pattern)
    mistral_match = _RE_MISTRAL_STANDARD.search(content)
    if mistral_match:
        try:
            calls = json.loads(mistral_match.group(1))
            if isinstance(calls, list):
                for call in calls:
                    name = call.get("name") or call.get("function")
                    args = call.get("arguments") or call.get("parameters") or {}
                    if name and (not tool_names or name in tool_names):
                        parsed_calls.append({
                            "id": f"call_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(args) if isinstance(args, dict) else args
                            }
                        })
            if parsed_calls:
                return parsed_calls
        except (json.JSONDecodeError, KeyError):
            pass

    # Format 2: XML <tool_call> oder <functioncall> (pre-compiled pattern)
    xml_matches = _RE_XML_TOOL_CALL.findall(content)
    for match in xml_matches:
        try:
            data = json.loads(match.strip())
            name = data.get("name") or data.get("function")
            args = data.get("arguments") or data.get("parameters") or {}
            if name and (not tool_names or name in tool_names):
                parsed_calls.append({
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args) if isinstance(args, dict) else str(args)
                    }
                })
        except (json.JSONDecodeError, KeyError):
            pass
    if parsed_calls:
        return parsed_calls

    # Format 3: JSON-Block mit Tool-Call-Struktur (pre-compiled pattern)
    json_blocks = _RE_JSON_BLOCK.findall(content)
    for block in json_blocks:
        try:
            data = json.loads(block)
            name = data.get("name") or data.get("tool") or data.get("function")
            args = data.get("arguments") or data.get("parameters") or data.get("args") or {}
            if name and (not tool_names or name in tool_names):
                parsed_calls.append({
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args) if isinstance(args, dict) else str(args)
                    }
                })
        except (json.JSONDecodeError, KeyError):
            pass

    return parsed_calls


# ══════════════════════════════════════════════════════════════════════════════
# Ergebnis-Datenstruktur
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SubAgentResult:
    """Ergebnis eines Sub-Agenten – komprimierte Zusammenfassung für den Main-Agent."""
    agent_name: str
    success: bool
    summary: str                         # 200–500 Wörter für Main-LLM
    key_findings: List[str]              # Bullet-Points
    sources: List[str]                   # Dateipfade, Page-IDs, Issue-Keys etc.
    diagram: str = ""                    # Mermaid-Code (ohne Fences), optional
    diagram_title: str = ""              # Titel fuer das Diagramm
    token_usage: int = 0
    duration_ms: int = 0
    error: Optional[str] = None

    def to_context_block(self) -> str:
        """Formatiert das Ergebnis als System-Context-Block für den Main-Agent."""
        if not self.success:
            return f"[{self.agent_name}] Nicht verfügbar: {self.error or 'Fehler'}\n"

        lines = [f"[{self.agent_name}]"]
        if self.key_findings:
            for finding in self.key_findings:
                lines.append(f"• {finding}")
        if self.summary:
            lines.append(f"Zusammenfassung: {self.summary}")
        if self.sources:
            lines.append(f"Quellen: {', '.join(self.sources[:5])}")
        lines.append("")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# Sub-Agent Basis-Klasse
# ══════════════════════════════════════════════════════════════════════════════

class SubAgent:
    """
    Basis-Klasse für spezialisierte Sub-Agenten.

    Jeder Sub-Agent:
    - Hat eine fokussierte Tool-Whitelist (nur relevante Tools)
    - Läuft in eigenem Mini-LLM-Loop (max 5 Iterationen, überschreibbar via max_iterations)
    - Gibt eine komprimierte Zusammenfassung zurück
    - Nutzt das tool_model (schnelles Modell) für alle LLM-Calls
    - Kann Tool-Ergebnisse via _process_tool_result() nachbearbeiten
    """

    name: str = "base"
    display_name: str = "Sub-Agent"
    description: str = ""           # System-Prompt für diesen Sub-Agent
    allowed_tools: List[str] = []   # Tool-Whitelist aus bestehendem ToolRegistry
    max_iterations: Optional[int] = None  # None = settings.sub_agents.max_iterations verwenden

    # Tools für die Content-Extraktion aktiviert werden soll
    # Subklassen können dies überschreiben
    content_extraction_tools: List[str] = []

    def __init__(self):
        self._model: str = settings.llm.tool_model or settings.llm.default_model
        self._current_query: str = ""  # Wird in run() gesetzt für Hooks
        # Auto-Confirm fuer Write-Ops: Bei Teams mit Plan-Approval (Implementation-Team)
        # werden write_file/edit_file automatisch ausgefuehrt. Fuer normale SubAgents False.
        self.auto_confirm_writes: bool = False
        self._change_tracker = None  # Optional ChangeTracker fuer Rollback-Tracking
        # Write-Tracking (auch ausserhalb run() initialisiert, damit _direct_file_op
        # unabhaengig von run() aufrufbar ist)
        self._files_written_count: int = 0
        self._files_written_paths: List[str] = []

    async def _direct_file_op(self, tool_name: str, args: Dict) -> "ToolResult":
        """Fuehrt write_file/edit_file/create_directory direkt aus.

        BYPASS der FileManager-Restriktionen:
        - allowed_paths wird NICHT geprueft (Plan-Approval deckt Scope)
        - allowed_extensions wird NICHT geprueft (jedes Dateiformat erlaubt, z.B.
          .tsx, .rs, .go, .kt, Dockerfile, .toml - auch solche die nicht in
          config.yaml's file_operations.allowed_extensions stehen)
        - denied_patterns werden NICHT geprueft

        Wird nur bei auto_confirm_writes=True verwendet. Der User hat im Plan-Modal
        den kompletten Feature-Umfang genehmigt. Rollback ist via ChangeTracker moeglich.

        Relative Pfade werden:
        - absolut falls bereits absolut
        - sonst relativ zum aktuellen Arbeitsverzeichnis
        """
        from pathlib import Path
        from app.agent.tools import ToolResult

        raw_path = str(args.get("path", "")).strip()
        if not raw_path:
            return ToolResult(success=False, error=f"{tool_name}: path fehlt")

        try:
            p = Path(raw_path)
            resolved = p.resolve() if p.is_absolute() else (Path.cwd() / p).resolve()
        except Exception as e:
            return ToolResult(success=False, error=f"{tool_name}: ungueltiger path {raw_path!r}: {e}")

        if tool_name == "write_file":
            content = args.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            try:
                resolved.parent.mkdir(parents=True, exist_ok=True)
                is_new = not resolved.exists()
                resolved.write_text(content, encoding="utf-8")
                self._files_written_count += 1
                self._files_written_paths.append(str(resolved))
                logger.info(f"[sub_agent:{self.name}] DIRECT write_file {resolved} ({len(content)} chars, new={is_new})")
                return ToolResult(success=True, data=f"Datei {'erstellt' if is_new else 'ueberschrieben'}: {resolved}")
            except Exception as e:
                logger.exception(f"[sub_agent:{self.name}] direct write_file failed: {e}")
                return ToolResult(success=False, error=f"write_file {resolved}: {e}")

        if tool_name == "create_directory":
            try:
                already = resolved.exists()
                resolved.mkdir(parents=True, exist_ok=True)
                logger.info(f"[sub_agent:{self.name}] DIRECT create_directory {resolved} (existed={already})")
                msg = f"Verzeichnis existiert bereits: {resolved}" if already else f"Verzeichnis erstellt: {resolved}"
                return ToolResult(success=True, data=msg)
            except Exception as e:
                logger.exception(f"[sub_agent:{self.name}] direct create_directory failed: {e}")
                return ToolResult(success=False, error=f"create_directory {resolved}: {e}")

        if tool_name == "edit_file":
            old_string = args.get("old_string", "")
            new_string = args.get("new_string", "")
            replace_all = bool(args.get("replace_all", False))
            try:
                if not resolved.exists():
                    return ToolResult(success=False, error=f"edit_file: Datei existiert nicht: {resolved}")
                content = resolved.read_text(encoding="utf-8", errors="replace")
                if replace_all:
                    count = content.count(old_string)
                    if count == 0:
                        return ToolResult(success=False, error=f"edit_file: old_string nicht gefunden in {resolved}")
                    new_content = content.replace(old_string, new_string)
                else:
                    count = content.count(old_string)
                    if count == 0:
                        return ToolResult(success=False, error=f"edit_file: old_string nicht gefunden in {resolved}")
                    if count > 1:
                        return ToolResult(
                            success=False,
                            error=f"edit_file: old_string mehrdeutig ({count} Treffer) in {resolved}. Mehr Kontext oder replace_all=true."
                        )
                    new_content = content.replace(old_string, new_string, 1)
                resolved.write_text(new_content, encoding="utf-8")
                self._files_written_count += 1
                self._files_written_paths.append(str(resolved))
                logger.info(f"[sub_agent:{self.name}] DIRECT edit_file {resolved} ({count} Ersetzungen)")
                return ToolResult(success=True, data=f"Datei bearbeitet: {resolved} ({count} Ersetzungen)")
            except Exception as e:
                logger.exception(f"[sub_agent:{self.name}] direct edit_file failed: {e}")
                return ToolResult(success=False, error=f"edit_file {resolved}: {e}")

        return ToolResult(success=False, error=f"_direct_file_op: unbekanntes Tool {tool_name}")

    async def _auto_execute_write(self, confirmation_data: Dict) -> "ToolResult":
        """Fuehrt eine bestaetigte Write-Operation aus (fuer Teams mit Plan-Approval).

        WICHTIG: Umgeht die allowed_paths-Restriktion des FileManagers bewusst.
        Der User hat im Plan-Modal den kompletten Feature-Umfang genehmigt inkl.
        aller betroffenen Pfade. Rollback ist via ChangeTracker gewaehrleistet.
        Standard-Tool-Calls (ausserhalb Implementation-Team) bleiben durch
        allowed_paths im FileManager geschuetzt.
        """
        from pathlib import Path
        from app.agent.tools import ToolResult

        operation = confirmation_data.get("operation")
        path = confirmation_data.get("path")
        if not path:
            return ToolResult(success=False, error="auto_execute_write: path fehlt in confirmation_data")

        resolved = Path(path).resolve()

        if operation == "write_file":
            content = confirmation_data.get("content", "")
            try:
                resolved.parent.mkdir(parents=True, exist_ok=True)
                resolved.write_text(content, encoding="utf-8")
                logger.info(f"[sub_agent:{self.name}] wrote {resolved} ({len(content)} chars)")
                return ToolResult(success=True, data=f"Datei geschrieben: {resolved}")
            except Exception as e:
                logger.exception(f"[sub_agent:{self.name}] write_file direct failed: {e}")
                return ToolResult(success=False, error=f"write_file fehlgeschlagen: {e}")

        if operation == "edit_file":
            old_string = confirmation_data.get("old_string", "")
            new_string = confirmation_data.get("new_string", "")
            replace_all = bool(confirmation_data.get("replace_all", False))
            try:
                if not resolved.exists():
                    return ToolResult(success=False, error=f"edit_file: Datei existiert nicht: {resolved}")
                content = resolved.read_text(encoding="utf-8", errors="replace")
                if replace_all:
                    new_content = content.replace(old_string, new_string)
                else:
                    if content.count(old_string) != 1:
                        return ToolResult(
                            success=False,
                            error=f"edit_file: old_string nicht eindeutig ({content.count(old_string)} Treffer). Nutze replace_all=true oder erweitere Kontext."
                        )
                    new_content = content.replace(old_string, new_string, 1)
                resolved.write_text(new_content, encoding="utf-8")
                logger.info(f"[sub_agent:{self.name}] edited {resolved}")
                return ToolResult(success=True, data=f"Datei bearbeitet: {resolved}")
            except Exception as e:
                logger.exception(f"[sub_agent:{self.name}] edit_file direct failed: {e}")
                return ToolResult(success=False, error=f"edit_file fehlgeschlagen: {e}")

        return ToolResult(
            success=False,
            error=f"auto_confirm_writes: Operation '{operation}' wird nicht unterstuetzt."
        )

    def _track_change(self, confirmation_data: Dict) -> None:
        """Tracked eine Write-Operation im ChangeTracker fuer spaeteren Rollback."""
        if not self._change_tracker:
            return
        try:
            operation = confirmation_data.get("operation")
            path = confirmation_data.get("path")
            if operation == "write_file":
                is_new = confirmation_data.get("is_new", True)
                if is_new:
                    self._change_tracker.track_create(path, agent=self.name)
                else:
                    self._change_tracker.track_modify(path, agent=self.name)
            elif operation == "edit_file":
                self._change_tracker.track_modify(path, agent=self.name)
        except Exception as e:
            logger.warning(f"[sub_agent:{self.name}] ChangeTracker-Track fehlgeschlagen: {e}")

    async def run(
        self,
        query: str,
        llm_client,
        tool_registry,
        conversation_context: Optional[str] = None,
    ) -> SubAgentResult:
        """
        Führt den Sub-Agent-Loop aus und gibt eine Zusammenfassung zurück.

        Args:
            query: Die ursprüngliche Nutzer-Anfrage
            llm_client: LLMClient-Singleton
            tool_registry: ToolRegistry-Singleton
            conversation_context: Optionaler Kontext aus vorheriger Konversation
                                  (z.B. "User fragte ursprünglich nach X...")

        Returns:
            SubAgentResult mit Zusammenfassung und Key-Findings
        """
        start_ms = int(time.time() * 1000)
        max_iterations = self.max_iterations or settings.sub_agents.max_iterations

        # Query speichern für Hooks (z.B. Content-Extraktion)
        self._current_query = query

        # Zaehler fuer tatsaechlich geschriebene Dateien (nur bei auto_confirm_writes)
        self._files_written_count: int = 0
        self._files_written_paths: List[str] = []

        # Fokussierter System-Prompt für diesen Sub-Agent
        context_section = ""
        if conversation_context:
            context_section = f"\n\n=== KONVERSATIONS-KONTEXT ===\n{conversation_context}\n=== ENDE KONTEXT ===\n"

        # WICHTIG: Write-Agents (Implementation-Team) bekommen einen anderen Prompt
        # als Research-Agents, weil ihr Auftrag Code-Schreiben ist, nicht Suchen.
        if self.auto_confirm_writes:
            system_prompt = (
                f"Du bist ein Code-Schreib-Agent: {self.display_name}.\n"
                f"{self.description}\n\n"
                "AUFTRAG: Du SCHREIBST Dateien mit write_file. Du bist KEIN Such-Agent.\n\n"
                "ARBEITSWEISE (STRIKT in dieser Reihenfolge):\n"
                "1. Lies den Task-Kontext und den absoluten Ziel-Pfad aus.\n"
                "2. Wenn der Task-Text keinen absoluten Pfad enthaelt, benutze relative Pfade\n"
                "   zum aktuellen Arbeitsverzeichnis.\n"
                "3. Rufe SOFORT write_file auf - eine Datei pro Call. Kein search_code vorher!\n"
                "4. write_file schreibt unmittelbar (Plan wurde vom User vorab genehmigt).\n"
                "5. Nach JEDER angelegten Datei: naechsten write_file Call fuer die naechste Datei.\n"
                "6. ERST wenn ALLE geplanten Dateien geschrieben sind: antworte mit JSON-Summary.\n\n"
                "PFLICHT-REGELN:\n"
                "- Mindestens 1 erfolgreicher write_file Call - sonst gilt der Task als fehlgeschlagen.\n"
                "- KEIN search_code/read_file/list_files beim Greenfield-Implementation.\n"
                "  Diese nur verwenden wenn du explizit existierenden Code anpassen musst.\n"
                "- Bei Fehler vom write_file (z.B. SCHREIBVORGANG ABGELEHNT): Pfad im Task-Kontext\n"
                "  pruefen und korrigieren, KEIN Aufgeben.\n"
                "- Content muss funktionsfaehiger Code sein (nicht 'TODO'/'...'-Platzhalter).\n"
                f"{context_section}\n\n"
                "JSON-FINISH (erst nach allen write_file Calls):\n"
                "{\n"
                '  "summary": "Welche Dateien hast du geschrieben, was enthalten sie?",\n'
                '  "key_findings": ["Datei X geschrieben (Y Zeilen Zweck)", "..."],\n'
                '  "sources": ["absoluter/pfad/zu/datei1.py", "..."]\n'
                "}"
            )
        else:
            system_prompt = (
                f"Du bist ein spezialisierter Such-Agent: {self.display_name}.\n"
                f"{self.description}\n\n"
                "ARBEITSWEISE:\n"
                "1. Fuehre mehrere gezielte Tool-Aufrufe durch (nicht nur einen!)\n"
                "2. Sammle KONKRETE Daten: Dateinamen, Zeilennummern, Metriken, Code-Snippets\n"
                "3. Wenn ein Suchergebnis nicht spezifisch genug ist, suche gezielter nach\n"
                "4. Wenn du fertig bist, fasse deine Ergebnisse als JSON zusammen\n\n"
                "QUALITAETSREGELN fuer Findings:\n"
                "- SCHLECHT: 'Es wurden mehrere relevante Dateien gefunden'\n"
                "- GUT: 'In `src/auth/login.py:42` fehlt Input-Validierung fuer das email-Feld'\n"
                "- Jedes Finding MUSS mindestens einen konkreten Verweis enthalten (Datei, Funktion, Metrik, ID)\n"
                f"{context_section}\n\n"
                "Wenn du alle Informationen gesammelt hast, antworte NUR mit diesem JSON:\n"
                "{\n"
                '  "summary": "Was wurde gefunden? (3-5 Saetze mit konkreten Details)",\n'
                '  "key_findings": ["Konkretes Finding mit Verweis", "..."],\n'
                '  "sources": ["pfad/datei.py", "JIRA-123", "wiki/page-id"],\n'
                '  "diagram": "OPTIONAL: Mermaid-Code OHNE ```-Fences, z.B. sequenceDiagram\\n    A->>B: Request",\n'
                '  "diagram_title": "OPTIONAL: Titel fuer das Diagramm"\n'
                "}\n\n"
                "DIAGRAMM-REGELN (nur wenn es zur Aufgabe passt, sonst weglassen):\n"
                "- Service-Aufrufe/API-Calls gefunden → sequenceDiagram\n"
                "- Komponenten/Module/Architektur → flowchart TD\n"
                "- Datenbank-Tabellen/Entities → erDiagram\n"
                "- Prozess-Ablaeufe/Workflows → flowchart TD mit Entscheidungen\n"
                "- Kein passender Typ → diagram-Feld WEGLASSEN\n"
                "- Der Mermaid-Code muss VALIDE sein (keine Umlaute in IDs, Quotes escapen)"
            )

        # Tool-Schemas nur für erlaubte Tools
        # Wichtig: Write-Ops inkludieren wenn der Agent sie in allowed_tools hat
        # (z.B. Implementation-Team braucht write_file/edit_file)
        write_tools = {"write_file", "edit_file", "run_pytest", "run_npm_tests"}
        needs_writes = any(t in write_tools for t in self.allowed_tools)
        all_schemas = tool_registry.get_openai_schemas(include_write_ops=needs_writes)
        tool_schemas = [
            schema for schema in all_schemas
            if schema["function"]["name"] in self.allowed_tools
        ]

        # Debug: Log welche Tools verfügbar vs. erlaubt
        available_tools = [s["function"]["name"] for s in all_schemas]
        matching = [t for t in self.allowed_tools if t in available_tools]
        missing = [t for t in self.allowed_tools if t not in available_tools]
        if missing:
            logger.warning(f"[sub_agent:{self.name}] Fehlende Tools: {missing}")
        logger.debug(f"[sub_agent:{self.name}] {len(tool_schemas)} Tools verfügbar: {matching[:5]}...")

        if not tool_schemas:
            return SubAgentResult(
                agent_name=self.display_name,
                success=False,
                summary="",
                key_findings=[],
                sources=[],
                error="Keine Tools verfügbar oder konfiguriert",
                duration_ms=int(time.time() * 1000) - start_ms,
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ]

        total_tokens = 0
        final_result: Optional[SubAgentResult] = None

        for iteration in range(max_iterations):
            logger.debug(f"[sub_agent:{self.name}] Iteration {iteration + 1}/{max_iterations}")
            try:
                response_text, tool_calls_raw, _prompt_tk, _compl_tk = await self._call_llm(
                    llm_client, messages, tool_schemas
                )
                total_tokens += _prompt_tk + _compl_tk
                logger.debug(f"[sub_agent:{self.name}] LLM response: {len(tool_calls_raw)} tool_calls, {len(response_text or '')} chars, {_prompt_tk}+{_compl_tk} tokens")
            except Exception as e:
                logger.error(f"[sub_agent:{self.name}] LLM-Fehler in Iteration {iteration + 1}: {e}")
                return SubAgentResult(
                    agent_name=self.display_name,
                    success=False,
                    summary="",
                    key_findings=[],
                    sources=[],
                    error=f"LLM-Fehler: {e}",
                    duration_ms=int(time.time() * 1000) - start_ms,
                )

            # Fallback: Text-basiertes Tool-Call-Parsing für Modelle ohne natives Tool-Calling
            native_tools = bool(tool_calls_raw)
            if not tool_calls_raw and response_text:
                text_tool_calls = _parse_text_tool_calls(response_text, self.allowed_tools)
                if text_tool_calls:
                    logger.debug(f"[sub_agent:{self.name}] Text-Parser erkannte {len(text_tool_calls)} Tool-Calls")
                    tool_calls_raw = text_tool_calls
                    native_tools = False

            # Keine Tool-Calls mehr → Agent ist fertig
            if not tool_calls_raw:
                # Versuche JSON aus der Antwort zu parsen
                final_result = self._parse_final_response(response_text)
                break

            # Tool-Calls ausführen
            if native_tools:
                # OpenAI-kompatibles Format: content muss None sein wenn tool_calls vorhanden
                messages.append({
                    "role": "assistant",
                    "content": response_text if response_text else None,
                    "tool_calls": tool_calls_raw
                })
            else:
                # Text-basiertes Format: Kein tool_calls-Feld im assistant-Message
                # WICHTIG: Mistral/vLLM lehnt leere assistant-Messages ab (400 Bad Request)
                messages.append({
                    "role": "assistant",
                    "content": response_text if response_text else "(Tool-Aufrufe werden verarbeitet)"
                })

            tool_results = []
            for tc in tool_calls_raw:
                tc_name = tc.get("function", {}).get("name", "")
                tc_args_raw = tc.get("function", {}).get("arguments", "{}")
                tc_id = tc.get("id", f"call_{uuid.uuid4().hex[:8]}")

                if tc_name not in self.allowed_tools:
                    tool_content = f"[Fehler] Tool '{tc_name}' nicht erlaubt für diesen Sub-Agent."
                else:
                    try:
                        args = json.loads(tc_args_raw) if isinstance(tc_args_raw, str) else tc_args_raw
                    except json.JSONDecodeError:
                        args = {}

                    try:
                        # Verbose tool-call logging fuer Implementation-Team Debugging
                        if self.auto_confirm_writes and tc_name in ("write_file", "edit_file", "create_directory"):
                            _path_arg = args.get("path", "<none>")
                            _content_len = len(args.get("content", "") or "") if tc_name == "write_file" else None
                            logger.info(
                                f"[sub_agent:{self.name}] TOOL_CALL {tc_name} path={_path_arg!r} "
                                f"content_len={_content_len} args_keys={list(args.keys())}"
                            )

                        # BYPASS fuer Implementation-Team: Write-Ops direkt ausfuehren,
                        # ohne FileManager-Preview (der PermissionError werfen wuerde).
                        # Der User hat im Plan-Modal schon alles genehmigt.
                        if self.auto_confirm_writes and tc_name in ("write_file", "edit_file", "create_directory"):
                            result = await self._direct_file_op(tc_name, args)
                            if result.success and self._change_tracker:
                                self._track_change({
                                    "operation": tc_name,
                                    "path": args.get("path", ""),
                                    "is_new": True if tc_name in ("write_file", "create_directory") else False,
                                })
                            tool_content = result.to_context()
                            tool_results.append((tc_id, tc_name, tool_content))
                            continue  # Skip normale tool_registry.execute

                        result = await tool_registry.execute(tc_name, **args)

                        # Result-Logging
                        if self.auto_confirm_writes and tc_name in ("write_file", "edit_file", "create_directory"):
                            logger.info(
                                f"[sub_agent:{self.name}] TOOL_RESULT {tc_name} "
                                f"success={result.success} requires_confirm={getattr(result, 'requires_confirmation', False)} "
                                f"error={result.error!r} data={str(result.data)[:120]!r}"
                            )

                        # Auto-Confirm fuer Implementation-Team: write_file/edit_file
                        # liefern nur Preview mit requires_confirmation=True. Normal laeuft
                        # das ueber User-UI-Bestaetigung. In Teams mit auto_confirm_writes
                        # (nach Plan-Approval) fuehren wir direkt aus.
                        if (
                            result.success
                            and getattr(result, "requires_confirmation", False)
                            and getattr(result, "confirmation_data", None)
                            and self.auto_confirm_writes
                        ):
                            try:
                                cdata = result.confirmation_data
                                logger.info(
                                    f"[sub_agent:{self.name}] AUTO_CONFIRM exec op={cdata.get('operation')} "
                                    f"path={cdata.get('path')!r}"
                                )
                                exec_result = await self._auto_execute_write(cdata)
                                if exec_result.success:
                                    tool_content = exec_result.data or "(Datei geschrieben)"
                                    logger.info(f"[sub_agent:{self.name}] AUTO_CONFIRM OK: {tool_content[:120]}")
                                    if self._change_tracker:
                                        self._track_change(cdata)
                                else:
                                    tool_content = f"[Fehler beim Schreiben] {exec_result.error}"
                                    logger.error(f"[sub_agent:{self.name}] AUTO_CONFIRM FAILED: {exec_result.error}")
                            except Exception as ex:
                                logger.exception(f"[sub_agent:{self.name}] auto-confirm write failed: {ex}")
                                tool_content = f"[Fehler beim Schreiben] {ex}"
                        else:
                            tool_content = result.to_context()

                        # Hook: Content-Extraktion für große Ergebnisse
                        if tc_name in self.content_extraction_tools:
                            tool_content = await self._process_tool_result(
                                tc_name, tool_content, args, llm_client
                            )

                        # Token-Schätzung (grob: 1 Token ≈ 4 Zeichen)
                        total_tokens += len(tool_content) // 4
                    except Exception as e:
                        tool_content = f"[Fehler] {tc_name}: {e}"

                tool_results.append((tc_id, tc_name, tool_content))

            # Tool-Ergebnisse je nach Format hinzufügen
            if native_tools:
                # OpenAI-Format: role="tool" mit tool_call_id
                for tc_id, tc_name, tool_content in tool_results:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": tool_content,
                    })
            else:
                # Text-basiertes Format: Tool-Ergebnisse als user-Message
                results_text = "\n\n".join([
                    f"=== Ergebnis von {tc_name} ===\n{tool_content}"
                    for tc_id, tc_name, tool_content in tool_results
                ])
                messages.append({
                    "role": "user",
                    "content": f"Tool-Ergebnisse:\n\n{results_text}"
                })

        # Write-Mode nur aktiv fuer Agents die write-tools in ihrem Toolset haben
        # (sonst waere z.B. der Reviewer fehlschlagend weil er keinen write macht).
        has_write_tools = any(
            t in ("write_file", "edit_file", "create_directory")
            for t in self.allowed_tools
        )
        write_mode = self.auto_confirm_writes and has_write_tools

        if final_result is None:
            # Write-Mode: Wenn mindestens 1 Datei geschrieben wurde, akzeptieren
            # wir das auch ohne JSON-Finish als Erfolg. Agent hat "geliefert".
            if write_mode and self._files_written_count > 0:
                logger.info(
                    f"[sub_agent:{self.name}] Kein JSON-Finish, aber "
                    f"{self._files_written_count} Datei(en) geschrieben - als Erfolg werten"
                )
                final_result = SubAgentResult(
                    agent_name=self.display_name,
                    success=True,
                    summary=f"{self._files_written_count} Datei(en) erstellt/geaendert.",
                    key_findings=[f"{p}" for p in self._files_written_paths],
                    sources=list(self._files_written_paths),
                    token_usage=total_tokens,
                    duration_ms=int(time.time() * 1000) - start_ms,
                )
            else:
                # Kein sauberes JSON-Finish nach max_iterations
                # → Finaler LLM-Call OHNE Tools erzwingt eine Zusammenfassung
                final_result = await self._force_summary(
                    messages, llm_client, total_tokens, start_ms
                )
        else:
            final_result.token_usage = total_tokens
            final_result.duration_ms = int(time.time() * 1000) - start_ms
            # Write-Mode: Falls write-tools-Agent 0 Dateien geschrieben hat, gilt als failed
            if write_mode and self._files_written_count == 0:
                logger.warning(
                    f"[sub_agent:{self.name}] JSON-Finish ohne einzigen write_file Call - "
                    f"Task gilt als fehlgeschlagen"
                )
                final_result.success = False
                final_result.error = (
                    "Agent hat JSON-Summary geliefert, aber KEINE Datei geschrieben. "
                    "write_file Tool wurde nicht aufgerufen."
                )

        return final_result

    async def _call_llm(
        self,
        llm_client,
        messages: List[Dict],
        tool_schemas: List[Dict],
    ):
        """
        Ruft das LLM mit Tool-Definitionen auf (kein Streaming).
        Nutzt zentralen LLM-Client für Connection-Pooling und Retry.

        Returns:
            Tuple (response_text, tool_calls_raw, prompt_tokens, completion_tokens)
        """
        # Sub-Agenten nutzen tool_temperature (deterministischer)
        temperature = settings.llm.tool_temperature if settings.llm.tool_temperature >= 0 else 0.0

        # Reasoning für Sub-Agenten (normalerweise aus, da tool-fokussiert)
        reasoning = settings.llm.tool_reasoning or None

        response = await default_llm_client.chat_with_tools(
            messages=messages,
            tools=tool_schemas,
            model=self._model,
            temperature=temperature,
            max_tokens=min(settings.llm.max_tokens, 2048),  # Sub-Agenten brauchen weniger
            timeout=TIMEOUT_TOOL,
            reasoning=reasoning,
        )

        # Debug-Log bei unerwarteten Situationen
        if response.finish_reason == "tool_calls" and not response.tool_calls:
            logger.warning(f"[sub_agent:{self.name}] finish_reason='tool_calls' aber keine tool_calls!")
            if response.content:
                logger.debug(f"  Content (erste 300 Zeichen): {response.content[:300]}")

        return (
            response.content or "",
            response.tool_calls,
            getattr(response, "prompt_tokens", 0),
            getattr(response, "completion_tokens", 0),
        )

    async def _force_summary(
        self,
        messages: List[Dict],
        llm_client,
        total_tokens: int,
        start_ms: int,
    ) -> SubAgentResult:
        """
        Erzwingt eine Zusammenfassung wenn der Agent max_iterations erreicht hat,
        ohne ein JSON-Finish zu liefern.

        Macht einen finalen LLM-Call OHNE Tools — das LLM kann dann nur noch
        Text produzieren und muss die gesammelten Tool-Ergebnisse zusammenfassen.
        """
        # Prüfen ob überhaupt Tool-Ergebnisse vorliegen
        had_tool_calls = any(
            m.get("role") in ("tool",)
            or (m.get("role") == "user" and "Tool-Ergebnisse" in m.get("content", ""))
            for m in messages
        )

        if not had_tool_calls:
            # Kein einziger Tool-Call → nichts zum Zusammenfassen
            return SubAgentResult(
                agent_name=self.display_name,
                success=False,
                summary="",
                key_findings=[],
                sources=[],
                error="Keine Tool-Ergebnisse (max_iterations erreicht ohne Tool-Calls)",
                token_usage=total_tokens,
                duration_ms=int(time.time() * 1000) - start_ms,
            )

        logger.info(f"[sub_agent:{self.name}] max_iterations erreicht — erzwinge Summary-Call ohne Tools")

        # Finaler Call: Nachrichten + explizite Aufforderung zur Zusammenfassung
        summary_messages = list(messages)
        summary_messages.append({
            "role": "user",
            "content": (
                "STOPP — keine weiteren Tool-Aufrufe. Fasse JETZT deine Ergebnisse zusammen.\n\n"
                "Schaue dir die Tool-Ergebnisse oben an und extrahiere die KONKRETEN Informationen:\n"
                "- Welche Dateien/Funktionen/Klassen hast du gefunden?\n"
                "- Welche Metriken, Zahlen, Versionen?\n"
                "- Welche konkreten Probleme oder Erkenntnisse?\n\n"
                "Antworte NUR mit diesem JSON (KEINE Tool-Calls, KEIN anderer Text):\n"
                "{\n"
                '  "summary": "Was wurde konkret gefunden? (3-5 Saetze, mit Dateinamen und Metriken)",\n'
                '  "key_findings": ["Konkretes Finding mit Verweis", "..."],\n'
                '  "sources": ["pfad/datei.py", "ID-123"],\n'
                '  "diagram": "OPTIONAL: Mermaid-Code wenn passend, sonst weglassen",\n'
                '  "diagram_title": "OPTIONAL: Titel"\n'
                "}"
            ),
        })

        try:
            # Chat OHNE Tools — LLM muss Text produzieren
            response_text, p_tk, c_tk = await default_llm_client.chat_quick_with_usage(
                messages=summary_messages,
                model=self._model,
                temperature=0.1,
                max_tokens=1024,
            )
            total_tokens += p_tk + c_tk

            if response_text:
                result = self._parse_final_response(response_text)
                result.token_usage = total_tokens
                result.duration_ms = int(time.time() * 1000) - start_ms
                # Wenn der Agent gearbeitet hat, ist es ein Erfolg auch ohne perfektes JSON
                if not result.success and had_tool_calls:
                    result.success = True
                    if not result.summary:
                        result.summary = response_text[:500]
                logger.info(
                    f"[sub_agent:{self.name}] Force-Summary erfolgreich: "
                    f"{len(result.key_findings)} Findings, {len(result.summary)} chars"
                )
                return result

        except Exception as e:
            logger.warning(f"[sub_agent:{self.name}] Force-Summary LLM-Call fehlgeschlagen: {e}")

        # Letzter Fallback: Assistant-Texte aus dem Loop sammeln
        assistant_texts = []
        for m in messages:
            if m.get("role") == "assistant" and m.get("content"):
                text = m["content"]
                if text and text != "(Tool-Aufrufe werden verarbeitet)":
                    assistant_texts.append(text)
        last_assistant = assistant_texts[-1] if assistant_texts else ""

        return SubAgentResult(
            agent_name=self.display_name,
            success=bool(last_assistant) or had_tool_calls,
            summary=last_assistant[:500] if last_assistant else "",
            key_findings=[],
            sources=[],
            token_usage=total_tokens,
            duration_ms=int(time.time() * 1000) - start_ms,
        )

    async def _process_tool_result(
        self,
        tool_name: str,
        content: str,
        args: Dict[str, Any],
        llm_client,
    ) -> str:
        """
        Hook für Subklassen: Verarbeitet Tool-Ergebnisse vor dem Einfügen in den Context.

        Standardimplementierung: Gibt Content unverändert zurück.
        Subklassen können dies überschreiben für:
        - Content-Extraktion bei großen Dokumenten
        - Relevanz-Filterung
        - Zusammenfassungen

        Args:
            tool_name: Name des aufgerufenen Tools
            content: Rohes Tool-Ergebnis
            args: Argumente des Tool-Calls
            llm_client: LLMClient für weitere Calls

        Returns:
            Verarbeiteter Content (kann kürzer sein als Original)
        """
        return content

    def _parse_final_response(self, text: str) -> SubAgentResult:
        """Parst die finale JSON-Antwort des Sub-Agenten."""
        if not text:
            return SubAgentResult(
                agent_name=self.display_name,
                success=False,
                summary="",
                key_findings=[],
                sources=[],
                error="Leere Antwort",
            )

        # JSON aus Text extrahieren (auch aus Markdown-Blöcken, pre-compiled pattern)
        json_text = text
        if "```" in text:
            match = _RE_JSON_EXTRACT.search(text)
            if match:
                json_text = match.group(1).strip()

        # Letztes { ... } im Text finden
        start = json_text.rfind("{")
        end = json_text.rfind("}") + 1
        if start >= 0 and end > start:
            json_text = json_text[start:end]

        try:
            data = json.loads(json_text)
            return SubAgentResult(
                agent_name=self.display_name,
                success=True,
                summary=data.get("summary", ""),
                key_findings=data.get("key_findings", []),
                sources=data.get("sources", []),
                diagram=data.get("diagram", ""),
                diagram_title=data.get("diagram_title", ""),
            )
        except (json.JSONDecodeError, KeyError):
            # Kein valides JSON → Freitext als Summary
            return SubAgentResult(
                agent_name=self.display_name,
                success=bool(text.strip()),
                summary=text[:500],
                key_findings=[],
                sources=[],
            )


# ══════════════════════════════════════════════════════════════════════════════
# NOTE: SubAgentDispatcher + Keyword-Routing + format_sub_agent_results
# wurden in v2.31.5 entfernt. Das Multi-Agent Team System (run_team)
# ersetzt das automatische Sub-Agent-Dispatching.
#
# Die SubAgent-Basisklasse + SubAgentResult bleiben erhalten, da sie von
# TeamAgent (multi_agent/) und ResearchAgent (knowledge_collector/) geerbt werden.
# ══════════════════════════════════════════════════════════════════════════════

# Ende der Datei — SubAgentDispatcher und format_sub_agent_results entfernt in v2.31.5
