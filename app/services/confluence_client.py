import base64
import re
from typing import Dict, List, Optional
from urllib.parse import urlparse, parse_qs

import httpx

from app.core.config import settings
from app.core.exceptions import ConfluenceError

# Shared HTTP Client für Connection-Pooling
_http_client: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    """Gibt den shared HTTP-Client für Confluence zurück."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=30,
            verify=settings.confluence.verify_ssl,
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
                keepalive_expiry=30.0
            )
        )
    return _http_client


async def close_confluence_client():
    """Schließt den HTTP-Client (für Shutdown)."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


class ConfluenceClient:
    def __init__(self):
        self.base_url = settings.confluence.base_url.rstrip("/")
        self.username = settings.confluence.username
        self.api_token = settings.confluence.api_token
        self.password = settings.confluence.password
        self.api_path = settings.confluence.api_path
        self._detected_api_path: Optional[str] = None

    def _get_secret(self) -> str:
        """API-Token bevorzugen (Cloud), sonst Passwort (Server/DC)."""
        return self.api_token or self.password

    def _headers(self) -> Dict:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}

        # Wenn api_token gesetzt aber kein username → Bearer Auth (PAT)
        if self.api_token and not self.username:
            headers["Authorization"] = f"Bearer {self.api_token}"
        else:
            # Basic Auth: Username + API-Token/Password
            secret = self._get_secret()
            if self.username and secret:
                creds = base64.b64encode(f"{self.username}:{secret}".encode()).decode()
                headers["Authorization"] = f"Basic {creds}"
        return headers

    def _api_url(self, path: str) -> str:
        """
        Baut die API-URL.

        Unterstützte Formate:
        - Cloud: base_url/wiki/rest/api/...
        - Server: base_url/rest/api/... oder base_url/confluence/rest/api/...
        """
        if self.api_path:
            # Explizit konfiguriert
            return f"{self.base_url}/{self.api_path}/rest/api{path}"
        elif self._detected_api_path is not None:
            # Bereits erkannt
            if self._detected_api_path:
                return f"{self.base_url}/{self._detected_api_path}/rest/api{path}"
            else:
                return f"{self.base_url}/rest/api{path}"
        else:
            # Standard: ohne Prefix (Server/DC)
            return f"{self.base_url}/rest/api{path}"

    async def _detect_api_path(self) -> str:
        """Erkennt den korrekten API-Pfad durch Testen verschiedener Endpunkte."""
        if self._detected_api_path is not None:
            return self._detected_api_path

        client = _get_http_client()

        # Verschiedene Pfade testen
        paths_to_try = [
            "",           # Server/DC ohne Context: /rest/api
            "wiki",       # Cloud: /wiki/rest/api
            "confluence", # Manche Server: /confluence/rest/api
        ]

        for api_path in paths_to_try:
            try:
                if api_path:
                    url = f"{self.base_url}/{api_path}/rest/api/space?limit=1"
                else:
                    url = f"{self.base_url}/rest/api/space?limit=1"

                resp = await client.get(url, headers=self._headers(), timeout=10)
                if resp.status_code == 200:
                    self._detected_api_path = api_path
                    return api_path
            except Exception:
                continue

        # Fallback: kein Prefix
        self._detected_api_path = ""
        return ""

    def _check_configured(self):
        if not self.base_url:
            raise ConfluenceError("Confluence ist nicht konfiguriert (base_url fehlt in config.yaml)")

    async def _ensure_api_path(self):
        """Stellt sicher, dass der API-Pfad erkannt wurde."""
        if not self.api_path and self._detected_api_path is None:
            await self._detect_api_path()

    def _build_cql(
        self,
        query: str,
        space_key: Optional[str],
        content_type: str,
        ancestor_id: Optional[str],
        labels: Optional[List[str]],
    ) -> str:
        """
        Baut eine CQL-Query für die Confluence-Suche.

        Verbesserungen:
        - Sucht in Titel UND Text (OR-Verknüpfung)
        - Unterstützt mehrere Suchbegriffe (AND-Verknüpfung zwischen Begriffen)
        - Wildcards für Teilwort-Suche
        """
        # Query bereinigen und in Wörter aufteilen
        query = query.strip()

        # Einfache Variante: Suche in text UND title
        # Confluence CQL: text ~ "word" sucht nach enthaltenen Wörtern
        # title ~ "word" sucht im Titel
        search_clause = f'(text~"{query}" OR title~"{query}")'

        parts = [search_clause, f'type="{content_type}"']

        if space_key:
            parts.append(f'space="{space_key}"')
        if ancestor_id:
            parts.append(f"ancestor={ancestor_id}")
        if labels:
            for label in labels:
                parts.append(f'label="{label}"')
        return " AND ".join(parts)

    async def search(
        self,
        query: str,
        space_key: Optional[str] = None,
        content_type: str = "page",
        limit: int = 20,
        ancestor_id: Optional[str] = None,
        labels: Optional[List[str]] = None,
    ) -> List[Dict]:
        self._check_configured()
        await self._ensure_api_path()
        cql = self._build_cql(query, space_key, content_type, ancestor_id, labels)
        params = {
            "cql": cql,
            "limit": limit,
            "expand": "space,excerpt,version",
        }
        client = _get_http_client()
        try:
            resp = await client.get(
                self._api_url("/content/search"),
                headers=self._headers(),
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            raise ConfluenceError(f"Confluence API Fehler {e.response.status_code}: {e.response.text}") from e
        except httpx.RequestError as e:
            raise ConfluenceError(f"Confluence Verbindungsfehler: {e}") from e

        results = []
        for item in data.get("results", []):
            space_name = item.get("space", {}).get("name", "")
            base = item.get("_links", {}).get("base", self.base_url)
            web_ui = item.get("_links", {}).get("webui", "")
            results.append({
                "id": item.get("id", ""),
                "title": item.get("title", ""),
                "url": f"{base}{web_ui}" if web_ui else "",
                "space": space_name,
                "excerpt": item.get("excerpt", ""),
                "last_modified": item.get("version", {}).get("when", ""),
            })
        return results

    async def get_page_by_id(self, page_id: str) -> Dict:
        self._check_configured()
        await self._ensure_api_path()
        client = _get_http_client()
        try:
            # export_view liefert sauberes HTML ohne Confluence-Makros
            resp = await client.get(
                self._api_url(f"/content/{page_id}"),
                headers=self._headers(),
                params={"expand": "body.export_view,version,space"},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            raise ConfluenceError(f"Seite nicht gefunden oder Fehler {e.response.status_code}") from e
        except httpx.RequestError as e:
            raise ConfluenceError(f"Verbindungsfehler: {e}") from e

        html_content = data.get("body", {}).get("export_view", {}).get("value", "")
        base = data.get("_links", {}).get("base", self.base_url)
        web_ui = data.get("_links", {}).get("webui", "")

        return {
            "id": data.get("id", ""),
            "title": data.get("title", ""),
            "url": f"{base}{web_ui}",
            "space": data.get("space", {}).get("name", ""),
            "content": self.extract_text_from_html(html_content),
        }

    async def get_page_by_url(self, url: str) -> Dict:
        """Fetch a page given its full Confluence URL (extracts page ID from URL or fetches by title)."""
        self._check_configured()

        # Try to extract page ID directly from URL (/pages/123456)
        id_match = re.search(r"/pages/(\d+)", url)
        if id_match:
            return await self.get_page_by_id(id_match.group(1))

        # Try pageId query param
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        if "pageId" in params:
            return await self.get_page_by_id(params["pageId"][0])

        raise ConfluenceError(f"Keine Seiten-ID in der URL gefunden: {url}. Bitte die Seiten-ID direkt angeben.")

    async def get_child_pages(self, page_id: str, limit: int = 50) -> List[Dict]:
        """
        Holt die direkten Kind-Seiten einer Confluence-Seite.

        Verwendet /content/{id}/child/page Endpunkt.
        Für rekursive Traversierung: Aufrufer ruft wiederholt auf.

        Args:
            page_id: Confluence Seiten-ID
            limit: Max. Anzahl Kind-Seiten

        Returns:
            Liste von Dicts mit id, title, url, space_key
        """
        self._check_configured()
        await self._ensure_api_path()
        client = _get_http_client()

        try:
            resp = await client.get(
                self._api_url(f"/content/{page_id}/child/page"),
                headers=self._headers(),
                params={"limit": limit, "expand": "version,space"},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            raise ConfluenceError(f"Fehler beim Abrufen der Kind-Seiten von {page_id}: {e.response.status_code}") from e
        except httpx.RequestError as e:
            raise ConfluenceError(f"Verbindungsfehler: {e}") from e

        results = []
        for item in data.get("results", []):
            base = item.get("_links", {}).get("base", self.base_url)
            web_ui = item.get("_links", {}).get("webui", "")
            results.append({
                "id": item.get("id", ""),
                "title": item.get("title", ""),
                "url": f"{base}{web_ui}" if web_ui else "",
                "space_key": item.get("space", {}).get("key", ""),
            })
        return results

    def extract_text_from_html(self, html_content: str) -> str:
        """
        Konvertiert HTML (export_view oder storage) in lesbaren Text.

        export_view liefert sauberes HTML ohne Confluence-Makros,
        daher ist diese Methode einfacher als der alte XML-Parser.
        """
        if not html_content:
            return ""

        # Versuche mit lxml zu parsen (robuster)
        try:
            from lxml import html as lxml_html
            doc = lxml_html.fromstring(f"<div>{html_content}</div>")
        except Exception:
            # Fallback: Regex-basierte Extraktion
            return self._extract_text_regex(html_content)

        lines = []
        self._traverse_html_node(doc, lines, indent=0)
        return "\n".join(lines).strip()

    def _extract_text_regex(self, html_content: str) -> str:
        """Fallback: Extrahiert Text aus HTML mit Regex."""
        # Code-Blöcke erhalten
        code_blocks = []
        def save_code(match):
            code_blocks.append(f"```\n{match.group(1)}\n```")
            return f"__CODE_BLOCK_{len(code_blocks) - 1}__"

        text = re.sub(r'<pre[^>]*>(.*?)</pre>', save_code, html_content, flags=re.DOTALL)
        text = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', text, flags=re.DOTALL)

        # Block-Elemente → Zeilenumbrüche
        text = re.sub(r'</(p|div|tr|li|h[1-6])>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<(br|hr)[^>]*/?>', '\n', text, flags=re.IGNORECASE)

        # Alle anderen Tags entfernen
        text = re.sub(r'<[^>]+>', ' ', text)

        # Code-Blöcke wiederherstellen
        for i, code in enumerate(code_blocks):
            text = text.replace(f"__CODE_BLOCK_{i}__", code)

        # HTML-Entities dekodieren
        text = re.sub(r'&nbsp;', ' ', text)
        text = re.sub(r'&lt;', '<', text)
        text = re.sub(r'&gt;', '>', text)
        text = re.sub(r'&amp;', '&', text)
        text = re.sub(r'&quot;', '"', text)
        text = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), text)

        # Mehrfache Leerzeichen/Zeilenumbrüche bereinigen
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n\s*\n', '\n\n', text)

        return text.strip()

    def _traverse_html_node(self, node, lines: List[str], indent: int):
        """Traversiert HTML-Knoten und extrahiert strukturierten Text."""
        tag = node.tag if isinstance(node.tag, str) else ""

        # Headings
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            prefix = "#" * int(tag[1])
            text = "".join(node.itertext()).strip()
            if text:
                lines.append(f"\n{prefix} {text}")
            return

        # Code-Blöcke (pre, code)
        if tag == "pre":
            code_text = "".join(node.itertext()).strip()
            if code_text:
                # Versuche Sprache aus class zu extrahieren
                lang = ""
                cls = node.get("class", "")
                lang_match = re.search(r'language-(\w+)', cls)
                if lang_match:
                    lang = lang_match.group(1)
                lines.append(f"```{lang}\n{code_text}\n```")
            return

        if tag == "code":
            # Inline-Code
            code_text = "".join(node.itertext()).strip()
            if code_text and "\n" not in code_text:
                lines.append(f"`{code_text}`")
            elif code_text:
                lines.append(f"```\n{code_text}\n```")
            return

        # Listen
        if tag in ("ul", "ol"):
            for child in node:
                self._traverse_html_node(child, lines, indent)
            return

        if tag == "li":
            # Direkten Text des List-Items
            direct_text = (node.text or "").strip()
            bullet = f"{'  ' * indent}- "

            if direct_text:
                lines.append(f"{bullet}{direct_text}")
            else:
                # Prüfe ob erstes Kind ein Text-Element ist
                first_child = node[0] if len(node) > 0 else None
                if first_child is not None and first_child.tag not in ("ul", "ol"):
                    lines.append(bullet.rstrip())

            # In Kinder rekursieren
            for child in node:
                if child.tag in ("ul", "ol"):
                    # Verschachtelte Liste
                    self._traverse_html_node(child, lines, indent + 1)
                else:
                    self._traverse_html_node(child, lines, indent)
                if child.tail and child.tail.strip():
                    lines.append(f"{'  ' * indent}  {child.tail.strip()}")
            return

        # Tabellen
        if tag == "table":
            for child in node:
                self._traverse_html_node(child, lines, indent)
            return

        if tag in ("thead", "tbody"):
            for child in node:
                self._traverse_html_node(child, lines, indent)
            return

        if tag == "tr":
            cells = []
            for cell in node:
                cell_text = "".join(cell.itertext()).strip()
                cells.append(cell_text)
            if any(cells):
                lines.append(" | ".join(cells))
            return

        # Paragraphen und Divs
        if tag in ("p", "div", "span"):
            text = "".join(node.itertext()).strip()
            if text:
                lines.append(text)
            return

        # Blockquote / Info-Boxen
        if tag == "blockquote":
            text = "".join(node.itertext()).strip()
            if text:
                # Jede Zeile mit > prefixen
                for line in text.split("\n"):
                    lines.append(f"> {line}")
            return

        # Links - Text extrahieren
        if tag == "a":
            text = "".join(node.itertext()).strip()
            href = node.get("href", "")
            if text and href:
                lines.append(f"[{text}]({href})")
            elif text:
                lines.append(text)
            return

        # Bilder - Alt-Text
        if tag == "img":
            alt = node.get("alt", "")
            if alt:
                lines.append(f"[Bild: {alt}]")
            return

        # Standard: Text und Rekursion
        if node.text and node.text.strip():
            lines.append(node.text.strip())

        for child in node:
            self._traverse_html_node(child, lines, indent)
            if child.tail and child.tail.strip():
                lines.append(child.tail.strip())

    async def get_page_attachments(
        self,
        page_id: str,
        media_type: Optional[str] = None
    ) -> List[Dict]:
        """
        Holt alle Attachments einer Seite.

        Args:
            page_id: Confluence Seiten-ID
            media_type: Optional Filter (z.B. "application/pdf")

        Returns:
            Liste von Attachment-Metadaten
        """
        self._check_configured()
        await self._ensure_api_path()
        client = _get_http_client()

        try:
            resp = await client.get(
                self._api_url(f"/content/{page_id}/child/attachment"),
                headers=self._headers(),
                params={"expand": "version,metadata.mediaType", "limit": 100},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            raise ConfluenceError(f"Attachments nicht abrufbar: {e.response.status_code}") from e
        except httpx.RequestError as e:
            raise ConfluenceError(f"Verbindungsfehler: {e}") from e

        attachments = []
        for item in data.get("results", []):
            item_type = item.get("metadata", {}).get("mediaType", "")

            # Filter nach Media-Type wenn angegeben
            if media_type and media_type not in item_type:
                continue

            download_link = item.get("_links", {}).get("download", "")
            if download_link and not download_link.startswith("http"):
                # Relative URL → absolut machen
                download_link = f"{self.base_url}{download_link}"

            attachments.append({
                "id": item.get("id", ""),
                "title": item.get("title", ""),
                "media_type": item_type,
                "size_bytes": item.get("extensions", {}).get("fileSize", 0),
                "download_url": download_link,
                "version": item.get("version", {}).get("number", 1),
            })

        return attachments

    async def get_pdf_attachments(self, page_id: str) -> List[Dict]:
        """Holt nur PDF-Attachments einer Seite."""
        return await self.get_page_attachments(page_id, media_type="application/pdf")

    async def download_attachment(self, download_url: str) -> bytes:
        """
        Lädt ein Attachment herunter.

        Args:
            download_url: Volle Download-URL des Attachments

        Returns:
            Binärer Inhalt des Attachments
        """
        client = _get_http_client()

        try:
            resp = await client.get(
                download_url,
                headers=self._headers(),
                timeout=60,  # PDFs können groß sein
            )
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPStatusError as e:
            raise ConfluenceError(f"Download fehlgeschlagen: {e.response.status_code}") from e
        except httpx.RequestError as e:
            raise ConfluenceError(f"Verbindungsfehler beim Download: {e}") from e


# Singleton
_confluence_client: Optional[ConfluenceClient] = None


def reset_confluence_client():
    """Setzt den Confluence-Client zurück (nach Settings-Änderung aufrufen)."""
    global _confluence_client
    _confluence_client = None


def get_confluence_client() -> ConfluenceClient:
    """Gibt den Confluence-Client zurück (Singleton)."""
    global _confluence_client
    if _confluence_client is None:
        _confluence_client = ConfluenceClient()
    return _confluence_client
