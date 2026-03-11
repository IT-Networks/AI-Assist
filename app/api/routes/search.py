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
from typing import Any, Dict, List, Optional, TYPE_CHECKING
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
_PENDING_MAX = 50  # Max pending searches (cleanup threshold)
_PENDING_TTL_SECONDS = 3600  # 1 hour TTL for pending searches

# Shared HTTP Client (Performance: avoid TCP/TLS handshake per request)
_search_client: Optional[httpx.AsyncClient] = None


def _get_search_client() -> httpx.AsyncClient:
    """Returns shared HTTP client for search requests (lazy init)."""
    global _search_client
    if _search_client is None:
        _search_client = httpx.AsyncClient(
            timeout=settings.search.timeout_seconds or 30,
            follow_redirects=True,
            headers=_DDG_HEADERS,
            verify=settings.search.verify_ssl,
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
                keepalive_expiry=30.0
            )
        )
    return _search_client


async def close_search_client():
    """Closes shared HTTP client (for shutdown)."""
    global _search_client
    if _search_client is not None:
        await _search_client.aclose()
        _search_client = None


def _cleanup_old_pending():
    """Remove expired pending searches to prevent memory leaks."""
    if len(_pending) <= _PENDING_MAX:
        return
    now = datetime.now()
    expired = []
    for search_id, item in _pending.items():
        try:
            created = datetime.fromisoformat(item.get("created_at", ""))
            if (now - created).total_seconds() > _PENDING_TTL_SECONDS:
                expired.append(search_id)
        except (ValueError, TypeError):
            expired.append(search_id)  # Invalid date → cleanup
    for search_id in expired:
        del _pending[search_id]
    if expired:
        print(f"[search] Cleanup: removed {len(expired)} expired pending searches")


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
    verify_ssl: bool = True


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

    # Debug-Logging für SSL-Einstellungen
    print(f"[search] Query: {query[:50]}... | verify_ssl={settings.search.verify_ssl} | proxy={bool(proxy_config)}")

    try:
        # Use shared client for better performance (connection reuse)
        # Note: proxy_config handled via environment or client init
        client = _get_search_client()
        resp = await client.post(_DDG_URL, data={"q": query, "kl": "de-de"})
        html = resp.text
    except httpx.TimeoutException:
        proxy_hint = f" (Proxy: {settings.search.proxy_url})" if settings.search.proxy_url else ""
        return [{"title": "Timeout", "snippet": f"Verbindung zu DuckDuckGo timed out nach {timeout}s.{proxy_hint}", "url": ""}]
    except httpx.ProxyError as e:
        return [{"title": "Proxy-Fehler", "snippet": f"Proxy-Verbindung fehlgeschlagen: {e}", "url": ""}]
    except httpx.ConnectError as e:
        error_str = str(e).lower()
        if "ssl" in error_str or "certificate" in error_str:
            ssl_status = "aktiviert" if settings.search.verify_ssl else "deaktiviert"
            return [{
                "title": "SSL-Zertifikatsfehler",
                "snippet": f"SSL-Verifizierung fehlgeschlagen. SSL-Prüfung ist aktuell {ssl_status}. "
                          f"Für selbstsignierte Proxy-Zertifikate: Settings → Web-Suche → "
                          f"'SSL-Zertifikate verifizieren' deaktivieren und speichern.",
                "url": ""
            }]
        return [{"title": "Verbindungsfehler", "snippet": str(e), "url": ""}]
    except Exception as e:
        error_str = str(e).lower()
        if "ssl" in error_str or "certificate" in error_str:
            ssl_status = "aktiviert" if settings.search.verify_ssl else "deaktiviert"
            return [{
                "title": "SSL-Zertifikatsfehler",
                "snippet": f"SSL-Verifizierung fehlgeschlagen ({e}). SSL-Prüfung ist aktuell {ssl_status}. "
                          f"Für selbstsignierte Proxy-Zertifikate: Settings → Web-Suche → "
                          f"'SSL-Zertifikate verifizieren' deaktivieren und speichern.",
                "url": ""
            }]
        return [{"title": "Fehler", "snippet": str(e), "url": ""}]

    results = []

    # Debug: HTML-Länge und erste Zeichen loggen
    print(f"[search] HTML response length: {len(html)} chars")
    # Immer ersten Teil der Antwort loggen für Debugging
    html_preview = html[:800].replace('\n', ' ').replace('\r', '')
    print(f"[search] HTML preview: {html_preview[:400]}...")
    if len(html) < 1000:
        print(f"[search] WARNING: Short response (possible block/error)")
        print(f"[search] Full short response: {html}")

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

    print(f"[search] Found: {len(title_blocks)} titles, {len(snippet_blocks)} snippets, {len(url_blocks)} urls")

    for i in range(min(max_results, len(title_blocks))):
        title = _clean(title_blocks[i])
        snippet = _clean(snippet_blocks[i]) if i < len(snippet_blocks) else ""
        url = unquote(url_blocks[i]) if i < len(url_blocks) else ""
        results.append({
            "title": title,
            "snippet": snippet,
            "url": url,
        })
        # Log jeden einzelnen Treffer
        print(f"[search] Result {i+1}: {title[:60]}... | {snippet[:80]}... | {url[:50]}...")

    if not results:
        # Fallback: DuckDuckGo Instant Answer JSON
        print(f"[search] No HTML results, trying JSON API fallback...")
        try:
            client = _get_search_client()
            r = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
            )
            data = r.json()
            abstract = data.get("AbstractText", "")
            abstract_url = data.get("AbstractURL", "")
            print(f"[search] JSON API response: abstract={bool(abstract)}, topics={len(data.get('RelatedTopics', []))}")
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
        except Exception as e:
            print(f"[search] JSON API fallback failed: {e}")

    if not results:
        print(f"[search] No results found for query: {query[:50]}...")
        # Hilfreiche Fehlermeldung mit Debug-Info
        return [{
            "title": "Keine Ergebnisse",
            "snippet": f"DuckDuckGo hat keine Treffer geliefert. "
                      f"HTML-Länge: {len(html)} Zeichen. "
                      f"Mögliche Ursachen: Proxy blockiert Anfrage, Query zu speziell, oder Captcha/Rate-Limiting.",
            "url": ""
        }]

    return results


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
        "verify_ssl": settings.search.verify_ssl,
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
    settings.search.verify_ssl = req.verify_ssl

    return {
        "success": True,
        "message": "Proxy-Konfiguration aktualisiert. POST /api/settings/save zum Persistieren.",
        "config": {
            "proxy_url": settings.search.proxy_url,
            "proxy_username": settings.search.proxy_username,
            "proxy_password": "***" if settings.search.proxy_password else "",
            "no_proxy": settings.search.no_proxy,
            "timeout_seconds": settings.search.timeout_seconds,
            "verify_ssl": settings.search.verify_ssl,
        }
    }


