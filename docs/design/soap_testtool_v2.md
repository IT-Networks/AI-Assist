# SOAP Test-Tool v2 - Design Document

## 1. System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND (app.js)                               │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  SOAP Services Panel                                                 │    │
│  │  ├── Stage Selector (DEV/TEST/PROD)                                 │    │
│  │  ├── Service List (collapsible)                                     │    │
│  │  │   └── Operation Cards                                            │    │
│  │  ├── Template Editor (CodeMirror/Monaco XML)                        │    │
│  │  └── Parameter Form (auto-generated)                                │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              API LAYER (FastAPI)                             │
│                                                                              │
│  /api/soap/                                                                  │
│  ├── services/           CRUD for services                                  │
│  ├── operations/         CRUD for operations                                │
│  ├── templates/          CRUD for XML templates                             │
│  ├── stages/             CRUD for stages (inherits from testtool)           │
│  ├── execute/            Execute SOAP calls                                 │
│  └── session/            Session token management                           │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           CORE COMPONENTS                                    │
│                                                                              │
│  ┌────────────────────┐  ┌────────────────────┐  ┌────────────────────┐    │
│  │  SoapServiceManager │  │  SoapSessionManager │  │  SoapTemplateEngine│    │
│  │  ─────────────────  │  │  ──────────────────  │  │  ─────────────────  │    │
│  │  - CRUD services    │  │  - Login/Logout      │  │  - Load templates  │    │
│  │  - CRUD operations  │  │  - Token storage     │  │  - Fill placeholders│   │
│  │  - Validate config  │  │  - Auto-refresh      │  │  - Validate XML    │    │
│  │  - Persist to disk  │  │  - Per-stage tokens  │  │  - Parse params    │    │
│  └────────────────────┘  └────────────────────┘  └────────────────────┘    │
│           │                        │                        │               │
│           └────────────────────────┼────────────────────────┘               │
│                                    ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                      SoapExecutor                                    │    │
│  │  ────────────────────────────────────────────────────────────────   │    │
│  │  1. Check session token (auto-login if needed)                      │    │
│  │  2. Load & fill template                                            │    │
│  │  3. Execute HTTP POST with SOAP envelope                            │    │
│  │  4. Parse response (handle faults)                                  │    │
│  │  5. Retry with new token on auth errors                             │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           AI AGENT TOOLS                                     │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  soap_list_services      - List all services/operations             │    │
│  │  soap_execute            - Execute operation with params            │    │
│  │  soap_create_template    - Generate template from WSDL/example      │    │
│  │  soap_get_session_status - Check session token status               │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           STORAGE                                            │
│                                                                              │
│  data/soap/                                                                  │
│  ├── services.json           Service definitions                            │
│  ├── sessions.json           Active session tokens (encrypted)              │
│  └── templates/                                                              │
│      ├── customer-service/                                                   │
│      │   ├── login.soap.xml                                                 │
│      │   ├── get_customer.soap.xml                                          │
│      │   └── search_orders.soap.xml                                         │
│      └── order-service/                                                      │
│          └── ...                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Data Models (config.py Erweiterung)

