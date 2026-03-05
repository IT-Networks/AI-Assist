from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class ContextSources(BaseModel):
    java_files: List[str] = Field(default_factory=list, max_length=50, description="Relative paths to Java files (max 50)")
    include_pom: bool = False
    auto_java_search: bool = False  # FTS-Index nach relevanten Dateien durchsuchen
    log_id: Optional[str] = Field(None, max_length=100)
    pdf_ids: List[str] = Field(default_factory=list, max_length=20, description="Max 20 PDFs")
    confluence_page_ids: List[str] = Field(default_factory=list, max_length=20, description="Max 20 Seiten")
    python_files: List[str] = Field(default_factory=list, max_length=50, description="Relative paths to Python files (max 50)")
    auto_python_search: bool = False  # FTS-Index nach relevanten Python-Dateien durchsuchen
    # Handbuch-Integration
    handbook_pages: List[str] = Field(default_factory=list, max_length=20, description="Relative paths to handbook pages (max 20)")
    auto_handbook_search: bool = False  # Handbuch-Index nach relevanten Seiten durchsuchen
    handbook_service_filter: Optional[str] = Field(None, max_length=100)
    # Skill-Integration
    active_skill_ids: List[str] = Field(default_factory=list, max_length=10, description="IDs der aktiven Skills (max 10)")
    auto_skill_knowledge: bool = True  # Automatisch in Skill-Wissensbasen suchen


class ChatRequest(BaseModel):
    session_id: str = Field(..., max_length=100)
    message: str = Field(..., min_length=1, max_length=100000, description="Max 100k Zeichen")
    model: Optional[str] = Field(None, max_length=100)
    stream: bool = True
    context_sources: Optional[ContextSources] = None


class ChatResponse(BaseModel):
    session_id: str
    response: str


class ModelInfo(BaseModel):
    id: str
    display_name: str


class ModelsResponse(BaseModel):
    models: List[ModelInfo]
    default: str


class UploadResponse(BaseModel):
    id: str
    filename: str
    size_bytes: int
    message: str


class LogSummaryResponse(BaseModel):
    log_id: str
    total_lines: int
    error_count: int
    warning_count: int
    errors: List[Dict]


class PDFInfoResponse(BaseModel):
    pdf_id: str
    filename: str
    page_count: int
    char_count: int


class ConfluenceSearchResult(BaseModel):
    id: str
    title: str
    url: str
    space: str
    excerpt: str
    last_modified: Optional[str] = None


class ConfluencePageResponse(BaseModel):
    id: str
    title: str
    url: str
    content: str


class ValidationResult(BaseModel):
    tool: str
    stdout: str
    stderr: str
    returncode: int


class ValidationResponse(BaseModel):
    repo_path: str
    results: Dict[str, ValidationResult]


class TestResponse(BaseModel):
    stdout: str
    stderr: str
    returncode: int
    passed: int
    failed: int
    errors: int


class GenerateRequest(BaseModel):
    target_dir: str = Field(..., max_length=500, description="Zielverzeichnis")
    description: str = Field(..., min_length=10, max_length=10000, description="Beschreibung (10-10k Zeichen)")
    session_id: str = Field(..., max_length=100)
    model: Optional[str] = Field(None, max_length=100)


class GenerateResponse(BaseModel):
    files_written: List[str]
    target_dir: str
    message: str
