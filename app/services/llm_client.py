import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from app.core.config import settings
from app.core.exceptions import LLMError

logger = logging.getLogger(__name__)


def _is_mistral_model(model: str) -> bool:
    """Prüft ob das Modell Mistral-basiert ist (strikte Message-Ordering)."""
    if not model:
        return False
    model_lower = model.lower()
    return any(name in model_lower for name in ("mistral", "devstral", "codestral", "pixtral"))


def _sanitize_messages_for_mistral(messages: List[Dict]) -> List[Dict]:
    """
    Sanitiert Messages für Mistral-Kompatibilität.

    Mistral-Modelle erlauben keine System-Messages nach User/Assistant/Tool-Messages.
    Diese Funktion konvertiert späte System-Messages zu User-Messages.

    Args:
        messages: Original-Nachrichten

    Returns:
        Sanitierte Nachrichten (Kopie)
    """
    if not messages:
        return messages

    result = []
    seen_non_system = False

    for msg in messages:
        msg_copy = dict(msg)
        role = msg_copy.get("role", "")

        if role == "system":
            if seen_non_system:
                # System-Message nach User/Assistant/Tool → konvertiere zu User mit Prefix
                content = msg_copy.get("content", "")
                msg_copy = {
                    "role": "user",
                    "content": f"[System Hinweis]\n{content}"
                }
                logger.debug("[llm] Converted late system message to user message for Mistral compatibility")
        else:
            seen_non_system = True

        result.append(msg_copy)

    return result


def _parse_tool_calls_from_content(content: str) -> tuple[str, List[Dict]]:
    """
    Parst [TOOL_CALLS][{...}] Format aus dem Content (für Mistral/lokale Modelle).

    Manche LLMs geben Tool-Calls nicht im strukturierten Format aus, sondern als:
    - [TOOL_CALLS][{"name": "...", "arguments": {...}}]
    - <tool_call>{"name": "...", "arguments": {...}}</tool_call>

    Returns:
        Tuple von (bereinigter Content, Liste von Tool-Calls im OpenAI-Format)
    """
    if not content:
        return content, []

    tool_calls = []
    clean_content = content

    # Pattern 1: [TOOL_CALLS][{...}] oder [TOOL_CALLS][{...}, {...}]
    tool_calls_match = re.search(r'\[TOOL_CALLS\]\s*(\[.*\])', content, re.DOTALL)
    if tool_calls_match:
        try:
            raw_calls = json.loads(tool_calls_match.group(1))
            for i, call in enumerate(raw_calls if isinstance(raw_calls, list) else [raw_calls]):
                tool_calls.append({
                    "id": f"call_{i}",
                    "type": "function",
                    "function": {
                        "name": call.get("name", ""),
                        "arguments": json.dumps(call.get("arguments", call.get("parameters", {})))
                    }
                })
            # Content bereinigen
            clean_content = content[:tool_calls_match.start()].strip()
            logger.debug(f"[llm] Parsed {len(tool_calls)} tool calls from [TOOL_CALLS] format")
        except json.JSONDecodeError as e:
            logger.warning(f"[llm] Could not parse [TOOL_CALLS]: {e}")

    # Pattern 2: <tool_call>{...}</tool_call>
    if not tool_calls:
        tool_call_matches = re.findall(r'<tool_call>\s*(\{.*?\})\s*</tool_call>', content, re.DOTALL)
        for i, match in enumerate(tool_call_matches):
            try:
                call = json.loads(match)
                tool_calls.append({
                    "id": f"call_{i}",
                    "type": "function",
                    "function": {
                        "name": call.get("name", ""),
                        "arguments": json.dumps(call.get("arguments", call.get("parameters", {})))
                    }
                })
            except json.JSONDecodeError:
                pass
        if tool_calls:
            clean_content = re.sub(r'<tool_call>.*?</tool_call>', '', content, flags=re.DOTALL).strip()
            logger.debug(f"[llm] Parsed {len(tool_calls)} tool calls from <tool_call> format")

    return clean_content, tool_calls

# Shared HTTP Client für Connection-Pooling (Performance-Optimierung)
# Vermeidet TCP/TLS-Handshake bei jedem Request (~200ms Ersparnis)
_http_client: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    """Gibt den shared HTTP-Client zurück (Lazy Init mit Connection-Pooling)."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=settings.llm.timeout_seconds,
            verify=settings.llm.verify_ssl,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30.0
            )
        )
    return _http_client


async def close_http_client():
    """Schließt den shared HTTP-Client (für Shutdown)."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None

