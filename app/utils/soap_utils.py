"""
SOAP/WSDL Utilities für AI-Assist.

Bietet:
- WSDL-Parsing (mit zeep oder lxml Fallback)
- SOAP-Envelope-Generierung
- Response-Parsing
- Namespace-Management

Abhängigkeiten:
- lxml (required): XML-Parsing
- zeep (optional): Vollständiges WSDL-Handling
"""

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple, Union
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

# Versuche zeep zu importieren (optional)
try:
    import zeep
    from zeep import Client as ZeepClient
    from zeep.exceptions import Fault as ZeepFault
    from zeep.transports import Transport
    ZEEP_AVAILABLE = True
except ImportError:
    ZEEP_AVAILABLE = False
    logger.debug("zeep nicht installiert - nutze lxml Fallback für WSDL-Parsing")

# Versuche lxml zu importieren
try:
    from lxml import etree
    LXML_AVAILABLE = True
except ImportError:
    LXML_AVAILABLE = False
    logger.warning("lxml nicht installiert - eingeschränkte XML-Funktionalität")


# ═══════════════════════════════════════════════════════════════════════════════
# Data Classes
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class WSDLParameter:
    """Ein Parameter einer WSDL-Operation."""
    name: str
    type: str
    required: bool = True
    is_complex: bool = False
    children: List["WSDLParameter"] = field(default_factory=list)


@dataclass
class WSDLOperation:
    """Eine WSDL-Operation (Methode)."""
    name: str
    input_params: List[WSDLParameter] = field(default_factory=list)
    output_type: str = "void"
    soap_action: str = ""
    documentation: str = ""


@dataclass
class WSDLService:
    """Geparseter WSDL-Service."""
    name: str
    endpoint: str
    target_namespace: str
    operations: List[WSDLOperation] = field(default_factory=list)
    namespaces: Dict[str, str] = field(default_factory=dict)
    soap_version: str = "1.1"  # "1.1" oder "1.2"


@dataclass
class SOAPResponse:
    """Geparsete SOAP-Response."""
    success: bool
    status_code: int
    data: Dict[str, Any] = field(default_factory=dict)
    raw_xml: str = ""
    fault_code: Optional[str] = None
    fault_message: Optional[str] = None
    fault_detail: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
# WSDL Parser
# ═══════════════════════════════════════════════════════════════════════════════

