# Design: SOAP und REST API Tools

## Problemstellung

Der aktuelle `http_request` Tool ist ein generischer HTTP-Client, der für SOAP-Webservices ungeeignet ist:

1. **SOAP-Envelope-Format** muss manuell erstellt werden → fehleranfällig
2. **WSDL-Parsing** fehlt komplett → keine automatische Request-Generierung
3. **Namespace-Handling** muss manuell erfolgen → kompliziert
4. **SOAPAction-Header** wird nicht automatisch gesetzt
5. **XML-Response-Parsing** fehlt → schwer lesbare Antworten

---

## Architektur-Übersicht

```
┌─────────────────────────────────────────────────────────────────────┐
│                        API Tools Layer                               │
├─────────────────────┬─────────────────────┬─────────────────────────┤
│   soap_request      │   rest_api          │   wsdl_info             │
│   (SOAP-Calls)      │   (REST-Calls)      │   (WSDL-Parser)         │
├─────────────────────┴─────────────────────┴─────────────────────────┤
│                      Shared Utilities                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐ │
│  │ WSDL Parser │  │ SOAP Builder│  │ XML Parser  │  │ JSON Schema │ │
│  │ (zeep/lxml) │  │ (Envelope)  │  │ (Response)  │  │ (OpenAPI)   │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘ │
├─────────────────────────────────────────────────────────────────────┤
│                    HTTP Client (httpx)                               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Tool 1: `wsdl_info` - WSDL-Analyse

### Zweck
Analysiert eine WSDL-Datei und gibt Informationen über verfügbare Operationen, Parameter und Typen zurück.

### Parameter

| Name | Typ | Required | Beschreibung |
|------|-----|----------|--------------|
| `wsdl_url` | string | ja | URL zur WSDL-Datei oder lokaler Pfad |
| `operation` | string | nein | Spezifische Operation für Details |
| `show_types` | boolean | nein | Komplexe Typen anzeigen (default: false) |

### Output-Format

```yaml
=== WSDL: ServiceName ===
Endpoint: https://api.example.com/soap

Operationen:
  1. GetUser
     - Input: userId (int, required)
     - Output: User (complex)

  2. CreateOrder
     - Input: order (Order, required)
     - Output: OrderResponse (complex)

Komplexe Typen:
  User:
    - id: int
    - name: string
    - email: string

  Order:
    - items: Item[]
    - total: decimal
```

### Implementierungsdetails

```python
# Abhängigkeiten
- zeep (optional, für vollständiges WSDL-Parsing)
- lxml (Fallback für einfaches XML-Parsing)

# Caching
- WSDL-Definitionen werden gecached (LRU, max 10 WSDLs)
- Cache-Key: URL + Timestamp der letzten Änderung
```

---

## Tool 2: `soap_request` - SOAP-Calls

### Zweck
Führt SOAP-Requests aus mit automatischer Envelope-Generierung basierend auf WSDL.

### Parameter

| Name | Typ | Required | Beschreibung |
|------|-----|----------|--------------|
| `wsdl_url` | string | ja | URL zur WSDL-Datei |
| `operation` | string | ja | Name der aufzurufenden Operation |
| `params` | string/dict | nein | Parameter als JSON oder Key=Value |
| `endpoint` | string | nein | Überschreibt den WSDL-Endpoint |
| `soap_version` | string | nein | "1.1" oder "1.2" (default: auto) |
| `raw_body` | string | nein | Manueller SOAP-Body (überschreibt params) |
| `timeout` | int | nein | Timeout in Sekunden (default: 30) |

### Beispiele

```bash
# Einfacher Call mit automatischer Envelope-Generierung
soap_request(
  wsdl_url="https://api.example.com/service?wsdl",
  operation="GetUser",
  params='{"userId": 123}'
)

