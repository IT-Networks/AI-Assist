import os
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings


class ModelEntry(BaseModel):
    id: str
    display_name: str


class DataSourceParam(BaseModel):
    """Ein Parameter für ein Datenquellen-Tool."""
    name: str = ""
    type: str = "string"       # string, number, boolean, object
    description: str = ""
    required: bool = False
    location: str = "query"    # query, body, path, header


class DataSourceAuthConfig(BaseModel):
    """Authentifizierungskonfiguration für eine Datenquelle."""
    type: str = "none"         # none, basic, bearer, api_key
    username: str = ""
    password: str = ""
    bearer_token: str = ""
    api_key_header: str = "X-API-Key"
    api_key_value: str = ""


class DataSourceConfig(BaseModel):
    """Konfiguration einer internen Datenquelle (HTTP-basiert)."""
    id: str = ""
    name: str = ""
    description: str = ""      # Was ist diese Datenquelle?
    base_url: str = ""         # z.B. http://jenkins.intern:8080
    verify_ssl: bool = True    # False für interne Systeme mit selbstsignierten Certs
    auth: DataSourceAuthConfig = Field(default_factory=DataSourceAuthConfig)
    custom_headers: Dict[str, str] = {}
    # KI-generierte Tool-Definition
    tool_description: str = ""  # Was kann das Tool, wie funktioniert es?
    tool_usage: str = ""        # Wann sollte der Agent dieses Tool verwenden?
    endpoint_path: str = ""     # Standard-Endpunkt (z.B. /api/json)
    method: str = "GET"
    parameters: List[DataSourceParam] = []
    explored: bool = False      # Wurde KI-Erkundung bereits durchgeführt?


class DataSourcesConfig(BaseModel):
    """Container für alle konfigurierten Datenquellen."""
    sources: List[DataSourceConfig] = []


class LLMConfig(BaseModel):
    base_url: str = "http://localhost/v1"
    api_key: str = "none"
    default_model: str = "gptoss120b"
    timeout_seconds: int = 120
    max_tokens: int = 4096
    temperature: float = 0.2
    verify_ssl: bool = True  # False für selbstsignierte Zertifikate
    streaming: bool = True  # True für Token-Streaming bei Antworten
    # Modell-Aufteilung für Agent
    tool_model: str = ""  # Schnelles Modell für Tool-Aufrufe/Suche (leer = default_model)
    analysis_model: str = ""  # Größeres Modell für Analyse/Antwort (leer = default_model)
    # Pro-Tool Modell-Zuweisung: {"tool_name": "model_id"} (leer = tool_model oder default_model)
    tool_models: Dict[str, str] = {}


class RepoEntry(BaseModel):
    """Ein Repository-Eintrag."""
    name: str  # Anzeigename
    path: str  # Pfad zum Repository


class JavaConfig(BaseModel):
    repo_path: str = ""  # Aktiver Repo-Pfad (Kompatibilität)
    repos: List[RepoEntry] = []  # Liste aller Repos
    active_repo: str = ""  # Name des aktiven Repos
    exclude_dirs: List[str] = ["target", ".git", "node_modules", ".idea"]
    max_file_size_kb: int = 500

    def get_active_path(self) -> str:
        """Gibt den Pfad des aktiven Repos zurück."""
        # Wenn active_repo gesetzt ist, suche in repos Liste
        if self.active_repo and self.repos:
            for repo in self.repos:
                if repo.name == self.active_repo:
                    return repo.path
        # Fallback auf repo_path (Kompatibilität)
        return self.repo_path


class ConfluenceConfig(BaseModel):
    base_url: str = ""
    username: str = ""
    api_token: str = ""   # Atlassian Cloud API Token (bevorzugt)
    password: str = ""    # Atlassian Server/DC Passwort (Fallback wenn api_token leer)
    default_space: str = ""
    verify_ssl: bool = True  # False für selbstsignierte Zertifikate