class WSDLParser:
    """
    Parser für WSDL-Dateien.

    Nutzt zeep wenn verfügbar, sonst lxml Fallback.
    """

    # Standard SOAP/WSDL Namespaces
    NS = {
        "wsdl": "http://schemas.xmlsoap.org/wsdl/",
        "soap": "http://schemas.xmlsoap.org/wsdl/soap/",
        "soap12": "http://schemas.xmlsoap.org/wsdl/soap12/",
        "xsd": "http://www.w3.org/2001/XMLSchema",
        "tns": "",  # Target namespace - wird dynamisch gesetzt
    }

    def __init__(self, wsdl_url: str, timeout: int = 30, verify_ssl: bool = True):
        """
        Args:
            wsdl_url: URL oder Pfad zur WSDL-Datei
            timeout: HTTP-Timeout in Sekunden
            verify_ssl: SSL-Zertifikate prüfen
        """
        self.wsdl_url = wsdl_url
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self._service: Optional[WSDLService] = None
        self._zeep_client: Optional[Any] = None

    def parse(self) -> WSDLService:
        """
        Parst die WSDL und gibt einen WSDLService zurück.

        Returns:
            WSDLService mit allen Operationen und Typen
        """
        if self._service:
            return self._service

        if ZEEP_AVAILABLE:
            self._service = self._parse_with_zeep()
        elif LXML_AVAILABLE:
            self._service = self._parse_with_lxml()
        else:
            raise RuntimeError(
                "Weder zeep noch lxml installiert. "
                "Installation: pip install lxml (oder pip install zeep für vollständige Unterstützung)"
            )

        return self._service

    def _parse_with_zeep(self) -> WSDLService:
        """Parst WSDL mit zeep (vollständig)."""
        import httpx

        # Transport mit Timeout konfigurieren
        session = httpx.Client(timeout=self.timeout, verify=self.verify_ssl)
        transport = Transport(session=session)

        try:
            client = ZeepClient(self.wsdl_url, transport=transport)
            self._zeep_client = client
        except Exception as e:
            raise ValueError(f"WSDL konnte nicht geladen werden: {e}")

        # Service-Informationen extrahieren
        wsdl = client.wsdl

        # Ersten Service und Port finden
        service_name = ""
        endpoint = ""

        for service in wsdl.services.values():
            service_name = service.name
            for port in service.ports.values():
                endpoint = port.binding_options.get("address", "")
                break
            break

        # Operationen extrahieren
        operations = []
        for service in wsdl.services.values():
            for port in service.ports.values():
                for op_name, operation in port.binding.all().items():
                    op = self._extract_zeep_operation(client, op_name, operation)
                    operations.append(op)

        return WSDLService(
            name=service_name,
            endpoint=endpoint,
            target_namespace=wsdl.target_namespace or "",
            operations=operations,
            namespaces=dict(wsdl.types.prefix_map) if wsdl.types else {},
            soap_version="1.2" if "soap12" in str(type(port.binding)).lower() else "1.1",
        )

    def _extract_zeep_operation(self, client, op_name: str, operation) -> WSDLOperation:
        """Extrahiert Details einer zeep-Operation."""
        input_params = []
        output_type = "void"
        soap_action = ""

        try:
            # Input-Parameter
            if hasattr(operation, "input") and operation.input:
                body = operation.input.body
                if body and hasattr(body, "type"):
                    input_params = self._extract_zeep_type(body.type)

            # Output-Typ
            if hasattr(operation, "output") and operation.output:
                body = operation.output.body
                if body and hasattr(body, "type"):
                    output_type = body.type.name if hasattr(body.type, "name") else str(body.type)

            # SOAPAction
            if hasattr(operation, "soapaction"):
                soap_action = operation.soapaction or ""
        except Exception as e:
            logger.debug(f"Fehler beim Extrahieren von Operation {op_name}: {e}")

        return WSDLOperation(
            name=op_name,
            input_params=input_params,
            output_type=output_type,
            soap_action=soap_action,
        )

    def _extract_zeep_type(self, zeep_type) -> List[WSDLParameter]:
        """Extrahiert Parameter aus einem zeep-Typ."""
        params = []

        try:
            if hasattr(zeep_type, "elements"):
                for name, element in zeep_type.elements:
                    param_type = "string"
                    is_complex = False
                    children = []

                    if hasattr(element, "type"):
                        el_type = element.type
                        if hasattr(el_type, "name"):
                            param_type = el_type.name
                        if hasattr(el_type, "elements") and el_type.elements:
                            is_complex = True
                            children = self._extract_zeep_type(el_type)

                    required = not getattr(element, "is_optional", False)

                    params.append(WSDLParameter(
                        name=name,
                        type=param_type,
                        required=required,
                        is_complex=is_complex,
                        children=children,
                    ))
        except Exception as e:
            logger.debug(f"Fehler beim Extrahieren von Typ: {e}")

        return params

    def _parse_with_lxml(self) -> WSDLService:
        """Parst WSDL mit lxml (Fallback, weniger vollständig)."""
        import httpx

        # WSDL laden
        if self.wsdl_url.startswith(("http://", "https://")):
            with httpx.Client(timeout=self.timeout, verify=self.verify_ssl) as client:
                response = client.get(self.wsdl_url)
                response.raise_for_status()
                wsdl_content = response.text
        else:
            # Lokale Datei
            with open(self.wsdl_url, "r", encoding="utf-8") as f:
                wsdl_content = f.read()

        # XML parsen
        root = etree.fromstring(wsdl_content.encode())

        # Namespaces extrahieren
        namespaces = dict(root.nsmap)
        if None in namespaces:
            namespaces["wsdl"] = namespaces.pop(None)

        # Target Namespace
        target_ns = root.get("targetNamespace", "")
        self.NS["tns"] = target_ns

        # Service-Name und Endpoint
        service_name = ""
        endpoint = ""

        service_elem = root.find(".//wsdl:service", namespaces) or root.find(".//{*}service")
        if service_elem is not None:
            service_name = service_elem.get("name", "UnknownService")

            # Port/Endpoint finden
            port_elem = service_elem.find(".//wsdl:port", namespaces) or service_elem.find(".//{*}port")
            if port_elem is not None:
                address_elem = port_elem.find(".//{*}address")
                if address_elem is not None:
                    endpoint = address_elem.get("location", "")

        # Operationen finden
        operations = []

        # Binding finden für SOAPAction
        binding_ops = {}
        for binding in root.findall(".//{*}binding"):
            for op in binding.findall(".//{*}operation"):
                op_name = op.get("name", "")
                soap_op = op.find(".//{*}operation")
                if soap_op is not None:
                    binding_ops[op_name] = soap_op.get("soapAction", "")

        # PortType Operationen
        for porttype in root.findall(".//{*}portType"):
            for op in porttype.findall(".//{*}operation"):
                op_name = op.get("name", "")

                # Input/Output Messages finden
                input_params = []
                output_type = "void"

                input_elem = op.find(".//{*}input")
                if input_elem is not None:
                    message_name = input_elem.get("message", "").split(":")[-1]
                    input_params = self._find_message_parts(root, message_name)

                output_elem = op.find(".//{*}output")
                if output_elem is not None:
                    message_name = output_elem.get("message", "").split(":")[-1]
                    output_type = message_name

                # Documentation
                doc_elem = op.find(".//{*}documentation")
                documentation = doc_elem.text if doc_elem is not None else ""

                operations.append(WSDLOperation(
                    name=op_name,
                    input_params=input_params,
                    output_type=output_type,
                    soap_action=binding_ops.get(op_name, ""),
                    documentation=documentation,
                ))

        # SOAP-Version erkennen
        soap_version = "1.1"
        if root.find(".//{http://schemas.xmlsoap.org/wsdl/soap12/}*") is not None:
            soap_version = "1.2"

        return WSDLService(
            name=service_name,
            endpoint=endpoint,
            target_namespace=target_ns,
            operations=operations,
            namespaces=namespaces,
            soap_version=soap_version,
        )

    def _find_message_parts(self, root, message_name: str) -> List[WSDLParameter]:
        """Findet die Parts einer WSDL-Message."""
        params = []

        for message in root.findall(".//{*}message"):
            if message.get("name", "") == message_name:
                for part in message.findall(".//{*}part"):
                    part_name = part.get("name", "")
                    part_type = part.get("type", part.get("element", ""))
                    # Namespace-Prefix entfernen
                    part_type = part_type.split(":")[-1] if ":" in part_type else part_type

                    params.append(WSDLParameter(
                        name=part_name,
                        type=part_type,
                        required=True,
                    ))

        return params

    def get_operation(self, operation_name: str) -> Optional[WSDLOperation]:
        """Gibt eine spezifische Operation zurück."""
        service = self.parse()
        for op in service.operations:
            if op.name.lower() == operation_name.lower():
                return op
        return None

    def get_zeep_client(self):
        """Gibt den zeep Client zurück (falls verfügbar)."""
        if not ZEEP_AVAILABLE:
            return None
        if not self._zeep_client:
            self.parse()
        return self._zeep_client


