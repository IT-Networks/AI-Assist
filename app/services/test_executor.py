"""
Test Executor - Führt SOAP-Requests aus mit automatischem Session-Management.

Features:
- Institut-basiertes Session-Management
- Automatisches Login bei fehlendem Token
- Auto-Retry bei Auth-Fehlern
- Response-Parsing (SOAP-Fault Erkennung)
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from xml.etree import ElementTree as ET

import httpx

from app.core.config import SoapService, SoapOperation
from app.services.test_session_manager import TestSessionManager, get_session_manager
from app.services.test_template_engine import TestTemplateEngine, get_template_engine

logger = logging.getLogger(__name__)


@dataclass
class SoapExecutionResult:
    """Ergebnis einer SOAP-Operation."""
    success: bool
    status_code: int = 0
    data: Dict[str, Any] = field(default_factory=dict)
    raw_xml: str = ""
    request_xml: str = ""
    elapsed_ms: int = 0
    institut_nr: str = ""
    # Fault-Informationen
    fault_code: Optional[str] = None
    fault_message: Optional[str] = None
    fault_detail: Optional[str] = None
    # Fehler
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Konvertiert zu Dictionary für API-Response."""
        return {
            'success': self.success,
            'status_code': self.status_code,
            'data': self.data,
            'elapsed_ms': self.elapsed_ms,
            'institut_nr': self.institut_nr,
            'fault_code': self.fault_code,
            'fault_message': self.fault_message,
            'error': self.error,
        }


