import os
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse, urlunparse, quote

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings


def build_proxy_url(
    proxy_url: str,
    proxy_username: str = "",
    proxy_password: str = ""
) -> Optional[str]:
    """
    Baut eine vollständige Proxy-URL inkl. Auth.

    Akzeptiert verschiedene Formate:
    - proxy.intern:8080           → http://proxy.intern:8080
    - http://proxy.intern:8080    → http://proxy.intern:8080
    - https://proxy.intern:8080   → https://proxy.intern:8080

    Mit Username/Password:
    - proxy.intern:8080 + user + pass → http://user:pass@proxy.intern:8080

    Args:
        proxy_url: Proxy-URL (mit oder ohne Schema)
        proxy_username: Optional - Benutzername für Proxy-Auth
        proxy_password: Optional - Passwort für Proxy-Auth

    Returns:
        Vollständige Proxy-URL oder None wenn leer
    """
    if not proxy_url:
        return None

    url = proxy_url.strip()

    # Schema ergänzen wenn nicht vorhanden
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"

    parsed = urlparse(url)

    # Hostname und Port extrahieren
    hostname = parsed.hostname or ""
    port = parsed.port

    if not hostname:
        return None

    # Mit Auth?
    if proxy_username and proxy_password:
        # Credentials URL-encoden (für Sonderzeichen)
        user = quote(proxy_username, safe="")
        passwd = quote(proxy_password, safe="")
        auth_netloc = f"{user}:{passwd}@{hostname}"
        if port:
            auth_netloc += f":{port}"
        return urlunparse((parsed.scheme or "http", auth_netloc, "", "", "", ""))

    # Ohne Auth - URL normalisiert zurückgeben
    netloc = hostname
    if port:
        netloc += f":{port}"
    return urlunparse((parsed.scheme or "http", netloc, "", "", "", ""))


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
    # Phase-spezifische Temperature: Tool-Phase deterministisch (0.0), Analyse-Phase präzise (0.1)
    tool_temperature: float = 0.0       # Temperature für Tool-Call-Phase (deterministisch)
    analysis_temperature: float = 0.1   # Temperature für Analyse-Phase (niedrig für präzise Faktenextraktion)
    # LLM-spezifische Kontext-Limits in Tokens (für automatisches Trimmen)
    # z.B. {"mistral-678b": 32000, "qwen-7b": 8000, "gptoss120b": 64000}
    llm_context_limits: Dict[str, int] = {}
    # Standard-Kontext-Limit falls kein LLM-spezifisches definiert ist
    default_context_limit: int = 32000


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
    # API-Pfad: "wiki" für Cloud, "" für manche Server, oder custom
    # Beispiele: "" → /rest/api, "wiki" → /wiki/rest/api, "confluence" → /confluence/rest/api
    api_path: str = ""  # Leer = auto-detect, sonst z.B. "wiki" oder "confluence"


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
    # Java-Pfad für WLP (JAVA_HOME). Leer = System-Default
    java_home: str = ""


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
    mvn_executable: str = "mvn"    # Pfad zum mvn-Binary (z.B. C:/maven/bin/mvn oder /opt/maven/bin/mvn)
    java_home: str = ""            # JAVA_HOME für Maven. Leer = System-Default
    settings_file: str = ""        # Maven User Settings (settings.xml). Leer = Default (~/.m2/settings.xml)
    local_repo: str = ""           # Lokales Repository. Leer = Default (~/.m2/repository)
    builds: List[MavenBuild] = []
    default_timeout_minutes: int = 15


# ══════════════════════════════════════════════════════════════════════════════
# Web Search
# ══════════════════════════════════════════════════════════════════════════════

class WebSearchConfig(BaseModel):
    """Internet-Recherche mit Nutzer-Bestätigungspflicht."""
    enabled: bool = False
    # Proxy-Konfiguration für Internet-Zugriff
    proxy_url: str = ""              # z.B. http://proxy.intern:8080 oder proxy.intern:8080
    proxy_username: str = ""         # Proxy-Benutzername (optional)
    proxy_password: str = ""         # Proxy-Passwort (optional)
    no_proxy: str = ""               # Kommagetrennte Liste ohne Proxy (z.B. "localhost,127.0.0.1,.intern")
    verify_ssl: bool = True          # SSL-Zertifikate prüfen (False für selbstsignierte Proxy-Zertifikate)
    timeout_seconds: int = 30        # Timeout für HTTP-Requests

    def get_proxy_url(self) -> Optional[str]:
        """Gibt die vollständige Proxy-URL inkl. Auth zurück."""
        return build_proxy_url(self.proxy_url, self.proxy_username, self.proxy_password)


