"""
Output API Routes - Formatierung und Diagramm-Generierung für MCP-Commands.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.output_formatter import (
    get_output_formatter,
    DiagramGenerator,
    DiagramType,
    DiagramFormat,
    format_brainstorm_output,
    format_design_output,
)
from app.models.skill import OutputConfig, OutputTemplate, DiagramConfig
from app.services.skill_manager import get_skill_manager


router = APIRouter(prefix="/api/output", tags=["output"])


# ══════════════════════════════════════════════════════════════════════════════
# Request/Response Models
# ══════════════════════════════════════════════════════════════════════════════

class DiagramRequest(BaseModel):
    """Request für Diagramm-Generierung."""
    type: str = Field(..., description="Diagramm-Typ: sequence, class, component, erd, usecase, context, flowchart")
    format: str = Field("mermaid", description="Format: mermaid, plantuml, ascii")
    title: str = Field(..., description="Titel des Diagramms")
    placeholders: Optional[Dict[str, str]] = Field(None, description="Platzhalter-Werte")
    custom_template: Optional[str] = Field(None, description="Custom Template")


class DiagramResponse(BaseModel):
    """Response für Diagramm-Generierung."""
    type: str
    format: str
    title: str
    content: str
    markdown: str


class FormatRequest(BaseModel):
    """Request für Ausgabe-Formatierung."""
    command: str = Field(..., description="MCP-Command Name")
    title: str = Field(..., description="Titel der Ausgabe")
    raw_content: str = Field(..., description="Roher Inhalt")
    sources: Optional[List[str]] = Field(None, description="Verwendete Quellen")
    diagram_data: Optional[Dict[str, Dict[str, str]]] = Field(
        None,
        description="Daten für Diagramme: {diagram_type: {placeholder: value}}"
    )


class FormattedSectionResponse(BaseModel):
    """Eine formatierte Sektion."""
    name: str
    content: str
    is_required: bool
    is_present: bool


class FormattedOutputResponse(BaseModel):
    """Vollständige formatierte Ausgabe."""
    command: str
    title: str
    sections: List[FormattedSectionResponse]
    diagrams: List[DiagramResponse]
    sources: List[str]
    markdown: str
    is_valid: bool
    validation_errors: List[str]


class PromptInstructionsRequest(BaseModel):
    """Request für Prompt-Instruktionen."""
    command: str = Field(..., description="MCP-Command Name")


class PromptInstructionsResponse(BaseModel):
    """Response mit Prompt-Instruktionen."""
    command: str
    instructions: str
    templates: List[str]
    diagrams: List[str]


class BrainstormFormatRequest(BaseModel):
    """Request für Brainstorm-Formatierung."""
    title: str
    use_cases: List[Dict[str, Any]]
    stakeholders: List[Dict[str, str]]
    risks: List[str]
    assumptions: List[str]
    open_questions: List[str]
    sources: List[str] = []
    diagram_data: Optional[Dict[str, str]] = None


class DesignFormatRequest(BaseModel):
    """Request für Design-Formatierung."""
    title: str
    overview: str
    components: List[Dict[str, str]]
    decisions: List[Dict[str, str]]
    sources: List[str] = []
    sequence_data: Optional[Dict[str, str]] = None
    component_data: Optional[Dict[str, str]] = None
    erd_data: Optional[Dict[str, str]] = None


class MarkdownResponse(BaseModel):
    """Response mit Markdown-Ausgabe."""
    markdown: str


# ══════════════════════════════════════════════════════════════════════════════
# Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/diagram", response_model=DiagramResponse)
async def generate_diagram(request: DiagramRequest):
    """
    Generiert ein Diagramm in verschiedenen Formaten.

    Unterstützte Typen:
    - sequence: Sequenzdiagramm
    - class: Klassendiagramm
    - component: Komponenten-Diagramm
    - erd: Entity-Relationship-Diagramm
    - usecase: Use-Case-Diagramm
    - context: Kontext-Diagramm
    - flowchart: Ablaufdiagramm

    Unterstützte Formate:
    - mermaid: Mermaid-Syntax
    - plantuml: PlantUML-Syntax
    - ascii: ASCII-Art
    """
    try:
        diagram_type = DiagramType(request.type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Ungültiger Diagramm-Typ: {request.type}. "
                   f"Erlaubt: {[t.value for t in DiagramType]}"
        )

    try:
        diagram_format = DiagramFormat(request.format)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Ungültiges Format: {request.format}. "
                   f"Erlaubt: {[f.value for f in DiagramFormat]}"
        )

    generator = DiagramGenerator()
    diagram = generator.generate(
        diagram_type=diagram_type,
        diagram_format=diagram_format,
        title=request.title,
        placeholders=request.placeholders,
        custom_template=request.custom_template
    )

    return DiagramResponse(
        type=diagram.type.value,
        format=diagram.format.value,
        title=diagram.title,
        content=diagram.content,
        markdown=diagram.to_markdown()
    )


@router.post("/format", response_model=FormattedOutputResponse)
async def format_output(request: FormatRequest):
    """
    Formatiert eine Ausgabe basierend auf der Skill-Konfiguration.

    Verwendet die OutputConfig des Command-Skills für:
    - Sektions-Extraktion basierend auf Templates
    - Diagramm-Generierung
    - Quellen-Attribution
    """
    formatter = get_output_formatter()
    skill_manager = get_skill_manager()

    # Config aus Skill laden
    config = None
    try:
        skills_config = skill_manager.get_command_skills_config(request.command)
        if skills_config and skills_config.get("output"):
            output_cfg = skills_config["output"]
            templates = [OutputTemplate(**t) for t in output_cfg.get("templates", [])]
            diagrams = [DiagramConfig(**d) for d in output_cfg.get("diagrams", [])]
            config = OutputConfig(
                templates=templates,
                diagrams=diagrams,
                include_sources=output_cfg.get("include_sources", True),
                enterprise_formatting=output_cfg.get("enterprise_formatting", True)
            )
    except Exception:
        pass  # Fallback auf None

    output = formatter.format_output(
        command=request.command,
        title=request.title,
        raw_content=request.raw_content,
        config=config,
        sources=request.sources,
        diagram_data=request.diagram_data
    )

    is_valid, errors = formatter.validate_output(output)

    return FormattedOutputResponse(
        command=output.command,
        title=output.title,
        sections=[
            FormattedSectionResponse(
                name=s.name,
                content=s.content,
                is_required=s.is_required,
                is_present=s.is_present
            )
            for s in output.sections
        ],
        diagrams=[
            DiagramResponse(
                type=d.type.value,
                format=d.format.value,
                title=d.title,
                content=d.content,
                markdown=d.to_markdown()
            )
            for d in output.diagrams
        ],
        sources=output.sources,
        markdown=output.to_markdown(),
        is_valid=is_valid,
        validation_errors=errors
    )


@router.post("/prompt-instructions", response_model=PromptInstructionsResponse)
async def get_prompt_instructions(request: PromptInstructionsRequest):
    """
    Generiert Prompt-Instruktionen für strukturierte Ausgabe.

    Diese Instruktionen werden dem LLM mitgegeben, um die
    gewünschte Ausgabe-Struktur zu erzwingen.
    """
    formatter = get_output_formatter()
    skill_manager = get_skill_manager()

    # Config aus Skill laden
    config = None
    template_names = []
    diagram_types = []

    try:
        skills_config = skill_manager.get_command_skills_config(request.command)
        if skills_config and skills_config.get("output"):
            output_cfg = skills_config["output"]
            templates = [OutputTemplate(**t) for t in output_cfg.get("templates", [])]
            diagrams = [DiagramConfig(**d) for d in output_cfg.get("diagrams", [])]
            config = OutputConfig(
                templates=templates,
                diagrams=diagrams,
                include_sources=output_cfg.get("include_sources", True)
            )
            template_names = [t.name for t in templates]
            diagram_types = [d.type for d in diagrams]
    except Exception:
        pass

    instructions = formatter.get_prompt_instructions(request.command, config)

    return PromptInstructionsResponse(
        command=request.command,
        instructions=instructions,
        templates=template_names,
        diagrams=diagram_types
    )


@router.post("/brainstorm", response_model=MarkdownResponse)
async def format_brainstorm(request: BrainstormFormatRequest):
    """
    Formatiert einen Brainstorm-Output im Enterprise-Format.

    Erzeugt strukturiertes Markdown mit:
    - Use Cases mit Ablaufbeschreibungen
    - Stakeholder-Mapping als Tabelle
    - Risiken und Annahmen
    - Offene Fragen als Checkliste
    - Optional: Kontext-Diagramm
    """
    markdown = format_brainstorm_output(
        title=request.title,
        use_cases=request.use_cases,
        stakeholders=request.stakeholders,
        risks=request.risks,
        assumptions=request.assumptions,
        open_questions=request.open_questions,
        sources=request.sources,
        diagram_data=request.diagram_data
    )

    return MarkdownResponse(markdown=markdown)


@router.post("/design", response_model=MarkdownResponse)
async def format_design(request: DesignFormatRequest):
    """
    Formatiert einen Design-Output im Enterprise-Format.

    Erzeugt strukturiertes Markdown mit:
    - Design Overview
    - Komponenten-Beschreibungen
    - Sequenzdiagramm (Mermaid)
    - Komponenten-Diagramm (ASCII)
    - ERD (ASCII)
    - Entscheidungsprotokoll als Tabelle
    """
    markdown = format_design_output(
        title=request.title,
        overview=request.overview,
        components=request.components,
        decisions=request.decisions,
        sources=request.sources,
        sequence_data=request.sequence_data,
        component_data=request.component_data,
        erd_data=request.erd_data
    )

    return MarkdownResponse(markdown=markdown)


@router.get("/diagram-types")
async def list_diagram_types():
    """
    Listet verfügbare Diagramm-Typen und Formate auf.
    """
    return {
        "types": [
            {
                "id": t.value,
                "name": t.value.title(),
                "description": _get_diagram_description(t)
            }
            for t in DiagramType
        ],
        "formats": [
            {
                "id": f.value,
                "name": f.value.title(),
                "description": _get_format_description(f)
            }
            for f in DiagramFormat
        ]
    }


def _get_diagram_description(diagram_type: DiagramType) -> str:
    """Gibt eine Beschreibung für den Diagramm-Typ zurück."""
    descriptions = {
        DiagramType.SEQUENCE: "Zeigt Interaktionen zwischen Komponenten über Zeit",
        DiagramType.CLASS: "UML-Klassendiagramm mit Attributen und Methoden",
        DiagramType.COMPONENT: "Architektur-Übersicht mit Schichten und Komponenten",
        DiagramType.ERD: "Entity-Relationship-Diagramm für Datenmodelle",
        DiagramType.USECASE: "Use-Case-Diagramm mit Akteuren und Funktionen",
        DiagramType.CONTEXT: "Kontext-Diagramm mit externen Systemen",
        DiagramType.FLOWCHART: "Ablaufdiagramm für Prozesse",
    }
    return descriptions.get(diagram_type, "")


def _get_format_description(diagram_format: DiagramFormat) -> str:
    """Gibt eine Beschreibung für das Format zurück."""
    descriptions = {
        DiagramFormat.MERMAID: "Mermaid-Syntax für Web-Rendering",
        DiagramFormat.PLANTUML: "PlantUML-Syntax für Java-basiertes Rendering",
        DiagramFormat.ASCII: "ASCII-Art für Terminal und Text-Ausgabe",
    }
    return descriptions.get(diagram_format, "")
