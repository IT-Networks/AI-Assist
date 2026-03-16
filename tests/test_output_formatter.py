"""
Tests für den Output Formatter.

Testet Diagramm-Generierung, Template-Engine und Ausgabe-Formatierung.
"""

import pytest
from datetime import datetime
from app.services.output_formatter import (
    DiagramType,
    DiagramFormat,
    DiagramGenerator,
    TemplateEngine,
    OutputFormatter,
    FormattedSection,
    GeneratedDiagram,
    FormattedOutput,
    format_brainstorm_output,
    format_design_output,
    get_output_formatter,
)
from app.models.skill import OutputConfig, OutputTemplate, DiagramConfig


class TestDiagramType:
    """Tests für DiagramType Enum."""

    def test_all_types_exist(self):
        """Alle erwarteten Diagramm-Typen existieren."""
        expected = ["sequence", "class", "component", "erd", "usecase", "context", "flowchart"]
        actual = [t.value for t in DiagramType]
        for exp in expected:
            assert exp in actual


class TestDiagramFormat:
    """Tests für DiagramFormat Enum."""

    def test_all_formats_exist(self):
        """Alle erwarteten Formate existieren."""
        expected = ["mermaid", "plantuml", "ascii"]
        actual = [f.value for f in DiagramFormat]
        for exp in expected:
            assert exp in actual


class TestGeneratedDiagram:
    """Tests für GeneratedDiagram."""

    def test_mermaid_to_markdown(self):
        """Mermaid-Diagramm wird korrekt als Markdown formatiert."""
        diagram = GeneratedDiagram(
            type=DiagramType.SEQUENCE,
            format=DiagramFormat.MERMAID,
            title="Test Diagram",
            content="sequenceDiagram\n    A->>B: Hello"
        )
        md = diagram.to_markdown()
        assert "### Test Diagram" in md
        assert "```mermaid" in md
        assert "sequenceDiagram" in md

    def test_plantuml_to_markdown(self):
        """PlantUML-Diagramm wird korrekt formatiert."""
        diagram = GeneratedDiagram(
            type=DiagramType.CLASS,
            format=DiagramFormat.PLANTUML,
            title="Class Diagram",
            content="@startuml\nclass User\n@enduml"
        )
        md = diagram.to_markdown()
        assert "```plantuml" in md

    def test_ascii_to_markdown(self):
        """ASCII-Diagramm wird korrekt formatiert."""
        diagram = GeneratedDiagram(
            type=DiagramType.COMPONENT,
            format=DiagramFormat.ASCII,
            title="Architecture",
            content="[A] --> [B]"
        )
        md = diagram.to_markdown()
        assert "```\n" in md
        assert "```mermaid" not in md


class TestFormattedSection:
    """Tests für FormattedSection."""

    def test_section_creation(self):
        """Sektion kann erstellt werden."""
        section = FormattedSection(
            name="Test Section",
            content="Some content",
            is_required=True,
            is_present=True
        )
        assert section.name == "Test Section"
        assert section.is_required
        assert section.is_present


class TestFormattedOutput:
    """Tests für FormattedOutput."""

    def test_to_markdown_basic(self):
        """Grundlegendes Markdown wird korrekt generiert."""
        output = FormattedOutput(
            command="brainstorm",
            title="Test Brainstorm",
            sections=[
                FormattedSection(
                    name="Summary",
                    content="This is a summary.",
                    is_required=True,
                    is_present=True
                )
            ],
            diagrams=[],
            sources=["Source 1", "Source 2"]
        )
        md = output.to_markdown()
        assert "# Test Brainstorm" in md
        assert "## Summary" in md
        assert "This is a summary." in md
        assert "## Quellen" in md
        assert "1. Source 1" in md

    def test_to_markdown_with_diagrams(self):
        """Diagramme werden in Markdown eingefügt."""
        diagram = GeneratedDiagram(
            type=DiagramType.SEQUENCE,
            format=DiagramFormat.MERMAID,
            title="Sequence",
            content="sequenceDiagram\n    A->>B: msg"
        )
        output = FormattedOutput(
            command="design",
            title="Design Doc",
            sections=[],
            diagrams=[diagram],
            sources=[]
        )
        md = output.to_markdown()
        assert "## Diagramme" in md
        assert "### Sequence" in md

    def test_absent_section_not_in_markdown(self):
        """Nicht vorhandene Sektionen erscheinen nicht im Markdown."""
        output = FormattedOutput(
            command="test",
            title="Test",
            sections=[
                FormattedSection(
                    name="Missing",
                    content="",
                    is_required=False,
                    is_present=False
                )
            ],
            diagrams=[],
            sources=[]
        )
        md = output.to_markdown()
        assert "## Missing" not in md