# ══════════════════════════════════════════════════════════════════════════════
# HTML Processing (für Internal Fetch)
# ══════════════════════════════════════════════════════════════════════════════

class HtmlProcessingConfig(BaseModel):
    """Konfiguration für HTML-Verarbeitung bei Internal Fetch."""
    enabled: bool = True                     # HTML automatisch parsen
    default_extract_mode: str = "text"       # text | structured | full
    max_output_length: int = 30000           # Max. Zeichen im Output
    chunk_size: int = 8000                   # Chunk-Größe für große Seiten
    chunk_overlap: int = 200                 # Überlappung zwischen Chunks
    remove_navigation: bool = True           # Nav/Header/Footer entfernen
    # Elemente die entfernt werden (CSS-Selektoren)
    remove_selectors: List[str] = [
        "script", "style", "nav", "footer",
        ".sidebar", ".advertisement", ".ad",
        "#cookie-banner", "#cookie-consent",
    ]
    # Elemente die erhalten bleiben (Whitelist, leer = alles)
    preserve_selectors: List[str] = []


# ══════════════════════════════════════════════════════════════════════════════
# API Tools (SOAP und REST)
# ══════════════════════════════════════════════════════════════════════════════

class SoapConfig(BaseModel):
    """SOAP-spezifische Konfiguration."""
    default_timeout: int = 30          # Timeout für SOAP-Requests
    cache_wsdl: bool = True            # WSDL-Definitionen cachen
    cache_ttl_minutes: int = 60        # Cache-Gültigkeitsdauer
    verify_ssl: bool = True            # SSL-Zertifikate prüfen


class RestConfig(BaseModel):
    """REST-spezifische Konfiguration."""
    default_timeout: int = 30          # Timeout für REST-Requests
    auto_format_response: bool = True  # JSON/XML automatisch formatieren
    max_response_size_kb: int = 500    # Max. Response-Größe
    verify_ssl: bool = True            # SSL-Zertifikate prüfen


class ApiToolsConfig(BaseModel):
    """Konfiguration für SOAP und REST API Tools."""
    enabled: bool = True               # API Tools aktivieren
    soap: SoapConfig = Field(default_factory=SoapConfig)
    rest: RestConfig = Field(default_factory=RestConfig)


# ══════════════════════════════════════════════════════════════════════════════
# Compile Tool - Validierung und Kompilierung
# ══════════════════════════════════════════════════════════════════════════════

class CompilePythonConfig(BaseModel):
    """Python-Validator Konfiguration."""
    enabled: bool = True
    linter: str = "ruff"               # ruff | flake8 | none
    type_checker: str = "none"         # mypy | none
    auto_fix_tool: str = "ruff"        # ruff | autopep8
    ignore_rules: List[str] = []       # z.B. ["E501", "W503"]


class CompileJavaConfig(BaseModel):
    """Java-Validator Konfiguration."""
    enabled: bool = True
    mode: str = "quick"                # quick | maven | gradle
    java_home: str = ""                # JAVA_HOME (leer = System)
    javac_options: str = "-Xlint:all"


class CompileSQLConfig(BaseModel):
    """SQL-Validator Konfiguration."""
    enabled: bool = True
    dialect: str = "db2"               # db2 | postgres | mysql | ansi
    check_best_practices: bool = True


class CompileSQLJConfig(BaseModel):
    """SQLJ-Validator Konfiguration."""
    enabled: bool = True
    sqlj_path: str = ""                # Pfad zum SQLJ Translator


class CompileXMLConfig(BaseModel):
    """XML-Validator Konfiguration."""
    enabled: bool = True
    validate_schemas: bool = True


class CompileConfigConfig(BaseModel):
    """Config-Validator Konfiguration."""
    enabled: bool = True
    formats: List[str] = ["yaml", "json", "properties", "toml"]