```python
# ══════════════════════════════════════════════════════════════════════════════
# SOAP Test-Tool v2 Models
# ══════════════════════════════════════════════════════════════════════════════

class SoapCredentials(BaseModel):
    """Credentials für SOAP-Service-Authentifizierung."""
    user: str = ""                    # Direkt oder {{env:SOAP_USER}}
    password: str = ""                # Direkt oder {{env:SOAP_PASSWORD}}


class SoapAuthConfig(BaseModel):
    """Authentifizierungs-Konfiguration für einen SOAP-Service."""
    type: str = "session"             # none | session | basic | api_key
    login_operation: str = ""         # ID der Login-Operation
    session_token_xpath: str = ""     # XPath zum Token in Login-Response
    session_expires_xpath: str = ""   # XPath zur Ablaufzeit (optional)
    error_codes_requiring_reauth: List[str] = [
        "SESSION_EXPIRED",
        "INVALID_TOKEN",
        "AuthenticationFault"
    ]
    credentials: SoapCredentials = SoapCredentials()
    # Runtime (nicht persistiert)
    _current_token: str = ""
    _token_expires_at: Optional[datetime] = None


class SoapParameter(BaseModel):
    """Parameter-Definition für eine SOAP-Operation."""
    name: str
    type: str = "string"              # string | integer | boolean | date | datetime | enum | object | array
    required: bool = False
    default: Any = None
    description: str = ""
    sensitive: bool = False           # Maskiert in Logs
    values: List[str] = []            # Erlaubte Werte für enum-Typ
    xpath: str = ""                   # Optional: XPath für Response-Extraktion


class SoapOperation(BaseModel):
    """Eine Operation (Methode) eines SOAP-Services."""
    id: str                           # z.B. "get_customer"
    name: str                         # z.B. "GetCustomer"
    description: str = ""
    template_file: str                # Relativer Pfad zum Template
    soap_action: str = ""             # SOAPAction HTTP-Header
    requires_session: bool = True     # Benötigt Session-Token?
    timeout_seconds: int = 60
    parameters: List[SoapParameter] = []
    # Response-Extraktion
    response_xpath: Dict[str, str] = {}  # z.B. {"customer_name": "//CustomerName/text()"}


class SoapService(BaseModel):
    """Ein SOAP-Service mit mehreren Operationen."""
    id: str                           # z.B. "customer-service"
    name: str                         # z.B. "Customer Service"
    description: str = ""
    wsdl_url: str = ""                # Optional für Auto-Discovery
    namespace: str = ""               # Target-Namespace
    soap_version: str = "1.1"         # 1.1 | 1.2
    auth: SoapAuthConfig = SoapAuthConfig()
    operations: List[SoapOperation] = []
    # Stage-URLs werden NICHT pro Service gespeichert, sondern global
    enabled: bool = True


class SoapStage(BaseModel):
    """Eine Deployment-Stage für SOAP-Services."""
    id: str                           # z.B. "dev"
    name: str                         # z.B. "Development"
    base_url: str                     # z.B. "https://dev.example.com/services"
    verify_ssl: bool = True
    # Pro Service kann die URL überschrieben werden
    service_urls: Dict[str, str] = {} # {service_id: custom_url}


class SoapToolConfig(BaseModel):
    """Haupt-Konfiguration für SOAP Test-Tool v2."""
    enabled: bool = False
    services: List[SoapService] = []
    stages: List[SoapStage] = []
    active_stage: str = ""
    templates_path: str = "data/soap/templates"
    # Session-Management
    session_storage_file: str = "data/soap/sessions.json"
    auto_refresh_sessions: bool = True
    session_refresh_before_expiry_seconds: int = 300  # 5 Min vor Ablauf
```

---

## 3. API Routes Design

### `/api/soap/` - Neue Route-Datei

