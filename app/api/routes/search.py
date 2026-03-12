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
    """Returns shared HTTP client for search requests (lazy init).

    Uses proxy settings from config.search if configured.
    Client is recreated when config changes via reset_search_client().
    """
    global _search_client
    if _search_client is None:
        # Proxy-URL aus Konfiguration
        proxy_url = settings.search.get_proxy_url()
        if proxy_url:
            print(f"[search] Creating HTTP client with proxy: {settings.search.proxy_url}")
        else:
            print(f"[search] Creating HTTP client without proxy")

        _search_client = httpx.AsyncClient(
            timeout=settings.search.timeout_seconds or 30,
            follow_redirects=True,
            headers=_DDG_HEADERS,
            verify=settings.search.verify_ssl,
            proxy=proxy_url,  # Proxy aus Konfiguration
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
                keepalive_expiry=30.0
            )
        )
    return _search_client


def reset_search_client():
    """Resets the shared HTTP client (forces recreation with new settings)."""
    global _search_client
    if _search_client is not None:
        # Schedule async close in background (non-blocking)
        asyncio.create_task(_close_client_async(_search_client))
        _search_client = None
        print("[search] HTTP client reset (will recreate with new settings)")


async def _close_client_async(client: httpx.AsyncClient):
    """Helper to close client asynchronously."""
    try:
        await client.aclose()
    except Exception as e:
        print(f"[search] Warning: Error closing old client: {e}")


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

# ddgs Library (ehemals duckduckgo-search)
try:
    from ddgs import DDGS
    _DDG_AVAILABLE = True
    print("[search] ddgs library available (v9+)")
except ImportError:
    try:
        # Fallback auf alte Library
        from duckduckgo_search import DDGS
        _DDG_AVAILABLE = True
        print("[search] WARNING: using old duckduckgo-search library, upgrade with: pip install ddgs")
    except ImportError:
        _DDG_AVAILABLE = False
        DDGS = None
        print("[search] WARNING: ddgs not installed, using legacy fallback")

# Fallback: Legacy HTML scraping (veraltet, wird blockiert)
_DDG_URL = "https://html.duckduckgo.com/html/"

# Edge User-Agent (Chrome ist im internen Netz oft gesperrt)
_EDGE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)