# Mit manuellem Body (für komplexe Fälle)
soap_request(
  wsdl_url="https://api.example.com/service?wsdl",
  operation="CreateOrder",
  raw_body='<ord:CreateOrder xmlns:ord="http://example.com/orders">
    <ord:item>
      <ord:productId>ABC123</ord:productId>
      <ord:quantity>2</ord:quantity>
    </ord:item>
  </ord:CreateOrder>'
)
```

### Generierte SOAP-Envelope (automatisch)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope
    xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:ns="http://example.com/service">
  <soap:Header>
    <!-- Optional: Security-Header, WS-Addressing -->
  </soap:Header>
  <soap:Body>
    <ns:GetUser>
      <ns:userId>123</ns:userId>
    </ns:GetUser>
  </soap:Body>
</soap:Envelope>
```

### Output-Format

```yaml
=== SOAP Response: GetUser ===
Status: 200 OK
Endpoint: https://api.example.com/soap

Response (parsed):
  User:
    id: 123
    name: "Max Mustermann"
    email: "max@example.com"
    createdAt: "2024-01-15T10:30:00Z"

[Raw XML: 1,234 Zeichen - nutze raw_response=true für vollständige Antwort]
```

### Fehlerbehandlung

```yaml
=== SOAP Fault ===
Code: soap:Client
Message: "Invalid userId format"
Detail: "userId must be a positive integer"

[Request-Body wird bei Fehlern angezeigt für Debugging]
```

---

## Tool 3: `rest_api` - Verbesserte REST-Calls

### Zweck
REST-API-Calls mit besserer Parameterhandhabung, Path-Variablen und automatischem Request-Building.

### Parameter

| Name | Typ | Required | Beschreibung |
|------|-----|----------|--------------|
| `url` | string | ja | URL mit optionalen Path-Variablen `{id}` |
| `method` | string | nein | GET, POST, PUT, DELETE, PATCH (default: GET) |
| `path_params` | string/dict | nein | Path-Variablen als JSON |
| `query_params` | string/dict | nein | Query-Parameter als JSON |
| `body` | string/dict | nein | Request-Body (auto JSON) |
| `headers` | string/dict | nein | Custom Headers |
| `auth` | string | nein | "bearer:TOKEN" oder "basic:user:pass" |
| `format_response` | boolean | nein | JSON/XML formatieren (default: true) |

### Beispiele

```bash
# GET mit Path-Variablen
rest_api(
  url="https://api.example.com/users/{userId}/orders/{orderId}",
  path_params='{"userId": 123, "orderId": 456}'
)

# POST mit Body und Auth
rest_api(
  url="https://api.example.com/orders",
  method="POST",
  body='{"items": [{"id": "ABC", "qty": 2}]}',
  auth="bearer:eyJhbGciOiJIUzI1..."
)

# Mit Query-Parametern
rest_api(
  url="https://api.example.com/search",
  query_params='{"q": "test", "limit": 10, "offset": 0}'
)
```

### Output-Format

```yaml
=== REST API: GET /users/123/orders/456 ===
Status: 200 OK
Time: 234ms

Response:
{
  "orderId": 456,
  "userId": 123,
  "items": [
    {"productId": "ABC", "quantity": 2, "price": 19.99}
  ],
  "total": 39.98,
  "status": "shipped"
}

Headers:
  Content-Type: application/json
  X-RateLimit-Remaining: 98
```

---

## Implementierungsplan

### Phase 1: Core SOAP Support (Priorität: Hoch)

```
app/utils/soap_utils.py        # SOAP-Utilities
├── WSDLParser                  # WSDL-Parsing mit zeep/lxml
├── SOAPEnvelopeBuilder         # Envelope-Generierung
├── SOAPResponseParser          # Response-Parsing
└── NamespaceManager            # Namespace-Handling

app/agent/api_tools.py         # Tool-Implementierungen
├── register_api_tools()        # Registrierung
├── wsdl_info()                 # WSDL-Analyse
├── soap_request()              # SOAP-Calls
└── rest_api()                  # Verbesserte REST-Calls
```

### Phase 2: Config-Erweiterung

