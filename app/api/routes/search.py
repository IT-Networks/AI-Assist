"""
Web-Suche API – Internet-Recherche mit Bestätigungspflicht.

Routes:
  GET    /api/search/status           – Suche aktiviert?
  PUT    /api/search/toggle           – An-/Ausschalten
  POST   /api/search/request          – Neue Suchanfrage (wird Agent aufgerufen)
  GET    /api/search/pending          – Ausstehende Bestätigungen
  POST   /api/search/confirm/{id}     – Suche bestätigen + ausführen
  DELETE /api/search/cancel/{id}      – Suche ablehnen
  GET    /api/search/history          – Letzte Suchergebnisse
"""

import asyncio
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import settings

router = APIRouter(prefix="/api/search", tags=["search"])

# ── In-Memory State ────────────────────────────────────────────────────────────

# search_id → {id, query, reason, status, results, created_at}
_pending: Dict[str, Dict[str, Any]] = {}
_history: List[Dict[str, Any]] = []        # Letzte 20 Ergebnisse
_HISTORY_MAX = 20


# ── Request Models ─────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    reason: str = ""           # Warum wird gesucht (für Nutzer-Info)
    max_results: int = 5


class ToggleRequest(BaseModel):
    enabled: bool


class SearchConfigRequest(BaseModel):
    """Proxy-Konfiguration für Web-Suche."""
    proxy_url: str = ""
    proxy_username: str = ""
    proxy_password: str = ""
    no_proxy: str = ""
    timeout_seconds: int = 30


# ── DuckDuckGo Search ─────────────────────────────────────────────────────────

_DDG_URL = "https://html.duckduckgo.com/html/"
_DDG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "de,en;q=0.9",
}

# Entferne HTML-Tags und &entity;
_TAG_RE = re.compile(r"<[^>]+>")
_ENT_RE = re.compile(r"&[a-zA-Z]+;|&#\d+;")


def _clean(html: str) -> str:
    s = _TAG_RE.sub("", html)
    s = _ENT_RE.sub(" ", s)
    return " ".join(s.split()).strip()


def _should_bypass_proxy(url: str) -> bool:
    """Prüft ob die URL im no_proxy-List ist und kein Proxy verwendet werden soll."""
    if not settings.search.no_proxy:
        return False

    no_proxy_list = [x.strip().lower() for x in settings.search.no_proxy.split(",") if x.strip()]
    if not no_proxy_list:
        return False

    # Host aus URL extrahieren
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except Exception:
        return False

    for pattern in no_proxy_list:
        # Exakte Übereinstimmung
        if host == pattern:
            return True
        # Wildcard-Suffix (z.B. ".intern" matched "server.intern")
        if pattern.startswith(".") and host.endswith(pattern):
            return True
        # Suffix ohne Punkt (z.B. "intern" matched "server.intern")
        if not pattern.startswith(".") and host.endswith("." + pattern):
            return True

    return False


def _get_proxy_config(target_url: str = "") -> dict:
    """Erstellt die Proxy-Konfiguration für httpx."""
    # Kein Proxy wenn in no_proxy-Liste
    if target_url and _should_bypass_proxy(target_url):
        return {}

    proxy_url = settings.search.get_proxy_url()
    if not proxy_url:
        return {}

    return {
        "proxy": proxy_url,
    }


