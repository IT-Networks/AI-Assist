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


# ══════════════════════════════════════════════════════════════════════════════
# Globale Proxy-Konfiguration (wird von allen Services verwendet)
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# Zentrale Credentials-Verwaltung
# ══════════════════════════════════════════════════════════════════════════════

class CredentialEntry(BaseModel):
    """
    Ein benanntes Credential-Set für zentrale Verwaltung.

    Kann von mehreren Services referenziert werden.
    """
    name: str = ""                   # Eindeutiger Name (z.B. "alm-prod", "jira-cloud")
    type: str = "basic"              # basic | bearer | api_key
    username: str = ""               # Für basic auth
    password: str = ""               # Für basic auth (SENSITIVE)
    token: str = ""                  # Für bearer/api_key (SENSITIVE)
    description: str = ""            # Optionale Beschreibung


class CredentialsConfig(BaseModel):
    """
    Zentrale Credentials-Verwaltung.

    Alle Services können auf diese Credentials per Name verweisen,
    statt eigene Username/Password-Felder zu verwenden.
    """
    credentials: List[CredentialEntry] = []

    def get(self, name: str) -> Optional[CredentialEntry]:
        """Gibt ein Credential-Entry nach Name zurück."""
        for cred in self.credentials:
            if cred.name == name:
                return cred
        return None

    def get_auth(self, name: str) -> tuple:
        """
        Gibt (username, password/token) für ein Credential zurück.

        Returns:
            (username, secret) oder ("", "") wenn nicht gefunden
        """
        cred = self.get(name)
        if not cred:
            return ("", "")

        if cred.type == "basic":
            return (cred.username, cred.password)
        elif cred.type == "bearer":
            return ("", cred.token)
        elif cred.type == "api_key":
            return ("", cred.token)
        return ("", "")


class ProxyConfig(BaseModel):
    """
    Zentrale Proxy-Konfiguration für alle externen HTTP-Verbindungen.

    Wird von Web-Suche, Update-Service, Internal-Fetch etc. verwendet.
    """
    enabled: bool = False            # Proxy global aktivieren
    url: str = ""                    # z.B. http://proxy.intern:8080
    username: str = ""               # Proxy-Benutzername (optional)
    password: str = ""               # Proxy-Passwort (optional)
    credential_ref: str = ""         # Referenz auf credentials.credentials[name="..."]
    no_proxy: str = ""               # Kommagetrennte Liste ohne Proxy (z.B. "localhost,127.0.0.1,.intern")
    verify_ssl: bool = True          # SSL-Zertifikate prüfen

    def get_proxy_url(self) -> Optional[str]:
        """Gibt die vollständige Proxy-URL inkl. Auth zurück."""
        if not self.enabled or not self.url:
            return None
        return build_proxy_url(self.url, self.username, self.password)


class ModelEntry(BaseModel):
    id: str
    display_name: str
    vision: bool = False
    ocr_model: bool = False  # OCR-Modelle unterstützen keine Tools


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


class LLMCacheConfig(BaseModel):
    """Konfiguration für LLM Response Caching."""
    enabled: bool = False              # Feature-Flag für Caching
    type: str = "local"                # "local" (in-memory) oder "redis"
    ttl_seconds: int = 300             # Cache TTL (5 Minuten default)
    max_size: int = 1000               # Max Einträge für local cache
    # Redis-Konfiguration (nur wenn type="redis")
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    # Cache-Kategorien aktivieren
    cache_routing: bool = True         # Sub-Agent Routing cachen
    cache_quick_calls: bool = True     # chat_quick() Calls cachen


class LLMConfig(BaseModel):
    base_url: str = "http://localhost/v1"
    api_key: str = "none"
    default_model: str = "gpt-oss-120b"
    timeout_seconds: int = 120
    max_tokens: int = 4096
    temperature: float = 0.2
    verify_ssl: bool = True  # False für selbstsignierte Zertifikate
    streaming: bool = True  # True für Token-Streaming bei Antworten
    # Modell-Aufteilung für Agent
    tool_model: str = ""  # Schnelles Modell für Tool-Aufrufe/Suche (leer = default_model)
    analysis_model: str = ""  # Größeres Modell für Analyse/Antwort (leer = default_model)
    complexity_model: str = ""  # Modell für Komplexitäts-Einschätzung (leer = tool_model)
    # Pro-Tool Modell-Zuweisung: {"tool_name": "model_id"} (leer = tool_model oder default_model)
    tool_models: Dict[str, str] = {}
    # Phase-spezifische Temperature: Tool-Phase deterministisch (0.0), Analyse-Phase präzise (0.1)
    tool_temperature: float = 0.0       # Temperature für Tool-Call-Phase (deterministisch)
    analysis_temperature: float = 0.1   # Temperature für Analyse-Phase (niedrig für präzise Faktenextraktion)
    # Reasoning-Support für GPT-OSS und ähnliche Modelle (o1, o3-mini)
    # Werte: "" (aus), "low", "medium", "high"
    reasoning_effort: str = ""          # Default-Reasoning für alle Calls (leer = aus)
    analysis_reasoning: str = "high"    # Reasoning für Analyse-Phase (komplexe Aufgaben)
    tool_reasoning: str = ""            # Reasoning für Tool-Phase (normalerweise aus)
    # Tool-Prefill: Fügt "[TOOL_CALLS]" als Assistant-Prefill hinzu um Modelle
    # in das richtige Output-Format zu zwingen (hilft bei Mistral/Qwen)
    use_tool_prefill: bool = False      # True aktiviert Prefill für alle Tool-Calls
    # Pro-Modell Prefill-Override: {"model_id": true/false}
    tool_prefill_models: Dict[str, bool] = {}
    # LLM-spezifische Kontext-Limits in Tokens (für automatisches Trimmen)
    # z.B. {"mistral-678b": 32000, "qwen-7b": 8000, "gpt-oss-120b": 64000}
    llm_context_limits: Dict[str, int] = {}
    # Standard-Kontext-Limit falls kein LLM-spezifisches definiert ist
    default_context_limit: int = 32000
    # Caching-Konfiguration
    cache: LLMCacheConfig = Field(default_factory=LLMCacheConfig)


