"""
Tests für Skill Command Enhancement.

Testet die Erweiterungen für command-trigger Aktivierung,
Research-Konfiguration und Output-Templates.
"""

import pytest
from pathlib import Path

from app.models.skill import (
    Skill,
    SkillActivation,
    ActivationMode,
    ResearchScope,
    ResearchConfig,
    OutputConfig,
    OutputTemplate,
    DiagramConfig,
)
from app.services.skill_manager import SkillManager


class TestActivationMode:
    """Tests für den erweiterten ActivationMode."""

    def test_command_trigger_mode_exists(self):
        """COMMAND_TRIGGER Mode ist verfügbar."""
        assert ActivationMode.COMMAND_TRIGGER == "command-trigger"

    def test_activation_with_trigger_commands(self):
        """SkillActivation akzeptiert trigger_commands."""
        activation = SkillActivation(
            mode=ActivationMode.COMMAND_TRIGGER,
            trigger_commands=["brainstorm", "design"]
        )
        assert activation.trigger_commands == ["brainstorm", "design"]
        assert activation.mode == ActivationMode.COMMAND_TRIGGER


class TestResearchConfig:
    """Tests für ResearchConfig."""

    def test_default_values(self):
        """Default-Werte sind korrekt gesetzt."""
        config = ResearchConfig()
        assert config.scope == ResearchScope.INTERNAL_ONLY
        assert "skills" in config.allowed_sources
        assert "handbook" in config.allowed_sources
        assert "confluence" in config.allowed_sources
        assert config.sanitize_queries is True
        assert config.max_web_results == 5

    def test_external_safe_scope(self):
        """EXTERNAL_SAFE erlaubt Web-Recherche."""
        config = ResearchConfig(
            scope=ResearchScope.EXTERNAL_SAFE,
            allowed_sources=["skills", "web"]
        )
        assert config.scope == ResearchScope.EXTERNAL_SAFE
        assert "web" in config.allowed_sources


class TestOutputConfig:
    """Tests für OutputConfig."""

    def test_templates(self):
        """OutputTemplates werden korrekt erstellt."""
        template = OutputTemplate(
            name="Use Cases",
            required=True,
            format="UC-{number}: {title}"
        )
        assert template.name == "Use Cases"
        assert template.required is True

    def test_diagrams(self):
        """DiagramConfig wird korrekt erstellt."""
        diagram = DiagramConfig(
            type="sequence",
            format="mermaid"
        )
        assert diagram.type == "sequence"
        assert diagram.format == "mermaid"

    def test_full_output_config(self):
        """Vollständige OutputConfig."""
        config = OutputConfig(
            templates=[
                OutputTemplate(name="Summary", required=True),
                OutputTemplate(name="Details", required=False)
            ],
            diagrams=[
                DiagramConfig(type="sequence", format="mermaid")
            ],
            include_sources=True,
            enterprise_formatting=True
        )
        assert len(config.templates) == 2
        assert len(config.diagrams) == 1


class TestEnhancedSkill:
    """Tests für erweiterte Skill-Eigenschaften."""

    def test_skill_with_research_and_output(self):
        """Skill mit research und output Konfiguration."""
        skill = Skill(
            id="test-skill",
            name="Test Skill",
            activation=SkillActivation(
                mode=ActivationMode.COMMAND_TRIGGER,
                trigger_commands=["brainstorm"]
            ),
            research=ResearchConfig(
                scope=ResearchScope.EXTERNAL_SAFE,
                allowed_sources=["skills", "web"]
            ),
            output=OutputConfig(
                templates=[OutputTemplate(name="Summary", required=True)],
                diagrams=[DiagramConfig(type="context", format="ascii")]
            )
        )
        assert skill.research is not None
        assert skill.research.scope == ResearchScope.EXTERNAL_SAFE
        assert skill.output is not None
        assert len(skill.output.templates) == 1


class TestSkillManagerCommandIntegration:
    """Tests für SkillManager Command Integration."""

    @pytest.fixture
    def manager_with_command_skills(self, tmp_path):
        """Erstellt einen SkillManager mit Command-Enhancement Skills."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        # Brainstorm-Skill erstellen
        brainstorm_skill = """
id: test-brainstorm
name: Test Brainstorm
description: Test Brainstorming Skill
type: hybrid