class CompileToolConfig(BaseModel):
    """Compile/Validate Tool Konfiguration."""
    enabled: bool = True
    default_changed_only: bool = True   # Nur geänderte Dateien
    default_fix: bool = False           # Auto-Fix deaktiviert
    default_strict: bool = False        # Warnings nicht als Errors
    timeout_per_file_seconds: int = 30
    total_timeout_seconds: int = 300
    # Validator-spezifische Konfiguration
    python: CompilePythonConfig = Field(default_factory=CompilePythonConfig)
    java: CompileJavaConfig = Field(default_factory=CompileJavaConfig)
    sql: CompileSQLConfig = Field(default_factory=CompileSQLConfig)
    sqlj: CompileSQLJConfig = Field(default_factory=CompileSQLJConfig)
    xml: CompileXMLConfig = Field(default_factory=CompileXMLConfig)
    config: CompileConfigConfig = Field(default_factory=CompileConfigConfig)


# ══════════════════════════════════════════════════════════════════════════════
# JUnit Test Generator
# ══════════════════════════════════════════════════════════════════════════════

class JUnitToolConfig(BaseModel):
    """JUnit Test Generator Konfiguration."""
    enabled: bool = True
    default_version: str = "5"          # JUnit 4 oder 5
    default_style: str = "auto"         # auto, basic, mockito, spring
    test_suffix: str = "Test"           # Suffix für Test-Klassen
    generate_negative_tests: bool = True
    generate_edge_cases: bool = True
    use_given_when_then: bool = True    # Given-When-Then Kommentare
    add_todo_comments: bool = True      # TODO-Marker für manuelle Ergänzungen


# ══════════════════════════════════════════════════════════════════════════════
# Prompt Templates (User-definierte Vorlagen)
# ══════════════════════════════════════════════════════════════════════════════

class PromptTemplate(BaseModel):
    """Eine Prompt-Vorlage für häufige Anwendungsfälle."""
    id: str = ""                        # Eindeutige ID
    name: str = ""                      # Anzeigename
    description: str = ""               # Kurzbeschreibung für Tooltip
    icon: str = ""                      # Icon-Name (z.B. "search", "database", "book")
    category: str = "general"           # Kategorie: general, search, debug, analysis
    prompt: str = ""                    # Der eigentliche Prompt mit {{placeholders}}
    placeholders: List[str] = []        # Liste der Platzhalter (z.B. ["suchbegriff", "dateifilter"])
    is_builtin: bool = False            # True = System-Template, nicht löschbar
    sort_order: int = 0                 # Sortierung in der UI


