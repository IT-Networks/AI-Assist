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
        is_windows = os.name == 'nt'

        # Windows: Wenn mvn ohne Extension angegeben, nach mvn.cmd suchen
        if is_windows and mvn == "mvn":
            import shutil
            mvn_cmd = shutil.which("mvn.cmd") or shutil.which("mvn.bat") or shutil.which("mvn")
            if mvn_cmd:
                mvn = mvn_cmd

        # Windows: .cmd/.bat Dateien müssen über cmd.exe ausgeführt werden
        if is_windows and (mvn.endswith(".cmd") or mvn.endswith(".bat")):
            cmd = ["cmd.exe", "/c", mvn, "-f", str(pom)] + build.goals.split()
        else:
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

    # ── maven_analyze_pom ─────────────────────────────────────────────────────
    async def maven_analyze_pom(**kwargs: Any) -> ToolResult:
        from app.api.routes.maven import _parse_dependencies
        pom_path: str = kwargs.get("pom_path", "")

        if not pom_path:
            # Versuche pom.xml aus aktiven Repo zu ermitteln
            from pathlib import Path
            repo = settings.java.get_active_path() if hasattr(settings, "java") else ""
            if repo:
                candidates = list(Path(repo).glob("pom.xml"))
                if candidates:
                    pom_path = str(candidates[0])
        if not pom_path:
            return ToolResult(success=False, error="pom_path nicht angegeben und kein aktives Java-Repo konfiguriert")

        from pathlib import Path
        if not Path(pom_path).exists():
            return ToolResult(success=False, error=f"pom.xml nicht gefunden: {pom_path}")

        try:
            deps = _parse_dependencies(pom_path)
        except Exception as e:
            return ToolResult(success=False, error=str(e))

        return ToolResult(success=True, data={
            "pom_path": pom_path,
            "dependency_count": len(deps),
            "dependencies": deps,
            "hint": (
                "safe_to_comment_out=true: scope ist test/provided/optional – sicher auskommentierbar. "
                "existing_exclusions: bereits vorhandene Exclusions. "
                "can_exclude_transitive=true: <exclusion> für Sub-Dependencies möglich."
            ),
        })

    registry.register(Tool(
        name="maven_analyze_pom",
        description=(
            "Liest alle Dependencies aus einer pom.xml und analysiert ob sie sicher "
            "auskommentiert oder per <exclusion> von Sub-Dependencies bereinigt werden können. "
            "Zeigt Scope, Version und bestehende Exclusions je Dependency. "
            "Nutze dies VOR jeder pom.xml-Änderung."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(name="pom_path", type="string", description="Pfad zur pom.xml (optional wenn aktives Java-Repo konfiguriert)", required=False),
        ],
        handler=maven_analyze_pom,
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════
    # COMBINED BUILD + DEPLOY WORKFLOW
    # ══════════════════════════════════════════════════════════════════════════

    # ── maven_build_deploy ────────────────────────────────────────────────────
    async def maven_build_deploy(**kwargs: Any) -> ToolResult:
        """
        Kombinierter Workflow: Maven Build + WLP Deployment.

        Führt aus:
        1. Maven Build (clean install)
        2. Artefakt in WLP deployen (dropins oder apps)
        3. Optional: Server neu starten
        4. Logs auf Fehler prüfen
        """
        import asyncio
        from pathlib import Path

        build_id: str = kwargs.get("build_id", "")
        server_id: str = kwargs.get("server_id", "")
        skip_tests: bool = kwargs.get("skip_tests", True)
        restart_server: bool = kwargs.get("restart_server", False)
        deploy_method: str = kwargs.get("deploy_method", "dropins")

        # Validierung
        if not build_id:
            return ToolResult(success=False, error="build_id ist erforderlich")
        if not server_id:
            return ToolResult(success=False, error="server_id ist erforderlich")

        build = next((b for b in settings.maven.builds if b.id == build_id), None)
        if not build:
            return ToolResult(success=False, error=f"Maven-Build '{build_id}' nicht gefunden")

        # WLP-Server prüfen
        if not settings.wlp.enabled:
            return ToolResult(success=False, error="WLP ist nicht aktiviert")

        from app.core.config import settings as app_settings
        srv = next((s for s in app_settings.wlp.servers if s.id == server_id), None)
        if not srv:
            return ToolResult(success=False, error=f"WLP-Server '{server_id}' nicht gefunden")

        steps = []
        overall_success = True

        # ── Step 1: Maven Build ──────────────────────────────────────────────
        build_result = await maven_run_build(
            build_id=build_id,
            skip_tests=skip_tests,
        )

        build_success = build_result.success
        steps.append({
            "step": "maven_build",
            "success": build_success,
            "exit_code": build_result.data.get("exit_code") if build_result.data else None,
            "summary": build_result.data.get("summary_lines", [])[-5:] if build_result.data else [],
        })

        if not build_success:
            return ToolResult(
                success=False,
                data={
                    "success": False,
                    "failed_at": "maven_build",
                    "steps": steps,
                    "build_output": build_result.data.get("full_output", "")[-2000:] if build_result.data else "",
                },
                error="Maven-Build fehlgeschlagen",
            )

        # ── Step 2: Artefakt finden ──────────────────────────────────────────
        pom_dir = Path(build.pom_path).parent
        artifact_path = None

        for pattern in ["target/*.war", "target/*.ear", "**/target/*.war", "**/target/*.ear"]:
            matches = list(pom_dir.glob(pattern))
            if matches:
                artifact_path = str(max(matches, key=lambda p: p.stat().st_mtime))
                break

        if not artifact_path:
            return ToolResult(
                success=False,
                data={
                    "success": False,
                    "failed_at": "find_artifact",
                    "steps": steps,
                },
                error="Kein WAR/EAR Artefakt in target/ gefunden nach Build",
            )

        steps.append({
            "step": "find_artifact",
            "success": True,
            "artifact": artifact_path,
            "size_kb": round(Path(artifact_path).stat().st_size / 1024, 1),
        })

        # ── Step 3: Deploy ───────────────────────────────────────────────────
        from app.agent.wlp_tools import register_wlp_tools
        # Importiere die Deploy-Funktion aus wlp_tools
        # Da wir die Funktion direkt nicht importieren können, rufen wir sie über die Registry

        # Direkter Aufruf der Deploy-Logik
        import shutil
        from datetime import datetime

        server_dir = Path(srv.wlp_path) / "usr" / "servers" / srv.server_name
        target_dir = server_dir / ("dropins" if deploy_method == "dropins" else "apps")
        target_dir.mkdir(parents=True, exist_ok=True)

        artifact = Path(artifact_path)
        target_file = target_dir / artifact.name

        # Backup
        backup_path = None
        if target_file.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = target_dir / f"{artifact.stem}_{timestamp}{artifact.suffix}.bak"
            try:
                shutil.copy2(target_file, backup_path)
            except Exception:
                backup_path = None

        # Kopieren
        try:
            shutil.copy2(artifact, target_file)
            deploy_success = True
        except Exception as e:
            deploy_success = False
            steps.append({"step": "deploy", "success": False, "error": str(e)})
            return ToolResult(
                success=False,
                data={"success": False, "failed_at": "deploy", "steps": steps},
                error=f"Deployment fehlgeschlagen: {e}",
            )

        steps.append({
            "step": "deploy",
            "success": True,
            "target": str(target_file),
            "method": deploy_method,
            "backup": str(backup_path) if backup_path else None,
        })

        # ── Step 4: Optional Server Restart ──────────────────────────────────
        if restart_server:
            from app.agent.wlp_tools import _run_server_command

            # Stop
            stop_result = await _run_server_command(srv.wlp_path, srv.server_name, "stop", timeout=60)
            steps.append({"step": "server_stop", "success": stop_result.get("success", False)})

            await asyncio.sleep(2)

            # Start
            start_result = await _run_server_command(srv.wlp_path, srv.server_name, "start", timeout=srv.start_timeout_seconds)
            steps.append({"step": "server_start", "success": start_result.get("success", False)})

            if not start_result.get("success"):
                overall_success = False

        # ── Step 5: Log-Check ────────────────────────────────────────────────
        await asyncio.sleep(3)  # Warten auf Log-Einträge

        deployment_errors = []
        try:
            log_path = Path(srv.wlp_path) / "usr" / "servers" / srv.server_name / "logs" / "messages.log"
            if log_path.exists():
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()[-100:]

                # Fehler extrahieren (vereinfacht)
                import re
                error_pattern = re.compile(r"\[(ERROR|WARNING)\s*\].*?(CWW[A-Z]{2}\d{4}[EIWA])")
                for line in lines:
                    match = error_pattern.search(line)
                    if match:
                        deployment_errors.append({
                            "severity": match.group(1),
                            "code": match.group(2),
                            "line": line.strip()[:150],
                        })
        except Exception:
            pass

        steps.append({
            "step": "log_check",
            "errors_found": len(deployment_errors),
        })

        # ── Result ───────────────────────────────────────────────────────────
        if deployment_errors:
            overall_success = False

        return ToolResult(
            success=overall_success,
            data={
                "success": overall_success,
                "steps": steps,
                "artifact": artifact.name,
                "deployed_to": str(target_file),
                "deployment_errors": deployment_errors[:5],
                "summary": (
                    "Build + Deploy erfolgreich!" if overall_success
                    else f"Deployment mit {len(deployment_errors)} Fehler(n)"
                ),
            },
            error=None if overall_success else "Deployment-Fehler erkannt - prüfe deployment_errors",
        )

    registry.register(Tool(
        name="maven_build_deploy",
        description=(
            "Kombinierter Workflow: Maven Build + WLP Deployment in einem Schritt. "
            "Ideal zum schnellen Testen von Code-Fixes. "
            "1. Führt Maven-Build aus (clean install) "
            "2. Kopiert WAR/EAR in WLP dropins/ oder apps/ "
            "3. Optional: Server neu starten "
            "4. Prüft Logs auf Deployment-Fehler"
        ),
        category=ToolCategory.DEVOPS,
        is_write_operation=True,
        parameters=[
            ToolParameter(
                name="build_id",
                type="string",
                description="ID des Maven-Builds (aus maven_list_builds)",
                required=True,
            ),
            ToolParameter(
                name="server_id",
                type="string",
                description="ID des WLP-Servers (aus wlp_list_servers)",
                required=True,
            ),
            ToolParameter(
                name="skip_tests",
                type="boolean",
                description="Maven-Tests überspringen für schnelleren Build (Standard: true)",
                required=False,
                default=True,
            ),
            ToolParameter(
                name="restart_server",
                type="boolean",
                description="WLP-Server nach Deploy neu starten (Standard: false = Hot-Deploy)",
                required=False,
                default=False,
            ),
            ToolParameter(
                name="deploy_method",
                type="string",
                description="Deployment-Methode: 'dropins' (Hot-Deploy) oder 'apps'",
                required=False,
                enum=["dropins", "apps"],
                default="dropins",
            ),
        ],
        handler=maven_build_deploy,
    ))
    count += 1

    return count
