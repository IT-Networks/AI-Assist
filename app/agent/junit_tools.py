"""
JUnit Test Generator Tools.

Tools für die Generierung von JUnit-Tests:
- analyze_java_class: Analysiert eine Java-Klasse und zeigt testbare Methoden
- generate_junit_test: Generiert JUnit-Tests für eine Klasse/Methode

Abgrenzung zu anderen Tools:
- search_code: Sucht Code nach Stichwort (für Entdeckung)
- trace_java_references: Verfolgt Vererbungshierarchie (für Analyse)
- read_file: Liest Dateiinhalt roh (für Detail-Ansicht)
- Diese Tools: Analysieren Struktur und generieren Tests

Verwendung:
1. analyze_java_class → Zeigt Methoden-Signaturen
2. generate_junit_test → Erstellt Test-Datei
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry

logger = logging.getLogger(__name__)


def register_junit_tools(registry: ToolRegistry) -> int:
    """
    Registriert die JUnit Test Generator Tools.

    Returns:
        Anzahl der registrierten Tools
    """
    from app.core.config import settings

    count = 0

    # ══════════════════════════════════════════════════════════════════════════════
    # analyze_java_class
    # ══════════════════════════════════════════════════════════════════════════════

    async def analyze_java_class(**kwargs: Any) -> ToolResult:
        """
        Analysiert eine Java-Klasse und zeigt deren Struktur.

        Nutze dieses Tool um:
        - Testbare Methoden einer Klasse zu identifizieren
        - Parameter und Rückgabetypen zu sehen
        - Dependencies/Felder zu erkennen (für Mocking)

        NICHT nutzen für:
        - Code-Suche nach Stichwort → search_code
        - Vererbungshierarchie → trace_java_references
        - Rohen Dateiinhalt lesen → read_file
        """
        class_path: str = kwargs.get("class_path", "").strip()
        show_all: bool = kwargs.get("show_all", False)

        if not class_path:
            return ToolResult(
                success=False,
                error=(
                    "class_path ist erforderlich. "
                    "Beispiel: analyze_java_class(class_path=\"com/example/UserService.java\") "
                    "oder analyze_java_class(class_path=\"UserService\")"
                )
            )

        # Repository-Pfad
        repo_path = settings.java.get_active_path()
        if not repo_path:
            return ToolResult(
                success=False,
                error="Kein Java-Repository konfiguriert. Setze java.repos in config.yaml"
            )

        repo = Path(repo_path)
        if not repo.exists():
            return ToolResult(success=False, error=f"Repository nicht gefunden: {repo_path}")

        # Java-Datei finden
        java_file = None

        # Vollständiger Pfad?
        if class_path.endswith(".java"):
            candidate = repo / class_path
            if candidate.exists():
                java_file = candidate
            else:
                # Ohne Pfad-Prefix suchen
                matches = list(repo.rglob(class_path.split("/")[-1]))
                if matches:
                    java_file = matches[0]
        else:
            # Klassenname → .java suchen
            simple_name = class_path.split(".")[-1]
            matches = list(repo.rglob(f"{simple_name}.java"))
            # Exclude test files
            matches = [m for m in matches if "test" not in str(m).lower()]
            if matches:
                java_file = matches[0]

        if not java_file or not java_file.exists():
            return ToolResult(
                success=False,
                error=f"Java-Klasse nicht gefunden: {class_path}. Nutze search_code um die Datei zu finden."
            )

        # Parsen
        try:
            from app.utils.java_analyzer import get_java_analyzer
            analyzer = get_java_analyzer()
            java_class = analyzer.parse_file(str(java_file))
        except ImportError:
            return ToolResult(
                success=False,
                error="javalang nicht installiert. Bitte 'pip install javalang' ausführen."
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Parse-Fehler: {e}")

        if not java_class:
            return ToolResult(success=False, error=f"Konnte {java_file.name} nicht parsen")

        # Ausgabe formatieren
        output = f"=== Java-Klasse: {java_class.name} ===\n"
        output += f"Datei: {java_file.relative_to(repo)}\n"
        output += f"Package: {java_class.package or '(default)'}\n"

        if java_class.extends:
            output += f"Extends: {java_class.extends}\n"
        if java_class.implements:
            output += f"Implements: {', '.join(java_class.implements)}\n"
        if java_class.annotations:
            output += f"Annotations: {', '.join(java_class.annotations)}\n"

        output += "\n"

        # Felder (Dependencies)
        deps = java_class.get_dependencies()
        if deps:
            output += "### Dependencies (für Mocking)\n"
            for dep in deps:
                output += f"  - {dep}\n"
            output += "\n"

        # Konstruktoren
        if java_class.constructors:
            output += "### Konstruktoren\n"
            for ctor in java_class.constructors:
                params = ", ".join(f"{p.type} {p.name}" for p in ctor.parameters)
                output += f"  {java_class.name}({params})\n"
            output += "\n"

        # Methoden
        testable = java_class.get_testable_methods()
        all_methods = java_class.methods

        output += f"### Testbare Methoden ({len(testable)}/{len(all_methods)})\n"

        for method in (all_methods if show_all else testable):
            # Signatur
            sig = method.signature
            modifiers = " ".join(method.modifiers) + " " if method.modifiers else ""

            testable_marker = "✓" if method.is_testable else "✗"
            output += f"  {testable_marker} {modifiers}{sig}\n"

            if method.throws:
                output += f"      throws {', '.join(method.throws)}\n"
            if method.annotations:
                output += f"      {' '.join(method.annotations)}\n"

        if not show_all and len(all_methods) > len(testable):
            output += f"\n  ({len(all_methods) - len(testable)} weitere Methoden mit show_all=true)\n"

        # Empfehlung
        output += "\n### Nächster Schritt\n"
        output += f"Nutze generate_junit_test(class_path=\"{java_file.relative_to(repo)}\") "
        output += "um Tests zu generieren.\n"

        return ToolResult(success=True, data=output)

    registry.register(Tool(
        name="analyze_java_class",
        description=(
            "Analysiert eine Java-Klasse und zeigt deren Struktur: "
            "Methoden mit Parametern und Rückgabetypen, Felder/Dependencies für Mocking, "
            "Konstruktoren. Nutze dieses Tool VOR generate_junit_test um testbare Methoden "
            "zu identifizieren. "
            "NICHT für Code-Suche (→ search_code) oder Vererbung (→ trace_java_references)."
        ),
        category=ToolCategory.ANALYSIS,
        parameters=[
            ToolParameter(
                name="class_path",
                type="string",
                description=(
                    "Pfad zur Java-Datei (z.B. 'com/example/UserService.java') "
                    "oder Klassenname (z.B. 'UserService')"
                ),
                required=True,
            ),
            ToolParameter(
                name="show_all",
                type="boolean",
                description="Auch private/static Methoden anzeigen (default: false)",
                required=False,
                default=False,
            ),
        ],
        handler=analyze_java_class,
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════════
    # generate_junit_test
    # ══════════════════════════════════════════════════════════════════════════════

    async def generate_junit_test(**kwargs: Any) -> ToolResult:
        """
        Generiert JUnit-Tests für eine Java-Klasse.

        Erstellt eine Test-Klasse mit:
        - Setup-Methode
        - Tests für jede Methode (happy path, negative, edge cases)
        - Given-When-Then Struktur
        - Optional: Mockito Mocks, Spring Integration

        Die Test-Datei wird im passenden Test-Verzeichnis erstellt
        (src/test/java mit gespiegelter Package-Struktur).
        """
        class_path: str = kwargs.get("class_path", "").strip()
        methods_str: str = kwargs.get("methods", "").strip()
        version: str = kwargs.get("version", "5")
        style: str = kwargs.get("style", "").strip() or "basic"
        dry_run: bool = kwargs.get("dry_run", False)
        overwrite: bool = kwargs.get("overwrite", False)

        if not class_path:
            return ToolResult(
                success=False,
                error=(
                    "class_path ist erforderlich. "
                    "Beispiel: generate_junit_test(class_path=\"com/example/UserService.java\")"
                )
            )

        # Repository-Pfad
        repo_path = settings.java.get_active_path()
        if not repo_path:
            return ToolResult(
                success=False,
                error="Kein Java-Repository konfiguriert."
            )

        repo = Path(repo_path)

        # Java-Datei finden
        java_file = None
        if class_path.endswith(".java"):
            candidate = repo / class_path
            if candidate.exists():
                java_file = candidate
            else:
                matches = list(repo.rglob(class_path.split("/")[-1]))
                if matches:
                    java_file = matches[0]
        else:
            simple_name = class_path.split(".")[-1]
            matches = list(repo.rglob(f"{simple_name}.java"))
            matches = [m for m in matches if "test" not in str(m).lower()]
            if matches:
                java_file = matches[0]

        if not java_file or not java_file.exists():
            return ToolResult(
                success=False,
                error=f"Java-Klasse nicht gefunden: {class_path}"
            )

        # Parsen
        try:
            from app.utils.java_analyzer import get_java_analyzer
            from app.utils.test_finder import get_test_finder
            from app.utils.junit_templates import (
                get_junit_template_engine,
                TestConfig,
                JUnitVersion,
                TestStyle,
            )

            analyzer = get_java_analyzer()
            java_class = analyzer.parse_file(str(java_file))

            if not java_class:
                return ToolResult(success=False, error=f"Konnte {java_file.name} nicht parsen")

            # Test-Verzeichnis
            test_finder = get_test_finder(str(repo))

            # JUnit-Version erkennen
            detected_version = test_finder.get_junit_version()
            if detected_version:
                version = detected_version

            junit_version = JUnitVersion.JUNIT5 if version == "5" else JUnitVersion.JUNIT4

            # Style
            style_map = {
                "basic": TestStyle.BASIC,
                "mockito": TestStyle.MOCKITO,
                "spring": TestStyle.SPRING,
                "param": TestStyle.PARAMETERIZED,
            }
            test_style = style_map.get(style.lower(), TestStyle.BASIC)

            # Auto-detect Mockito wenn Dependencies vorhanden
            if test_style == TestStyle.BASIC and java_class.get_dependencies():
                test_style = TestStyle.MOCKITO

            # Config
            config = TestConfig(
                version=junit_version,
                style=test_style,
                generate_negative_tests=True,
                generate_edge_cases=True,
                use_given_when_then=True,
                add_todo_comments=True,
            )

            # Methoden filtern
            methods = java_class.get_testable_methods()
            if methods_str:
                method_names = [m.strip().lower() for m in methods_str.split(",")]
                methods = [m for m in methods if m.name.lower() in method_names]
                if not methods:
                    return ToolResult(
                        success=False,
                        error=f"Keine der Methoden gefunden: {methods_str}"
                    )

            # Template Engine
            engine = get_junit_template_engine(config)

            # Test generieren
            test_code = engine.generate_test_class(java_class, methods)

            # Test-Pfad bestimmen
            test_path, package = test_finder.get_test_path_for_source(str(java_file))

            # Existierenden Test prüfen
            existing_test = test_finder.find_existing_test(str(java_file))

        except ImportError as e:
            return ToolResult(success=False, error=f"Import-Fehler: {e}")
        except Exception as e:
            logger.exception("Fehler bei Test-Generierung")
            return ToolResult(success=False, error=f"Fehler: {e}")

        # Ausgabe
        output = f"=== JUnit Test Generator ===\n"
        output += f"Source: {java_file.relative_to(repo)}\n"
        output += f"Klasse: {java_class.name}\n"
        output += f"JUnit Version: {version}\n"
        output += f"Style: {test_style.value}\n"
        output += f"Methoden: {len(methods)}\n\n"

        if existing_test:
            output += f"⚠️ Existierender Test: {existing_test.relative_to(repo)}\n"
            if not overwrite:
                output += "Nutze overwrite=true um zu überschreiben.\n\n"

        output += f"Test-Datei: {test_path.relative_to(repo)}\n\n"

        output += "### Generierter Test-Code\n"
        output += f"```java\n{test_code}\n```\n"

        if dry_run:
            output += "\n[DRY RUN - Datei wurde nicht geschrieben]\n"
            return ToolResult(success=True, data=output)

        # Datei schreiben
        if existing_test and not overwrite:
            output += "\n[Test existiert bereits - nicht überschrieben]\n"
            return ToolResult(
                success=True,
                data=output,
                requires_confirmation=True,
                confirmation_data={
                    "operation": "generate_junit_test",
                    "existing": str(existing_test),
                    "test_path": str(test_path),
                    "message": "Test existiert. Mit overwrite=true überschreiben?"
                }
            )

        try:
            test_finder.ensure_package_dirs(test_path)
            test_path.write_text(test_code, encoding="utf-8")
            output += f"\n✓ Test-Datei erstellt: {test_path.relative_to(repo)}\n"

            # Validierung
            from app.utils.validators.java_validator import JavaValidator
            validator = JavaValidator({"mode": "quick"})
            result = await validator.validate(str(test_path), fix=False, strict=False)

            if result.success:
                output += "✓ Syntax-Validierung: OK\n"
            else:
                output += f"⚠️ Syntax-Validierung: {result.error_count} Fehler\n"
                for issue in result.errors[:3]:
                    output += f"   - {issue}\n"
                output += "\nHinweis: Manuelle Anpassungen erforderlich.\n"

        except Exception as e:
            output += f"\n✗ Fehler beim Schreiben: {e}\n"
            return ToolResult(success=False, data=output, error=str(e))

        return ToolResult(success=True, data=output)

    registry.register(Tool(
        name="generate_junit_test",
        description=(
            "Generiert JUnit-Tests für eine Java-Klasse. "
            "Erstellt Test-Klasse mit Setup, Happy-Path-Tests, Negative-Tests und Edge-Cases. "
            "Unterstützt JUnit 4/5, Mockito und Spring. "
            "Die Test-Datei wird im src/test/java Verzeichnis mit gespiegelter Package-Struktur erstellt. "
            "Tipp: Nutze zuerst analyze_java_class um die Methoden zu sehen."
        ),
        category=ToolCategory.FILE,
        is_write_operation=True,
        parameters=[
            ToolParameter(
                name="class_path",
                type="string",
                description="Pfad zur Java-Datei oder Klassenname",
                required=True,
            ),
            ToolParameter(
                name="methods",
                type="string",
                description="Kommagetrennte Methodennamen (leer = alle testbaren)",
                required=False,
            ),
            ToolParameter(
                name="version",
                type="string",
                description="JUnit Version: '4' oder '5' (default: auto-detect oder '5')",
                required=False,
                enum=["4", "5"],
            ),
            ToolParameter(
                name="style",
                type="string",
                description="Test-Stil: basic, mockito, spring (default: auto-detect basierend auf Dependencies)",
                required=False,
                enum=["basic", "mockito", "spring"],
            ),
            ToolParameter(
                name="dry_run",
                type="boolean",
                description="Nur Preview, nicht schreiben",
                required=False,
                default=False,
            ),
            ToolParameter(
                name="overwrite",
                type="boolean",
                description="Existierenden Test überschreiben",
                required=False,
                default=False,
            ),
        ],
        handler=generate_junit_test,
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════════
    # run_junit_tests
    # ══════════════════════════════════════════════════════════════════════════════

    async def run_junit_tests(**kwargs: Any) -> ToolResult:
        """
        Führt JUnit-Tests aus und zeigt Ergebnisse.

        Nutze dieses Tool um:
        - Tests für eine Klasse/Package auszuführen
        - Test-Ergebnisse und Coverage zu sehen
        - Fehlgeschlagene Tests zu analysieren

        Bei Fehlern: Nutze suggest_test_fix für Lösungsvorschläge.
        """
        from app.services.test_execution import get_test_execution_service

        target: str = kwargs.get("target", "").strip()
        with_coverage: bool = kwargs.get("with_coverage", True)
        test_method: str = kwargs.get("test_method", "").strip() or None

        if not target:
            return ToolResult(
                success=False,
                error=(
                    "target ist erforderlich. "
                    "Beispiele:\n"
                    "- run_junit_tests(target=\"UserService\")\n"
                    "- run_junit_tests(target=\"com.example.service\")\n"
                    "- run_junit_tests(target=\"UserServiceTest\", test_method=\"testCreate\")"
                )
            )

        session_id = kwargs.get("_session_id", "default")
        service = get_test_execution_service()

        results = []
        output_lines = []

        try:
            async for event in service.run_tests(target, session_id, with_coverage, test_method):
                event_type = event.get("type", "")

                if event_type == "output":
                    # Nur wichtige Zeilen sammeln
                    line = event.get("data", {}).get("line", "")
                    if any(k in line for k in ["Running", "Tests run", "PASSED", "FAILED", "ERROR"]):
                        output_lines.append(line)

                elif event_type in ("started", "test_started", "test_finished", "suite_finished", "finished", "error"):
                    results.append(event)

        except Exception as e:
            return ToolResult(success=False, error=f"Test-Ausführung fehlgeschlagen: {str(e)}")

        # Final event finden
        final_event = next((e for e in reversed(results) if e["type"] == "finished"), None)

        if not final_event:
            error_event = next((e for e in results if e["type"] == "error"), None)
            error_msg = error_event.get("data", {}).get("message", "Unbekannter Fehler") if error_event else "Test-Lauf fehlgeschlagen"
            return ToolResult(success=False, error=error_msg)

        run = final_event["data"]

        # Formatierte Ausgabe
        output = [
            f"# Test-Ergebnisse: {run.get('target', target)}",
            f"Status: {'✓ PASSED' if run.get('status') == 'passed' else '✗ FAILED'}",
            f"Tests: {run.get('passed_tests', 0)}/{run.get('total_tests', 0)} bestanden",
            ""
        ]

        # Build info
        output.append(f"Build: {run.get('build_tool', 'maven')}")
        output.append(f"Dauer: {run.get('duration_seconds', 0):.2f}s")
        output.append("")

        # Suites und Tests
        for suite in run.get("suites", []):
            output.append(f"## {suite.get('name', 'Unknown')}")
            for tc in suite.get("tests", []):
                status = tc.get("status", "unknown")
                icon_map = {"passed": "✓", "failed": "✗", "error": "⚠", "skipped": "○"}
                icon = icon_map.get(status, "?")
                duration = tc.get("duration_seconds", 0)
                output.append(f"  {icon} {tc.get('name', 'unknown')} ({duration:.2f}s)")

                if tc.get("failure_message"):
                    output.append(f"    └─ {tc.get('failure_message', '')[:200]}")
                    if tc.get("stack_trace"):
                        # Erste relevante Zeile des Stack Traces
                        lines = tc.get("stack_trace", "").split("\n")[:3]
                        for line in lines:
                            if line.strip():
                                output.append(f"       {line.strip()[:100]}")
            output.append("")

        # Coverage
        if run.get("coverage_percent") is not None:
            coverage = run.get("coverage_percent", 0)
            bar_filled = int(coverage / 5)
            bar_empty = 20 - bar_filled
            output.append(f"Coverage: {coverage:.1f}% {'█' * bar_filled}{'░' * bar_empty}")
            output.append("")

        # Summary
        output.append("---")
        output.append(f"Passed: {run.get('passed_tests', 0)} | Failed: {run.get('failed_tests', 0)} | Errors: {run.get('error_tests', 0)} | Skipped: {run.get('skipped_tests', 0)}")

        # Empfehlung bei Fehlern
        if run.get("failed_tests", 0) > 0 or run.get("error_tests", 0) > 0:
            output.append("")
            output.append("💡 Tipp: Nutze suggest_test_fix für Fix-Vorschläge")

        return ToolResult(
            success=run.get("status") == "passed",
            data="\n".join(output),
            confirmation_data={"test_run": run}
        )

    registry.register(Tool(
        name="run_junit_tests",
        description=(
            "Führt JUnit-Tests aus und zeigt Ergebnisse inkl. Coverage. "
            "Unterstützt Maven und Gradle. "
            "Parameter: target (Klasse oder Package), with_coverage (bool), test_method (optional, spezifische Methode). "
            "Bei Fehlern: Nutze suggest_test_fix für Lösungsvorschläge."
        ),
        category=ToolCategory.ANALYSIS,
        parameters=[
            ToolParameter(
                name="target",
                type="string",
                description="Klasse oder Package zum Testen (z.B. 'UserService' oder 'com.example.service')",
                required=True
            ),
            ToolParameter(
                name="with_coverage",
                type="boolean",
                description="Coverage-Report generieren (default: true)",
                required=False,
                default=True
            ),
            ToolParameter(
                name="test_method",
                type="string",
                description="Spezifische Test-Methode (optional, z.B. 'testCreate')",
                required=False
            ),
        ],
        handler=run_junit_tests
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════════
    # suggest_test_fix
    # ══════════════════════════════════════════════════════════════════════════════

    async def suggest_test_fix(**kwargs: Any) -> ToolResult:
        """
        Generiert Fix-Vorschläge für fehlgeschlagene Tests.

        Analysiert den Fehler und schlägt einen Fix vor:
        - Pattern-basiert für bekannte Fehlertypen
        - LLM-basiert für komplexe Fälle

        Nutze dieses Tool nach run_junit_tests wenn Tests fehlschlagen.
        """
        from app.services.test_execution import (
            get_test_fix_generator,
            TestCase,
            TestStatus
        )

        test_class: str = kwargs.get("test_class", "").strip()
        test_method: str = kwargs.get("test_method", "").strip()
        error_message: str = kwargs.get("error_message", "").strip()
        stack_trace: str = kwargs.get("stack_trace", "").strip()

        if not test_class or not error_message:
            return ToolResult(
                success=False,
                error=(
                    "test_class und error_message sind erforderlich.\n"
                    "Beispiel:\n"
                    "suggest_test_fix(\n"
                    "  test_class=\"UserServiceTest\",\n"
                    "  test_method=\"testUpdateUser\",\n"
                    "  error_message=\"expected:<John> but was:<Jane>\"\n"
                    ")"
                )
            )

        # TestCase erstellen
        test_case = TestCase(
            name=test_method or "unknown",
            class_name=test_class,
            status=TestStatus.FAILED,
            failure_message=error_message,
            stack_trace=stack_trace or None
        )

        generator = get_test_fix_generator()

        try:
            fix = await generator.generate_fix(test_case)
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Fix-Generierung fehlgeschlagen: {str(e)}"
            )

        if not fix:
            return ToolResult(
                success=False,
                error=(
                    "Konnte keinen Fix generieren.\n"
                    "Mögliche Gründe:\n"
                    "- Fehlermeldung nicht erkannt\n"
                    "- Test-Klasse nicht gefunden\n"
                    "- Komplexer Fehler der manuelle Analyse erfordert"
                )
            )

        # Ausgabe formatieren
        confidence_bar = "█" * int(fix.confidence * 10) + "░" * (10 - int(fix.confidence * 10))
        output = [
            f"# Fix-Vorschlag für {test_class}.{test_method or '?'}",
            "",
            f"**Typ:** {fix.fix_type}",
            f"**Confidence:** {fix.confidence:.0%} {confidence_bar}",
            "",
            f"## Beschreibung",
            fix.description,
            "",
        ]

        if fix.file_path:
            output.append(f"**Datei:** {fix.file_path}")
            output.append("")

        if fix.diff:
            output.append("## Vorgeschlagene Änderung")
            output.append("```diff")
            output.append(fix.diff)
            output.append("```")
            output.append("")

        output.append("---")
        output.append("💡 Prüfe den Vorschlag sorgfältig bevor du ihn anwendest.")

        return ToolResult(
            success=True,
            data="\n".join(output),
            confirmation_data={
                "fix": fix.to_dict(),
                "action": "review_fix"
            }
        )

    registry.register(Tool(
        name="suggest_test_fix",
        description=(
            "Generiert Fix-Vorschläge für fehlgeschlagene Tests. "
            "Analysiert Fehlermeldung und Stack-Trace um einen Fix vorzuschlagen. "
            "Nutze dieses Tool nach run_junit_tests wenn Tests fehlschlagen."
        ),
        category=ToolCategory.ANALYSIS,
        parameters=[
            ToolParameter(
                name="test_class",
                type="string",
                description="Name der Test-Klasse (z.B. 'UserServiceTest')",
                required=True
            ),
            ToolParameter(
                name="test_method",
                type="string",
                description="Name der fehlgeschlagenen Test-Methode",
                required=False
            ),
            ToolParameter(
                name="error_message",
                type="string",
                description="Fehlermeldung des Tests",
                required=True
            ),
            ToolParameter(
                name="stack_trace",
                type="string",
                description="Stack-Trace (optional, verbessert Fix-Qualität)",
                required=False
            ),
        ],
        handler=suggest_test_fix
    ))
    count += 1

    return count
