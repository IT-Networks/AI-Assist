"""
Output Formatter - Strukturierte Ausgabe für MCP-Commands.

Features:
- Template-basierte Ausgabe-Strukturierung
- Diagramm-Generierung (Mermaid, PlantUML, ASCII)
- Quellen-Attribution
- Enterprise-Formatierung
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from app.models.skill import OutputConfig, OutputTemplate, DiagramConfig


# ══════════════════════════════════════════════════════════════════════════════
# Enums und Datenklassen
# ══════════════════════════════════════════════════════════════════════════════

class DiagramType(str, Enum):
    """Verfügbare Diagramm-Typen."""
    SEQUENCE = "sequence"
    CLASS = "class"
    COMPONENT = "component"
    ERD = "erd"
    USECASE = "usecase"
    CONTEXT = "context"
    FLOWCHART = "flowchart"


class DiagramFormat(str, Enum):
    """Ausgabeformate für Diagramme."""
    MERMAID = "mermaid"
    PLANTUML = "plantuml"
    ASCII = "ascii"


@dataclass
class FormattedSection:
    """Eine formatierte Ausgabe-Sektion."""
    name: str
    content: str
    is_required: bool = True
    is_present: bool = False


@dataclass
class GeneratedDiagram:
    """Ein generiertes Diagramm."""
    type: DiagramType
    format: DiagramFormat
    title: str
    content: str

    def to_markdown(self) -> str:
        """Formatiert das Diagramm als Markdown."""
        if self.format == DiagramFormat.MERMAID:
            return f"### {self.title}\n\n```mermaid\n{self.content}\n```"
        elif self.format == DiagramFormat.PLANTUML:
            return f"### {self.title}\n\n```plantuml\n{self.content}\n```"
        else:  # ASCII
            return f"### {self.title}\n\n```\n{self.content}\n```"


@dataclass
class FormattedOutput:
    """Vollständige formatierte Ausgabe."""
    command: str
    title: str
    sections: List[FormattedSection]
    diagrams: List[GeneratedDiagram]
    sources: List[str]
    timestamp: datetime = field(default_factory=datetime.now)

    def to_markdown(self) -> str:
        """Generiert vollständiges Markdown-Dokument."""
        parts = [f"# {self.title}\n"]

        # Metadaten
        parts.append(f"*Generiert: {self.timestamp.strftime('%Y-%m-%d %H:%M')}*\n")
        parts.append("---\n")

        # Sektionen
        for section in self.sections:
            if section.is_present:
                parts.append(f"## {section.name}\n")
                parts.append(section.content)
                parts.append("\n")

        # Diagramme
        if self.diagrams:
            parts.append("## Diagramme\n")
            for diagram in self.diagrams:
                parts.append(diagram.to_markdown())
                parts.append("\n")

        # Quellen
        if self.sources:
            parts.append("---\n")
            parts.append("## Quellen\n")
            for i, source in enumerate(self.sources, 1):
                parts.append(f"{i}. {source}\n")

        return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════════════════
# Diagram Generator
# ══════════════════════════════════════════════════════════════════════════════

class DiagramGenerator:
    """Generiert Diagramme in verschiedenen Formaten."""

    # Standard-Templates für verschiedene Diagramm-Typen
    TEMPLATES = {
        DiagramType.SEQUENCE: {
            DiagramFormat.MERMAID: """sequenceDiagram
    participant {actor1} as {actor1_label}
    participant {actor2} as {actor2_label}
    participant {actor3} as {actor3_label}

    {actor1}->>{actor2}: {action1}
    {actor2}->>{actor3}: {action2}
    {actor3}-->>{actor2}: {response2}
    {actor2}-->>{actor1}: {response1}""",
            DiagramFormat.ASCII: """┌─────────┐     ┌─────────┐     ┌─────────┐
│{actor1:^9}│     │{actor2:^9}│     │{actor3:^9}│
└────┬────┘     └────┬────┘     └────┬────┘
     │               │               │
     │  {action1}    │               │
     │──────────────▶│               │
     │               │  {action2}    │
     │               │──────────────▶│
     │               │               │
     │               │◀──────────────│
     │               │  {response2}  │
     │◀──────────────│               │
     │  {response1}  │               │
     │               │               │"""
        },
        DiagramType.COMPONENT: {
            DiagramFormat.ASCII: """┌─────────────────────────────────────────────────────────────┐
