"""
Jira REST API Client.

Unterstützt JQL-Suche und Issue-Abruf über Jira REST API v2.
Auth: api_token ohne username = Bearer Auth, mit username = Basic Auth.
"""

import base64
from typing import Dict, List, Optional

import httpx

from app.core.config import settings
from app.core.exceptions import JiraError

# Shared HTTP Client für Connection-Pooling
_http_client: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    """Gibt den shared HTTP-Client für Jira zurück."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=30,
            verify=settings.jira.verify_ssl,
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
                keepalive_expiry=30.0
            )
        )
    return _http_client


async def close_jira_client():
    """Schließt den HTTP-Client (für Shutdown)."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


class JiraClient:
    def __init__(self):
        self.base_url = settings.jira.base_url.rstrip("/")
        self.username = settings.jira.username
        self.api_token = settings.jira.api_token
        self.password = settings.jira.password
        self.default_project = settings.jira.default_project

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
        return f"{self.base_url}/rest/api/2{path}"

    def _check_configured(self):
        if not self.base_url:
            raise JiraError("Jira ist nicht konfiguriert (base_url fehlt in config.yaml)")

    async def search(
        self,
        jql: str,
        max_results: int = 20,
    ) -> List[Dict]:
        """
        JQL-Suche nach Issues.

        Args:
            jql: JQL-Query (z.B. 'project=PROJ AND status="Open"')
            max_results: Maximale Anzahl Ergebnisse

        Returns:
            Liste von Issue-Dicts mit key, summary, status, assignee, priority, updated
        """
        self._check_configured()

        payload = {
            "jql": jql,
            "maxResults": max_results,
            "fields": ["summary", "status", "assignee", "priority", "updated", "issuetype", "parent", "subtasks"],
        }

        client = _get_http_client()
        try:
            resp = await client.post(
                self._api_url("/search"),
                headers=self._headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            raise JiraError(f"Jira API Fehler {e.response.status_code}: {e.response.text}") from e
        except httpx.RequestError as e:
            raise JiraError(f"Jira Verbindungsfehler: {e}") from e

        results = []
        for issue in data.get("issues", []):
            fields = issue.get("fields", {})
            # Parent-Key extrahieren (falls Subtask)
            parent_data = fields.get("parent")
            parent_key = parent_data.get("key", "") if parent_data else ""
            # Subtask-Anzahl
            subtasks = fields.get("subtasks", [])
            subtask_count = len(subtasks)
            results.append({
                "key": issue.get("key", ""),
                "summary": fields.get("summary", ""),
                "status": (fields.get("status") or {}).get("name", ""),
                "assignee": (fields.get("assignee") or {}).get("displayName", "Nicht zugewiesen"),
                "priority": (fields.get("priority") or {}).get("name", ""),
                "type": (fields.get("issuetype") or {}).get("name", ""),
                "updated": fields.get("updated", ""),
                "parent_key": parent_key,
                "subtask_count": subtask_count,
                "url": f"{self.base_url}/browse/{issue.get('key', '')}",
            })
        return results

    async def get_issue(self, issue_key: str) -> Dict:
        """
        Holt ein einzelnes Issue mit Details und Kommentaren.

        Args:
            issue_key: Issue-Schlüssel (z.B. "PROJ-123")

        Returns:
            Dict mit key, summary, description, status, comments, etc.
        """
        self._check_configured()

        client = _get_http_client()
        try:
            resp = await client.get(
                self._api_url(f"/issue/{issue_key}"),
                headers=self._headers(),
                params={"expand": "renderedFields"},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            raise JiraError(f"Issue '{issue_key}' nicht gefunden oder Fehler {e.response.status_code}") from e
        except httpx.RequestError as e:
            raise JiraError(f"Jira Verbindungsfehler: {e}") from e

        fields = data.get("fields", {})
        rendered = data.get("renderedFields", {})

        # Beschreibung: gerendert bevorzugen, sonst raw
        description = rendered.get("description") or fields.get("description") or ""
        # HTML-Tags entfernen für bessere LLM-Lesbarkeit
        import re
        description = re.sub(r"<[^>]+>", " ", description)
        description = re.sub(r"\s{2,}", " ", description).strip()

        # Kommentare extrahieren
        comments = []
        comment_data = fields.get("comment", {}).get("comments", [])
        for c in comment_data[-10:]:  # Letzte 10 Kommentare
            author = (c.get("author") or {}).get("displayName", "Unbekannt")
            body = c.get("body", "")
            if isinstance(body, str):
                body = re.sub(r"<[^>]+>", " ", body)
                body = re.sub(r"\s{2,}", " ", body).strip()
            created = c.get("created", "")
            comments.append({
                "author": author,
                "created": created,
                "body": body,
            })

        # Subtasks extrahieren (vollständiger Key, nicht nur ID)
        subtasks = []
        for st in fields.get("subtasks", []):
            st_key = st.get("key", "")
            st_fields = st.get("fields", {})
            subtasks.append({
                "key": st_key,
                "summary": st_fields.get("summary", ""),
                "status": (st_fields.get("status") or {}).get("name", ""),
                "type": (st_fields.get("issuetype") or {}).get("name", "Sub-task"),
                "url": f"{self.base_url}/browse/{st_key}",
            })

        # Parent-Issue ermitteln (falls dies ein Subtask ist)
        parent = None
        parent_data = fields.get("parent")
        if parent_data:
            parent_key = parent_data.get("key", "")
            parent_fields = parent_data.get("fields", {})
            parent = {
                "key": parent_key,
                "summary": parent_fields.get("summary", ""),
                "status": (parent_fields.get("status") or {}).get("name", ""),
                "url": f"{self.base_url}/browse/{parent_key}",
            }

        return {
            "key": data.get("key", ""),
            "summary": fields.get("summary", ""),
            "description": description,
            "status": (fields.get("status") or {}).get("name", ""),
            "assignee": (fields.get("assignee") or {}).get("displayName", "Nicht zugewiesen"),
            "reporter": (fields.get("reporter") or {}).get("displayName", ""),
            "priority": (fields.get("priority") or {}).get("name", ""),
            "type": (fields.get("issuetype") or {}).get("name", ""),
            "created": fields.get("created", ""),
            "updated": fields.get("updated", ""),
            "labels": fields.get("labels", []),
            "components": [c.get("name", "") for c in fields.get("components", [])],
            "comments": comments,
            "subtasks": subtasks,
            "parent": parent,
            "url": f"{self.base_url}/browse/{data.get('key', '')}",
        }


# Singleton
_jira_client: Optional[JiraClient] = None


def reset_jira_client():
    """Setzt den Jira-Client zurück (nach Settings-Änderung aufrufen)."""
    global _jira_client
    _jira_client = None


def get_jira_client() -> JiraClient:
    """Gibt den Jira-Client zurück (Singleton)."""
    global _jira_client
    if _jira_client is None:
        _jira_client = JiraClient()
    return _jira_client