SYSTEM_PROMPT = """Du bist ein erfahrener Software-Ingenieur mit Expertise in Java und Python. Du beherrschst:
- Java 8-21, Spring Boot, Jakarta EE, Maven
- Python 3.9+, FastAPI, pytest, pydantic, asyncio, SQLAlchemy
- WebSphere Liberty Profile (WLP) Administration und Log-Analyse
- IBM-Fehlercodes (CWWK-Serie)
- Code-Review, Refactoring und Design Patterns

Bei Code-Review: Identifiziere Bugs, Performance-Probleme und Style-Verletzungen.
Bei Code-Generierung: Halte dich an die Muster aus dem bereitgestellten Context.
Bei Log-Analyse: Nenne Root Causes und konkrete Fix-Vorschläge.
Antworte immer mit konkreten Code-Beispielen.
Formatiere Java-Code in ```java Blöcken, Python-Code in ```python Blöcken.
Kontext wird in klar markierten Abschnitten bereitgestellt (z.B. [DATEI: Pfad], [PYTHON-DATEI: Pfad], [LOG], [PDF], [CONFLUENCE]).

Wenn du mehrere Python-Dateien erstellst, nutze immer dieses Format:
=== FILE: relativer/pfad/datei.py ===
[Dateiinhalt]
=== END FILE ===

## WICHTIG: Aufgaben-Abschluss

Nach Abschluss einer Aufgabe (z.B. Datei bearbeitet):
1. Führe KEINE weiteren Tool-Calls aus, es sei denn der User fragt explizit danach
2. Fasse kurz zusammen was du gemacht hast
3. Warte auf weitere Anweisungen

Nach einer Datei-Bearbeitung (edit_file, write_file):
- Bearbeite NICHT automatisch weitere Dateien
- Erkläre was geändert wurde
- Frage ob weitere Änderungen gewünscht sind

Wenn du [STOP] oder [HINWEIS] Nachrichten erhältst, befolge diese und höre auf, weitere Tools aufzurufen.

## GitHub Pull Request Analyse

Bei PR-Analysen AUSSCHLIESSLICH GitHub-Tools verwenden:
- github_pr_details: PR-Metadaten (Titel, Autor, Status)
- github_pr_diff: Code-Änderungen im PR (Diff)
- github_get_file: Vollständige Datei aus GitHub-Repo

NIEMALS lokale Tools für GitHub-PRs verwenden:
- NICHT search_code (durchsucht lokale Dateien, nicht GitHub)
- NICHT read_file (liest lokale Dateien, nicht GitHub)
- NICHT search_java_class (für lokale Java-Projekte)
- NICHT trace_java_references (für lokale Java-Projekte)

WICHTIG: Nach Aufruf von github_pr_details oder github_pr_diff:
- Die PR-Analyse erscheint automatisch im Workspace-Panel (rechts)
- Gib im Chat NUR eine kurze Bestätigung: "PR #X wird im Workspace analysiert"
- KEINE detaillierte Diff-Analyse im Chat - das macht der Workspace automatisch
- Bei Fragen zu PR-Metadaten (Autor, Anzahl PRs, etc.) kannst du diese im Chat beantworten
"""

_RETRY_DELAYS = [2, 4, 8]  # Exponential Backoff in Sekunden

# Differenzierte Timeouts für verschiedene Call-Typen
TIMEOUT_QUICK = 15.0      # Complexity-Check, einfache Klassifikation
TIMEOUT_TOOL = 60.0       # Tool-Calls (Standard)
TIMEOUT_ANALYSIS = 120.0  # Lange Analysen, Streaming