class TestDiagramGenerator:
    """Tests für den DiagramGenerator."""

    @pytest.fixture
    def generator(self):
        return DiagramGenerator()

    def test_sequence_mermaid(self, generator):
        """Sequenzdiagramm in Mermaid wird generiert."""
        diagram = generator.generate(
            DiagramType.SEQUENCE,
            DiagramFormat.MERMAID,
            "Auth Flow",
            {
                "actor1": "User",
                "actor1_label": "User",
                "actor2": "API",
                "actor2_label": "API Gateway",
                "actor3": "DB",
                "actor3_label": "Database",
                "action1": "Request",
                "action2": "Query",
                "response1": "Response",
                "response2": "Data"
            }
        )
        assert diagram.type == DiagramType.SEQUENCE
        assert diagram.format == DiagramFormat.MERMAID
        assert "sequenceDiagram" in diagram.content
        assert "User" in diagram.content
        assert "API" in diagram.content

    def test_sequence_ascii(self, generator):
        """Sequenzdiagramm in ASCII wird generiert."""
        diagram = generator.generate(
            DiagramType.SEQUENCE,
            DiagramFormat.ASCII,
            "Flow",
            {
                "actor1": "A",
                "actor2": "B",
                "actor3": "C",
                "action1": "msg1",
                "action2": "msg2",
                "response1": "r1",
                "response2": "r2"
            }
        )
        assert diagram.format == DiagramFormat.ASCII
        assert "──────────────▶" in diagram.content or "→" in diagram.content or "─" in diagram.content

    def test_component_mermaid(self, generator):
        """Komponenten-Diagramm in Mermaid wird generiert."""
        diagram = generator.generate(
            DiagramType.COMPONENT,
            DiagramFormat.MERMAID,
            "Architecture",
            {
                "layer1_name": "Frontend",
                "layer2_name": "Backend",
                "layer3_name": "Data",
                "comp1": "UI", "comp2": "API", "comp3": "Auth",
                "svc1": "Service1", "svc2": "Service2", "svc3": "Service3",
                "repo1": "DB1", "repo2": "DB2", "repo3": "DB3"
            }
        )
        assert "graph TD" in diagram.content
        assert "Frontend" in diagram.content

    def test_usecase_ascii(self, generator):
        """UseCase-Diagramm in ASCII wird generiert."""
        diagram = generator.generate(
            DiagramType.USECASE,
            DiagramFormat.ASCII,
            "Use Cases",
            {
                "system": "MyApp",
                "actor1": "Admin",
                "actor2": "User",
                "uc1": "Login",
                "uc2": "Logout",
                "uc3": "View",
                "uc4": "Edit",
                "uc5": "Delete"
            }
        )
        # Use cases werden ersetzt, aber Format-Strings wie {system:^17} bleiben
        assert "Login" in diagram.content
        assert "Logout" in diagram.content

    def test_erd_mermaid(self, generator):
        """ERD in Mermaid wird generiert."""
        diagram = generator.generate(
            DiagramType.ERD,
            DiagramFormat.MERMAID,
            "Data Model",
            {
                "entity1": "User",
                "entity2": "Order",
                "entity3": "Product",
                "pk1": "id", "pk2": "id", "pk3": "id",
                "fk1": "user_id",
                "attr1": "name", "attr2": "email",
                "attr3": "date", "attr5": "total",
                "attr6": "price"
            }
        )
        assert "erDiagram" in diagram.content
        assert "User" in diagram.content

    def test_fallback_template(self, generator):
        """Bei fehlendem Template wird Fallback verwendet."""
        diagram = generator.generate(
            DiagramType.FLOWCHART,  # Hat kein Template
            DiagramFormat.PLANTUML,  # Hat kein Template
            "Flow"
        )
        assert "flowchart" in diagram.content.lower()
        assert "plantuml" in diagram.content.lower()

    def test_custom_template(self, generator):
        """Custom Template wird verwendet."""
        custom = "Custom: {name} - {value}"
        diagram = generator.generate(
            DiagramType.SEQUENCE,
            DiagramFormat.MERMAID,
            "Custom",
            {"name": "Test", "value": "123"},
            custom_template=custom
        )
        assert diagram.content == "Custom: Test - 123"

    def test_generate_from_config(self, generator):
        """DiagramConfig wird korrekt verarbeitet."""
        config = DiagramConfig(
            type="sequence",
            format="mermaid"
        )
        diagram = generator.generate_from_config(
            config,
            {
                "actor1": "A", "actor1_label": "A",
                "actor2": "B", "actor2_label": "B",
                "actor3": "C", "actor3_label": "C",
                "action1": "x", "action2": "y",
                "response1": "z", "response2": "w"
            }
        )
        assert diagram.type == DiagramType.SEQUENCE
        assert diagram.format == DiagramFormat.MERMAID