class PromptTemplatesConfig(BaseModel):
    """Konfiguration für Prompt-Templates."""
    enabled: bool = True
    show_in_chat_header: bool = True    # Templates über dem Chat anzeigen
    max_recent: int = 5                 # Max. kürzlich verwendete Templates
    templates: List[PromptTemplate] = Field(default_factory=lambda: [
        # === Code Search Templates ===
        PromptTemplate(
            id="code_search_class",
            name="Klasse finden",
            description="Suche nach einer Java/Python-Klasse im Code",
            icon="search",
            category="search",
            prompt="Suche im Code nach der Klasse oder dem Service '{{klassenname}}'. Zeige mir:\n1. Wo die Klasse definiert ist\n2. Die wichtigsten Methoden\n3. Abhängigkeiten und Verwendungen",
            placeholders=["klassenname"],
            is_builtin=True,
            sort_order=1
        ),
        PromptTemplate(
            id="code_search_function",
            name="Funktion/Methode finden",
            description="Suche nach einer bestimmten Funktion oder Methode",
            icon="search",
            category="search",
            prompt="Finde die Methode oder Funktion '{{methodenname}}' im Code. Zeige:\n1. Die vollständige Implementierung\n2. Wo sie aufgerufen wird\n3. Parameter und Rückgabewerte",
            placeholders=["methodenname"],
            is_builtin=True,
            sort_order=2
        ),
        PromptTemplate(
            id="code_search_pattern",
            name="Code-Muster suchen",
            description="Suche nach einem bestimmten Code-Pattern",
            icon="search",
            category="search",
            prompt="Suche im Code nach dem Muster '{{suchmuster}}'. Filter: {{dateifilter}}.\nZeige alle Fundstellen mit Kontext.",
            placeholders=["suchmuster", "dateifilter"],
            is_builtin=True,
            sort_order=3
        ),
        # === Confluence Templates ===
        PromptTemplate(
            id="confluence_search",
            name="Confluence durchsuchen",
            description="Suche in der Confluence-Dokumentation",
            icon="book",
            category="search",
            prompt="Durchsuche die Confluence-Dokumentation nach '{{suchbegriff}}'. Fasse die wichtigsten Ergebnisse zusammen und gib Links zu den relevanten Seiten.",
            placeholders=["suchbegriff"],
            is_builtin=True,
            sort_order=10
        ),
        PromptTemplate(
            id="confluence_service_doc",
            name="Service-Doku finden",
            description="Finde die Dokumentation zu einem Service",
            icon="book",
            category="search",
            prompt="Finde die Confluence-Dokumentation zum Service '{{servicename}}'. Zeige:\n1. Übersicht und Zweck\n2. API/Schnittstellen\n3. Konfiguration\n4. Bekannte Probleme",
            placeholders=["servicename"],
            is_builtin=True,
            sort_order=11
        ),
        # === Database Debugging Templates ===
        PromptTemplate(
            id="db_debug_table",
            name="Tabelle analysieren",
            description="Analysiere eine Datenbanktabelle",
            icon="database",
            category="debug",
            prompt="Analysiere die Datenbanktabelle '{{tabellenname}}':\n1. Zeige die Struktur (Spalten, Typen, Keys)\n2. Führe aus: SELECT * FROM {{tabellenname}} WHERE {{bedingung}} FETCH FIRST 20 ROWS ONLY\n3. Erkläre die Daten",
            placeholders=["tabellenname", "bedingung"],
            is_builtin=True,
            sort_order=20
        ),
        PromptTemplate(
            id="db_debug_query",
            name="Query ausführen & erklären",
            description="Führe eine SQL-Query aus und erkläre das Ergebnis",
            icon="database",
            category="debug",
            prompt="Führe folgende SQL-Query aus und erkläre das Ergebnis:\n\n```sql\n{{query}}\n```\n\nZeige:\n1. Das Ergebnis formatiert als Tabelle\n2. Auffälligkeiten in den Daten\n3. Mögliche Probleme",
            placeholders=["query"],
            is_builtin=True,
            sort_order=21
        ),
        PromptTemplate(
            id="db_debug_trace",
            name="Datensatz verfolgen",
            description="Verfolge einen Datensatz durch mehrere Tabellen",
            icon="database",
            category="debug",
            prompt="Verfolge den Datensatz mit {{schluesselfeld}} = '{{wert}}' durch die relevanten Tabellen.\n1. Zeige alle zugehörigen Einträge\n2. Erkläre die Beziehungen\n3. Finde eventuelle Inkonsistenzen",
            placeholders=["schluesselfeld", "wert"],
            is_builtin=True,
            sort_order=22
        ),
        # === Analysis Templates ===
        PromptTemplate(
            id="analyze_error",
            name="Fehler analysieren",
            description="Analysiere einen Fehler oder eine Exception",
            icon="bug",
            category="debug",
            prompt="Analysiere diesen Fehler:\n\n```\n{{fehlermeldung}}\n```\n\n1. Was ist die Ursache?\n2. Wo im Code tritt er auf?\n3. Wie kann er behoben werden?",
            placeholders=["fehlermeldung"],
            is_builtin=True,
            sort_order=30
        ),
        PromptTemplate(
            id="analyze_log",
            name="Log analysieren",
            description="Analysiere Log-Einträge nach Problemen",
            icon="file-text",
            category="debug",
            prompt="Analysiere diese Log-Einträge:\n\n```\n{{loginhalt}}\n```\n\n1. Finde Fehler und Warnungen\n2. Erkläre den Ablauf\n3. Identifiziere mögliche Probleme",
            placeholders=["loginhalt"],
            is_builtin=True,
            sort_order=31
        ),
    ])


# ══════════════════════════════════════════════════════════════════════════════
# Internal Fetch (Intranet-URLs abrufen)
# ══════════════════════════════════════════════════════════════════════════════

