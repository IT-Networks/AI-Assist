# External Access Logger - Design Document

## Übersicht

System zum Logging aller KI-Zugriffe auf externe/interne Systeme außerhalb des lokalen Rechners.

### Ziele
- **Transparenz**: Nachvollziehbarkeit welche externen Systeme wann abgefragt wurden
- **Compliance**: Audit-Trail für Datenzugriffe
- **Debugging**: Fehleranalyse bei Verbindungsproblemen
- **Statistik**: Nutzungsmuster erkennen

---

## Betroffene Module (External HTTP Requests)

| Tool | Client-Funktion | Externe Systeme |
|------|-----------------|-----------------|
| `github_tools.py` | `get_github_client()` | GitHub Enterprise |
| `jenkins_tools.py` | `get_jenkins_client()` | Jenkins CI/CD |
| `mq_tools.py` | `get_mq_client()` | MQ-Series |
| `testtool_tools.py` | `get_testtool_client()` | Test-Services |
| `internal_fetch_tools.py` | `get_internal_client()` | Intranet-URLs |
| `api_tools.py` | direktes httpx | SOAP/REST APIs |

**Zentraler Integration Point**: `app/core/http_client.py` → `HttpClientPool`

---

## Log Entry Schema

```python
@dataclass
class ExternalAccessEntry:
    id: str                    # UUID
    timestamp: str             # ISO-8601
    session_id: str            # Session-Referenz
    tool_name: str             # z.B. "github_list_repos", "internal_fetch"
    client_type: str           # "github", "jenkins", "mq", "testtool", "internal", "api"
    method: str                # GET, POST, PUT, DELETE, PATCH
    url: str                   # Ziel-URL (ohne Query-Parameter mit Secrets)
    host: str                  # Extrahierter Hostname
    status_code: int           # HTTP Status (0 bei Connection Error)
    success: bool              # True wenn 2xx
    response_size: int         # Bytes
    duration_ms: int           # Antwortzeit in Millisekunden
    error_message: Optional[str]  # Bei Fehler
    content_type: Optional[str]   # Response Content-Type

    # Privacy: Nicht geloggt werden:
    # - Authorization Header
    # - Request Body mit Credentials
    # - Query-Parameter (können Tokens enthalten)
```

---

## Architektur

### Option A: Decorator-basiert (Empfohlen)

```
┌─────────────────────────────────────────────────────────┐
│                     Tool Handler                         │
│  github_list_repos(), jenkins_job_status(), ...         │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│              Logging HTTP Client Wrapper                 │
│  LoggingAsyncClient(httpx.AsyncClient)                  │
│  - Wrapt request() Methode                              │
│  - Misst Duration                                       │
│  - Loggt Entry                                          │
└─────────────────────┬───────────────────────────────────┘
                      │
          ┌───────────┴───────────┐
          ▼                       ▼
┌──────────────────┐    ┌──────────────────────┐
│  httpx Request   │    │  ExternalAccessLogger │
│  (actual HTTP)   │    │  (async JSONL write)  │
└──────────────────┘    └──────────────────────┘
```

**Vorteile:**
- Minimale Code-Änderungen
- Alle Requests automatisch erfasst
- Zentrale Stelle für Logging-Logic

### Option B: Event-basiert (Alternative)

Hooks in jedem Tool-Handler für explizites Logging.

**Nachteile:**
- Code-Duplikation
- Leicht vergessen bei neuen Tools

---

## Implementierung

### 1. ExternalAccessLogger (neu)

Speicherort: `app/services/external_access_logger.py`

```python
class ExternalAccessLogger:
    """
    Loggt externe Zugriffe für Audit und Debugging.

    Format: JSONL (wie TranscriptLogger)
    Speicherort: {data_dir}/access_logs/{date}.jsonl
    """

    async def log_access(self, entry: ExternalAccessEntry) -> None:
        """Loggt einen externen Zugriff."""

    async def search_logs(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        host: Optional[str] = None,
        tool_name: Optional[str] = None,
        success_only: bool = False,
        limit: int = 100
    ) -> List[ExternalAccessEntry]:
        """Durchsucht Access-Logs."""

    async def get_statistics(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Gibt Zugriffsstatistiken zurück."""
        # - Requests pro Tool
        # - Requests pro Host
        # - Fehlerrate
        # - Durchschnittliche Response-Zeit
```