class TestTemplateEngine:
    """Tests für die TemplateEngine."""

    @pytest.fixture
    def engine(self):
        return TemplateEngine()

    def test_render_simple_template(self, engine):
        """Einfaches Template wird gerendert."""
        result = engine.render_template(
            "Hello {name}, welcome to {place}!",
            {"name": "World", "place": "Earth"}
        )
        assert result == "Hello World, welcome to Earth!"

    def test_render_missing_placeholder(self, engine):
        """Fehlende Platzhalter bleiben erhalten."""
        result = engine.render_template(
            "Hello {name}, your ID is {id}",
            {"name": "User"}
        )
        assert "{id}" in result

    def test_render_output_template(self, engine):
        """OutputTemplate wird zu FormattedSection gerendert."""
        template = OutputTemplate(
            name="Executive Summary",
            required=True
        )
        section = engine.render_output_template(template, "This is the summary.")
        assert section.name == "Executive Summary"
        assert section.content == "This is the summary."
        assert section.is_required
        assert section.is_present

    def test_render_empty_content(self, engine):
        """Leerer Inhalt wird erkannt."""
        template = OutputTemplate(name="Empty", required=True)
        section = engine.render_output_template(template, "   ")
        assert not section.is_present

    def test_generate_structured_prompt(self, engine):
        """Strukturierter Prompt wird generiert."""
        config = OutputConfig(
            templates=[
                OutputTemplate(name="Summary", required=True, example="Example summary"),
                OutputTemplate(name="Details", required=False, format="- Point 1\n- Point 2")
            ],
            diagrams=[
                DiagramConfig(type="sequence", format="mermaid")
            ],
            include_sources=True
        )
        prompt = engine.generate_structured_prompt(config, "brainstorm")
        assert "Ausgabe-Format für /brainstorm" in prompt
        assert "Summary" in prompt
        assert "(PFLICHT)" in prompt
        assert "(optional)" in prompt
        assert "Sequence" in prompt
        assert "Quellen" in prompt


