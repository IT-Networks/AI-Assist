# SOAP Test-Tool v2 - Überarbeitetes Design (Multi-Institut)

## Konzeptänderung

**Bisheriges Design:**
- Mehrere Service-URLs pro Stage
- Credentials global pro Service

**Neues Design:**
- **Ein Service-Endpunkt** + **Ein Login-Endpunkt** pro Stage
- **Institut-Nummer** im Template bestimmt Routing
- **Credentials und Session-Token pro Institut**

---

## 1. Neue Architektur

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         STAGE (z.B. DEV / TEST / PROD)                       │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  service_url: https://dev.example.com/soap/services                 │    │
│  │  login_url:   https://dev.example.com/soap/login                    │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  INSTITUTE (Multi-Tenant)                                            │    │
│  │                                                                      │    │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │    │
│  │  │ Institut 001  │  │ Institut 002  │  │ Institut 003  │              │    │
│  │  │ User: usr001  │  │ User: usr002  │  │ User: usr003  │              │    │
│  │  │ Token: abc... │  │ Token: def... │  │ Token: ghi... │              │    │
│  │  └──────────────┘  └──────────────┘  └──────────────┘              │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Datenmodell (Überarbeitet)

```python
# ══════════════════════════════════════════════════════════════════════════════
# SOAP Test-Tool v2 - Multi-Institut Models
# ══════════════════════════════════════════════════════════════════════════════

class SoapInstitut(BaseModel):
    """Ein Institut mit eigenen Credentials und Session."""
    institut_nr: str = ""             # z.B. "001", "002", "100"
    name: str = ""                    # z.B. "Sparkasse Musterstadt"
    user: str = ""                    # Login-User für dieses Institut
    password: str = ""                # Passwort (oder {{env:INST_001_PW}})
    enabled: bool = True


class SoapStage(BaseModel):
    """Eine Stage (Umgebung) mit einem Endpunkt für alle Services."""
    id: str = ""                      # z.B. "dev", "test", "prod"
    name: str = ""                    # z.B. "Development"

    # EIN Endpunkt für alle Service-Aufrufe
    service_url: str = ""             # z.B. "https://dev.example.com/soap/services"

    # EIN Endpunkt für Login
    login_url: str = ""               # z.B. "https://dev.example.com/soap/login"

    verify_ssl: bool = True

    # Institute mit ihren Credentials
    institute: List[SoapInstitut] = []


class SoapParameter(BaseModel):
    """Parameter-Definition für eine SOAP-Operation."""
    name: str = ""
    type: str = "string"              # string | integer | boolean | date | enum
    required: bool = False
    default: str = ""
    description: str = ""
    sensitive: bool = False
    values: List[str] = []            # Für enum


class SoapOperation(BaseModel):
    """Eine Operation (Methode) eines SOAP-Services."""
    id: str = ""                      # z.B. "get_customer"
    name: str = ""                    # z.B. "GetCustomer"
    description: str = ""
    template_file: str = ""           # z.B. "get_customer.soap.xml"
    soap_action: str = ""
    timeout_seconds: int = 60
    parameters: List[SoapParameter] = []
    response_xpath: Dict[str, str] = {}


class SoapService(BaseModel):
    """Ein SOAP-Service mit mehreren Operationen."""
    id: str = ""
    name: str = ""
    description: str = ""
    namespace: str = ""
    soap_version: str = "1.1"

    # Login-Operation (einmal pro Service definiert)
    login_template: str = "login.soap.xml"
    session_token_xpath: str = "//SessionToken/text()"
    session_expires_xpath: str = ""
    error_codes_requiring_reauth: List[str] = ["SESSION_EXPIRED", "INVALID_TOKEN"]

    operations: List[SoapOperation] = []
    enabled: bool = True


class SoapToolConfig(BaseModel):
    """SOAP Test-Tool v2 Hauptkonfiguration."""
    enabled: bool = False
    services: List[SoapService] = []
    stages: List[SoapStage] = []
    active_stage: str = ""
    templates_path: str = "data/soap/templates"
    session_storage_file: str = "data/soap/sessions.json"
```

---

## 3. Session-Key Struktur

Sessions werden pro **Stage + Institut** gespeichert:

```python
# Session-Key Format
session_key = f"{stage_id}:{institut_nr}"

# Beispiel sessions.json
{
    "dev:001": {
        "token": "abc123...",
        "user": "usr001",
        "created_at": "2026-03-13T10:00:00",
        "expires_at": "2026-03-13T18:00:00"
    },
    "dev:002": {
        "token": "def456...",
        "user": "usr002",
        "created_at": "2026-03-13T10:05:00",
        "expires_at": "2026-03-13T18:05:00"
    },
    "test:001": {
        "token": "xyz789...",
        "user": "usr001_test",
        "created_at": "2026-03-13T09:00:00",
        "expires_at": "2026-03-13T17:00:00"
    }
}
```

