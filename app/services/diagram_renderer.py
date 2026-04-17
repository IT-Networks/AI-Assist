"""
Diagram Renderer - rendert Mermaid/Graphviz/PlantUML-Quellen zu PNG/SVG.

Primaer-Strategie: Kroki HTTP-API (https://kroki.io) — unterstuetzt alle gaengigen
Formate ohne lokale Installation. Fallback: lokale Binaries (mmdc, dot, plantuml),
falls auf PATH verfuegbar.

Bei Render-Fehler: None — der Aufrufer postet dann den Source als Code-Block.
"""

import asyncio
import base64
import logging
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


SUPPORTED_FORMATS = ("mermaid", "graphviz", "plantuml", "dot")
KROKI_URL = "https://kroki.io"


class DiagramRenderer:
    """Rendert Textquellen (Mermaid/Graphviz/PlantUML) zu PNG-Dateien."""

    def __init__(self, output_dir: Optional[Path] = None) -> None:
        project_root = Path(__file__).parent.parent.parent
        self.output_dir = output_dir or (project_root / "sandbox_uploads" / "webex_diagrams")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ── Public API ───────────────────────────────────────────────────────────

    async def render(
        self,
        source: str,
        fmt: str = "mermaid",
        caption: str = "",
    ) -> Optional[Path]:
        """Rendert source → PNG-Datei. Gibt Path zurueck oder None bei Fehler.

        Args:
            source: Diagramm-Quelltext (z.B. ``graph TD; A-->B``)
            fmt: Format (mermaid/graphviz/plantuml/dot)
            caption: nicht genutzt (reserviert fuer zukuenftige Meta-Infos)
        """
        fmt = (fmt or "mermaid").strip().lower()
        if fmt == "dot":
            fmt = "graphviz"
        if fmt not in SUPPORTED_FORMATS:
            logger.warning("[diagram] unsupported format: %s", fmt)
            return None
        if not source or not source.strip():
            return None

        target = self.output_dir / f"{fmt}_{uuid.uuid4().hex[:12]}.png"

        # 1) Lokale Binaries versuchen (schneller, offline-faehig)
        rendered = await self._try_local(source, fmt, target)
        if rendered:
            return rendered

        # 2) Kroki HTTP-Fallback
        rendered = await self._try_kroki(source, fmt, target)
        if rendered:
            return rendered

        logger.info("[diagram] rendering failed for format=%s (len=%d)", fmt, len(source))
        return None

    # ── Strategien ───────────────────────────────────────────────────────────

    async def _try_local(self, source: str, fmt: str, target: Path) -> Optional[Path]:
        try:
            if fmt == "mermaid" and shutil.which("mmdc"):
                return await self._render_mermaid_cli(source, target)
            if fmt == "graphviz" and shutil.which("dot"):
                return await self._render_graphviz_cli(source, target)
            # plantuml: Binary heisst 'plantuml' oder java-Aufruf — wir ueberspringen
            # und vertrauen auf Kroki.
        except Exception as e:
            logger.debug("[diagram] local render failed: %s", e)
        return None

    async def _render_mermaid_cli(self, source: str, target: Path) -> Optional[Path]:
        """Nutzt @mermaid-js/mermaid-cli (mmdc)."""
        src_file = target.with_suffix(".mmd")
        try:
            src_file.write_text(source, encoding="utf-8")
            proc = await asyncio.create_subprocess_exec(
                "mmdc", "-i", str(src_file), "-o", str(target),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                proc.kill()
                logger.warning("[diagram] mmdc timeout")
                return None
            if proc.returncode == 0 and target.exists() and target.stat().st_size > 0:
                return target
            logger.debug("[diagram] mmdc exit=%s stderr=%s", proc.returncode, (stderr or b"")[:200])
        finally:
            try:
                src_file.unlink(missing_ok=True)
            except Exception:
                pass
        return None

    async def _render_graphviz_cli(self, source: str, target: Path) -> Optional[Path]:
        """Nutzt Graphviz 'dot' binary: dot -Tpng -o out.png < source."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "dot", "-Tpng", "-o", str(target),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(
                    proc.communicate(input=source.encode("utf-8")), timeout=30,
                )
            except asyncio.TimeoutError:
                proc.kill()
                logger.warning("[diagram] dot timeout")
                return None
            if proc.returncode == 0 and target.exists() and target.stat().st_size > 0:
                return target
            logger.debug("[diagram] dot exit=%s stderr=%s", proc.returncode, (stderr or b"")[:200])
        except FileNotFoundError:
            pass
        return None

    async def _try_kroki(self, source: str, fmt: str, target: Path) -> Optional[Path]:
        """POST https://kroki.io/{format}/png mit Raw-Body = Source."""
        from app.core.config import settings

        proxy = None
        if settings.webex.use_proxy and settings.proxy.enabled:
            proxy = settings.proxy.get_proxy_url()

        # Kroki-Formate: mermaid, graphviz, plantuml — matcht unsere SUPPORTED_FORMATS
        url = f"{KROKI_URL}/{fmt}/png"
        try:
            async with httpx.AsyncClient(
                timeout=45,
                verify=False if proxy else True,  # Corporate-Proxies brechen oft SSL
                proxy=proxy,
            ) as client:
                resp = await client.post(
                    url,
                    content=source.encode("utf-8"),
                    headers={"Content-Type": "text/plain"},
                )
                if resp.status_code != 200:
                    logger.debug("[diagram] kroki HTTP %s: %s", resp.status_code, resp.text[:200])
                    return None
                if not resp.content.startswith(b"\x89PNG"):
                    logger.debug("[diagram] kroki returned non-PNG content")
                    return None
                target.write_bytes(resp.content)
                return target
        except Exception as e:
            logger.debug("[diagram] kroki render failed: %s", e)
            return None


# ── Singleton ────────────────────────────────────────────────────────────────

_renderer: Optional[DiagramRenderer] = None


def get_diagram_renderer() -> DiagramRenderer:
    global _renderer
    if _renderer is None:
        _renderer = DiagramRenderer()
    return _renderer


# ── Utility: Source als Markdown-Code-Block formatieren (Fallback) ──────────

def source_as_code_block(source: str, fmt: str) -> str:
    """Wrappt Diagramm-Source in einen Markdown-Code-Block mit Sprach-Tag."""
    lang = fmt if fmt in SUPPORTED_FORMATS else ""
    safe = source.strip()
    if len(safe) > 4000:
        safe = safe[:4000] + "\n... (gekuerzt)"
    return f"```{lang}\n{safe}\n```"