class TestExecutor:
    """
    Führt SOAP-Requests aus mit automatischem Session-Management pro Institut.

    Flow:
    1. Session-Token für Institut holen (Login wenn nötig)
    2. Template laden und füllen (mit institut, session_token)
    3. HTTP-Request an service_url ausführen
    4. Response parsen (Fault erkennen)
    5. Bei Auth-Fehler: Re-Login und Retry
    """

    def __init__(
        self,
        session_manager: Optional[TestSessionManager] = None,
        template_engine: Optional[TestTemplateEngine] = None
    ):
        self._session_manager = session_manager
        self._template_engine = template_engine

    @property
    def sessions(self) -> TestSessionManager:
        if self._session_manager is None:
            self._session_manager = get_session_manager()
        return self._session_manager

    @property
    def templates(self) -> TestTemplateEngine:
        if self._template_engine is None:
            self._template_engine = get_template_engine()
        return self._template_engine

    async def execute(
        self,
        service: SoapService,
        operation: SoapOperation,
        institut_nr: str,
        params: Dict[str, Any],
        retry_on_auth_error: bool = True
    ) -> SoapExecutionResult:
        """
        Führt eine SOAP-Operation für ein Institut aus.

        Args:
            service: Service-Definition
            operation: Operation-Definition
            institut_nr: Institut-Nummer (bestimmt Credentials/Session)
            params: User-Parameter
            retry_on_auth_error: Bei Auth-Fehler automatisch re-login?

        Returns:
            SoapExecutionResult
        """
        from app.core.config import settings

        start_time = time.time()

        try:
            # 1. Service-URL prüfen
            if not settings.test_tool.service_url:
                return SoapExecutionResult(
                    success=False,
                    error="service_url nicht konfiguriert",
                    institut_nr=institut_nr,
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

            # 2. Session-Token für Institut holen
            try:
                token = await self.sessions.get_token(institut_nr)
            except ValueError as e:
                return SoapExecutionResult(
                    success=False,
                    error=f"Login fehlgeschlagen: {e}",
                    institut_nr=institut_nr,
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

            # 3. Template laden
            try:
                template = self.templates.load_template(service.id, operation.template_file)
            except FileNotFoundError:
                # Fallback: Nur operation.template_file ohne Service-Prefix
                try:
                    template = self.templates.load_template("", operation.template_file)
                except FileNotFoundError as e:
                    return SoapExecutionResult(
                        success=False,
                        error=str(e),
                        institut_nr=institut_nr,
                        elapsed_ms=int((time.time() - start_time) * 1000)
                    )

            # 4. Template füllen
            auto_params = {
                'institut': institut_nr,
                'session_token': token,
            }

            try:
                envelope = self.templates.fill_template(template, params, auto_params)
            except ValueError as e:
                return SoapExecutionResult(
                    success=False,
                    error=f"Template-Fehler: {e}",
                    institut_nr=institut_nr,
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

            # 5. Headers
            headers = self._get_soap_headers(service, operation)

            # 6. Request ausführen
            logger.debug(f"SOAP Request: {operation.name} für Institut {institut_nr}")

            try:
                async with httpx.AsyncClient(
                    timeout=operation.timeout_seconds,
                    verify=settings.test_tool.verify_ssl
                ) as client:
                    response = await client.post(
                        settings.test_tool.service_url,
                        content=envelope.encode('utf-8'),
                        headers=headers
                    )
            except httpx.TimeoutException:
                return SoapExecutionResult(
                    success=False,
                    error=f"Timeout nach {operation.timeout_seconds}s",
                    request_xml=envelope,
                    institut_nr=institut_nr,
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )
            except Exception as e:
                return SoapExecutionResult(
                    success=False,
                    error=f"HTTP-Fehler: {e}",
                    request_xml=envelope,
                    institut_nr=institut_nr,
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

            # 7. Response parsen
            result = self._parse_response(response, service, operation)
            result.request_xml = envelope
            result.institut_nr = institut_nr
            result.elapsed_ms = int((time.time() - start_time) * 1000)

            # 8. Auth-Error? Retry mit neuem Token
            if not result.success and retry_on_auth_error:
                if self._is_auth_error(result, service):
                    logger.info(f"Auth-Fehler erkannt, versuche Re-Login für Institut {institut_nr}")
                    self.sessions.invalidate(institut_nr)

                    return await self.execute(
                        service, operation, institut_nr, params,
                        retry_on_auth_error=False
                    )

            return result

        except Exception as e:
            logger.exception(f"Unerwarteter Fehler bei SOAP-Ausführung: {e}")
            return SoapExecutionResult(
                success=False,
                error=f"Unerwarteter Fehler: {e}",
                institut_nr=institut_nr,
                elapsed_ms=int((time.time() - start_time) * 1000)
            )

    def _get_soap_headers(
        self,
        service: SoapService,
        operation: SoapOperation
    ) -> Dict[str, str]:
        """Generiert HTTP-Headers für SOAP-Request."""
        headers = {}

        if service.soap_version == "1.2":
            content_type = 'application/soap+xml; charset=utf-8'
            if operation.soap_action:
                content_type += f'; action="{operation.soap_action}"'
            headers['Content-Type'] = content_type
        else:
            headers['Content-Type'] = 'text/xml; charset=utf-8'
            if operation.soap_action:
                headers['SOAPAction'] = f'"{operation.soap_action}"'

        return headers

    def _parse_response(
        self,
        response: httpx.Response,
        service: SoapService,
        operation: SoapOperation
    ) -> SoapExecutionResult:
        """Parst SOAP-Response."""
        raw_xml = response.text

        # HTTP-Fehler?
        if response.status_code >= 500:
            return SoapExecutionResult(
                success=False,
                status_code=response.status_code,
                raw_xml=raw_xml,
                error=f"Server-Fehler: HTTP {response.status_code}"
            )

        # XML parsen
        try:
            root = ET.fromstring(raw_xml)
        except ET.ParseError as e:
            return SoapExecutionResult(
                success=False,
                status_code=response.status_code,
                raw_xml=raw_xml,
                error=f"XML-Parse-Fehler: {e}"
            )

        # SOAP-Fault prüfen
        fault = self._find_soap_fault(root)
        if fault:
            return SoapExecutionResult(
                success=False,
                status_code=response.status_code,
                raw_xml=raw_xml,
                fault_code=fault.get('code'),
                fault_message=fault.get('message'),
                fault_detail=fault.get('detail')
            )

        # Daten extrahieren
        data = {}

        # Automatische Extraktion aus Body
        body = self._find_soap_body(root)
        if body is not None:
            data = self._element_to_dict(body)

        # Spezifische XPath-Extraktionen
        if operation.response_xpath:
            for key, xpath in operation.response_xpath.items():
                extracted = self._extract_xpath(root, xpath)
                if extracted is not None:
                    data[key] = extracted

        return SoapExecutionResult(
            success=True,
            status_code=response.status_code,
            data=data,
            raw_xml=raw_xml
        )

    def _find_soap_fault(self, root: ET.Element) -> Optional[Dict[str, str]]:
        """Sucht SOAP-Fault in Response."""
        fault_searches = [
            './/{http://schemas.xmlsoap.org/soap/envelope/}Fault',
            './/{http://www.w3.org/2003/05/soap-envelope}Fault',
            './/{*}Fault',
        ]

        fault = None
        for search in fault_searches:
            fault = root.find(search)
            if fault is not None:
                break

        if fault is None:
            return None

        result = {}
        for child in fault:
            tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
            tag_lower = tag.lower()

            if tag_lower == 'faultcode':
                result['code'] = child.text
            elif tag_lower == 'faultstring':
                result['message'] = child.text
            elif tag_lower == 'detail':
                result['detail'] = ET.tostring(child, encoding='unicode')
            elif tag_lower == 'code':
                value = child.find('.//{*}Value')
                result['code'] = value.text if value is not None else child.text
            elif tag_lower == 'reason':
                text = child.find('.//{*}Text')
                result['message'] = text.text if text is not None else child.text

        return result

    def _find_soap_body(self, root: ET.Element) -> Optional[ET.Element]:
        """Findet SOAP-Body in Response."""
        searches = [
            './/{http://schemas.xmlsoap.org/soap/envelope/}Body',
            './/{http://www.w3.org/2003/05/soap-envelope}Body',
            './/{*}Body',
        ]

        for search in searches:
            body = root.find(search)
            if body is not None:
                return body

        return None

    def _element_to_dict(self, element: ET.Element) -> Dict[str, Any]:
        """Konvertiert XML-Element zu Dictionary."""
        result: Dict[str, Any] = {}

        for child in element:
            tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag

            if len(child) > 0:
                child_data = self._element_to_dict(child)
            else:
                child_data = child.text.strip() if child.text else None

            if tag in result:
                if not isinstance(result[tag], list):
                    result[tag] = [result[tag]]
                result[tag].append(child_data)
            else:
                result[tag] = child_data

        return result

    def _extract_xpath(self, root: ET.Element, xpath: str) -> Optional[str]:
        """Extrahiert Wert per XPath."""
        try:
            import re
            tag_match = re.search(r'//(\w+)', xpath)
            if tag_match:
                tag_name = tag_match.group(1)
                for elem in root.iter():
                    local = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
                    if local == tag_name:
                        return elem.text
        except Exception as e:
            logger.debug(f"XPath-Extraktion fehlgeschlagen: {e}")

        return None

    def _is_auth_error(self, result: SoapExecutionResult, service: SoapService) -> bool:
        """Prüft ob Response ein Auth-Fehler ist."""
        if result.status_code in (401, 403):
            return True

        error_codes = service.error_codes_requiring_reauth

        if result.fault_code:
            for error_code in error_codes:
                if error_code.lower() in result.fault_code.lower():
                    return True

        if result.fault_message:
            for error_code in error_codes:
                if error_code.lower() in result.fault_message.lower():
                    return True

        return False


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_executor: Optional[TestExecutor] = None


def get_test_executor() -> TestExecutor:
    """Gibt Singleton-Instanz des Executors zurück."""
    global _executor
    if _executor is None:
        _executor = TestExecutor()
    return _executor
