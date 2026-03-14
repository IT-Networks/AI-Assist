"""
Anonymizer - Maskiert sensible Daten für Analytics.

Maskiert:
- IP-Adressen: 192.168.1.100 → ***.***.***.***
- Pfade: /home/user/secret → /***/***/***
- Credentials: password=abc123 → password=***
- Emails: user@firma.de → ***@***.***
- Bearer/Basic Auth Tokens
- Konfigurierbare Firmen-Patterns (Ticket-IDs etc.)
"""

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class AnonymizerConfig:
    """Konfiguration für den Anonymizer."""
    enabled: bool = True
    mask_ips: bool = True
    mask_paths: bool = True
    mask_credentials: bool = True
    mask_emails: bool = True
    mask_urls_with_auth: bool = True
    mask_company_data: bool = True
    # Zusätzliche Regex-Patterns für firmenspezifische Daten
    company_patterns: List[str] = field(default_factory=list)
    # Pfad-Komponenten die NICHT maskiert werden (z.B. "src", "app")
    path_whitelist: List[str] = field(default_factory=lambda: [
        "src", "app", "lib", "test", "tests", "config", "docs",
        "api", "services", "models", "utils", "core", "agent"
    ])


class Anonymizer:
    """
    Maskiert sensible Daten für Analytics-Logging.

    Alle Maskierungen verwenden *** als Platzhalter,
    um die Struktur erkennbar zu halten ohne Inhalte preiszugeben.
    """

    # Pre-compiled Regex Patterns für Performance
    _PATTERNS = {
        # IP-Adressen (IPv4)
        'ip': re.compile(
            r'\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}'
            r'(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b'
        ),
        # IPv6 (vereinfacht)
        'ipv6': re.compile(
            r'\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b|'
            r'\b(?:[0-9a-fA-F]{1,4}:){1,7}:\b|'
            r'\b::(?:[0-9a-fA-F]{1,4}:){0,6}[0-9a-fA-F]{1,4}\b'
        ),
        # Email-Adressen
        'email': re.compile(
            r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b'
        ),
        # URLs mit eingebetteter Auth (http://user:pass@host)
        'url_auth': re.compile(
            r'(https?://)([^:]+):([^@]+)@([^\s/]+)'
        ),
        # Unix-Pfade (mindestens 3 Segmente)
        'path_unix': re.compile(
            r'(?<![a-zA-Z0-9])(/[a-zA-Z0-9_.-]+){3,}'
        ),
        # Windows-Pfade
        'path_win': re.compile(
            r'[A-Za-z]:\\(?:[^\\/:*?"<>|\r\n]+\\){2,}[^\\/:*?"<>|\r\n]*'
        ),
        # Credential Key-Value Paare
        'credential_kv': re.compile(
            r'(password|passwd|pwd|secret|token|api_key|apikey|auth|'
            r'bearer|credential|private_key|access_key|secret_key)'
            r'\s*[=:]\s*["\']?([^\s"\']+)["\']?',
            re.IGNORECASE
        ),
        # Bearer Token
        'bearer': re.compile(
            r'Bearer\s+[A-Za-z0-9._-]{10,}',
            re.IGNORECASE
        ),
        # Basic Auth
        'basic_auth': re.compile(
            r'Basic\s+[A-Za-z0-9+/=]{10,}',
            re.IGNORECASE
        ),
        # Hostnames mit Ports (intern.firma.de:8080)
        'hostname_port': re.compile(
            r'\b[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+:\d{2,5}\b'
        ),
        # UUIDs (oft in URLs/Logs)
        'uuid': re.compile(
            r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
            r'[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b'
        ),
    }

    def __init__(self, config: Optional[AnonymizerConfig] = None):
        self.config = config or AnonymizerConfig()
        self._company_patterns: List[re.Pattern] = []

        # Firmen-Patterns kompilieren
        if self.config.company_patterns:
            for pattern in self.config.company_patterns:
                try:
                    self._company_patterns.append(re.compile(pattern))
                except re.error:
                    pass  # Ungültiges Pattern ignorieren

    def anonymize(self, text: str) -> str:
        """
        Anonymisiert einen Text durch Maskierung sensibler Daten.

        Args:
            text: Der zu anonymisierende Text

        Returns:
            Anonymisierter Text mit *** als Maskierung
        """
        if not text or not self.config.enabled:
            return text

        result = text

        # 1. URLs mit Auth zuerst (bevor andere Patterns greifen)
        if self.config.mask_urls_with_auth:
            result = self._mask_url_auth(result)

        # 2. Credentials (password=xxx, Bearer xxx)
        if self.config.mask_credentials:
            result = self._mask_credentials(result)

        # 3. IP-Adressen
        if self.config.mask_ips:
            result = self._mask_ips(result)

        # 4. Emails
        if self.config.mask_emails:
            result = self._mask_emails(result)

        # 5. Pfade
        if self.config.mask_paths:
            result = self._mask_paths(result)

        # 6. Firmen-spezifische Patterns
        if self.config.mask_company_data:
            result = self._mask_company_data(result)

        return result

    def anonymize_dict(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Anonymisiert rekursiv ein Dictionary.

        Sensitive Keys werden komplett maskiert,
        andere Werte werden durch anonymize() geschickt.
        """
        if not self.config.enabled:
            return data

        # Keys die komplett maskiert werden
        sensitive_keys = {
            'password', 'passwd', 'pwd', 'secret', 'token', 'api_key',
            'apikey', 'auth', 'authorization', 'bearer', 'credential',
            'private_key', 'access_key', 'secret_key', 'api_token'
        }

        result = {}
        for key, value in data.items():
            key_lower = key.lower()

            # Sensitive Keys komplett maskieren
            if key_lower in sensitive_keys:
                result[key] = "***"
            elif isinstance(value, dict):
                result[key] = self.anonymize_dict(value)
            elif isinstance(value, list):
                result[key] = [
                    self.anonymize_dict(v) if isinstance(v, dict)
                    else self.anonymize(str(v)) if isinstance(v, str)
                    else v
                    for v in value
                ]
            elif isinstance(value, str):
                result[key] = self.anonymize(value)
            else:
                result[key] = value

        return result

    def hash_query(self, query: str) -> Tuple[str, List[str]]:
        """
        Erstellt einen Hash und Kategorie-Tags für eine Query.

        Der Hash ermöglicht Korrelation ohne den Inhalt preiszugeben.
        Die Kategorien ermöglichen Analyse ohne den Text zu lesen.

        Args:
            query: Die User-Query

        Returns:
            Tuple (sha256_hash, ["category1", "category2", ...])
        """
        # Hash erstellen
        query_hash = hashlib.sha256(query.encode('utf-8')).hexdigest()[:12]

        # Kategorien erkennen
        categories = self._categorize_query(query)

        return query_hash, categories

    def _categorize_query(self, query: str) -> List[str]:
        """Kategorisiert eine Query basierend auf Schlüsselwörtern."""
        query_lower = query.lower()
        categories = []

        # Kategorie-Patterns
        category_patterns = {
            'code_search': ['klasse', 'methode', 'function', 'class', 'method',
                           'implementierung', 'code', 'suche', 'find'],
            'error_debug': ['fehler', 'error', 'exception', 'bug', 'problem',
                           'stacktrace', 'debug', 'fix'],
            'documentation': ['dokumentation', 'doku', 'wiki', 'confluence',
                             'handbuch', 'beschreibung', 'explain'],
            'database': ['datenbank', 'database', 'sql', 'tabelle', 'table',
                        'query', 'db2', 'select'],
            'api': ['api', 'rest', 'endpoint', 'request', 'response',
                   'soap', 'wsdl', 'service'],
            'java': ['.java', 'java', 'spring', 'maven', 'pom'],
            'python': ['.py', 'python', 'pip', 'django', 'flask'],
            'devops': ['jenkins', 'pipeline', 'build', 'deploy', 'ci/cd',
                      'docker', 'kubernetes'],
            'config': ['config', 'konfiguration', 'settings', 'einstellung',
                      'yaml', 'properties'],
        }

        for category, keywords in category_patterns.items():
            if any(kw in query_lower for kw in keywords):
                categories.append(category)

        # Mindestens eine Kategorie
        if not categories:
            categories.append('general')

        return categories[:5]  # Max 5 Kategorien

    # ═══════════════════════════════════════════════════════════════════════
    # Private Maskierungs-Methoden
    # ═══════════════════════════════════════════════════════════════════════

    def _mask_url_auth(self, text: str) -> str:
        """Maskiert URLs mit eingebetteter Authentifizierung."""
        def replace_url_auth(match):
            protocol = match.group(1)
            host = match.group(4)
            # Host auch anonymisieren wenn intern
            masked_host = self._mask_hostname(host)
            return f"{protocol}***:***@{masked_host}"

        return self._PATTERNS['url_auth'].sub(replace_url_auth, text)

    def _mask_credentials(self, text: str) -> str:
        """Maskiert Credential Key-Value Paare und Auth-Token."""
        # Key=Value Credentials
        def replace_credential(match):
            key = match.group(1)
            return f"{key}=***"

        result = self._PATTERNS['credential_kv'].sub(replace_credential, text)

        # Bearer Token
        result = self._PATTERNS['bearer'].sub('Bearer ***', result)

        # Basic Auth
        result = self._PATTERNS['basic_auth'].sub('Basic ***', result)

        return result

    def _mask_ips(self, text: str) -> str:
        """Maskiert IP-Adressen."""
        # IPv4
        result = self._PATTERNS['ip'].sub('***.***.***.***', text)
        # IPv6
        result = self._PATTERNS['ipv6'].sub('***:***:***:***', result)
        return result

    def _mask_emails(self, text: str) -> str:
        """Maskiert Email-Adressen."""
        def replace_email(match):
            email = match.group(0)
            # Domain-Endung behalten für Kategorisierung
            parts = email.split('@')
            if len(parts) == 2:
                domain_parts = parts[1].split('.')
                if len(domain_parts) >= 2:
                    tld = domain_parts[-1]
                    return f"***@***.{tld}"
            return "***@***.***"

        return self._PATTERNS['email'].sub(replace_email, text)

    def _mask_paths(self, text: str) -> str:
        """Maskiert Dateipfade unter Beibehaltung der Struktur."""
        # Unix-Pfade
        result = self._mask_unix_paths(text)
        # Windows-Pfade
        result = self._mask_windows_paths(result)
        return result

    def _mask_unix_paths(self, text: str) -> str:
        """Maskiert Unix-Pfade."""
        def replace_path(match):
            path = match.group(0)
            segments = path.split('/')
            masked_segments = []

            for i, seg in enumerate(segments):
                if not seg:  # Leeres Segment (führender /)
                    masked_segments.append('')
                elif seg in self.config.path_whitelist:
                    # Whitelist-Segment behalten
                    masked_segments.append(seg)
                elif i == len(segments) - 1 and '.' in seg:
                    # Dateiname: Extension behalten
                    parts = seg.rsplit('.', 1)
                    if len(parts) == 2:
                        masked_segments.append(f"***.{parts[1]}")
                    else:
                        masked_segments.append('***')
                else:
                    masked_segments.append('***')

            return '/'.join(masked_segments)

        return self._PATTERNS['path_unix'].sub(replace_path, text)

    def _mask_windows_paths(self, text: str) -> str:
        """Maskiert Windows-Pfade."""
        def replace_path(match):
            path = match.group(0)
            # Laufwerk behalten
            if ':' in path:
                drive, rest = path.split(':', 1)
                segments = rest.split('\\')
                masked_segments = []

                for i, seg in enumerate(segments):
                    if not seg:
                        masked_segments.append('')
                    elif seg.lower() in [s.lower() for s in self.config.path_whitelist]:
                        masked_segments.append(seg)
                    elif i == len(segments) - 1 and '.' in seg:
                        parts = seg.rsplit('.', 1)
                        if len(parts) == 2:
                            masked_segments.append(f"***.{parts[1]}")
                        else:
                            masked_segments.append('***')
                    else:
                        masked_segments.append('***')

                sep = '\\'
                return f"{drive}:{sep}{sep.join(masked_segments)}"
            return '***'

        return self._PATTERNS['path_win'].sub(replace_path, text)

    def _mask_hostname(self, hostname: str) -> str:
        """Maskiert einen Hostnamen aber behält die Struktur."""
        parts = hostname.split('.')
        if len(parts) >= 2:
            # Subdomain maskieren, TLD behalten
            return '***.***.' + '.'.join(parts[-2:])
        return '***.***'

    def _mask_company_data(self, text: str) -> str:
        """Maskiert firmenspezifische Patterns."""
        result = text

        # Standard-Patterns (Ticket-IDs wie PROJ-1234)
        ticket_pattern = re.compile(r'\b[A-Z]{2,6}-\d{3,6}\b')
        result = ticket_pattern.sub('***-****', result)

        # Hostnamen mit Ports
        result = self._PATTERNS['hostname_port'].sub(
            lambda m: self._mask_hostname(m.group(0).split(':')[0]) + ':****',
            result
        )

        # Benutzerdefinierte Patterns
        for pattern in self._company_patterns:
            result = pattern.sub('***', result)

        return result


# ═══════════════════════════════════════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════════════════════════════════════

_anonymizer: Optional[Anonymizer] = None


def get_anonymizer(config: Optional[AnonymizerConfig] = None) -> Anonymizer:
    """Gibt Singleton-Instanz zurück."""
    global _anonymizer
    if _anonymizer is None:
        _anonymizer = Anonymizer(config)
    return _anonymizer
