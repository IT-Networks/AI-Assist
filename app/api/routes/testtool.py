"""
Test-Tool API Routes (Multi-Institut).

Routes:
  GET    /api/testtool/config               - Konfiguration abrufen
  PUT    /api/testtool/config               - Endpoints konfigurieren

  GET    /api/testtool/institute            - Institute auflisten
  POST   /api/testtool/institute            - Institut hinzufügen
  PUT    /api/testtool/institute/{nr}       - Institut aktualisieren
  DELETE /api/testtool/institute/{nr}       - Institut löschen

  GET    /api/testtool/services             - Services auflisten
  POST   /api/testtool/services             - Service hinzufügen
  GET    /api/testtool/services/{id}        - Service-Details
  PUT    /api/testtool/services/{id}        - Service aktualisieren
  DELETE /api/testtool/services/{id}        - Service löschen

  POST   /api/testtool/services/{id}/operations           - Operation hinzufügen
  PUT    /api/testtool/services/{id}/operations/{op_id}   - Operation aktualisieren
  DELETE /api/testtool/services/{id}/operations/{op_id}   - Operation löschen

  GET    /api/testtool/templates/{svc}/{op}     - Template abrufen
  PUT    /api/testtool/templates/{svc}/{op}     - Template speichern
  POST   /api/testtool/templates/validate       - Template validieren

  POST   /api/testtool/execute/{svc}/{op}       - Operation ausführen

  GET    /api/testtool/session/{institut}       - Session-Status
  POST   /api/testtool/session/{institut}/login - Manuelles Login
  DELETE /api/testtool/session/{institut}       - Session löschen
  GET    /api/testtool/sessions                 - Alle Sessions
"""

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import (
    settings,
    SoapInstitut,
    SoapService,
    SoapOperation,
    SoapParameter,
)

router = APIRouter(prefix="/api/testtool", tags=["testtool"])


# ══════════════════════════════════════════════════════════════════════════════
# Request Models
# ══════════════════════════════════════════════════════════════════════════════

class ConfigRequest(BaseModel):
    service_url: str = ""
    login_url: str = ""
    verify_ssl: bool = True


class InstitutRequest(BaseModel):
    institut_nr: str
    name: str = ""
    user: str = ""
    password: str = ""
    enabled: bool = True


class ParameterRequest(BaseModel):
    name: str
    type: str = "string"
    required: bool = False
    default: str = ""
    description: str = ""
    sensitive: bool = False
    values: List[str] = []


class OperationRequest(BaseModel):
    id: str = ""
    name: str
    description: str = ""
    template_file: str = ""
    soap_action: str = ""
    timeout_seconds: int = 60
    parameters: List[ParameterRequest] = []
    response_xpath: Dict[str, str] = {}


class ServiceRequest(BaseModel):
    name: str
    description: str = ""
    namespace: str = ""
    soap_version: str = "1.1"
    login_template: str = "login.soap.xml"
    session_token_xpath: str = "//SessionToken/text()"
    session_expires_xpath: str = ""
    error_codes_requiring_reauth: List[str] = []
    operations: List[OperationRequest] = []
    enabled: bool = True


class ExecuteRequest(BaseModel):
    institut_nr: str
    params: Dict[str, Any] = {}


class TemplateRequest(BaseModel):
    content: str


# ══════════════════════════════════════════════════════════════════════════════
# Config (Endpoints)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/config")
async def get_config() -> Dict[str, Any]:
    """Gibt die Test-Tool Konfiguration zurück."""
    return {
        "enabled": settings.test_tool.enabled,
        "service_url": settings.test_tool.service_url,
        "login_url": settings.test_tool.login_url,
        "verify_ssl": settings.test_tool.verify_ssl,
        "templates_path": settings.test_tool.templates_path,
        "institut_count": len(settings.test_tool.institute),
        "service_count": len(settings.test_tool.services),
    }


