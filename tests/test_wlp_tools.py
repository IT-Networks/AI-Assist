"""
Tests für app/agent/wlp_tools.py

Testet WLP Server Management Tools:
- Helper Functions (XML-Parsing, Error-Extraction, Feature-Validation)
- Error Code Database
- Feature Compatibility Matrix
"""

import pytest
from pathlib import Path
from xml.etree import ElementTree as ET

from app.agent.wlp_tools import (
    _parse_server_xml,
    _check_feature_compatibility,
    _extract_errors_from_log,
    WLP_ERROR_CODES,
    INCOMPATIBLE_FEATURES,
)


# ══════════════════════════════════════════════════════════════════════════════
# Error Code Database Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestWLPErrorCodes:
    """Tests für die WLP Error Code Datenbank."""

    def test_error_codes_not_empty(self):
        """Error Code Datenbank ist nicht leer."""
        assert len(WLP_ERROR_CODES) > 0

    def test_known_error_code_exists(self):
        """Bekannte Error Codes sind vorhanden."""
        known_codes = ["CWWKZ0013E", "CWWKE0701E", "CWWKF0011E", "J2CA0045E"]
        for code in known_codes:
            assert code in WLP_ERROR_CODES, f"Expected {code} in WLP_ERROR_CODES"

    def test_error_code_structure(self):
        """Error Codes haben die erwartete Struktur."""
        for code, info in WLP_ERROR_CODES.items():
            assert "severity" in info, f"{code} missing 'severity'"
            assert "meaning" in info, f"{code} missing 'meaning'"
            assert info["severity"] in ["ERROR", "WARNING", "INFO", "AUDIT"], \
                f"{code} has invalid severity: {info['severity']}"

    def test_error_codes_have_fixes_for_errors(self):
        """ERROR-Codes sollten Fix-Vorschläge haben."""
        error_codes = [
            code for code, info in WLP_ERROR_CODES.items()
            if info["severity"] == "ERROR"
        ]
        codes_with_fix = [
            code for code in error_codes
            if WLP_ERROR_CODES[code].get("fix")
        ]
        # Mindestens 50% der Error-Codes sollten Fixes haben
        assert len(codes_with_fix) >= len(error_codes) * 0.5, \
            f"Only {len(codes_with_fix)}/{len(error_codes)} ERROR codes have fixes"

    def test_cwwkz0013e_details(self):
        """CWWKZ0013E hat korrekte Details."""
        code = "CWWKZ0013E"
        assert code in WLP_ERROR_CODES
        info = WLP_ERROR_CODES[code]
        assert info["severity"] == "ERROR"
        assert "ClassNotFoundException" in info["meaning"] or "class" in info["meaning"].lower()
        assert info["fix"] is not None


# ══════════════════════════════════════════════════════════════════════════════
# Feature Compatibility Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestFeatureCompatibility:
    """Tests für Feature-Kompatibilitätsprüfung."""

    def test_incompatible_features_defined(self):
        """Inkompatible Features sind definiert."""
        assert len(INCOMPATIBLE_FEATURES) > 0

    def test_no_conflicts_with_single_feature(self):
        """Keine Konflikte bei einzelnem Feature."""
        conflicts = _check_feature_compatibility(["servlet-4.0"])
        assert len(conflicts) == 0

    def test_no_conflicts_with_compatible_features(self):
        """Keine Konflikte bei kompatiblen Features."""
        compatible = ["servlet-4.0", "jpa-2.2", "cdi-2.0", "jaxrs-2.1"]
        conflicts = _check_feature_compatibility(compatible)
        assert len(conflicts) == 0

    def test_conflict_javax_vs_jakarta_servlet(self):
        """Erkennt Konflikt zwischen javax und jakarta Servlet."""
        features = ["servlet-4.0", "servlet-6.0"]
        conflicts = _check_feature_compatibility(features)
        assert len(conflicts) >= 1
        assert any("servlet" in str(c).lower() for c in conflicts)

    def test_conflict_jpa_versions(self):
        """Erkennt Konflikt zwischen JPA Versionen."""
        features = ["jpa-2.2", "persistence-3.0"]
        conflicts = _check_feature_compatibility(features)
        assert len(conflicts) >= 1

    def test_conflict_cdi_versions(self):
        """Erkennt Konflikt zwischen CDI Versionen."""
        features = ["cdi-2.0", "cdi-4.0"]
        conflicts = _check_feature_compatibility(features)
        assert len(conflicts) >= 1

    def test_conflict_details_structure(self):
        """Konflikt-Details haben erwartete Struktur."""
        features = ["servlet-4.0", "servlet-6.0"]
        conflicts = _check_feature_compatibility(features)
        assert len(conflicts) > 0
        conflict = conflicts[0]
        assert "type" in conflict
        assert "features_a" in conflict
        assert "features_b" in conflict
        assert "message" in conflict


