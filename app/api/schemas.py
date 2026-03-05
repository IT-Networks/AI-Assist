from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class ContextSources(BaseModel):
    java_files: List[str] = Field(default_factory=list, description="Relative paths to Java files")
    include_pom: bool = False
    auto_java_search: bool = False  # FTS-Index nach relevanten Dateien durchsuchen
    log_id: Optional[str] = None
    pdf_ids: List[str] = Field(default_factory=list)
    confluence_page_ids: List[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    session_id: str
    message: str
    model: Optional[str] = None
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
