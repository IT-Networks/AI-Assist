import asyncio
import re
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from app.api.schemas import GenerateRequest, GenerateResponse, ValidationResponse, TestResponse
from app.core.config import settings
from app.core.exceptions import LLMError

router = APIRouter(prefix="/api/python", tags=["python"])


def _get_reader():
    from app.services.python_reader import PythonReader
    if not settings.python.repo_path:
        raise HTTPException(status_code=400, detail="python.repo_path nicht konfiguriert")
    return PythonReader(
        settings.python.repo_path,
        exclude_dirs=settings.python.exclude_dirs,
        max_file_size_kb=settings.python.max_file_size_kb,
    )


# ── Repo-Endpunkte ───────────────────────────────────────────────────────────

@router.get("/tree")
async def get_python_tree():
    reader = _get_reader()
    return reader.get_file_tree()


@router.get("/file")
async def get_python_file(path: str = Query(..., description="Relativer Dateipfad")):
    reader = _get_reader()
    try:
        content = reader.read_file(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Datei nicht gefunden: {path}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"path": path, "content": content}


@router.get("/summary")
async def get_python_summary(path: str = Query(..., description="Relativer Dateipfad")):
    reader = _get_reader()
    summary = reader.summarize_file(path)
    if "error" in summary and len(summary) <= 2:
        raise HTTPException(status_code=422, detail=summary["error"])
    return summary


@router.get("/search")
async def search_python_symbol(q: str = Query(..., description="Symbol-Name (Klasse/Funktion)")):
    reader = _get_reader()
    matches = reader.search_symbol(q)
    return {"query": q, "matches": matches}


# ── Index-Endpunkte ──────────────────────────────────────────────────────────

@router.post("/index/build")
async def build_python_index(
    force: bool = Query(False),
    background: bool = Query(False),
    bg_tasks: BackgroundTasks = None,
):
    from app.services.python_indexer import get_python_indexer
    from app.services.python_reader import PythonReader

    if not settings.python.repo_path:
        raise HTTPException(status_code=400, detail="python.repo_path nicht konfiguriert")

    reader = PythonReader(
        settings.python.repo_path,
        exclude_dirs=settings.python.exclude_dirs,
        max_file_size_kb=settings.python.max_file_size_kb,
    )
    indexer = get_python_indexer()

    if background and bg_tasks is not None:
        def _run():
            loop = asyncio.new_event_loop()
            loop.run_until_complete(
                asyncio.get_event_loop().run_in_executor(
                    None, lambda: indexer.build(settings.python.repo_path, reader, force=force)
                )
            )
        bg_tasks.add_task(_run)
        return {"message": "Index-Build gestartet (Hintergrund)"}

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: indexer.build(settings.python.repo_path, reader, force=force)
    )
    return result


@router.get("/index/status")
async def get_python_index_status():
    from app.services.python_indexer import get_python_indexer
    indexer = get_python_indexer()
    stats = indexer.get_stats()
    return {**stats, "is_built": stats["is_built"]}


@router.get("/index/search")
async def search_python_index(
    q: str = Query(...),
    top_k: int = Query(5, ge=1, le=20),
):
    from app.services.python_indexer import get_python_indexer
    indexer = get_python_indexer()
    if not indexer.is_built():
        raise HTTPException(status_code=400, detail="Index nicht aufgebaut – bitte zuerst /index/build aufrufen")
    results = indexer.search(q, top_k=top_k)
    return {"query": q, "results": results}


@router.delete("/index")
async def delete_python_index():
    from app.services.python_indexer import get_python_indexer
    get_python_indexer().clear()
    return {"message": "Python-Index gelöscht"}


# ── Validierung ───────────────────────────────────────────────────────────────

