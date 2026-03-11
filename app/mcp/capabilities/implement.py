"""
Implement Capability - Code implementation and generation.

Generates production-ready code based on designs or direct requirements.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from app.mcp.capabilities.base import (
    BaseCapability,
    CapabilityPhase,
    CapabilitySession
)

logger = logging.getLogger(__name__)


class FileWorkflowTracker:
    """
    Trackt erstellte Verzeichnisse und Dateien um redundante Operationen zu vermeiden.
    """

    def __init__(self):
        self.created_directories: Set[str] = set()
        self.created_files: Set[str] = set()
        self.pending_files: Dict[str, str] = {}  # path -> content

    def needs_directory(self, dir_path: str) -> bool:
        """Prüft ob ein Verzeichnis noch erstellt werden muss."""
        normalized = str(Path(dir_path).resolve())
        if normalized in self.created_directories:
            return False
        # Auch Parent-Verzeichnisse als erstellt markieren
        return True

    def mark_directory_created(self, dir_path: str) -> None:
        """Markiert ein Verzeichnis als erstellt."""
        path = Path(dir_path).resolve()
        # Markiere auch alle Parent-Verzeichnisse
        for parent in [path] + list(path.parents):
            self.created_directories.add(str(parent))

    def add_pending_file(self, file_path: str, content: str) -> None:
        """Fügt eine Datei zur Warteschlange hinzu."""
        self.pending_files[file_path] = content

    def mark_file_created(self, file_path: str) -> None:
        """Markiert eine Datei als erstellt."""
        normalized = str(Path(file_path).resolve())
        self.created_files.add(normalized)
        # Auch das Verzeichnis als erstellt markieren
        self.mark_directory_created(str(Path(file_path).parent))

    def get_files_to_create(self) -> Dict[str, str]:
        """Gibt alle noch zu erstellenden Dateien zurück."""
        return {
            path: content
            for path, content in self.pending_files.items()
            if str(Path(path).resolve()) not in self.created_files
        }

    def get_directories_to_create(self) -> List[str]:
        """Gibt alle noch zu erstellenden Verzeichnisse zurück (sortiert nach Tiefe)."""
        dirs_needed = set()
        for file_path in self.pending_files.keys():
            parent = Path(file_path).parent
            if self.needs_directory(str(parent)):
                dirs_needed.add(str(parent))

        # Sortieren nach Tiefe (kürzeste zuerst = Parent-Verzeichnisse zuerst)
        return sorted(dirs_needed, key=lambda p: len(Path(p).parts))


class ImplementCapability(BaseCapability):
    """
    Code implementation capability.

    Flow:
    1. Understand implementation requirements
    2. Analyze existing codebase and patterns
    3. Generate implementation plan
    4. Create code artifacts (mit Workflow-Tracking)
    5. Validate and document
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Workflow-Tracker für Multi-File-Operationen
        self._workflow_trackers: Dict[str, FileWorkflowTracker] = {}

    def _get_tracker(self, session_id: str) -> FileWorkflowTracker:
        """Gibt den Workflow-Tracker für eine Session zurück."""
        if session_id not in self._workflow_trackers:
            self._workflow_trackers[session_id] = FileWorkflowTracker()
        return self._workflow_trackers[session_id]

    @property
    def name(self) -> str:
        return "implement"

    @property
    def description(self) -> str:
        return (
            "Code-Implementierung basierend auf Design oder Requirements. "
            "Generiert produktionsreifen Code mit Best Practices. "
            "Verwende für: Feature-Implementation, Code-Generierung, Refactoring."
        )

    @property
    def handoff_targets(self) -> List[str]:
        return ["analyze"]

    def _get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Was soll implementiert werden?"
                },
                "context": {
                    "type": "string",
                    "description": "Optional: Design-Dokument oder Kontext"
                },
                "implementation_type": {
                    "type": "string",
                    "enum": ["feature", "component", "api", "service", "refactor", "auto"],
                    "description": "Art der Implementierung (default: auto)"
                },
                "language": {
                    "type": "string",
                    "description": "Programmiersprache (default: python)"
                },
                "framework": {
                    "type": "string",
                    "description": "Optional: Framework (z.B. fastapi, react)"
                },
                "with_tests": {
                    "type": "boolean",
                    "description": "Tests generieren? (default: true)"
                }
            },
            "required": ["query"]
        }

    async def _phase_explore(self, session: CapabilitySession) -> None:
        """Explore implementation requirements and codebase."""
        impl_type = session.metadata.get("implementation_type", "auto")
        language = session.metadata.get("language", "python")
        framework = session.metadata.get("framework", "")

        # Check for handoff artifacts
        handoff_artifacts = session.metadata.get("handoff_artifacts", [])
        design_context = ""
        for artifact in handoff_artifacts:
            if artifact.get("artifact_type") in ("design", "code_skeleton"):
                design_context += f"\n{artifact.get('content', '')}"

        exploration_prompt = f"""
Du bist ein Senior Software Engineer. Analysiere die Implementierungsanforderungen.

IMPLEMENTIERUNG:
{session.query}

DESIGN-KONTEXT:
{design_context or session.context or "Kein Design-Dokument"}

SPRACHE: {language}
FRAMEWORK: {framework or "Standard"}
TYP: {impl_type}

Analysiere:
1. IMPLEMENTATION SCOPE: Was genau muss implementiert werden?
2. DATEI-STRUKTUR: Welche Dateien müssen erstellt/geändert werden?
3. ABHÄNGIGKEITEN: Benötigte Imports, Packages, Services
4. PATTERNS: Zu verwendende Design Patterns
5. EDGE CASES: Zu beachtende Sonderfälle
6. TESTING: Test-Strategie
"""

        if self.llm_callback:
            response = await self._call_llm(exploration_prompt)
        else:
            response = self._generate_default_exploration(session.query, impl_type, language)

        # Detect implementation type if auto
        actual_type = impl_type
        if impl_type == "auto":
            actual_type = self._detect_impl_type(session.query, response)
            session.metadata["detected_impl_type"] = actual_type

        session.add_step(
            phase=CapabilityPhase.EXPLORE,
            title="Implementation Analysis",
            content=response,
            insights=[f"Implementation type: {actual_type}", f"Language: {language}"]
        )

    async def _phase_analyze(self, session: CapabilitySession) -> None:
        """Create detailed implementation plan."""
        impl_type = session.metadata.get("detected_impl_type",
                                         session.metadata.get("implementation_type", "feature"))
        language = session.metadata.get("language", "python")

        explore_step = next(
            (s for s in session.steps if s.phase == CapabilityPhase.EXPLORE),
            None
        )

        plan_prompt = f"""
Erstelle einen detaillierten Implementierungsplan.

EXPLORATION:
{explore_step.content if explore_step else ""}

SPRACHE: {language}
TYP: {impl_type}

Erstelle einen Plan mit:

## Implementierungsplan

### 1. Dateien zu erstellen
| Datei | Zweck | Priorität |
|-------|-------|-----------|
| ... | ... | 1 |

### 2. Dateien zu ändern
| Datei | Änderungen |
|-------|------------|
| ... | ... |

### 3. Implementierungsreihenfolge
1. [Schritt 1]
2. [Schritt 2]
...

### 4. Code-Struktur
```{language}
# Hauptstruktur/Klassen
```

### 5. Tests
- Unit Tests: [Beschreibung]
- Integration Tests: [Falls nötig]
"""

        if self.llm_callback:
            response = await self._call_llm(plan_prompt)
        else:
            response = self._generate_default_plan(session.query, impl_type, language)

        session.add_step(
            phase=CapabilityPhase.ANALYZE,
            title="Implementation Plan",
            content=response
        )

    async def _phase_synthesize(self, session: CapabilitySession) -> None:
        """Generate the actual implementation code."""
        impl_type = session.metadata.get("detected_impl_type",
                                         session.metadata.get("implementation_type", "feature"))
        language = session.metadata.get("language", "python")
        framework = session.metadata.get("framework", "")
        with_tests = session.metadata.get("with_tests", True)

        # Workflow-Tracker für diese Session
        tracker = self._get_tracker(session.session_id)

        # Get implementation plan
        plan_step = next(
            (s for s in session.steps if s.phase == CapabilityPhase.ANALYZE),
            None
        )

        code_prompt = f"""
Generiere den vollständigen Implementierungscode.

PLAN:
{plan_step.content if plan_step else ""}

SPRACHE: {language}
FRAMEWORK: {framework or "Standard"}
TESTS: {"Ja" if with_tests else "Nein"}

Generiere:
1. Vollständigen, produktionsreifen Code
2. Dokumentation (Docstrings, Kommentare)
3. Type Hints (für Python)
4. Error Handling
{"5. Unit Tests" if with_tests else ""}

Format:
```{language}
# === DATEINAME: [path/to/file.py] ===
[code]
```

Für jede Datei einen eigenen Block.
"""

        if self.llm_callback:
            implementation = await self._call_llm(code_prompt)
        else:
            implementation = self._generate_default_implementation(
                session.query, impl_type, language, with_tests
            )

        session.add_step(
            phase=CapabilityPhase.SYNTHESIZE,
            title="Code Implementation",
            content=implementation
        )

        # Parse and create artifacts for each file
        files = self._parse_code_blocks(implementation)

        # Dateien zum Tracker hinzufügen (für Batch-Verarbeitung)
        for filename, code in files.items():
            tracker.add_pending_file(filename, code)

        # Verzeichnisse die erstellt werden müssen (ohne Duplikate)
        dirs_to_create = tracker.get_directories_to_create()
        if dirs_to_create:
            session.metadata["directories_to_create"] = dirs_to_create
            logger.debug(f"[Implement] Verzeichnisse zu erstellen: {dirs_to_create}")

        # Dateien die erstellt werden müssen
        files_to_create = tracker.get_files_to_create()
        session.metadata["files_to_create"] = list(files_to_create.keys())

        for filename, code in files_to_create.items():
            session.add_artifact(
                artifact_type="code",
                title=filename,
                content=code,
                metadata={
                    "language": language,
                    "framework": framework,
                    "implementation_type": impl_type
                }
            )

        # Create summary artifact
        session.add_artifact(
            artifact_type="implementation_summary",
            title=f"Implementation: {session.query[:40]}",
            content=self._create_summary(session, files),
            metadata={"files_count": len(files)}
        )

    async def _phase_validate(self, session: CapabilitySession) -> None:
        """Validate the implementation."""
        code_artifacts = session.get_artifacts_by_type("code")
        tracker = self._get_tracker(session.session_id)

        validation_items = []

        # Workflow-Info hinzufügen
        dirs_to_create = session.metadata.get("directories_to_create", [])
        files_to_create = session.metadata.get("files_to_create", [])

        if dirs_to_create:
            validation_items.append(f"**Verzeichnisse zu erstellen:** {len(dirs_to_create)}")
            for d in dirs_to_create:
                validation_items.append(f"  📁 {d}")

        if files_to_create:
            validation_items.append(f"\n**Dateien zu erstellen:** {len(files_to_create)}")

        for artifact in code_artifacts:
            # Basic validation checks
            checks = self._validate_code(artifact.content, artifact.metadata.get("language", "python"))
            validation_items.append(f"  📄 **{artifact.title}**: {', '.join(checks)}")

        # Cleanup: Tracker für diese Session entfernen
        if session.session_id in self._workflow_trackers:
            del self._workflow_trackers[session.session_id]

        insights = []
        if code_artifacts:
            insights.append(f"{len(code_artifacts)} Dateien validiert")
        if dirs_to_create:
            insights.append(f"{len(dirs_to_create)} Verzeichnisse geplant")

        session.add_step(
            phase=CapabilityPhase.VALIDATE,
            title="Implementation Validation",
            content="\n".join(validation_items) if validation_items else "No code artifacts to validate",
            insights=insights
        )

    def _detect_impl_type(self, query: str, analysis: str) -> str:
        """Detect implementation type from query and analysis."""
        query_lower = query.lower()

        if any(kw in query_lower for kw in ["api", "endpoint", "route"]):
            return "api"
        if any(kw in query_lower for kw in ["component", "widget", "ui"]):
            return "component"
        if any(kw in query_lower for kw in ["service", "manager", "handler"]):
            return "service"
        if any(kw in query_lower for kw in ["refactor", "optimize", "improve"]):
            return "refactor"

        return "feature"

    def _parse_code_blocks(self, implementation: str) -> Dict[str, str]:
        """Parse code blocks from implementation output."""
        files = {}
        current_file = None
        current_code = []

        lines = implementation.split("\n")
        for line in lines:
            # Check for file marker
            if "=== DATEINAME:" in line or "=== FILE:" in line:
                # Save previous file
                if current_file and current_code:
                    files[current_file] = "\n".join(current_code)
                    current_code = []

                # Extract filename
                parts = line.split(":", 1)
                if len(parts) > 1:
                    filename = parts[1].strip().rstrip("=").strip()
                    current_file = filename

            elif current_file:
                # Skip code block markers
                if line.strip().startswith("```"):
                    continue
                current_code.append(line)

        # Save last file
        if current_file and current_code:
            files[current_file] = "\n".join(current_code)

        # If no files found, create a single artifact
        if not files:
            files["implementation.py"] = implementation

        return files

    def _validate_code(self, code: str, language: str) -> List[str]:
        """Basic code validation checks."""
        checks = []

        if language == "python":
            if "def " in code or "class " in code:
                checks.append("Structure OK")
            if '"""' in code or "'''" in code:
                checks.append("Docstrings OK")
            if ": " in code and "->" in code:
                checks.append("Type hints OK")
            if "try:" in code or "except" in code:
                checks.append("Error handling OK")

        return checks if checks else ["Basic structure OK"]

    def _create_summary(self, session: CapabilitySession, files: Dict[str, str]) -> str:
        """Create implementation summary."""
        file_list = "\n".join([f"- {f}" for f in files.keys()])

        # Verzeichnisse aus Metadaten
        dirs_to_create = session.metadata.get("directories_to_create", [])
        dirs_section = ""
        if dirs_to_create:
            dirs_list = "\n".join([f"- 📁 {d}" for d in dirs_to_create])
            dirs_section = f"""
## Directories to Create
{dirs_list}
"""

        return f"""
# Implementation Summary

## Query
{session.query}
{dirs_section}
## Files to Create/Modify
{file_list}

## Implementation Type
{session.metadata.get("detected_impl_type", session.metadata.get("implementation_type", "feature"))}

## Language
{session.metadata.get("language", "python")}

## Next Steps
1. Review generated code
2. Create directories (if needed)
3. Write files
4. Run tests with /analyze
"""

    def _generate_default_exploration(self, query: str, impl_type: str, language: str) -> str:
        return f"""
## Implementation Analysis: {query}

### 1. IMPLEMENTATION SCOPE
- Hauptaufgabe: {query}
- Typ: {impl_type}

### 2. DATEI-STRUKTUR
- [Zu analysierende Dateien]

### 3. ABHÄNGIGKEITEN
- Standard-Library
- Projekt-interne Module

### 4. PATTERNS
- [Passende Patterns]

### 5. EDGE CASES
- Input-Validierung
- Error Handling

### 6. TESTING
- Unit Tests für Hauptfunktionalität
"""

    def _generate_default_plan(self, query: str, impl_type: str, language: str) -> str:
        return f"""
## Implementierungsplan: {query}

### 1. Dateien zu erstellen
| Datei | Zweck | Priorität |
|-------|-------|-----------|
| {impl_type}.py | Hauptimplementierung | 1 |
| test_{impl_type}.py | Tests | 2 |

### 2. Dateien zu ändern
| Datei | Änderungen |
|-------|------------|
| __init__.py | Export hinzufügen |

### 3. Implementierungsreihenfolge
1. Basis-Struktur erstellen
2. Hauptlogik implementieren
3. Tests schreiben

### 4. Code-Struktur
```{language}
class {impl_type.title()}:
    def __init__(self):
        pass

    def execute(self):
        pass
```

### 5. Tests
- Unit Tests für alle öffentlichen Methoden
"""

    def _generate_default_implementation(
        self,
        query: str,
        impl_type: str,
        language: str,
        with_tests: bool
    ) -> str:
        impl = f"""
# === DATEINAME: {impl_type}.py ===
\"\"\"
{query}

Auto-generated implementation.
\"\"\"

from typing import Any, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class {impl_type.title().replace('_', '')}:
    \"\"\"
    Implementation for: {query}
    \"\"\"

    def __init__(self):
        \"\"\"Initialize the {impl_type}.\"\"\"
        self._initialized = True

    def execute(self, **kwargs) -> Dict[str, Any]:
        \"\"\"
        Execute the main functionality.

        Args:
            **kwargs: Implementation-specific arguments

        Returns:
            Result dictionary
        \"\"\"
        logger.info(f"Executing {impl_type}")

        try:
            # TODO: Implement actual logic
            result = self._process(**kwargs)
            return {{"success": True, "result": result}}
        except Exception as e:
            logger.error(f"Error in {impl_type}: {{e}}")
            return {{"success": False, "error": str(e)}}

    def _process(self, **kwargs) -> Any:
        \"\"\"Internal processing logic.\"\"\"
        # TODO: Implement
        return None
"""

        if with_tests:
            impl += f"""

# === DATEINAME: test_{impl_type}.py ===
\"\"\"
Tests for {impl_type}.
\"\"\"

import pytest
from {impl_type} import {impl_type.title().replace('_', '')}


class Test{impl_type.title().replace('_', '')}:
    \"\"\"Test suite for {impl_type}.\"\"\"

    def setup_method(self):
        \"\"\"Set up test fixtures.\"\"\"
        self.instance = {impl_type.title().replace('_', '')}()

    def test_initialization(self):
        \"\"\"Test proper initialization.\"\"\"
        assert self.instance._initialized is True

    def test_execute_success(self):
        \"\"\"Test successful execution.\"\"\"
        result = self.instance.execute()
        assert "success" in result

    def test_execute_with_params(self):
        \"\"\"Test execution with parameters.\"\"\"
        result = self.instance.execute(param="value")
        assert result is not None
"""

        return impl