class PythonConfig(BaseModel):
    repo_path: str = ""  # Aktiver Repo-Pfad (Kompatibilität)
    repos: List[RepoEntry] = []  # Liste aller Repos
    active_repo: str = ""  # Name des aktiven Repos
    exclude_dirs: List[str] = ["__pycache__", ".venv", ".git", "node_modules", ".mypy_cache", ".pytest_cache", "dist", "build"]
    max_file_size_kb: int = 500

    def get_active_path(self) -> str:
        """Gibt den Pfad des aktiven Repos zurück."""
        if self.active_repo and self.repos:
            for repo in self.repos:
                if repo.name == self.active_repo:
                    return repo.path
        return self.repo_path


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
    db_schema: str = ""  # Renamed from 'schema' to avoid Pydantic conflict
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


class JiraConfig(BaseModel):
    """Konfiguration für Jira-Anbindung."""
    enabled: bool = False
    base_url: str = ""  # z.B. https://jira.example.com
    username: str = ""
    api_token: str = ""   # Atlassian Cloud API Token (bevorzugt)
    password: str = ""    # Server/DC Passwort (Fallback)
    default_project: str = ""  # Standard-Projektschlüssel (z.B. "PROJ")
    verify_ssl: bool = True  # False für selbstsignierte Zertifikate


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
    chats_directory: str = "./chats"


class SubAgentsConfig(BaseModel):
    """Konfiguration für das Sub-Agenten-System."""
    enabled: bool = True
    timeout_seconds: int = 30           # Timeout pro Sub-Agent
    max_iterations: int = 5             # Max Tool-Calls pro Sub-Agent
    min_query_length: int = 15          # Kürzere Queries überspringen
    routing_model: str = ""             # Modell für Intent-Routing (leer = tool_model)
    agents: List[str] = Field(
        default_factory=lambda: [
            "code_explorer",
            "wiki_agent",
            "jira_agent",
            "database_agent",
            "knowledge_agent",
            "datasource_agent",
        ]
    )


# ══════════════════════════════════════════════════════════════════════════════
# MQ Series
# ══════════════════════════════════════════════════════════════════════════════

class MQQueue(BaseModel):
    """Definition einer MQ-Queue."""
    id: str = ""
    name: str = ""
    description: str = ""          # Was macht diese Queue?
    url: str = ""                  # HTTP-Endpunkt zum Abrufen/Einspielen
    method: str = "GET"            # GET = lesen, POST/PUT = einspielen
    service: str = ""              # Zugehöriger Service / was er triggert oder liest
    role: str = "read"             # read | trigger | both
    headers: Dict[str, str] = {}   # Feste HTTP-Header je Queue
    body_template: str = ""        # JSON-Template für PUT/POST (Platzhalter: {{key}})
    verify_ssl: bool = True
    timeout_seconds: int = 30


class MQConfig(BaseModel):
    """MQ-Series Konfiguration."""
    enabled: bool = False
    queues: List[MQQueue] = []


# ══════════════════════════════════════════════════════════════════════════════
# Test Tool
# ══════════════════════════════════════════════════════════════════════════════

class TestStageUrl(BaseModel):
    """Eine URL innerhalb einer Stage."""
    url: str = ""
    description: str = ""


class TestStage(BaseModel):
    """Eine Deployment-Stage (Dev, Test, Prod …)."""
    id: str = ""
    name: str = ""
    urls: List[TestStageUrl] = []


class TestServiceParam(BaseModel):
    """Ein Parameter eines Services."""
    name: str = ""
    type: str = "string"           # string | number | boolean | object | array
    description: str = ""
    required: bool = False
    default: str = ""
    location: str = "body"         # body | query | path | header


class TestService(BaseModel):
    """Definition eines testbaren Services."""
    id: str = ""
    name: str = ""
    description: str = ""
    endpoint: str = ""             # z.B. /api/orders
    method: str = "POST"
    content_type: str = "application/json"
    parameters: List[TestServiceParam] = []
    headers: Dict[str, str] = {}
    # Lokale Ausführung (Python/Java im Repo)
    local_script: str = ""         # Relativer Pfad zum Skript im aktiven Repo
    local_interpreter: str = ""    # python | java | mvn | …


