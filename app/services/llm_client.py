import asyncio
import json
from typing import AsyncGenerator, List, Optional

import httpx

from app.core.config import settings
from app.core.exceptions import LLMError

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
- Java 8–21, Spring Boot, Jakarta EE, Maven
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
"""

_RETRY_DELAYS = [2, 4, 8]  # Exponential Backoff in Sekunden


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
                if e.response.status_code >= 500 and attempt < len(_RETRY_DELAYS):
                    print(f"[llm] Stream Retry {attempt + 1} nach HTTP {e.response.status_code}")
                    continue
                raise LLMError(f"LLM API Fehler {e.response.status_code}: {e.response.text}") from e
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


llm_client = LLMClient()


def get_llm_client() -> LLMClient:
    """Gibt die LLM-Client Instanz zurück."""
    return llm_client