---

## 4. Template-Struktur (Mit Institut)

### Login-Template

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!--
  Login Template - Holt Session-Token für ein Institut

  Platzhalter:
    {{institut}}  - Institut-Nummer (required, bestimmt Routing)
    {{user}}      - Benutzername (auto-inject aus Institut-Config)
    {{password}}  - Passwort (auto-inject aus Institut-Config)

  Response-Extraktion:
    session_token: //SessionToken/text()
-->
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:auth="http://example.com/auth">
  <soap:Body>
    <auth:Login>
      <auth:InstitutNr>{{institut}}</auth:InstitutNr>
      <auth:Username>{{user}}</auth:Username>
      <auth:Password>{{password}}</auth:Password>
    </auth:Login>
  </soap:Body>
</soap:Envelope>
```

### Service-Template (z.B. GetCustomer)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!--
  GetCustomer Template

  Platzhalter:
    {{institut}}       - Institut-Nummer (required, bestimmt Routing)
    {{session_token}}  - Session-Token (auto-inject)
    {{customer_id}}    - Kunden-ID (required, user input)
-->
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:cust="http://example.com/customer">
  <soap:Header>
    <cust:AuthHeader>
      <cust:InstitutNr>{{institut}}</cust:InstitutNr>
      <cust:SessionToken>{{session_token}}</cust:SessionToken>
    </cust:AuthHeader>
  </soap:Header>
  <soap:Body>
    <cust:GetCustomer>
      <cust:InstitutNr>{{institut}}</cust:InstitutNr>
      <cust:CustomerId>{{customer_id}}</cust:CustomerId>
    </cust:GetCustomer>
  </soap:Body>
</soap:Envelope>
```

---

## 5. Execution Flow (Überarbeitet)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  User: "Hole Kunde 12345 für Institut 001 auf DEV"                          │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  1. STAGE ERMITTELN                                                          │
│     └─ Aus Kontext oder Parameter: stage_id = "dev"                         │
│     └─ Stage config laden: service_url, login_url                           │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  2. INSTITUT ERMITTELN                                                       │
│     └─ Aus Parameter: institut_nr = "001"                                   │
│     └─ Institut-Config laden: user, password                                │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  3. SESSION-TOKEN PRÜFEN                                                     │
│     └─ Key: "dev:001"                                                       │
│     └─ Token vorhanden und gültig? → Weiter zu Schritt 5                    │
│     └─ Kein Token oder abgelaufen? → Login (Schritt 4)                      │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  4. LOGIN (falls nötig)                                                      │
│     └─ Template: login.soap.xml                                             │
│     └─ URL: stage.login_url                                                 │
│     └─ Parameter: {institut: "001", user: "usr001", password: "***"}        │
│     └─ Token aus Response extrahieren und speichern                         │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  5. SERVICE AUSFÜHREN                                                        │
│     └─ Template: get_customer.soap.xml                                      │
│     └─ URL: stage.service_url                                               │
│     └─ Parameter: {institut: "001", session_token: "abc...",                │
│                    customer_id: "12345"}                                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  6. RESPONSE VERARBEITEN                                                     │
│     └─ Erfolg? → Daten zurückgeben                                          │
│     └─ Auth-Fehler? → Token invalidieren, Re-Login, Retry                   │
│     └─ Anderer Fehler? → Fault-Details zurückgeben                          │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. API Änderungen

### Stages mit Instituten

```python
# POST /api/soap/stages
{
    "name": "Development",
    "service_url": "https://dev.example.com/soap/services",
    "login_url": "https://dev.example.com/soap/login",
    "verify_ssl": true,
    "institute": [
        {"institut_nr": "001", "name": "Sparkasse Musterstadt", "user": "usr001", "password": "{{env:INST_001_PW}}"},
        {"institut_nr": "002", "name": "Sparkasse Beispiel", "user": "usr002", "password": "{{env:INST_002_PW}}"},
        {"institut_nr": "100", "name": "Test-Institut", "user": "testuser", "password": "testpass"}
    ]
}
```

### Execute mit Institut

```python
# POST /api/soap/execute/{service_id}/{operation_id}
{
    "institut_nr": "001",           # REQUIRED - bestimmt Credentials + Routing
    "params": {
        "customer_id": "12345"
    },
    "stage_id": "dev"               # Optional, sonst aktive Stage
}
```

### Session Status pro Institut

