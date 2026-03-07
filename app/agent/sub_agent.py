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
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core.config import settings


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
    - Läuft in eigenem Mini-LLM-Loop (max 5 Iterationen)
    - Gibt eine komprimierte Zusammenfassung zurück
    - Nutzt das tool_model (schnelles Modell) für alle LLM-Calls
    """

    name: str = "base"
    display_name: str = "Sub-Agent"
    description: str = ""          # System-Prompt für diesen Sub-Agent
    allowed_tools: List[str] = []  # Tool-Whitelist aus bestehendem ToolRegistry

    def __init__(self):
        self._model: str = settings.llm.tool_model or settings.llm.default_model

    async def run(
        self,
        query: str,
        llm_client,
        tool_registry,
    ) -> SubAgentResult:
        """
        Führt den Sub-Agent-Loop aus und gibt eine Zusammenfassung zurück.

        Args:
            query: Die ursprüngliche Nutzer-Anfrage
            llm_client: LLMClient-Singleton
            tool_registry: ToolRegistry-Singleton

        Returns:
            SubAgentResult mit Zusammenfassung und Key-Findings
        """
        start_ms = int(time.time() * 1000)
        max_iterations = settings.sub_agents.max_iterations

        # Fokussierter System-Prompt für diesen Sub-Agent
        system_prompt = (
            f"Du bist ein spezialisierter Such-Agent: {self.display_name}.\n"
            f"{self.description}\n\n"
            "Deine Aufgabe: Suche alle relevanten Informationen zur Anfrage des Nutzers. "
            "Führe mehrere gezielte Suchen durch. "
            "Fasse am Ende deine wichtigsten Findings kompakt zusammen.\n\n"
            "Antworte abschließend NUR mit diesem JSON-Format:\n"
            "{\n"
            '  "summary": "Kurze Zusammenfassung der Findings (3-5 Sätze)",\n'
            '  "key_findings": ["Finding 1", "Finding 2", ...],\n'
            '  "sources": ["Pfad/ID/Key 1", "Pfad/ID/Key 2", ...]\n'
            "}"
        )

        # Tool-Schemas nur für erlaubte Tools
        tool_schemas = [
            schema for schema in tool_registry.get_openai_schemas(include_write_ops=False)
            if schema["function"]["name"] in self.allowed_tools
        ]

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
            try:
                response_text, tool_calls_raw = await self._call_llm(
                    llm_client, messages, tool_schemas
                )
            except Exception as e:
                return SubAgentResult(
                    agent_name=self.display_name,
                    success=False,
                    summary="",
                    key_findings=[],
                    sources=[],
                    error=f"LLM-Fehler: {e}",
                    duration_ms=int(time.time() * 1000) - start_ms,
                )

            # Keine Tool-Calls mehr → Agent ist fertig
            if not tool_calls_raw:
                # Versuche JSON aus der Antwort zu parsen
                final_result = self._parse_final_response(response_text)
                break

            # Tool-Calls ausführen
            messages.append({"role": "assistant", "content": response_text or "", "tool_calls": tool_calls_raw})

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
                        # Token-Schätzung (grob: 1 Token ≈ 4 Zeichen)
                        total_tokens += len(tool_content) // 4
                    except Exception as e:
                        tool_content = f"[Fehler] {tc_name}: {e}"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": tool_content,
                })

        if final_result is None:
            # Kein sauberes JSON-Finish → Freitext-Fallback
            last_assistant = next(
                (m["content"] for m in reversed(messages) if m.get("role") == "assistant"),
                ""
            )
            final_result = SubAgentResult(
                agent_name=self.display_name,
                success=bool(last_assistant),
                summary=last_assistant[:500] if last_assistant else "",
                key_findings=[],
                sources=[],
                token_usage=total_tokens,
                duration_ms=int(time.time() * 1000) - start_ms,
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
        Ruft das LLM mit Tool-Definitionen auf (kein Streaming, kein Konfirmations-Flow).
        Gibt (response_text, tool_calls_raw) zurück.
        """
        from app.services.llm_client import _get_http_client, _RETRY_DELAYS, _is_retryable
        import httpx

        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": settings.llm.temperature,
            "max_tokens": min(settings.llm.max_tokens, 2048),  # Sub-Agenten brauchen weniger
            "stream": False,
            "tools": tool_schemas,
            "tool_choice": "auto",
        }

        headers = {"Content-Type": "application/json"}
        if settings.llm.api_key and settings.llm.api_key != "none":
            headers["Authorization"] = f"Bearer {settings.llm.api_key}"

        last_exc = None
        for attempt, delay in enumerate([0] + _RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            try:
                client = _get_http_client()
                response = await client.post(
                    f"{settings.llm.base_url.rstrip('/')}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                choice = data["choices"][0]["message"]
                text = choice.get("content") or ""
                tool_calls = choice.get("tool_calls") or []
                return text, tool_calls
            except Exception as e:
                last_exc = e
                if _is_retryable(e) and attempt < len(_RETRY_DELAYS):
                    continue
                break

        raise RuntimeError(f"LLM-Aufruf fehlgeschlagen: {last_exc}")

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

        # JSON aus Text extrahieren (auch aus Markdown-Blöcken)
        json_text = text
        if "```" in text:
            import re
            match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
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
# Sub-Agent Dispatcher
# ══════════════════════════════════════════════════════════════════════════════

# Keyword-Fallback wenn LLM-Routing fehlschlägt
_KEYWORD_ROUTING: Dict[str, List[str]] = {
    "code_explorer":  ["klasse", "methode", "java", "python", "code", "implementierung",
                       "funktion", "interface", ".java", ".py", "source", "quellcode",
                       "import", "package", "extends", "implements"],
    "wiki_agent":     ["confluence", "wiki", "dokumentation", "doku", "seite", "page",
                       "beschreibung", "architektur", "konzept"],
    "jira_agent":     ["ticket", "issue", "jira", "bug", "story", "epic", "aufgabe",
                       "task", "fehler", "fehlermeldung"],
    "database_agent": ["tabelle", "sql", "datenbank", "db", "schema", "query", "abfrage",
                       "spalte", "select", "db2", "table", "record", "datensatz"],
    "knowledge_agent":["handbuch", "service", "pdf", "skill", "wissen", "konzept",
                       "prozess", "vorgang", "field", "feld"],
}

_INTENT_PROMPT = """\
Analysiere folgende Nutzer-Anfrage und entscheide, welche Datenquellen sinnvoll zu durchsuchen sind.
Antworte NUR mit JSON – kein erklärender Text.

Verfügbare Agenten:
- code_explorer: Java/Python Quellcode, Klassen, Methoden, SQL-Dateien
- wiki_agent: Confluence-Wiki, technische Dokumentation, Architektur-Seiten
- jira_agent: Jira-Tickets, Bugs, User Stories, Aufgaben
- database_agent: DB2-Tabellen, SQL-Abfragen, Datenbankschema
- knowledge_agent: Internes Handbuch, PDF-Dokumente, Skill-Wissensbasis

Anfrage: "{query}"

Antworte mit: {{"agents": ["agent1", "agent2", ...]}}
Wähle nur Agenten die für diese Anfrage wirklich relevant sind (1-5 Agenten)."""


class SubAgentDispatcher:
    """
    Entscheidet welche Sub-Agenten für eine Anfrage aktiviert werden
    und führt sie parallel aus.

    Routing-Strategie:
    1. LLM-basiertes Intent-Routing (qwen-7b/tool_model) – ~2s, präziser
    2. Fallback: Keyword-Matching wenn LLM-Routing fehlschlägt
    """

    def __init__(self, agents: Dict[str, "SubAgent"]):
        """
        Args:
            agents: Dict von agent_name → SubAgent-Instanz
        """
        self._agents = agents
        self._model: str = settings.llm.tool_model or settings.llm.default_model

    async def classify_intent(self, query: str, llm_client) -> List[str]:
        """
        Nutzt das LLM (tool_model) zur Intent-Klassifikation.
        Gibt eine Liste relevanter Agent-Namen zurück.
        Fällt auf Keyword-Matching zurück bei Fehler.
        """
        enabled = settings.sub_agents.agents

        prompt = _INTENT_PROMPT.replace("{query}", query[:500])
        messages = [{"role": "user", "content": prompt}]

        try:
            from app.services.llm_client import _get_http_client

            headers = {"Content-Type": "application/json"}
            if settings.llm.api_key and settings.llm.api_key != "none":
                headers["Authorization"] = f"Bearer {settings.llm.api_key}"

            payload = {
                "model": self._model,
                "messages": messages,
                "temperature": 0.0,
                "max_tokens": 150,
                "stream": False,
            }

            client = _get_http_client()
            response = await asyncio.wait_for(
                client.post(
                    f"{settings.llm.base_url.rstrip('/')}/chat/completions",
                    headers=headers,
                    json=payload,
                ),
                timeout=10.0,  # Kurzer Timeout für Routing-Call
            )
            response.raise_for_status()
            text = response.json()["choices"][0]["message"]["content"] or ""

            # JSON parsen
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                raw_agents = data.get("agents", [])
                # Nur aktivierte und bekannte Agenten
                result = [a for a in raw_agents if a in enabled and a in self._agents]
                if result:
                    print(f"[sub_agents] Intent-Routing: {result}")
                    return result
        except Exception as e:
            print(f"[sub_agents] Intent-Routing fehlgeschlagen ({e}), nutze Keyword-Fallback")

        return self._keyword_routing(query, enabled)

    def _keyword_routing(self, query: str, enabled: List[str]) -> List[str]:
        """Keyword-basiertes Fallback-Routing."""
        query_lower = query.lower()
        matched = []
        for agent_name, keywords in _KEYWORD_ROUTING.items():
            if agent_name not in enabled or agent_name not in self._agents:
                continue
            if any(kw in query_lower for kw in keywords):
                matched.append(agent_name)

        # Wenn gar nichts matcht: alle aktivierten Agenten
        if not matched:
            matched = [a for a in enabled if a in self._agents]

        print(f"[sub_agents] Keyword-Routing: {matched}")
        return matched

    async def dispatch(
        self,
        query: str,
        llm_client,
        tool_registry,
    ) -> List[SubAgentResult]:
        """
        Bestimmt relevante Sub-Agenten und führt sie parallel aus.

        Args:
            query: Nutzer-Anfrage
            llm_client: LLMClient-Singleton
            tool_registry: ToolRegistry-Singleton

        Returns:
            Liste von SubAgentResult (auch fehlgeschlagene)
        """
        timeout = settings.sub_agents.timeout_seconds

        # Routing: Welche Agenten sind relevant?
        relevant_agents = await self.classify_intent(query, llm_client)

        if not relevant_agents:
            print("[sub_agents] Keine relevanten Agenten ermittelt")
            return []

        print(f"[sub_agents] Starte {len(relevant_agents)} Agenten parallel: {relevant_agents}")

        # Alle relevanten Agenten parallel ausführen
        async def run_with_timeout(agent_name: str) -> SubAgentResult:
            agent = self._agents[agent_name]
            try:
                return await asyncio.wait_for(
                    agent.run(query, llm_client, tool_registry),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                return SubAgentResult(
                    agent_name=agent.display_name,
                    success=False,
                    summary="",
                    key_findings=[],
                    sources=[],
                    error=f"Timeout nach {timeout}s",
                )
            except Exception as e:
                return SubAgentResult(
                    agent_name=agent.display_name,
                    success=False,
                    summary="",
                    key_findings=[],
                    sources=[],
                    error=str(e),
                )

        results = await asyncio.gather(
            *[run_with_timeout(name) for name in relevant_agents]
        )

        successful = sum(1 for r in results if r.success)
        print(f"[sub_agents] Fertig: {successful}/{len(results)} erfolgreich")
        return list(results)


def format_sub_agent_results(results: List[SubAgentResult]) -> str:
    """
    Formatiert Sub-Agent-Ergebnisse als System-Context-Block für den Main-Agent.
    """
    if not results:
        return ""

    successful = [r for r in results if r.success]
    if not successful:
        return ""

    lines = ["=== Vorab-Recherche (Spezial-Agenten) ==="]
    for result in results:
        lines.append(result.to_context_block())
    lines.append("=== Ende Vorab-Recherche ===")
    return "\n".join(lines)