```python
# app/api/routes/soap.py

router = APIRouter(prefix="/api/soap", tags=["soap"])

# ═══════════════════════════════════════════════════════════════════════════════
# STAGES
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/stages")
async def list_stages() -> Dict[str, Any]:
    """Alle Stages auflisten."""

@router.post("/stages")
async def add_stage(stage: SoapStageRequest) -> Dict[str, Any]:
    """Neue Stage hinzufügen."""

@router.put("/stages/{stage_id}")
async def update_stage(stage_id: str, stage: SoapStageRequest) -> Dict[str, Any]:
    """Stage aktualisieren."""

@router.delete("/stages/{stage_id}")
async def delete_stage(stage_id: str) -> Dict[str, Any]:
    """Stage löschen."""

@router.put("/stages/active")
async def set_active_stage(stage_id: str) -> Dict[str, Any]:
    """Aktive Stage setzen."""


# ═══════════════════════════════════════════════════════════════════════════════
# SERVICES
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/services")
async def list_services() -> Dict[str, Any]:
    """Alle Services mit ihren Operationen auflisten."""

@router.post("/services")
async def create_service(service: SoapServiceRequest) -> Dict[str, Any]:
    """Neuen Service anlegen."""

@router.get("/services/{service_id}")
async def get_service(service_id: str) -> Dict[str, Any]:
    """Service-Details abrufen."""

@router.put("/services/{service_id}")
async def update_service(service_id: str, service: SoapServiceRequest) -> Dict[str, Any]:
    """Service aktualisieren."""

@router.delete("/services/{service_id}")
async def delete_service(service_id: str) -> Dict[str, Any]:
    """Service löschen (inkl. Templates)."""

@router.post("/services/import-wsdl")
async def import_from_wsdl(wsdl_url: str) -> Dict[str, Any]:
    """Service und Operationen aus WSDL importieren."""


# ═══════════════════════════════════════════════════════════════════════════════
# OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/services/{service_id}/operations")
async def add_operation(service_id: str, operation: SoapOperationRequest) -> Dict[str, Any]:
    """Operation zu Service hinzufügen."""

@router.put("/services/{service_id}/operations/{operation_id}")
async def update_operation(service_id: str, operation_id: str, operation: SoapOperationRequest) -> Dict[str, Any]:
    """Operation aktualisieren."""

@router.delete("/services/{service_id}/operations/{operation_id}")
async def delete_operation(service_id: str, operation_id: str) -> Dict[str, Any]:
    """Operation löschen."""


# ═══════════════════════════════════════════════════════════════════════════════
# TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/templates/{service_id}/{operation_id}")
async def get_template(service_id: str, operation_id: str) -> Dict[str, Any]:
    """Template-XML abrufen."""

@router.put("/templates/{service_id}/{operation_id}")
async def save_template(service_id: str, operation_id: str, content: str) -> Dict[str, Any]:
    """Template-XML speichern."""

@router.post("/templates/{service_id}/{operation_id}/validate")
async def validate_template(service_id: str, operation_id: str, content: str) -> Dict[str, Any]:
    """Template validieren (Platzhalter prüfen, XML-Syntax)."""

@router.post("/templates/generate")
async def generate_template(wsdl_url: str, operation_name: str) -> Dict[str, Any]:
    """Template aus WSDL generieren."""


# ═══════════════════════════════════════════════════════════════════════════════
# EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/execute/{service_id}/{operation_id}")
async def execute_operation(
    service_id: str,
    operation_id: str,
    params: Dict[str, Any],
    stage_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    SOAP-Operation ausführen.

    Flow:
    1. Stage ermitteln (Parameter oder aktive Stage)
    2. Session-Token prüfen (auto-login wenn nötig)
    3. Template laden und füllen
    4. Request ausführen
    5. Bei Auth-Error: Re-Login und Retry
    6. Response parsen und zurückgeben
    """


# ═══════════════════════════════════════════════════════════════════════════════
# SESSION
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/session/{service_id}")
async def get_session_status(service_id: str, stage_id: Optional[str] = None) -> Dict[str, Any]:
    """Session-Status prüfen (Token vorhanden? Ablaufzeit?)."""

@router.post("/session/{service_id}/login")
async def force_login(service_id: str, stage_id: Optional[str] = None) -> Dict[str, Any]:
    """Manuelles Login erzwingen."""

@router.delete("/session/{service_id}")
async def logout(service_id: str, stage_id: Optional[str] = None) -> Dict[str, Any]:
    """Session-Token löschen."""
```

---

## 4. Core Components

### 4.1 SoapSessionManager

```python
# app/services/soap_session_manager.py

class SoapSessionManager:
    """
    Verwaltet Session-Tokens für SOAP-Services.

    Features:
    - Token-Speicherung pro Service + Stage
    - Automatisches Re-Login bei Ablauf
    - Sichere Credential-Auflösung ({{env:VAR}})
    """

    def __init__(self, storage_path: str):
        self.storage_path = Path(storage_path)
        self._sessions: Dict[str, SessionInfo] = {}
        self._load()

    async def get_token(self, service: SoapService, stage: SoapStage) -> str:
        """
        Gibt gültigen Session-Token zurück.
        Führt Login durch wenn nötig.
        """
        key = f"{service.id}:{stage.id}"
        session = self._sessions.get(key)

        if session and not self._is_expired(session):
            return session.token

        # Login durchführen
        return await self._login(service, stage)

    async def _login(self, service: SoapService, stage: SoapStage) -> str:
        """Führt Login-Operation durch und speichert Token."""
        login_op = self._find_login_operation(service)
        if not login_op:
            raise ValueError(f"Keine Login-Operation für {service.id}")

        # Credentials auflösen
        credentials = self._resolve_credentials(service.auth.credentials)

        # Template laden und füllen
        template = self._load_template(service, login_op)
        envelope = self._fill_template(template, credentials)

        # Request ausführen
        response = await self._execute_soap(
            url=f"{stage.base_url}/{service.id}",
            envelope=envelope,
            operation=login_op,
            verify_ssl=stage.verify_ssl
        )

        # Token extrahieren
        token = self._extract_xpath(response, service.auth.session_token_xpath)
        expires = self._extract_xpath(response, service.auth.session_expires_xpath)

        # Speichern
        self._sessions[f"{service.id}:{stage.id}"] = SessionInfo(
            token=token,
            expires_at=expires,
            created_at=datetime.now()
        )
        self._save()

        return token

    def _resolve_credentials(self, creds: SoapCredentials) -> Dict[str, str]:
        """Löst {{env:VAR}} Platzhalter auf."""
        result = {}
        for key in ['user', 'password']:
            value = getattr(creds, key)
            if value.startswith('{{env:') and value.endswith('}}'):
                env_var = value[6:-2]
                result[key] = os.environ.get(env_var, '')
            else:
                result[key] = value
        return result
```