class RepoEntry(BaseModel):
    """Ein Repository-Eintrag."""
    name: str  # Anzeigename
    path: str  # Pfad zum Repository


class JavaConfig(BaseModel):
    repo_path: str = ""  # Haupt-Repo-Pfad (Kompatibilität)
    repos: List[RepoEntry] = []  # Liste aller Repos
    active_repo: str = ""  # DEPRECATED: Wird ignoriert, alle Repos sind durchsuchbar
    exclude_dirs: List[str] = ["target", ".git", "node_modules", ".idea"]
    max_file_size_kb: int = 500

    def get_all_paths(self) -> List[str]:
        """Gibt alle konfigurierten Repo-Pfade zurück."""
        paths = []
        # Alle Repos aus der Liste
        for repo in self.repos:
            if repo.path:
                paths.append(repo.path)
        # repo_path als Fallback/zusätzlicher Pfad
        if self.repo_path and self.repo_path not in paths:
            paths.append(self.repo_path)
        return paths

    def get_active_path(self) -> str:
        """Gibt den ersten Repo-Pfad zurück (Abwärtskompatibilität)."""
        paths = self.get_all_paths()
        return paths[0] if paths else ""


class ConfluenceConfig(BaseModel):
    base_url: str = ""
    credential_ref: str = ""  # Referenz auf zentrale Credentials (bevorzugt)
    username: str = ""        # Direkt (Fallback wenn credential_ref leer)
    api_token: str = ""   # Bei leerem username: Bearer Token, sonst Basic Auth (username:api_token)
    password: str = ""    # Atlassian Server/DC Passwort (Fallback wenn api_token leer)
    default_space: str = ""
    verify_ssl: bool = True  # False für selbstsignierte Zertifikate
    # API-Pfad: "wiki" für Cloud, "" für manche Server, oder custom
    # Beispiele: "" → /rest/api, "wiki" → /wiki/rest/api, "confluence" → /confluence/rest/api
    api_path: str = ""  # Leer = auto-detect, sonst z.B. "wiki" oder "confluence"


class PythonConfig(BaseModel):
    repo_path: str = ""  # Haupt-Repo-Pfad (Kompatibilität)
    repos: List[RepoEntry] = []  # Liste aller Repos
    active_repo: str = ""  # DEPRECATED: Wird ignoriert, alle Repos sind durchsuchbar
    exclude_dirs: List[str] = ["__pycache__", ".venv", ".git", "node_modules", ".mypy_cache", ".pytest_cache", "dist", "build"]
    max_file_size_kb: int = 500

    def get_all_paths(self) -> List[str]:
        """Gibt alle konfigurierten Repo-Pfade zurück."""
        paths = []
        for repo in self.repos:
            if repo.path:
                paths.append(repo.path)
        if self.repo_path and self.repo_path not in paths:
            paths.append(self.repo_path)
        return paths

    def get_active_path(self) -> str:
        """Gibt den ersten Repo-Pfad zurück (Abwärtskompatibilität)."""
        paths = self.get_all_paths()
        return paths[0] if paths else ""


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
    structure_mode: str = "auto"  # auto | directory | flat
    # auto: Erkennt automatisch ob Unterordner existieren
    # directory: Erwartet funktionen/SERVICE_NAME/tab.htm
    # flat: Erwartet FUNKTIONSNAME_tabname.htm (alle Dateien in einem Ordner)
    functions_subdir: str = "funktionen"  # Subordner für Service-Funktionen (bei directory-Modus)
    fields_subdir: str = "felder"  # Subordner für Feld-Definitionen
    # Tab-Suffixe für flache Struktur (FUNKTION_suffix.htm)
    known_tab_suffixes: List[str] = [
        "statistik", "use_cases", "aenderungen", "dqm",
        "fachlich", "intern", "parameter", "uebersicht",
        "eingabe", "ausgabe", "allgemein", "technik",
        "historie", "beispiele", "varianten", "fehler"
    ]
    # Performance: Anzahl paralleler Threads für Netzwerk-I/O
    # Höhere Werte bei schnellem Netzwerk (16-32), niedriger bei langsamem (4-8)
    parallel_workers: int = 16


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
    credential_ref: str = ""  # Referenz auf zentrale Credentials (bevorzugt)
    username: str = ""        # Direkt (Fallback wenn credential_ref leer)
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
    credential_ref: str = ""  # Referenz auf zentrale Credentials (bevorzugt)
    username: str = ""        # Direkt (Fallback wenn credential_ref leer)
    api_token: str = ""   # Bei leerem username: Bearer Token, sonst Basic Auth (username:api_token)
    password: str = ""    # Server/DC Passwort (Fallback)
    default_project: str = ""  # Standard-Projektschlüssel (z.B. "PROJ")
    verify_ssl: bool = True  # False für selbstsignierte Zertifikate