@router.post("/validate", response_model=ValidationResponse)
async def validate_python(
    repo_path: str = Query(..., description="Pfad zum Python-Projekt"),
    tools: List[str] = Query(["flake8", "ruff", "mypy"], description="Tools: flake8, ruff, mypy"),
):
    project_path = Path(repo_path)
    if not project_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Verzeichnis nicht gefunden: {repo_path}")

    tool_map = {
        "flake8": settings.tools.flake8,
        "ruff": settings.tools.ruff,
        "mypy": settings.tools.mypy,
    }

    results = {}
    for tool in tools:
        if tool not in tool_map:
            continue
        tool_path = tool_map[tool]
        try:
            proc = await asyncio.create_subprocess_exec(
                tool_path, ".",
                cwd=str(project_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            except asyncio.TimeoutError:
                proc.kill()
                results[tool] = {
                    "tool": tool,
                    "stdout": "",
                    "stderr": f"Timeout nach 60s",
                    "returncode": -1,
                }
                continue
            results[tool] = {
                "tool": tool,
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
                "returncode": proc.returncode,
            }
        except FileNotFoundError:
            results[tool] = {
                "tool": tool,
                "stdout": "",
                "stderr": f"Tool nicht gefunden: {tool_path}",
                "returncode": -1,
            }

    return ValidationResponse(repo_path=repo_path, results=results)


# ── Tests ─────────────────────────────────────────────────────────────────────

@router.post("/test", response_model=TestResponse)
async def run_python_tests(
    repo_path: str = Query(..., description="Pfad zum Python-Projekt"),
    args: Optional[str] = Query(None, description="Zusätzliche pytest-Argumente, z.B. '-v --tb=short'"),
):
    project_path = Path(repo_path)
    if not project_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Verzeichnis nicht gefunden: {repo_path}")

    cmd = [settings.tools.pytest]
    if args:
        cmd.extend(args.split())

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(project_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            proc.kill()
            raise HTTPException(status_code=504, detail="pytest Timeout nach 120s")
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail=f"pytest nicht gefunden: {settings.tools.pytest}")

    stdout_text = stdout.decode("utf-8", errors="replace")
    stderr_text = stderr.decode("utf-8", errors="replace")

    # Parse "N passed, M failed, K error" from pytest summary line
    passed = failed = errors_count = 0
    summary_match = re.search(
        r"(\d+) passed|(\d+) failed|(\d+) error",
        stdout_text,
        re.IGNORECASE,
    )
    for m in re.finditer(r"(\d+) (passed|failed|error)", stdout_text, re.IGNORECASE):
        n, label = int(m.group(1)), m.group(2).lower()
        if label == "passed":
            passed = n
        elif label == "failed":
            failed = n
        elif label == "error":
            errors_count = n

    return TestResponse(
        stdout=stdout_text,
        stderr=stderr_text,
        returncode=proc.returncode,
        passed=passed,
        failed=failed,
        errors=errors_count,
    )


# ── Code-Generierung ─────────────────────────────────────────────────────────

@router.post("/generate", response_model=GenerateResponse)
async def generate_python_app(request: GenerateRequest):
    target = Path(request.target_dir)
    target.mkdir(parents=True, exist_ok=True)

    from app.services.llm_client import llm_client

    generation_prompt = f"""Erstelle eine vollständige Python-Anwendung basierend auf dieser Beschreibung:

{request.description}

Schreibe alle notwendigen Dateien. Trenne jede Datei mit diesem Format:

=== FILE: relativer/pfad/zur/datei.py ===
[Dateiinhalt hier]
=== END FILE ===

Erstelle alle Dateien die für eine lauffähige Anwendung notwendig sind (z.B. main.py, requirements.txt, README.md falls sinnvoll).
Halte dich an moderne Python-Best-Practices (type hints, docstrings, Fehlerbehandlung).
"""

    messages = [
        {"role": "user", "content": generation_prompt}
    ]

    try:
        response_text = await llm_client.chat(messages=messages, model=request.model)
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Parse === FILE: ... === blocks
    files_written = []
    pattern = re.compile(
        r"=== FILE: (.+?) ===\n(.*?)(?:=== END FILE ===|(?==== FILE:))",
        re.DOTALL,
    )

    for match in pattern.finditer(response_text):
        rel_path = match.group(1).strip()
        content = match.group(2).rstrip()

        # Security: prevent path traversal
        file_path = (target / rel_path).resolve()
        if not str(file_path).startswith(str(target.resolve())):
            continue

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        files_written.append(rel_path)

    if not files_written:
        # Fallback: LLM didn't use the format – save raw response as note
        note_path = target / "generated_response.md"
        note_path.write_text(response_text, encoding="utf-8")
        files_written.append("generated_response.md")

    return GenerateResponse(
        files_written=files_written,
        target_dir=str(target),
        message=f"{len(files_written)} Datei(en) erstellt in {target}",
    )