class TestOutputFormatter:
    """Tests für den OutputFormatter."""

    @pytest.fixture
    def formatter(self):
        return OutputFormatter()

    def test_format_without_config(self, formatter):
        """Formatierung ohne Config erstellt einzelne Sektion."""
        output = formatter.format_output(
            command="test",
            title="Test Output",
            raw_content="Some content here."
        )
        assert len(output.sections) == 1
        assert output.sections[0].name == "Inhalt"
        assert output.sections[0].content == "Some content here."

    def test_format_with_templates(self, formatter):
        """Formatierung mit Templates extrahiert Sektionen."""
        config = OutputConfig(
            templates=[
                OutputTemplate(name="Summary", required=True),
                OutputTemplate(name="Details", required=False)
            ]
        )
        raw_content = """## Summary
This is the summary section.

## Details
These are the details.
"""
        output = formatter.format_output(
            command="test",
            title="Test",
            raw_content=raw_content,
            config=config
        )
        assert len(output.sections) == 2
        assert output.sections[0].name == "Summary"
        assert "summary section" in output.sections[0].content

    def test_format_with_diagrams(self, formatter):
        """Diagramme werden generiert."""
        config = OutputConfig(
            templates=[],
            diagrams=[
                DiagramConfig(type="sequence", format="mermaid")
            ]
        )
        output = formatter.format_output(
            command="design",
            title="Design",
            raw_content="Content",
            config=config,
            diagram_data={
                "sequence": {
                    "actor1": "A", "actor1_label": "A",
                    "actor2": "B", "actor2_label": "B",
                    "actor3": "C", "actor3_label": "C",
                    "action1": "x", "action2": "y",
                    "response1": "z", "response2": "w"
                }
            }
        )
        assert len(output.diagrams) == 1
        assert output.diagrams[0].type == DiagramType.SEQUENCE

    def test_format_with_sources(self, formatter):
        """Quellen werden hinzugefügt."""
        output = formatter.format_output(
            command="test",
            title="Test",
            raw_content="Content",
            sources=["Source A", "Source B"]
        )
        assert len(output.sources) == 2

    def test_validate_output_success(self, formatter):
        """Validierung erfolgreich bei vollständiger Ausgabe."""
        output = FormattedOutput(
            command="test",
            title="Test",
            sections=[
                FormattedSection(name="Required", content="Yes", is_required=True, is_present=True)
            ],
            diagrams=[],
            sources=[]
        )
        is_valid, errors = formatter.validate_output(output)
        assert is_valid
        assert len(errors) == 0

    def test_validate_output_missing_required(self, formatter):
        """Validierung schlägt fehl bei fehlender Pflichtsektion."""
        output = FormattedOutput(
            command="test",
            title="Test",
            sections=[
                FormattedSection(name="Required", content="", is_required=True, is_present=False)
            ],
            diagrams=[],
            sources=[]
        )
        is_valid, errors = formatter.validate_output(output)
        assert not is_valid
        assert "Pflicht-Sektion 'Required' fehlt" in errors[0]

    def test_get_prompt_instructions_empty(self, formatter):
        """Leere Anweisungen ohne Config."""
        result = formatter.get_prompt_instructions("test")
        assert result == ""

    def test_get_prompt_instructions_with_config(self, formatter):
        """Prompt-Anweisungen werden generiert."""
        config = OutputConfig(
            templates=[OutputTemplate(name="Summary", required=True)],
            diagrams=[]
        )
        result = formatter.get_prompt_instructions("test", config)
        assert "Summary" in result


class TestFindSection:
    """Tests für die Sektions-Extraktion."""

    @pytest.fixture
    def formatter(self):
        return OutputFormatter()

    def test_find_h2_section(self, formatter):
        """H2-Sektion wird gefunden."""
        content = """## Introduction
This is the intro.

## Body
This is the body.
"""
        result = formatter._find_section(content, "Introduction")
        assert "This is the intro." in result
        assert "This is the body." not in result

    def test_find_h3_section(self, formatter):
        """H3-Sektion wird gefunden."""
        content = """### Details
Detailed content here.

### More
More content.
"""
        result = formatter._find_section(content, "Details")
        assert "Detailed content here." in result

    def test_find_section_case_insensitive(self, formatter):
        """Sektions-Suche ist case-insensitive."""
        content = """## SUMMARY
The summary text.
"""
        result = formatter._find_section(content, "Summary")
        assert "summary text" in result

    def test_find_nonexistent_section(self, formatter):
        """Nicht existierende Sektion gibt leeren String zurück."""
        content = """## Existing
Content.
"""
        result = formatter._find_section(content, "Missing")
        assert result == ""


