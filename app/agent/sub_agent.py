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

        # Fokussierter System-Prompt für diesen Sub-Agent
        context_section = ""
        if conversation_context:
            context_section = f"\n\n=== KONVERSATIONS-KONTEXT ===\n{conversation_context}\n=== ENDE KONTEXT ===\n"

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
            "- SCHLECHT: 'Die Performance koennte verbessert werden'\n"
            "- GUT: 'Die Funktion `process_batch()` in `worker.py:128` hat O(n^2) Komplexitaet bei 10k+ Eintraegen'\n"
            "- Jedes Finding MUSS mindestens einen konkreten Verweis enthalten (Datei, Funktion, Metrik, ID)\n"
            f"{context_section}\n\n"
            "Wenn du alle Informationen gesammelt hast, antworte NUR mit diesem JSON:\n"
            "{\n"
            '  "summary": "Was wurde gefunden? (3-5 Saetze mit konkreten Details)",\n'
            '  "key_findings": [\n'
            '    "Konkretes Finding mit `datei:zeile` oder Metrik",\n'
            '    "Weiteres Finding mit Verweis auf konkrete Stelle"\n'
            '  ],\n'
            '  "sources": ["pfad/zur/datei.py", "JIRA-123", "wiki/page-id"]\n'
            "}"
        )

        # Tool-Schemas nur für erlaubte Tools
        all_schemas = tool_registry.get_openai_schemas(include_write_ops=False)
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
                        result = await tool_registry.execute(tc_name, **args)
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

        if final_result is None:
            # Kein sauberes JSON-Finish nach max_iterations
            # → Finaler LLM-Call OHNE Tools erzwingt eine Zusammenfassung
            final_result = await self._force_summary(
                messages, llm_client, total_tokens, start_ms
            )
        else:
            final_result.token_usage = total_tokens
            final_result.duration_ms = int(time.time() * 1000) - start_ms

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
                '  "key_findings": [\n'
                '    "Konkretes Finding mit `datei:zeile` oder Metrik — NICHT vage formulieren",\n'
                '    "Weiteres Finding"\n'
                '  ],\n'
                '  "sources": ["pfad/datei.py", "ID-123"]\n'
                "}"
            ),
        })

        try:
            # Chat OHNE Tools — LLM muss Text produzieren
            response_text = await default_llm_client.chat_quick(
                messages=summary_messages,
                model=self._model,
                temperature=0.1,
                max_tokens=1024,
            )
            total_tokens += len(response_text or "") // 4  # Grobe Schätzung

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
