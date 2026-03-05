"""
Skill Data Models - Pydantic-Modelle für Skill-Definitionen.

Skills kombinieren:
- System-Prompts (Anweisungen für das LLM)
- Wissensquellen (PDFs, Markdown, Text - durchsuchbar)
- Optionale Tools (spezifische Funktionen)
"""

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml
from pydantic import BaseModel, Field, field_validator


class SkillType(str, Enum):
    """Typ eines Skills."""
    KNOWLEDGE = "knowledge"  # Nur Wissensquelle (durchsuchbar)
    PROMPT = "prompt"        # Nur System-Prompt
    TOOL = "tool"            # Stellt Tools bereit
    HYBRID = "hybrid"        # Kombination aus allem


class ActivationMode(str, Enum):
    """Wann wird ein Skill aktiviert?"""
    ALWAYS = "always"        # Immer aktiv
    ON_DEMAND = "on-demand"  # Manuell aktiviert
    AUTO = "auto"            # Automatisch bei Trigger-Wörtern


class KnowledgeSourceType(str, Enum):
    """Typ einer Wissensquelle."""
    PDF = "pdf"
    MARKDOWN = "markdown"
    TEXT = "text"
    HTML = "html"


class KnowledgeSource(BaseModel):
    """Eine Wissensquelle für einen Skill."""
    type: KnowledgeSourceType
    path: Optional[str] = None      # Dateipfad (relativ zum Skills-Verzeichnis)
    content: Optional[str] = None   # Inline-Content (für type=text)
    chunk_size: int = 1000          # Tokens pro Chunk
    chunk_overlap: int = 100        # Überlappung zwischen Chunks

    @field_validator('path', 'content')
    @classmethod
    def validate_source(cls, v, info):
        # Mindestens path oder content muss gesetzt sein
        return v


class SkillActivation(BaseModel):
    """Aktivierungs-Einstellungen für einen Skill."""
    mode: ActivationMode = ActivationMode.ON_DEMAND
    trigger_words: List[str] = Field(default_factory=list)
    confidence_threshold: float = 0.8  # Für auto-Aktivierung


class ToolParameter(BaseModel):
    """Parameter-Definition für ein Skill-Tool."""
    name: str
    type: str  # string, number, boolean, array, object
    description: str
    required: bool = False
    default: Optional[Any] = None


class SkillTool(BaseModel):
    """Tool-Definition die ein Skill bereitstellen kann."""
    name: str
    description: str
    parameters: List[ToolParameter] = Field(default_factory=list)
    handler: Optional[str] = None  # Python-Funktion (dotted path)


class SkillMetadata(BaseModel):
    """Metadaten eines Skills."""
    author: Optional[str] = None
    created: Optional[datetime] = None
    updated: Optional[datetime] = None
    tags: List[str] = Field(default_factory=list)
    source_file: Optional[str] = None  # Für PDF->Skill


class Skill(BaseModel):
    """
    Vollständige Skill-Definition.

    Ein Skill kann enthalten:
    - System-Prompt: Wird bei Aktivierung zum LLM-Kontext hinzugefügt
    - Wissensquellen: Werden indexiert und bei Bedarf durchsucht
    - Tools: Können vom LLM aufgerufen werden (zukünftig)
    """
    id: str = Field(..., description="Eindeutige ID (slug)")
    name: str = Field(..., description="Anzeigename")
    description: str = Field(default="", description="Kurzbeschreibung")
    version: str = Field(default="1.0")
    type: SkillType = SkillType.KNOWLEDGE

    activation: SkillActivation = Field(default_factory=SkillActivation)
    system_prompt: Optional[str] = Field(default=None, description="LLM System-Prompt")
    knowledge_sources: List[KnowledgeSource] = Field(default_factory=list)
    tools: List[SkillTool] = Field(default_factory=list)
    metadata: SkillMetadata = Field(default_factory=SkillMetadata)

    # Runtime-State (nicht in YAML gespeichert)
    _file_path: Optional[Path] = None
    _is_active: bool = False

    class Config:
        use_enum_values = True

    @classmethod
    def from_yaml(cls, yaml_path: Path) -> "Skill":
        """Lädt einen Skill aus einer YAML-Datei."""
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        skill = cls(**data)
        skill._file_path = yaml_path
        return skill

    def to_yaml(self, yaml_path: Optional[Path] = None) -> str:
        """Serialisiert den Skill als YAML."""
        path = yaml_path or self._file_path

        # Nur persistierbare Felder exportieren
        data = self.model_dump(
            exclude_none=True,
            exclude={"_file_path", "_is_active"}
        )

        yaml_str = yaml.dump(
            data,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False
        )

        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(yaml_str)

        return yaml_str

    def has_knowledge(self) -> bool:
        """Prüft ob der Skill Wissensquellen hat."""
        return len(self.knowledge_sources) > 0

    def has_prompt(self) -> bool:
        """Prüft ob der Skill einen System-Prompt hat."""
        return bool(self.system_prompt)

    def has_tools(self) -> bool:
        """Prüft ob der Skill Tools bereitstellt."""
        return len(self.tools) > 0


# ══════════════════════════════════════════════════════════════════════════════
# API Response Models
# ══════════════════════════════════════════════════════════════════════════════

class SkillSummary(BaseModel):
    """Kurzübersicht eines Skills für Listen."""
    id: str
    name: str
    description: str
    type: str
    activation_mode: str
    has_knowledge: bool
    has_prompt: bool
    is_active: bool = False
    tags: List[str] = Field(default_factory=list)


class SkillDetail(BaseModel):
    """Detailansicht eines Skills."""
    id: str
    name: str
    description: str
    version: str
    type: str
    activation: Dict[str, Any]
    system_prompt: Optional[str]
    knowledge_sources: List[Dict[str, Any]]
    tools: List[Dict[str, Any]]
    metadata: Dict[str, Any]
    is_active: bool = False


class SkillCreateRequest(BaseModel):
    """Request zum Erstellen eines Skills."""
    name: str
    description: str = ""
    type: SkillType = SkillType.KNOWLEDGE
    activation_mode: ActivationMode = ActivationMode.ON_DEMAND
    trigger_words: List[str] = Field(default_factory=list)
    system_prompt: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


class SkillFromPDFRequest(BaseModel):
    """Request zum Erstellen eines Skills aus einer PDF."""
    pdf_id: str = Field(..., description="ID der hochgeladenen PDF")
    name: str = Field(..., description="Name des neuen Skills")
    description: str = Field(default="", description="Beschreibung")
    trigger_words: List[str] = Field(default_factory=list)
    system_prompt: str = Field(
        default="Beantworte Fragen basierend auf dem folgenden Dokument.",
        description="System-Prompt für den Skill"
    )
    chunk_size: int = Field(default=1000, ge=100, le=5000)
    chunk_overlap: int = Field(default=100, ge=0, le=500)
    selected_pages: Optional[List[int]] = Field(
        default=None,
        description="Nur bestimmte Seiten verwenden (1-basiert)"
    )


class SkillActivateRequest(BaseModel):
    """Request zum Aktivieren/Deaktivieren von Skills."""
    skill_ids: List[str]
    activate: bool = True


class SkillSearchResult(BaseModel):
    """Suchergebnis aus Skill-Wissensbasen."""
    skill_id: str
    skill_name: str
    source_path: str
    snippet: str
    rank: float


class ActiveSkillsResponse(BaseModel):
    """Response mit aktiven Skills einer Session."""
    session_id: str
    active_skills: List[SkillSummary]
    combined_prompt_tokens: int