# ═══════════════════════════════════════════════════════════════════════════════
# SOAP Envelope Builder
# ═══════════════════════════════════════════════════════════════════════════════

class SOAPEnvelopeBuilder:
    """
    Baut SOAP-Envelopes für Requests.
    """

    SOAP11_NS = "http://schemas.xmlsoap.org/soap/envelope/"
    SOAP12_NS = "http://www.w3.org/2003/05/soap-envelope"

    def __init__(self, service: WSDLService):
        """
        Args:
            service: Geparseter WSDLService
        """
        self.service = service
        self.soap_ns = self.SOAP12_NS if service.soap_version == "1.2" else self.SOAP11_NS

    def build_envelope(
        self,
        operation: WSDLOperation,
        params: Dict[str, Any],
        headers: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Baut einen SOAP-Envelope für eine Operation.

        Args:
            operation: Die aufzurufende Operation
            params: Parameter als Dictionary
            headers: Optionale SOAP-Header

        Returns:
            XML-String des SOAP-Envelopes
        """
        # Namespace-Prefix für Service
        tns_prefix = "tns"
        tns_uri = self.service.target_namespace

        # Envelope erstellen
        envelope = f'''<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="{self.soap_ns}" xmlns:{tns_prefix}="{tns_uri}">'''

        # Header (optional)
        if headers:
            envelope += "\n  <soap:Header>"
            envelope += self._dict_to_xml(headers, indent=4)
            envelope += "\n  </soap:Header>"

        # Body
        envelope += "\n  <soap:Body>"
        envelope += f"\n    <{tns_prefix}:{operation.name}>"

        # Parameter hinzufügen
        if params:
            envelope += self._dict_to_xml(params, indent=6, prefix=tns_prefix)

        envelope += f"\n    </{tns_prefix}:{operation.name}>"
        envelope += "\n  </soap:Body>"
        envelope += "\n</soap:Envelope>"

        return envelope

    def _dict_to_xml(
        self,
        data: Dict[str, Any],
        indent: int = 0,
        prefix: Optional[str] = None,
    ) -> str:
        """Konvertiert ein Dictionary zu XML-Elementen."""
        xml = ""
        indent_str = " " * indent

        for key, value in data.items():
            tag = f"{prefix}:{key}" if prefix else key

            if isinstance(value, dict):
                xml += f"\n{indent_str}<{tag}>"
                xml += self._dict_to_xml(value, indent + 2, prefix)
                xml += f"\n{indent_str}</{tag}>"
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        xml += f"\n{indent_str}<{tag}>"
                        xml += self._dict_to_xml(item, indent + 2, prefix)
                        xml += f"\n{indent_str}</{tag}>"
                    else:
                        xml += f"\n{indent_str}<{tag}>{self._escape_xml(item)}</{tag}>"
            elif value is None:
                xml += f"\n{indent_str}<{tag}/>"
            else:
                xml += f"\n{indent_str}<{tag}>{self._escape_xml(value)}</{tag}>"

        return xml

    @staticmethod
    def _escape_xml(value: Any) -> str:
        """Escaped einen Wert für XML."""
        s = str(value)
        s = s.replace("&", "&amp;")
        s = s.replace("<", "&lt;")
        s = s.replace(">", "&gt;")
        s = s.replace('"', "&quot;")
        s = s.replace("'", "&apos;")
        return s

    def get_soap_headers(self, operation: WSDLOperation) -> Dict[str, str]:
        """Gibt die notwendigen HTTP-Header für einen SOAP-Request zurück."""
        headers = {
            "Content-Type": "text/xml; charset=utf-8" if self.service.soap_version == "1.1"
                           else "application/soap+xml; charset=utf-8",
        }

        if operation.soap_action and self.service.soap_version == "1.1":
            headers["SOAPAction"] = f'"{operation.soap_action}"'

        return headers


# ═══════════════════════════════════════════════════════════════════════════════
# SOAP Response Parser
# ═══════════════════════════════════════════════════════════════════════════════

class SOAPResponseParser:
    """
    Parser für SOAP-Responses.
    """

    SOAP11_NS = "http://schemas.xmlsoap.org/soap/envelope/"
    SOAP12_NS = "http://www.w3.org/2003/05/soap-envelope"

    def parse(self, xml_content: str, status_code: int = 200) -> SOAPResponse:
        """
        Parst eine SOAP-Response.

        Args:
            xml_content: XML-String der Response
            status_code: HTTP Status Code

        Returns:
            SOAPResponse mit geparsten Daten oder Fault-Informationen
        """
        try:
            if LXML_AVAILABLE:
                return self._parse_with_lxml(xml_content, status_code)
            else:
                return self._parse_with_elementtree(xml_content, status_code)
        except Exception as e:
            logger.error(f"Fehler beim Parsen der SOAP-Response: {e}")
            return SOAPResponse(
                success=False,
                status_code=status_code,
                raw_xml=xml_content,
                fault_message=str(e),
            )

    def _parse_with_lxml(self, xml_content: str, status_code: int) -> SOAPResponse:
        """Parst mit lxml."""
        root = etree.fromstring(xml_content.encode())

        # Namespace-Map
        nsmap = {
            "soap": self.SOAP11_NS,
            "soap12": self.SOAP12_NS,
        }

        # Fault prüfen (explicit None checks to avoid FutureWarning)
        fault = root.find(".//soap:Fault", nsmap)
        if fault is None:
            fault = root.find(".//soap12:Fault", nsmap)
        if fault is None:
            fault = root.find(".//{*}Fault")

        if fault is not None:
            return self._extract_fault(fault, xml_content, status_code)

        # Body extrahieren (explicit None checks to avoid FutureWarning)
        body = root.find(".//soap:Body", nsmap)
        if body is None:
            body = root.find(".//soap12:Body", nsmap)
        if body is None:
            body = root.find(".//{*}Body")

        if body is None:
            return SOAPResponse(
                success=False,
                status_code=status_code,
                raw_xml=xml_content,
                fault_message="Kein SOAP Body gefunden",
            )

        # Body-Inhalt zu Dictionary konvertieren
        data = {}
        for child in body:
            data.update(self._element_to_dict(child))

        return SOAPResponse(
            success=True,
            status_code=status_code,
            data=data,
            raw_xml=xml_content,
        )

    def _parse_with_elementtree(self, xml_content: str, status_code: int) -> SOAPResponse:
        """Parst mit ElementTree (Fallback)."""
        root = ET.fromstring(xml_content)

        # Fault prüfen (mit Namespace-Wildcard)
        fault = None
        for elem in root.iter():
            if "Fault" in elem.tag:
                fault = elem
                break

        if fault is not None:
            return self._extract_fault_et(fault, xml_content, status_code)

        # Body finden
        body = None
        for elem in root.iter():
            if "Body" in elem.tag:
                body = elem
                break

        if body is None:
            return SOAPResponse(
                success=False,
                status_code=status_code,
                raw_xml=xml_content,
                fault_message="Kein SOAP Body gefunden",
            )

        # Body-Inhalt zu Dictionary
        data = {}
        for child in body:
            data.update(self._element_to_dict_et(child))

        return SOAPResponse(
            success=True,
            status_code=status_code,
            data=data,
            raw_xml=xml_content,
        )

    def _extract_fault(self, fault, xml_content: str, status_code: int) -> SOAPResponse:
        """Extrahiert SOAP-Fault Informationen (lxml)."""
        fault_code = None
        fault_message = None
        fault_detail = None

        # Iteriere durch direkte Kinder des Fault-Elements
        # Dies funktioniert sowohl mit als auch ohne Namespace-Prefix
        for child in fault:
            # Extrahiere lokalen Tag-Namen (ohne Namespace)
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            tag_lower = tag.lower()

            # SOAP 1.1: faultcode, faultstring, detail
            # SOAP 1.2: Code/Value, Reason/Text, Detail
            if tag_lower == "faultcode":
                fault_code = child.text
            elif tag_lower == "code":
                # SOAP 1.2: Code hat Value als Kind
                value_elem = child.find(".//{*}Value")
                if value_elem is None:
                    # Fallback: direkt im Code-Element suchen
                    for subchild in child:
                        subtag = subchild.tag.split("}")[-1] if "}" in subchild.tag else subchild.tag
                        if subtag.lower() == "value":
                            value_elem = subchild
                            break
                fault_code = value_elem.text if value_elem is not None else child.text
            elif tag_lower == "faultstring":
                fault_message = child.text
            elif tag_lower == "reason":
                # SOAP 1.2: Reason hat Text als Kind
                text_elem = child.find(".//{*}Text")
                if text_elem is None:
                    for subchild in child:
                        subtag = subchild.tag.split("}")[-1] if "}" in subchild.tag else subchild.tag
                        if subtag.lower() == "text":
                            text_elem = subchild
                            break
                fault_message = text_elem.text if text_elem is not None else child.text
            elif tag_lower in ("detail", "faultdetail"):
                fault_detail = etree.tostring(child, encoding="unicode", pretty_print=True)

        return SOAPResponse(
            success=False,
            status_code=status_code,
            raw_xml=xml_content,
            fault_code=fault_code,
            fault_message=fault_message,
            fault_detail=fault_detail,
        )

    def _extract_fault_et(self, fault, xml_content: str, status_code: int) -> SOAPResponse:
        """Extrahiert SOAP-Fault mit ElementTree."""
        fault_code = None
        fault_message = None
        fault_detail = None

        for child in fault:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag in ("faultcode", "Code"):
                fault_code = child.text or (child[0].text if len(child) > 0 else None)
            elif tag in ("faultstring", "Reason"):
                fault_message = child.text or (child[0].text if len(child) > 0 else None)
            elif tag in ("detail", "Detail"):
                fault_detail = ET.tostring(child, encoding="unicode")

        return SOAPResponse(
            success=False,
            status_code=status_code,
            raw_xml=xml_content,
            fault_code=fault_code,
            fault_message=fault_message,
            fault_detail=fault_detail,
        )

    def _element_to_dict(self, element) -> Dict[str, Any]:
        """Konvertiert ein lxml-Element zu einem Dictionary."""
        # Tag ohne Namespace
        tag = element.tag.split("}")[-1] if "}" in element.tag else element.tag

        result: Dict[str, Any] = {}

        # Attribute
        if element.attrib:
            result["@attributes"] = dict(element.attrib)

        # Kinder
        children = list(element)
        if children:
            child_dict: Dict[str, Any] = {}
            for child in children:
                child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                child_data = self._element_to_dict(child)

                if child_tag in child_dict:
                    # Liste erstellen wenn Tag mehrfach vorkommt
                    if not isinstance(child_dict[child_tag], list):
                        child_dict[child_tag] = [child_dict[child_tag]]
                    child_dict[child_tag].append(child_data.get(child_tag))
                else:
                    child_dict[child_tag] = child_data.get(child_tag)

            result[tag] = child_dict
        elif element.text and element.text.strip():
            result[tag] = element.text.strip()
        else:
            result[tag] = None

        return result

    def _element_to_dict_et(self, element) -> Dict[str, Any]:
        """Konvertiert ein ElementTree-Element zu einem Dictionary."""
        tag = element.tag.split("}")[-1] if "}" in element.tag else element.tag

        result: Dict[str, Any] = {}

        children = list(element)
        if children:
            child_dict: Dict[str, Any] = {}
            for child in children:
                child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                child_data = self._element_to_dict_et(child)

                if child_tag in child_dict:
                    if not isinstance(child_dict[child_tag], list):
                        child_dict[child_tag] = [child_dict[child_tag]]
                    child_dict[child_tag].append(child_data.get(child_tag))
                else:
                    child_dict[child_tag] = child_data.get(child_tag)

            result[tag] = child_dict
        elif element.text and element.text.strip():
            result[tag] = element.text.strip()
        else:
            result[tag] = None

        return result


# ═══════════════════════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════════════════════

@lru_cache(maxsize=10)
def get_cached_wsdl(wsdl_url: str, timeout: int = 30, verify_ssl: bool = True) -> WSDLService:
    """
    Gibt einen gecachten WSDLService zurück.

    Cache-Key: wsdl_url
    Max. 10 WSDLs im Cache
    """
    parser = WSDLParser(wsdl_url, timeout, verify_ssl)
    return parser.parse()


def format_wsdl_info(service: WSDLService, operation_name: Optional[str] = None, show_types: bool = False) -> str:
    """
    Formatiert WSDL-Informationen für die Ausgabe.

    Args:
        service: Geparseter WSDLService
        operation_name: Optional, nur diese Operation anzeigen
        show_types: Komplexe Typen anzeigen

    Returns:
        Formatierter String
    """
    output = f"=== WSDL: {service.name} ===\n"
    output += f"Endpoint: {service.endpoint}\n"
    output += f"Target Namespace: {service.target_namespace}\n"
    output += f"SOAP Version: {service.soap_version}\n\n"

    if operation_name:
        # Nur spezifische Operation
        op = None
        for o in service.operations:
            if o.name.lower() == operation_name.lower():
                op = o
                break

        if op:
            output += _format_operation_detail(op, show_types)
        else:
            output += f"Operation '{operation_name}' nicht gefunden.\n"
            output += f"Verfügbare Operationen: {', '.join(o.name for o in service.operations)}"
    else:
        # Alle Operationen
        output += f"Operationen ({len(service.operations)}):\n"
        for i, op in enumerate(service.operations, 1):
            output += f"\n  {i}. {op.name}\n"
            if op.input_params:
                params_str = ", ".join(
                    f"{p.name}: {p.type}{'*' if p.required else ''}"
                    for p in op.input_params
                )
                output += f"     Input: {params_str}\n"
            else:
                output += f"     Input: (keine Parameter)\n"
            output += f"     Output: {op.output_type}\n"
            if op.soap_action:
                output += f"     SOAPAction: {op.soap_action}\n"

    if not ZEEP_AVAILABLE:
        output += "\n[Hinweis: zeep nicht installiert. Für vollständiges WSDL-Parsing: pip install zeep]"

    return output


def _format_operation_detail(op: WSDLOperation, show_types: bool) -> str:
    """Formatiert eine einzelne Operation im Detail."""
    output = f"Operation: {op.name}\n"
    output += "=" * 40 + "\n\n"

    if op.documentation:
        output += f"Beschreibung: {op.documentation}\n\n"

    if op.soap_action:
        output += f"SOAPAction: {op.soap_action}\n\n"

    output += "Input-Parameter:\n"
    if op.input_params:
        for param in op.input_params:
            required = " (required)" if param.required else " (optional)"
            output += f"  - {param.name}: {param.type}{required}\n"
            if show_types and param.is_complex and param.children:
                for child in param.children:
                    child_req = " (required)" if child.required else ""
                    output += f"      └─ {child.name}: {child.type}{child_req}\n"
    else:
        output += "  (keine Parameter)\n"

    output += f"\nOutput: {op.output_type}\n"

    return output


def format_soap_response(response: SOAPResponse, include_raw: bool = False) -> str:
    """
    Formatiert eine SOAP-Response für die Ausgabe.

    Args:
        response: Geparsete SOAPResponse
        include_raw: Raw XML einschließen

    Returns:
        Formatierter String
    """
    if response.success:
        output = f"=== SOAP Response ===\n"
        output += f"Status: {response.status_code} OK\n\n"

        if response.data:
            output += "Response:\n"
            output += _format_dict(response.data, indent=2)
        else:
            output += "(Leere Response)\n"

        if include_raw and response.raw_xml:
            output += f"\n\nRaw XML:\n{response.raw_xml}"
    else:
        output = f"=== SOAP Fault ===\n"
        output += f"Status: {response.status_code}\n\n"

        if response.fault_code:
            output += f"Code: {response.fault_code}\n"
        if response.fault_message:
            output += f"Message: {response.fault_message}\n"
        if response.fault_detail:
            output += f"\nDetail:\n{response.fault_detail}\n"

        if include_raw and response.raw_xml:
            output += f"\n\nRaw XML:\n{response.raw_xml}"

    return output


def _format_dict(data: Dict[str, Any], indent: int = 0) -> str:
    """Formatiert ein Dictionary für die Ausgabe."""
    output = ""
    indent_str = " " * indent

    for key, value in data.items():
        if key == "@attributes":
            continue

        if isinstance(value, dict):
            output += f"{indent_str}{key}:\n"
            output += _format_dict(value, indent + 2)
        elif isinstance(value, list):
            output += f"{indent_str}{key}:\n"
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    output += f"{indent_str}  [{i}]:\n"
                    output += _format_dict(item, indent + 4)
                else:
                    output += f"{indent_str}  - {item}\n"
        else:
            output += f"{indent_str}{key}: {value}\n"

    return output