### 4.2 SoapTemplateEngine

```python
# app/services/soap_template_engine.py

class SoapTemplateEngine:
    """
    Verarbeitet SOAP-XML-Templates.

    Platzhalter-Format:
    - {{name}}           - Required, kein Default
    - {{name:default}}   - Optional mit Default
    - {{name:}}          - Optional, leerer Default
    """

    PLACEHOLDER_PATTERN = re.compile(r'\{\{(\w+)(?::([^}]*))?\}\}')

    def __init__(self, templates_path: str):
        self.templates_path = Path(templates_path)

    def load_template(self, service_id: str, operation_id: str) -> str:
        """Lädt Template von Disk."""
        path = self.templates_path / service_id / f"{operation_id}.soap.xml"
        if not path.exists():
            raise FileNotFoundError(f"Template nicht gefunden: {path}")
        return path.read_text(encoding='utf-8')

    def fill_template(
        self,
        template: str,
        params: Dict[str, Any],
        auto_params: Dict[str, Any] = None
    ) -> str:
        """
        Füllt Platzhalter im Template.

        Args:
            template: XML-Template mit Platzhaltern
            params: User-Parameter
            auto_params: Automatisch injizierte Params (session_token, user)

        Returns:
            Gefülltes XML
        """
        all_params = {**(auto_params or {}), **params}

        def replace(match):
            name = match.group(1)
            default = match.group(2)

            if name in all_params:
                return self._escape_xml(str(all_params[name]))
            elif default is not None:
                return self._escape_xml(default)
            else:
                raise ValueError(f"Required parameter missing: {name}")

        return self.PLACEHOLDER_PATTERN.sub(replace, template)

    def extract_parameters(self, template: str) -> List[Dict[str, Any]]:
        """Extrahiert Parameter-Definitionen aus Template."""
        params = []
        for match in self.PLACEHOLDER_PATTERN.finditer(template):
            name = match.group(1)
            default = match.group(2)
            params.append({
                'name': name,
                'required': default is None,
                'default': default or '',
                'auto_inject': name in ('session_token', 'user')
            })
        return params

    def validate_template(self, content: str) -> Dict[str, Any]:
        """Validiert Template (XML-Syntax, SOAP-Struktur)."""
        errors = []
        warnings = []

        # XML-Syntax prüfen
        try:
            ET.fromstring(content)
        except ET.ParseError as e:
            errors.append(f"XML-Syntax-Fehler: {e}")

        # SOAP-Envelope prüfen
        if '<soap:Envelope' not in content and '<Envelope' not in content:
            warnings.append("Kein SOAP-Envelope gefunden")

        # Platzhalter extrahieren
        params = self.extract_parameters(content)

        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings,
            'parameters': params
        }
```

### 4.3 SoapExecutor