_DDG_HEADERS = {
    "User-Agent": _EDGE_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Sec-Ch-Ua": '"Microsoft Edge";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
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
    """
    Führt eine DuckDuckGo-Suche durch.

    Verwendet die duckduckgo-search Library (empfohlen) mit HTML-Scraping als Fallback.
    """
    timeout = settings.search.timeout_seconds or 30
    proxy_url = settings.search.get_proxy_url()

    print(f"[search] Query: {query[:50]}... | method={'library' if _DDG_AVAILABLE else 'legacy'} | proxy={bool(proxy_url)}")

    # ═══════════════════════════════════════════════════════════════════════════
    # Methode 1: duckduckgo-search Library (bevorzugt)
    # ═══════════════════════════════════════════════════════════════════════════
    if _DDG_AVAILABLE:
        try:
            results = await _ddg_search_library(query, max_results, proxy_url, timeout)
            if results:
                return results
            print("[search] Library returned no results, trying fallback...")
        except Exception as e:
            print(f"[search] Library search failed: {e}, trying fallback...")

    # ═══════════════════════════════════════════════════════════════════════════
    # Methode 2: Legacy HTML-Scraping (Fallback)
    # ═══════════════════════════════════════════════════════════════════════════
    return await _ddg_search_legacy(query, max_results, timeout)


async def _ddg_search_library(
    query: str,
    max_results: int,
    proxy_url: Optional[str],
    timeout: int
) -> List[Dict[str, str]]:
    """Suche mit ddgs Library (v9+)."""
    import asyncio

    results = []

    def sync_search():
        """Synchrone Suche (Library ist nicht async)."""
        try:
            # ddgs v9+ hat eine simplere API ohne constructor params
            # Proxy wird über Umgebungsvariablen konfiguriert wenn nötig
            if proxy_url:
                import os
                os.environ["HTTP_PROXY"] = proxy_url
                os.environ["HTTPS_PROXY"] = proxy_url
                print(f"[search] Using proxy via env: {proxy_url[:50]}...")

            print(f"[search] DDGS config: timeout={timeout}, proxy={bool(proxy_url)}")

            with DDGS() as ddgs:
                # Text-Suche durchführen (wt-wt = worldwide für bessere Ergebnisse)
                search_results = list(ddgs.text(
                    query,
                    region="wt-wt",
                    safesearch="moderate",
                    max_results=max_results
                ))
                return search_results
        except Exception as e:
            error_msg = str(e).lower()
            error_short = str(e)[:200]  # Begrenze Fehlerausgabe
            if "proxy" in error_msg:
                print(f"[search] DDGS Proxy error: {error_short}")
            elif "ssl" in error_msg or "certificate" in error_msg:
                print(f"[search] DDGS SSL error: {error_short} (try disabling SSL verification)")
            elif "timeout" in error_msg:
                print(f"[search] DDGS Timeout: {error_short}")
            elif "ratelimit" in error_msg or "rate" in error_msg:
                print(f"[search] DDGS Rate-Limit: {error_short}")
            else:
                print(f"[search] DDGS error: {error_short}")
            raise

    # In Thread ausführen (Library ist synchron)
    loop = asyncio.get_event_loop()
    search_results = await loop.run_in_executor(None, sync_search)

    for r in search_results:
        title = r.get("title", "")
        # ddgs v9+ uses "body", older versions use "snippet"
        snippet = r.get("body", "") or r.get("snippet", "")
        # ddgs v9+ uses "href", older versions use "url"
        url = r.get("href", "") or r.get("url", "")
        results.append({
            "title": title,
            "snippet": snippet[:500] if snippet else "",
            "url": url,
        })
        print(f"[search] Result: {title[:60]}... | {url[:50]}...")

    print(f"[search] Library found {len(results)} results")
    return results


async def _ddg_search_legacy(query: str, max_results: int, timeout: int) -> List[Dict[str, str]]:
    """Legacy HTML-Scraping (Fallback wenn Library nicht verfügbar)."""
    try:
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
        return [{"title": "Fehler", "snippet": str(e), "url": ""}]

    results = []

    # Debug: HTML-Länge loggen
    print(f"[search] Legacy HTML response: {len(html)} chars")
    if len(html) < 1000:
        print(f"[search] WARNING: Short response (possible block)")

    # Titel: class="result__a"
    title_blocks = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.DOTALL)
    snippet_blocks = re.findall(r'class="result__snippet[^"]*"[^>]*>(.*?)</span>', html, re.DOTALL)
    url_blocks = re.findall(r'uddg=([^&"]+)', html)

    print(f"[search] Legacy found: {len(title_blocks)} titles, {len(snippet_blocks)} snippets")

    for i in range(min(max_results, len(title_blocks))):
        title = _clean(title_blocks[i])
        snippet = _clean(snippet_blocks[i]) if i < len(snippet_blocks) else ""
        url = unquote(url_blocks[i]) if i < len(url_blocks) else ""
        results.append({"title": title, "snippet": snippet, "url": url})

    if not results:
        # Fallback: DuckDuckGo Instant Answer JSON
        import json as json_module
        try:
            client = _get_search_client()
            r = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
            )

            # Response validieren
            raw_bytes = r.content
            if not raw_bytes or len(raw_bytes) < 2:
                print(f"[search] JSON API returned empty response (status: {r.status_code})")
            else:
                # Prüfen ob es wirklich JSON ist (nicht HTML-Fehlerseite)
                text = raw_bytes.decode('utf-8', errors='replace').strip()
                if text.startswith('<') or text.startswith('<!'):
                    print(f"[search] JSON API returned HTML instead of JSON (blocked?)")
                elif text:
                    try:
                        data = json_module.loads(text)
                        abstract = data.get("AbstractText", "")
                        if abstract:
                            results.append({
                                "title": data.get("Heading", query),
                                "snippet": abstract[:500],
                                "url": data.get("AbstractURL", ""),
                            })
                        for topic in data.get("RelatedTopics", [])[:max_results]:
                            if isinstance(topic, dict) and topic.get("Text"):
                                results.append({
                                    "title": topic.get("Text", "")[:80],
                                    "snippet": topic.get("Text", "")[:300],
                                    "url": topic.get("FirstURL", ""),
                                })
                    except json_module.JSONDecodeError as e:
                        print(f"[search] JSON parse error: {e} | Response: {text[:100]}...")
        except Exception as e:
            print(f"[search] JSON API fallback failed: {e}")

    if not results:
        return [{
            "title": "Keine Ergebnisse",
            "snippet": f"DuckDuckGo hat keine Treffer geliefert. Mögliche Ursachen: Bot-Sperre, Proxy blockiert, oder Rate-Limiting.",
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

    # HTTP-Client neu erstellen damit neue Proxy-Einstellungen wirksam werden
    reset_search_client()

    return {
        "success": True,
        "message": "Proxy-Konfiguration aktualisiert. HTTP-Client wird neu erstellt. POST /api/settings/save zum Persistieren.",
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
    proxy_url = settings.search.get_proxy_url()

    debug_info = {
        "query": test_query,
        "search_method": "duckduckgo-search library" if _DDG_AVAILABLE else "legacy HTML scraping",
        "library_available": _DDG_AVAILABLE,
        "proxy_url": settings.search.proxy_url or "(kein Proxy)",
        "proxy_configured": bool(proxy_url),
        "verify_ssl": settings.search.verify_ssl,
        "timeout": timeout,
    }

    try:
        # Vollständige Suche ausführen (verwendet Library wenn verfügbar)
        results = await _ddg_search(test_query, 3)

        debug_info["search_results"] = results
        debug_info["result_count"] = len(results)

        # Prüfe ob Ergebnisse "echte" Treffer sind
        has_real_results = any(
            r.get("url") and not r.get("title", "").startswith("Keine")
            for r in results
        )
        debug_info["has_real_results"] = has_real_results

        return {
            "success": has_real_results,
            "message": "Suche erfolgreich" if has_real_results else "Keine echten Ergebnisse gefunden",
            "debug": debug_info,
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
            "debug": debug_info,
        }


@router.get("/info")
async def get_search_info() -> Dict[str, Any]:
    """Gibt Informationen über die Such-Konfiguration zurück."""
    return {
        "library_available": _DDG_AVAILABLE,
        "library_name": "duckduckgo-search" if _DDG_AVAILABLE else None,
        "search_method": "library" if _DDG_AVAILABLE else "legacy",
        "enabled": settings.search.enabled,
        "proxy_configured": bool(settings.search.proxy_url),
        "verify_ssl": settings.search.verify_ssl,
        "timeout_seconds": settings.search.timeout_seconds,
    }
