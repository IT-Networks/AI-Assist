"""
Context Manager - Verwaltet das 3-Schichten-Kontext-System.

Schichten:
1. GLOBAL: User-weite Präferenzen (~/.ai-assist/global/)
2. PROJECT: Projekt-spezifischer Kontext (.ai-assist/)
3. SESSION: Temporärer Session-Kontext (in-memory)

Inspiriert von Claude Code's CLAUDE.md und Auto-Memory System.
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from app.core.config import settings
from app.utils.token_counter import estimate_tokens


@dataclass
class ProjectContext:
    """Geladener Projekt-Kontext."""
    name: str
    description: str = ""
    architecture: Dict[str, Any] = field(default_factory=dict)
    conventions: Dict[str, Any] = field(default_factory=dict)
    critical_knowledge: List[str] = field(default_factory=list)
    forbidden: List[str] = field(default_factory=list)
    raw_content: str = ""

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.raw_content)

    def to_prompt(self, max_tokens: int = 500) -> str:
        """Konvertiert zu LLM-Prompt-Format."""
        if not self.raw_content:
            return ""

        # Wenn unter Token-Limit, vollständig zurückgeben
        if self.tokens <= max_tokens:
            return self.raw_content

        # Sonst: Kompakte Version
        parts = [f"# Projekt: {self.name}"]

        if self.description:
            parts.append(f"\n{self.description}")

        if self.critical_knowledge:
            parts.append("\n## Wichtig:")
            for item in self.critical_knowledge[:5]:
                parts.append(f"- {item}")

        if self.forbidden:
            parts.append("\n## Verboten:")
            for item in self.forbidden[:3]:
                parts.append(f"- {item}")

        return "\n".join(parts)


@dataclass
class GlobalPreferences:
    """User-weite Präferenzen."""
    coding_style: Dict[str, str] = field(default_factory=dict)
    preferred_frameworks: List[str] = field(default_factory=list)
    language: str = "de"

    def to_prompt(self) -> str:
        """Konvertiert zu LLM-Prompt-Format."""
        if not self.coding_style and not self.preferred_frameworks:
            return ""

        parts = ["## User-Präferenzen"]

        if self.coding_style:
            for key, value in self.coding_style.items():
                parts.append(f"- {key}: {value}")

        if self.preferred_frameworks:
            parts.append(f"- Bevorzugte Frameworks: {', '.join(self.preferred_frameworks)}")

        return "\n".join(parts)


class ContextManager:
    """
    Verwaltet das 3-Schichten-Kontext-System.

    Lädt und cached Kontext aus verschiedenen Quellen:
    - Global: ~/.ai-assist/global/
    - Project: {project_root}/.ai-assist/ oder /data/projects/{id}/
    - Session: In-Memory via AgentState
    """

    # Dateinamen
    PROJECT_CONTEXT_FILE = "PROJECT_CONTEXT.md"
    PROJECT_CONTEXT_YAML = "PROJECT_CONTEXT.yaml"
    GLOBAL_PREFS_FILE = "user_preferences.yaml"
    MEMORY_FILE = "MEMORY.md"

    # Cache
    _project_cache: Dict[str, ProjectContext] = {}
    _global_prefs_cache: Optional[GlobalPreferences] = None
    _cache_timestamps: Dict[str, datetime] = {}

    # Cache-Timeout (5 Minuten)
    CACHE_TTL_SECONDS = 300

    def __init__(self, global_dir: Optional[str] = None):
        """
        Initialisiert den ContextManager.

        Args:
            global_dir: Pfad zum globalen Konfigurationsverzeichnis.
                        Default: ~/.ai-assist/global/
        """
        if global_dir:
            self.global_dir = Path(global_dir)
        else:
            # Default: User Home
            home = Path.home()
            self.global_dir = home / ".ai-assist" / "global"

        # Verzeichnis erstellen wenn nicht vorhanden
        self.global_dir.mkdir(parents=True, exist_ok=True)

    # ══════════════════════════════════════════════════════════════════════
    # Project Context (Layer 2)
    # ══════════════════════════════════════════════════════════════════════

    async def load_project_context(
        self,
        project_path: Optional[str] = None,
        project_id: Optional[str] = None
    ) -> Optional[ProjectContext]:
        """
        Lädt den Projekt-Kontext.

        Sucht in folgender Reihenfolge:
        1. {project_path}/.ai-assist/PROJECT_CONTEXT.md
        2. {project_path}/.ai-assist/PROJECT_CONTEXT.yaml
        3. {project_path}/PROJECT_CONTEXT.md (Root)
        4. /data/projects/{project_id}/.ai-assist/PROJECT_CONTEXT.md

        Returns:
            ProjectContext oder None wenn nicht gefunden
        """
        cache_key = project_path or project_id or "default"

        # Cache prüfen
        if cache_key in self._project_cache:
            cache_time = self._cache_timestamps.get(cache_key)
            if cache_time and (datetime.now() - cache_time).seconds < self.CACHE_TTL_SECONDS:
                return self._project_cache[cache_key]

        context = None

        # Suchpfade aufbauen
        search_paths = []

        if project_path:
            base = Path(project_path)
            search_paths.extend([
                base / ".ai-assist" / self.PROJECT_CONTEXT_FILE,
                base / ".ai-assist" / self.PROJECT_CONTEXT_YAML,
                base / self.PROJECT_CONTEXT_FILE,
            ])

        if project_id:
            data_base = Path(settings.index.directory) / "projects" / project_id
            search_paths.extend([
                data_base / ".ai-assist" / self.PROJECT_CONTEXT_FILE,
                data_base / self.PROJECT_CONTEXT_YAML,
            ])

        # Dateien durchsuchen
        for path in search_paths:
            if path.exists():
                context = await self._load_context_file(path)
                if context:
                    break

        # Cache aktualisieren
        if context:
            self._project_cache[cache_key] = context
            self._cache_timestamps[cache_key] = datetime.now()

        return context

    async def _load_context_file(self, path: Path) -> Optional[ProjectContext]:
        """Lädt eine Kontext-Datei (MD oder YAML)."""
        try:
            content = path.read_text(encoding="utf-8")

            if path.suffix.lower() == ".yaml":
                return self._parse_yaml_context(content)
            else:
                return self._parse_md_context(content)
        except Exception as e:
            print(f"[ContextManager] Fehler beim Laden von {path}: {e}")
            return None

    def _parse_yaml_context(self, content: str) -> ProjectContext:
        """Parst YAML-Kontext."""
        data = yaml.safe_load(content) or {}

        project = data.get("project", {})

        return ProjectContext(
            name=project.get("name", "Unknown"),
            description=project.get("description", ""),
            architecture=data.get("architecture", {}),
            conventions=data.get("conventions", {}),
            critical_knowledge=data.get("critical_knowledge", []),
            forbidden=data.get("forbidden", []),
            raw_content=content
        )

    def _parse_md_context(self, content: str) -> ProjectContext:
        """Parst Markdown-Kontext."""
        # Einfaches Parsing: Name aus erster Überschrift
        lines = content.split("\n")
        name = "Unknown"

        for line in lines:
            if line.startswith("# "):
                name = line[2:].strip()
                break

        return ProjectContext(
            name=name,
            raw_content=content
        )

    async def save_project_context(
        self,
        project_path: str,
        context: ProjectContext
    ) -> bool:
        """
        Speichert den Projekt-Kontext.

        Returns:
            True bei Erfolg
        """
        try:
            ai_assist_dir = Path(project_path) / ".ai-assist"
            ai_assist_dir.mkdir(parents=True, exist_ok=True)

            context_file = ai_assist_dir / self.PROJECT_CONTEXT_FILE
            context_file.write_text(context.raw_content, encoding="utf-8")

            # Cache invalidieren
            cache_key = project_path
            if cache_key in self._project_cache:
                del self._project_cache[cache_key]

            return True
        except Exception as e:
            print(f"[ContextManager] Fehler beim Speichern: {e}")
            return False

    # ══════════════════════════════════════════════════════════════════════
    # Global Preferences (Layer 1)
    # ══════════════════════════════════════════════════════════════════════

    async def load_global_preferences(self) -> Optional[GlobalPreferences]:
        """
        Lädt die globalen User-Präferenzen.

        Returns:
            GlobalPreferences oder None
        """
        # Cache prüfen
        if self._global_prefs_cache:
            cache_time = self._cache_timestamps.get("global_prefs")
            if cache_time and (datetime.now() - cache_time).seconds < self.CACHE_TTL_SECONDS:
                return self._global_prefs_cache

        prefs_file = self.global_dir / self.GLOBAL_PREFS_FILE

        if not prefs_file.exists():
            return None

        try:
            content = prefs_file.read_text(encoding="utf-8")
            data = yaml.safe_load(content) or {}

            prefs = GlobalPreferences(
                coding_style=data.get("coding_style", {}),
                preferred_frameworks=data.get("preferred_frameworks", []),
                language=data.get("language", "de")
            )

            # Cache
            self._global_prefs_cache = prefs
            self._cache_timestamps["global_prefs"] = datetime.now()

            return prefs
        except Exception as e:
            print(f"[ContextManager] Fehler beim Laden globaler Präferenzen: {e}")
            return None

    async def save_global_preferences(self, prefs: GlobalPreferences) -> bool:
        """Speichert globale Präferenzen."""
        try:
            prefs_file = self.global_dir / self.GLOBAL_PREFS_FILE

            data = {
                "coding_style": prefs.coding_style,
                "preferred_frameworks": prefs.preferred_frameworks,
                "language": prefs.language
            }

            prefs_file.write_text(
                yaml.dump(data, allow_unicode=True, default_flow_style=False),
                encoding="utf-8"
            )

            # Cache invalidieren
            self._global_prefs_cache = None

            return True
        except Exception as e:
            print(f"[ContextManager] Fehler beim Speichern: {e}")
            return False

    # ══════════════════════════════════════════════════════════════════════
    # MEMORY.md (Dynamisches Projekt-Wissen)
    # ══════════════════════════════════════════════════════════════════════

    async def load_memory_md(
        self,
        project_path: Optional[str] = None,
        max_lines: int = 200
    ) -> str:
        """
        Lädt MEMORY.md (wie Claude Code - max 200 Zeilen).

        Returns:
            Inhalt der MEMORY.md oder leerer String
        """
        if not project_path:
            return ""

        memory_file = Path(project_path) / ".ai-assist" / self.MEMORY_FILE

        if not memory_file.exists():
            return ""

        try:
            lines = memory_file.read_text(encoding="utf-8").split("\n")

            # Max 200 Zeilen (wie Claude Code)
            truncated = lines[:max_lines]

            if len(lines) > max_lines:
                truncated.append(f"\n... ({len(lines) - max_lines} weitere Zeilen)")

            return "\n".join(truncated)
        except Exception as e:
            print(f"[ContextManager] Fehler beim Laden von MEMORY.md: {e}")
            return ""

    async def append_to_memory_md(
        self,
        project_path: str,
        entry: str,
        category: str = "learned"
    ) -> bool:
        """
        Fügt einen Eintrag zu MEMORY.md hinzu.

        Args:
            project_path: Projektpfad
            entry: Der zu speichernde Text
            category: Kategorie (learned, decision, warning, etc.)

        Returns:
            True bei Erfolg
        """
        try:
            ai_assist_dir = Path(project_path) / ".ai-assist"
            ai_assist_dir.mkdir(parents=True, exist_ok=True)

            memory_file = ai_assist_dir / self.MEMORY_FILE

            timestamp = datetime.now().strftime("%Y-%m-%d")
            formatted_entry = f"\n## [{category}] {timestamp}\n{entry}\n"

            # Append
            with open(memory_file, "a", encoding="utf-8") as f:
                f.write(formatted_entry)

            return True
        except Exception as e:
            print(f"[ContextManager] Fehler beim Schreiben in MEMORY.md: {e}")
            return False

    # ══════════════════════════════════════════════════════════════════════
    # Combined Context Building
    # ══════════════════════════════════════════════════════════════════════

    async def build_full_context(
        self,
        project_path: Optional[str] = None,
        project_id: Optional[str] = None,
        max_tokens: int = 1500
    ) -> str:
        """
        Baut den vollständigen Kontext für LLM-Injection.

        Kombiniert:
        1. Projekt-Kontext (PROJECT_CONTEXT.md)
        2. Globale Präferenzen
        3. MEMORY.md (dynamisch)

        Returns:
            Formatierter Kontext-String
        """
        parts = []
        tokens_used = 0

        # 1. Projekt-Kontext (höchste Priorität)
        project_ctx = await self.load_project_context(project_path, project_id)
        if project_ctx:
            project_prompt = project_ctx.to_prompt(max_tokens=max_tokens // 2)
            if project_prompt:
                parts.append("=== PROJEKT-KONTEXT ===")
                parts.append(project_prompt)
                tokens_used += estimate_tokens(project_prompt)

        # 2. MEMORY.md (dynamisches Wissen)
        remaining = max_tokens - tokens_used
        if remaining > 200 and project_path:
            memory_content = await self.load_memory_md(project_path, max_lines=50)
            if memory_content:
                memory_tokens = estimate_tokens(memory_content)
                if memory_tokens <= remaining - 100:
                    parts.append("\n=== GELERNTES WISSEN ===")
                    parts.append(memory_content)
                    tokens_used += memory_tokens

        # 3. Globale Präferenzen (niedrigste Priorität)
        remaining = max_tokens - tokens_used
        if remaining > 100:
            global_prefs = await self.load_global_preferences()
            if global_prefs:
                prefs_prompt = global_prefs.to_prompt()
                if prefs_prompt and estimate_tokens(prefs_prompt) <= remaining:
                    parts.append("\n" + prefs_prompt)

        return "\n".join(parts) if parts else ""

    def clear_cache(self, project_path: Optional[str] = None) -> None:
        """Leert den Cache (komplett oder für ein Projekt)."""
        if project_path:
            if project_path in self._project_cache:
                del self._project_cache[project_path]
            if project_path in self._cache_timestamps:
                del self._cache_timestamps[project_path]
        else:
            self._project_cache.clear()
            self._global_prefs_cache = None
            self._cache_timestamps.clear()


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_context_manager: Optional[ContextManager] = None


def get_context_manager() -> ContextManager:
    """Gibt Singleton-Instanz zurück."""
    global _context_manager
    if _context_manager is None:
        _context_manager = ContextManager()
    return _context_manager
