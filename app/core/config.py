import os
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings


class ModelEntry(BaseModel):
    id: str
    display_name: str


class LLMConfig(BaseModel):
    base_url: str = "http://localhost/v1"
    api_key: str = "none"
    default_model: str = "gptoss120b"
    timeout_seconds: int = 120
    max_tokens: int = 4096
    temperature: float = 0.2
    verify_ssl: bool = True  # False für selbstsignierte Zertifikate
    # Modell-Aufteilung für Agent
    tool_model: str = ""  # Schnelles Modell für Tool-Aufrufe/Suche (leer = default_model)
    analysis_model: str = ""  # Größeres Modell für Analyse/Antwort (leer = default_model)


class JavaConfig(BaseModel):
    repo_path: str = ""
    exclude_dirs: List[str] = ["target", ".git", "node_modules", ".idea"]
    max_file_size_kb: int = 500


class ConfluenceConfig(BaseModel):
    base_url: str = ""
    username: str = ""
    api_token: str = ""   # Atlassian Cloud API Token (bevorzugt)
    password: str = ""    # Atlassian Server/DC Passwort (Fallback wenn api_token leer)
    default_space: str = ""


class PythonConfig(BaseModel):
    repo_path: str = ""
    exclude_dirs: List[str] = ["__pycache__", ".venv", ".git", "node_modules", ".mypy_cache", ".pytest_cache", "dist", "build"]
    max_file_size_kb: int = 500


class ToolsConfig(BaseModel):
    flake8: str = "/root/.local/bin/flake8"
    ruff: str = "/root/.local/bin/ruff"
    mypy: str = "/root/.local/bin/mypy"
    pytest: str = "/root/.local/bin/pytest"


class IndexConfig(BaseModel):
    directory: str = "./index"
    auto_build_on_start: bool = False
    max_search_results: int = 5


class HandbookConfig(BaseModel):
    """Konfiguration für HTML-Handbuch auf Netzlaufwerk."""
    enabled: bool = False
    path: str = ""  # Pfad zum Handbuch-Verzeichnis (kann Netzlaufwerk sein)
    index_on_start: bool = False  # Automatisch beim Start indexieren
    exclude_patterns: List[str] = ["**/archiv/**", "**/backup/**", "**/.git/**"]
    # Struktur-Erkennung
    functions_subdir: str = "funktionen"  # Subordner für Service-Funktionen
    fields_subdir: str = "felder"  # Subordner für Feld-Definitionen


class SkillsConfig(BaseModel):
    """Konfiguration für das Skill-System."""
    enabled: bool = True
    directory: str = "./skills"
    auto_activation: bool = False  # Automatische Skill-Aktivierung basierend auf Keywords
    max_active_skills: int = 5  # Max. gleichzeitig aktive Skills


class FileOperationsConfig(BaseModel):
    """Konfiguration für Datei-Operationen (Read/Write/Edit)."""
    enabled: bool = False
    default_mode: str = "read_only"  # read_only | write_with_confirm
    allowed_paths: List[str] = []  # Erlaubte Pfade für Schreiboperationen
    allowed_extensions: List[str] = [".java", ".py", ".xml", ".yaml", ".yml", ".json", ".md", ".properties", ".sql", ".sqlj"]
    denied_patterns: List[str] = ["**/node_modules/**", "**/.git/**", "**/target/**", "**/__pycache__/**"]
    backup_enabled: bool = True
    backup_directory: str = "./backups"


class DatabaseConfig(BaseModel):
    """Konfiguration für DB2-Datenbankverbindung."""
    enabled: bool = False
    driver: str = "ibm_db"  # ibm_db oder jaydebeapi
    host: str = ""
    port: int = 50000
    database: str = ""
    schema: str = ""
    username: str = ""
    password: str = ""
    # Sicherheit
    require_confirmation: bool = True  # Bestätigung vor jeder Query
    max_rows: int = 1000  # Max. Zeilen pro Abfrage
    timeout_seconds: int = 30
    readonly: bool = True  # Nur SELECT erlaubt
    # Für JDBC (jaydebeapi)
    jdbc_driver_path: str = ""  # Pfad zur db2jcc4.jar
    jdbc_driver_class: str = "com.ibm.db2.jcc.DB2Driver"


class ContextConfig(BaseModel):
    max_tokens: int = 32000
    max_file_context_kb: int = 100


class UploadsConfig(BaseModel):
    directory: str = "./uploads"
    max_file_size_mb: int = 50
    cleanup_after_hours: int = 24


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = False


class Settings(BaseModel):
    llm: LLMConfig = LLMConfig()
    models: List[ModelEntry] = []
    java: JavaConfig = JavaConfig()
    python: PythonConfig = PythonConfig()
    tools: ToolsConfig = ToolsConfig()
    confluence: ConfluenceConfig = ConfluenceConfig()
    context: ContextConfig = ContextConfig()
    uploads: UploadsConfig = UploadsConfig()
    server: ServerConfig = ServerConfig()
    index: IndexConfig = IndexConfig()
    handbook: HandbookConfig = HandbookConfig()
    database: DatabaseConfig = DatabaseConfig()
    skills: SkillsConfig = SkillsConfig()
    file_operations: FileOperationsConfig = FileOperationsConfig()

    def apply_env_overrides(self) -> "Settings":
        if os.getenv("LLM_BASE_URL"):
            self.llm.base_url = os.getenv("LLM_BASE_URL")
        if os.getenv("LLM_API_KEY"):
            self.llm.api_key = os.getenv("LLM_API_KEY")
        if os.getenv("JAVA_REPO_PATH"):
            self.java.repo_path = os.getenv("JAVA_REPO_PATH")
        if os.getenv("CONFLUENCE_BASE_URL"):
            self.confluence.base_url = os.getenv("CONFLUENCE_BASE_URL")
        if os.getenv("CONFLUENCE_USERNAME"):
            self.confluence.username = os.getenv("CONFLUENCE_USERNAME")
        if os.getenv("CONFLUENCE_API_TOKEN"):
            self.confluence.api_token = os.getenv("CONFLUENCE_API_TOKEN")
        if os.getenv("CONFLUENCE_PASSWORD"):
            self.confluence.password = os.getenv("CONFLUENCE_PASSWORD")
        if os.getenv("PYTHON_REPO_PATH"):
            self.python.repo_path = os.getenv("PYTHON_REPO_PATH")
        if os.getenv("HANDBOOK_PATH"):
            self.handbook.path = os.getenv("HANDBOOK_PATH")
            self.handbook.enabled = True
        if os.getenv("SKILLS_DIRECTORY"):
            self.skills.directory = os.getenv("SKILLS_DIRECTORY")
        return self


def load_settings(config_path: str = "config.yaml") -> Settings:
    from dotenv import load_dotenv
    load_dotenv()

    config_file = Path(config_path)
    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}

    settings = Settings(**data)
    settings.apply_env_overrides()

    # Ensure required directories exist
    Path(settings.uploads.directory).mkdir(parents=True, exist_ok=True)
    Path(settings.index.directory).mkdir(parents=True, exist_ok=True)
    Path(settings.skills.directory).mkdir(parents=True, exist_ok=True)
    if settings.file_operations.backup_enabled:
        Path(settings.file_operations.backup_directory).mkdir(parents=True, exist_ok=True)

    return settings


settings: Settings = load_settings()