class InternalFetchConfig(BaseModel):
    """Konfiguration für das Abrufen interner/Intranet-URLs."""
    enabled: bool = False
    base_urls: List[str] = []        # Erlaubte URL-Prefixe (Sicherheit)
    verify_ssl: bool = True          # SSL-Zertifikate prüfen
    timeout_seconds: int = 30        # Timeout für HTTP-Requests
    # Authentifizierung
    auth_type: str = "none"          # "none", "basic", "bearer"
    auth_username: str = ""          # Benutzername für Basic Auth
    auth_password: str = ""          # Passwort für Basic Auth
    auth_token: str = ""             # Bearer Token
    # Proxy-Konfiguration
    proxy_url: str = ""              # Proxy für interne Requests (optional)
    proxy_username: str = ""         # Proxy-Benutzername (optional)
    proxy_password: str = ""         # Proxy-Passwort (optional)
    # HTML Processing
    html_processing: HtmlProcessingConfig = Field(default_factory=HtmlProcessingConfig)

    def get_proxy_url(self) -> Optional[str]:
        """Gibt die vollständige Proxy-URL inkl. Auth zurück."""
        return build_proxy_url(self.proxy_url, self.proxy_username, self.proxy_password)


# ══════════════════════════════════════════════════════════════════════════════
# Jenkins (intern gehostet)
# ══════════════════════════════════════════════════════════════════════════════

class JenkinsJobPath(BaseModel):
    """Ein Jenkins Job-Pfad (Ordner-Struktur)."""
    name: str = ""                  # Anzeigename (z.B. "OSPE", "PKP")
    path: str = ""                  # Pfad relativ zur base_url (z.B. "job/Verbund/job/OSPE")


class JenkinsConfig(BaseModel):
    """Jenkins CI/CD Server Konfiguration (intern gehostet)."""
    enabled: bool = False
    base_url: str = ""              # z.B. http://jenkins.intern:8080
    username: str = ""              # Jenkins-Benutzername
    api_token: str = ""             # Jenkins API-Token (statt Passwort)
    verify_ssl: bool = False        # False für interne Server mit Self-Signed Certs
    # Job-Pfade (Ordner-Struktur in Jenkins)
    job_paths: List[JenkinsJobPath] = []  # z.B. [{"name": "OSPE", "path": "job/Verbund/job/OSPE"}]
    default_job_path: str = ""      # Name des Standard-Pfads
    job_filter: str = ""            # Optionaler Prefix-Filter (z.B. "MyProject-")
    timeout_seconds: int = 30       # Timeout für API-Calls
    # Sicherheit: Build-Trigger benötigt Bestätigung
    require_build_confirmation: bool = True


# ══════════════════════════════════════════════════════════════════════════════
# GitHub Enterprise (intern gehostet)
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# MCP (Model Context Protocol) - Lokale Implementation
# ══════════════════════════════════════════════════════════════════════════════

class MCPServerEntry(BaseModel):
    """Ein lokaler MCP-Server-Eintrag."""
    id: str = ""
    name: str = ""
    description: str = ""
    command: str = ""                  # Ausführbares Kommando (z.B. "python", "node")
    args: List[str] = []               # Argumente für das Kommando
    env: Dict[str, str] = {}           # Zusätzliche Umgebungsvariablen
    working_dir: str = ""              # Arbeitsverzeichnis (leer = aktuelles)
    timeout_seconds: int = 30          # Timeout pro Request
    auto_start: bool = True            # Server beim App-Start starten


class MCPConfig(BaseModel):
    """MCP (Model Context Protocol) Konfiguration - Lokale Implementation."""
    enabled: bool = False
    servers: List[MCPServerEntry] = []
    # Sequential Thinking (lokale Implementation)
    sequential_thinking_enabled: bool = True
    max_thinking_steps: int = 10       # Max. Denkschritte pro Anfrage
    thinking_timeout_seconds: int = 120
    # Wann Sequential Thinking automatisch aktivieren
    auto_activate_on_error: bool = True     # Bei komplexen Fehlern
    auto_activate_on_planning: bool = True  # Bei Planungsaufgaben
    min_complexity_score: float = 0.7       # Komplexitätsschwelle (0.0-1.0)
    # Debug
    debug_logging: bool = False


# ══════════════════════════════════════════════════════════════════════════════
# Docker Sandbox (Sichere Code-Ausführung)
# ══════════════════════════════════════════════════════════════════════════════