@router.put("/config")
async def update_config(req: ConfigRequest) -> Dict[str, Any]:
    """Aktualisiert die Endpoint-Konfiguration."""
    settings.test_tool.service_url = req.service_url
    settings.test_tool.login_url = req.login_url
    settings.test_tool.verify_ssl = req.verify_ssl

    return {
        "updated": True,
        "service_url": settings.test_tool.service_url,
        "login_url": settings.test_tool.login_url,
        "verify_ssl": settings.test_tool.verify_ssl,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Institute
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/institute")
async def list_institute() -> Dict[str, Any]:
    """Listet alle Institute auf."""
    return {
        "institute": [
            {
                "institut_nr": i.institut_nr,
                "name": i.name,
                "user": i.user,
                "password": "********" if i.password else "",
                "enabled": i.enabled,
            }
            for i in settings.test_tool.institute
        ]
    }


@router.post("/institute")
async def add_institut(req: InstitutRequest) -> Dict[str, Any]:
    """Fügt ein neues Institut hinzu."""
    # Prüfen ob bereits existiert
    existing = [i.institut_nr for i in settings.test_tool.institute]
    if req.institut_nr in existing:
        raise HTTPException(
            status_code=400,
            detail=f"Institut {req.institut_nr} existiert bereits"
        )

    institut = SoapInstitut(
        institut_nr=req.institut_nr,
        name=req.name,
        user=req.user,
        password=req.password,
        enabled=req.enabled,
    )
    settings.test_tool.institute.append(institut)

    return {"added": {"institut_nr": institut.institut_nr, "name": institut.name}}


@router.put("/institute/{institut_nr}")
async def update_institut(institut_nr: str, req: InstitutRequest) -> Dict[str, Any]:
    """Aktualisiert ein Institut."""
    for i, inst in enumerate(settings.test_tool.institute):
        if inst.institut_nr == institut_nr:
            # Password nicht überschreiben wenn "********"
            password = req.password if req.password != "********" else inst.password

            settings.test_tool.institute[i] = SoapInstitut(
                institut_nr=institut_nr,
                name=req.name,
                user=req.user,
                password=password,
                enabled=req.enabled,
            )
            return {"updated": {"institut_nr": institut_nr, "name": req.name}}

    raise HTTPException(status_code=404, detail=f"Institut {institut_nr} nicht gefunden")


@router.delete("/institute/{institut_nr}")
async def delete_institut(institut_nr: str) -> Dict[str, Any]:
    """Löscht ein Institut."""
    before = len(settings.test_tool.institute)
    settings.test_tool.institute = [
        i for i in settings.test_tool.institute if i.institut_nr != institut_nr
    ]

    if len(settings.test_tool.institute) == before:
        raise HTTPException(status_code=404, detail=f"Institut {institut_nr} nicht gefunden")

    # Session invalidieren
    from app.services.test_session_manager import get_session_manager
    get_session_manager().invalidate(institut_nr)

    return {"deleted": institut_nr}


# ══════════════════════════════════════════════════════════════════════════════
# Services
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/services")
async def list_services() -> Dict[str, Any]:
    """Listet alle Services mit Operationen auf."""
    services = []
    for svc in settings.test_tool.services:
        services.append({
            "id": svc.id,
            "name": svc.name,
            "description": svc.description,
            "enabled": svc.enabled,
            "soap_version": svc.soap_version,
            "operation_count": len(svc.operations),
            "operations": [
                {
                    "id": op.id,
                    "name": op.name,
                    "description": op.description,
                    "parameters": [
                        {
                            "name": p.name,
                            "type": p.type,
                            "required": p.required,
                            "default_value": p.default,
                            "description": p.description,
                        }
                        for p in op.parameters
                        # Filter out auto-injected params
                        if p.name not in ('institut', 'session_token', 'user', 'password')
                    ],
                }
                for op in svc.operations
            ],
        })
    return {"services": services}


@router.post("/services")
async def create_service(req: ServiceRequest) -> Dict[str, Any]:
    """Erstellt einen neuen Service."""
    service_id = str(uuid.uuid4())[:8]

    operations = []
    for op_req in req.operations:
        op_id = op_req.id or str(uuid.uuid4())[:8]
        operations.append(SoapOperation(
            id=op_id,
            name=op_req.name,
            description=op_req.description,
            template_file=op_req.template_file or f"{op_id}.soap.xml",
            soap_action=op_req.soap_action,
            timeout_seconds=op_req.timeout_seconds,
            parameters=[SoapParameter(**p.model_dump()) for p in op_req.parameters],
            response_xpath=op_req.response_xpath,
        ))

    service = SoapService(
        id=service_id,
        name=req.name,
        description=req.description,
        namespace=req.namespace,
        soap_version=req.soap_version,
        login_template=req.login_template,
        session_token_xpath=req.session_token_xpath,
        session_expires_xpath=req.session_expires_xpath,
        error_codes_requiring_reauth=req.error_codes_requiring_reauth or ["SESSION_EXPIRED", "INVALID_TOKEN"],
        operations=operations,
        enabled=req.enabled,
    )

    settings.test_tool.services.append(service)
    return {"added": {"id": service.id, "name": service.name}}


@router.get("/services/{service_id}")
async def get_service(service_id: str) -> Dict[str, Any]:
    """Gibt Service-Details zurück."""
    service = next((s for s in settings.test_tool.services if s.id == service_id), None)
    if not service:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' nicht gefunden")

    return {"service": service.model_dump()}


@router.put("/services/{service_id}")
async def update_service(service_id: str, req: ServiceRequest) -> Dict[str, Any]:
    """Aktualisiert einen Service."""
    for i, svc in enumerate(settings.test_tool.services):
        if svc.id == service_id:
            operations = []
            for op_req in req.operations:
                op_id = op_req.id or str(uuid.uuid4())[:8]
                operations.append(SoapOperation(
                    id=op_id,
                    name=op_req.name,
                    description=op_req.description,
                    template_file=op_req.template_file or f"{op_id}.soap.xml",
                    soap_action=op_req.soap_action,
                    timeout_seconds=op_req.timeout_seconds,
                    parameters=[SoapParameter(**p.model_dump()) for p in op_req.parameters],
                    response_xpath=op_req.response_xpath,
                ))

            settings.test_tool.services[i] = SoapService(
                id=service_id,
                name=req.name,
                description=req.description,
                namespace=req.namespace,
                soap_version=req.soap_version,
                login_template=req.login_template,
                session_token_xpath=req.session_token_xpath,
                session_expires_xpath=req.session_expires_xpath,
                error_codes_requiring_reauth=req.error_codes_requiring_reauth or ["SESSION_EXPIRED", "INVALID_TOKEN"],
                operations=operations,
                enabled=req.enabled,
            )
            return {"updated": {"id": service_id, "name": req.name}}

    raise HTTPException(status_code=404, detail=f"Service '{service_id}' nicht gefunden")


@router.delete("/services/{service_id}")
async def delete_service(service_id: str) -> Dict[str, Any]:
    """Löscht einen Service."""
    before = len(settings.test_tool.services)
    settings.test_tool.services = [s for s in settings.test_tool.services if s.id != service_id]

    if len(settings.test_tool.services) == before:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' nicht gefunden")

    return {"deleted": service_id}


# ══════════════════════════════════════════════════════════════════════════════
# Operations
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/services/{service_id}/operations")
async def add_operation(service_id: str, req: OperationRequest) -> Dict[str, Any]:
    """Fügt eine Operation zu einem Service hinzu."""
    service = next((s for s in settings.test_tool.services if s.id == service_id), None)
    if not service:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' nicht gefunden")

    op_id = req.id or str(uuid.uuid4())[:8]

    operation = SoapOperation(
        id=op_id,
        name=req.name,
        description=req.description,
        template_file=req.template_file or f"{op_id}.soap.xml",
        soap_action=req.soap_action,
        timeout_seconds=req.timeout_seconds,
        parameters=[SoapParameter(**p.model_dump()) for p in req.parameters],
        response_xpath=req.response_xpath,
    )

    service.operations.append(operation)
    return {"added": {"id": op_id, "name": req.name}}


@router.put("/services/{service_id}/operations/{operation_id}")
async def update_operation(service_id: str, operation_id: str, req: OperationRequest) -> Dict[str, Any]:
    """Aktualisiert eine Operation."""
    service = next((s for s in settings.test_tool.services if s.id == service_id), None)
    if not service:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' nicht gefunden")

    for i, op in enumerate(service.operations):
        if op.id == operation_id:
            service.operations[i] = SoapOperation(
                id=operation_id,
                name=req.name,
                description=req.description,
                template_file=req.template_file or f"{operation_id}.soap.xml",
                soap_action=req.soap_action,
                timeout_seconds=req.timeout_seconds,
                parameters=[SoapParameter(**p.model_dump()) for p in req.parameters],
                response_xpath=req.response_xpath,
            )
            return {"updated": {"id": operation_id, "name": req.name}}

    raise HTTPException(status_code=404, detail=f"Operation '{operation_id}' nicht gefunden")


@router.delete("/services/{service_id}/operations/{operation_id}")
async def delete_operation(service_id: str, operation_id: str) -> Dict[str, Any]:
    """Löscht eine Operation."""
    service = next((s for s in settings.test_tool.services if s.id == service_id), None)
    if not service:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' nicht gefunden")

    before = len(service.operations)
    service.operations = [op for op in service.operations if op.id != operation_id]

    if len(service.operations) == before:
        raise HTTPException(status_code=404, detail=f"Operation '{operation_id}' nicht gefunden")

    return {"deleted": operation_id}


# ══════════════════════════════════════════════════════════════════════════════
# Templates
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/templates/{service_id}/{operation_id}")
async def get_template(service_id: str, operation_id: str) -> Dict[str, Any]:
    """Gibt Template-XML zurück."""
    from app.services.test_template_engine import get_template_engine
    engine = get_template_engine()

    try:
        content = engine.load_template(service_id, operation_id)
        params = engine.extract_parameters(content)
        return {
            "content": content,
            "parameters": params,
            "service_id": service_id,
            "operation_id": operation_id,
        }
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Template nicht gefunden: {service_id}/{operation_id}"
        )


