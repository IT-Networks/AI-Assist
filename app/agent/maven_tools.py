"""
Agent-Tools für Maven-Build-Ausführung.
"""

from typing import Any

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry


def register_maven_tools(registry: ToolRegistry) -> int:
    from app.core.config import settings

    if not settings.maven.enabled:
        return 0

    count = 0

    # ── maven_list_builds ─────────────────────────────────────────────────────
    async def maven_list_builds(**kwargs: Any) -> ToolResult:
        from app.api.routes.maven import _running_builds
        builds = [
            {
                "id": b.id,
                "name": b.name,
                "description": b.description,
                "pom_path": b.pom_path,
                "goals": b.goals,
                "is_running": b.id in _running_builds,
            }
            for b in settings.maven.builds
        ]
        return ToolResult(success=True, data={"builds": builds})

    registry.register(Tool(
        name="maven_list_builds",
        description="Listet alle konfigurierten Maven-Builds auf inkl. Ziele und Status.",
        category=ToolCategory.SEARCH,
        parameters=[],
        handler=maven_list_builds,
    ))
    count += 1

    # ── maven_run_build ───────────────────────────────────────────────────────
    async def maven_run_build(**kwargs: Any) -> ToolResult:
        import asyncio, os
        from pathlib import Path

        build_id: str = kwargs.get("build_id", "")
        extra_args: str = kwargs.get("extra_args", "")
        skip_tests: bool = kwargs.get("skip_tests", None)

        build = next((b for b in settings.maven.builds if b.id == build_id), None)
        if not build:
            return ToolResult(success=False, error=f"Build '{build_id}' nicht gefunden")

        pom = Path(build.pom_path)
        if not pom.exists():
            return ToolResult(success=False, error=f"pom.xml nicht gefunden: {pom}")

        mvn = settings.maven.mvn_executable
        cmd = [mvn, "-f", str(pom)] + build.goals.split()
        if build.profiles:
            cmd += ["-P", ",".join(build.profiles)]
        if (skip_tests is True) or (skip_tests is None and build.skip_tests):
            cmd += ["-DskipTests=true"]
        if build.extra_args:
            cmd += build.extra_args.split()
        if extra_args:
            cmd += extra_args.split()

        env = dict(os.environ)
        if build.jvm_args:
            env["MAVEN_OPTS"] = build.jvm_args

        timeout = settings.maven.default_timeout_minutes * 60

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(pom.parent),
                env=env,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = stdout.decode(errors="replace") if stdout else ""
            success = proc.returncode == 0

            # Relevante Zeilen extrahieren (Fehler + Zusammenfassung)
            lines = output.splitlines()
            relevant = [l for l in lines if any(k in l for k in ("[ERROR]", "[WARNING]", "BUILD ", "Tests run:", "BUILD SUCCESS", "BUILD FAILURE"))]

            return ToolResult(
                success=success,
                data={
                    "exit_code": proc.returncode,
                    "success": success,
                    "summary_lines": relevant[-50:],
                    "full_output": output[-5000:] if len(output) > 5000 else output,
                    "cmd": " ".join(cmd),
                },
                error=None if success else "Maven-Build fehlgeschlagen. Prüfe summary_lines.",
            )
        except asyncio.TimeoutError:
            return ToolResult(success=False, error=f"Build-Timeout nach {settings.maven.default_timeout_minutes} Minuten")
        except FileNotFoundError:
            return ToolResult(success=False, error=f"mvn-Executable nicht gefunden: {mvn}")
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    registry.register(Tool(
        name="maven_run_build",
        description=(
            "Führt einen konfigurierten Maven-Build aus und wartet auf das Ergebnis. "
            "Gibt Build-Zusammenfassung, Fehlerzeilen und Exit-Code zurück. "
            "Nutze maven_list_builds für verfügbare Build-IDs."
        ),
        category=ToolCategory.FILE,
        is_write_operation=True,
        parameters=[
            ToolParameter(name="build_id", type="string", description="ID des Builds", required=True),
            ToolParameter(name="extra_args", type="string", description="Zusätzliche mvn-Argumente", required=False),
            ToolParameter(name="skip_tests", type="boolean", description="Tests überspringen", required=False),
        ],
        handler=maven_run_build,
    ))
    count += 1

    return count