# ══════════════════════════════════════════════════════════════════════════════
# Log Error Extraction Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestLogErrorExtraction:
    """Tests für Log-Fehler-Extraktion."""

    def test_empty_log(self):
        """Leeres Log ergibt keine Fehler."""
        errors = _extract_errors_from_log([])
        assert len(errors) == 0

    def test_info_lines_not_extracted_as_errors(self):
        """INFO-Zeilen werden nicht als Fehler extrahiert."""
        lines = [
            "[3/12/26 12:00:00:000] INFO CWWKF0011I: Server ready",
        ]
        errors = _extract_errors_from_log(lines)
        # INFO should be extracted but with INFO severity
        info_errors = [e for e in errors if e.get("severity") == "ERROR"]
        assert len(info_errors) == 0

    def test_extract_error_code(self):
        """Extrahiert WLP Error Code korrekt."""
        # Format muss [ERROR] oder [WARNING] enthalten (mit Klammern)
        lines = [
            "[ERROR] CWWKZ0013E: Application failed to start",
        ]
        errors = _extract_errors_from_log(lines)
        assert len(errors) >= 1
        assert any(e.get("code") == "CWWKZ0013E" for e in errors)

    def test_extract_java_exception(self):
        """Extrahiert Java Exceptions."""
        lines = [
            "java.lang.NullPointerException: Cannot invoke method on null",
        ]
        errors = _extract_errors_from_log(lines)
        assert len(errors) >= 1
        error = errors[0]
        assert "Exception" in error.get("exception_type", "") or "Exception" in error.get("raw_line", "")

    def test_error_has_line_number(self):
        """Extrahierte Fehler haben Zeilennummern."""
        lines = [
            "some log line",
            "[ERROR] CWWKZ0002E: App start failed",
            "another line",
        ]
        errors = _extract_errors_from_log(lines)
        assert len(errors) >= 1
        assert errors[0].get("line_number") == 2

    def test_multiple_errors_extracted(self):
        """Mehrere Fehler werden extrahiert."""
        lines = [
            "[ERROR] CWWKZ0002E: First error",
            "[INFO] Some info",
            "[ERROR] CWWKE0701E: Second error",
            "[WARNING] CWWKF0011E: A warning",
        ]
        errors = _extract_errors_from_log(lines)
        error_codes = [e.get("code") for e in errors]
        assert "CWWKZ0002E" in error_codes
        assert "CWWKE0701E" in error_codes

    def test_error_includes_meaning_if_known(self):
        """Bekannte Fehler haben Meaning aus Datenbank."""
        lines = [
            "[ERROR] CWWKZ0013E: App failed with ClassNotFoundException",
        ]
        errors = _extract_errors_from_log(lines)
        assert len(errors) >= 1
        error = next((e for e in errors if e.get("code") == "CWWKZ0013E"), None)
        assert error is not None
        assert error.get("meaning") is not None
        assert error.get("meaning") != "Unbekannter Fehlercode"


# ══════════════════════════════════════════════════════════════════════════════
# Server XML Parsing Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestServerXmlParsing:
    """Tests für server.xml Parsing."""

    @pytest.fixture
    def sample_server_xml(self, tmp_path):
        """Erstellt eine Test server.xml."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<server description="Test Server">
    <featureManager>
        <feature>servlet-4.0</feature>
        <feature>jpa-2.2</feature>
        <feature>cdi-2.0</feature>
    </featureManager>

    <httpEndpoint id="defaultHttpEndpoint"
                  host="*"
                  httpPort="9080"
                  httpsPort="9443" />

    <webApplication id="myapp"
                    name="MyApp"
                    location="myapp.war"
                    context-root="/app" />

    <dataSource id="myDS" jndiName="jdbc/myDS">
        <properties serverName="localhost" databaseName="testdb" />
        <connectionManager maxPoolSize="10" />
    </dataSource>

    <variable name="app.version" value="1.0.0" />
</server>
"""
        xml_path = tmp_path / "server.xml"
        xml_path.write_text(xml_content, encoding="utf-8")
        return xml_path

    def test_parse_nonexistent_file(self, tmp_path):
        """Nicht existierende Datei ergibt Fehler."""
        result = _parse_server_xml(tmp_path / "nonexistent.xml")
        assert "error" in result

    def test_parse_invalid_xml(self, tmp_path):
        """Ungültiges XML ergibt Fehler."""
        invalid_xml = tmp_path / "invalid.xml"
        invalid_xml.write_text("<server><unclosed>", encoding="utf-8")
        result = _parse_server_xml(invalid_xml)
        assert "error" in result

    def test_parse_extracts_features(self, sample_server_xml):
        """Extrahiert Features korrekt."""
        result = _parse_server_xml(sample_server_xml)
        assert "error" not in result
        assert "features" in result
        assert "servlet-4.0" in result["features"]
        assert "jpa-2.2" in result["features"]
        assert "cdi-2.0" in result["features"]
        assert len(result["features"]) == 3

    def test_parse_extracts_http_endpoint(self, sample_server_xml):
        """Extrahiert HTTP Endpoint korrekt."""
        result = _parse_server_xml(sample_server_xml)
        assert result.get("http_endpoint") is not None
        endpoint = result["http_endpoint"]
        assert endpoint["httpPort"] == "9080"
        assert endpoint["httpsPort"] == "9443"

    def test_parse_extracts_applications(self, sample_server_xml):
        """Extrahiert Applications korrekt."""
        result = _parse_server_xml(sample_server_xml)
        assert len(result.get("applications", [])) == 1
        app = result["applications"][0]
        assert app["name"] == "MyApp"
        assert app["location"] == "myapp.war"
        assert app["context_root"] == "/app"

    def test_parse_extracts_datasources(self, sample_server_xml):
        """Extrahiert DataSources korrekt."""
        result = _parse_server_xml(sample_server_xml)
        assert len(result.get("datasources", [])) == 1
        ds = result["datasources"][0]
        assert ds["id"] == "myDS"
        assert ds["jndiName"] == "jdbc/myDS"

    def test_parse_extracts_variables(self, sample_server_xml):
        """Extrahiert Variables korrekt."""
        result = _parse_server_xml(sample_server_xml)
        assert "variables" in result
        assert result["variables"].get("app.version") == "1.0.0"

    def test_parse_empty_server_xml(self, tmp_path):
        """Leere server.xml wird korrekt geparst."""
        empty_xml = tmp_path / "empty.xml"
        empty_xml.write_text("<server></server>", encoding="utf-8")
        result = _parse_server_xml(empty_xml)
        assert "error" not in result
        assert result["features"] == []
        assert result["applications"] == []