### 2. LoggingAsyncClient Wrapper (neu)

Speicherort: `app/core/http_client.py` (erweitern)

```python
class LoggingAsyncClient:
    """
    Wrapper um httpx.AsyncClient mit automatischem Access-Logging.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        client_type: str,
        logger: ExternalAccessLogger,
        session_id: Optional[str] = None
    ):
        self._client = client
        self._client_type = client_type
        self._logger = logger
        self._session_id = session_id

    async def request(
        self,
        method: str,
        url: str,
        **kwargs
    ) -> httpx.Response:
        """Führt Request aus und loggt Zugriff."""
        start = time.monotonic()

        try:
            response = await self._client.request(method, url, **kwargs)
            duration = int((time.monotonic() - start) * 1000)

            await self._log_entry(
                method=method,
                url=url,
                status_code=response.status_code,
                success=response.is_success,
                response_size=len(response.content),
                duration_ms=duration,
                content_type=response.headers.get("content-type"),
                error_message=None
            )

            return response

        except httpx.RequestError as e:
            duration = int((time.monotonic() - start) * 1000)

            await self._log_entry(
                method=method,
                url=url,
                status_code=0,
                success=False,
                response_size=0,
                duration_ms=duration,
                content_type=None,
                error_message=str(e)
            )

            raise
```

### 3. HttpClientPool Anpassung

```python
class HttpClientPool:
    # ... bestehender Code ...

    # NEU: Logging aktivieren
    _access_logger: Optional[ExternalAccessLogger] = None
    _current_session_id: Optional[str] = None

    @classmethod
    def enable_logging(
        cls,
        logger: ExternalAccessLogger,
        session_id: Optional[str] = None
    ) -> None:
        """Aktiviert Access-Logging für alle Clients."""
        cls._access_logger = logger
        cls._current_session_id = session_id

    @classmethod
    def get(cls, name: str, ...) -> httpx.AsyncClient:
        # ... bestehende Client-Erstellung ...

        # Wrapper hinzufügen wenn Logging aktiv
        if cls._access_logger and name not in cls._logged_clients:
            client = LoggingAsyncClient(
                client=cls._clients[name],
                client_type=name,
                logger=cls._access_logger,
                session_id=cls._current_session_id
            )
            cls._logged_clients[name] = client
            return client

        return cls._logged_clients.get(name) or cls._clients[name]
```

---

## Storage Format

### JSONL (Empfohlen)

```json
{"id":"abc123","ts":"2024-03-11T14:30:00Z","session":"sess_1","tool":"github_list_repos","client":"github","method":"GET","url":"https://github.example.com/api/v3/orgs/IT-Networks/repos","host":"github.example.com","status":200,"success":true,"size":4523,"duration_ms":245,"content_type":"application/json"}
```

**Vorteile:**
- Einfaches Append
- Lesbar mit Standard-Tools (grep, jq)
- Keine DB-Locks

### SQLite Alternative

Für komplexere Queries (z.B. Aggregationen) kann später eine SQLite-Tabelle hinzugefügt werden.

---

## API Endpoints (Optional)

```yaml
GET /api/access-logs
  Query:
    - start_date: ISO-8601
    - end_date: ISO-8601
    - tool: string
    - host: string
    - success: boolean
    - limit: int (default 100)
  Response:
    - entries: List[ExternalAccessEntry]
    - total: int

GET /api/access-logs/statistics
  Query:
    - start_date: ISO-8601
    - end_date: ISO-8601
  Response:
    - total_requests: int
    - success_rate: float
    - by_tool: Dict[str, int]
    - by_host: Dict[str, int]
    - avg_duration_ms: float
```

---

## Konfiguration

```yaml
# config.yaml
access_logging:
  enabled: true
  log_directory: "index/access_logs"
  max_age_days: 90          # Auto-Cleanup
  log_request_body: false   # Privacy
  log_response_body: false  # Performance
  exclude_hosts: []         # Hosts die nicht geloggt werden
```