```python
# app/services/soap_executor.py

class SoapExecutor:
    """
    Führt SOAP-Requests aus mit automatischem Session-Management.
    """

    def __init__(
        self,
        session_manager: SoapSessionManager,
        template_engine: SoapTemplateEngine
    ):
        self.sessions = session_manager
        self.templates = template_engine

    async def execute(
        self,
        service: SoapService,
        operation: SoapOperation,
        stage: SoapStage,
        params: Dict[str, Any],
        retry_on_auth_error: bool = True
    ) -> SoapExecutionResult:
        """
        Führt SOAP-Operation aus.

        Flow:
        1. Session-Token holen (falls required)
        2. Template laden und füllen
        3. Request ausführen
        4. Response parsen
        5. Bei Auth-Fehler: Re-Login und Retry
        """
        # Auto-Params vorbereiten
        auto_params = {}
        if operation.requires_session:
            token = await self.sessions.get_token(service, stage)
            creds = self.sessions._resolve_credentials(service.auth.credentials)
            auto_params['session_token'] = token
            auto_params['user'] = creds.get('user', '')

        # Template füllen
        template = self.templates.load_template(service.id, operation.id)
        envelope = self.templates.fill_template(template, params, auto_params)

        # URL bestimmen
        base_url = stage.service_urls.get(service.id, stage.base_url)
        url = f"{base_url.rstrip('/')}"

        # Headers
        headers = {
            'Content-Type': 'text/xml; charset=utf-8' if service.soap_version == '1.1'
                           else 'application/soap+xml; charset=utf-8',
        }
        if operation.soap_action and service.soap_version == '1.1':
            headers['SOAPAction'] = f'"{operation.soap_action}"'

        # Request ausführen
        try:
            async with httpx.AsyncClient(
                timeout=operation.timeout_seconds,
                verify=stage.verify_ssl
            ) as client:
                response = await client.post(url, content=envelope, headers=headers)
        except Exception as e:
            return SoapExecutionResult(
                success=False,
                error=str(e),
                request_xml=envelope
            )

        # Response parsen
        result = self._parse_response(response, service)
        result.request_xml = envelope

        # Auth-Error? Retry mit neuem Token
        if not result.success and retry_on_auth_error:
            if self._is_auth_error(result, service):
                # Token invalidieren und neu holen
                self.sessions.invalidate(service.id, stage.id)
                return await self.execute(
                    service, operation, stage, params,
                    retry_on_auth_error=False  # Nur 1 Retry
                )

        return result

    def _is_auth_error(self, result: SoapExecutionResult, service: SoapService) -> bool:
        """Prüft ob Response ein Auth-Fehler ist."""
        if result.fault_code:
            for error_code in service.auth.error_codes_requiring_reauth:
                if error_code.lower() in result.fault_code.lower():
                    return True
                if error_code.lower() in (result.fault_message or '').lower():
                    return True
        return False
```

---

## 5. AI Agent Tools