class ALMConfig(BaseModel):
    """HP ALM/Quality Center Konfiguration für Testfall-Management."""
    enabled: bool = False
    base_url: str = ""                    # z.B. https://alm.company.com/qcbin
    credential_ref: str = ""              # Referenz auf zentrale Credentials (bevorzugt)
    username: str = ""                    # Direkt (Fallback wenn credential_ref leer)
    password: str = ""                    # SENSITIVE - wird maskiert
    domain: str = ""                      # ALM Domain (z.B. "DEFAULT")
    project: str = ""                     # ALM Project Name
    verify_ssl: bool = True
    timeout_seconds: int = 30
    # Session-Management
    session_cache_ttl: int = 3600         # Session-Cookie TTL in Sekunden (1 Stunde)
    auto_reconnect: bool = True           # Auto Re-Auth bei Session-Timeout
    # Verhalten
    require_confirmation: bool = True     # Bestätigung für Create/Update-Operationen
    default_test_type: str = "MANUAL"     # MANUAL | AUTOMATED
    # API-Format (für neuere ALM-Versionen 16+)
    prefer_json: bool = False             # True = JSON statt XML (nur ALM 16+)


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
    reload_excludes: List[str] = ["chats/*", "output/*", "generated/*", "*.pyc", "__pycache__/*"]
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

    @property
    def effective_url(self) -> str:
        """URL mit http:// Prefix falls kein Schema angegeben."""
        if not self.url:
            return self.url
        if self.url.startswith(("http://", "https://")):
            return self.url
        return f"http://{self.url}"


class MQConfig(BaseModel):
    """MQ-Series Konfiguration."""
    enabled: bool = False
    queues: List[MQQueue] = []


# ══════════════════════════════════════════════════════════════════════════════
# Test Tool
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# Test-Tool (SOAP Multi-Institut)
# ══════════════════════════════════════════════════════════════════════════════

class SoapInstitut(BaseModel):
    """Ein Institut mit eigenen Credentials und Session."""
    institut_nr: str = ""             # z.B. "001", "002", "100"
    name: str = ""                    # z.B. "Sparkasse Musterstadt"
    credential_ref: str = ""          # Referenz auf zentrale Credentials (bevorzugt)
    user: str = ""                    # Login-User (Fallback wenn credential_ref leer)
    password: str = ""                # Passwort (oder {{env:INST_001_PW}}, Fallback)
    enabled: bool = True


class SoapParameter(BaseModel):
    """Parameter-Definition für eine SOAP-Operation."""
    name: str = ""
    type: str = "string"              # string | integer | boolean | date | enum
    required: bool = False
    default: str = ""
    description: str = ""
    sensitive: bool = False           # Maskiert in Logs
    values: List[str] = []            # Erlaubte Werte für enum-Typ


class SoapOperation(BaseModel):
    """Eine Operation (Methode) eines SOAP-Services."""
    id: str = ""                      # z.B. "get_customer"
    name: str = ""                    # z.B. "GetCustomer"
    description: str = ""
    template_file: str = ""           # Relativer Pfad zum Template
    soap_action: str = ""             # SOAPAction HTTP-Header
    timeout_seconds: int = 60
    parameters: List[SoapParameter] = []
    # Response-Extraktion: XPath zu Feldern
    response_xpath: Dict[str, str] = {}


class SoapService(BaseModel):
    """Ein SOAP-Service mit mehreren Operationen."""
    id: str = ""                      # z.B. "customer"
    name: str = ""                    # z.B. "Kundenverwaltung"
    description: str = ""
    namespace: str = ""               # Target-Namespace
    soap_version: str = "1.1"         # 1.1 | 1.2
    # Login-Konfiguration
    login_template: str = "login.soap.xml"
    session_token_xpath: str = "//SessionToken/text()"
    session_expires_xpath: str = ""
    error_codes_requiring_reauth: List[str] = ["SESSION_EXPIRED", "INVALID_TOKEN"]
    # Operationen
    operations: List[SoapOperation] = []
    enabled: bool = True


class TestToolConfig(BaseModel):
    """Test-Tool Konfiguration (SOAP Multi-Institut)."""
    enabled: bool = False
    # EIN Endpunkt für alle Services
    service_url: str = ""             # z.B. "https://soap.example.com/services"
    login_url: str = ""               # z.B. "https://soap.example.com/auth/login"
    verify_ssl: bool = True
    # Globales Login-Template (für alle Institute)
    login_template: str = "login.soap.xml"
    session_token_xpath: str = "//SessionToken/text()"
    # Institute (Multi-Tenant)
    institute: List[SoapInstitut] = []
    # Services
    services: List[SoapService] = []
    templates_path: str = "data/test_tool/templates"
    # Session-Management (pro Institut)
    session_storage_file: str = "data/test_tool/sessions.json"
    session_refresh_before_expiry_seconds: int = 300


# ══════════════════════════════════════════════════════════════════════════════
# Log Servers
# ══════════════════════════════════════════════════════════════════════════════

class LogServer(BaseModel):
    """Ein einzelner Log-Server innerhalb einer Stage."""
    id: str = ""
    name: str = ""
    url: str = ""                  # Base-URL des Servers (z.B. host:port oder http://host:port)
    description: str = ""
    verify_ssl: bool = True

    @property
    def effective_url(self) -> str:
        """URL mit http:// Prefix falls kein Schema angegeben."""
        if not self.url:
            return self.url
        if self.url.startswith(("http://", "https://")):
            return self.url
        return f"http://{self.url}"


class LogStage(BaseModel):
    """Eine Stage mit einem oder mehreren Log-Servern."""
    id: str = ""
    name: str = ""
    servers: List[LogServer] = []


class LogServersConfig(BaseModel):
    """Log-Server Konfiguration pro Stage."""
    enabled: bool = False
    stages: List[LogStage] = []
    credential_ref: str = ""       # Referenz auf zentrale Credentials (basic auth)
    default_tail: int = 4          # tail-Parameter 0-4 (4 = längster tail)


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
    # Proxy wird jetzt global über settings.proxy konfiguriert
    timeout_seconds: int = 30        # Timeout für HTTP-Requests


# ══════════════════════════════════════════════════════════════════════════════
# Update Service (GitHub-basierte App-Updates)
# ══════════════════════════════════════════════════════════════════════════════

