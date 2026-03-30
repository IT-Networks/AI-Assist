"""
HP ALM/Quality Center REST API Client.

Unterstützt Session-basierte Authentifizierung (LWSSO) und CRUD-Operationen
fuer Testfaelle, Test-Sets und Test-Runs.

Auth-Flow:
1. POST /authentication-point/alm-authenticate
   - Versucht zuerst JSON-Body: {"alm-authentication": {"user": ..., "password": ...}}
   - Fallback auf Basic Auth Header wenn JSON-Auth fehlschlaegt
2. Extrahiere LWSSO_COOKIE_KEY aus Response-Cookies oder Set-Cookie Headers
3. POST /rest/site-session mit LWSSO Cookie
4. Extrahiere QCSession, ALM_USER, XSRF-TOKEN

Die Authentifizierung erfolgt automatisch bei jedem ALM-Tool-Aufruf.
"""

import base64
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

import httpx

from app.core.config import settings
from app.core.exceptions import ALMError

logger = logging.getLogger(__name__)


def _strip_html(html: str) -> str:
    """
    Konvertiert HTML zu lesbarem Plain-Text.

    - Ersetzt <br>, <br/>, </p>, </div> durch Newlines
    - Entfernt alle anderen HTML-Tags
    - Dekodiert HTML-Entities (&nbsp;, &lt;, etc.)
    - Bereinigt mehrfache Leerzeilen
    """
    if not html:
        return ""

    import html as html_module

    text = html

    # Block-Elemente durch Newlines ersetzen
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</p>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</div>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</li>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</tr>', '\n', text, flags=re.IGNORECASE)

    # Listen-Bullets hinzufuegen
    text = re.sub(r'<li[^>]*>', '• ', text, flags=re.IGNORECASE)

    # Alle verbleibenden Tags entfernen
    text = re.sub(r'<[^>]+>', '', text)

    # HTML-Entities dekodieren
    text = html_module.unescape(text)

    # Mehrfache Leerzeichen/Newlines bereinigen
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n', '\n\n', text)
    text = text.strip()

    return text

# Shared HTTP Client fuer Connection-Pooling
_http_client: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    """Gibt den shared HTTP-Client fuer ALM zurueck."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=settings.alm.timeout_seconds,
            verify=settings.alm.verify_ssl,
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
                keepalive_expiry=30.0
            )
        )
    return _http_client


async def _reset_http_client():
    """Setzt den HTTP-Client zurueck (loescht Cookie-Jar)."""
    global _http_client
    if _http_client is not None:
        try:
            await _http_client.aclose()
        except Exception:
            pass
        _http_client = None


async def close_alm_client():
    """Schliesst den HTTP-Client (fuer Shutdown)."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


# ══════════════════════════════════════════════════════════════════════════════
# Data Models
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ALMSession:
    """Verwaltet ALM Session-Cookies."""
    lwsso_cookie: str = ""
    qc_session: str = ""
    alm_user: str = ""
    xsrf_token: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    ttl_seconds: int = 3600

    def is_valid(self) -> bool:
        """Prueft ob die Session noch gueltig ist."""
        expires_at = self.created_at + timedelta(seconds=self.ttl_seconds)
        # 5 Minuten Puffer vor Ablauf
        return datetime.now() < (expires_at - timedelta(minutes=5))

    def get_cookies(self) -> Dict[str, str]:
        """Gibt alle Session-Cookies als Dict zurueck."""
        return {
            "LWSSO_COOKIE_KEY": self.lwsso_cookie,
            "QCSession": self.qc_session,
            "ALM_USER": self.alm_user,
            "XSRF-TOKEN": self.xsrf_token,
        }


@dataclass
class ALMTestStep:
    """Design-Step eines Testfalls."""
    id: int
    step_order: int
    name: str
    description: str
    expected_result: str


@dataclass
class ALMTest:
    """Testfall aus dem Test Pool (Testfall-Definitionen)."""
    id: int
    name: str
    description: str = ""
    folder_id: int = 0
    folder_path: str = ""
    test_type: str = "MANUAL"
    status: str = ""
    owner: str = ""
    creation_date: Optional[str] = None
    steps: List[ALMTestStep] = field(default_factory=list)

    def to_markdown(self) -> str:
        """Formatiert als Markdown fuer Agent-Response."""
        md = f"## Testfall: {self.name} (ID: {self.id})\n\n"
        md += f"**Folder:** {self.folder_path}\n"
        md += f"**Typ:** {self.test_type}\n"
        md += f"**Status:** {self.status}\n"
        md += f"**Owner:** {self.owner}\n\n"

        if self.description:
            clean_desc = _strip_html(self.description)
            md += f"### Beschreibung\n{clean_desc}\n\n"

        if self.steps:
            md += "### Test-Schritte\n\n"
            md += "| # | Name | Beschreibung | Erwartetes Ergebnis |\n"
            md += "|---|------|--------------|---------------------|\n"
            for step in self.steps:
                step_name = (step.name or "-").replace("|", "\\|").replace("\n", " ")
                desc = _strip_html(step.description).replace("|", "\\|").replace("\n", " ")
                expected = _strip_html(step.expected_result).replace("|", "\\|").replace("\n", " ")
                md += f"| {step.step_order} | {step_name} | {desc} | {expected} |\n"

        return md


@dataclass
class ALMFolder:
    """Test Pool Folder (Ordnerstruktur fuer Testfall-Definitionen)."""
    id: int
    name: str
    parent_id: int
    path: str = ""


@dataclass
class ALMTestSetFolder:
    """Test Lab Folder (fuer Test-Sets)."""
    id: int
    name: str
    parent_id: int
    path: str = ""


@dataclass
class ALMTestSet:
    """Test-Set aus dem Test Lab."""
    id: int
    name: str
    folder_id: int
    status: str = ""
    description: str = ""
    user_01: str = "Nur Intern"  # Custom Field: Anzeige (Extern, Nur Intern, Sparkasse)


@dataclass
class ALMTestInstance:
    """Test-Instance in einem Test-Set."""
    id: int
    test_id: int
    test_name: str
    test_set_id: int
    status: str = "No Run"
    last_run_id: Optional[int] = None
    exec_date: Optional[str] = None
    tester: str = ""


@dataclass
class ALMRun:
    """Test-Run Ergebnis."""
    id: int
    test_instance_id: int
    status: str
    comment: str = ""
    execution_date: Optional[str] = None
    executor: str = ""


# ══════════════════════════════════════════════════════════════════════════════
# ALM Client
# ══════════════════════════════════════════════════════════════════════════════

