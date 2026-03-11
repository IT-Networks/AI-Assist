"""
Tests für app/utils/soap_utils.py und app/agent/api_tools.py

Testet SOAP/WSDL-Parsing und REST-API-Funktionalität.
"""

import pytest

from app.utils.soap_utils import (
    WSDLParser,
    SOAPEnvelopeBuilder,
    SOAPResponseParser,
    WSDLService,
    WSDLOperation,
    WSDLParameter,
    SOAPResponse,
)


class TestWSDLParameter:
    """Tests für WSDLParameter Dataclass."""

    def test_create_simple_parameter(self):
        """Einfachen Parameter erstellen."""
        param = WSDLParameter(
            name="userId",
            type="string",
        )
        assert param.name == "userId"
        assert param.type == "string"
        assert param.required is True
        assert param.is_complex is False

    def test_create_complex_parameter(self):
        """Komplexen Parameter erstellen."""
        child = WSDLParameter(name="street", type="string")
        param = WSDLParameter(
            name="address",
            type="Address",
            is_complex=True,
            children=[child],
        )
        assert param.is_complex is True
        assert len(param.children) == 1


class TestWSDLOperation:
    """Tests für WSDLOperation Dataclass."""

    def test_create_operation(self):
        """WSDLOperation erstellen."""
        param = WSDLParameter(name="userId", type="string")
        operation = WSDLOperation(
            name="GetUser",
            input_params=[param],
            output_type="User",
            soap_action="http://test.example.com/GetUser",
        )
        assert operation.name == "GetUser"
        assert operation.soap_action == "http://test.example.com/GetUser"
        assert len(operation.input_params) == 1


class TestWSDLService:
    """Tests für WSDLService Dataclass."""

    def test_create_service(self):
        """WSDLService erstellen."""
        service = WSDLService(
            name="TestService",
            endpoint="http://test.example.com/soap",
            target_namespace="http://test.example.com/",
            operations=[],
        )
        assert service.name == "TestService"
        assert service.endpoint == "http://test.example.com/soap"
        assert len(service.operations) == 0

    def test_service_with_operations(self):
        """Service mit Operationen erstellen."""
        operation = WSDLOperation(
            name="Ping",
            input_params=[],
            output_type="string",
        )
        service = WSDLService(
            name="PingService",
            endpoint="http://test.com/ping",
            target_namespace="http://test.com/",
            operations=[operation],
        )
        assert len(service.operations) == 1
        assert service.operations[0].name == "Ping"


class TestSOAPResponse:
    """Tests für SOAPResponse Dataclass."""

    def test_success_response(self):
        """Erfolgreiche SOAP-Response."""
        response = SOAPResponse(
            success=True,
            status_code=200,
            data={"result": "ok"},
            raw_xml="<soap:Envelope>...</soap:Envelope>",
        )
        assert response.success is True
        assert response.status_code == 200
        assert response.data["result"] == "ok"

    def test_error_response(self):
        """SOAP-Response mit Fehler."""
        response = SOAPResponse(
            success=False,
            status_code=500,
            fault_code="soap:Server",
            fault_message="Internal server error",
        )
        assert response.success is False
        assert response.fault_code == "soap:Server"


class TestSOAPEnvelopeBuilder:
    """Tests für SOAPEnvelopeBuilder."""

    def _create_test_service(self) -> WSDLService:
        """Erstellt einen Test-Service."""
        return WSDLService(
            name="TestService",
            endpoint="http://test.example.com/soap",
            target_namespace="http://test.example.com/",
            operations=[],
            soap_version="1.1",
        )

    def _create_test_operation(self, name: str = "GetUser") -> WSDLOperation:
        """Erstellt eine Test-Operation."""
        return WSDLOperation(
            name=name,
            input_params=[WSDLParameter(name="userId", type="string")],
            output_type="User",
            soap_action=f"http://test.example.com/{name}",
        )

    def test_build_simple_envelope(self):
        """Einfachen SOAP-Envelope erstellen."""
        service = self._create_test_service()
        builder = SOAPEnvelopeBuilder(service)
        operation = self._create_test_operation()

        envelope = builder.build_envelope(
            operation=operation,
            params={"userId": "123"},
        )

        assert "soap:Envelope" in envelope
        assert "GetUser" in envelope
        assert "userId" in envelope
        assert "123" in envelope

    def test_build_envelope_with_empty_params(self):
        """SOAP-Envelope ohne Parameter."""
        service = self._create_test_service()
        builder = SOAPEnvelopeBuilder(service)
        operation = self._create_test_operation("Ping")

        envelope = builder.build_envelope(
            operation=operation,
            params={},
        )

        assert "Ping" in envelope

    def test_build_envelope_with_complex_params(self):
        """SOAP-Envelope mit verschachtelten Parametern."""
        service = self._create_test_service()
        builder = SOAPEnvelopeBuilder(service)
        operation = self._create_test_operation("CreateOrder")

        envelope = builder.build_envelope(
            operation=operation,
            params={
                "order": {
                    "customerId": "C001",
                    "total": 100.50,
                }
            },
        )

        assert "CreateOrder" in envelope
        assert "customerId" in envelope
        assert "C001" in envelope

    def test_get_soap_headers(self):
        """SOAP HTTP-Header generieren."""
        service = self._create_test_service()
        builder = SOAPEnvelopeBuilder(service)
        operation = self._create_test_operation()

        headers = builder.get_soap_headers(operation)

        assert "Content-Type" in headers
        assert "text/xml" in headers["Content-Type"]


