"""
SOAP Test-Tool v2 Agent Tools (Multi-Institut).

Ermöglicht der KI:
- Services, Operationen und Institute auflisten
- SOAP-Operationen für ein Institut ausführen
- Session-Status prüfen
- Templates lesen/speichern
"""

import json
import logging
from typing import Any

from app.agent.tools import Tool, ToolCategory, ToolParameter, ToolResult, ToolRegistry

logger = logging.getLogger(__name__)


def register_soap_tools(registry: ToolRegistry) -> int:
    """
    Registriert SOAP Test-Tool v2 Agent-Tools.

    Returns:
        Anzahl registrierter Tools
    """
    from app.core.config import settings

    if not settings.soap_tool.enabled:
        return 0

    count = 0

    # ══════════════════════════════════════════════════════════════════════════
    # soap_list_services
    # ══════════════════════════════════════════════════════════════════════════

    async def soap_list_services(**kwargs: Any) -> ToolResult:
        """Listet alle SOAP-Services und Institute auf."""
        services = []

        for svc in settings.soap_tool.services:
            if not svc.enabled:
                continue

            operations = []
            for op in svc.operations:
                user_params = [
                    {
                        'name': p.name,
                        'type': p.type,
                        'required': p.required,
                        'description': p.description,
                    }
                    for p in op.parameters
                    if p.name not in ('institut', 'session_token', 'user', 'password')
                ]

                operations.append({
                    'id': op.id,
                    'name': op.name,
                    'description': op.description,
                    'parameters': user_params,
                })

            services.append({
                'id': svc.id,
                'name': svc.name,
                'description': svc.description,
                'operations': operations,
            })

        institute = [
            {'institut_nr': i.institut_nr, 'name': i.name}
            for i in settings.soap_tool.institute
            if i.enabled
        ]

        return ToolResult(
            success=True,
            data={
                'services': services,
                'institute': institute,
                'service_url': settings.soap_tool.service_url,
                'login_url': settings.soap_tool.login_url,
            }
        )

    registry.register(Tool(
        name="soap_list_services",
        description=(
            "Listet alle verfügbaren SOAP-Services, ihre Operationen und konfigurierte Institute auf. "
            "Zeigt Service-Namen, Beschreibungen und benötigte Parameter. "
            "WICHTIG: Jeder Service-Aufruf benötigt eine Institut-Nummer."
        ),
        category=ToolCategory.SEARCH,
        parameters=[],
        handler=soap_list_services,
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════
    # soap_execute
    # ══════════════════════════════════════════════════════════════════════════

    async def soap_execute(**kwargs: Any) -> ToolResult:
        """Führt eine SOAP-Operation für ein Institut aus."""
        service_id: str = kwargs.get('service_id', '')
        operation_id: str = kwargs.get('operation_id', '')
        institut_nr: str = kwargs.get('institut_nr', '')
        params_str: str = kwargs.get('params', '{}')

        if not institut_nr:
            available = [i.institut_nr for i in settings.soap_tool.institute if i.enabled]
            return ToolResult(
                success=False,
                error=f"institut_nr ist erforderlich. Verfügbare Institute: {available}"
            )

        # Service finden
        service = next(
            (s for s in settings.soap_tool.services if s.id == service_id and s.enabled),
            None
        )
        if not service:
            available = [s.id for s in settings.soap_tool.services if s.enabled]
            return ToolResult(
                success=False,
                error=f"Service '{service_id}' nicht gefunden. Verfügbar: {available}"
            )

        # Operation finden
        operation = next(
            (op for op in service.operations if op.id == operation_id),
            None
        )
        if not operation:
            available = [op.id for op in service.operations]
            return ToolResult(
                success=False,
                error=f"Operation '{operation_id}' nicht gefunden. Verfügbar: {available}"
            )

        # Institut prüfen
        institut = next(
            (i for i in settings.soap_tool.institute if i.institut_nr == institut_nr and i.enabled),
            None
        )
        if not institut:
            available = [i.institut_nr for i in settings.soap_tool.institute if i.enabled]
            return ToolResult(
                success=False,
                error=f"Institut '{institut_nr}' nicht verfügbar. Verfügbar: {available}"
            )

        # Parameter parsen
        try:
            params = json.loads(params_str) if params_str else {}
        except json.JSONDecodeError as e:
            return ToolResult(success=False, error=f"Ungültige JSON-Parameter: {e}")

        # Ausführen
        from app.services.soap_executor import get_soap_executor
        executor = get_soap_executor()

        try:
            result = await executor.execute(service, operation, institut_nr, params)
        except Exception as e:
            logger.exception(f"SOAP-Ausführung fehlgeschlagen: {e}")
            return ToolResult(success=False, error=f"Ausführung fehlgeschlagen: {e}")

        if result.success:
            return ToolResult(
                success=True,
                data={
                    'status_code': result.status_code,
                    'response': result.data,
                    'elapsed_ms': result.elapsed_ms,
                    'institut_nr': result.institut_nr,
                    'operation': operation.name,
                }
            )
        else:
            return ToolResult(
                success=False,
                data={
                    'status_code': result.status_code,
                    'fault_code': result.fault_code,
                    'fault_message': result.fault_message,
                    'elapsed_ms': result.elapsed_ms,
                    'institut_nr': result.institut_nr,
                },
                error=result.error or result.fault_message or 'SOAP-Fault'
            )

    registry.register(Tool(
        name="soap_execute",
        description=(
            "Führt eine SOAP-Operation für ein bestimmtes Institut aus. "
            "WICHTIG: institut_nr ist immer erforderlich - sie bestimmt welches Institut "
            "(und damit welche Credentials/Session) verwendet wird. "
            "Das Session-Management erfolgt automatisch (Login bei fehlendem Token, Re-Login bei Auth-Fehlern). "
            "Nutze zuerst soap_list_services um verfügbare Services, Operationen und Institute zu sehen."
        ),
        category=ToolCategory.FILE,
        is_write_operation=True,
        parameters=[
            ToolParameter(
                name="service_id",
                type="string",
                description="ID des Services (z.B. 'customer')",
                required=True
            ),
            ToolParameter(
                name="operation_id",
                type="string",
                description="ID der Operation (z.B. 'get_customer')",
                required=True
            ),
            ToolParameter(
                name="institut_nr",
                type="string",
                description="Institut-Nummer (z.B. '001', '002'). ERFORDERLICH!",
                required=True
            ),
            ToolParameter(
                name="params",
                type="string",
                description='Weitere Parameter als JSON, z.B. {"customer_id": "12345"}',
                required=False
            ),
        ],
        handler=soap_execute,
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════
    # soap_get_session_status
    # ══════════════════════════════════════════════════════════════════════════

    async def soap_get_session_status(**kwargs: Any) -> ToolResult:
        """Prüft Session-Status für ein Institut."""
        institut_nr: str = kwargs.get('institut_nr', '')

        if not institut_nr:
            # Alle Sessions zurückgeben
            from app.services.soap_session_manager import get_session_manager
            manager = get_session_manager()
            sessions = manager.get_all_sessions()

            return ToolResult(
                success=True,
                data={
                    'sessions': {
                        nr: {
                            'has_token': s.has_token,
                            'is_expired': s.is_expired,
                            'user': s.user,
                        }
                        for nr, s in sessions.items()
                    }
                }
            )

        from app.services.soap_session_manager import get_session_manager
        manager = get_session_manager()
        status = manager.get_status(institut_nr)

        # Institut-Name
        institut = next(
            (i for i in settings.soap_tool.institute if i.institut_nr == institut_nr),
            None
        )

        return ToolResult(
            success=True,
            data={
                'institut_nr': institut_nr,
                'institut_name': institut.name if institut else '',
                'has_token': status.has_token,
                'is_expired': status.is_expired,
                'expires_at': status.expires_at.isoformat() if status.expires_at else None,
                'user': status.user,
                'message': (
                    'Token gültig' if status.has_token and not status.is_expired
                    else 'Token abgelaufen' if status.is_expired
                    else 'Kein Token - Login wird beim nächsten Aufruf automatisch durchgeführt'
                ),
            }
        )

    registry.register(Tool(
        name="soap_get_session_status",
        description=(
            "Prüft ob ein gültiger Session-Token für ein Institut existiert. "
            "Zeigt Token-Status, Ablaufzeit und Benutzer. "
            "Ohne institut_nr werden alle Sessions angezeigt."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="institut_nr",
                type="string",
                description="Institut-Nummer (optional, ohne = alle Sessions)",
                required=False
            ),
        ],
        handler=soap_get_session_status,
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════
    # soap_get_template
    # ══════════════════════════════════════════════════════════════════════════

    async def soap_get_template(**kwargs: Any) -> ToolResult:
        """Lädt ein SOAP-Template."""
        service_id: str = kwargs.get('service_id', '')
        template_file: str = kwargs.get('template_file', '')

        if not template_file:
            return ToolResult(success=False, error="template_file ist erforderlich")

        from app.services.soap_template_engine import get_template_engine
        engine = get_template_engine()

        try:
            content = engine.load_template(service_id, template_file)
            params = engine.extract_parameters(content, include_auto_inject=True)

            return ToolResult(
                success=True,
                data={
                    'service_id': service_id,
                    'template_file': template_file,
                    'template': content,
                    'parameters': params,
                }
            )
        except FileNotFoundError:
            return ToolResult(
                success=False,
                error=f"Template nicht gefunden: {service_id}/{template_file}"
            )

    registry.register(Tool(
        name="soap_get_template",
        description=(
            "Lädt ein SOAP-XML-Template. "
            "Zeigt das Template mit allen Platzhaltern ({{param}}) und deren Definitionen."
        ),
        category=ToolCategory.SEARCH,
        parameters=[
            ToolParameter(
                name="service_id",
                type="string",
                description="ID des Services (Unterverzeichnis)",
                required=False
            ),
            ToolParameter(
                name="template_file",
                type="string",
                description="Template-Dateiname (z.B. 'login.soap.xml')",
                required=True
            ),
        ],
        handler=soap_get_template,
    ))
    count += 1

    # ══════════════════════════════════════════════════════════════════════════
    # soap_save_template
    # ══════════════════════════════════════════════════════════════════════════

    async def soap_save_template(**kwargs: Any) -> ToolResult:
        """Speichert ein SOAP-Template."""
        service_id: str = kwargs.get('service_id', '')
        template_file: str = kwargs.get('template_file', '')
        content: str = kwargs.get('content', '')

        if not template_file:
            return ToolResult(success=False, error="template_file ist erforderlich")
        if not content:
            return ToolResult(success=False, error="content (Template-XML) ist erforderlich")

        from app.services.soap_template_engine import get_template_engine
        engine = get_template_engine()

        validation = engine.validate_template(content)
        if not validation['valid']:
            return ToolResult(
                success=False,
                error=f"Ungültiges Template: {', '.join(validation['errors'])}"
            )

        path = engine.save_template(service_id, template_file)

        return ToolResult(
            success=True,
            data={
                'saved_to': str(path),
                'service_id': service_id,
                'template_file': template_file,
                'parameters': validation['parameters'],
                'warnings': validation['warnings'],
            }
        )

    registry.register(Tool(
        name="soap_save_template",
        description=(
            "Speichert oder erstellt ein SOAP-XML-Template. "
            "Das Template wird vor dem Speichern validiert. "
            "Nutze Platzhalter: {{institut}}, {{session_token}}, {{param_name}}, {{param:default}}"
        ),
        category=ToolCategory.FILE,
        is_write_operation=True,
        parameters=[
            ToolParameter(
                name="service_id",
                type="string",
                description="ID des Services (Unterverzeichnis)",
                required=False
            ),
            ToolParameter(
                name="template_file",
                type="string",
                description="Template-Dateiname (z.B. 'get_customer.soap.xml')",
                required=True
            ),
            ToolParameter(
                name="content",
                type="string",
                description="Template-XML mit Platzhaltern",
                required=True
            ),
        ],
        handler=soap_save_template,
    ))
    count += 1

    logger.info(f"[soap_tools] {count} SOAP-Tools registriert")
    return count