# ══════════════════════════════════════════════════════════════════════════════
# Integration Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestWLPToolsIntegration:
    """Integrationstests für WLP Tools."""

    def test_parse_and_validate_features(self, tmp_path):
        """Parse server.xml und validiere Features."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<server>
    <featureManager>
        <feature>servlet-4.0</feature>
        <feature>servlet-6.0</feature>
    </featureManager>
</server>
"""
        xml_path = tmp_path / "server.xml"
        xml_path.write_text(xml_content, encoding="utf-8")

        # Parse
        parsed = _parse_server_xml(xml_path)
        assert "error" not in parsed

        # Validate
        conflicts = _check_feature_compatibility(parsed["features"])
        assert len(conflicts) >= 1, "Should detect servlet version conflict"

    def test_full_error_analysis_workflow(self):
        """Vollständiger Error-Analyse-Workflow."""
        # Log-Format muss [ERROR] oder [WARNING] in Klammern haben
        log_lines = [
            "[INFO] Server starting",
            "[ERROR] CWWKZ0013E: Application MyApp failed to start",
            "java.lang.ClassNotFoundException: com.example.MissingClass",
            "[ERROR] CWWKE0701E: Bundle resolution failed",
            "[INFO] Server stopping",
        ]

        errors = _extract_errors_from_log(log_lines)

        # Should find multiple errors (2 WLP codes + 1 Java exception)
        assert len(errors) >= 2

        # Check first WLP error
        cwwkz_error = next((e for e in errors if e.get("code") == "CWWKZ0013E"), None)
        assert cwwkz_error is not None
        assert cwwkz_error["severity"] == "ERROR"
        assert cwwkz_error.get("fix") is not None  # Known code should have fix

        # Check second WLP error
        cwwke_error = next((e for e in errors if e.get("code") == "CWWKE0701E"), None)
        assert cwwke_error is not None


class TestEdgeCases:
    """Tests für Grenzfälle."""

    def test_feature_with_special_characters(self):
        """Feature-Namen mit Sonderzeichen."""
        features = ["mpHealth-3.1", "microProfile-5.0"]
        conflicts = _check_feature_compatibility(features)
        # Should not crash
        assert isinstance(conflicts, list)

    def test_empty_feature_list(self):
        """Leere Feature-Liste."""
        conflicts = _check_feature_compatibility([])
        assert len(conflicts) == 0

    def test_log_with_unicode(self):
        """Log-Zeilen mit Unicode."""
        lines = [
            "[ERROR] CWWKZ0013E: Applikation mit Ümläuten: Tëst",
        ]
        errors = _extract_errors_from_log(lines)
        assert len(errors) >= 1

    def test_malformed_error_code(self):
        """Unvollständiger Error Code."""
        lines = [
            "[ERROR] CWW: incomplete code",
            "[ERROR] CWWKZ: also incomplete",
        ]
        errors = _extract_errors_from_log(lines)
        # Should not extract malformed codes
        cwwk_errors = [e for e in errors if e.get("code", "").startswith("CWWK")]
        assert len(cwwk_errors) == 0