class TestConvenienceFunctions:
    """Tests für die Convenience-Funktionen."""

    def test_format_brainstorm_output(self):
        """Brainstorm-Output wird korrekt formatiert."""
        result = format_brainstorm_output(
            title="Test Brainstorm",
            use_cases=[
                {
                    "title": "Login",
                    "actor": "User",
                    "trigger": "Click login",
                    "steps": ["Enter credentials", "Submit"],
                    "result": "Logged in",
                    "priority": "high"
                }
            ],
            stakeholders=[
                {"name": "Admin", "role": "Administrator", "interest": "High", "influence": "High"}
            ],
            risks=["Security breach"],
            assumptions=["Users have internet"],
            open_questions=["What about 2FA?"],
            sources=["Internal docs"]
        )
        assert "# Test Brainstorm" in result
        assert "## Use Cases" in result
        assert "UC-01: Login" in result
        assert "**Akteur:** User" in result
        assert "## Stakeholder-Mapping" in result
        assert "| Admin |" in result
        assert "## Risiken" in result
        assert "## Annahmen" in result
        assert "## Offene Fragen" in result
        assert "- [ ] What about 2FA?" in result

    def test_format_brainstorm_with_diagram(self):
        """Brainstorm mit Kontext-Diagramm."""
        result = format_brainstorm_output(
            title="Test",
            use_cases=[],
            stakeholders=[],
            risks=[],
            assumptions=[],
            open_questions=[],
            sources=[],
            diagram_data={
                "system_name": "MySystem",
                "comp1": "A", "comp2": "B", "comp3": "C",
                "ext1": "Ext1", "ext2": "Ext2", "ext3": "Ext3"
            }
        )
        assert "## Kontext-Diagramm" in result

    def test_format_design_output(self):
        """Design-Output wird korrekt formatiert."""
        result = format_design_output(
            title="Test Design",
            overview="System overview text.",
            components=[
                {
                    "name": "API Gateway",
                    "responsibility": "Handle requests",
                    "technology": "FastAPI"
                }
            ],
            decisions=[
                {
                    "decision": "Use REST",
                    "alternatives": "GraphQL",
                    "rationale": "Simpler"
                }
            ],
            sources=["Architecture docs"]
        )
        assert "# Test Design" in result
        assert "## Design Overview" in result
        assert "System overview text." in result
        assert "### API Gateway" in result
        assert "**Technologie:** FastAPI" in result
        assert "## Entscheidungsprotokoll" in result
        assert "| Use REST |" in result

    def test_format_design_with_diagrams(self):
        """Design mit allen Diagramm-Typen."""
        result = format_design_output(
            title="Full Design",
            overview="Overview",
            components=[],
            decisions=[],
            sources=[],
            sequence_data={
                "actor1": "A", "actor1_label": "A",
                "actor2": "B", "actor2_label": "B",
                "actor3": "C", "actor3_label": "C",
                "action1": "x", "action2": "y",
                "response1": "z", "response2": "w"
            },
            component_data={
                "layer1_name": "L1", "layer2_name": "L2", "layer3_name": "L3",
                "comp1": "C1", "comp2": "C2", "comp3": "C3",
                "svc1": "S1", "svc2": "S2", "svc3": "S3",
                "repo1": "R1", "repo2": "R2", "repo3": "R3"
            },
            erd_data={
                "entity1": "E1", "entity2": "E2", "entity3": "E3",
                "pk1": "id1", "pk2": "id2", "pk3": "id3",
                "fk1": "fk",
                "attr1": "a1", "attr2": "a2", "attr3": "a3",
                "attr4": "a4", "attr5": "a5", "attr6": "a6"
            }
        )
        assert "## Sequenzdiagramm" in result
        assert "## Komponenten-Diagramm" in result
        assert "## Datenmodell (ERD)" in result


class TestSingleton:
    """Tests für Singleton-Pattern."""

    def test_get_output_formatter_returns_same_instance(self):
        """get_output_formatter gibt immer dieselbe Instanz zurück."""
        formatter1 = get_output_formatter()
        formatter2 = get_output_formatter()
        assert formatter1 is formatter2

    def test_formatter_has_required_components(self):
        """Formatter hat alle Komponenten."""
        formatter = get_output_formatter()
        assert hasattr(formatter, 'template_engine')
        assert hasattr(formatter, 'diagram_generator')
