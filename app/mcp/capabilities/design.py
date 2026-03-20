"""
Design Capability - System and component design.

Creates architecture, API specifications, and component designs
based on requirements.
"""

import logging
from typing import Any, Dict, List, Optional

from app.mcp.capabilities.base import (
    BaseCapability,
    CapabilityPhase,
    CapabilitySession,
    CapabilityArtifact
)

logger = logging.getLogger(__name__)


class DesignCapability(BaseCapability):
    """
    System and component design capability.

    Flow:
    1. Understand requirements (from brainstorm or direct input)
    2. Analyze design constraints and patterns
    3. Create architecture/API/component design
    4. Validate design against requirements
    5. Generate design documentation
    """

    @property
    def name(self) -> str:
        return "design"

    @property
    def description(self) -> str:
        return (
            "SOFTWARE-ARCHITEKTUR: System- und Komponentendesign für Code-Projekte. "
            "Erstellt Architektur, API-Spezifikationen und Komponenten-Designs. "
            "NUR für: Architekturplanung, API-Design, Datenbank-Schemas, Code-Strukturen. "
            "NICHT für: Texte schreiben, Dokumentation erstellen, Reports, Recherche."
        )

    @property
    def handoff_targets(self) -> List[str]:
        return ["implement"]

    def _get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Was soll designed werden?"
                },
                "context": {
                    "type": "string",
                    "description": "Optional: Requirements oder Kontext vom Brainstorming"
                },
                "design_type": {
                    "type": "string",
                    "enum": ["architecture", "api", "component", "database", "auto"],
                    "description": "Art des Designs (default: auto)"
                },
                "output_format": {
                    "type": "string",
                    "enum": ["markdown", "diagram", "code", "all"],
                    "description": "Output-Format (default: markdown)"
                },
                "constraints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: Design-Constraints"
                }
            },
            "required": ["query"]
        }

    async def _phase_explore(self, session: CapabilitySession) -> None:
        """Explore design requirements and constraints."""
        design_type = session.metadata.get("design_type", "auto")
        constraints = session.metadata.get("constraints", [])

        # Check for handoff artifacts
        handoff_artifacts = session.metadata.get("handoff_artifacts", [])
        requirements_context = ""
        if handoff_artifacts:
            for artifact in handoff_artifacts:
                if artifact.get("artifact_type") == "requirements":
                    requirements_context = artifact.get("content", "")
                    break

        exploration_prompt = f"""
Du bist ein Software-Architekt. Analysiere die Design-Anforderungen.

DESIGN-ANFRAGE:
{session.query}

REQUIREMENTS-KONTEXT:
{requirements_context or session.context or "Keine spezifischen Requirements"}

DESIGN-TYP: {design_type}
CONSTRAINTS: {', '.join(constraints) if constraints else "Keine spezifischen"}

Analysiere:
1. DESIGN-SCOPE: Was muss designed werden?
2. ABHÄNGIGKEITEN: Welche Systeme/Komponenten sind betroffen?
3. TECHNOLOGIE-STACK: Empfohlene Technologien
4. PATTERNS: Relevante Design Patterns
5. RISIKEN: Potenzielle Design-Risiken

Fokussiere auf {design_type if design_type != "auto" else "den passendsten Design-Typ"}.
"""

        if self.llm_callback:
            response = await self._call_llm(exploration_prompt)
        else:
            response = self._generate_default_exploration(session.query, design_type)

        # Determine actual design type if auto
        actual_type = design_type
        if design_type == "auto":
            actual_type = self._detect_design_type(session.query, response)
            session.metadata["detected_design_type"] = actual_type

        session.add_step(
            phase=CapabilityPhase.EXPLORE,
            title="Design Requirements Analysis",
            content=response,
            insights=[f"Detected design type: {actual_type}"]
        )

    async def _phase_analyze(self, session: CapabilitySession) -> None:
        """Analyze design options and patterns."""
        design_type = session.metadata.get("detected_design_type",
                                           session.metadata.get("design_type", "architecture"))

        explore_step = next(
            (s for s in session.steps if s.phase == CapabilityPhase.EXPLORE),
            None
        )

        analysis_prompt = f"""
Basierend auf der Analyse, entwickle konkrete Design-Optionen.

EXPLORATION:
{explore_step.content if explore_step else ""}

DESIGN-TYP: {design_type}

Für {"Architecture" if design_type == "architecture" else
      "API" if design_type == "api" else
      "Component" if design_type == "component" else
      "Database" if design_type == "database" else "System"} Design:

Erstelle:
1. DESIGN-OPTIONEN: 2-3 mögliche Ansätze mit Vor-/Nachteilen
2. EMPFEHLUNG: Bevorzugter Ansatz mit Begründung
3. KOMPONENTEN: Hauptkomponenten und ihre Verantwortlichkeiten
4. SCHNITTSTELLEN: Wichtige Interfaces/APIs
5. DATENFLUSS: Wie fließen Daten durch das System?
"""

        if self.llm_callback:
            response = await self._call_llm(analysis_prompt)
        else:
            response = self._generate_default_analysis(session.query, design_type)

        session.add_step(
            phase=CapabilityPhase.ANALYZE,
            title="Design Options Analysis",
            content=response
        )

    async def _phase_synthesize(self, session: CapabilitySession) -> None:
        """Create the actual design artifacts."""
        design_type = session.metadata.get("detected_design_type",
                                           session.metadata.get("design_type", "architecture"))
        output_format = session.metadata.get("output_format", "markdown")

        steps_content = "\n\n".join([
            f"### {s.title}\n{s.content}"
            for s in session.steps
        ])

        synthesis_prompt = f"""
Erstelle das finale Design-Dokument.

ANALYSE:
{steps_content}

DESIGN-TYP: {design_type}
FORMAT: {output_format}

Erstelle ein vollständiges Design-Dokument:

# {design_type.title()} Design: [Name]

## 1. Overview
Kurze Beschreibung des Designs

## 2. Design Decisions
Wichtige Entscheidungen und Begründungen

## 3. {"Architecture" if design_type == "architecture" else
       "API Specification" if design_type == "api" else
       "Component Structure" if design_type == "component" else
       "Schema" if design_type == "database" else "Design"}

{self._get_design_template(design_type)}

## 4. Interfaces
Schnittstellen-Definitionen

## 5. Data Flow
Datenfluss-Beschreibung

## 6. Implementation Notes
Hinweise für die Implementierung

## 7. Next Steps
→ Implementierung mit /implement
"""

        if self.llm_callback:
            design_doc = await self._call_llm(synthesis_prompt)
        else:
            design_doc = self._generate_default_design(session.query, design_type)

        session.add_step(
            phase=CapabilityPhase.SYNTHESIZE,
            title=f"{design_type.title()} Design",
            content=design_doc
        )

        # Create design artifact
        session.add_artifact(
            artifact_type="design",
            title=f"{design_type.title()} Design: {session.query[:40]}",
            content=design_doc,
            metadata={
                "design_type": design_type,
                "output_format": output_format,
                "version": "1.0"
            }
        )

        # Generate code skeleton if needed
        if output_format in ("code", "all"):
            await self._generate_code_skeleton(session, design_type)

    async def _generate_code_skeleton(
        self,
        session: CapabilitySession,
        design_type: str
    ) -> None:
        """Generate code skeleton from design."""
        design_artifact = next(
            (a for a in session.artifacts if a.artifact_type == "design"),
            None
        )

        if not design_artifact:
            return

        code_prompt = f"""
Basierend auf dem Design, erstelle ein Code-Skeleton.

DESIGN:
{design_artifact.content}

Erstelle:
1. Datei-Struktur
2. Klassen/Interfaces mit Signaturen (ohne Implementation)
3. Wichtige Typen/Enums

Format als Python-Code mit Kommentaren.
"""

        if self.llm_callback:
            code_skeleton = await self._call_llm(code_prompt)
        else:
            code_skeleton = self._generate_default_skeleton(design_type)

        session.add_artifact(
            artifact_type="code_skeleton",
            title=f"Code Skeleton: {session.query[:30]}",
            content=code_skeleton,
            metadata={"language": "python"}
        )

    def _detect_design_type(self, query: str, analysis: str) -> str:
        """Detect the most appropriate design type."""
        query_lower = query.lower()
        analysis_lower = analysis.lower()

        if any(kw in query_lower for kw in ["api", "endpoint", "rest", "graphql"]):
            return "api"
        if any(kw in query_lower for kw in ["database", "schema", "table", "entity"]):
            return "database"
        if any(kw in query_lower for kw in ["component", "widget", "ui", "frontend"]):
            return "component"
        if any(kw in query_lower for kw in ["architecture", "system", "service"]):
            return "architecture"

        # Fallback to architecture
        return "architecture"

    def _get_design_template(self, design_type: str) -> str:
        """Get design template based on type."""
        templates = {
            "architecture": """
### System Components
```
┌─────────────┐     ┌─────────────┐
│ Component A │────▶│ Component B │
└─────────────┘     └─────────────┘
        │
        ▼
┌─────────────┐
│ Component C │
└─────────────┘
```

### Component Responsibilities
- Component A: [Verantwortlichkeit]
- Component B: [Verantwortlichkeit]
""",
            "api": """
### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET    | /api/v1/resource | List resources |
| POST   | /api/v1/resource | Create resource |
| GET    | /api/v1/resource/:id | Get resource |
| PUT    | /api/v1/resource/:id | Update resource |
| DELETE | /api/v1/resource/:id | Delete resource |

### Request/Response Models
```json
{
  "id": "string",
  "name": "string",
  "created_at": "datetime"
}
```
""",
            "component": """
### Component Hierarchy
```
RootComponent
├── HeaderComponent
├── MainComponent
│   ├── SidebarComponent
│   └── ContentComponent
└── FooterComponent
```

### Props/State
- Props: [Input-Props]
- State: [Interner State]
- Events: [Emittierte Events]
""",
            "database": """
### Entity Relationship Diagram
```
┌──────────────┐       ┌──────────────┐
│    User      │──────▶│    Order     │
├──────────────┤       ├──────────────┤
│ id (PK)      │       │ id (PK)      │
│ name         │       │ user_id (FK) │
│ email        │       │ total        │
└──────────────┘       └──────────────┘
```

### Tables
- users: Benutzerdaten
- orders: Bestellungen
"""
        }
        return templates.get(design_type, templates["architecture"])

    def _generate_default_exploration(self, query: str, design_type: str) -> str:
        return f"""
## Design Requirements Analysis: {query}

### 1. DESIGN-SCOPE
- Primäres Ziel: {query}
- Design-Typ: {design_type}

### 2. ABHÄNGIGKEITEN
- [Zu identifizieren]

### 3. TECHNOLOGIE-STACK
- Backend: Python/FastAPI (basierend auf Projekt)
- Frontend: [Falls relevant]
- Datenbank: [Falls relevant]

### 4. PATTERNS
- [Zu empfehlen basierend auf Analyse]

### 5. RISIKEN
- [Zu identifizieren]
"""

    def _generate_default_analysis(self, query: str, design_type: str) -> str:
        return f"""
## Design Options: {query}

### Option 1: Standard Approach
- Beschreibung: Klassischer Ansatz
- Vorteile: Bewährt, gut dokumentiert
- Nachteile: Möglicherweise nicht optimal

### Option 2: Modern Approach
- Beschreibung: Moderner Ansatz
- Vorteile: Skalierbar, wartbar
- Nachteile: Mehr initialer Aufwand

### Empfehlung
Option 2 - Modern Approach

### Komponenten
- [Hauptkomponenten basierend auf Design-Typ]

### Schnittstellen
- [Zu definieren]
"""

    def _generate_default_design(self, query: str, design_type: str) -> str:
        template = self._get_design_template(design_type)
        return f"""
# {design_type.title()} Design: {query}

## 1. Overview
Design für "{query}" basierend auf {design_type} Ansatz.

## 2. Design Decisions
- Decision 1: [Begründung]
- Decision 2: [Begründung]

## 3. {design_type.title()}
{template}

## 4. Interfaces
[Interface-Definitionen]

## 5. Data Flow
[Datenfluss-Beschreibung]

## 6. Implementation Notes
- [Hinweis 1]
- [Hinweis 2]

## 7. Next Steps
1. Review des Designs
2. Implementierung starten mit /implement
"""

    def _generate_default_skeleton(self, design_type: str) -> str:
        return """
# Code Skeleton

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

# --- Models ---

@dataclass
class BaseModel:
    id: str
    created_at: str
    updated_at: str

# --- Interfaces ---

class IRepository(ABC):
    @abstractmethod
    async def get(self, id: str) -> Optional[BaseModel]:
        pass

    @abstractmethod
    async def create(self, data: Dict[str, Any]) -> BaseModel:
        pass

# --- Services ---

class BaseService:
    def __init__(self, repository: IRepository):
        self.repository = repository

# TODO: Implement concrete classes
```
"""
