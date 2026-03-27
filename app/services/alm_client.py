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
    """Testfall aus dem Test Plan."""
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
            md += f"### Beschreibung\n{self.description}\n\n"

        if self.steps:
            md += "### Test-Schritte\n\n"
            md += "| # | Schritt | Erwartetes Ergebnis |\n"
            md += "|---|---------|--------------------|\n"
            for step in self.steps:
                desc = step.description.replace("\n", " ").replace("|", "\\|")
                expected = step.expected_result.replace("\n", " ").replace("|", "\\|")
                md += f"| {step.step_order} | {desc} | {expected} |\n"

        return md


@dataclass
class ALMFolder:
    """Test-Plan Folder."""
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
        self.username = settings.alm.username
        self.password = settings.alm.password
        self.domain = settings.alm.domain
        self.project = settings.alm.project
        self._session: Optional[ALMSession] = None
        self._folder_cache: Dict[int, ALMFolder] = {}
        self._folder_cache_time: Optional[datetime] = None

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

        # Versuch 1: JSON-Auth
        try:
            resp = await client.post(
                auth_url,
                headers=self._auth_headers(),
                json=self._auth_body(),
            )
            resp.raise_for_status()
            lwsso_cookie = self._extract_lwsso_cookie(resp)
        except httpx.HTTPStatusError as e:
            logger.warning(f"ALM JSON-Auth fehlgeschlagen ({e.response.status_code}), versuche Basic Auth...")
        except httpx.RequestError as e:
            raise ALMError(f"ALM Verbindungsfehler: {e}") from e

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

        logger.info(f"ALM: Session erstellt fuer User {session_cookies['alm_user'] or self.username}")
        return self._session

    async def ensure_session(self) -> ALMSession:
        """Stellt sicher dass eine gueltige Session existiert."""
        if not self._session or not self._session.is_valid():
            await self.authenticate()
        return self._session

    async def logout(self) -> None:
        """Beendet ALM Session."""
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
        logger.info("ALM: Session beendet")

    async def test_connection(self) -> Dict[str, Any]:
        """
        Testet die ALM-Verbindung.

        Returns:
            {"success": True, "user": "...", "domain": "...", "project": "..."}
            oder {"success": False, "error": "..."}
        """
        try:
            session = await self.authenticate()
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
            raise ALMError(f"ALM Response XML Parse Error: {e}") from e

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
        limit: int = 50,
    ) -> List[ALMTest]:
        """
        Sucht Testfaelle.

        Args:
            query: Suchbegriff (wird in Name gesucht)
            folder_id: Optional - nur in diesem Folder suchen
            limit: Max. Anzahl Ergebnisse

        Returns:
            Liste von ALMTest-Objekten
        """
        self._check_configured()

        # ALM Query-Syntax bauen
        # Syntax: {field[operator'value']} oder {field[value]} fuer numerisch
        # Contains: name[*pattern*] (Wildcard-Syntax, nicht ~'pattern')
        query_parts = []
        if query:
            # ALM verwendet *pattern* fuer Contains-Suche (Wildcards)
            # Single quotes werden NICHT verwendet bei Wildcard-Suche
            # Escape: * -> \*, ' -> ''
            escaped = query.replace("*", "\\*").replace("'", "''")
            query_parts.append(f"name[*{escaped}*]")
        if folder_id is not None:
            query_parts.append(f"parent-id[{folder_id}]")

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
        params = {"query": f"{{parent-id[{test_id}]}}"}
        root = await self._request("GET", "/design-steps", params=params)

        steps = []
        entities = root.find("Entities") or root
        for entity in entities.findall("Entity"):
            data = self._parse_entity(entity)
            steps.append(ALMTestStep(
                id=int(data.get("id", 0)),
                step_order=int(data.get("step-order", 0)),
                name=data.get("name", ""),
                description=data.get("description", ""),
                expected_result=data.get("expected", ""),
            ))

        # Nach step_order sortieren
        steps.sort(key=lambda s: s.step_order)
        return steps

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
        Listet Test-Plan-Folders.

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

    async def get_folder_path(self, folder_id: int) -> str:
        """
        Gibt den vollen Pfad eines Folders zurueck.

        Args:
            folder_id: Folder-ID

        Returns:
            Pfad wie "Root/Module/SubModule"
        """
        # Cache pruefen (5 Minuten TTL)
        if self._folder_cache_time and datetime.now() - self._folder_cache_time > timedelta(minutes=5):
            self._folder_cache.clear()
            self._folder_cache_time = None

        if folder_id in self._folder_cache:
            return self._folder_cache[folder_id].path

        # Folder laden
        try:
            root = await self._request("GET", f"/test-folders/{folder_id}")
            data = self._parse_entity(root)

            folder = ALMFolder(
                id=folder_id,
                name=data.get("name", ""),
                parent_id=int(data.get("parent-id", 0)),
            )

            # Pfad rekursiv bauen
            if folder.parent_id and folder.parent_id > 0:
                parent_path = await self.get_folder_path(folder.parent_id)
                folder.path = f"{parent_path}/{folder.name}"
            else:
                folder.path = folder.name

            self._folder_cache[folder_id] = folder
            self._folder_cache_time = datetime.now()

            return folder.path
        except ALMError:
            return f"Folder-{folder_id}"

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
            ))

        return test_sets

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
            instances.append(ALMTestInstance(
                id=int(data.get("id", 0)),
                test_id=int(data.get("test-id", 0)),
                test_name=data.get("test-config-name", ""),
                test_set_id=test_set_id,
                status=data.get("status", "No Run"),
                last_run_id=int(data.get("last-modified", 0)) if data.get("last-modified") else None,
                exec_date=data.get("exec-date"),
                tester=data.get("actual-tester", ""),
            ))

        return instances

    async def create_run(
        self,
        test_instance_id: int,
        status: str,
        comment: str = "",
    ) -> ALMRun:
        """
        Erstellt einen Test-Run.

        Args:
            test_instance_id: Test-Instance-ID
            status: Passed | Failed | Not Completed | Blocked
            comment: Kommentar zum Ergebnis

        Returns:
            Erstellter ALMRun
        """
        self._check_configured()

        # Status validieren
        valid_statuses = ["Passed", "Failed", "Not Completed", "Blocked", "N/A"]
        if status not in valid_statuses:
            raise ALMError(f"Ungueltiger Status: {status}. Erlaubt: {valid_statuses}")

        fields = {
            "test-instance": test_instance_id,
            "status": status,
            "name": f"Run {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "subtype-id": "hp.qc.run.MANUAL",
        }
        if comment:
            fields["comments"] = comment

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
        limit: int = 50,
    ) -> List[ALMTestInstance]:
        """
        Sucht Test-Instances im Test Lab.

        Args:
            query: Suchbegriff (im Test-Namen)
            test_set_id: Optional - nur in diesem Test-Set
            status: Optional - nur mit diesem Status
            limit: Max. Anzahl Ergebnisse

        Returns:
            Liste von ALMTestInstance
        """
        self._check_configured()

        query_parts = []
        if query:
            # ALM verwendet *pattern* fuer Contains-Suche (Wildcards)
            escaped = query.replace("*", "\\*").replace("'", "''")
            query_parts.append(f"test-config-name[*{escaped}*]")
        if test_set_id is not None:
            query_parts.append(f"cycle-id[{test_set_id}]")
        if status:
            query_parts.append(f"status[{status}]")

        params = {"page-size": str(limit)}
        if query_parts:
            params["query"] = "{" + ";".join(query_parts) + "}"

        logger.debug(f"ALM search_test_instances query: {params.get('query', 'none')}")

        root = await self._request("GET", "/test-instances", params=params)

        instances = []
        entities = root.find("Entities") or root
        for entity in entities.findall("Entity"):
            data = self._parse_entity(entity)
            instances.append(ALMTestInstance(
                id=int(data.get("id", 0)),
                test_id=int(data.get("test-id", 0)),
                test_name=data.get("test-config-name", ""),
                test_set_id=int(data.get("cycle-id", 0)),
                status=data.get("status", "No Run"),
                last_run_id=int(data.get("last-modified", 0)) if data.get("last-modified") else None,
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