async def _ddg_search(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """Führt eine DuckDuckGo-HTML-Suche durch und gibt Treffer zurück."""
    timeout = settings.search.timeout_seconds or 30

    # Proxy-Konfiguration (mit no_proxy-Prüfung)
    proxy_config = _get_proxy_config(_DDG_URL)

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=_DDG_HEADERS,
            **proxy_config,
        ) as client:
            resp = await client.post(_DDG_URL, data={"q": query, "kl": "de-de"})
            html = resp.text
    except httpx.TimeoutException:
        proxy_hint = f" (Proxy: {settings.search.proxy_url})" if settings.search.proxy_url else ""
        return [{"title": "Timeout", "snippet": f"Verbindung zu DuckDuckGo timed out nach {timeout}s.{proxy_hint}", "url": ""}]
    except httpx.ProxyError as e:
        return [{"title": "Proxy-Fehler", "snippet": f"Proxy-Verbindung fehlgeschlagen: {e}", "url": ""}]
    except Exception as e:
        return [{"title": "Fehler", "snippet": str(e), "url": ""}]

    results = []

    # Titel: class="result__a"
    title_blocks = re.findall(
        r'class="result__a"[^>]*>(.*?)</a>', html, re.DOTALL
    )
    # Snippet: class="result__snippet"
    snippet_blocks = re.findall(
        r'class="result__snippet[^"]*"[^>]*>(.*?)</span>', html, re.DOTALL
    )
    # URL: uddg=<encoded-url>
    url_blocks = re.findall(r'uddg=([^&"]+)', html)

    for i in range(min(max_results, len(title_blocks))):
        results.append({
            "title": _clean(title_blocks[i]),
            "snippet": _clean(snippet_blocks[i]) if i < len(snippet_blocks) else "",
            "url": unquote(url_blocks[i]) if i < len(url_blocks) else "",
        })

    if not results:
        # Fallback: DuckDuckGo Instant Answer JSON
        try:
            async with httpx.AsyncClient(timeout=timeout, **proxy_config) as client:
                r = await client.get(
                    "https://api.duckduckgo.com/",
                    params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
                )
                data = r.json()
            abstract = data.get("AbstractText", "")
            abstract_url = data.get("AbstractURL", "")
            if abstract:
                results.append({
                    "title": data.get("Heading", query),
                    "snippet": abstract[:500],
                    "url": abstract_url,
                })
            for topic in data.get("RelatedTopics", [])[:max_results]:
                if isinstance(topic, dict) and topic.get("Text"):
                    results.append({
                        "title": topic.get("Text", "")[:80],
                        "snippet": topic.get("Text", "")[:300],
                        "url": topic.get("FirstURL", ""),
                    })
        except Exception:
            pass

    return results or [{"title": "Keine Ergebnisse", "snippet": "DuckDuckGo hat keine Treffer geliefert.", "url": ""}]


# ── Sanitization ──────────────────────────────────────────────────────────────

_INTERNAL_IP_RE = re.compile(
    r"\b(10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|127\.\d+\.\d+\.\d+)\b"
)
_INTERNAL_HOST_RE = re.compile(
    r"\b[\w-]+(\.local|\.intern|\.corp|\.lan|\.internal|\.dev\.)\b", re.I
)
_PATH_RE = re.compile(r"(/home/|/var/|/opt/|C:\\|D:\\)[\w/\\.-]+")


def check_internal_data(query: str) -> List[str]:
    """Prüft ob die Query interne Projektdaten enthält."""
    warnings = []
    if _INTERNAL_IP_RE.search(query):
        warnings.append("Interne IP-Adresse")
    if _INTERNAL_HOST_RE.search(query):
        warnings.append("Interner Hostname")
    if _PATH_RE.search(query):
        warnings.append("Lokaler Dateipfad")
    return warnings


# ── Toggle ────────────────────────────────────────────────────────────────────

@router.get("/status")
async def get_status() -> Dict[str, Any]:
    return {
        "enabled": settings.search.enabled,
        "pending_count": sum(1 for v in _pending.values() if v["status"] == "pending"),
    }


@router.put("/toggle")
async def toggle_search(req: ToggleRequest) -> Dict[str, Any]:
    settings.search.enabled = req.enabled
    return {"enabled": settings.search.enabled}


# ── Proxy-Konfiguration ──────────────────────────────────────────────────────

@router.get("/config")
async def get_search_config() -> Dict[str, Any]:
    """Gibt die aktuelle Proxy-Konfiguration zurück (Passwort maskiert)."""
    return {
        "enabled": settings.search.enabled,
        "proxy_url": settings.search.proxy_url,
        "proxy_username": settings.search.proxy_username,
        "proxy_password": "***" if settings.search.proxy_password else "",
        "no_proxy": settings.search.no_proxy,
        "timeout_seconds": settings.search.timeout_seconds,
    }