activation:
  mode: command-trigger
  trigger_commands:
    - brainstorm

research:
  scope: external-safe
  allowed_sources:
    - skills
    - web
  sanitize_queries: true
  max_web_results: 3

output:
  templates:
    - name: Use Cases
      required: true
  diagrams:
    - type: context
      format: ascii
  include_sources: true

system_prompt: |
  Test brainstorm prompt.
"""
        (skills_dir / "test-brainstorm.yaml").write_text(brainstorm_skill)

        # Design-Skill erstellen
        design_skill = """
id: test-design
name: Test Design
description: Test Design Skill
type: hybrid

activation:
  mode: command-trigger
  trigger_commands:
    - design

system_prompt: |
  Test design prompt.
"""
        (skills_dir / "test-design.yaml").write_text(design_skill)

        db_path = tmp_path / "test.db"
        return SkillManager(
            skills_dir=str(skills_dir),
            db_path=str(db_path)
        )

    def test_get_skills_for_command_brainstorm(self, manager_with_command_skills):
        """Skills für brainstorm Command werden gefunden."""
        skills = manager_with_command_skills.get_skills_for_command("brainstorm")
        assert len(skills) == 1
        assert skills[0].id == "test-brainstorm"

    def test_get_skills_for_command_design(self, manager_with_command_skills):
        """Skills für design Command werden gefunden."""
        skills = manager_with_command_skills.get_skills_for_command("design")
        assert len(skills) == 1
        assert skills[0].id == "test-design"

    def test_get_skills_for_unknown_command(self, manager_with_command_skills):
        """Unbekannte Commands geben leere Liste zurück."""
        skills = manager_with_command_skills.get_skills_for_command("unknown")
        assert len(skills) == 0

    def test_get_command_skills_config(self, manager_with_command_skills):
        """get_command_skills_config liefert aggregierte Konfiguration."""
        config = manager_with_command_skills.get_command_skills_config("brainstorm")

        assert "test-brainstorm" in config["skills"]
        assert "Test brainstorm prompt" in config["combined_system_prompt"]
        assert config["research"] is not None
        assert config["research"]["scope"] == "external-safe"
        assert config["output"] is not None

    def test_list_command_triggers(self, manager_with_command_skills):
        """list_command_triggers gibt Übersicht zurück."""
        triggers = manager_with_command_skills.list_command_triggers()

        assert "brainstorm" in triggers
        assert "design" in triggers
        assert "test-brainstorm" in triggers["brainstorm"]
        assert "test-design" in triggers["design"]


class TestEnterpriseBrainstormSkill:
    """Tests für den Enterprise Brainstorm Skill."""

    @pytest.fixture
    def enterprise_brainstorm(self):
        """Lädt den Enterprise Brainstorm Skill."""
        skill_path = Path(__file__).parent.parent / "skills" / "enterprise-brainstorm.yaml"
        if skill_path.exists():
            return Skill.from_yaml(skill_path)
        pytest.skip("enterprise-brainstorm.yaml nicht gefunden")

    def test_skill_loads(self, enterprise_brainstorm):
        """Skill wird korrekt geladen."""
        assert enterprise_brainstorm.id == "enterprise-brainstorm"
        assert enterprise_brainstorm.activation.mode == ActivationMode.COMMAND_TRIGGER
        assert "brainstorm" in enterprise_brainstorm.activation.trigger_commands

    def test_research_config(self, enterprise_brainstorm):
        """Research-Konfiguration ist korrekt."""
        assert enterprise_brainstorm.research is not None
        assert enterprise_brainstorm.research.scope == ResearchScope.EXTERNAL_SAFE
        assert "web" in enterprise_brainstorm.research.allowed_sources

    def test_output_templates(self, enterprise_brainstorm):
        """Output-Templates sind definiert."""
        assert enterprise_brainstorm.output is not None
        template_names = [t.name for t in enterprise_brainstorm.output.templates]
        assert "Use Cases" in template_names
        assert "Stakeholder-Mapping" in template_names


class TestEnterpriseDesignSkill:
    """Tests für den Enterprise Design Skill."""

    @pytest.fixture
    def enterprise_design(self):
        """Lädt den Enterprise Design Skill."""
        skill_path = Path(__file__).parent.parent / "skills" / "enterprise-design.yaml"
        if skill_path.exists():
            return Skill.from_yaml(skill_path)
        pytest.skip("enterprise-design.yaml nicht gefunden")

    def test_skill_loads(self, enterprise_design):
        """Skill wird korrekt geladen."""
        assert enterprise_design.id == "enterprise-design"
        assert "design" in enterprise_design.activation.trigger_commands

    def test_diagram_configs(self, enterprise_design):
        """Diagramm-Konfigurationen sind definiert."""
        assert enterprise_design.output is not None
        diagram_types = [d.type for d in enterprise_design.output.diagrams]
        assert "sequence" in diagram_types
        assert "component" in diagram_types


class TestEnterpriseAnalyzeSkill:
    """Tests für den Enterprise Analyze Skill."""

    @pytest.fixture
    def enterprise_analyze(self):
        """Lädt den Enterprise Analyze Skill."""
        skill_path = Path(__file__).parent.parent / "skills" / "enterprise-analyze.yaml"
        if skill_path.exists():
            return Skill.from_yaml(skill_path)
        pytest.skip("enterprise-analyze.yaml nicht gefunden")

    def test_skill_loads(self, enterprise_analyze):
        """Skill wird korrekt geladen."""
        assert enterprise_analyze.id == "enterprise-analyze"
        assert enterprise_analyze.activation.mode == ActivationMode.COMMAND_TRIGGER
        assert "analyze" in enterprise_analyze.activation.trigger_commands

    def test_research_config_internal_only(self, enterprise_analyze):
        """Research-Konfiguration ist internal-only (kein Web)."""
        assert enterprise_analyze.research is not None
        assert enterprise_analyze.research.scope == ResearchScope.INTERNAL_ONLY
        assert "web" not in enterprise_analyze.research.allowed_sources

    def test_owasp_in_system_prompt(self, enterprise_analyze):
        """System-Prompt enthält OWASP-Referenzen."""
        assert enterprise_analyze.system_prompt is not None
        assert "OWASP" in enterprise_analyze.system_prompt

    def test_output_templates(self, enterprise_analyze):
        """Output-Templates sind für Analyse definiert."""
        assert enterprise_analyze.output is not None
        template_names = [t.name for t in enterprise_analyze.output.templates]
        assert "Security Findings" in template_names
        assert "Quality Findings" in template_names
        assert "Metrics" in template_names


class TestEnterpriseResearchSkill:
    """Tests für den Enterprise Research Skill."""

    @pytest.fixture
    def enterprise_research(self):
        """Lädt den Enterprise Research Skill."""
        skill_path = Path(__file__).parent.parent / "skills" / "enterprise-research.yaml"
        if skill_path.exists():
            return Skill.from_yaml(skill_path)
        pytest.skip("enterprise-research.yaml nicht gefunden")

    def test_skill_loads(self, enterprise_research):
        """Skill wird korrekt geladen."""
        assert enterprise_research.id == "enterprise-research"
        assert enterprise_research.activation.mode == ActivationMode.COMMAND_TRIGGER
        assert "research" in enterprise_research.activation.trigger_commands

    def test_research_config_external_safe(self, enterprise_research):
        """Research-Konfiguration ist external-safe mit Sanitization."""
        assert enterprise_research.research is not None
        assert enterprise_research.research.scope == ResearchScope.EXTERNAL_SAFE
        assert enterprise_research.research.sanitize_queries is True
        assert "web" in enterprise_research.research.allowed_sources

    def test_source_prioritization_in_prompt(self, enterprise_research):
        """System-Prompt enthält Quellen-Priorisierung."""
        assert enterprise_research.system_prompt is not None
        assert "Confluence" in enterprise_research.system_prompt
        assert "Handbook" in enterprise_research.system_prompt

    def test_query_sanitization_in_prompt(self, enterprise_research):
        """System-Prompt enthält Query-Sanitization-Regeln."""
        assert enterprise_research.system_prompt is not None
        assert "Sanitization" in enterprise_research.system_prompt or "NIEMALS" in enterprise_research.system_prompt

    def test_output_templates(self, enterprise_research):
        """Output-Templates sind für Research definiert."""
        assert enterprise_research.output is not None
        template_names = [t.name for t in enterprise_research.output.templates]
        assert "Internal Findings" in template_names
        assert "External Findings" in template_names
        assert "Sources" in template_names