@dataclass
class LLMResponse:
    """Strukturierte LLM-Antwort für Tool-basierte Calls."""
    content: Optional[str] = None
    tool_calls: List[Dict[str, Any]] = None
    finish_reason: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def __post_init__(self):
        if self.tool_calls is None:
            self.tool_calls = []

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def _is_retryable(exc: Exception) -> bool:
    """Prüft ob eine Exception einen Retry rechtfertigt."""
    if isinstance(exc, httpx.RequestError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


class LLMClient:
    def __init__(self):
        self.base_url = settings.llm.base_url.rstrip("/")
        self.api_key = settings.llm.api_key
        self.timeout = settings.llm.timeout_seconds
        self.default_model = settings.llm.default_model
        self.max_tokens = settings.llm.max_tokens
        self.temperature = settings.llm.temperature
        self.verify_ssl = settings.llm.verify_ssl

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key and self.api_key != "none":
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def chat(
        self,
        messages: List[dict],
        model: str = None,
    ) -> str:
        model = model or self.default_model
        # Mistral-Kompatibilität
        if _is_mistral_model(model):
            logger.info(f"[llm.chat] Mistral detected: {model}, sanitizing messages")
            messages = _sanitize_messages_for_mistral(messages)
        payload = {
            "model": model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        last_exc = None
        for attempt, delay in enumerate([0] + _RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            try:
                client = _get_http_client()
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
            except Exception as e:
                last_exc = e
                if _is_retryable(e) and attempt < len(_RETRY_DELAYS):
                    print(f"[llm] Retry {attempt + 1} nach Fehler: {e}")
                    continue
                break

        if isinstance(last_exc, httpx.HTTPStatusError):
            raise LLMError(f"LLM API Fehler {last_exc.response.status_code}: {last_exc.response.text}") from last_exc
        if isinstance(last_exc, httpx.RequestError):
            raise LLMError(f"LLM Verbindungsfehler: {last_exc}") from last_exc
        if isinstance(last_exc, (KeyError, IndexError)):
            raise LLMError(f"Unerwartetes LLM-Antwortformat: {last_exc}") from last_exc
        raise LLMError(f"LLM Fehler: {last_exc}") from last_exc

    async def chat_stream(
        self,
        messages: List[dict],
        model: str = None,
    ) -> AsyncGenerator[str, None]:
        model = model or self.default_model
        # Mistral-Kompatibilität
        if _is_mistral_model(model):
            messages = _sanitize_messages_for_mistral(messages)
        payload = {
            "model": model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        last_exc = None
        for attempt, delay in enumerate([0] + _RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            try:
                client = _get_http_client()
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            return
                        try:
                            chunk = json.loads(raw)
                            delta = chunk["choices"][0].get("delta", {})
                            token = delta.get("content", "")
                            if token:
                                yield token
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
                    return  # Stream erfolgreich abgeschlossen
            except httpx.HTTPStatusError as e:
                last_exc = e
                status = e.response.status_code
                if status >= 500 and attempt < len(_RETRY_DELAYS):
                    print(f"[llm] Stream Retry {attempt + 1} nach HTTP {status}")
                    continue
                # Benutzerfreundliche Fehlermeldung für Gateway-Timeouts
                if status == 504:
                    raise LLMError(f"LLM Gateway Timeout (504): Der LLM-Server hat zu lange gebraucht.") from e
                # HTML/Bild-Response erkennen
                try:
                    raw = e.response.text[:500]
                    if raw.startswith("<!") or "base64" in raw.lower():
                        raise LLMError(f"LLM API Fehler {status}: Gateway-Fehlerseite statt JSON") from e
                except Exception:
                    pass
                raise LLMError(f"LLM API Fehler {status}: {e.response.text[:200]}") from e
            except httpx.RequestError as e:
                last_exc = e
                if attempt < len(_RETRY_DELAYS):
                    print(f"[llm] Stream Retry {attempt + 1} nach Verbindungsfehler: {e}")
                    continue
                raise LLMError(f"LLM Verbindungsfehler: {e}") from e

        if last_exc:
            raise LLMError(f"LLM Streaming fehlgeschlagen nach {len(_RETRY_DELAYS)} Versuchen: {last_exc}") from last_exc

    async def list_models(self) -> List[str]:
        """Listet verfügbare Modelle vom LLM-Server auf."""
        client = _get_http_client()
        try:
            response = await client.get(
                f"{self.base_url}/models",
                headers=self._headers(),
                timeout=10.0  # Kürzerer Timeout für Model-Liste
            )
            response.raise_for_status()
            data = response.json()
            # OpenAI-Format: {"data": [{"id": "model-name"}, ...]}
            if "data" in data:
                return [m.get("id", "") for m in data["data"] if m.get("id")]
            return []
        except Exception:
            return []

    def _inject_reasoning(
        self,
        messages: List[Dict],
        reasoning: Optional[str],
    ) -> List[Dict]:
        """
        Injiziert reasoning-Direktive in die System-Message.

        GPT-OSS und ähnliche Modelle unterstützen 'reasoning: high/medium/low'
        als Präfix in der System-Message für erweitertes Reasoning.

        Args:
            messages: Original-Nachrichten
            reasoning: "low", "medium", "high" oder None/""

        Returns:
            Messages mit injizierter reasoning-Direktive (Kopie)
        """
        if not reasoning or reasoning not in ("low", "medium", "high"):
            return messages

        # Kopie erstellen um Original nicht zu verändern
        messages = [dict(m) for m in messages]

        # System-Message finden oder erstellen
        system_idx = next(
            (i for i, m in enumerate(messages) if m.get("role") == "system"),
            None
        )

        reasoning_prefix = f"reasoning: {reasoning}\n\n"

        if system_idx is not None:
            # Reasoning-Präfix zur bestehenden System-Message hinzufügen
            current_content = messages[system_idx].get("content", "")
            # Nicht doppelt hinzufügen
            if not current_content.startswith("reasoning:"):
                messages[system_idx]["content"] = reasoning_prefix + current_content
        else:
            # Neue System-Message am Anfang einfügen
            messages.insert(0, {
                "role": "system",
                "content": reasoning_prefix.strip()
            })

        return messages

    async def chat_with_tools(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        tool_choice: str = "auto",
        reasoning: Optional[str] = None,
        use_tool_prefill: bool = False,
    ) -> LLMResponse:
        """
        Zentraler LLM-Call mit Tool-Support.

        Konsolidiert alle Tool-basierten Aufrufe aus orchestrator.py und sub_agent.py.
        Nutzt Connection-Pooling und Retry-Logik.

        Args:
            messages: Chat-Nachrichten
            tools: Optional Tool-Definitionen (OpenAI-Format)
            model: Modell (default: default_model)
            temperature: Temperature (default: settings.llm.temperature)
            max_tokens: Max Tokens (default: settings.llm.max_tokens)
            timeout: Request-Timeout in Sekunden (default: TIMEOUT_TOOL)
            tool_choice: "auto", "none", oder {"type": "function", "function": {"name": "..."}}
            reasoning: Reasoning-Effort für GPT-OSS: "low", "medium", "high" (None = aus)
            use_tool_prefill: Wenn True, wird ein Assistant-Prefill mit [TOOL_CALLS] hinzugefügt
                              um das Modell in das richtige Output-Format zu zwingen

        Returns:
            LLMResponse mit content, tool_calls, finish_reason, usage
        """
        model = model or self.default_model
        temperature = temperature if temperature is not None else self.temperature
        max_tokens = max_tokens or self.max_tokens
        timeout = timeout or TIMEOUT_TOOL

        # DEBUG: Model-Check für Mistral-Erkennung (immer loggen)
        is_mistral = _is_mistral_model(model)
        print(f"[LLM DEBUG] chat_with_tools called - model='{model}', is_mistral={is_mistral}")
        logger.warning(f"[llm] Model: '{model}', is_mistral={is_mistral}")

        # Reasoning in System-Message injizieren falls aktiviert
        if reasoning:
            messages = self._inject_reasoning(messages, reasoning)
            logger.debug(f"[llm] Reasoning aktiviert: {reasoning}")

        # Tool-Prefill: Assistant-Message mit [TOOL_CALLS] Prefix hinzufügen
        # Zwingt das Modell, im richtigen Format zu antworten
        if use_tool_prefill and tools:
            messages = [dict(m) for m in messages]  # Kopie
            # LiteLLM-Style Prefill mit prefix: true
            messages.append({
                "role": "assistant",
                "content": "[TOOL_CALLS]",
                "prefix": True  # LiteLLM-spezifisch
            })
            logger.debug("[llm] Tool-Prefill aktiviert")

        # Mistral-Kompatibilität: System-Messages nach Tool-Responses konvertieren
        if is_mistral:
            original_roles = [m.get("role") for m in messages]
            messages = _sanitize_messages_for_mistral(messages)
            new_roles = [m.get("role") for m in messages]
            changed = original_roles != new_roles
            print(f"[LLM DEBUG] Mistral sanitization: changed={changed}")
            if changed:
                logger.warning(f"[llm] Mistral sanitization applied: {original_roles} -> {new_roles}")

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        last_exc = None
        for attempt, delay in enumerate([0] + _RETRY_DELAYS):
            if delay:
                logger.debug(f"[llm] Retry {attempt} nach {delay}s")
                await asyncio.sleep(delay)
            try:
                client = _get_http_client()
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                    timeout=timeout,
                )
                response.raise_for_status()
                data = response.json()

                # Response parsen
                if "choices" not in data or not data["choices"]:
                    logger.warning(f"[llm] Keine 'choices' in Response: {list(data.keys())}")
                    return LLMResponse(finish_reason="error", model=model)

                choice = data["choices"][0]
                message = choice.get("message", {})
                usage = data.get("usage", {})

                content = message.get("content")
                tool_calls = message.get("tool_calls") or []

                # Fallback: Parse [TOOL_CALLS] aus Content wenn keine strukturierten tool_calls
                if not tool_calls and content and ("[TOOL_CALLS]" in content or "<tool_call>" in content):
                    content, tool_calls = _parse_tool_calls_from_content(content)

                return LLMResponse(
                    content=content,
                    tool_calls=tool_calls,
                    finish_reason=choice.get("finish_reason", ""),
                    model=model,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                )

            except httpx.HTTPStatusError as e:
                last_exc = e
                status = e.response.status_code
                body = ""
                try:
                    raw_body = e.response.text[:2000]
                    # Erkennen von HTML/Bild-Responses (Gateway-Fehlerseiten)
                    if raw_body.startswith("<!") or raw_body.startswith("<html") or "base64" in raw_body.lower():
                        body = f"[Gateway-Fehlerseite - keine JSON-Response]"
                        logger.warning(f"[llm] HTTP {status}: Gateway gab HTML/Bild zurück statt JSON")
                    elif raw_body.startswith("data:image") or len(raw_body) > 500 and not raw_body.strip().startswith("{"):
                        body = f"[Ungültige Response - kein JSON]"
                        logger.warning(f"[llm] HTTP {status}: Response ist kein JSON (erste 100 Zeichen: {raw_body[:100]})")
                    else:
                        body = raw_body[:500]
                except Exception:
                    pass
                logger.warning(f"[llm] HTTP {status} (Versuch {attempt + 1}): {body}")
                if status >= 500 and attempt < len(_RETRY_DELAYS):
                    continue
                # Benutzerfreundliche Fehlermeldung für Gateway-Timeouts
                if status == 504:
                    raise LLMError(f"LLM Gateway Timeout (504): Der LLM-Server hat zu lange gebraucht. Versuche eine kürzere Anfrage oder wähle ein schnelleres Modell.") from e
                raise LLMError(f"LLM API Fehler {status}: {body}") from e

            except httpx.TimeoutException as e:
                last_exc = e
                logger.warning(f"[llm] Timeout nach {timeout}s (Versuch {attempt + 1})")
                if attempt < len(_RETRY_DELAYS):
                    continue
                raise LLMError(f"LLM Timeout nach {attempt + 1} Versuchen") from e

            except httpx.RequestError as e:
                last_exc = e
                logger.warning(f"[llm] Verbindungsfehler (Versuch {attempt + 1}): {e}")
                if attempt < len(_RETRY_DELAYS):
                    continue
                raise LLMError(f"LLM Verbindungsfehler: {e}") from e

            except Exception as e:
                last_exc = e
                logger.error(f"[llm] Unerwarteter Fehler: {type(e).__name__}: {e}")
                if _is_retryable(e) and attempt < len(_RETRY_DELAYS):
                    continue
                break

        raise LLMError(f"LLM-Aufruf fehlgeschlagen: {last_exc}") from last_exc

    async def chat_quick(
        self,
        messages: List[Dict],
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 256,
    ) -> str:
        """
        Schneller LLM-Call für einfache Aufgaben (Klassifikation, Komplexität).

        Nutzt kurzen Timeout und wenige Tokens.
        """
        response = await self.chat_with_tools(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=TIMEOUT_QUICK,
        )
        return response.content or ""


llm_client = LLMClient()


def get_llm_client() -> LLMClient:
    """Gibt die LLM-Client Instanz zurück."""
    return llm_client


# Exports für andere Module
__all__ = [
    "LLMClient",
    "LLMResponse",
    "llm_client",
    "get_llm_client",
    "close_http_client",
    "_get_http_client",
    "_is_retryable",
    "_RETRY_DELAYS",
    "TIMEOUT_QUICK",
    "TIMEOUT_TOOL",
    "TIMEOUT_ANALYSIS",
    "SYSTEM_PROMPT",
]