```python
# app/agent/soap_tools.py

def register_soap_tools(registry: ToolRegistry) -> int:
    """Registriert SOAP Test-Tool v2 Agent-Tools."""

    from app.core.config import settings

    if not settings.soap_tool.enabled:
        return 0

    count = 0

    # ══════════════════════════════════════════════════════════════════════════
    # soap_list_services
    # ══════════════════════════════════════════════════════════════════════════

    async def soap_list_services(**kwargs) -> ToolResult:
        """Listet alle SOAP-Services mit Operationen."""
        services = []
        for svc in settings.soap_tool.services:
            services.append({
                'id': svc.id,
                'name': svc.name,
                'description': svc.description,
                'operations': [
                    {
                        'id': op.id,
                        'name': op.name,
                        'description': op.description,
                        'requires_session': op.requires_session,
                        'parameters': [
                            {
                                'name': p.name,
                                'type': p.type,
                                'required': p.required,
                                'description': p.description
                            }
                            for p in op.parameters
                            if not p.name.startswith('session_')  # Auto-injected ausblenden
                        ]
                    }
                    for op in svc.operations
                ]
            })

        return ToolResult(
            success=True,
            data={
                'services': services,
                'stages': [{'id': s.id, 'name': s.name} for s in settings.soap_tool.stages],
                'active_stage': settings.soap_tool.active_stage
            }
        )

    registry.register(Tool(
        name="soap_list_services",
        description=(
            "Listet alle verfügbaren SOAP-Services und ihre Operationen auf. "
            "Zeigt Service-Namen, Beschreibungen und benötigte Parameter. "
            "Nutze dieses Tool um verfügbare Services zu entdecken."
        ),
        category=ToolCategory.SEARCH,
        parameters=[],
        handler=soap_list_services
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════
    # soap_execute
    # ══════════════════════════════════════════════════════════════════════════

    async def soap_execute(**kwargs) -> ToolResult:
        """Führt eine SOAP-Operation aus."""
        service_id = kwargs.get('service_id')
        operation_id = kwargs.get('operation_id')
        params_json = kwargs.get('params', '{}')
        stage_id = kwargs.get('stage_id')

        # Service & Operation finden
        service = next((s for s in settings.soap_tool.services if s.id == service_id), None)
        if not service:
            return ToolResult(success=False, error=f"Service '{service_id}' nicht gefunden")

        operation = next((o for o in service.operations if o.id == operation_id), None)
        if not operation:
            return ToolResult(success=False, error=f"Operation '{operation_id}' nicht gefunden")

        # Stage ermitteln
        stage = None
        if stage_id:
            stage = next((s for s in settings.soap_tool.stages if s.id == stage_id), None)
        if not stage:
            stage = next((s for s in settings.soap_tool.stages if s.id == settings.soap_tool.active_stage), None)
        if not stage:
            return ToolResult(success=False, error="Keine Stage konfiguriert")

        # Parameter parsen
        try:
            params = json.loads(params_json) if params_json else {}
        except json.JSONDecodeError as e:
            return ToolResult(success=False, error=f"Ungültige JSON-Parameter: {e}")

        # Ausführen
        executor = get_soap_executor()
        result = await executor.execute(service, operation, stage, params)

        return ToolResult(
            success=result.success,
            data={
                'status_code': result.status_code,
                'response': result.data,
                'fault_code': result.fault_code,
                'fault_message': result.fault_message,
                'elapsed_ms': result.elapsed_ms,
                'stage': stage.name,
            },
            error=result.error
        )

    registry.register(Tool(
        name="soap_execute",
        description=(
            "Führt eine SOAP-Operation aus. Session-Management erfolgt automatisch "
            "(Login, Token-Refresh). Gibt die geparste Response oder Fehlermeldung zurück. "
            "Stage kann explizit angegeben werden oder wird aus dem Kontext erkannt."
        ),
        category=ToolCategory.FILE,
        is_write_operation=True,
        parameters=[
            ToolParameter(name="service_id", type="string", description="ID des Services", required=True),
            ToolParameter(name="operation_id", type="string", description="ID der Operation", required=True),
            ToolParameter(name="params", type="string", description="Parameter als JSON-Objekt", required=False),
            ToolParameter(name="stage_id", type="string", description="Stage-ID (optional, sonst aktive Stage)", required=False),
        ],
        handler=soap_execute
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════
    # soap_get_session_status
    # ══════════════════════════════════════════════════════════════════════════

    async def soap_get_session_status(**kwargs) -> ToolResult:
        """Prüft Session-Status für einen Service."""
        service_id = kwargs.get('service_id')
        stage_id = kwargs.get('stage_id', settings.soap_tool.active_stage)

        manager = get_session_manager()
        status = manager.get_status(service_id, stage_id)

        return ToolResult(
            success=True,
            data={
                'has_token': status.has_token,
                'expires_at': status.expires_at.isoformat() if status.expires_at else None,
                'is_expired': status.is_expired,
                'service': service_id,
                'stage': stage_id
            }
        )

    registry.register(Tool(
        name="soap_get_session_status",
        description=(
            "Prüft ob ein gültiger Session-Token für einen Service existiert. "
            "Zeigt Token-Status und Ablaufzeit."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(name="service_id", type="string", description="ID des Services", required=True),
            ToolParameter(name="stage_id", type="string", description="Stage-ID (optional)", required=False),
        ],
        handler=soap_get_session_status
    ))
    count += 1

    return count
```

---

## 6. Frontend Integration

### 6.1 Neuer Panel-Tab: "SOAP Services"