---

## Privacy & Security

### Nicht geloggt:
- Authorization Headers (Tokens, Passwords)
- Request Body (kann Credentials enthalten)
- Query-Parameter (können API-Keys enthalten)
- Response Body (Performance + Privacy)

### URL-Sanitization:
```python
def sanitize_url(url: str) -> str:
    """
    Entfernt sensible Daten aus URL.
    - Query-Parameter werden entfernt
    - Passwörter in Basic-Auth URLs werden maskiert
    """
    parsed = urlparse(url)

    # Maskiere Passwort in Basic-Auth URL (user:pass@host → user:****@host)
    netloc = parsed.netloc
    if '@' in netloc and ':' in netloc.split('@')[0]:
        auth_part, host_part = netloc.rsplit('@', 1)
        if ':' in auth_part:
            user, _ = auth_part.split(':', 1)
            netloc = f"{user}:****@{host_part}"

    return f"{parsed.scheme}://{netloc}{parsed.path}"
```

### Beispiel-Sanitization:
```
Input:  https://user:secret123@host.com/api?token=abc&key=xyz
Output: https://user:****@host.com/api
```

---

## Implementierungsplan

### Phase 1: Core Logger (1-2h)
1. `ExternalAccessEntry` Dataclass
2. `ExternalAccessLogger` Klasse
3. JSONL Schreib-/Lesefunktionen

### Phase 2: Client Wrapper (1h)
1. `LoggingAsyncClient` Wrapper
2. `HttpClientPool.enable_logging()` Integration
3. Session-ID Propagation

### Phase 3: Aktivierung (30min)
1. Logger in `main.py` initialisieren
2. Bei Session-Start aktivieren
3. Config-Option hinzufügen

### Phase 4: UI/API (Optional, 1-2h)
1. API-Endpoints für Log-Abfrage
2. Statistik-Endpoint
3. Frontend-Anzeige (optional)

---

## Beispiel-Output

```
📋 Access Log - Session xyz123

| Zeitpunkt           | Tool              | Host                  | Status | Zeit   |
|---------------------|-------------------|-----------------------|--------|--------|
| 14:30:00            | github_list_repos | github.example.com    | 200    | 245ms  |
| 14:30:01            | github_pr_details | github.example.com    | 200    | 189ms  |
| 14:32:15            | jenkins_job_status| jenkins.example.com   | 200    | 1234ms |
| 14:33:00            | internal_fetch    | wiki.example.com      | 500    | 45ms   |

📊 Statistik:
- Total Requests: 4
- Erfolgsrate: 75%
- Durchschn. Antwortzeit: 428ms
- Hosts: github.example.com (2), jenkins.example.com (1), wiki.example.com (1)
```

---

## Entscheidungen

| Aspekt | Entscheidung | Begründung |
|--------|--------------|------------|
| Storage | JSONL | Einfach, append-only, lesbar |
| Integration | Wrapper-Pattern | Zentral, keine Tool-Änderungen |
| Privacy | URL-only, keine Bodies | Sicherheit + Performance |
| Granularität | Pro Request | Detailliertes Tracking |

---

## Implementierungsstatus

**Status: Implementiert**

### Erstellte Dateien:
- `app/services/external_access_logger.py` - Core Logger Service
- `app/api/routes/access_logs.py` - API Endpoints
- `docs/design/external_access_logger.md` - Dieses Design-Dokument

### Geänderte Dateien:
- `app/core/http_client.py` - LoggingAsyncClient Wrapper hinzugefügt
- `app/core/config.py` - AccessLoggingConfig hinzugefügt
- `main.py` - Logger-Initialisierung und Router

### API Endpoints:
- `GET /api/access-logs` - Logs abfragen
- `GET /api/access-logs/statistics` - Statistiken
- `GET /api/access-logs/status` - System-Status
- `POST /api/access-logs/cleanup` - Alte Logs löschen

### Konfiguration in config.yaml:
```yaml
access_logging:
  enabled: true
  max_age_days: 90
```
