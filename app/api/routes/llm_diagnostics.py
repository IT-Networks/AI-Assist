"""
LLM Diagnostics API - Tests für Reasoning, Tool-Calling und Modell-Kapazitäten.

Endpoints:
- POST /api/llm/diagnose/reasoning - Testet ob ein Modell Reasoning unterstützt
- POST /api/llm/diagnose/tools - Testet Tool-Calling mit optionalem Prefill
- POST /api/llm/diagnose/all - Führt alle Tests durch
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.llm_client import llm_client, LLMResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/llm/diagnose", tags=["llm-diagnostics"])


# ══════════════════════════════════════════════════════════════════════════════
# Request/Response Schemas
# ══════════════════════════════════════════════════════════════════════════════

class DiagnoseRequest(BaseModel):
    """Request für Diagnose-Tests."""
    model: Optional[str] = Field(
        default=None,
        description="Modell-ID (None = default_model)"
    )
    timeout: float = Field(
        default=30.0,
        description="Timeout in Sekunden"
    )


class ReasoningTestRequest(DiagnoseRequest):
    """Request für Reasoning-Test."""
    reasoning_level: str = Field(
        default="high",
        description="Reasoning-Level: low, medium, high"
    )


class ToolTestRequest(DiagnoseRequest):
    """Request für Tool-Call-Test."""
    use_prefill: bool = Field(
        default=False,
        description="Ob Assistant-Prefill verwendet werden soll"
    )
    test_tool: str = Field(
        default="get_current_time",
        description="Welches Test-Tool verwendet werden soll"
    )


class DiagnoseResult(BaseModel):
    """Ergebnis eines Diagnose-Tests."""
    test_name: str
    success: bool
    model: str
    duration_ms: int
    details: Dict[str, Any]
    raw_response: Optional[str] = None
    error: Optional[str] = None


class AllDiagnoseResult(BaseModel):
    """Ergebnis aller Diagnose-Tests."""
    model: str
    tests: List[DiagnoseResult]
    summary: Dict[str, Any]


# ══════════════════════════════════════════════════════════════════════════════
# Test-Tools für Diagnose
# ══════════════════════════════════════════════════════════════════════════════

DIAGNOSTIC_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Gibt die aktuelle Uhrzeit zurück. Verwende dieses Tool um die Zeit abzufragen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "Zeitzone (z.B. 'Europe/Berlin', 'UTC')",
                        "default": "UTC"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "Führt eine mathematische Berechnung durch.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Mathematischer Ausdruck (z.B. '2 + 2', '10 * 5')"
                    }
                },
                "required": ["expression"]
            }
        }
    }
]


# ══════════════════════════════════════════════════════════════════════════════
# Diagnose-Funktionen
# ══════════════════════════════════════════════════════════════════════════════

async def _test_reasoning(
    model: str,
    reasoning_level: str,
    timeout: float
) -> DiagnoseResult:
    """
    Testet ob ein Modell das Reasoning-Feature versteht.

    Das Modell erhält eine Aufgabe die explizit Reasoning erfordert
    und soll bestätigen ob es erweitertes Reasoning nutzt.
    """
    start = time.time()
    test_name = f"reasoning_{reasoning_level}"

    messages = [
        {
            "role": "system",
            "content": "Du bist ein Test-Assistent. Beantworte die Frage präzise."
        },
        {
            "role": "user",
            "content": (
                "Dies ist ein Test für erweiterte Reasoning-Fähigkeiten.\n\n"
                "Aufgabe: Löse dieses logische Problem Schritt für Schritt:\n"
                "Anna ist älter als Bob. Bob ist älter als Clara. Clara ist 20.\n"
                "Wer ist am ältesten?\n\n"
                "Beginne deine Antwort mit 'REASONING_AKTIV:' wenn du "
                "erweitertes Reasoning/Chain-of-Thought verwendest, "
                "ansonsten mit 'STANDARD:'.\n"
                "Zeige dann deinen Denkprozess."
            )
        }
    ]

    try:
        response = await llm_client.chat_with_tools(
            messages=messages,
            model=model,
            reasoning=reasoning_level,
            temperature=0.1,
            max_tokens=500,
            timeout=timeout,
        )

        duration_ms = int((time.time() - start) * 1000)
        content = response.content or ""

        # Analyse der Antwort
        has_reasoning_marker = "REASONING_AKTIV" in content.upper()
        has_step_by_step = any(marker in content.lower() for marker in [
            "schritt", "step", "zuerst", "dann", "also", "daraus folgt",
            "erstens", "zweitens", "1.", "2.", "zunächst"
        ])
        correct_answer = "anna" in content.lower()

        reasoning_detected = has_reasoning_marker or has_step_by_step

        return DiagnoseResult(
            test_name=test_name,
            success=reasoning_detected and correct_answer,
            model=model,
            duration_ms=duration_ms,
            details={
                "reasoning_level": reasoning_level,
                "reasoning_marker_found": has_reasoning_marker,
                "step_by_step_detected": has_step_by_step,
                "correct_answer": correct_answer,
                "tokens_used": response.total_tokens,
            },
            raw_response=content[:1000],  # Erste 1000 Zeichen
        )

    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        logger.error(f"[diagnose] Reasoning-Test fehlgeschlagen: {e}")
        return DiagnoseResult(
            test_name=test_name,
            success=False,
            model=model,
            duration_ms=duration_ms,
            details={"reasoning_level": reasoning_level},
            error=str(e),
        )


async def _test_tool_calling(
    model: str,
    use_prefill: bool,
    test_tool: str,
    timeout: float
) -> DiagnoseResult:
    """
    Testet ob ein Modell Tool-Calls korrekt generiert.

    Vergleicht Ergebnisse mit und ohne Prefill.
    """
    start = time.time()
    test_name = f"tool_call_{'with_prefill' if use_prefill else 'no_prefill'}"

    # Prompt der eindeutig einen Tool-Call erfordert
    if test_tool == "get_current_time":
        user_message = "Wie spät ist es gerade? Nutze das verfügbare Tool um die Zeit abzufragen."
    else:
        user_message = "Berechne 42 * 17. Nutze das calculate Tool."

    messages = [
        {
            "role": "system",
            "content": (
                "Du bist ein Assistent mit Zugriff auf Tools. "
                "Wenn der User eine Aufgabe stellt die ein Tool erfordert, "
                "MUSST du das entsprechende Tool aufrufen. "
                "Antworte NICHT selbst wenn ein Tool verfügbar ist."
            )
        },
        {"role": "user", "content": user_message}
    ]

    try:
        response = await llm_client.chat_with_tools(
            messages=messages,
            tools=DIAGNOSTIC_TOOLS,
            model=model,
            use_tool_prefill=use_prefill,
            temperature=0.0,  # Deterministisch
            max_tokens=256,
            timeout=timeout,
            tool_choice="auto",
        )

        duration_ms = int((time.time() - start) * 1000)
        content = response.content or ""

        # Analyse
        has_native_tool_calls = bool(response.tool_calls)
        has_text_tool_markers = any(marker in content for marker in [
            "[TOOL_CALLS]", "<tool_call>", "<functioncall>"
        ])

        tool_call_detected = has_native_tool_calls or has_text_tool_markers

        # Tool-Call Details extrahieren
        tool_details = []
        if response.tool_calls:
            for tc in response.tool_calls:
                func = tc.get("function", {})
                tool_details.append({
                    "name": func.get("name"),
                    "arguments": func.get("arguments"),
                })

        return DiagnoseResult(
            test_name=test_name,
            success=tool_call_detected,
            model=model,
            duration_ms=duration_ms,
            details={
                "use_prefill": use_prefill,
                "test_tool": test_tool,
                "native_tool_calls": has_native_tool_calls,
                "text_tool_markers": has_text_tool_markers,
                "tool_calls_count": len(response.tool_calls),
                "tool_calls": tool_details,
                "finish_reason": response.finish_reason,
                "tokens_used": response.total_tokens,
            },
            raw_response=content[:500] if content else None,
        )

    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        logger.error(f"[diagnose] Tool-Test fehlgeschlagen: {e}")
        return DiagnoseResult(
            test_name=test_name,
            success=False,
            model=model,
            duration_ms=duration_ms,
            details={"use_prefill": use_prefill, "test_tool": test_tool},
            error=str(e),
        )


async def _test_json_mode(model: str, timeout: float) -> DiagnoseResult:
    """Testet ob das Modell strukturierten JSON-Output liefern kann."""
    start = time.time()
    test_name = "json_mode"

    messages = [
        {
            "role": "system",
            "content": (
                "Du antwortest NUR mit validem JSON. Kein zusätzlicher Text.\n"
                "Format: {\"name\": \"...\", \"age\": ...}"
            )
        },
        {
            "role": "user",
            "content": "Gib mir Beispieldaten für eine Person namens Max, 30 Jahre alt."
        }
    ]

    try:
        response = await llm_client.chat_with_tools(
            messages=messages,
            model=model,
            temperature=0.0,
            max_tokens=100,
            timeout=timeout,
        )

        duration_ms = int((time.time() - start) * 1000)
        content = response.content or ""

        # JSON parsen versuchen
        is_valid_json = False
        parsed_json = None
        try:
            # JSON aus Content extrahieren (mit oder ohne Markdown)
            json_content = content.strip()
            if json_content.startswith("```"):
                # Markdown JSON Block
                lines = json_content.split("\n")
                json_lines = [l for l in lines if not l.startswith("```")]
                json_content = "\n".join(json_lines)

            parsed_json = json.loads(json_content)
            is_valid_json = True
        except json.JSONDecodeError:
            pass

        return DiagnoseResult(
            test_name=test_name,
            success=is_valid_json,
            model=model,
            duration_ms=duration_ms,
            details={
                "valid_json": is_valid_json,
                "parsed_data": parsed_json,
                "tokens_used": response.total_tokens,
            },
            raw_response=content[:300],
        )

    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        return DiagnoseResult(
            test_name=test_name,
            success=False,
            model=model,
            duration_ms=duration_ms,
            details={},
            error=str(e),
        )


# ══════════════════════════════════════════════════════════════════════════════
# API Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/reasoning", response_model=DiagnoseResult)
async def test_reasoning(
    request: ReasoningTestRequest = Body(default=ReasoningTestRequest())
):
    """
    Testet ob ein Modell Reasoning-Direktiven versteht.

    Sendet eine logische Aufgabe mit aktiviertem Reasoning und prüft
    ob das Modell Chain-of-Thought Reasoning zeigt.

    Kann ohne Body aufgerufen werden - nutzt dann Defaults.
    """
    model = request.model or settings.llm.default_model
    result = await _test_reasoning(
        model=model,
        reasoning_level=request.reasoning_level,
        timeout=request.timeout,
    )
    return result


@router.post("/tools", response_model=DiagnoseResult)
async def test_tools(
    request: ToolTestRequest = Body(default=ToolTestRequest())
):
    """
    Testet Tool-Calling mit optionalem Prefill.

    Mit use_prefill=True wird ein Assistant-Prefill "[TOOL_CALLS]" gesendet,
    um das Modell in das richtige Output-Format zu zwingen.

    Vergleiche Ergebnisse mit/ohne Prefill um zu sehen ob es hilft.
    """
    model = request.model or settings.llm.default_model
    result = await _test_tool_calling(
        model=model,
        use_prefill=request.use_prefill,
        test_tool=request.test_tool,
        timeout=request.timeout,
    )
    return result


@router.post("/tools/compare")
async def compare_tool_methods(
    request: DiagnoseRequest = Body(default=DiagnoseRequest())
):
    """
    Vergleicht Tool-Calling mit und ohne Prefill.

    Führt beide Varianten aus und zeigt welche zuverlässiger funktioniert.
    """
    model = request.model or settings.llm.default_model

    # Parallel ausführen
    results = await asyncio.gather(
        _test_tool_calling(model, use_prefill=False, test_tool="get_current_time", timeout=request.timeout),
        _test_tool_calling(model, use_prefill=True, test_tool="get_current_time", timeout=request.timeout),
    )

    no_prefill, with_prefill = results

    return {
        "model": model,
        "comparison": {
            "without_prefill": {
                "success": no_prefill.success,
                "native_tool_calls": no_prefill.details.get("native_tool_calls"),
                "text_markers": no_prefill.details.get("text_tool_markers"),
                "duration_ms": no_prefill.duration_ms,
            },
            "with_prefill": {
                "success": with_prefill.success,
                "native_tool_calls": with_prefill.details.get("native_tool_calls"),
                "text_markers": with_prefill.details.get("text_tool_markers"),
                "duration_ms": with_prefill.duration_ms,
            },
        },
        "recommendation": (
            "prefill" if with_prefill.success and not no_prefill.success
            else "no_prefill" if no_prefill.success
            else "both_failed"
        ),
        "full_results": [no_prefill, with_prefill],
    }


@router.post("/json", response_model=DiagnoseResult)
async def test_json_mode(
    request: DiagnoseRequest = Body(default=DiagnoseRequest())
):
    """Testet ob das Modell strukturierten JSON-Output liefern kann."""
    model = request.model or settings.llm.default_model
    return await _test_json_mode(model, request.timeout)


@router.post("/all", response_model=AllDiagnoseResult)
async def run_all_diagnostics(
    request: DiagnoseRequest = Body(default=DiagnoseRequest())
):
    """
    Führt alle Diagnose-Tests durch.

    Tests:
    - Reasoning (low, medium, high)
    - Tool-Calling (mit/ohne Prefill)
    - JSON-Mode
    """
    model = request.model or settings.llm.default_model
    timeout = request.timeout

    # Alle Tests parallel ausführen
    results = await asyncio.gather(
        _test_reasoning(model, "low", timeout),
        _test_reasoning(model, "medium", timeout),
        _test_reasoning(model, "high", timeout),
        _test_tool_calling(model, use_prefill=False, test_tool="get_current_time", timeout=timeout),
        _test_tool_calling(model, use_prefill=True, test_tool="get_current_time", timeout=timeout),
        _test_json_mode(model, timeout),
    )

    # Summary erstellen
    reasoning_tests = [r for r in results if r.test_name.startswith("reasoning")]
    tool_tests = [r for r in results if r.test_name.startswith("tool_call")]
    json_test = next((r for r in results if r.test_name == "json_mode"), None)

    summary = {
        "total_tests": len(results),
        "passed": sum(1 for r in results if r.success),
        "failed": sum(1 for r in results if not r.success),
        "reasoning_support": {
            "low": next((r.success for r in reasoning_tests if "low" in r.test_name), False),
            "medium": next((r.success for r in reasoning_tests if "medium" in r.test_name), False),
            "high": next((r.success for r in reasoning_tests if "high" in r.test_name), False),
        },
        "tool_calling": {
            "works_without_prefill": next(
                (r.success for r in tool_tests if "no_prefill" in r.test_name), False
            ),
            "works_with_prefill": next(
                (r.success for r in tool_tests if "with_prefill" in r.test_name), False
            ),
            "recommendation": (
                "use_prefill" if any(
                    r.success and "with_prefill" in r.test_name
                    for r in tool_tests
                ) and not any(
                    r.success and "no_prefill" in r.test_name
                    for r in tool_tests
                )
                else "no_prefill_needed" if any(
                    r.success and "no_prefill" in r.test_name
                    for r in tool_tests
                )
                else "investigate"
            ),
        },
        "json_mode": json_test.success if json_test else False,
        "total_duration_ms": sum(r.duration_ms for r in results),
    }

    return AllDiagnoseResult(
        model=model,
        tests=list(results),
        summary=summary,
    )


@router.get("/models")
async def list_available_models():
    """Listet alle verfügbaren Modelle mit ihren Konfigurationen."""
    try:
        models = await llm_client.list_models()
        return {
            "available_models": models,
            "default_model": settings.llm.default_model,
            "tool_model": settings.llm.tool_model or settings.llm.default_model,
            "analysis_model": settings.llm.analysis_model or settings.llm.default_model,
            "configured_context_limits": settings.llm.llm_context_limits,
            "reasoning_settings": {
                "default": settings.llm.reasoning_effort,
                "analysis": settings.llm.analysis_reasoning,
                "tool": settings.llm.tool_reasoning,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/example-request")
async def get_example_request():
    """
    Gibt einen Beispiel-Request zurück, den du in Postman/Bruno verwenden kannst.

    Kopiere den 'curl_command' oder nutze den 'request_body' direkt.
    """
    base_url = settings.llm.base_url.rstrip("/")
    model = settings.llm.default_model

    # Einfacher Request ohne Tools
    simple_body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Du bist ein hilfreicher Assistent."},
            {"role": "user", "content": "Wie geht es dir?"}
        ],
        "temperature": 0.2,
        "max_tokens": 256,
        "stream": False
    }

    # Request mit Tools (wie Agent ihn macht)
    tools_body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Du bist ein hilfreicher Assistent mit Tool-Zugriff."},
            {"role": "user", "content": "Wie spät ist es?"}
        ],
        "temperature": 0.0,
        "max_tokens": 256,
        "stream": False,
        "tools": DIAGNOSTIC_TOOLS,
        "tool_choice": "auto"
    }

    import json as json_module

    return {
        "base_url": base_url,
        "endpoint": f"{base_url}/chat/completions",
        "simple_request": {
            "description": "Einfacher Chat ohne Tools",
            "body": simple_body,
            "curl_command": f"""curl -X POST "{base_url}/chat/completions" \\
  -H "Content-Type: application/json" \\
  -d '{json_module.dumps(simple_body, ensure_ascii=False)}'"""
        },
        "tools_request": {
            "description": "Chat mit Tools (wie Agent)",
            "body": tools_body,
            "curl_command": f"""curl -X POST "{base_url}/chat/completions" \\
  -H "Content-Type: application/json" \\
  -d '{json_module.dumps(tools_body, ensure_ascii=False)}'"""
        },
        "hint": "Wenn der einfache Request funktioniert aber der mit Tools nicht, liegt das Problem am Tool-Format."
    }


@router.post("/debug-agent-request")
async def debug_agent_request(
    message: str = Body(default="Wie geht es dir?", embed=True),
    model: Optional[str] = Body(default=None, embed=True),
):
    """
    Zeigt den exakten Request, den der Agent an das LLM senden würde.

    Nützlich zum Debuggen warum Requests fehlschlagen.
    """
    from app.agent.orchestrator import get_agent_orchestrator, SYSTEM_PROMPT
    from app.utils.token_counter import estimate_tokens, estimate_messages_tokens

    model = model or settings.llm.default_model

    # Baue Messages wie der Agent es tut
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": message}
    ]

    # Token-Schätzung
    system_tokens = estimate_tokens(SYSTEM_PROMPT)
    user_tokens = estimate_tokens(message)
    total_tokens = estimate_messages_tokens(messages)

    # Request-Body wie er gesendet würde
    request_body = {
        "model": model,
        "messages": messages,
        "temperature": settings.llm.tool_temperature,
        "max_tokens": settings.llm.max_tokens,
        "stream": False,
    }

    return {
        "model": model,
        "token_estimate": {
            "system_prompt": system_tokens,
            "user_message": user_tokens,
            "total": total_tokens,
            "max_context": settings.llm.llm_context_limits.get(model, settings.llm.default_context_limit),
        },
        "system_prompt_length": len(SYSTEM_PROMPT),
        "system_prompt_preview": SYSTEM_PROMPT[:500] + "..." if len(SYSTEM_PROMPT) > 500 else SYSTEM_PROMPT,
        "request_body": request_body,
        "request_body_size_bytes": len(json.dumps(request_body)),
        "endpoint": f"{settings.llm.base_url.rstrip('/')}/chat/completions",
    }
