import base64
import re
from typing import Dict, List, Optional
from urllib.parse import urlparse, parse_qs

import httpx
from lxml import etree

from app.core.config import settings
from app.core.exceptions import ConfluenceError


class ConfluenceClient:
    def __init__(self):
        self.base_url = settings.confluence.base_url.rstrip("/")
        self.username = settings.confluence.username
        self.api_token = settings.confluence.api_token
        self.password = settings.confluence.password

    def _get_secret(self) -> str:
        """API-Token bevorzugen (Cloud), sonst Passwort (Server/DC)."""
        return self.api_token or self.password

    def _headers(self) -> Dict:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        secret = self._get_secret()
        if self.username and secret:
            creds = base64.b64encode(f"{self.username}:{secret}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"
        return headers

    def _api_url(self, path: str) -> str:
        return f"{self.base_url}/wiki/rest/api{path}"

    def _check_configured(self):
        if not self.base_url:
            raise ConfluenceError("Confluence ist nicht konfiguriert (base_url fehlt in config.yaml)")

    def _build_cql(
        self,
        query: str,
        space_key: Optional[str],
        content_type: str,
        ancestor_id: Optional[str],
        labels: Optional[List[str]],
    ) -> str:
        parts = [f'text~"{query}"', f'type="{content_type}"']
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
        cql = self._build_cql(query, space_key, content_type, ancestor_id, labels)
        params = {
            "cql": cql,
            "limit": limit,
            "expand": "space,excerpt,version",
        }
        verify_ssl = settings.confluence.verify_ssl
        print(f"[confluence] verify_ssl={verify_ssl}, base_url={self.base_url}")
        async with httpx.AsyncClient(timeout=30, verify=verify_ssl) as client:
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
        verify_ssl = settings.confluence.verify_ssl
        print(f"[confluence] get_page verify_ssl={verify_ssl}")
        async with httpx.AsyncClient(timeout=30, verify=verify_ssl) as client:
            try:
                resp = await client.get(
                    self._api_url(f"/content/{page_id}"),
                    headers=self._headers(),
                    params={"expand": "body.storage,version,space"},
                )
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as e:
                raise ConfluenceError(f"Seite nicht gefunden oder Fehler {e.response.status_code}") from e
            except httpx.RequestError as e:
                raise ConfluenceError(f"Verbindungsfehler: {e}") from e

        storage = data.get("body", {}).get("storage", {}).get("value", "")
        base = data.get("_links", {}).get("base", self.base_url)
        web_ui = data.get("_links", {}).get("webui", "")

        return {
            "id": data.get("id", ""),
            "title": data.get("title", ""),
            "url": f"{base}{web_ui}",
            "space": data.get("space", {}).get("name", ""),
            "content": self.extract_text_from_storage(storage),
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

    def extract_text_from_storage(self, storage_body: str) -> str:
        """Convert Confluence storage format (XML/HTML) to readable plain text."""
        if not storage_body:
            return ""
        try:
            root = etree.fromstring(f"<root>{storage_body}</root>")
        except etree.XMLSyntaxError:
            # Fallback: strip HTML tags with regex
            text = re.sub(r"<[^>]+>", " ", storage_body)
            return re.sub(r"\s{2,}", " ", text).strip()

        lines = []
        self._traverse_node(root, lines, indent=0)
        return "\n".join(lines).strip()

    def _traverse_node(self, node, lines: List[str], indent: int):
        tag = node.tag.split("}")[-1] if "}" in node.tag else node.tag

        # Headings
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            prefix = "#" * int(tag[1])
            text = "".join(node.itertext()).strip()
            if text:
                lines.append(f"\n{prefix} {text}")
        # Code blocks
        elif tag == "code":
            code_text = "".join(node.itertext()).strip()
            if code_text:
                lang = node.get("language", "")
                lines.append(f"```{lang}\n{code_text}\n```")
            return  # Don't recurse
        # Structured macros (Confluence-specific)
        elif tag == "structured-macro":
            macro_name = node.get("ac:name", node.get("name", ""))
            if macro_name == "code":
                body = node.find(".//{http://atlassian.com/content}plain-text-body")
                if body is None:
                    body = node.find(".//plain-text-body")
                code = body.text if body is not None else ""
                lines.append(f"```\n{code}\n```")
            return
        # List items
        elif tag in ("li", "dt", "dd"):
            text = "".join(node.itertext()).strip()
            if text:
                lines.append(f"{'  ' * indent}- {text}")
            return
        # Paragraphs and divs
        elif tag in ("p", "div", "td", "th"):
            text = "".join(node.itertext()).strip()
            if text:
                lines.append(text)
            return
        # Table rows
        elif tag == "tr":
            cells = ["".join(cell.itertext()).strip() for cell in node]
            if any(cells):
                lines.append(" | ".join(cells))
            return
        # Root or container tags: recurse
        else:
            if node.text and node.text.strip():
                lines.append(node.text.strip())

        for child in node:
            self._traverse_node(child, lines, indent + 1)
            if child.tail and child.tail.strip():
                lines.append(child.tail.strip())