class DockerSandboxConfig(BaseModel):
    """Docker/Podman Sandbox für sichere Python-Code-Ausführung."""
    enabled: bool = False
    backend: str = "auto"                  # "auto" | "docker" | "podman"
    # Pfade zu den Container-Runtimes (leer = aus PATH)
    docker_path: str = ""                  # z.B. "C:/Program Files/Docker/docker.exe"
    podman_path: str = ""                  # z.B. "C:/podman/bin/podman.exe" (portable)
    image: str = "python:3.11-slim"       # Base-Image (oder custom mit Paketen)
    custom_image: str = ""                 # Custom Image mit vorinstallierten Paketen
    # Ressourcen-Limits
    memory_limit: str = "512m"             # Max RAM (z.B. "256m", "512m", "1g")
    cpu_limit: float = 1.0                 # Max CPU-Cores (z.B. 0.5, 1.0, 2.0)
    timeout_seconds: int = 60              # Max Ausführungszeit
    max_output_bytes: int = 131072         # Max Output-Größe (128KB)
    # Netzwerk
    network_enabled: bool = True           # Lesender Netzwerkzugriff (für requests etc.)
    # Session-Management
    session_enabled: bool = True           # Variablen zwischen Aufrufen erhalten
    session_timeout_minutes: int = 30      # Session-Timeout nach Inaktivität
    max_sessions: int = 5                  # Max gleichzeitige Sessions
    # Datei-Upload
    file_upload_enabled: bool = True       # Dateien in Container hochladen
    max_upload_size_mb: int = 10           # Max Upload-Größe pro Datei
    upload_directory: str = "./sandbox_uploads"  # Temporäres Upload-Verzeichnis
    # Vorinstallierte Pakete (bei Nutzung von python:slim werden diese installiert)
    preinstalled_packages: List[str] = [
        "requests",
        "pandas",
        "numpy",
        "cryptography",
        "beautifulsoup4",
        "lxml",
        "pillow",
        "pyyaml",
        "python-dateutil",
        "chardet",
    ]
    # Sicherheit
    read_only_filesystem: bool = False     # Read-only Root (kann pip install verhindern)
    drop_capabilities: bool = True         # Alle Linux Capabilities entfernen


class GitHubConfig(BaseModel):
    """GitHub Enterprise Server Konfiguration (intern gehostet)."""
    enabled: bool = False
    base_url: str = ""              # z.B. https://github.intern.example.com
    token: str = ""                 # Personal Access Token
    verify_ssl: bool = False        # False für interne Server mit Self-Signed Certs
    default_org: str = ""           # Standard-Organisation für Repo-Listen
    default_repo: str = ""          # Standard-Repository (Format: org/repo)
    timeout_seconds: int = 30       # Timeout für API-Calls
    max_items: int = 50             # Max. Items bei Listen (PRs, Issues, etc.)
    # Filter für relevante Daten
    pr_state_filter: str = "open"   # open | closed | all
    issue_state_filter: str = "open"  # open | closed | all

    def get_api_url(self) -> str:
        """Gibt die API-URL zurück (base_url + /api/v3)."""
        if self.base_url:
            return f"{self.base_url.rstrip('/')}/api/v3"
        return ""


class AccessLoggingConfig(BaseModel):
    """Konfiguration für das External Access Logging."""
    enabled: bool = True               # Access-Logging aktiviert
    log_directory: str = ""            # Verzeichnis für Logs (default: index/access_logs)
    max_age_days: int = 90             # Auto-Cleanup nach X Tagen
    log_request_body: bool = False     # Request-Body loggen (Privacy!)
    log_response_body: bool = False    # Response-Body loggen (Performance!)
    exclude_hosts: List[str] = []      # Hosts die nicht geloggt werden


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
    search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    jenkins: JenkinsConfig = Field(default_factory=JenkinsConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    internal_fetch: InternalFetchConfig = Field(default_factory=InternalFetchConfig)
    docker_sandbox: DockerSandboxConfig = Field(default_factory=DockerSandboxConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    api_tools: ApiToolsConfig = Field(default_factory=ApiToolsConfig)
    compile_tool: CompileToolConfig = Field(default_factory=CompileToolConfig)
    junit_tool: JUnitToolConfig = Field(default_factory=JUnitToolConfig)
    prompt_templates: PromptTemplatesConfig = Field(default_factory=PromptTemplatesConfig)
    access_logging: AccessLoggingConfig = Field(default_factory=AccessLoggingConfig)

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
    if settings.docker_sandbox.enabled and settings.docker_sandbox.file_upload_enabled:
        Path(settings.docker_sandbox.upload_directory).mkdir(parents=True, exist_ok=True)

    return settings


settings: Settings = load_settings()
