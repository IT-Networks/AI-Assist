import json
from typing import AsyncGenerator, List

import httpx

from app.core.config import settings
from app.core.exceptions import LLMError

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
"""


class LLMClient:
    def __init__(self):
        self.base_url = settings.llm.base_url.rstrip("/")
        self.api_key = settings.llm.api_key
        self.timeout = settings.llm.timeout_seconds
        self.default_model = settings.llm.default_model
        self.max_tokens = settings.llm.max_tokens
        self.temperature = settings.llm.temperature

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
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
            except httpx.HTTPStatusError as e:
                raise LLMError(f"LLM API Fehler {e.response.status_code}: {e.response.text}") from e
            except httpx.RequestError as e:
                raise LLMError(f"LLM Verbindungsfehler: {e}") from e
            except (KeyError, IndexError) as e:
                raise LLMError(f"Unerwartetes LLM-Antwortformat: {e}") from e

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
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
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
                            break
                        try:
                            chunk = json.loads(raw)
                            delta = chunk["choices"][0].get("delta", {})
                            token = delta.get("content", "")
                            if token:
                                yield token
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
            except httpx.HTTPStatusError as e:
                raise LLMError(f"LLM API Fehler {e.response.status_code}: {e.response.text}") from e
            except httpx.RequestError as e:
                raise LLMError(f"LLM Verbindungsfehler: {e}") from e


llm_client = LLMClient()