│                      {layer1_name:^41}│
│   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐    │
│   │ {comp1:^12} │   │ {comp2:^12} │   │ {comp3:^12} │    │
│   └──────┬───────┘   └──────┬───────┘   └──────┬───────┘    │
└──────────┼──────────────────┼──────────────────┼────────────┘
           │                  │                  │
┌──────────┼──────────────────┼──────────────────┼────────────┐
│          ▼                  ▼                  ▼            │
│                       {layer2_name:^41}│
│   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐    │
│   │ {svc1:^12} │   │ {svc2:^12} │   │ {svc3:^12} │    │
│   └──────┬───────┘   └──────┬───────┘   └──────┬───────┘    │
└──────────┼──────────────────┼──────────────────┼────────────┘
           │                  │                  │
┌──────────┼──────────────────┼──────────────────┼────────────┐
│          ▼                  ▼                  ▼            │
│                     {layer3_name:^41}│
│   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐    │
│   │ {repo1:^12} │   │ {repo2:^12} │   │ {repo3:^12} │    │
│   └──────────────┘   └──────────────┘   └──────────────┘    │
└─────────────────────────────────────────────────────────────┘""",
            DiagramFormat.MERMAID: """graph TD
    subgraph {layer1_name}
        {comp1}[{comp1}]
        {comp2}[{comp2}]
        {comp3}[{comp3}]
    end
    subgraph {layer2_name}
        {svc1}[{svc1}]
        {svc2}[{svc2}]
        {svc3}[{svc3}]
    end
    subgraph {layer3_name}
        {repo1}[{repo1}]
        {repo2}[{repo2}]
        {repo3}[{repo3}]
    end
    {comp1} --> {svc1}
    {comp2} --> {svc2}
    {comp3} --> {svc3}
    {svc1} --> {repo1}
    {svc2} --> {repo2}
    {svc3} --> {repo3}"""
        },
        DiagramType.CONTEXT: {
            DiagramFormat.ASCII: """┌─────────────────────────────────────────────────────────┐
│                    {system_name:^37}│
│                                                         │
│   ┌────────────┐   ┌────────────┐   ┌────────────┐     │
│   │ {comp1:^10} │───│ {comp2:^10} │───│ {comp3:^10} │     │
│   └────────────┘   └────────────┘   └────────────┘     │
│                                                         │
└────────────────────────┬────────────────────────────────┘
                         │
          ┌──────────────┼──────────────┐
          │              │              │
          ▼              ▼              ▼
   ┌──────────┐   ┌──────────┐   ┌──────────┐
   │ {ext1:^8} │   │ {ext2:^8} │   │ {ext3:^8} │
   └──────────┘   └──────────┘   └──────────┘"""
        },
        DiagramType.USECASE: {
            DiagramFormat.ASCII: """                        ┌───────────────────────────┐
                        │        {system:^17}│
                        │                           │
  ┌─────────────┐       │   ○ {uc1}                 │
  │   {actor1:^9} │───────│   ○ {uc2}                 │
  └─────────────┘       │   ○ {uc3}                 │
                        │                           │
  ┌─────────────┐       │   ○ {uc4}                 │
  │   {actor2:^9} │───────│   ○ {uc5}                 │
  └─────────────┘       │                           │
                        └───────────────────────────┘""",
            DiagramFormat.MERMAID: """graph LR
    subgraph {system}
        UC1(({uc1}))
        UC2(({uc2}))
        UC3(({uc3}))
        UC4(({uc4}))
        UC5(({uc5}))
    end
    A1[{actor1}] --> UC1
    A1 --> UC2
    A1 --> UC3
    A2[{actor2}] --> UC4
    A2 --> UC5"""
        },
        DiagramType.ERD: {
            DiagramFormat.ASCII: """┌──────────────────┐         ┌──────────────────┐
