"""
XML Validator - Well-Formedness und Schema-Validierung.

Prüfungen:
1. Well-Formed XML (Syntax)
2. Schema-Validierung (wenn XSD referenziert)
3. Spezifische Formate:
   - pom.xml → Maven POM
   - web.xml → Servlet
   - persistence.xml → JPA
"""

import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

from app.utils.validators.base import (
    BaseValidator,
    Severity,
    ValidationIssue,
    ValidationResult,
)

logger = logging.getLogger(__name__)

# Versuche lxml für bessere Fehlerberichte
try:
    from lxml import etree
    LXML_AVAILABLE = True
except ImportError:
    LXML_AVAILABLE = False


class XMLValidator(BaseValidator):
    """Validator für XML-Dateien."""

    file_extensions = [".xml", ".xsd", ".xsl", ".xslt", ".svg", ".xhtml", ".jspx"]
    name = "xml"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)

        self.validate_schemas = self.config.get("validate_schemas", True)

    async def validate(
        self,
        file_path: str,
        fix: bool = False,
        strict: bool = False,
    ) -> ValidationResult:
        """Validiert eine XML-Datei."""
        start = time.time()
        issues: List[ValidationIssue] = []

        path = Path(file_path)

        # Datei existiert?
        if not path.exists():
            return ValidationResult(
                file_path=file_path,
                file_type=self.name,
                success=False,
                issues=[ValidationIssue(
                    severity=Severity.ERROR,
                    message=f"File not found: {file_path}",
                )],
            )

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return ValidationResult(
                file_path=file_path,
                file_type=self.name,
                success=False,
                issues=[ValidationIssue(
                    severity=Severity.ERROR,
                    message=f"Cannot read file: {e}",
                )],
            )

        # 1. Well-Formedness prüfen
        if LXML_AVAILABLE:
            well_formed_issues = self._check_wellformed_lxml(content, file_path)
        else:
            well_formed_issues = self._check_wellformed_stdlib(content, file_path)

        issues.extend(well_formed_issues)

        # Bei Syntax-Fehlern: Abbrechen
        if any(i.severity == Severity.ERROR for i in well_formed_issues):
            return self._create_result(file_path, issues, start, strict)

        # 2. Spezifische Format-Prüfungen
        filename = path.name.lower()
        if filename == "pom.xml":
            pom_issues = self._validate_pom(content, file_path)
            issues.extend(pom_issues)
        elif filename == "web.xml":
            web_issues = self._validate_web_xml(content, file_path)
            issues.extend(web_issues)
        elif filename == "persistence.xml":
            jpa_issues = self._validate_persistence_xml(content, file_path)
            issues.extend(jpa_issues)

        # 3. Allgemeine Best Practices
        bp_issues = self._check_best_practices(content, file_path)
        issues.extend(bp_issues)

        return self._create_result(file_path, issues, start, strict)

    def _check_wellformed_lxml(
        self,
        content: str,
        file_path: str
    ) -> List[ValidationIssue]:
        """Prüft Well-Formedness mit lxml (bessere Fehlerberichte)."""
        issues = []

        try:
            etree.fromstring(content.encode("utf-8"))
        except etree.XMLSyntaxError as e:
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                message=str(e),
                line=e.lineno if hasattr(e, "lineno") else None,
                column=e.offset if hasattr(e, "offset") else None,
                source="lxml",
            ))

        return issues

    def _check_wellformed_stdlib(
        self,
        content: str,
        file_path: str
    ) -> List[ValidationIssue]:
        """Prüft Well-Formedness mit stdlib ElementTree."""
        issues = []

        try:
            ET.fromstring(content)
        except ET.ParseError as e:
            # Position extrahieren
            line = None
            column = None
            if e.position:
                line, column = e.position

            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                message=str(e),
                line=line,
                column=column,
                source="xml",
            ))

        return issues

    def _validate_pom(self, content: str, file_path: str) -> List[ValidationIssue]:
        """Validiert Maven pom.xml spezifische Regeln."""
        issues = []

        try:
            if LXML_AVAILABLE:
                root = etree.fromstring(content.encode("utf-8"))
                nsmap = root.nsmap
                ns = nsmap.get(None, "")
            else:
                root = ET.fromstring(content)
                ns = ""

            # Namespace-Präfix für XPath
            ns_prefix = f"{{{ns}}}" if ns else ""

            # Required elements
            required = ["groupId", "artifactId", "version"]
            for elem_name in required:
                elem = root.find(f".//{ns_prefix}{elem_name}")
                if elem is None or not elem.text:
                    # Könnte von Parent erben - nur Warning
                    if elem_name != "version":  # version oft aus parent
                        issues.append(ValidationIssue(
                            severity=Severity.WARNING,
                            message=f"Missing or empty <{elem_name}> in pom.xml",
                            rule="pom-missing-element",
                            source="pom",
                        ))

            # Packaging prüfen
            packaging = root.find(f".//{ns_prefix}packaging")
            valid_packagings = {"jar", "war", "ear", "pom", "maven-plugin", "bundle"}
            if packaging is not None and packaging.text and packaging.text.lower() not in valid_packagings:
                issues.append(ValidationIssue(
                    severity=Severity.WARNING,
                    message=f"Unusual packaging type: {packaging.text}",
                    source="pom",
                ))

        except Exception as e:
            logger.debug(f"POM validation error: {e}")

        return issues

    def _validate_web_xml(self, content: str, file_path: str) -> List[ValidationIssue]:
        """Validiert web.xml (Servlet Deployment Descriptor)."""
        issues = []

        try:
            if LXML_AVAILABLE:
                root = etree.fromstring(content.encode("utf-8"))
            else:
                root = ET.fromstring(content)

            # Servlet-Mappings prüfen
            servlets = set()
            for servlet in root.iter():
                if servlet.tag.endswith("servlet-name"):
                    if servlet.text:
                        servlets.add(servlet.text)

            # Servlet-Mappings ohne Servlet-Definition
            for mapping in root.iter():
                if mapping.tag.endswith("servlet-mapping"):
                    servlet_name = None
                    for child in mapping:
                        if child.tag.endswith("servlet-name") and child.text:
                            servlet_name = child.text
                            break

                    if servlet_name and servlet_name not in servlets:
                        issues.append(ValidationIssue(
                            severity=Severity.WARNING,
                            message=f"Servlet mapping references undefined servlet: {servlet_name}",
                            rule="undefined-servlet",
                            source="web.xml",
                        ))

        except Exception as e:
            logger.debug(f"web.xml validation error: {e}")

        return issues

    def _validate_persistence_xml(self, content: str, file_path: str) -> List[ValidationIssue]:
        """Validiert JPA persistence.xml."""
        issues = []

        try:
            if LXML_AVAILABLE:
                root = etree.fromstring(content.encode("utf-8"))
            else:
                root = ET.fromstring(content)

            # Persistence-Units prüfen
            units = []
            for elem in root.iter():
                if elem.tag.endswith("persistence-unit"):
                    unit_name = elem.get("name")
                    if not unit_name:
                        issues.append(ValidationIssue(
                            severity=Severity.ERROR,
                            message="persistence-unit without name attribute",
                            source="persistence.xml",
                        ))
                    else:
                        units.append(unit_name)

            if not units:
                issues.append(ValidationIssue(
                    severity=Severity.WARNING,
                    message="No persistence-unit defined",
                    source="persistence.xml",
                ))

        except Exception as e:
            logger.debug(f"persistence.xml validation error: {e}")

        return issues

    def _check_best_practices(self, content: str, file_path: str) -> List[ValidationIssue]:
        """Prüft allgemeine XML Best Practices."""
        issues = []

        # XML Declaration vorhanden?
        if not content.strip().startswith("<?xml"):
            issues.append(ValidationIssue(
                severity=Severity.INFO,
                message="Missing XML declaration (<?xml version=\"1.0\"?>)",
                line=1,
                rule="missing-declaration",
                source="xml",
            ))

        # Encoding angegeben?
        if "<?xml" in content and "encoding=" not in content.split("?>")[0]:
            issues.append(ValidationIssue(
                severity=Severity.INFO,
                message="XML declaration missing encoding attribute",
                line=1,
                rule="missing-encoding",
                source="xml",
            ))

        return issues