@router.put("/config")
async def update_search_config(req: SearchConfigRequest) -> Dict[str, Any]:
    """Aktualisiert die Proxy-Konfiguration."""
    settings.search.proxy_url = req.proxy_url
    settings.search.proxy_username = req.proxy_username
    # Passwort nur setzen wenn nicht maskiert
    if req.proxy_password and req.proxy_password != "***":
        settings.search.proxy_password = req.proxy_password
    settings.search.no_proxy = req.no_proxy
    settings.search.timeout_seconds = max(5, min(req.timeout_seconds, 120))  # 5-120s

    return {
        "success": True,
        "message": "Proxy-Konfiguration aktualisiert. POST /api/settings/save zum Persistieren.",
        "config": {
            "proxy_url": settings.search.proxy_url,
            "proxy_username": settings.search.proxy_username,
            "proxy_password": "***" if settings.search.proxy_password else "",
            "no_proxy": settings.search.no_proxy,
            "timeout_seconds": settings.search.timeout_seconds,
        }
    }


# ── Pending Management ────────────────────────────────────────────────────────

@router.post("/request")
async def create_search_request(req: SearchRequest) -> Dict[str, Any]:
    """Legt eine neue Suchanfrage an (Status: pending, wartet auf Bestätigung)."""
    if not settings.search.enabled:
        raise HTTPException(status_code=403, detail="Web-Suche ist deaktiviert")

    warnings = check_internal_data(req.query)
    if warnings:
        raise HTTPException(
            status_code=400,
            detail=f"Query enthält möglicherweise interne Daten: {', '.join(warnings)}. "
                   f"Bitte nur generische Suchbegriffe verwenden.",
        )

    search_id = str(uuid.uuid4())[:8]
    _pending[search_id] = {
        "id": search_id,
        "query": req.query,
        "reason": req.reason,
        "max_results": min(req.max_results, 10),
        "status": "pending",   # pending | executing | done | rejected | timeout
        "results": None,
        "error": None,
        "created_at": datetime.now().isoformat(),
    }
    return {"search_id": search_id, "status": "pending"}


@router.get("/pending")
async def list_pending() -> Dict[str, Any]:
    items = [v for v in _pending.values() if v["status"] in ("pending", "executing")]
    return {"pending": items, "count": len(items)}


@router.post("/confirm/{search_id}")
async def confirm_search(search_id: str) -> Dict[str, Any]:
    """Nutzer bestätigt die Suche – wird sofort ausgeführt."""
    print(f"[search] Confirm request for {search_id}")
    item = _pending.get(search_id)
    if not item:
        print(f"[search] ERROR: Search {search_id} not found in pending: {list(_pending.keys())}")
        raise HTTPException(status_code=404, detail=f"Suche '{search_id}' nicht gefunden")
    if item["status"] != "pending":
        print(f"[search] ERROR: Search {search_id} already in status: {item['status']}")
        raise HTTPException(status_code=409, detail=f"Suche ist bereits im Status: {item['status']}")

    item["status"] = "executing"
    print(f"[search] Executing search: {item['query'][:50]}...")
    try:
        results = await _ddg_search(item["query"], item["max_results"])
        item["results"] = results
        item["status"] = "done"
        item["executed_at"] = datetime.now().isoformat()
        print(f"[search] Search {search_id} completed with {len(results)} results")
        # In History ablegen
        _history.append({**item})
        if len(_history) > _HISTORY_MAX:
            _history.pop(0)
    except Exception as e:
        print(f"[search] ERROR during search {search_id}: {e}")
        item["status"] = "done"
        item["error"] = str(e)
        item["results"] = []

    return item


@router.delete("/cancel/{search_id}")
async def cancel_search(search_id: str) -> Dict[str, Any]:
    """Nutzer lehnt die Suche ab."""
    item = _pending.get(search_id)
    if item and item["status"] == "pending":
        item["status"] = "rejected"
        item["rejected_at"] = datetime.now().isoformat()
    return {"cancelled": search_id}


@router.get("/poll/{search_id}")
async def poll_search(search_id: str) -> Dict[str, Any]:
    """Agent pollt den Status einer Suchanfrage."""
    item = _pending.get(search_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Suche '{search_id}' nicht gefunden")
    return {
        "status": item["status"],
        "results": item.get("results"),
        "error": item.get("error"),
    }


@router.get("/history")
async def get_history() -> Dict[str, Any]:
    return {"history": list(reversed(_history)), "count": len(_history)}