```yaml
# config.yaml - Neue Sektion
api_tools:
  enabled: true

  soap:
    default_timeout: 30
    cache_wsdl: true
    cache_ttl_minutes: 60
    verify_ssl: true
    # WS-Security (optional)
    security:
      enabled: false
      username: ""
      password: ""
      token: ""

  rest:
    default_timeout: 30
    auto_format_response: true
    max_response_size_kb: 500
```

### Phase 3: Erweiterte Features

1. **WS-Security Support** - Username/Password Token Profile
2. **MTOM/XOP** - Binary Attachments
3. **OpenAPI/Swagger Import** - REST-Schema-Parsing
4. **Request History** - Letzte N Requests speichern
5. **Response Caching** - Für idempotente Requests

---

## Abhängigkeiten

### Required
```
httpx           # HTTP-Client (bereits vorhanden)
lxml            # XML-Parsing (robuster als ElementTree)
```

### Optional (für vollständiges WSDL-Parsing)
```
zeep            # Vollwertiger SOAP-Client
                # Fallback: Manuelles XML-Parsing mit lxml
```

### Installation
```bash
pip install lxml
pip install zeep  # Optional, empfohlen für komplexe WSDLs
```

---

## Fallback-Strategie (ohne zeep)

Falls `zeep` nicht installiert ist:

1. **WSDL-Parsing**: Manuelles XML-Parsing mit lxml
   - Operationsnamen extrahieren
   - Simple Types parsen
   - Complex Types als "object" anzeigen

2. **SOAP-Envelope**: Template-basierte Generierung
   ```python
   SOAP_TEMPLATE = """<?xml version="1.0"?>
   <soap:Envelope xmlns:soap="...">
     <soap:Body>
       {body}
     </soap:Body>
   </soap:Envelope>"""
   ```

3. **Hinweis an User**:
   ```
   [Hinweis: zeep nicht installiert.
   Für vollständiges WSDL-Parsing: pip install zeep]
   ```

---

## Beispiel-Workflow

```bash
# 1. WSDL analysieren
> wsdl_info(wsdl_url="https://api.company.com/service?wsdl")

=== WSDL: OrderService ===
Operationen:
  1. CreateOrder (input: Order, output: OrderResponse)
  2. GetOrder (input: orderId, output: Order)
  3. ListOrders (input: customerId, output: Order[])

# 2. Operation aufrufen
> soap_request(
    wsdl_url="https://api.company.com/service?wsdl",
    operation="GetOrder",
    params='{"orderId": "ORD-123"}'
  )

=== SOAP Response: GetOrder ===
Status: 200 OK

Response:
  Order:
    id: "ORD-123"
    customer: "CUST-456"
    items:
      - product: "Widget A"
        quantity: 3
    total: 89.97
    status: "shipped"

# 3. Bei Fehler: Details anzeigen
> soap_request(
    wsdl_url="...",
    operation="CreateOrder",
    params='{"invalid": "data"}'
  )

=== SOAP Fault ===
Code: soap:Client
Message: "Required field 'customerId' is missing"

Request Body (für Debugging):
<soap:Envelope>
  <soap:Body>
    <CreateOrder>
      <invalid>data</invalid>
    </CreateOrder>
  </soap:Body>
</soap:Envelope>
```

---

## Nächste Schritte

1. [ ] `app/utils/soap_utils.py` erstellen
2. [ ] `app/agent/api_tools.py` erstellen
3. [ ] Config-Schema erweitern
4. [ ] Tools in Registry registrieren
5. [ ] Tests schreiben
6. [ ] Dokumentation aktualisieren

---

## Offene Fragen

1. **zeep als Dependency?**
   - Pro: Vollständiges WSDL-Parsing, WS-Security
   - Contra: Zusätzliche Dependency, größeres Image

2. **WSDL-Caching?**
   - In-Memory LRU Cache vs. Datei-Cache
   - Cache-Invalidierung bei WSDL-Änderungen

3. **Authentifizierung?**
   - WS-Security (UsernameToken, X.509)
   - HTTP Basic/Bearer (bereits in http_request)
   - Kerberos/SPNEGO für Windows-Umgebungen