class TestSOAPResponseParser:
    """Tests für SOAPResponseParser."""

    def test_parse_success_response(self):
        """Erfolgreiche Response parsen."""
        parser = SOAPResponseParser()
        xml_response = """<?xml version="1.0"?>
        <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
            <soap:Body>
                <GetUserResponse xmlns="http://test.example.com/">
                    <user>
                        <id>123</id>
                        <name>Test User</name>
                    </user>
                </GetUserResponse>
            </soap:Body>
        </soap:Envelope>
        """

        result = parser.parse(xml_response)
        assert result.success is True
        assert result.data is not None

    def test_parse_fault_response(self):
        """SOAP-Fault parsen."""
        parser = SOAPResponseParser()
        xml_response = """<?xml version="1.0"?>
        <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
            <soap:Body>
                <soap:Fault>
                    <faultcode>soap:Server</faultcode>
                    <faultstring>Internal error</faultstring>
                </soap:Fault>
            </soap:Body>
        </soap:Envelope>
        """

        result = parser.parse(xml_response)
        assert result.success is False
        assert result.fault_code is not None or result.fault_message is not None

    def test_parse_invalid_xml(self):
        """Ungültiges XML parsen."""
        parser = SOAPResponseParser()
        result = parser.parse("not valid xml")
        assert result.success is False


class TestWSDLParser:
    """Tests für WSDLParser - nur statische/Mock-Tests."""

    def test_wsdl_operation_creation(self):
        """WSDLOperation korrekt erstellt."""
        op = WSDLOperation(
            name="TestOp",
            input_params=[],
            output_type="void",
            soap_action="",
        )
        assert op.name == "TestOp"

    def test_wsdl_service_creation(self):
        """WSDLService korrekt erstellt."""
        service = WSDLService(
            name="Test",
            endpoint="http://test.com",
            target_namespace="http://test.com/",
            operations=[],
        )
        assert service.name == "Test"


class TestNamespaceHandling:
    """Tests für Namespace-Handling."""

    def test_parse_response_with_namespaces(self):
        """Response mit mehreren Namespaces parsen."""
        parser = SOAPResponseParser()
        xml_response = """<?xml version="1.0"?>
        <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                          xmlns:ns1="http://namespace1.com/"
                          xmlns:ns2="http://namespace2.com/">
            <soapenv:Body>
                <ns1:Response>
                    <ns2:data>value</ns2:data>
                </ns1:Response>
            </soapenv:Body>
        </soapenv:Envelope>
        """

        result = parser.parse(xml_response)
        assert result.success is True


class TestEdgeCases:
    """Tests für Grenzfälle."""

    def _create_test_service(self) -> WSDLService:
        """Erstellt einen Test-Service."""
        return WSDLService(
            name="TestService",
            endpoint="http://test.example.com/soap",
            target_namespace="http://test.example.com/",
            operations=[],
            soap_version="1.1",
        )

    def _create_test_operation(self, name: str = "Test") -> WSDLOperation:
        """Erstellt eine Test-Operation."""
        return WSDLOperation(
            name=name,
            input_params=[],
            output_type="void",
        )

    def test_envelope_special_characters(self):
        """Envelope mit Sonderzeichen in Werten."""
        service = self._create_test_service()
        builder = SOAPEnvelopeBuilder(service)
        operation = self._create_test_operation()

        envelope = builder.build_envelope(
            operation=operation,
            params={"text": "Hello <World> & 'Test'"},
        )

        # Sonderzeichen sollten escaped sein
        assert "&lt;" in envelope and "&gt;" in envelope

    def test_envelope_unicode(self):
        """Envelope mit Unicode-Zeichen."""
        service = self._create_test_service()
        builder = SOAPEnvelopeBuilder(service)
        operation = self._create_test_operation()

        envelope = builder.build_envelope(
            operation=operation,
            params={"text": "Ümläut Tëst 日本語"},
        )

        assert "Ümläut" in envelope or "日本語" in envelope

    def test_empty_response_body(self):
        """Leerer Response-Body."""
        parser = SOAPResponseParser()
        xml_response = """<?xml version="1.0"?>
        <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
            <soap:Body>
            </soap:Body>
        </soap:Envelope>
        """

        result = parser.parse(xml_response)
        assert result.success is True