│    {entity1:^12}    │         │    {entity2:^12}    │
├──────────────────┤         ├──────────────────┤
│ PK {pk1:^12} │───┐     │ PK {pk2:^12} │
│    {attr1:^14} │   │     │ FK {fk1:^12} │◀──┐
│    {attr2:^14} │   └────▶│    {attr3:^14} │   │
│    {attr4:^14} │         │    {attr5:^14} │   │
└──────────────────┘         └──────────────────┘   │
                                                    │
┌──────────────────┐                                │
│    {entity3:^12}    │                                │
├──────────────────┤                                │
│ PK {pk3:^12} │────────────────────────────────────┘
│    {attr6:^14} │
└──────────────────┘""",
            DiagramFormat.MERMAID: """erDiagram
    {entity1} ||--o{{ {entity2} : contains
    {entity2} ||--o{{ {entity3} : has

    {entity1} {{
        string {pk1} PK
        string {attr1}
        string {attr2}
    }}

    {entity2} {{
        string {pk2} PK
        string {fk1} FK
        string {attr3}
    }}

    {entity3} {{
        string {pk3} PK
        string {attr6}
    }}"""
        },
        DiagramType.CLASS: {
            DiagramFormat.MERMAID: """classDiagram
    class {class1} {{
        +{attr1}: {type1}
        +{attr2}: {type2}
        +{method1}(): {return1}
        +{method2}(): {return2}
    }}

    class {class2} {{
        +{attr3}: {type3}
        +{method3}(): {return3}
    }}

    class {class3} {{
        +{attr4}: {type4}
        +{method4}(): {return4}
    }}

    {class1} --> {class2}: uses
    {class2} --> {class3}: depends"""
        }
    }

    def generate(
        self,
        diagram_type: DiagramType,
        diagram_format: DiagramFormat,
        title: str,
        placeholders: Optional[Dict[str, str]] = None,
        custom_template: Optional[str] = None
    ) -> GeneratedDiagram:
        """
        Generiert ein Diagramm.

        Args:
            diagram_type: Typ des Diagramms
            diagram_format: Ausgabeformat
            title: Titel des Diagramms
            placeholders: Platzhalter-Werte für das Template
            custom_template: Optional ein eigenes Template

        Returns:
            GeneratedDiagram mit dem generierten Inhalt
        """
        # Template auswählen
        if custom_template:
            template = custom_template
        elif diagram_type in self.TEMPLATES and diagram_format in self.TEMPLATES[diagram_type]:
            template = self.TEMPLATES[diagram_type][diagram_format]
        else:
            # Fallback: Einfaches Platzhalter-Template
            template = f"[{diagram_type.value} diagram in {diagram_format.value} format]"

        # Platzhalter ersetzen
        content = template
        if placeholders:
            for key, value in placeholders.items():
                content = content.replace(f"{{{key}}}", str(value))

        return GeneratedDiagram(
            type=diagram_type,
            format=diagram_format,
            title=title,
            content=content
        )

    def generate_from_config(
        self,
        config: DiagramConfig,
        placeholders: Optional[Dict[str, str]] = None
    ) -> GeneratedDiagram:
        """Generiert ein Diagramm aus einer DiagramConfig."""
        diagram_type = DiagramType(config.type)
        diagram_format = DiagramFormat(config.format)

        return self.generate(
            diagram_type=diagram_type,
            diagram_format=diagram_format,
            title=f"{config.type.title()} Diagram",
            placeholders=placeholders,
            custom_template=config.template
        )


# ══════════════════════════════════════════════════════════════════════════════
# Template Engine
# ══════════════════════════════════════════════════════════════════════════════

class TemplateEngine:
    """
    Verarbeitet Output-Templates für strukturierte Ausgabe.

    Unterstützt Platzhalter wie {variable} und verschachtelte Strukturen.
    """

    def __init__(self):
        self.diagram_generator = DiagramGenerator()

    def render_template(
        self,
        template: str,
        data: Dict[str, Any]
    ) -> str:
        """
        Rendert ein Template mit gegebenen Daten.

        Args:
            template: Template-String mit {platzhaltern}
            data: Dictionary mit Werten für die Platzhalter

        Returns:
            Gerenderter String
        """
        result = template

        for key, value in data.items():
            placeholder = f"{{{key}}}"
            if placeholder in result:
                result = result.replace(placeholder, str(value))

        return result

    def render_output_template(
        self,
        template: OutputTemplate,
        content: str
    ) -> FormattedSection:
        """
        Rendert ein OutputTemplate zu einer FormattedSection.

        Args:
            template: Das Template aus der Skill-Konfiguration
            content: Der generierte Inhalt für diese Sektion

        Returns:
            FormattedSection mit dem gerenderten Inhalt
        """
        return FormattedSection(
            name=template.name,
            content=content,
            is_required=template.required,
            is_present=bool(content.strip())
        )

    def generate_structured_prompt(
        self,
        config: OutputConfig,
        command: str
    ) -> str:
        """
        Generiert einen strukturierten Prompt basierend auf der OutputConfig.

        Dieser Prompt wird an das LLM gesendet, um die gewünschte
        Ausgabe-Struktur zu erzwingen.

        Args:
            config: OutputConfig aus dem Skill
            command: Name des MCP-Commands

        Returns:
            Strukturierter Prompt für das LLM
        """
        parts = [f"## Ausgabe-Format für /{command}\n"]
        parts.append("Strukturiere deine Antwort mit den folgenden Sektionen:\n")

        # Templates als Anweisungen
        for i, template in enumerate(config.templates, 1):
            required = "(PFLICHT)" if template.required else "(optional)"
            parts.append(f"\n### {i}. {template.name} {required}")

            if template.format:
                parts.append(f"\nFormat:\n```\n{template.format}\n```")

            if template.example:
                parts.append(f"\nBeispiel:\n{template.example}")

        # Diagramm-Anweisungen
        if config.diagrams:
            parts.append("\n\n## Erforderliche Diagramme\n")
            for diagram in config.diagrams:
                parts.append(f"- **{diagram.type.title()}** im {diagram.format}-Format")

        # Quellen
        if config.include_sources:
            parts.append("\n\n## Quellen")
            parts.append("Füge am Ende eine Liste der verwendeten Quellen hinzu.")

        return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════════════════
# Output Formatter
# ══════════════════════════════════════════════════════════════════════════════

class OutputFormatter:
    """
    Hauptklasse für die Ausgabe-Formatierung.

    Kombiniert Template-Engine und Diagramm-Generator für
    vollständig formatierte Ausgaben.
    """

    def __init__(self):
        self.template_engine = TemplateEngine()
        self.diagram_generator = DiagramGenerator()

    def format_output(
        self,
        command: str,
        title: str,
        raw_content: str,
        config: Optional[OutputConfig] = None,
        sources: Optional[List[str]] = None,
        diagram_data: Optional[Dict[str, Dict[str, str]]] = None
    ) -> FormattedOutput:
        """
        Formatiert eine Ausgabe vollständig.

        Args:
            command: MCP-Command-Name
            title: Titel der Ausgabe
            raw_content: Der rohe Ausgabe-Inhalt
            config: OutputConfig für Strukturierung
            sources: Liste verwendeter Quellen
            diagram_data: Daten für Diagramm-Generierung
                          {diagram_type: {placeholder: value}}

        Returns:
            FormattedOutput mit allen Sektionen und Diagrammen
        """
        sections = []
        diagrams = []

        # Sektionen aus Config erstellen
        if config and config.templates:
            sections = self._extract_sections(raw_content, config.templates)
        else:
            # Fallback: Gesamten Inhalt als eine Sektion
            sections = [FormattedSection(
                name="Inhalt",
                content=raw_content,
                is_required=True,
                is_present=True
            )]

        # Diagramme generieren
        if config and config.diagrams:
            for diagram_config in config.diagrams:
                placeholders = {}
                if diagram_data and diagram_config.type in diagram_data:
                    placeholders = diagram_data[diagram_config.type]

                diagram = self.diagram_generator.generate_from_config(
                    diagram_config,
                    placeholders
                )
                diagrams.append(diagram)

        return FormattedOutput(
            command=command,
            title=title,
            sections=sections,
            diagrams=diagrams,
            sources=sources or []
        )

    def _extract_sections(
        self,
        content: str,
        templates: List[OutputTemplate]
    ) -> List[FormattedSection]:
        """
        Extrahiert Sektionen aus dem Inhalt basierend auf Templates.

        Sucht nach Markdown-Überschriften die den Template-Namen entsprechen.
        """
        sections = []

        for template in templates:
            # Suche nach Sektion mit diesem Namen
            section_content = self._find_section(content, template.name)

            sections.append(FormattedSection(
                name=template.name,
                content=section_content,
                is_required=template.required,
                is_present=bool(section_content.strip())
            ))

        return sections

    def _find_section(self, content: str, section_name: str) -> str:
        """
        Findet eine Sektion im Markdown-Inhalt.

        Sucht nach ## {section_name} oder ### {section_name}
        und extrahiert den Inhalt bis zur nächsten Überschrift.
        """
        # Pattern für die Sektion
        patterns = [
            rf'^##\s+{re.escape(section_name)}\s*\n(.*?)(?=^##|\Z)',
            rf'^###\s+{re.escape(section_name)}\s*\n(.*?)(?=^###|^##|\Z)',
            rf'^{re.escape(section_name)}\s*\n[-=]+\n(.*?)(?=^[^\n]+\n[-=]+|\Z)',
        ]

        for pattern in patterns:
            match = re.search(pattern, content, re.MULTILINE | re.DOTALL | re.IGNORECASE)
            if match:
                return match.group(1).strip()

        return ""

    def get_prompt_instructions(
        self,
        command: str,
        config: Optional[OutputConfig] = None
    ) -> str:
        """
        Generiert Prompt-Anweisungen für die gewünschte Ausgabe-Struktur.

        Diese Anweisungen werden dem LLM mitgegeben, um die
        Ausgabe zu strukturieren.
        """
        if not config:
            return ""

        return self.template_engine.generate_structured_prompt(config, command)

    def validate_output(
        self,
        output: FormattedOutput
    ) -> Tuple[bool, List[str]]:
        """
        Validiert eine formatierte Ausgabe.

        Prüft ob alle erforderlichen Sektionen vorhanden sind.

        Returns:
            Tuple[bool, List[str]]: (ist_valide, liste_der_fehler)
        """
        errors = []

        for section in output.sections:
            if section.is_required and not section.is_present:
                errors.append(f"Pflicht-Sektion '{section.name}' fehlt")

        return len(errors) == 0, errors


# ══════════════════════════════════════════════════════════════════════════════
# Convenience Functions
# ══════════════════════════════════════════════════════════════════════════════

def format_brainstorm_output(
    title: str,
    use_cases: List[Dict[str, Any]],
    stakeholders: List[Dict[str, str]],
    risks: List[str],
    assumptions: List[str],
    open_questions: List[str],
    sources: List[str],
    diagram_data: Optional[Dict[str, str]] = None
) -> str:
    """
    Formatiert einen Brainstorm-Output im Enterprise-Format.

    Convenience-Funktion für häufigen Use Case.
    """
    parts = [f"# {title}\n"]
    parts.append(f"*Generiert: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n")
    parts.append("---\n")

    # Use Cases
    parts.append("## Use Cases\n")
    for i, uc in enumerate(use_cases, 1):
        parts.append(f"### UC-{i:02d}: {uc.get('title', 'Untitled')}")
        parts.append(f"**Akteur:** {uc.get('actor', '-')}")
        parts.append(f"**Auslöser:** {uc.get('trigger', '-')}")
        if 'precondition' in uc:
            parts.append(f"**Vorbedingung:** {uc['precondition']}")
        parts.append("**Ablauf:**")
        for j, step in enumerate(uc.get('steps', []), 1):
            parts.append(f"{j}. {step}")
        parts.append(f"**Ergebnis:** {uc.get('result', '-')}")
        parts.append(f"**Priorität:** {uc.get('priority', 'medium')}\n")

    # Stakeholder
    parts.append("## Stakeholder-Mapping\n")
    parts.append("| Stakeholder | Rolle | Interesse | Einfluss |")
    parts.append("|-------------|-------|-----------|----------|")
    for sh in stakeholders:
        parts.append(f"| {sh.get('name', '-')} | {sh.get('role', '-')} | "
                    f"{sh.get('interest', '-')} | {sh.get('influence', '-')} |")
    parts.append("")

    # Risiken
    parts.append("## Risiken\n")
    for risk in risks:
        parts.append(f"- {risk}")
    parts.append("")

    # Annahmen
    parts.append("## Annahmen\n")
    for assumption in assumptions:
        parts.append(f"- {assumption}")
    parts.append("")

    # Offene Fragen
    parts.append("## Offene Fragen\n")
    for question in open_questions:
        parts.append(f"- [ ] {question}")
    parts.append("")

    # Context-Diagramm
    if diagram_data:
        parts.append("## Kontext-Diagramm\n")
        generator = DiagramGenerator()
        diagram = generator.generate(
            DiagramType.CONTEXT,
            DiagramFormat.ASCII,
            "System-Kontext",
            diagram_data
        )
        parts.append(f"```\n{diagram.content}\n```\n")

    # Quellen
    if sources:
        parts.append("---\n")
        parts.append("## Quellen\n")
        for i, source in enumerate(sources, 1):
            parts.append(f"{i}. {source}")

    return "\n".join(parts)


def format_design_output(
    title: str,
    overview: str,
    components: List[Dict[str, str]],
    decisions: List[Dict[str, str]],
    sources: List[str],
    sequence_data: Optional[Dict[str, str]] = None,
    component_data: Optional[Dict[str, str]] = None,
    erd_data: Optional[Dict[str, str]] = None
) -> str:
    """
    Formatiert einen Design-Output im Enterprise-Format.

    Convenience-Funktion für häufigen Use Case.
    """
    parts = [f"# {title}\n"]
    parts.append(f"*Generiert: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n")
    parts.append("---\n")

    # Overview
    parts.append("## Design Overview\n")
    parts.append(overview)
    parts.append("")

    # Komponenten
    parts.append("## Komponenten\n")
    for comp in components:
        parts.append(f"### {comp.get('name', 'Component')}")
        parts.append(f"**Verantwortung:** {comp.get('responsibility', '-')}")
        if 'interfaces' in comp:
            parts.append(f"**Schnittstellen:** {comp['interfaces']}")
        if 'dependencies' in comp:
            parts.append(f"**Abhängigkeiten:** {comp['dependencies']}")
        if 'technology' in comp:
            parts.append(f"**Technologie:** {comp['technology']}")
        parts.append("")

    # Diagramme
    generator = DiagramGenerator()

    if sequence_data:
        parts.append("## Sequenzdiagramm\n")
        diagram = generator.generate(
            DiagramType.SEQUENCE,
            DiagramFormat.MERMAID,
            "Hauptablauf",
            sequence_data
        )
        parts.append(f"```mermaid\n{diagram.content}\n```\n")

    if component_data:
        parts.append("## Komponenten-Diagramm\n")
        diagram = generator.generate(
            DiagramType.COMPONENT,
            DiagramFormat.ASCII,
            "Architektur",
            component_data
        )
        parts.append(f"```\n{diagram.content}\n```\n")

    if erd_data:
        parts.append("## Datenmodell (ERD)\n")
        diagram = generator.generate(
            DiagramType.ERD,
            DiagramFormat.ASCII,
            "Entity Relationship",
            erd_data
        )
        parts.append(f"```\n{diagram.content}\n```\n")

    # Entscheidungen
    parts.append("## Entscheidungsprotokoll\n")
    parts.append("| Entscheidung | Alternativen | Begründung |")
    parts.append("|--------------|--------------|------------|")
    for dec in decisions:
        parts.append(f"| {dec.get('decision', '-')} | "
                    f"{dec.get('alternatives', '-')} | "
                    f"{dec.get('rationale', '-')} |")
    parts.append("")

    # Quellen
    if sources:
        parts.append("---\n")
        parts.append("## Quellen\n")
        for i, source in enumerate(sources, 1):
            parts.append(f"{i}. {source}")

    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_output_formatter: Optional[OutputFormatter] = None


def get_output_formatter() -> OutputFormatter:
    """Gibt die Singleton-Instanz des OutputFormatters zurück."""
    global _output_formatter
    if _output_formatter is None:
        _output_formatter = OutputFormatter()
    return _output_formatter