class TestToolConfig(BaseModel):
    """Test-Tool Konfiguration."""
    enabled: bool = False
    stages: List[TestStage] = []
    services: List[TestService] = []
    active_stage: str = ""         # ID der aktiven Stage
    default_timeout_seconds: int = 60
    local_wlp_url: str = ""        # Lokaler WLP-Server für direkte Testweiterleitung


# ══════════════════════════════════════════════════════════════════════════════
# Log Servers
# ══════════════════════════════════════════════════════════════════════════════

class LogServer(BaseModel):
    """Ein einzelner Log-Server innerhalb einer Stage."""
    id: str = ""
    name: str = ""
    url: str = ""                  # URL zum Log-Download (HTTP GET)
    description: str = ""
    headers: Dict[str, str] = {}
    verify_ssl: bool = True


class LogStage(BaseModel):
    """Eine Stage mit einem oder mehreren Log-Servern."""
    id: str = ""
    name: str = ""
    servers: List[LogServer] = []


class LogServersConfig(BaseModel):
    """Log-Server Konfiguration pro Stage."""
    enabled: bool = False
    stages: List[LogStage] = []
    # Anzahl der letzten Zeilen standardmäßig beim Download
    default_tail_lines: int = 500


# ══════════════════════════════════════════════════════════════════════════════
# WLP Server
# ══════════════════════════════════════════════════════════════════════════════

class WLPServerEntry(BaseModel):
    """Ein WLP-Server-Eintrag."""
    id: str = ""
    name: str = ""
    description: str = ""
    wlp_path: str = ""             # Pfad zum WLP-Installationsverzeichnis
    server_name: str = "defaultServer"
    start_timeout_seconds: int = 300
    extra_jvm_args: str = ""


class WLPConfig(BaseModel):
    """WebSphere Liberty Profile Konfiguration."""
    enabled: bool = False
    servers: List[WLPServerEntry] = []
    # Pfad zum aktiven Repo (für Artefakt-Prüfung, Fallback = java.get_active_path())
    repo_path: str = ""


# ══════════════════════════════════════════════════════════════════════════════
# Maven Build
# ══════════════════════════════════════════════════════════════════════════════

class MavenBuild(BaseModel):
    """Definition eines Maven-Builds."""
    id: str = ""
    name: str = ""
    description: str = ""
    pom_path: str = ""             # Absoluter oder relativer Pfad zur pom.xml
    goals: str = "clean install"
    profiles: List[str] = []
    skip_tests: bool = False
    jvm_args: str = ""             # z.B. -Xmx512m
    extra_args: str = ""           # Beliebige zusätzliche mvn-Argumente


class MavenConfig(BaseModel):
    """Maven-Build Konfiguration."""
    enabled: bool = False
    mvn_executable: str = "mvn"    # Pfad zum mvn-Binary
    builds: List[MavenBuild] = []
    default_timeout_minutes: int = 15


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
    jira: JiraConfig = JiraConfig()
    skills: SkillsConfig = SkillsConfig()
    file_operations: FileOperationsConfig = FileOperationsConfig()
    data_sources: DataSourcesConfig = Field(default_factory=DataSourcesConfig)
    sub_agents: SubAgentsConfig = Field(default_factory=SubAgentsConfig)
    mq: MQConfig = Field(default_factory=MQConfig)
    test_tool: TestToolConfig = Field(default_factory=TestToolConfig)
    log_servers: LogServersConfig = Field(default_factory=LogServersConfig)
    wlp: WLPConfig = Field(default_factory=WLPConfig)
    maven: MavenConfig = Field(default_factory=MavenConfig)

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
    Path(settings.server.chats_directory).mkdir(parents=True, exist_ok=True)
    if settings.file_operations.backup_enabled:
        Path(settings.file_operations.backup_directory).mkdir(parents=True, exist_ok=True)

    return settings


settings: Settings = load_settings()