class ALMClient:
    """HP ALM REST API Client mit Session-Management."""

    def __init__(self):
        self.base_url = settings.alm.base_url.rstrip("/")
        self.domain = settings.alm.domain
        self.project = settings.alm.project

        # Credentials: Zentrale Referenz oder direkte Werte
        if settings.alm.credential_ref:
            cred = settings.credentials.get(settings.alm.credential_ref)
            if cred:
                self.username = cred.username
                self.password = cred.password or cred.token
            else:
                # Fallback wenn credential_ref ungueltig
                self.username = settings.alm.username
                self.password = settings.alm.password
        else:
            self.username = settings.alm.username
            self.password = settings.alm.password

        self._session: Optional[ALMSession] = None
        self._folder_cache: Dict[int, ALMFolder] = {}
        self._folder_cache_time: Optional[datetime] = None
        self._test_lab_folder_cache: Dict[int, ALMTestSetFolder] = {}
        self._test_lab_folder_cache_time: Optional[datetime] = None

    def _check_configured(self):
        """Prueft ob ALM konfiguriert ist."""
        if not self.base_url:
            raise ALMError("ALM ist nicht konfiguriert (base_url fehlt in config.yaml)")
        if not self.domain or not self.project:
            raise ALMError("ALM Domain und Project muessen konfiguriert sein")

    def _rest_url(self, path: str) -> str:
        """Baut die REST API URL."""
        return f"{self.base_url}/rest/domains/{self.domain}/projects/{self.project}{path}"

    def _auth_headers(self) -> Dict[str, str]:
        """Erstellt Header fuer initiale Authentifizierung (JSON-basiert)."""
        return {
            "cache-control": "no-cache",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _auth_body(self) -> Dict[str, Any]:
        """Erstellt JSON-Body fuer ALM-Authentifizierung."""
        return {
            "alm-authentication": {
                "user": self.username,
                "password": self.password
            }
        }

    def _session_headers(self) -> Dict[str, str]:
        """Erstellt Header mit XSRF-Token fuer authentifizierte Requests."""
        headers = {
            "Accept": "application/xml",
            "Content-Type": "application/xml",
        }
        if self._session and self._session.xsrf_token:
            headers["X-XSRF-TOKEN"] = self._session.xsrf_token
        return headers

    def _session_cookies(self) -> Dict[str, str]:
        """Gibt Session-Cookies zurueck."""
        if self._session:
            return self._session.get_cookies()
        return {}

    def _extract_lwsso_cookie(self, resp: httpx.Response) -> str:
        """
        Extrahiert LWSSO_COOKIE_KEY aus einer HTTP-Response.

        Prueft zuerst resp.cookies, dann alle Set-Cookie Header.
        """
        # Methode 1: Direkt aus httpx Cookies
        lwsso = resp.cookies.get("LWSSO_COOKIE_KEY", "")
        if lwsso:
            logger.debug("ALM: LWSSO aus resp.cookies extrahiert")
            return lwsso

        # Methode 2: Aus allen Set-Cookie Headers
        set_cookies = resp.headers.get_list("Set-Cookie")
        for sc in set_cookies:
            if "LWSSO_COOKIE_KEY=" in sc:
                match = re.search(r"LWSSO_COOKIE_KEY=([^;]+)", sc)
                if match:
                    logger.debug("ALM: LWSSO aus Set-Cookie Header extrahiert")
                    return match.group(1)

        # Debug: Logge was wir bekommen haben
        logger.warning(
            f"ALM: LWSSO_COOKIE_KEY nicht gefunden. "
            f"Response-Status: {resp.status_code}, "
            f"Cookies: {list(resp.cookies.keys())}, "
            f"Set-Cookie Headers: {len(set_cookies)}"
        )
        return ""

    def _extract_session_cookies(self, resp: httpx.Response) -> Dict[str, str]:
        """
        Extrahiert alle Session-Cookies aus einer HTTP-Response.

        Returns:
            Dict mit QCSession, ALM_USER, XSRF-TOKEN
        """
        result = {
            "qc_session": resp.cookies.get("QCSession", ""),
            "alm_user": resp.cookies.get("ALM_USER", ""),
            "xsrf_token": resp.cookies.get("XSRF-TOKEN", ""),
        }

        # Fallback: Aus Set-Cookie Headers
        set_cookies = resp.headers.get_list("Set-Cookie")
        for sc in set_cookies:
            if "QCSession=" in sc and not result["qc_session"]:
                match = re.search(r"QCSession=([^;]+)", sc)
                if match:
                    result["qc_session"] = match.group(1)
            if "XSRF-TOKEN=" in sc and not result["xsrf_token"]:
                match = re.search(r"XSRF-TOKEN=([^;]+)", sc)
                if match:
                    result["xsrf_token"] = match.group(1)
            if "ALM_USER=" in sc and not result["alm_user"]:
                match = re.search(r"ALM_USER=([^;]+)", sc)
                if match:
                    result["alm_user"] = match.group(1)

        return result

    # ═══════════════════════════════════════════════════════════════════════
    # Session Management
    # ═══════════════════════════════════════════════════════════════════════

    async def authenticate(self) -> ALMSession:
        """
        Authentifiziert gegen ALM und erstellt Session.

        Nutzt JSON-basierte Authentifizierung:
        POST /authentication-point/alm-authenticate
        Body: {"alm-authentication": {"user": "...", "password": "..."}}

        Falls JSON-Auth fehlschlaegt, wird Basic Auth als Fallback versucht.

        Returns:
            ALMSession mit allen Cookies
        """
        self._check_configured()
        client = _get_http_client()

        # Step 1: Initial Authentication (JSON-basiert)
        auth_url = f"{self.base_url}/authentication-point/alm-authenticate"
        logger.info(f"ALM: Authentifiziere gegen {auth_url}")

        lwsso_cookie = ""

        # Versuch 1: JSON-Auth (mit kurzem Timeout - Fallback soll schnell greifen)
        try:
            resp = await client.post(
                auth_url,
                headers=self._auth_headers(),
                json=self._auth_body(),
                timeout=10.0,  # Kurzer Timeout fuer JSON-Auth-Versuch
            )
            resp.raise_for_status()
            lwsso_cookie = self._extract_lwsso_cookie(resp)
            if lwsso_cookie:
                logger.info("ALM: JSON-Auth erfolgreich")
        except httpx.HTTPStatusError as e:
            logger.warning(f"ALM JSON-Auth fehlgeschlagen ({e.response.status_code}), versuche Basic Auth...")
        except (httpx.RequestError, httpx.TimeoutException) as e:
            logger.warning(f"ALM JSON-Auth Timeout/Fehler ({type(e).__name__}), versuche Basic Auth...")

        # Versuch 2: Basic Auth als Fallback
        if not lwsso_cookie:
            try:
                credentials = base64.b64encode(
                    f"{self.username}:{self.password}".encode()
                ).decode()
                resp = await client.post(
                    auth_url,
                    headers={
                        "Authorization": f"Basic {credentials}",
                        "Accept": "application/json",
                    },
                )
                resp.raise_for_status()
                lwsso_cookie = self._extract_lwsso_cookie(resp)
                if lwsso_cookie:
                    logger.info("ALM: Basic-Auth erfolgreich")
            except httpx.HTTPStatusError as e:
                raise ALMError(f"ALM Authentifizierung fehlgeschlagen: {e.response.status_code}") from e
            except httpx.RequestError as e:
                raise ALMError(f"ALM Verbindungsfehler: {e}") from e

        if not lwsso_cookie:
            raise ALMError(
                "ALM Authentifizierung: LWSSO_COOKIE_KEY nicht erhalten. "
                "Bitte pruefen Sie Benutzername/Passwort und Server-URL."
            )

        logger.debug("ALM: LWSSO Cookie erhalten")

        # Step 2: Create Session
        session_url = f"{self.base_url}/rest/site-session"
        try:
            resp = await client.post(
                session_url,
                headers={"Accept": "application/xml"},
                cookies={"LWSSO_COOKIE_KEY": lwsso_cookie},
                content="",
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise ALMError(f"ALM Session-Erstellung fehlgeschlagen: {e.response.status_code}") from e

        # Session-Cookies extrahieren
        session_cookies = self._extract_session_cookies(resp)

        self._session = ALMSession(
            lwsso_cookie=lwsso_cookie,
            qc_session=session_cookies["qc_session"],
            alm_user=session_cookies["alm_user"],
            xsrf_token=session_cookies["xsrf_token"],
            created_at=datetime.now(),
            ttl_seconds=settings.alm.session_cache_ttl,
        )

        logger.info(f"ALM: Site-Session erstellt fuer User {session_cookies['alm_user'] or self.username}")

        # Note: Project Session wird bei Bedarf erstellt (nicht hier!)
        # Siehe _create_project_session() - wird nur bei verify_project=True aufgerufen

        return self._session

    async def _create_project_session(self) -> None:
        """
        Erstellt eine Projekt-Session fuer das aktuelle Projekt.

        Dies ist optional und wird nur bei manchen ALM-Versionen benoetigt.
        Fehler werden ignoriert, da nicht alle ALM-Server diesen Endpoint haben.
        """
        if not self._session:
            return

        try:
            client = _get_http_client()
            # Manche ALM-Versionen erwarten /session am Ende
            project_session_url = f"{self.base_url}/rest/domains/{self.domain}/projects/{self.project}/session"

            logger.debug(f"ALM: Versuche Projekt-Session fuer {self.domain}/{self.project}")

            resp = await client.post(
                project_session_url,
                headers=self._session_headers(),
                cookies=self._session_cookies(),
                content="",
            )
            # 200 oder 201 sind OK
            if resp.status_code in (200, 201):
                logger.info(f"ALM: Projekt-Session erstellt fuer {self.domain}/{self.project}")
            else:
                # Nicht fatal - manche ALM-Versionen brauchen das nicht
                logger.debug(f"ALM: Projekt-Session Status {resp.status_code} (optional, wird ignoriert)")
        except Exception as e:
            # Komplett ignorieren - dieser Endpoint ist optional
            logger.debug(f"ALM: Projekt-Session nicht verfuegbar: {e}")

    async def ensure_session(self) -> ALMSession:
        """Stellt sicher dass eine gueltige Session existiert."""
        if not self._session or not self._session.is_valid():
            await self.authenticate()
        return self._session

    async def logout(self) -> None:
        """Beendet ALM Session und setzt HTTP-Client zurueck."""
        if not self._session:
            return

        client = _get_http_client()
        try:
            await client.delete(
                f"{self.base_url}/authentication-point/logout",
                cookies=self._session_cookies(),
            )
        except Exception as e:
            logger.warning(f"ALM Logout fehlgeschlagen: {e}")

        self._session = None

        # WICHTIG: HTTP-Client zuruecksetzen um Cookie-Jar zu leeren!
        # Sonst werden alte Cookies beim naechsten Login gemerged.
        await _reset_http_client()

        logger.info("ALM: Session und HTTP-Client zurueckgesetzt")

    async def test_connection(self, verify_project: bool = False) -> Dict[str, Any]:
        """
        Testet die ALM-Verbindung und optional ob das Projekt existiert.

        Args:
            verify_project: Wenn True, wird geprueft ob das Projekt existiert
                           (default: False fuer schnellen Connection-Test)

        Returns:
            {"success": True, "user": "...", "domain": "...", "project": "..."}
            oder {"success": False, "error": "..."}
        """
        try:
            session = await self.authenticate()

            # Zusaetzlich Projekt-Existenz pruefen mit echtem API-Aufruf
            if verify_project:
                logger.debug(f"ALM: Pruefe Projekt-Existenz via /tests Endpoint")
                try:
                    # Erst Projekt-Session erstellen (manche ALM-Versionen benoetigen das)
                    await self._create_project_session()
                    # Dann testen ob Projekt erreichbar
                    await self._request("GET", "/tests", None, {"page-size": "1"})
                    logger.info(f"ALM: Projekt {self.domain}/{self.project} verifiziert")
                except ALMError as e:
                    error_msg = str(e).lower()
                    if "404" in error_msg:
                        return {
                            "success": False,
                            "error": f"Projekt '{self.project}' nicht gefunden in Domain '{self.domain}'"
                        }
                    elif "401" in error_msg:
                        return {
                            "success": False,
                            "error": f"Keine Berechtigung fuer Projekt '{self.project}'"
                        }
                    # Anderer Fehler - trotzdem als Projekt-Problem melden
                    return {
                        "success": False,
                        "error": f"Projekt '{self.project}' nicht erreichbar: {e}"
                    }

            return {
                "success": True,
                "user": session.alm_user or self.username,
                "domain": self.domain,
                "project": self.project,
            }
        except ALMError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": f"Unerwarteter Fehler: {e}"}

    async def list_domains(self) -> List[str]:
        """
        Listet verfuegbare Domains.

        Returns:
            Liste von Domain-Namen
        """
        if not self.base_url:
            raise ALMError("ALM base_url ist nicht konfiguriert")

        await self.ensure_session()
        client = _get_http_client()

        url = f"{self.base_url}/rest/domains"
        try:
            resp = await client.get(
                url,
                headers=self._session_headers(),
                cookies=self._session_cookies(),
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise ALMError(f"Fehler beim Laden der Domains: {e.response.status_code}") from e

        domains = []
        try:
            root = ET.fromstring(resp.content)
            for domain in root.findall(".//Domain"):
                name = domain.get("Name", "")
                if name:
                    domains.append(name)
        except ET.ParseError:
            logger.warning("Konnte Domain-XML nicht parsen")

        return domains

    async def list_projects(self, domain: Optional[str] = None) -> List[Dict[str, str]]:
        """
        Listet verfuegbare Projekte in einer Domain.

        Args:
            domain: Optional - Domain (default: aktuelle Domain)

        Returns:
            Liste von {"name": ..., "domain": ...}
        """
        if not self.base_url:
            raise ALMError("ALM base_url ist nicht konfiguriert")

        target_domain = domain or self.domain
        if not target_domain:
            raise ALMError("Keine Domain angegeben")

        await self.ensure_session()
        client = _get_http_client()

        url = f"{self.base_url}/rest/domains/{target_domain}/projects"
        try:
            resp = await client.get(
                url,
                headers=self._session_headers(),
                cookies=self._session_cookies(),
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise ALMError(f"Fehler beim Laden der Projekte: {e.response.status_code}") from e

        projects = []
        try:
            root = ET.fromstring(resp.content)
            for project in root.findall(".//Project"):
                name = project.get("Name", "")
                if name:
                    projects.append({"name": name, "domain": target_domain})
        except ET.ParseError:
            logger.warning("Konnte Project-XML nicht parsen")

        logger.info(f"ALM: {len(projects)} Projekte in Domain {target_domain} gefunden")
        return projects

    async def switch_project(self, project: str, domain: Optional[str] = None) -> Dict[str, Any]:
        """
        Wechselt das aktive Projekt zur Laufzeit.

        Fuehrt einen sauberen Logout durch bevor zum neuen Projekt gewechselt wird.
        Wenn bereits im Zielprojekt, wird nichts geaendert.

        Args:
            project: Neuer Projekt-Name
            domain: Optional - Neue Domain

        Returns:
            {"success": True, "domain": ..., "project": ..., "already_active": bool}
        """
        old_domain = self.domain
        old_project = self.project
        target_domain = domain or self.domain

        # Pruefen ob bereits im Zielprojekt - dann nichts tun!
        if project == old_project and target_domain == old_domain:
            logger.info(f"ALM: Bereits im Projekt {old_domain}/{old_project}, kein Wechsel noetig")
            return {
                "success": True,
                "domain": self.domain,
                "project": self.project,
                "previous_domain": old_domain,
                "previous_project": old_project,
                "already_active": True,
            }

        # Wichtig: Erst Logout der alten Session durchfuehren!
        if self._session:
            logger.info(f"ALM: Logout aus {old_domain}/{old_project} vor Projektwechsel")
            await self.logout()

        self.domain = target_domain
        self.project = project

        # Session und Cache zuruecksetzen
        self._session = None
        self._folder_cache.clear()
        self._folder_cache_time = None
        self._test_lab_folder_cache.clear()
        self._test_lab_folder_cache_time = None

        logger.info(f"ALM: Projekt gewechselt von {old_domain}/{old_project} zu {self.domain}/{self.project}")

        return {
            "success": True,
            "domain": self.domain,
            "project": self.project,
            "previous_domain": old_domain,
            "previous_project": old_project,
            "already_active": False,
        }

    def get_current_context(self) -> Dict[str, Any]:
        """
        Gibt den aktuellen Kontext (Domain/Projekt) zurueck.

        Returns:
            {"domain": ..., "project": ..., "base_url": ..., "user": ...}
        """
        return {
            "domain": self.domain,
            "project": self.project,
            "base_url": self.base_url,
            "user": self.username,
            "has_session": self._session is not None and self._session.is_valid(),
        }

    # ═══════════════════════════════════════════════════════════════════════
    # Helper Methods
    # ═══════════════════════════════════════════════════════════════════════

    async def _request(
        self,
        method: str,
        path: str,
        body: Optional[str] = None,
        params: Optional[Dict[str, str]] = None,
    ) -> ET.Element:
        """
        Fuehrt einen authentifizierten Request aus.

        Returns:
            Parsed XML Element
        """
        await self.ensure_session()
        client = _get_http_client()

        url = self._rest_url(path)
        headers = self._session_headers()
        cookies = self._session_cookies()

        try:
            if method.upper() == "GET":
                resp = await client.get(url, headers=headers, cookies=cookies, params=params)
            elif method.upper() == "POST":
                resp = await client.post(url, headers=headers, cookies=cookies, content=body or "")
            elif method.upper() == "PUT":
                resp = await client.put(url, headers=headers, cookies=cookies, content=body or "")
            elif method.upper() == "DELETE":
                resp = await client.delete(url, headers=headers, cookies=cookies)
            else:
                raise ALMError(f"Unsupported HTTP method: {method}")

            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401 and settings.alm.auto_reconnect:
                # Session abgelaufen - neu authentifizieren
                logger.info("ALM: Session abgelaufen, re-authentifiziere...")
                self._session = None
                await self.ensure_session()
                # Retry
                return await self._request(method, path, body, params)
            raise ALMError(f"ALM API Fehler {e.response.status_code}: {e.response.text}") from e
        except httpx.RequestError as e:
            raise ALMError(f"ALM Verbindungsfehler: {e}") from e

        try:
            return ET.fromstring(resp.content)
        except ET.ParseError as e:
            # Log response content for debugging
            content_preview = resp.content[:200] if resp.content else b"(empty)"
            logger.error(f"ALM XML Parse Error. Response preview: {content_preview}")
            raise ALMError(f"ALM Response Parse Error: {e}. Content starts with: {content_preview[:50]}") from e

    def _parse_entity(self, entity: ET.Element) -> Dict[str, str]:
        """Parsed ein ALM Entity XML zu Dict."""
        result = {}
        fields = entity.find("Fields")
        if fields is not None:
            for field in fields.findall("Field"):
                name = field.get("Name", "")
                value_elem = field.find("Value")
                value = value_elem.text if value_elem is not None and value_elem.text else ""
                result[name] = value
        return result

    def _build_entity_xml(self, entity_type: str, fields: Dict[str, Any]) -> str:
        """Baut XML fuer Entity-Erstellung/Update."""
        xml = f'<?xml version="1.0" encoding="UTF-8"?>\n<Entity Type="{entity_type}">\n<Fields>\n'
        for name, value in fields.items():
            if value is not None:
                escaped_value = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                xml += f'<Field Name="{name}"><Value>{escaped_value}</Value></Field>\n'
        xml += '</Fields>\n</Entity>'
        return xml

    # ═══════════════════════════════════════════════════════════════════════
    # Test Operations (CRUD)
    # ═══════════════════════════════════════════════════════════════════════

    async def search_tests(
        self,
        query: str = "",
        folder_id: Optional[int] = None,
        owner: Optional[str] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        status: Optional[str] = None,
        test_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[ALMTest]:
        """
        Sucht Testfaelle mit erweiterten Filtern.

        Args:
            query: Suchbegriff (wird in Name gesucht)
            folder_id: Optional - nur in diesem Folder suchen
            owner: Optional - Testfall-Autor (Benutzername)
            created_after: Optional - Erstellt nach Datum (YYYY-MM-DD)
            created_before: Optional - Erstellt vor Datum (YYYY-MM-DD)
            status: Optional - Status (z.B. Ready, Design)
            test_type: Optional - MANUAL oder AUTOMATED
            limit: Max. Anzahl Ergebnisse

        Returns:
            Liste von ALMTest-Objekten
        """
        self._check_configured()

        # ALM Query-Syntax bauen
        # Syntax mit Leerzeichen: field['*pattern*'] (Quotes erforderlich!)
        query_parts = []

        if query:
            escaped = query.replace("'", "''")
            query_parts.append(f"name['*{escaped}*']")

        if folder_id is not None:
            query_parts.append(f"parent-id[{folder_id}]")

        if owner:
            escaped_owner = owner.replace("'", "''")
            query_parts.append(f"owner['*{escaped_owner}*']")

        if created_after:
            # Datum-Filter: creation-time (nicht creation-date!)
            query_parts.append(f"creation-time[>='{created_after}']")

        if created_before:
            query_parts.append(f"creation-time[<='{created_before}']")

        if status:
            query_parts.append(f"status['{status}']")

        if test_type:
            # subtype-id: MANUAL oder AUTOMATED
            query_parts.append(f"subtype-id['{test_type}']")

        params = {"page-size": str(limit)}
        if query_parts:
            params["query"] = "{" + ";".join(query_parts) + "}"

        logger.debug(f"ALM search_tests query: {params.get('query', 'none')}")

        root = await self._request("GET", "/tests", params=params)

        tests = []
        entities = root.find("Entities") or root
        for entity in entities.findall("Entity"):
            data = self._parse_entity(entity)
            tests.append(ALMTest(
                id=int(data.get("id", 0)),
                name=data.get("name", ""),
                description=data.get("description", ""),
                folder_id=int(data.get("parent-id", 0)),
                test_type=data.get("subtype-id", "MANUAL"),
                status=data.get("status", ""),
                owner=data.get("owner", ""),
                creation_date=data.get("creation-date"),
            ))

        logger.info(f"ALM: {len(tests)} Testfaelle gefunden")
        return tests

    async def get_test(self, test_id: int, include_steps: bool = True) -> ALMTest:
        """
        Laedt einen Testfall mit allen Details.

        Args:
            test_id: Test-ID
            include_steps: Auch Design-Steps laden

        Returns:
            ALMTest mit optionalen Steps
        """
        self._check_configured()

        root = await self._request("GET", f"/tests/{test_id}")
        data = self._parse_entity(root)

        test = ALMTest(
            id=int(data.get("id", test_id)),
            name=data.get("name", ""),
            description=data.get("description", ""),
            folder_id=int(data.get("parent-id", 0)),
            test_type=data.get("subtype-id", "MANUAL"),
            status=data.get("status", ""),
            owner=data.get("owner", ""),
            creation_date=data.get("creation-date"),
        )

        # Folder-Pfad ermitteln
        if test.folder_id:
            test.folder_path = await self.get_folder_path(test.folder_id)

        # Steps laden
        if include_steps:
            test.steps = await self.get_test_steps(test_id)

        logger.info(f"ALM: Testfall {test_id} geladen: {test.name}")
        return test

    async def get_test_steps(self, test_id: int) -> List[ALMTestStep]:
        """
        Laedt Design-Steps eines Testfalls.

        Args:
            test_id: Test-ID

        Returns:
            Liste von ALMTestStep
        """
        try:
            params = {"query": f"{{parent-id[{test_id}]}}"}
            logger.debug(f"ALM: Loading design-steps for test {test_id}")
            root = await self._request("GET", "/design-steps", params=params)

            steps = []
            entities = root.find("Entities") or root
            for entity in entities.findall("Entity"):
                data = self._parse_entity(entity)
                logger.debug(f"ALM: Step data: {data}")

                # Sichere int-Konvertierung
                step_id = 0
                step_order = 0
                try:
                    step_id = int(data.get("id", 0) or 0)
                    step_order = int(data.get("step-order", 0) or 0)
                except (ValueError, TypeError):
                    pass

                steps.append(ALMTestStep(
                    id=step_id,
                    step_order=step_order,
                    name=data.get("name", ""),
                    description=data.get("description", ""),
                    expected_result=data.get("expected", ""),
                ))

            # Nach step_order sortieren
            steps.sort(key=lambda s: s.step_order)
            logger.info(f"ALM: {len(steps)} Design-Steps fuer Test {test_id} geladen")
            return steps

        except ALMError as e:
            logger.warning(f"ALM: Fehler beim Laden der Design-Steps: {e}")
            return []
        except Exception as e:
            logger.exception(f"ALM: Unerwarteter Fehler bei Design-Steps: {e}")
            return []

    async def create_test(
        self,
        name: str,
        folder_id: int,
        description: str = "",
        test_type: str = "",
        steps: Optional[List[Dict[str, str]]] = None,
    ) -> ALMTest:
        """
        Erstellt einen neuen Testfall.

        Args:
            name: Testfall-Name
            folder_id: Ziel-Folder-ID (parent-id)
            description: Beschreibung
            test_type: MANUAL oder AUTOMATED (default aus Config)
            steps: Optional - Liste von {"description": ..., "expected": ...}

        Returns:
            Erstellter ALMTest
        """
        self._check_configured()

        if not test_type:
            test_type = settings.alm.default_test_type

        fields = {
            "name": name,
            "parent-id": folder_id,
            "subtype-id": test_type,
        }
        if description:
            fields["description"] = description

        xml = self._build_entity_xml("test", fields)
        root = await self._request("POST", "/tests", body=xml)
        data = self._parse_entity(root)

        test_id = int(data.get("id", 0))
        logger.info(f"ALM: Testfall erstellt: ID={test_id}, Name={name}")

        # Steps hinzufuegen
        if steps:
            for i, step in enumerate(steps):
                await self._create_step(test_id, i + 1, step)

        return await self.get_test(test_id)

    async def _create_step(self, test_id: int, order: int, step: Dict[str, str]) -> int:
        """Erstellt einen Design-Step."""
        fields = {
            "parent-id": test_id,
            "step-order": order,
            "name": step.get("name", f"Step {order}"),
            "description": step.get("description", ""),
            "expected": step.get("expected", step.get("expected_result", "")),
        }
        xml = self._build_entity_xml("design-step", fields)
        root = await self._request("POST", "/design-steps", body=xml)
        data = self._parse_entity(root)
        return int(data.get("id", 0))

    async def update_test(self, test_id: int, fields: Dict[str, Any]) -> ALMTest:
        """
        Aktualisiert einen Testfall.

        Args:
            test_id: Test-ID
            fields: Zu aktualisierende Felder {"name": ..., "description": ...}

        Returns:
            Aktualisierter ALMTest
        """
        self._check_configured()

        xml = self._build_entity_xml("test", fields)
        await self._request("PUT", f"/tests/{test_id}", body=xml)

        logger.info(f"ALM: Testfall {test_id} aktualisiert")
        return await self.get_test(test_id)

    # ═══════════════════════════════════════════════════════════════════════
    # Folder Navigation
    # ═══════════════════════════════════════════════════════════════════════

    async def list_folders(self, parent_id: int = 0) -> List[ALMFolder]:
        """
        Listet Test Pool Folders (Ordnerstruktur fuer Testfall-Definitionen).

        Args:
            parent_id: Parent-Folder-ID (0 = Root)

        Returns:
            Liste von ALMFolder
        """
        self._check_configured()

        params = {}
        if parent_id > 0:
            params["query"] = f"{{parent-id[{parent_id}]}}"

        root = await self._request("GET", "/test-folders", params=params)

        folders = []
        entities = root.find("Entities") or root
        for entity in entities.findall("Entity"):
            data = self._parse_entity(entity)
            folders.append(ALMFolder(
                id=int(data.get("id", 0)),
                name=data.get("name", ""),
                parent_id=int(data.get("parent-id", 0)),
            ))

        return folders

    async def _ensure_folder_cache(self) -> None:
        """
        Laedt ALLE Test Pool Folder in einem einzigen API-Call und baut Pfade lokal.

        Performance: Ein API-Call statt N rekursive Calls pro Folder.
        """
        if (self._folder_cache
                and self._folder_cache_time
                and datetime.now() - self._folder_cache_time < timedelta(minutes=5)):
            return

        self._folder_cache.clear()

        try:
            root = await self._request("GET", "/test-folders", params={"page-size": "5000"})

            all_folders: Dict[int, ALMFolder] = {}
            entities = root.find("Entities") or root
            for entity in entities.findall("Entity"):
                data = self._parse_entity(entity)
                fid = int(data.get("id", 0))
                all_folders[fid] = ALMFolder(
                    id=fid,
                    name=data.get("name", ""),
                    parent_id=int(data.get("parent-id", 0)),
                )

            def build_path(fid: int) -> str:
                if fid not in all_folders:
                    return ""
                f = all_folders[fid]
                if f.path:
                    return f.path
                if f.parent_id and f.parent_id > 0 and f.parent_id in all_folders:
                    parent_path = build_path(f.parent_id)
                    f.path = f"{parent_path}/{f.name}" if parent_path else f.name
                else:
                    f.path = f.name
                return f.path

            for fid in all_folders:
                build_path(fid)

            self._folder_cache = all_folders
            self._folder_cache_time = datetime.now()
            logger.info(f"ALM: {len(all_folders)} Test Pool Folder gecached (1 API-Call)")

        except ALMError as e:
            logger.warning(f"ALM: Bulk-Load Test Pool Folders fehlgeschlagen: {e}")

    async def get_folder_path(self, folder_id: int) -> str:
        """
        Gibt den vollen Pfad eines Test Pool Folders zurueck.

        Performance: Laedt beim ersten Aufruf ALLE Folder in einem API-Call
        und berechnet Pfade lokal (statt N rekursive API-Calls).
        """
        await self._ensure_folder_cache()

        if folder_id in self._folder_cache:
            return self._folder_cache[folder_id].path

        # Fallback: Einzelner API-Call wenn nicht im Cache
        try:
            root = await self._request("GET", f"/test-folders/{folder_id}")
            data = self._parse_entity(root)
            return data.get("name", f"Folder-{folder_id}")
        except ALMError:
            return f"Folder-{folder_id}"

    async def create_folder(self, name: str, parent_id: int = 0) -> ALMFolder:
        """
        Erstellt einen neuen Test Pool Folder.

        Args:
            name: Name des neuen Folders
            parent_id: Parent-Folder-ID (0 = Root)

        Returns:
            Der erstellte ALMFolder
        """
        self._check_configured()

        fields = {"name": name}
        if parent_id > 0:
            fields["parent-id"] = str(parent_id)

        xml = self._build_entity_xml("test-folder", fields)
        root = await self._request("POST", "/test-folders", body=xml)
        data = self._parse_entity(root)

        folder = ALMFolder(
            id=int(data.get("id", 0)),
            name=data.get("name", name),
            parent_id=int(data.get("parent-id", parent_id)),
        )

        logger.info(f"ALM: Folder erstellt: ID={folder.id}, Name={folder.name}")
        return folder

    async def _ensure_test_lab_folder_cache(self) -> None:
        """
        Laedt ALLE Test Lab Folder in einem einzigen API-Call und baut Pfade lokal.

        Performance: Ein API-Call statt N rekursive Calls pro Folder.
        """
        # Cache noch gueltig?
        if (self._test_lab_folder_cache
                and self._test_lab_folder_cache_time
                and datetime.now() - self._test_lab_folder_cache_time < timedelta(minutes=5)):
            return

        self._test_lab_folder_cache.clear()

        try:
            # Alle Folder auf einmal laden (kein Filter = alle)
            root = await self._request("GET", "/test-set-folders", params={"page-size": "5000"})

            # Alle Folder parsen
            all_folders: Dict[int, ALMTestSetFolder] = {}
            entities = root.find("Entities") or root
            for entity in entities.findall("Entity"):
                data = self._parse_entity(entity)
                fid = int(data.get("id", 0))
                all_folders[fid] = ALMTestSetFolder(
                    id=fid,
                    name=data.get("name", ""),
                    parent_id=int(data.get("parent-id", 0)),
                )

            # Pfade lokal berechnen (keine weiteren API-Calls)
            def build_path(fid: int) -> str:
                if fid not in all_folders:
                    return ""
                f = all_folders[fid]
                if f.path:
                    return f.path
                if f.parent_id and f.parent_id > 0 and f.parent_id in all_folders:
                    parent_path = build_path(f.parent_id)
                    f.path = f"{parent_path}/{f.name}" if parent_path else f.name
                else:
                    f.path = f.name
                return f.path

            for fid in all_folders:
                build_path(fid)

            self._test_lab_folder_cache = all_folders
            self._test_lab_folder_cache_time = datetime.now()
            logger.info(f"ALM: {len(all_folders)} Test Lab Folder gecached (1 API-Call)")

            # Debug: Alle Folder mit Pfaden loggen
            for fid, f in all_folders.items():
                logger.debug(f"ALM TestLabFolder: ID={fid}, name='{f.name}', parent={f.parent_id}, path='{f.path}'")

        except ALMError as e:
            logger.warning(f"ALM: Bulk-Load Test Lab Folders fehlgeschlagen: {e}")

    async def get_test_lab_folder_path(self, folder_id: int) -> str:
        """
        Gibt den vollen Pfad eines Test Lab Folders zurueck.

        Performance: Laedt beim ersten Aufruf ALLE Folder in einem API-Call
        und berechnet Pfade lokal (statt N rekursive API-Calls).

        Args:
            folder_id: Folder-ID

        Returns:
            Pfad wie "Root/Regression/Sprint-1"
        """
        await self._ensure_test_lab_folder_cache()

        if folder_id in self._test_lab_folder_cache:
            return self._test_lab_folder_cache[folder_id].path

        # Fallback: Einzelner API-Call wenn nicht im Cache
        try:
            root = await self._request("GET", f"/test-set-folders/{folder_id}")
            data = self._parse_entity(root)
            return data.get("name", f"TestLabFolder-{folder_id}")
        except ALMError:
            return f"TestLabFolder-{folder_id}"

    # ═══════════════════════════════════════════════════════════════════════
    # Test Execution (Test Lab)
    # ═══════════════════════════════════════════════════════════════════════

    async def list_test_sets(self, folder_id: Optional[int] = None) -> List[ALMTestSet]:
        """
        Listet Test-Sets aus dem Test Lab.

        Args:
            folder_id: Optional - nur in diesem Folder

        Returns:
            Liste von ALMTestSet
        """
        self._check_configured()

        params = {}
        if folder_id is not None:
            params["query"] = f"{{parent-id[{folder_id}]}}"

        root = await self._request("GET", "/test-sets", params=params)

        test_sets = []
        entities = root.find("Entities") or root
        for entity in entities.findall("Entity"):
            data = self._parse_entity(entity)
            test_sets.append(ALMTestSet(
                id=int(data.get("id", 0)),
                name=data.get("name", ""),
                folder_id=int(data.get("parent-id", 0)),
                status=data.get("status", ""),
                description=data.get("description", ""),
                user_01=data.get("user-01", "Nur Intern"),
            ))

        return test_sets

    async def create_test_set(
        self,
        name: str,
        folder_id: int,
        description: str = "",
        user_01: str = "Nur Intern",
    ) -> ALMTestSet:
        """
        Erstellt ein neues Test-Set im Test Lab.

        Args:
            name: Name des Test-Sets
            folder_id: Test Lab Folder-ID (aus alm_list_test_lab_folders)
            description: Optionale Beschreibung
            user_01: Custom Field "Anzeige" - Erlaubte Werte: "Extern", "Nur Intern", "Sparkasse" (Default: "Nur Intern")

        Returns:
            Erstelltes ALMTestSet

        Raises:
            ALMError: Wenn user_01 einen unzulaessigen Wert hat
        """
        self._check_configured()

        # Validate user_01
        valid_values = ["Extern", "Nur Intern", "Sparkasse"]
        if user_01 not in valid_values:
            raise ALMError(f"Ungueltiger Wert fuer 'Anzeige' (user-01): '{user_01}'. Erlaubte Werte: {valid_values}")

        fields = {
            "name": name,
            "parent-id": str(folder_id),
            "subtype-id": "hp.qc.test-set.default",
            "user-01": user_01,
        }
        if description:
            fields["description"] = description

        xml = self._build_entity_xml("test-set", fields)
        root = await self._request("POST", "/test-sets", body=xml)
        data = self._parse_entity(root)

        test_set = ALMTestSet(
            id=int(data.get("id", 0)),
            name=data.get("name", name),
            folder_id=int(data.get("parent-id", folder_id)),
            status=data.get("status", ""),
            description=description,
            user_01=data.get("user-01", user_01),
        )

        logger.info(f"ALM: Test-Set erstellt: ID={test_set.id}, Name={test_set.name}, Anzeige={user_01}")
        return test_set

    async def create_test_lab_folder(self, name: str, parent_id: int = 0) -> ALMTestSetFolder:
        """
        Erstellt einen neuen Folder im Test Lab.

        Args:
            name: Name des neuen Folders
            parent_id: Parent-Folder-ID im Test Lab (0 = Root)

        Returns:
            Der erstellte ALMTestSetFolder
        """
        self._check_configured()

        fields = {"name": name}
        if parent_id > 0:
            fields["parent-id"] = str(parent_id)

        xml = self._build_entity_xml("test-set-folder", fields)
        root = await self._request("POST", "/test-set-folders", body=xml)
        data = self._parse_entity(root)

        folder = ALMTestSetFolder(
            id=int(data.get("id", 0)),
            name=data.get("name", name),
            parent_id=int(data.get("parent-id", parent_id)),
        )

        # Cache invalidieren
        self._test_lab_folder_cache.clear()
        self._test_lab_folder_cache_time = None

        logger.info(f"ALM: Test Lab Folder erstellt: ID={folder.id}, Name={folder.name}")
        return folder

    async def add_test_to_test_set(self, test_id: int, test_set_id: int) -> ALMTestInstance:
        """
        Fuegt einen Testfall (aus dem Test Pool) einem Test-Set (im Test Lab) hinzu.

        Args:
            test_id: Test-ID aus dem Test Pool
            test_set_id: Test-Set-ID im Test Lab

        Returns:
            Erstellte ALMTestInstance
        """
        self._check_configured()

        fields = {
            "test-id": str(test_id),
            "cycle-id": str(test_set_id),
            "subtype-id": "hp.qc.test-instance.MANUAL",
        }

        xml = self._build_entity_xml("test-instance", fields)
        root = await self._request("POST", "/test-instances", body=xml)
        data = self._parse_entity(root)

        instance = ALMTestInstance(
            id=int(data.get("id", 0)),
            test_id=test_id,
            test_name=data.get("test-config-name", ""),
            test_set_id=test_set_id,
            status=data.get("status", "No Run"),
        )

        logger.info(f"ALM: Test {test_id} zu Test-Set {test_set_id} hinzugefuegt (Instance-ID: {instance.id})")
        return instance

    async def get_test_instances(self, test_set_id: int) -> List[ALMTestInstance]:
        """
        Laedt Test-Instances eines Test-Sets.

        Args:
            test_set_id: Test-Set-ID

        Returns:
            Liste von ALMTestInstance
        """
        self._check_configured()

        params = {"query": f"{{cycle-id[{test_set_id}]}}"}
        root = await self._request("GET", "/test-instances", params=params)

        instances = []
        entities = root.find("Entities") or root
        for entity in entities.findall("Entity"):
            data = self._parse_entity(entity)
            # test-config-name oder name als Fallback
            test_name = data.get("test-config-name") or data.get("name", "")
            # last-modified ist Timestamp, nicht Run-ID
            instances.append(ALMTestInstance(
                id=int(data.get("id", 0) or 0),
                test_id=int(data.get("test-id", 0) or 0),
                test_name=test_name,
                test_set_id=test_set_id,
                status=data.get("status", "No Run"),
                last_run_id=None,
                exec_date=data.get("exec-date"),
                tester=data.get("actual-tester", ""),
            ))

        return instances

    async def get_latest_test_instance(self, test_set_id: int) -> Optional[ALMTestInstance]:
        """
        Laedt die neueste Test-Instance eines Test-Sets (nach exec-date).

        Args:
            test_set_id: Test-Set-ID

        Returns:
            Neueste ALMTestInstance oder None wenn keine gefunden
        """
        instances = await self.get_test_instances(test_set_id)
        if not instances:
            return None

        # Sortiere nach exec-date (neueste zuerst)
        # Falls exec-date fehlt, wird die zuerst gefunden als "neueste" genommen
        instances_with_date = [inst for inst in instances if inst.exec_date]
        instances_without_date = [inst for inst in instances if not inst.exec_date]

        # Sortiere nach exec-date in absteigender Reihenfolge (neueste zuerst)
        instances_with_date.sort(
            key=lambda x: x.exec_date or "",
            reverse=True
        )

        if instances_with_date:
            return instances_with_date[0]
        elif instances_without_date:
            return instances_without_date[0]  # Fallback
        return None

    async def get_test_instance(self, test_instance_id: int) -> Optional[ALMTestInstance]:
        """
        Laedt eine einzelne Test-Instance anhand ihrer ID.

        Args:
            test_instance_id: Test-Instance-ID

        Returns:
            ALMTestInstance oder None wenn nicht gefunden
        """
        self._check_configured()

        try:
            root = await self._request("GET", f"/test-instances/{test_instance_id}")
            data = self._parse_entity(root)
            test_name = data.get("test-config-name") or data.get("name", "")
            return ALMTestInstance(
                id=int(data.get("id", 0) or 0),
                test_id=int(data.get("test-id", 0) or 0),
                test_name=test_name,
                test_set_id=int(data.get("cycle-id", 0) or 0),
                status=data.get("status", "No Run"),
                last_run_id=None,
                exec_date=data.get("exec-date"),
                tester=data.get("actual-tester", ""),
            )
        except Exception as e:
            logger.warning(f"ALM: Test-Instance {test_instance_id} nicht gefunden: {e}")
            return None

    async def create_run(
        self,
        test_instance_id: int,
        status: str,
        comment: str = "",
        cycle_id: Optional[int] = None,
    ) -> ALMRun:
        """
        Erstellt einen Test-Run.

        Args:
            test_instance_id: Test-Instance-ID
            status: Passed | Failed | Not Completed | Blocked
            comment: Kommentar zum Ergebnis
            cycle_id: Test-Set-ID (cycle-id). Optional, wird in einigen QC-Installationen benötigt.

        Returns:
            Erstellter ALMRun
        """
        self._check_configured()

        # Status validieren
        valid_statuses = ["Passed", "Failed", "Not Completed", "Blocked", "N/A"]
        if status not in valid_statuses:
            raise ALMError(f"Ungueltiger Status: {status}. Erlaubt: {valid_statuses}")

        # Wenn cycle_id nicht gegeben, versuche aus Test-Instance zu laden
        if cycle_id is None:
            try:
                inst = await self.get_test_instance(test_instance_id)
                if inst:
                    cycle_id = inst.test_set_id
                    logger.debug(f"ALM: cycle_id aus Test-Instance {test_instance_id} geladen: {cycle_id}")
            except Exception as e:
                logger.warning(f"ALM: Konnte cycle_id aus Test-Instance {test_instance_id} nicht laden: {e}")

        fields = {
            "test-instance": test_instance_id,
            "status": status,
            "name": f"Run {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "subtype-id": "hp.qc.run.MANUAL",
        }
        if comment:
            fields["comments"] = comment
        if cycle_id is not None:
            # ALM API erwartet "test-cycle-id" (nicht "cycle-id")
            fields["test-cycle-id"] = str(cycle_id)

        xml = self._build_entity_xml("run", fields)
        root = await self._request("POST", "/runs", body=xml)
        data = self._parse_entity(root)

        run = ALMRun(
            id=int(data.get("id", 0)),
            test_instance_id=test_instance_id,
            status=status,
            comment=comment,
            execution_date=data.get("execution-date"),
            executor=data.get("owner", self.username),
        )

        logger.info(f"ALM: Test-Run erstellt: ID={run.id}, Status={status}")
        return run

    async def list_test_set_folders(self, parent_id: int = 0) -> List[ALMTestSetFolder]:
        """
        Listet Test Lab Folders (Ordnerstruktur fuer Test-Sets).

        Args:
            parent_id: Parent-Folder-ID (0 = Root)

        Returns:
            Liste von ALMTestSetFolder
        """
        self._check_configured()

        params = {}
        if parent_id > 0:
            params["query"] = f"{{parent-id[{parent_id}]}}"

        root = await self._request("GET", "/test-set-folders", params=params)

        folders = []
        entities = root.find("Entities") or root
        for entity in entities.findall("Entity"):
            data = self._parse_entity(entity)
            folders.append(ALMTestSetFolder(
                id=int(data.get("id", 0)),
                name=data.get("name", ""),
                parent_id=int(data.get("parent-id", 0)),
            ))

        return folders

    async def get_run_history(
        self,
        test_instance_id: int,
        limit: int = 20
    ) -> List[ALMRun]:
        """
        Laedt die Run-Historie einer Test-Instance.

        Args:
            test_instance_id: Test-Instance-ID
            limit: Max. Anzahl Ergebnisse

        Returns:
            Liste von ALMRun (neueste zuerst)
        """
        self._check_configured()

        params = {
            "query": f"{{test-instance[{test_instance_id}]}}",
            "page-size": str(limit),
            "order-by": "{execution-date[DESC]}",
        }

        root = await self._request("GET", "/runs", params=params)

        runs = []
        entities = root.find("Entities") or root
        for entity in entities.findall("Entity"):
            data = self._parse_entity(entity)
            runs.append(ALMRun(
                id=int(data.get("id", 0)),
                test_instance_id=test_instance_id,
                status=data.get("status", ""),
                comment=data.get("comments", ""),
                execution_date=data.get("execution-date"),
                executor=data.get("owner", ""),
            ))

        logger.info(f"ALM: {len(runs)} Runs fuer Test-Instance {test_instance_id} gefunden")
        return runs

    async def search_test_instances(
        self,
        query: str = "",
        test_set_id: Optional[int] = None,
        status: Optional[str] = None,
        tester: Optional[str] = None,
        executed_after: Optional[str] = None,
        executed_before: Optional[str] = None,
        limit: int = 50,
    ) -> List[ALMTestInstance]:
        """
        Sucht Test-Instances im Test Lab mit erweiterten Filtern.

        Args:
            query: Suchbegriff (im Test-Namen)
            test_set_id: Optional - nur in diesem Test-Set
            status: Optional - nur mit diesem Status
            tester: Optional - Ausgefuehrt von diesem Tester
            executed_after: Optional - Ausgefuehrt nach Datum (YYYY-MM-DD)
            executed_before: Optional - Ausgefuehrt vor Datum (YYYY-MM-DD)
            limit: Max. Anzahl Ergebnisse

        Returns:
            Liste von ALMTestInstance
        """
        self._check_configured()

        query_parts = []
        if query:
            # ALM test-instances: Cross-Filter auf test.name fuer Testfall-Namen
            escaped = query.replace("'", "''")
            query_parts.append(f"test.name['*{escaped}*']")

        if test_set_id is not None:
            query_parts.append(f"cycle-id[{test_set_id}]")

        if status:
            query_parts.append(f"status['{status}']")

        if tester:
            escaped_tester = tester.replace("'", "''")
            query_parts.append(f"actual-tester['*{escaped_tester}*']")

        if executed_after:
            query_parts.append(f"exec-date[>='{executed_after}']")

        if executed_before:
            query_parts.append(f"exec-date[<='{executed_before}']")

        params = {"page-size": str(limit)}
        if query_parts:
            params["query"] = "{" + ";".join(query_parts) + "}"

        logger.debug(f"ALM search_test_instances query: {params.get('query', 'none')}")

        root = await self._request("GET", "/test-instances", params=params)

        instances = []
        entities = root.find("Entities") or root
        for entity in entities.findall("Entity"):
            data = self._parse_entity(entity)
            # test-config-name oder name als Fallback
            test_name = data.get("test-config-name") or data.get("name", "")
            # last-modified ist Timestamp, nicht Run-ID - ignorieren
            instances.append(ALMTestInstance(
                id=int(data.get("id", 0) or 0),
                test_id=int(data.get("test-id", 0) or 0),
                test_name=test_name,
                test_set_id=int(data.get("cycle-id", 0) or 0),
                status=data.get("status", "No Run"),
                last_run_id=None,  # Wird nicht zuverlaessig geliefert
                exec_date=data.get("exec-date"),
                tester=data.get("actual-tester", ""),
            ))

        logger.info(f"ALM: {len(instances)} Test-Instances gefunden")
        return instances


# ══════════════════════════════════════════════════════════════════════════════
# Singleton Instance
# ══════════════════════════════════════════════════════════════════════════════

_alm_client: Optional[ALMClient] = None


def get_alm_client() -> ALMClient:
    """Gibt die Singleton ALMClient Instanz zurueck."""
    global _alm_client
    if _alm_client is None:
        _alm_client = ALMClient()
    return _alm_client


def reset_alm_client() -> None:
    """Setzt den ALM Client zurueck (bei Config-Aenderung)."""
    global _alm_client
    if _alm_client is not None:
        # Session invalidieren
        _alm_client._session = None
    _alm_client = None
