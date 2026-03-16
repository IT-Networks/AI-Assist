"""
Test Session Manager - Verwaltet Session-Tokens pro Institut.

Features:
- Token-Speicherung pro Institut
- Automatisches Login bei fehlendem/abgelaufenem Token
- Sichere Credential-Auflösung ({{env:VAR}})
- Persistente Session-Speicherung
"""

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger(__name__)


@dataclass
class SessionInfo:
    """Informationen über eine aktive Session."""
    token: str
    created_at: datetime
    institut_nr: str
    user: str = ""
    expires_at: Optional[datetime] = None

    def is_expired(self, buffer_seconds: int = 0) -> bool:
        """Prüft ob Session abgelaufen ist (mit optionalem Puffer)."""
        if self.expires_at is None:
            return False
        return datetime.now() >= (self.expires_at - timedelta(seconds=buffer_seconds))

    def to_dict(self) -> Dict[str, Any]:
        """Serialisiert zu Dictionary."""
        return {
            'token': self.token,
            'institut_nr': self.institut_nr,
            'user': self.user,
            'created_at': self.created_at.isoformat(),
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionInfo":
        """Deserialisiert aus Dictionary."""
        return cls(
            token=data['token'],
            institut_nr=data.get('institut_nr', ''),
            user=data.get('user', ''),
            created_at=datetime.fromisoformat(data['created_at']),
            expires_at=datetime.fromisoformat(data['expires_at']) if data.get('expires_at') else None,
        )


@dataclass
class SessionStatus:
    """Status einer Session für API-Response."""
    institut_nr: str = ""
    has_token: bool = False
    is_expired: bool = False
    expires_at: Optional[datetime] = None
    user: str = ""
    token_preview: str = ""


class TestSessionManager:
    """
    Verwaltet Session-Tokens pro Institut.

    Session-Key: institut_nr (z.B. "001", "002")
    """

    # Pattern für Umgebungsvariablen: {{env:VAR_NAME}}
    ENV_PATTERN = re.compile(r'\{\{env:(\w+)\}\}')

    def __init__(
        self,
        storage_path: str = "data/test_tool/sessions.json",
        refresh_before_expiry_seconds: int = 300
    ):
        self.storage_path = Path(storage_path)
        self.refresh_buffer = refresh_before_expiry_seconds
        self._sessions: Dict[str, SessionInfo] = {}
        self._load()

    def _load(self):
        """Lädt Sessions aus Datei."""
        if not self.storage_path.exists():
            return

        try:
            data = json.loads(self.storage_path.read_text(encoding='utf-8'))
            for key, session_data in data.items():
                try:
                    self._sessions[key] = SessionInfo.from_dict(session_data)
                except (KeyError, ValueError) as e:
                    logger.warning(f"Ungültige Session ignoriert ({key}): {e}")
        except Exception as e:
            logger.warning(f"Session-Datei konnte nicht geladen werden: {e}")

    def _save(self):
        """Speichert Sessions in Datei."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            key: session.to_dict()
            for key, session in self._sessions.items()
        }

        self.storage_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding='utf-8'
        )

    async def get_token(
        self,
        institut_nr: str,
        force_refresh: bool = False
    ) -> str:
        """
        Gibt gültigen Session-Token für ein Institut zurück.

        Führt automatisch Login durch wenn nötig.

        Args:
            institut_nr: Institut-Nummer
            force_refresh: Erzwingt neuen Login

        Returns:
            Session-Token

        Raises:
            ValueError: Login fehlgeschlagen oder Institut nicht konfiguriert
        """
        session = self._sessions.get(institut_nr)

        # Token vorhanden und gültig?
        if session and not force_refresh:
            if not session.is_expired(self.refresh_buffer):
                return session.token
            else:
                logger.info(f"Session abgelaufen für Institut {institut_nr}, erneuere...")

        # Login durchführen
        return await self._login(institut_nr)

    async def _login(self, institut_nr: str) -> str:
        """
        Führt Login für ein Institut durch.

        Args:
            institut_nr: Institut-Nummer

        Returns:
            Neuer Session-Token

        Raises:
            ValueError: Institut nicht gefunden oder Login fehlgeschlagen
        """
        from app.core.config import settings

        # Institut finden
        institut = next(
            (i for i in settings.test_tool.institute if i.institut_nr == institut_nr and i.enabled),
            None
        )
        if not institut:
            available = [i.institut_nr for i in settings.test_tool.institute if i.enabled]
            raise ValueError(
                f"Institut '{institut_nr}' nicht gefunden oder deaktiviert. "
                f"Verfügbar: {available}"
            )

        # Credentials auflösen
        user = self._resolve_env(institut.user)
        password = self._resolve_env(institut.password)

        logger.debug(f"[Login] Institut {institut_nr}: user_raw='{institut.user}', user_resolved='{user}'")
        logger.debug(f"[Login] Institut {institut_nr}: password_raw='{institut.password[:3] if institut.password else ''}...', password_resolved={'***' if password else '(leer)'}")

        if not user or not password:
            raise ValueError(
                f"Credentials für Institut {institut_nr} nicht vollständig. "
                f"User: '{institut.user}' -> '{user}', Password: {'gesetzt' if institut.password else 'leer'} -> {'gesetzt' if password else 'leer'}"
            )

        logger.info(f"Login für Institut {institut_nr} als '{user}'...")

        # Login-URL prüfen
        if not settings.test_tool.login_url:
            raise ValueError("login_url nicht konfiguriert in test_tool")

        # Globales Login-Template aus Config verwenden
        login_template = settings.test_tool.login_template or "login.soap.xml"

        # Template laden
        from app.services.test_template_engine import get_template_engine
        engine = get_template_engine()

        try:
            template = engine.load_template("", login_template)
        except FileNotFoundError:
            raise ValueError(
                f"Login-Template nicht gefunden: {login_template}\n"
                f"Pfad: {settings.test_tool.templates_path}/{login_template}"
            )

        # Template füllen
        auto_params = {
            'institut': institut_nr,
            'user': user,
            'password': password,
        }
        logger.debug(f"[Login] Template-Parameter: institut='{institut_nr}', user='{user}', password={'***' if password else '(leer)'}")

        envelope = engine.fill_template(template, {}, auto_params=auto_params)

        # Debug: Prüfen ob Platzhalter ersetzt wurden
        unreplaced = []
        if '{{user}}' in envelope:
            unreplaced.append('{{user}}')
        if '{{password}}' in envelope:
            unreplaced.append('{{password}}')
        if '{{institut}}' in envelope:
            unreplaced.append('{{institut}}')

        if unreplaced:
            logger.error(f"[Login] FEHLER: Platzhalter nicht ersetzt: {unreplaced}")
            logger.error(f"[Login] auto_params keys: {list(auto_params.keys())}")
            logger.error(f"[Login] Envelope (erste 500 Zeichen): {envelope[:500]}")
            raise ValueError(
                f"Template-Platzhalter nicht ersetzt: {unreplaced}. "
                f"Prüfe ob Template-Datei korrekt ist: {login_template}"
            )

        # Log envelope for debugging (ohne sensitive Daten)
        debug_envelope = envelope.replace(password, '***PASSWORD***') if password else envelope
        logger.info(f"[Login] Envelope erstellt, Länge: {len(envelope)} Bytes")
        logger.info(f"[Login] Envelope (maskiert):\n{debug_envelope}")

        # Headers (SOAP 1.1 Standard)
        headers = {
            'Content-Type': 'text/xml; charset=utf-8',
        }

        # Request ausführen
        try:
            async with httpx.AsyncClient(
                timeout=60,
                verify=settings.test_tool.verify_ssl
            ) as client:
                response = await client.post(
                    settings.test_tool.login_url,
                    content=envelope.encode('utf-8'),
                    headers=headers
                )
        except Exception as e:
            raise ValueError(f"Login-Request fehlgeschlagen: {e}")

        # Response prüfen
        if response.status_code >= 400:
            # Envelope für Debugging (Password maskiert)
            debug_envelope = envelope.replace(password, '***') if password else envelope
            logger.error(f"[Login] HTTP {response.status_code} von {settings.test_tool.login_url}")
            logger.error(f"[Login] Request-Envelope (vollständig, {len(envelope)} Bytes):\n{debug_envelope}")
            logger.error(f"[Login] Response:\n{response.text}")
            raise ValueError(
                f"Login fehlgeschlagen: HTTP {response.status_code}\n"
                f"URL: {settings.test_tool.login_url}\n"
                f"Response: {response.text[:500]}\n"
                f"Tipp: Prüfe ob login.soap.xml die korrekten Namespaces für deinen SOAP-Server hat."
            )

        # Token extrahieren mit globalem XPath
        session_token_xpath = settings.test_tool.session_token_xpath or "//SessionToken/text()"
        token = self._extract_xpath(response.text, session_token_xpath)
        if not token:
            raise ValueError(
                f"Session-Token konnte nicht extrahiert werden.\n"
                f"XPath: {session_token_xpath}\n"
                f"Response: {response.text[:500]}"
            )

        # Ablaufzeit (optional, derzeit nicht global konfiguriert)
        expires_at = None

        # Session speichern
        self._sessions[institut_nr] = SessionInfo(
            token=token,
            institut_nr=institut_nr,
            user=user,
            created_at=datetime.now(),
            expires_at=expires_at,
        )
        self._save()

        logger.info(f"Login erfolgreich für Institut {institut_nr}")
        return token

    def _resolve_env(self, value: str) -> str:
        """Löst {{env:VAR}} Platzhalter auf."""
        if not value:
            return value

        match = self.ENV_PATTERN.match(value)
        if match:
            env_var = match.group(1)
            env_value = os.environ.get(env_var, '')
            if not env_value:
                logger.warning(f"Umgebungsvariable {env_var} nicht gesetzt")
            return env_value

        return value

    def _extract_xpath(self, xml_content: str, xpath: str) -> Optional[str]:
        """Extrahiert Wert per XPath aus XML."""
        if not xpath:
            return None

        try:
            root = ET.fromstring(xml_content)

            # Suche nach Tag-Namen (XPath vereinfacht)
            import re
            tag_match = re.search(r'//(\w+)', xpath)
            if tag_match:
                tag_name = tag_match.group(1)
                for elem in root.iter():
                    local_name = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
                    if local_name == tag_name:
                        return elem.text

        except ET.ParseError as e:
            logger.error(f"XML-Parse-Fehler bei XPath-Extraktion: {e}")
        except Exception as e:
            logger.error(f"XPath-Extraktion fehlgeschlagen: {e}")

        return None

    def get_status(self, institut_nr: str) -> SessionStatus:
        """Gibt Status einer Session zurück."""
        session = self._sessions.get(institut_nr)

        if not session:
            return SessionStatus(institut_nr=institut_nr, has_token=False)

        return SessionStatus(
            institut_nr=institut_nr,
            has_token=True,
            is_expired=session.is_expired(),
            expires_at=session.expires_at,
            user=session.user,
            token_preview=session.token[:8] + '...' if len(session.token) > 8 else session.token
        )

    def invalidate(self, institut_nr: str):
        """Invalidiert eine Session."""
        if institut_nr in self._sessions:
            del self._sessions[institut_nr]
            self._save()
            logger.info(f"Session invalidiert: Institut {institut_nr}")

    def invalidate_all(self):
        """Invalidiert alle Sessions."""
        count = len(self._sessions)
        self._sessions.clear()
        self._save()
        logger.info(f"{count} Sessions invalidiert")

    def get_all_sessions(self) -> Dict[str, SessionStatus]:
        """Gibt Status aller Sessions zurück."""
        return {
            institut_nr: SessionStatus(
                institut_nr=institut_nr,
                has_token=True,
                is_expired=session.is_expired(),
                expires_at=session.expires_at,
                user=session.user,
                token_preview=session.token[:8] + '...' if len(session.token) > 8 else session.token
            )
            for institut_nr, session in self._sessions.items()
        }

    def get_institut_credentials(self, institut_nr: str) -> Dict[str, str]:
        """Gibt aufgelöste Credentials für ein Institut zurück."""
        from app.core.config import settings

        institut = next(
            (i for i in settings.test_tool.institute if i.institut_nr == institut_nr),
            None
        )
        if not institut:
            raise ValueError(f"Institut {institut_nr} nicht gefunden")

        return {
            'institut': institut_nr,
            'user': self._resolve_env(institut.user),
            'password': self._resolve_env(institut.password),
        }


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_session_manager: Optional[TestSessionManager] = None


def get_session_manager() -> TestSessionManager:
    """Gibt Singleton-Instanz des Session-Managers zurück."""
    global _session_manager
    if _session_manager is None:
        from app.core.config import settings
        _session_manager = TestSessionManager(
            storage_path=settings.test_tool.session_storage_file,
            refresh_before_expiry_seconds=settings.test_tool.session_refresh_before_expiry_seconds
        )
    return _session_manager