```python
# GET /api/soap/session/{stage_id}/{institut_nr}
# Response:
{
    "stage_id": "dev",
    "institut_nr": "001",
    "institut_name": "Sparkasse Musterstadt",
    "has_token": true,
    "is_expired": false,
    "user": "usr001",
    "expires_at": "2026-03-13T18:00:00"
}
```

---

## 7. AI Tool Anpassungen

### soap_execute (Überarbeitet)

```python
Tool(
    name="soap_execute",
    description=(
        "Führt eine SOAP-Operation aus. "
        "WICHTIG: institut_nr ist immer erforderlich - sie bestimmt welches Institut "
        "(und damit welche Credentials/Session) verwendet wird. "
        "Die Stage kann aus dem Kontext erkannt werden (DEV/TEST/PROD)."
    ),
    parameters=[
        ToolParameter(name="service_id", type="string", required=True),
        ToolParameter(name="operation_id", type="string", required=True),
        ToolParameter(name="institut_nr", type="string", required=True,
                      description="Institut-Nummer (z.B. '001', '002')"),
        ToolParameter(name="params", type="string", required=False,
                      description="Weitere Parameter als JSON"),
        ToolParameter(name="stage_id", type="string", required=False,
                      description="Stage (dev/test/prod), sonst aus Kontext"),
    ]
)
```

### soap_list_institutes (NEU)

```python
Tool(
    name="soap_list_institutes",
    description="Listet alle konfigurierten Institute pro Stage auf.",
    parameters=[
        ToolParameter(name="stage_id", type="string", required=False),
    ]
)
```

---

## 8. Beispiel-Konfiguration (config.yaml)

```yaml
soap_tool:
  enabled: true
  templates_path: "data/soap/templates"
  active_stage: "dev"

  stages:
    - id: "dev"
      name: "Development"
      service_url: "https://dev.banking.example.com/soap/services"
      login_url: "https://dev.banking.example.com/soap/auth/login"
      verify_ssl: false
      institute:
        - institut_nr: "001"
          name: "Sparkasse Musterstadt"
          user: "dev_001"
          password: "{{env:SOAP_DEV_001_PW}}"
        - institut_nr: "002"
          name: "Sparkasse Beispiel"
          user: "dev_002"
          password: "{{env:SOAP_DEV_002_PW}}"

    - id: "test"
      name: "Test/Integration"
      service_url: "https://test.banking.example.com/soap/services"
      login_url: "https://test.banking.example.com/soap/auth/login"
      verify_ssl: true
      institute:
        - institut_nr: "001"
          name: "Sparkasse Musterstadt"
          user: "test_001"
          password: "{{env:SOAP_TEST_001_PW}}"

  services:
    - id: "customer"
      name: "Kundenverwaltung"
      namespace: "http://banking.example.com/customer"
      login_template: "login.soap.xml"
      session_token_xpath: "//SessionToken/text()"
      error_codes_requiring_reauth: ["SESSION_EXPIRED", "INVALID_TOKEN", "AUTH_FAILED"]
      operations:
        - id: "get_customer"
          name: "GetCustomer"
          template_file: "get_customer.soap.xml"
          soap_action: "http://banking.example.com/customer/GetCustomer"
          parameters:
            - name: "customer_id"
              type: "string"
              required: true
              description: "Kunden-ID"
```

---

## 9. Code-Änderungen erforderlich

### config.py
- `SoapStage`: Feld `service_urls` entfernen
- `SoapStage`: Felder `service_url` und `login_url` hinzufügen
- `SoapStage`: Feld `institute: List[SoapInstitut]` hinzufügen
- `SoapInstitut`: Neue Klasse

### soap_session_manager.py
- Session-Key ändern: `f"{stage_id}:{institut_nr}"`
- `get_token()`: Parameter `institut_nr` statt `service_id`
- Credentials aus `stage.institute` laden statt `service.auth.credentials`

### soap_executor.py
- `execute()`: Parameter `institut_nr` hinzufügen
- Auto-Params: `institut` Platzhalter füllen
- URL: Immer `stage.service_url` verwenden

### soap.py (API Routes)
- Execute-Endpoint: `institut_nr` Parameter
- Session-Endpoints: Per Institut

### soap_tools.py (AI Tools)
- Alle Tools: `institut_nr` Parameter
- Neues Tool: `soap_list_institutes`

---

## 10. Nächste Schritte

1. **Config-Modelle anpassen** (config.py)
2. **Session-Manager überarbeiten** (institut-basiert)
3. **Executor anpassen** (institut Parameter)
4. **API Routes updaten**
5. **AI Tools updaten**
6. **Beispiel-Templates aktualisieren**

**Bereit für Implementation?** → `/sc:implement`
