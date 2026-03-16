"""
Design Persistence - Speichert Design-Outputs als MD-Dateien.

Features:
- Persistente Speicherung von Brainstorm/Design-Outputs
- Index-basierte Verwaltung
- Implementation-Tracking
- YAML-Frontmatter für Metadaten
"""

import json
import re
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.core.config import settings


# ══════════════════════════════════════════════════════════════════════════════
# Enums und Datenmodelle
# ══════════════════════════════════════════════════════════════════════════════

class DesignType(str, Enum):
    """Typ des Designs."""
    BRAINSTORM = "brainstorm"
    DESIGN = "design"


class DesignStatus(str, Enum):
    """Status des Designs."""
    DRAFT = "draft"
    APPROVED = "approved"
    IMPLEMENTED = "implemented"
    ARCHIVED = "archived"


class ImplementationRef(BaseModel):
    """Referenz zu einer Implementation."""
    file_path: str
    status: str = "pending"
    commit: Optional[str] = None
    updated: Optional[datetime] = None


class DesignMetadata(BaseModel):
    """Metadaten eines gespeicherten Designs."""
    id: str
    type: DesignType
    title: str
    created: datetime
    updated: Optional[datetime] = None
    status: DesignStatus = DesignStatus.DRAFT
    tags: List[str] = []
    sources: List[str] = []
    command: Optional[str] = None
    implementation_refs: List[ImplementationRef] = []


class SavedDesign(BaseModel):
    """Ein vollständig gespeichertes Design."""
    metadata: DesignMetadata
    content: str
    file_path: str


class DesignSummary(BaseModel):
    """Zusammenfassung für Listen."""
    id: str
    type: DesignType
    title: str
    created: datetime
    status: DesignStatus
    tags: List[str]
    file_path: str


class DesignIndex(BaseModel):
    """Index aller gespeicherten Designs."""
    version: str = "1.0"
    updated: datetime = Field(default_factory=datetime.now)
    designs: List[DesignMetadata] = []


# ══════════════════════════════════════════════════════════════════════════════
# Design Persistence Service
# ══════════════════════════════════════════════════════════════════════════════

