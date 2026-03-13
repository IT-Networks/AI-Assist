"""
Test Template Engine - Verarbeitet XML-Templates mit Platzhaltern.

Platzhalter-Format:
- {{name}}           - Required, kein Default
- {{name:default}}   - Optional mit Default
- {{name:}}          - Optional, leerer Default
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)


class TestTemplateEngine:
    """
    Verarbeitet SOAP-XML-Templates mit Platzhalter-Ersetzung.

    Features:
    - Platzhalter mit optionalen Defaults
    - Auto-Extraktion von Parametern aus Templates
    - XML-Validierung
    - Sichere XML-Escaping
    """

    # Pattern für Platzhalter: {{name}} oder {{name:default}}
    PLACEHOLDER_PATTERN = re.compile(r'\{\{(\w+)(?::([^}]*))?\}\}')

    # Auto-injizierte Parameter (werden nicht als User-Input erwartet)
    AUTO_INJECT_PARAMS = {'session_token', 'user', 'password'}

    def __init__(self, templates_path: str = "data/test_tool/templates"):
        """
        Args:
            templates_path: Basis-Pfad für Template-Dateien
        """
        self.templates_path = Path(templates_path)
        self._cache: Dict[str, str] = {}

    def load_template(self, service_id: str, operation_id: str) -> str:
        """
        Lädt ein Template von Disk.

        Args:
            service_id: Service-ID (Unterverzeichnis)
            operation_id: Operation-ID (Dateiname ohne .soap.xml)

        Returns:
            Template-Inhalt als String

        Raises:
            FileNotFoundError: Template existiert nicht
        """
        cache_key = f"{service_id}/{operation_id}"

        if cache_key in self._cache:
            return self._cache[cache_key]

        # Verschiedene Dateinamen-Varianten probieren
        possible_names = [
            f"{operation_id}.soap.xml",
            f"{operation_id}.xml",
            operation_id,  # Falls bereits mit Extension
        ]

        template_dir = self.templates_path / service_id

        for name in possible_names:
            path = template_dir / name
            if path.exists():
                content = path.read_text(encoding='utf-8')
                self._cache[cache_key] = content
                logger.debug(f"Template geladen: {path}")
                return content

        raise FileNotFoundError(
            f"Template nicht gefunden: {template_dir}/{operation_id}.soap.xml"
        )

    def save_template(
        self,
        service_id: str,
        operation_id: str,
        content: str
    ) -> Path:
        """
        Speichert ein Template auf Disk.

        Args:
            service_id: Service-ID
            operation_id: Operation-ID
            content: Template-XML

        Returns:
            Pfad zur gespeicherten Datei
        """
        template_dir = self.templates_path / service_id
        template_dir.mkdir(parents=True, exist_ok=True)

        path = template_dir / f"{operation_id}.soap.xml"
        path.write_text(content, encoding='utf-8')

        # Cache invalidieren
        cache_key = f"{service_id}/{operation_id}"
        if cache_key in self._cache:
            del self._cache[cache_key]

        logger.info(f"Template gespeichert: {path}")
        return path

    def delete_template(self, service_id: str, operation_id: str) -> bool:
        """
        Löscht ein Template.

        Returns:
            True wenn gelöscht, False wenn nicht gefunden
        """
        path = self.templates_path / service_id / f"{operation_id}.soap.xml"

        if path.exists():
            path.unlink()
            cache_key = f"{service_id}/{operation_id}"
            if cache_key in self._cache:
                del self._cache[cache_key]
            return True

        return False

    def fill_template(
        self,
        template: str,
        params: Dict[str, Any],
        auto_params: Optional[Dict[str, Any]] = None,
        strict: bool = True
    ) -> str:
        """
        Füllt Platzhalter im Template mit Werten.

        Args:
            template: XML-Template mit {{placeholder}} Syntax
            params: User-Parameter
            auto_params: Automatisch injizierte Params (session_token, user)
            strict: Bei True wird Exception geworfen wenn Required-Param fehlt

        Returns:
            Gefülltes XML

        Raises:
            ValueError: Required-Parameter fehlt (wenn strict=True)
        """
        all_params = {**(auto_params or {}), **params}
        missing_params = []

        logger.debug(f"[Template] fill_template aufgerufen mit {len(all_params)} Parametern: {list(all_params.keys())}")

        def replace_placeholder(match: re.Match) -> str:
            name = match.group(1)
            default = match.group(2)

            if name in all_params:
                value = all_params[name]
                # None-Werte als leer behandeln
                if value is None:
                    value = ""
                return self._escape_xml(str(value))
            elif default is not None:
                # Hat Default-Wert (kann auch leer sein)
                return self._escape_xml(default)
            else:
                # Required-Parameter fehlt
                missing_params.append(name)
                return f"{{{{MISSING:{name}}}}}"

        result = self.PLACEHOLDER_PATTERN.sub(replace_placeholder, template)

        if missing_params and strict:
            raise ValueError(
                f"Required parameters missing: {', '.join(missing_params)}"
            )

        return result

    def extract_parameters(
        self,
        template: str,
        include_auto_inject: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Extrahiert Parameter-Definitionen aus einem Template.

        Args:
            template: XML-Template
            include_auto_inject: Auto-injizierte Params einschließen?

        Returns:
            Liste von Parameter-Definitionen
        """
        params = []
        seen = set()

        for match in self.PLACEHOLDER_PATTERN.finditer(template):
            name = match.group(1)
            default = match.group(2)

            if name in seen:
                continue
            seen.add(name)

            is_auto = name in self.AUTO_INJECT_PARAMS
            if is_auto and not include_auto_inject:
                continue

            params.append({
                'name': name,
                'required': default is None,
                'default': default if default is not None else '',
                'auto_inject': is_auto,
                'sensitive': name in {'password', 'session_token', 'api_key'}
            })

        return params

    def validate_template(self, content: str) -> Dict[str, Any]:
        """
        Validiert ein Template auf XML-Syntax und SOAP-Struktur.

        Args:
            content: Template-XML

        Returns:
            Dict mit 'valid', 'errors', 'warnings', 'parameters'
        """
        errors = []
        warnings = []

        # Leerer Content?
        if not content or not content.strip():
            errors.append("Template ist leer")
            return {
                'valid': False,
                'errors': errors,
                'warnings': warnings,
                'parameters': []
            }

        # XML-Syntax prüfen (ohne Platzhalter)
        # Ersetze temporär alle Platzhalter für XML-Validierung
        test_xml = self.PLACEHOLDER_PATTERN.sub('PLACEHOLDER', content)

        try:
            ET.fromstring(test_xml)
        except ET.ParseError as e:
            errors.append(f"XML-Syntax-Fehler: {e}")

        # SOAP-Envelope prüfen
        envelope_patterns = [
            '<soap:Envelope',
            '<SOAP-ENV:Envelope',
            '<soapenv:Envelope',
            '<Envelope',
        ]
        has_envelope = any(p in content for p in envelope_patterns)
        if not has_envelope:
            warnings.append("Kein SOAP-Envelope gefunden (soap:Envelope)")

        # Body prüfen
        body_patterns = ['<soap:Body', '<SOAP-ENV:Body', '<soapenv:Body', '<Body']
        has_body = any(p in content for p in body_patterns)
        if not has_body:
            warnings.append("Kein SOAP-Body gefunden")

        # Parameter extrahieren
        params = self.extract_parameters(content, include_auto_inject=True)

        # Warnungen für fehlende Auto-Inject Parameter
        param_names = {p['name'] for p in params}
        if 'session_token' not in param_names:
            # Nur warnen wenn Auth-Header vorhanden scheint
            if 'AuthHeader' in content or 'SessionToken' in content:
                warnings.append(
                    "AuthHeader gefunden aber kein {{session_token}} Platzhalter"
                )

        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings,
            'parameters': params
        }

    def template_exists(self, service_id: str, operation_id: str) -> bool:
        """Prüft ob ein Template existiert."""
        path = self.templates_path / service_id / f"{operation_id}.soap.xml"
        return path.exists()

    def list_templates(self, service_id: str) -> List[str]:
        """Listet alle Templates für einen Service."""
        template_dir = self.templates_path / service_id
        if not template_dir.exists():
            return []

        templates = []
        for path in template_dir.glob("*.soap.xml"):
            # Operation-ID ist Dateiname ohne Extension
            op_id = path.stem.replace('.soap', '')
            templates.append(op_id)

        return sorted(templates)

    def clear_cache(self):
        """Leert den Template-Cache."""
        self._cache.clear()

    @staticmethod
    def _escape_xml(value: Any) -> str:
        """Escaped einen Wert für sichere XML-Einbettung."""
        s = str(value)
        s = s.replace("&", "&amp;")
        s = s.replace("<", "&lt;")
        s = s.replace(">", "&gt;")
        s = s.replace('"', "&quot;")
        s = s.replace("'", "&apos;")
        return s


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_template_engine: Optional[TestTemplateEngine] = None


def get_template_engine() -> TestTemplateEngine:
    """Gibt Singleton-Instanz des Template-Engines zurück."""
    global _template_engine
    if _template_engine is None:
        from app.core.config import settings
        _template_engine = TestTemplateEngine(settings.test_tool.templates_path)
    return _template_engine