@router.put("/templates/{service_id}/{operation_id}")
async def save_template(service_id: str, operation_id: str, req: TemplateRequest) -> Dict[str, Any]:
    """Speichert Template-XML."""
    from app.services.test_template_engine import get_template_engine
    engine = get_template_engine()

    validation = engine.validate_template(req.content)
    if not validation['valid']:
        raise HTTPException(
            status_code=400,
            detail=f"Ungültiges Template: {', '.join(validation['errors'])}"
        )

    path = engine.save_template(service_id, operation_id, req.content)

    return {
        "saved": str(path),
        "parameters": validation['parameters'],
        "warnings": validation['warnings'],
    }


@router.post("/templates/validate")
async def validate_template(req: TemplateRequest) -> Dict[str, Any]:
    """Validiert ein Template."""
    from app.services.test_template_engine import get_template_engine
    engine = get_template_engine()
    return engine.validate_template(req.content)


# ══════════════════════════════════════════════════════════════════════════════
# Execution
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/execute/{service_id}/{operation_id}")
async def execute_operation(service_id: str, operation_id: str, req: ExecuteRequest) -> Dict[str, Any]:
    """
    Führt eine Test-Operation für ein Institut aus.

    Session-Management erfolgt automatisch:
    - Login bei fehlendem Token
    - Re-Login bei Auth-Fehlern
    """
    # Service finden
    service = next((s for s in settings.test_tool.services if s.id == service_id), None)
    if not service:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' nicht gefunden")

    # Operation finden
    operation = next((op for op in service.operations if op.id == operation_id), None)
    if not operation:
        raise HTTPException(status_code=404, detail=f"Operation '{operation_id}' nicht gefunden")

    # Institut prüfen
    institut = next(
        (i for i in settings.test_tool.institute if i.institut_nr == req.institut_nr and i.enabled),
        None
    )
    if not institut:
        available = [i.institut_nr for i in settings.test_tool.institute if i.enabled]
        raise HTTPException(
            status_code=400,
            detail=f"Institut '{req.institut_nr}' nicht gefunden oder deaktiviert. Verfügbar: {available}"
        )

    # Ausführen
    from app.services.test_executor import get_test_executor
    executor = get_test_executor()

    result = await executor.execute(service, operation, req.institut_nr, req.params)

    return {
        "success": result.success,
        "status_code": result.status_code,
        "data": result.data,
        "elapsed_ms": result.elapsed_ms,
        "institut_nr": result.institut_nr,
        "fault_code": result.fault_code,
        "fault_message": result.fault_message,
        "error": result.error,
        "request_xml": result.request_xml if not result.success else None,
        "response_xml": result.raw_xml[:2000] if not result.success else None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Session Management
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/session/{institut_nr}")
async def get_session_status(institut_nr: str) -> Dict[str, Any]:
    """Gibt Session-Status für ein Institut zurück."""
    from app.services.test_session_manager import get_session_manager
    manager = get_session_manager()

    status = manager.get_status(institut_nr)

    # Institut-Name hinzufügen
    institut = next(
        (i for i in settings.test_tool.institute if i.institut_nr == institut_nr),
        None
    )

    return {
        "institut_nr": institut_nr,
        "institut_name": institut.name if institut else "",
        "has_token": status.has_token,
        "is_expired": status.is_expired,
        "expires_at": status.expires_at.isoformat() if status.expires_at else None,
        "user": status.user,
        "token_preview": status.token_preview,
    }


@router.post("/session/{institut_nr}/login")
async def force_login(institut_nr: str) -> Dict[str, Any]:
    """Erzwingt neuen Login für ein Institut."""
    from app.services.test_session_manager import get_session_manager
    manager = get_session_manager()

    # Institut prüfen
    institut = next(
        (i for i in settings.test_tool.institute if i.institut_nr == institut_nr and i.enabled),
        None
    )
    if not institut:
        raise HTTPException(status_code=404, detail=f"Institut {institut_nr} nicht gefunden")

    try:
        token = await manager.get_token(institut_nr, force_refresh=True)
        status = manager.get_status(institut_nr)

        return {
            "success": True,
            "institut_nr": institut_nr,
            "user": status.user,
            "expires_at": status.expires_at.isoformat() if status.expires_at else None,
            "token_preview": token[:8] + '...' if len(token) > 8 else token,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/session/{institut_nr}")
async def logout(institut_nr: str) -> Dict[str, Any]:
    """Löscht Session-Token für ein Institut."""
    from app.services.test_session_manager import get_session_manager
    manager = get_session_manager()

    manager.invalidate(institut_nr)
    return {"deleted": True, "institut_nr": institut_nr}


@router.get("/sessions")
async def list_all_sessions() -> Dict[str, Any]:
    """Gibt alle aktiven Sessions zurück."""
    from app.services.test_session_manager import get_session_manager
    manager = get_session_manager()

    sessions = manager.get_all_sessions()

    return {
        "sessions": {
            institut_nr: {
                "has_token": status.has_token,
                "is_expired": status.is_expired,
                "expires_at": status.expires_at.isoformat() if status.expires_at else None,
                "user": status.user,
            }
            for institut_nr, status in sessions.items()
        }
    }