class DesignPersistence:
    """
    Service für persistente Design-Speicherung.

    Speichert Designs als Markdown-Dateien mit YAML-Frontmatter
    und verwaltet einen Index für schnellen Zugriff.
    """

    def __init__(self, base_path: Optional[Path] = None):
        """
        Initialisiert den Persistence Service.

        Args:
            base_path: Basis-Pfad für Design-Speicherung.
                       Default: docs/designs/
        """
        if base_path:
            self.base_path = Path(base_path)
        else:
            # Versuche settings zu nutzen, sonst Fallback
            try:
                self.base_path = Path(settings.designs.path) if hasattr(settings, 'designs') else Path("docs/designs")
            except:
                self.base_path = Path("docs/designs")

        self._ensure_directories()
        self._index: Optional[DesignIndex] = None

    def _ensure_directories(self):
        """Stellt sicher, dass alle benötigten Verzeichnisse existieren."""
        self.base_path.mkdir(parents=True, exist_ok=True)
        (self.base_path / "brainstorm").mkdir(exist_ok=True)
        (self.base_path / "design").mkdir(exist_ok=True)

    def _get_index_path(self) -> Path:
        """Gibt den Pfad zur Index-Datei zurück."""
        return self.base_path / "index.json"

    def _load_index(self) -> DesignIndex:
        """Lädt den Design-Index."""
        if self._index is not None:
            return self._index

        index_path = self._get_index_path()
        if index_path.exists():
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._index = DesignIndex(**data)
            except Exception:
                self._index = DesignIndex()
        else:
            self._index = DesignIndex()

        return self._index

    def _save_index(self):
        """Speichert den Design-Index."""
        if self._index is None:
            return

        self._index.updated = datetime.now()
        index_path = self._get_index_path()

        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(self._index.model_dump(mode="json"), f, indent=2, default=str)

    def _generate_id(self, design_type: DesignType) -> str:
        """Generiert eine eindeutige Design-ID."""
        date_str = datetime.now().strftime("%Y-%m-%d")
        index = self._load_index()

        # Zähle existierende Designs vom selben Tag
        same_day_count = sum(
            1 for d in index.designs
            if d.id.startswith(f"{design_type.value}-{date_str}")
        )

        return f"{design_type.value}-{date_str}-{same_day_count + 1:03d}"

    def _generate_filename(self, title: str, design_type: DesignType) -> str:
        """Generiert einen Dateinamen aus dem Titel."""
        date_str = datetime.now().strftime("%Y-%m-%d")

        # Titel bereinigen für Dateinamen
        clean_title = re.sub(r'[^\w\s-]', '', title.lower())
        clean_title = re.sub(r'[\s_]+', '-', clean_title)
        clean_title = clean_title[:50]  # Max 50 Zeichen

        return f"{date_str}_{clean_title}.md"

    def _create_frontmatter(self, metadata: DesignMetadata) -> str:
        """Erstellt YAML-Frontmatter für die MD-Datei."""
        lines = [
            "---",
            f"id: {metadata.id}",
            f"type: {metadata.type.value}",
            f"title: \"{metadata.title}\"",
            f"created: {metadata.created.isoformat()}",
            f"status: {metadata.status.value}",
        ]

        if metadata.tags:
            lines.append(f"tags: [{', '.join(metadata.tags)}]")

        if metadata.sources:
            lines.append(f"sources: [{', '.join(metadata.sources)}]")

        if metadata.command:
            lines.append(f"command: {metadata.command}")

        lines.append("---")
        return "\n".join(lines)

    def _parse_frontmatter(self, content: str) -> tuple[Dict[str, Any], str]:
        """Parst YAML-Frontmatter aus Markdown-Content."""
        if not content.startswith("---"):
            return {}, content

        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}, content

        frontmatter_str = parts[1].strip()
        body = parts[2].strip()

        # Einfaches YAML-Parsing
        metadata = {}
        for line in frontmatter_str.split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")

                # Listen parsen
                if value.startswith("[") and value.endswith("]"):
                    value = [v.strip() for v in value[1:-1].split(",") if v.strip()]

                metadata[key] = value

        return metadata, body

    def save(
        self,
        design_type: DesignType,
        title: str,
        content: str,
        tags: Optional[List[str]] = None,
        sources: Optional[List[str]] = None,
        command: Optional[str] = None,
        status: DesignStatus = DesignStatus.DRAFT
    ) -> SavedDesign:
        """
        Speichert ein Design als MD-Datei.

        Args:
            design_type: Typ (brainstorm oder design)
            title: Titel des Designs
            content: Markdown-Inhalt
            tags: Tags für Kategorisierung
            sources: Verwendete Quellen
            command: Ursprünglicher MCP-Command
            status: Status des Designs

        Returns:
            SavedDesign mit Metadaten und Dateipfad
        """
        # ID und Dateiname generieren
        design_id = self._generate_id(design_type)
        filename = self._generate_filename(title, design_type)
        file_path = self.base_path / design_type.value / filename

        # Metadaten erstellen
        metadata = DesignMetadata(
            id=design_id,
            type=design_type,
            title=title,
            created=datetime.now(),
            status=status,
            tags=tags or [],
            sources=sources or [],
            command=command,
            implementation_refs=[]
        )

        # Frontmatter erstellen
        frontmatter = self._create_frontmatter(metadata)

        # Implementation Tracking Section hinzufügen
        tracking_section = """

---

## Implementation Tracking

| Datei | Status | Commit |
|-------|--------|--------|
| - | - | - |

*Wird automatisch aktualisiert bei /implement*
"""

        # Vollständigen Inhalt zusammensetzen
        full_content = f"{frontmatter}\n\n{content}{tracking_section}"

        # Datei schreiben
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(full_content)

        # Index aktualisieren
        index = self._load_index()
        index.designs.append(metadata)
        self._save_index()

        return SavedDesign(
            metadata=metadata,
            content=content,
            file_path=str(file_path.relative_to(self.base_path.parent) if self.base_path.parent.exists() else file_path)
        )

    def list_designs(
        self,
        design_type: Optional[DesignType] = None,
        status: Optional[DesignStatus] = None,
        tag: Optional[str] = None,
        limit: int = 50
    ) -> List[DesignSummary]:
        """
        Listet gespeicherte Designs.

        Args:
            design_type: Filter nach Typ
            status: Filter nach Status
            tag: Filter nach Tag
            limit: Maximale Anzahl

        Returns:
            Liste von Design-Zusammenfassungen
        """
        index = self._load_index()
        designs = index.designs

        # Filter anwenden
        if design_type:
            designs = [d for d in designs if d.type == design_type]

        if status:
            designs = [d for d in designs if d.status == status]

        if tag:
            designs = [d for d in designs if tag in d.tags]

        # Nach Erstellungsdatum sortieren (neueste zuerst)
        designs = sorted(designs, key=lambda d: d.created, reverse=True)

        # Limit anwenden
        designs = designs[:limit]

        # Zu Summaries konvertieren
        return [
            DesignSummary(
                id=d.id,
                type=d.type,
                title=d.title,
                created=d.created,
                status=d.status,
                tags=d.tags,
                file_path=f"{d.type.value}/{self._generate_filename(d.title, d.type)}"
            )
            for d in designs
        ]

    def get_design(self, design_id: str) -> Optional[SavedDesign]:
        """
        Lädt ein Design anhand seiner ID.

        Args:
            design_id: Die Design-ID

        Returns:
            SavedDesign oder None wenn nicht gefunden
        """
        index = self._load_index()

        # Metadaten finden
        metadata = next((d for d in index.designs if d.id == design_id), None)
        if not metadata:
            return None

        # Datei finden und lesen
        filename = self._generate_filename(metadata.title, metadata.type)
        file_path = self.base_path / metadata.type.value / filename

        if not file_path.exists():
            # Fallback: Suche nach Datei mit der ID
            for f in (self.base_path / metadata.type.value).glob("*.md"):
                content = f.read_text(encoding="utf-8")
                if f"id: {design_id}" in content:
                    file_path = f
                    break
            else:
                return None

        content = file_path.read_text(encoding="utf-8")
        _, body = self._parse_frontmatter(content)

        return SavedDesign(
            metadata=metadata,
            content=body,
            file_path=str(file_path)
        )

    def update_status(self, design_id: str, status: DesignStatus) -> bool:
        """
        Aktualisiert den Status eines Designs.

        Args:
            design_id: Die Design-ID
            status: Neuer Status

        Returns:
            True bei Erfolg
        """
        index = self._load_index()

        for design in index.designs:
            if design.id == design_id:
                design.status = status
                design.updated = datetime.now()
                self._save_index()

                # Auch die Datei aktualisieren
                saved = self.get_design(design_id)
                if saved:
                    self._update_file_status(saved.file_path, status)

                return True

        return False

    def _update_file_status(self, file_path: str, status: DesignStatus):
        """Aktualisiert den Status in der MD-Datei."""
        path = Path(file_path)
        if not path.exists():
            return

        content = path.read_text(encoding="utf-8")

        # Status im Frontmatter ersetzen
        content = re.sub(
            r'^status: \w+',
            f'status: {status.value}',
            content,
            flags=re.MULTILINE
        )

        path.write_text(content, encoding="utf-8")

    def link_implementation(
        self,
        design_id: str,
        files: List[str],
        commit: Optional[str] = None
    ) -> bool:
        """
        Verknüpft Implementations-Dateien mit einem Design.

        Args:
            design_id: Die Design-ID
            files: Liste der implementierten Dateien
            commit: Optionaler Commit-Hash

        Returns:
            True bei Erfolg
        """
        index = self._load_index()

        for design in index.designs:
            if design.id == design_id:
                # Neue Referenzen hinzufügen
                for file_path in files:
                    ref = ImplementationRef(
                        file_path=file_path,
                        status="implemented",
                        commit=commit,
                        updated=datetime.now()
                    )

                    # Prüfen ob schon vorhanden
                    existing = next(
                        (r for r in design.implementation_refs if r.file_path == file_path),
                        None
                    )
                    if existing:
                        existing.status = "implemented"
                        existing.commit = commit
                        existing.updated = datetime.now()
                    else:
                        design.implementation_refs.append(ref)

                design.updated = datetime.now()
                self._save_index()

                # Tracking-Tabelle in der Datei aktualisieren
                saved = self.get_design(design_id)
                if saved:
                    self._update_tracking_table(saved.file_path, design.implementation_refs)

                return True

        return False

    def _update_tracking_table(self, file_path: str, refs: List[ImplementationRef]):
        """Aktualisiert die Implementation-Tracking-Tabelle in der MD-Datei."""
        path = Path(file_path)
        if not path.exists():
            return

        content = path.read_text(encoding="utf-8")

        # Neue Tabelle erstellen
        table_rows = ["| Datei | Status | Commit |", "|-------|--------|--------|"]
        for ref in refs:
            commit_str = ref.commit[:7] if ref.commit else "-"
            table_rows.append(f"| {ref.file_path} | {ref.status} | {commit_str} |")

        if not refs:
            table_rows.append("| - | - | - |")

        new_table = "\n".join(table_rows)

        # Alte Tabelle ersetzen
        pattern = r'## Implementation Tracking\n\n\|.*?\n\|.*?\n(\|.*?\n)*'
        replacement = f"## Implementation Tracking\n\n{new_table}\n"

        content = re.sub(pattern, replacement, content)

        path.write_text(content, encoding="utf-8")

    def get_stats(self) -> Dict[str, Any]:
        """Gibt Statistiken über gespeicherte Designs zurück."""
        index = self._load_index()

        return {
            "total": len(index.designs),
            "by_type": {
                "brainstorm": sum(1 for d in index.designs if d.type == DesignType.BRAINSTORM),
                "design": sum(1 for d in index.designs if d.type == DesignType.DESIGN)
            },
            "by_status": {
                status.value: sum(1 for d in index.designs if d.status == status)
                for status in DesignStatus
            },
            "last_updated": index.updated.isoformat() if index.updated else None
        }


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_design_persistence: Optional[DesignPersistence] = None


def get_design_persistence() -> DesignPersistence:
    """Gibt die Singleton-Instanz des DesignPersistence Service zurück."""
    global _design_persistence
    if _design_persistence is None:
        _design_persistence = DesignPersistence()
    return _design_persistence