class UpdateConfig(BaseModel):
    """Konfiguration für GitHub-basierte App-Updates."""
    enabled: bool = False
    # GitHub Repository (öffentlich oder privat)
    repo_url: str = ""               # z.B. https://github.com/user/ai-assist-releases
    github_token: str = ""           # Personal Access Token für private Repos
    # Branch für Updates (leer = Releases/Tags verwenden, "main" = immer main-Branch)
    branch: str = ""                 # z.B. "main" für direkte Branch-Updates
    # Proxy verwenden (aus globaler proxy-Konfiguration)
    use_proxy: bool = True           # Globalen Proxy verwenden
    verify_ssl: bool = False         # SSL-Zertifikate prüfen (False für Corporate Proxies)
    timeout_seconds: int = 120       # Timeout für Downloads
    # Auto-Update
    check_on_start: bool = False     # Beim Start nach Updates suchen
    # Whitelist: Nur diese Pfade werden aktualisiert
    include_patterns: List[str] = Field(default_factory=lambda: [
        "app/**/*.py",
        "tests/**/*.py",
        "static/**/*",
        "templates/**/*",
        "requirements.txt",
        "main.py",
        "VERSION",
    ])
    # Blacklist: Diese Pfade werden NIE überschrieben
    exclude_patterns: List[str] = Field(default_factory=lambda: [
        "**/.env*",
        "**/config.yaml",
        "**/settings*.json",
        "data/**",              # Datenbanken, Analytics, Templates
        "index/**",             # Such-Indizes
        "uploads/**",           # Hochgeladene PDFs
        "chats/**",             # Chat-Verläufe
        "logs/**",              # Log-Dateien
        "backups/**",           # Backups
        "skills/**",            # Custom Skills
        "claudedocs/**",        # Claude-Dokumentation
        "sandbox_uploads/**",   # Sandbox-Uploads
        "scripts/**",           # Generierte Python-Scripte
        "htmlcov/**",           # Test-Coverage-Reports
        "**/*.db",              # Alle SQLite-Datenbanken
    ])

    # Proxy wird über settings.proxy.get_proxy_url() geholt


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
        # === Code Review Templates ===
        PromptTemplate(
            id="codereview_findings",
            name="Code Review bearbeiten",
            description="Bearbeite Code Review Findings aus einem Jira-Ticket",
            icon="bug",
            category="review",
            prompt="""Bearbeite die Code Review Findings aus dem Jira-Ticket.

## Aufgabe
Lies das Jira **{{jira_key}}** mit allen Subtasks (jedes Finding ist ein Subtask) und bearbeite diese Schritt für Schritt im Repository **{{repo}}**.

## Workflow
1. **Jira lesen**: `read_jira_issue(issue_key="{{jira_key}}", include_subtasks=true)`
2. **Übersicht zeigen**: Liste alle Findings mit Status
3. **Pro Finding**:
   - Subtask-Details lesen
   - Betroffene Datei(en) im Repo lesen
   - Fix erklären und bestätigen lassen
   - Änderung mit edit_file durchführen
4. **Abschluss**: Zusammenfassung + Git-Commit-Vorschlag

Starte jetzt mit dem Lesen des Jira-Tickets.""",
            placeholders=["jira_key", "repo"],
            is_builtin=True,
            sort_order=40
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
    credential_ref: str = ""         # Referenz auf zentrale Credentials (bevorzugt)
    auth_username: str = ""          # Direkt (Fallback wenn credential_ref leer)
    auth_password: str = ""          # Passwort für Basic Auth
    auth_token: str = ""             # Bearer Token
    # Proxy-Konfiguration
    proxy_url: str = ""              # Proxy für interne Requests (optional)
    proxy_credential_ref: str = ""   # Referenz auf zentrale Credentials für Proxy
    proxy_username: str = ""         # Direkt (Fallback)
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
    credential_ref: str = ""        # Referenz auf zentrale Credentials (bevorzugt)
    username: str = ""              # Direkt (Fallback wenn credential_ref leer)
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
# Sonatype IQ Server (Lifecycle) - Vulnerability/Policy Management
# ══════════════════════════════════════════════════════════════════════════════

class IQServerConfig(BaseModel):
    """Sonatype IQ Server (Lifecycle) Konfiguration für Findings und Waivers."""
    enabled: bool = False
    base_url: str = ""                  # z.B. https://iq.intern:8070
    credential_ref: str = ""            # Referenz auf zentrale Credentials (bevorzugt)
    username: str = ""                  # User-Code (Fallback wenn credential_ref leer)
    api_token: str = ""                 # Passcode (Fallback wenn credential_ref leer)
    verify_ssl: bool = False            # False für interne Server mit Self-Signed Certs
    default_app: str = ""               # Default Application publicId
    default_org_id: str = ""            # Default Organisation-ID (für Org-Level-Waivers)
    timeout_seconds: int = 30           # Timeout für API-Calls
    # Waiver-Defaults
    default_waiver_days: int = 90       # Standard-Ablauf für Waivers (Tage)
    default_matcher_strategy: str = "EXACT_COMPONENT"  # EXACT_COMPONENT | ALL_VERSIONS | ALL_COMPONENTS
    require_waiver_confirmation: bool = True  # Bestätigung vor Waiver-Anlage


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
    # Sequential Thinking (lokale Implementation) - aktiviert durch User via /seq
    sequential_thinking_enabled: bool = True
    max_thinking_steps: int = 10       # Max. Denkschritte pro Anfrage
    thinking_timeout_seconds: int = 120
    # Research Phase (Hybrid Orchestration)
    research_enabled: bool = True
    auto_research_on_question: bool = True  # Bei Fragen automatisch recherchieren
    auto_research_keywords: List[str] = [
        "wie funktioniert", "was ist", "best practice", "dokumentation",
        "how to", "tutorial", "example", "erkläre", "explain"
    ]
    research_timeout_seconds: int = 30
    max_research_results: int = 10
    research_sources: List[str] = ["memory", "code_java", "code_python", "handbook"]
    # Debug
    debug_logging: bool = False


# ══════════════════════════════════════════════════════════════════════════════
# Container Sandbox (Sichere Code-Ausführung mit Podman)
# ══════════════════════════════════════════════════════════════════════════════

class WSLIntegrationConfig(BaseModel):
    """WSL Podman Konfiguration.

    Container-Ausführung über Podman in WSL2 Ubuntu.
    """
    distro_name: str = "Ubuntu"            # WSL-Distribution (z.B. "Ubuntu", "Ubuntu-24.04")
    podman_path_in_wsl: str = "/usr/bin/podman"  # Pfad zu Podman in WSL
    # Interne Image-Registry/Pfad für podman (z.B. für air-gapped Umgebungen)
    internal_image_path: str = ""          # Lokaler Pfad oder Registry-URL für Images


class DockerSandboxConfig(BaseModel):
    """WSL Podman Sandbox für sichere Python-Code-Ausführung.

    Verwendet Podman innerhalb von WSL2 Ubuntu.
    Voraussetzung: WSL2 mit Ubuntu und installiertem Podman.
    """
    enabled: bool = False
    image: str = "python:3.11-slim"        # Base-Image
    custom_image: str = ""                 # Custom Image mit vorinstallierten Paketen
    # Ressourcen-Limits
    memory_limit: str = "512m"             # Max RAM
    cpu_limit: float = 1.0                 # Max CPU-Cores
    timeout_seconds: int = 60              # Max Ausführungszeit
    max_output_bytes: int = 131072         # Max Output-Größe (128KB)
    # Netzwerk
    network_enabled: bool = True           # Lesender Netzwerkzugriff
    # Session-Management
    session_enabled: bool = True           # Variablen zwischen Aufrufen erhalten
    session_timeout_minutes: int = 30      # Session-Timeout
    max_sessions: int = 5                  # Max gleichzeitige Sessions
    # Datei-Upload
    file_upload_enabled: bool = True       # Dateien in Container hochladen
    max_upload_size_mb: int = 10           # Max Upload-Größe
    upload_directory: str = "./sandbox_uploads"
    # Vorinstallierte Pakete
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
    read_only_filesystem: bool = False
    drop_capabilities: bool = True
    # WSL Podman Einstellungen
    wsl_integration: WSLIntegrationConfig = Field(default_factory=WSLIntegrationConfig)


class ScriptExecutionConfig(BaseModel):
    """Konfiguration für Python-Script-Generierung und -Ausführung.

    Ermöglicht dem AI-Agent, Python-Scripte zu erstellen und sicher auszuführen.
    Scripte werden validiert und erfordern User-Bestätigung vor Ausführung.
    """
    enabled: bool = True
    scripts_directory: str = "./scripts"      # Konfigurierbarer Pfad für Scripte
    max_scripts: int = 100                    # Max. gespeicherte Scripte
    max_script_size_kb: int = 100             # Max. Script-Größe in KB
    max_total_size_mb: int = 50               # Max. Gesamtgröße aller Scripte
    cleanup_days: int = 30                    # Auto-Cleanup nach X Tagen (0 = deaktiviert)
    require_confirmation: bool = True         # Bestätigung vor Ausführung (empfohlen: True)

    # Sicherheit - erlaubte Imports (Whitelist)
    # WICHTIG: Diese Liste wird AUSSCHLIESSLICH aus config.yaml geladen
    # config.yaml ist die Single Source of Truth für allowed_imports
    # Alle Änderungen werden über Settings UI in config.yaml persistiert
    # Keine Defaults hier - verhindert Konfusion zwischen config.py und config.yaml
    allowed_imports: List[str] = []

    # Sicherheit - blockierte Patterns (Regex)
    blocked_patterns: List[str] = [
        r"subprocess", r"os\.system", r"os\.popen", r"os\.exec",
        r"eval\s*\(", r"exec\s*\(", r"__import__", r"compile\s*\(",
        r"open\s*\([^)]*['\"][wa]", r"shutil\.rmtree", r"shutil\.move",
        r"socket\.", r"urllib\.request", r"http\.client",
        r"importlib", r"builtins", r"globals\s*\(", r"locals\s*\(",
        r"getattr\s*\(", r"setattr\s*\(", r"delattr\s*\(",
    ]

    # Ausführung
    use_container: bool = False               # Docker/Podman-Sandbox (momentan nicht implementiert, wird lokal ausgeführt)
    timeout_seconds: int = 30                 # Max. Ausführungszeit
    max_output_size_kb: int = 256             # Max. stdout/stderr in KB

    # Dateisystem-Zugriff für Scripts
    allowed_file_paths: List[str] = []        # Pfade auf die Scripte zugreifen dürfen (leer = kein Zugriff)

    # pip install aus internem Nexus
    pip_install_enabled: bool = False         # pip install vor Script-Ausführung erlauben
    pip_index_url: str = ""                   # Nexus PyPI URL (z.B. https://nexus.intern/repository/pypi/)
    pip_trusted_host: str = ""                # Trusted Host für pip (z.B. nexus.intern)
    pip_install_timeout_seconds: int = 60     # Timeout für pip install
    pip_cache_requirements: bool = True       # pip-Cache verwenden (schneller bei Wiederholungen)
    pip_cache_dir: str = "./scripts/.pip_cache"  # Pip-Cache-Verzeichnis

    # pip Packages die installiert werden dürfen (separate Whitelist von allowed_imports!)
    # WICHTIG: Diese Liste wird AUSSCHLIESSLICH aus config.yaml geladen
    # config.yaml ist die Single Source of Truth für pip_allowed_packages
    # Alle Änderungen werden über Settings UI in config.yaml persistiert
    # Keine Defaults hier - verhindert Konfusion zwischen config.py und config.yaml
    pip_allowed_packages: List[str] = []


class GitHubConfig(BaseModel):
    """GitHub Enterprise Server Konfiguration (intern gehostet)."""
    enabled: bool = False
    base_url: str = ""              # z.B. https://github.intern.example.com
    credential_ref: str = ""        # Referenz auf zentrale Credentials (bevorzugt, type=bearer)
    token: str = ""                 # Personal Access Token (Fallback wenn credential_ref leer)
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


class ServiceNowConfig(BaseModel):
    """ServiceNow Service Portal Konfiguration."""
    enabled: bool = False
    instance_url: str = ""             # z.B. "http://localhost:8080" oder "https://company.service-now.com"

    # Authentifizierung
    auth_type: str = "basic"           # "basic" oder "oauth2"
    credential_ref: str = ""           # Referenz auf zentrale Credentials (bevorzugt)
    username: str = ""                 # Direkt (Fallback wenn credential_ref leer)
    password: str = ""
    # OAuth2 (optional)
    client_id: str = ""
    client_secret: str = ""

    # Performance
    cache_ttl_seconds: int = 300       # 5 Minuten Cache-TTL
    max_results_default: int = 20      # Standard-Limit für Abfragen
    request_timeout_seconds: int = 30  # Request-Timeout

    # Rate Limiting (konservativ für lokale Instanz)
    max_requests_per_minute: int = 60

    # Custom Tables für kundenspezifische Anwendungen
    custom_app_tables: List[str] = []  # z.B. ["u_custom_apps", "x_myco_applications"]

    # Standard-Tabellen (können überschrieben werden)
    business_app_table: str = "cmdb_ci_business_app"
    incident_table: str = "incident"
    change_table: str = "change_request"
    knowledge_table: str = "kb_knowledge"

    def get_api_url(self, endpoint: str = "") -> str:
        """Gibt die vollständige API-URL zurück."""
        base = self.instance_url.rstrip("/")
        if endpoint:
            return f"{base}/api/now/{endpoint.lstrip('/')}"
        return f"{base}/api/now"


# ══════════════════════════════════════════════════════════════════════════════
# Analytics (User-Daten Analyse für Tool-Optimierung)
# ══════════════════════════════════════════════════════════════════════════════

class AnalyticsAnonymizeConfig(BaseModel):
    """Anonymisierungs-Einstellungen für Analytics."""
    enabled: bool = True
    mask_ips: bool = True
    mask_paths: bool = True
    mask_credentials: bool = True
    mask_emails: bool = True
    mask_urls_with_auth: bool = True
    mask_company_data: bool = True
    # Zusätzliche Regex-Patterns für firmenspezifische Daten
    company_patterns: List[str] = Field(default_factory=lambda: [
        r"\b[A-Z]{2,6}-\d{3,6}\b",  # Ticket-IDs wie PROJ-1234
    ])
    # Pfad-Komponenten die NICHT maskiert werden
    path_whitelist: List[str] = Field(default_factory=lambda: [
        "src", "app", "lib", "test", "tests", "config", "docs",
        "api", "services", "models", "utils", "core", "agent"
    ])


class AnalyticsIncludeConfig(BaseModel):
    """Was soll geloggt werden?"""
    model_config = {"protected_namespaces": ()}

    tool_selection: bool = True       # Welches Tool wurde gewählt
    tool_execution: bool = True       # Ausführungs-Ergebnis
    tool_errors: bool = True          # Fehler bei Tool-Ausführung
    model_settings: bool = True       # Modell + Temperature etc.
    user_feedback: bool = True        # Inferiertes User-Feedback
    decision_reasoning: bool = False  # Warum wurde Tool gewählt (verbose)


class AnalyticsConfig(BaseModel):
    """
    Konfiguration für das Analytics-System.

    Loggt anonymisierte Tool-Nutzung und KI-Entscheidungen
    für spätere Analyse durch Claude zur Programmverbesserung.
    """
    enabled: bool = False             # Master-Switch
    storage_path: str = "./data/analytics"
    retention_days: int = 90          # Aufbewahrungsdauer
    # Anonymisierung
    anonymize: AnalyticsAnonymizeConfig = Field(default_factory=AnalyticsAnonymizeConfig)
    # Was loggen
    log_level: str = "standard"       # minimal | standard | detailed
    include: AnalyticsIncludeConfig = Field(default_factory=AnalyticsIncludeConfig)
    # Export/Storage
    export_format: str = "jsonl"      # jsonl | sqlite
    compress_after_days: int = 7      # GZIP nach X Tagen
    max_storage_mb: int = 500         # Max Speicherplatz


# ══════════════════════════════════════════════════════════════════════════════
# Task-Decomposition Agent System
# ══════════════════════════════════════════════════════════════════════════════

class KnowledgeBaseSourcesConfig(BaseModel):
    """Welche Quellen für den Knowledge Collector aktiviert sind."""
    confluence: bool = True
    handbook: bool = True


class KnowledgeBaseConfig(BaseModel):
    """Konfiguration für den Knowledge Collector."""
    enabled: bool = True
    path: str = "knowledge-base"
    max_crawl_depth: int = 3
    max_pages_per_research: int = 30
    max_pdfs_per_research: int = 10
    max_parallel_agents: int = 5
    synthesis_model: str = ""
    auto_search: bool = True
    sources: KnowledgeBaseSourcesConfig = Field(default_factory=KnowledgeBaseSourcesConfig)


class MultiAgentTeamAgentConfig(BaseModel):
    """Konfiguration eines Agenten innerhalb eines Multi-Agent-Teams."""
    name: str = ""
    model: str = ""
    system_prompt: str = ""
    tools: List[str] = []
    max_turns: int = 15


class MultiAgentTeamConfig(BaseModel):
    """Konfiguration eines Multi-Agent-Teams."""
    name: str = ""
    description: str = ""
    agents: List[MultiAgentTeamAgentConfig] = []
    strategy: str = "dependency-first"
    max_parallel: int = 3


class MultiAgentConfig(BaseModel):
    """Konfiguration fuer das Multi-Agent Team System."""
    enabled: bool = False
    coordinator_model: str = ""
    max_concurrent_agents: int = 3
    task_timeout_seconds: int = 120
    default_strategy: str = "dependency-first"
    teams: List[MultiAgentTeamConfig] = []


class TaskAgentConfig(BaseModel):
    """
    Konfiguration fuer das Task-Decomposition Agent System.

    Das System zerlegt komplexe User-Anfragen in spezialisierte Tasks,
    die von dedizierten Agenten mit eigenen Models und Prompts ausgefuehrt werden.
    """
    enabled: bool = False                  # Master-Switch fuer Task-Decomposition
    # Model-Zuweisung pro Agent-Typ
    research_model: str = ""               # Fuer Research-Tasks (leer = tool_model)
    code_model: str = ""                   # Fuer Code-Tasks (leer = default_model)
    analyst_model: str = ""                # Fuer Analyse-Tasks (leer = analysis_model)
    devops_model: str = ""                 # Fuer DevOps-Tasks (leer = tool_model)
    docs_model: str = ""                   # Fuer Doku-Tasks (leer = tool_model)
    debug_model: str = ""                  # Fuer Debug-Tasks (leer = analysis_model)
    fallback_model: str = ""               # Fallback wenn Agent-Model nicht verfuegbar
    # Execution Settings
    max_parallel_tasks: int = 3            # Max. parallel ausfuehrbare Tasks
    task_timeout_seconds: int = 120        # Timeout pro Task
    max_retries_per_task: int = 3          # Max. Retry-Versuche pro Task
    # Phase Synthesis (Zusammenfassung bei Phasenwechsel)
    enable_phase_synthesis: bool = True    # Zwischen-Synthese aktivieren
    synthesis_max_tokens: int = 500        # Max. Tokens fuer Synthese
    # Complexity Threshold: Ab wann wird zerlegt?
    min_tasks_for_decomposition: int = 2   # Mindestens 2 Tasks fuer Zerlegung
    # Planning
    planning_model: str = ""               # Model fuer TaskPlanner (leer = analysis_model)
    planning_temperature: float = 0.1      # Temperature fuer Planung
    # ════════════════════════════════════════════════════════════════════════
    # Enhancement/Context Collection Settings
    # ════════════════════════════════════════════════════════════════════════
    # Welche Enhancement-Typen brauchen User-Bestaetigung?
    # "all" = alle, "none" = keine, "write_only" = nur Schreiboperationen
    enhancement_confirm_mode: str = "none"   # Default: keine Bestaetigung noetig
    # Bei welchen Enhancement-Typen soll immer bestaetigt werden?
    # Moegliche Werte: "research", "sequential", "analyze", "brainstorm"
    enhancement_always_confirm: List[str] = []
    # Research: Interne Quellen bevorzugen (Wiki/Confluence vor Web)
    research_internal_first: bool = True     # Intern zuerst, Web nur als Fallback
    # Hinweis: Web-Suche wird über search.enabled gesteuert (in config.yaml)


class WebexConfig(BaseModel):
    """Webex Messaging Konfiguration (OAuth2 Authorization Code Flow)."""
    enabled: bool = False
    # OAuth2 Integration (bevorzugt)
    client_id: str = ""                # Integration Client-ID
    client_secret: str = ""            # Integration Client-Secret
    redirect_uri: str = "http://localhost:8000/api/webex/oauth/callback"
    scopes: str = "spark:rooms_read spark:messages_read spark:people_read"
    # Token (OAuth oder manueller Bearer-Token)
    access_token: str = ""             # Manueller Bearer-Token ODER OAuth Access-Token
    refresh_token: str = ""            # OAuth Refresh-Token (90 Tage gültig, automatisch)
    token_expires_at: str = ""         # ISO-Zeitstempel (automatisch bei OAuth)
    # Allgemein
    base_url: str = "https://webexapis.com/v1"
    timeout_seconds: int = 30
    use_proxy: bool = True             # Zentralen Proxy verwenden
    verify_ssl: bool = False           # SSL-Zertifikate prüfen (false für Corporate Proxy)
    # Automation (Polling)
    polling_enabled: bool = False
    polling_interval_minutes: int = 5  # 1-60 Minuten
    max_messages_per_poll: int = 100


class EmailConfig(BaseModel):
    """Exchange E-Mail Konfiguration (EWS mit NTLM)."""
    enabled: bool = False
    ews_url: str = ""                      # z.B. mail.example.com/EWS/Exchange.asmx
    smtp_address: str = ""                 # E-Mail-Adresse des Postfachs
    domain: str = ""                       # NTLM-Domain (z.B. FIRMA)
    credential_ref: str = ""               # Zentrale Credentials (bevorzugt)
    username: str = ""                     # Direkt: NTLM-Benutzername (ohne Domain)
    password: str = ""                     # SENSITIVE - Direkt (Fallback)
    verify_ssl: bool = True
    timeout_seconds: int = 30
    # Automation (Polling)
    polling_enabled: bool = False
    polling_interval_minutes: int = 5      # 1-60 Minuten
    max_emails_per_poll: int = 50


class TTSConfig(BaseModel):
    """Text-to-Speech Konfiguration (OpenAI-kompatible API)."""
    enabled: bool = False
    base_url: str = ""                  # z.B. "http://tts-server:8000/v1/audio/speech"
    api_key: str = "none"
    voice: str = "alloy"                # Stimme: alloy, echo, fable, onyx, nova, shimmer
    response_format: str = "flac"       # flac, mp3, wav, opus
    speed: float = 1.0                  # Sprechgeschwindigkeit (0.5-2.0)


class WhisperConfig(BaseModel):
    """Whisper STT Konfiguration."""
    enabled: bool = False
    base_url: str = "http://localhost:8000/v1"
    api_key: str = "none"
    model: str = ""  # Whisper-Modell (leer = nicht mitsenden, Server wählt selbst)
    ffmpeg_path: str = ""  # Expliziter Pfad zu ffmpeg (leer = aus PATH suchen)


class Settings(BaseModel):
    # Globale Einstellungen
    credentials: CredentialsConfig = Field(default_factory=CredentialsConfig)  # Zentrale Credentials
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)  # Zentrale Proxy-Konfiguration

    # LLM und Modelle
    llm: LLMConfig = LLMConfig()
    models: List[ModelEntry] = []

    # Entwicklung
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
    alm: ALMConfig = ALMConfig()  # HP ALM/Quality Center
    skills: SkillsConfig = SkillsConfig()
    file_operations: FileOperationsConfig = FileOperationsConfig()
    data_sources: DataSourcesConfig = Field(default_factory=DataSourcesConfig)
    sub_agents: SubAgentsConfig = Field(default_factory=SubAgentsConfig)
    knowledge_base: KnowledgeBaseConfig = Field(default_factory=KnowledgeBaseConfig)
    multi_agent: MultiAgentConfig = Field(default_factory=MultiAgentConfig)
    task_agents: TaskAgentConfig = Field(default_factory=TaskAgentConfig)
    mq: MQConfig = Field(default_factory=MQConfig)
    test_tool: TestToolConfig = Field(default_factory=TestToolConfig)
    log_servers: LogServersConfig = Field(default_factory=LogServersConfig)
    wlp: WLPConfig = Field(default_factory=WLPConfig)
    maven: MavenConfig = Field(default_factory=MavenConfig)
    search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    jenkins: JenkinsConfig = Field(default_factory=JenkinsConfig)
    iq_server: IQServerConfig = Field(default_factory=IQServerConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    internal_fetch: InternalFetchConfig = Field(default_factory=InternalFetchConfig)
    docker_sandbox: DockerSandboxConfig = Field(default_factory=DockerSandboxConfig)
    script_execution: ScriptExecutionConfig = Field(default_factory=ScriptExecutionConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    api_tools: ApiToolsConfig = Field(default_factory=ApiToolsConfig)
    compile_tool: CompileToolConfig = Field(default_factory=CompileToolConfig)
    junit_tool: JUnitToolConfig = Field(default_factory=JUnitToolConfig)
    prompt_templates: PromptTemplatesConfig = Field(default_factory=PromptTemplatesConfig)
    access_logging: AccessLoggingConfig = Field(default_factory=AccessLoggingConfig)
    servicenow: ServiceNowConfig = Field(default_factory=ServiceNowConfig)
    analytics: AnalyticsConfig = Field(default_factory=AnalyticsConfig)
    update: UpdateConfig = Field(default_factory=UpdateConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    webex: WebexConfig = Field(default_factory=WebexConfig)
    whisper: WhisperConfig = Field(default_factory=WhisperConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)

    def apply_env_overrides(self) -> "Settings":
        if os.getenv("LLM_BASE_URL"):
            self.llm.base_url = os.getenv("LLM_BASE_URL")
        if os.getenv("LLM_API_KEY"):
            self.llm.api_key = os.getenv("LLM_API_KEY")
        if os.getenv("JAVA_REPO_PATH"):
            self.java.repo_path = os.getenv("JAVA_REPO_PATH")
        # Confluence: Keine Env-Overrides - config.yaml ist einzige Quelle
        # (Credentials über UI verwalten, nicht über Umgebungsvariablen)
        if os.getenv("PYTHON_REPO_PATH"):
            self.python.repo_path = os.getenv("PYTHON_REPO_PATH")
        if os.getenv("HANDBOOK_PATH"):
            self.handbook.path = os.getenv("HANDBOOK_PATH")
            self.handbook.enabled = True
        if os.getenv("SKILLS_DIRECTORY"):
            self.skills.directory = os.getenv("SKILLS_DIRECTORY")
        # ServiceNow Env-Overrides
        if os.getenv("SERVICENOW_URL"):
            self.servicenow.instance_url = os.getenv("SERVICENOW_URL")
            self.servicenow.enabled = True
        if os.getenv("SERVICENOW_USERNAME"):
            self.servicenow.username = os.getenv("SERVICENOW_USERNAME")
        if os.getenv("SERVICENOW_PASSWORD"):
            self.servicenow.password = os.getenv("SERVICENOW_PASSWORD")
        # Script Execution Env-Overrides
        if os.getenv("SCRIPT_ALLOWED_FILE_PATHS"):
            sep = ';' if os.name == 'nt' else ':'
            self.script_execution.allowed_file_paths = [
                p.strip() for p in os.getenv("SCRIPT_ALLOWED_FILE_PATHS").split(sep) if p.strip()
            ]
        if os.getenv("SCRIPT_PIP_INDEX_URL"):
            self.script_execution.pip_index_url = os.getenv("SCRIPT_PIP_INDEX_URL")
            self.script_execution.pip_install_enabled = True
        if os.getenv("SCRIPT_PIP_TRUSTED_HOST"):
            self.script_execution.pip_trusted_host = os.getenv("SCRIPT_PIP_TRUSTED_HOST")
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