# ── Pending Management ────────────────────────────────────────────────────────

@router.post("/request")
async def create_search_request(req: SearchRequest) -> Dict[str, Any]:
    """Legt eine neue Suchanfrage an (Status: pending, wartet auf Bestätigung)."""
    if not settings.search.enabled:
        raise HTTPException(status_code=403, detail="Web-Suche ist deaktiviert")

    # Cleanup expired pending searches to prevent memory leaks
    _cleanup_old_pending()

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
        print(f"[search] Search {search_id} completed with {len(results)} results:")
        for idx, r in enumerate(results[:5]):  # Erste 5 Results loggen
            print(f"[search]   [{idx+1}] {r.get('title', '')[:50]} | {r.get('snippet', '')[:80]}...")
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


# ── Test-Endpoint ────────────────────────────────────────────────────────────

@router.post("/test")
async def test_search() -> Dict[str, Any]:
    """
    Führt eine Test-Suche durch und gibt Debug-Informationen zurück.
    Nützlich um Proxy- und Parsing-Probleme zu diagnostizieren.
    """
    test_query = "python programming"
    timeout = settings.search.timeout_seconds or 30
    proxy_config = _get_proxy_config(_DDG_URL)

    debug_info = {
        "query": test_query,
        "proxy_url": settings.search.proxy_url or "(kein Proxy)",
        "proxy_configured": bool(proxy_config),
        "verify_ssl": settings.search.verify_ssl,
        "timeout": timeout,
    }

    try:
        client = _get_search_client()
        resp = await client.post(_DDG_URL, data={"q": test_query, "kl": "de-de"})
        html = resp.text
        status_code = resp.status_code

        debug_info["http_status"] = status_code
        debug_info["response_length"] = len(html)
        debug_info["response_preview"] = html[:1000] if len(html) < 5000 else html[:500] + "\n...[truncated]...\n" + html[-500:]

        # Regex-Matching testen
        title_blocks = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.DOTALL)
        snippet_blocks = re.findall(r'class="result__snippet[^"]*"[^>]*>(.*?)</span>', html, re.DOTALL)
        url_blocks = re.findall(r'uddg=([^&"]+)', html)

        debug_info["regex_matches"] = {
            "titles_found": len(title_blocks),
            "snippets_found": len(snippet_blocks),
            "urls_found": len(url_blocks),
            "first_title": _clean(title_blocks[0])[:100] if title_blocks else None,
        }

        # Alternative: Prüfe ob es eine Bot-Block-Seite ist
        bot_indicators = [
            "robot" in html.lower(),
            "captcha" in html.lower(),
            "blocked" in html.lower(),
            "unusual traffic" in html.lower(),
            "js-challenge" in html.lower(),
        ]
        debug_info["bot_block_indicators"] = {
            "robot": "robot" in html.lower(),
            "captcha": "captcha" in html.lower(),
            "blocked": "blocked" in html.lower(),
            "unusual_traffic": "unusual traffic" in html.lower(),
            "possible_block": any(bot_indicators),
        }

        # Vollständige Suche ausführen
        results = await _ddg_search(test_query, 3)
        debug_info["search_results"] = results

        return {
            "success": True,
            "debug": debug_info,
        }

    except httpx.TimeoutException as e:
        return {
            "success": False,
            "error": f"Timeout nach {timeout}s",
            "debug": debug_info,
        }
    except httpx.ProxyError as e:
        return {
            "success": False,
            "error": f"Proxy-Fehler: {e}",
            "debug": debug_info,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
            "debug": debug_info,
        }