```javascript
// Erweiterung in app.js

function renderSOAPPanel() {
    return `
        <div class="soap-panel" id="soap-panel">
            <!-- Stage Selector -->
            <div class="soap-stage-selector">
                <label>Stage:</label>
                <select id="soap-stage-select" onchange="soapSetActiveStage(this.value)">
                    <!-- Dynamisch gefüllt -->
                </select>
            </div>

            <!-- Service List -->
            <div class="soap-services" id="soap-services">
                <!-- Collapsible Service Cards -->
            </div>

            <!-- Template Editor (Modal) -->
            <div class="soap-template-editor" id="soap-template-editor" style="display:none;">
                <div class="editor-header">
                    <h3 id="template-editor-title">Template bearbeiten</h3>
                    <button onclick="closeTemplateEditor()">X</button>
                </div>
                <div class="editor-body">
                    <textarea id="template-xml-editor" class="xml-editor"></textarea>
                    <div id="template-params" class="params-list"></div>
                </div>
                <div class="editor-footer">
                    <button onclick="validateTemplate()">Validieren</button>
                    <button onclick="saveTemplate()">Speichern</button>
                </div>
            </div>

            <!-- Execution Panel -->
            <div class="soap-execution" id="soap-execution">
                <h4>Operation ausführen</h4>
                <div id="soap-params-form"></div>
                <button onclick="executeSoapOperation()">Ausführen</button>
                <div id="soap-result"></div>
            </div>
        </div>
    `;
}

// Service Card Template
function renderServiceCard(service) {
    return `
        <div class="soap-service-card" data-service-id="${service.id}">
            <div class="service-header" onclick="toggleServiceOperations('${service.id}')">
                <span class="service-name">${service.name}</span>
                <span class="service-ops-count">${service.operations.length} Operationen</span>
                <span class="chevron">▼</span>
            </div>
            <div class="service-operations" id="ops-${service.id}" style="display:none;">
                ${service.operations.map(op => renderOperationItem(service.id, op)).join('')}
            </div>
            <div class="service-actions">
                <button onclick="editService('${service.id}')">Bearbeiten</button>
                <button onclick="addOperation('${service.id}')">+ Operation</button>
            </div>
        </div>
    `;
}

function renderOperationItem(serviceId, operation) {
    return `
        <div class="operation-item" data-operation-id="${operation.id}">
            <span class="op-name">${operation.name}</span>
            <span class="op-desc">${operation.description}</span>
            <div class="op-actions">
                <button onclick="editTemplate('${serviceId}', '${operation.id}')" title="Template">
                    📄
                </button>
                <button onclick="selectOperation('${serviceId}', '${operation.id}')" title="Ausführen">
                    ▶️
                </button>
            </div>
        </div>
    `;
}
```

---

## 7. File Structure

```
AI-Assist/
├── app/
│   ├── api/routes/
│   │   └── soap.py                    # NEW: API Routes
│   ├── agent/
│   │   └── soap_tools.py              # NEW: Agent Tools
│   └── services/
│       ├── soap_session_manager.py    # NEW: Session Management
│       ├── soap_template_engine.py    # NEW: Template Processing
│       └── soap_executor.py           # NEW: Execution Logic
├── data/
│   └── soap/
│       ├── services.json              # Service Definitions
│       ├── sessions.json              # Active Sessions (encrypted)
│       └── templates/
│           ├── customer-service/
│           │   ├── login.soap.xml
│           │   └── get_customer.soap.xml
│           └── order-service/
│               └── ...
└── static/
    └── app.js                         # Frontend Extension
```

---

## 8. Migration von TestTool v1

```python
# Migration Script: migrate_testtool_to_soap.py

def migrate_testtool_config():
    """Migriert bestehende TestTool-Konfiguration zu SOAP v2."""

    from app.core.config import settings

    # Stages übernehmen
    for old_stage in settings.test_tool.stages:
        new_stage = SoapStage(
            id=old_stage.id,
            name=old_stage.name,
            base_url=old_stage.urls[0].url if old_stage.urls else '',
            verify_ssl=True
        )
        settings.soap_tool.stages.append(new_stage)

    # Aktive Stage übernehmen
    settings.soap_tool.active_stage = settings.test_tool.active_stage

    # Services mit einzelner Operation erstellen
    for old_service in settings.test_tool.services:
        # Prüfen ob SOAP-Content
        if old_service.content_type in ['text/xml', 'application/soap+xml']:
            new_service = SoapService(
                id=old_service.id,
                name=old_service.name,
                description=old_service.description,
                operations=[
                    SoapOperation(
                        id='default',
                        name=old_service.name,
                        template_file=f"{old_service.id}/default.soap.xml",
                        parameters=[
                            SoapParameter(
                                name=p.name,
                                type=p.type,
                                required=p.required,
                                description=p.description
                            )
                            for p in old_service.parameters
                        ]
                    )
                ]
            )
            settings.soap_tool.services.append(new_service)
```

---

## 9. Nächste Schritte

1. **Phase 1: Core Models** - Config-Klassen in `config.py` hinzufügen
2. **Phase 2: Services** - Session Manager, Template Engine, Executor implementieren
3. **Phase 3: API Routes** - REST-Endpoints für CRUD + Execution
4. **Phase 4: Agent Tools** - AI-Integration
5. **Phase 5: Frontend** - Panel in app.js

**Bereit für Implementation?** → `/sc:implement`
