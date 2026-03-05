"""
Skill Manager - Verwaltung von Skills und deren Wissensbasen.

Features:
- Skills aus YAML-Dateien laden
- Wissensquellen indexieren (FTS5)
- Skill-Aktivierung pro Session
- PDF-zu-Skill Transformation
"""

import json
import re
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set
import os

from app.models.skill import (
    Skill,
    SkillType,
    ActivationMode,
    KnowledgeSource,
    KnowledgeSourceType,
    SkillSummary,
    SkillDetail,
    SkillSearchResult,
)


def slugify(text: str) -> str:
    """Konvertiert Text in einen URL-freundlichen Slug."""
    text = text.lower()
    text = re.sub(r'[äÄ]', 'ae', text)
    text = re.sub(r'[öÖ]', 'oe', text)
    text = re.sub(r'[üÜ]', 'ue', text)
    text = re.sub(r'[ß]', 'ss', text)
    text = re.sub(r'[^a-z0-9]+', '-', text)
    text = text.strip('-')
    return text


class SkillManager:
    """
    Verwaltet Skills: Laden, Aktivieren, Wissenssuche.
    """

    def __init__(
        self,
        skills_dir: str = "./skills",
        db_path: str = "./index/skills_index.db"
    ):
        self.skills_dir = Path(skills_dir)
        self.db_path = Path(db_path)
        self._skills: Dict[str, Skill] = {}
        self._active_skills: Dict[str, Set[str]] = {}  # session_id -> skill_ids

        # Verzeichnisse erstellen
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        (self.skills_dir / "data").mkdir(exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_db()
        self._load_skills()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path))
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        """Initialisiert die Skill-Datenbank."""
        with self._connect() as con:
            con.executescript("""
                -- Skill-Metadaten
                CREATE TABLE IF NOT EXISTS skills (
                    skill_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    type TEXT NOT NULL,
                    activation_mode TEXT NOT NULL,
                    trigger_words_json TEXT,
                    system_prompt TEXT,
                    file_path TEXT,
                    metadata_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                -- Wissens-Chunks (FTS5)
                CREATE VIRTUAL TABLE IF NOT EXISTS skill_knowledge_fts USING fts5(
                    skill_id UNINDEXED,
                    chunk_id UNINDEXED,
                    source_path UNINDEXED,
                    source_type UNINDEXED,
                    content,
                    tokenize='porter unicode61'
                );

                -- Index-Metadaten
                CREATE TABLE IF NOT EXISTS skill_index_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
            """)

    def _load_skills(self) -> None:
        """Lädt alle Skill-Definitionen aus dem Skills-Verzeichnis."""
        for yaml_file in self.skills_dir.glob("*.yaml"):
            try:
                skill = Skill.from_yaml(yaml_file)
                self._skills[skill.id] = skill
                self._sync_skill_to_db(skill)
            except Exception as e:
                print(f"[SkillManager] Fehler beim Laden von {yaml_file.name}: {e}")

        for yml_file in self.skills_dir.glob("*.yml"):
            if yml_file.stem not in self._skills:
                try:
                    skill = Skill.from_yaml(yml_file)
                    self._skills[skill.id] = skill
                    self._sync_skill_to_db(skill)
                except Exception as e:
                    print(f"[SkillManager] Fehler beim Laden von {yml_file.name}: {e}")

        print(f"[SkillManager] {len(self._skills)} Skills geladen")

    def _sync_skill_to_db(self, skill: Skill) -> None:
        """Synchronisiert Skill-Metadaten in die Datenbank."""
        with self._connect() as con:
            con.execute(
                """INSERT OR REPLACE INTO skills
                   (skill_id, name, description, type, activation_mode,
                    trigger_words_json, system_prompt, file_path, metadata_json, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    skill.id,
                    skill.name,
                    skill.description,
                    skill.type,
                    skill.activation.mode,
                    json.dumps(skill.activation.trigger_words, ensure_ascii=False),
                    skill.system_prompt,
                    str(skill._file_path) if skill._file_path else None,
                    json.dumps(skill.metadata.model_dump(), ensure_ascii=False, default=str),
                    datetime.now().isoformat()
                )
            )

    # ══════════════════════════════════════════════════════════════════════════
    # CRUD Operations
    # ══════════════════════════════════════════════════════════════════════════

    def get_skill(self, skill_id: str) -> Optional[Skill]:
        """Gibt einen Skill zurück."""
        return self._skills.get(skill_id)

    def list_skills(self, session_id: Optional[str] = None) -> List[SkillSummary]:
        """Listet alle Skills auf."""
        active_ids = self._active_skills.get(session_id, set()) if session_id else set()

        return [
            SkillSummary(
                id=s.id,
                name=s.name,
                description=s.description,
                type=s.type,
                activation_mode=s.activation.mode,
                has_knowledge=s.has_knowledge(),
                has_prompt=s.has_prompt(),
                is_active=s.id in active_ids,
                tags=s.metadata.tags
            )
            for s in sorted(self._skills.values(), key=lambda x: x.name)
        ]

    def get_skill_detail(self, skill_id: str, session_id: Optional[str] = None) -> Optional[SkillDetail]:
        """Gibt detaillierte Skill-Informationen zurück."""
        skill = self._skills.get(skill_id)
        if not skill:
            return None

        active_ids = self._active_skills.get(session_id, set()) if session_id else set()

        return SkillDetail(
            id=skill.id,
            name=skill.name,
            description=skill.description,
            version=skill.version,
            type=skill.type,
            activation=skill.activation.model_dump(),
            system_prompt=skill.system_prompt,
            knowledge_sources=[ks.model_dump() for ks in skill.knowledge_sources],
            tools=[t.model_dump() for t in skill.tools],
            metadata=skill.metadata.model_dump(),
            is_active=skill.id in active_ids
        )

    def create_skill(
        self,
        name: str,
        description: str = "",
        skill_type: SkillType = SkillType.KNOWLEDGE,
        activation_mode: ActivationMode = ActivationMode.ON_DEMAND,
        trigger_words: Optional[List[str]] = None,
        system_prompt: Optional[str] = None,
        knowledge_sources: Optional[List[KnowledgeSource]] = None,
        tags: Optional[List[str]] = None,
    ) -> Skill:
        """Erstellt einen neuen Skill."""
        from app.models.skill import SkillActivation, SkillMetadata

        skill_id = slugify(name)

        # ID-Kollision vermeiden
        counter = 1
        original_id = skill_id
        while skill_id in self._skills:
            skill_id = f"{original_id}-{counter}"
            counter += 1

        skill = Skill(
            id=skill_id,
            name=name,
            description=description,
            type=skill_type,
            activation=SkillActivation(
                mode=activation_mode,
                trigger_words=trigger_words or []
            ),
            system_prompt=system_prompt,
            knowledge_sources=knowledge_sources or [],
            metadata=SkillMetadata(
                author="user",
                created=datetime.now(),
                tags=tags or []
            )
        )

        # YAML speichern
        yaml_path = self.skills_dir / f"{skill_id}.yaml"
        skill._file_path = yaml_path
        skill.to_yaml(yaml_path)

        # In Memory und DB laden
        self._skills[skill_id] = skill
        self._sync_skill_to_db(skill)

        # Wissensquellen indexieren
        if skill.has_knowledge():
            self._index_skill_knowledge(skill)

        return skill

    def update_skill(self, skill_id: str, **updates) -> Optional[Skill]:
        """Aktualisiert einen Skill."""
        skill = self._skills.get(skill_id)
        if not skill:
            return None

        # Felder aktualisieren
        for key, value in updates.items():
            if hasattr(skill, key):
                setattr(skill, key, value)

        skill.metadata.updated = datetime.now()

        # Speichern
        skill.to_yaml()
        self._sync_skill_to_db(skill)

        # Bei Wissensänderungen neu indexieren
        if "knowledge_sources" in updates:
            self._clear_skill_knowledge(skill_id)
            self._index_skill_knowledge(skill)

        return skill

    def delete_skill(self, skill_id: str) -> bool:
        """Löscht einen Skill."""
        skill = self._skills.get(skill_id)
        if not skill:
            return False

        # YAML löschen
        if skill._file_path and skill._file_path.exists():
            skill._file_path.unlink()

        # Aus DB entfernen
        with self._connect() as con:
            con.execute("DELETE FROM skills WHERE skill_id=?", (skill_id,))
            con.execute("DELETE FROM skill_knowledge_fts WHERE skill_id=?", (skill_id,))

        # Aus Memory entfernen
        del self._skills[skill_id]

        # Aus aktiven Sessions entfernen
        for session_skills in self._active_skills.values():
            session_skills.discard(skill_id)

        return True

    # ══════════════════════════════════════════════════════════════════════════
    # Knowledge Indexing
    # ══════════════════════════════════════════════════════════════════════════

    def _index_skill_knowledge(self, skill: Skill) -> int:
        """Indexiert alle Wissensquellen eines Skills."""
        total_chunks = 0

        for source in skill.knowledge_sources:
            try:
                chunks = self._extract_and_chunk_source(skill, source)
                total_chunks += len(chunks)

                with self._connect() as con:
                    for i, chunk in enumerate(chunks):
                        chunk_id = f"{skill.id}_{source.type}_{i}"
                        con.execute(
                            """INSERT INTO skill_knowledge_fts
                               (skill_id, chunk_id, source_path, source_type, content)
                               VALUES (?, ?, ?, ?, ?)""",
                            (skill.id, chunk_id, source.path or "inline", source.type, chunk)
                        )
            except Exception as e:
                print(f"[SkillManager] Fehler beim Indexieren von {source.path}: {e}")

        return total_chunks

    def _extract_and_chunk_source(
        self,
        skill: Skill,
        source: KnowledgeSource
    ) -> List[str]:
        """Extrahiert Text aus einer Quelle und teilt ihn in Chunks."""
        text = self._extract_source_text(skill, source)
        if not text:
            return []

        return self._split_into_chunks(
            text,
            chunk_size=source.chunk_size,
            overlap=source.chunk_overlap
        )

    def _extract_source_text(self, skill: Skill, source: KnowledgeSource) -> str:
        """Extrahiert Text aus einer Wissensquelle."""
        if source.type == KnowledgeSourceType.TEXT:
            return source.content or ""

        if not source.path:
            return ""

        # Pfad relativ zum Skills-Verzeichnis oder absolut
        if Path(source.path).is_absolute():
            file_path = Path(source.path)
        else:
            file_path = self.skills_dir / source.path

        if not file_path.exists():
            # Versuche auch relativ zum Projekt-Root
            file_path = Path(source.path)
            if not file_path.exists():
                print(f"[SkillManager] Quelle nicht gefunden: {source.path}")
                return ""

        if source.type == KnowledgeSourceType.PDF:
            return self._extract_pdf_text(file_path)
        elif source.type in (KnowledgeSourceType.MARKDOWN, KnowledgeSourceType.HTML):
            return file_path.read_text(encoding="utf-8", errors="replace")
        else:
            return file_path.read_text(encoding="utf-8", errors="replace")

    def _extract_pdf_text(self, pdf_path: Path) -> str:
        """Extrahiert Text aus einer PDF-Datei."""
        try:
            import pdfplumber
            text_parts = []
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
            return "\n\n".join(text_parts)
        except Exception as e:
            print(f"[SkillManager] PDF-Extraktion fehlgeschlagen: {e}")
            return ""

    def _split_into_chunks(
        self,
        text: str,
        chunk_size: int = 1000,
        overlap: int = 100
    ) -> List[str]:
        """Teilt Text in überlappende Chunks."""
        # Einfache Implementierung: Nach Zeichen mit Satzgrenzen
        sentences = re.split(r'(?<=[.!?])\s+', text)
        chunks = []
        current_chunk = []
        current_length = 0

        for sentence in sentences:
            sentence_length = len(sentence)

            if current_length + sentence_length > chunk_size and current_chunk:
                # Chunk abschließen
                chunks.append(" ".join(current_chunk))

                # Overlap: Letzte Sätze behalten
                overlap_text = " ".join(current_chunk)
                while len(overlap_text) > overlap and current_chunk:
                    current_chunk.pop(0)
                    overlap_text = " ".join(current_chunk)

                current_length = len(overlap_text)

            current_chunk.append(sentence)
            current_length += sentence_length

        # Letzten Chunk hinzufügen
        if current_chunk:
            chunks.append(" ".join(current_chunk))

        return chunks

    def _clear_skill_knowledge(self, skill_id: str) -> None:
        """Löscht indexierte Wissens-Chunks eines Skills."""
        with self._connect() as con:
            con.execute("DELETE FROM skill_knowledge_fts WHERE skill_id=?", (skill_id,))

    def reindex_skill(self, skill_id: str) -> int:
        """Indiziert einen Skill neu."""
        skill = self._skills.get(skill_id)
        if not skill:
            return 0

        self._clear_skill_knowledge(skill_id)
        return self._index_skill_knowledge(skill)

    # ══════════════════════════════════════════════════════════════════════════
    # Knowledge Search
    # ══════════════════════════════════════════════════════════════════════════

    def search_knowledge(
        self,
        query: str,
        skill_ids: Optional[List[str]] = None,
        top_k: int = 5
    ) -> List[SkillSearchResult]:
        """
        Durchsucht die Wissensbasen der angegebenen Skills.

        Args:
            query: Suchbegriff
            skill_ids: Skills in denen gesucht werden soll (None = alle)
            top_k: Maximale Anzahl Ergebnisse
        """
        if not query.strip():
            return []

        safe_query = query.replace('"', '""')

        with self._connect() as con:
            if skill_ids:
                placeholders = ",".join("?" * len(skill_ids))
                sql = f"""
                    SELECT skill_id, source_path,
                           snippet(skill_knowledge_fts, 4, '>>>', '<<<', '...', 30) AS snippet,
                           rank
                    FROM skill_knowledge_fts
                    WHERE skill_id IN ({placeholders})
                      AND skill_knowledge_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """
                params = (*skill_ids, safe_query, top_k)
            else:
                sql = """
                    SELECT skill_id, source_path,
                           snippet(skill_knowledge_fts, 4, '>>>', '<<<', '...', 30) AS snippet,
                           rank
                    FROM skill_knowledge_fts
                    WHERE skill_knowledge_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """
                params = (safe_query, top_k)

            try:
                rows = con.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                # Fallback: LIKE-Suche
                like = f"%{query}%"
                rows = con.execute(
                    """SELECT skill_id, source_path,
                              substr(content, 1, 200) AS snippet, 0 AS rank
                       FROM skill_knowledge_fts
                       WHERE content LIKE ?
                       LIMIT ?""",
                    (like, top_k)
                ).fetchall()

        results = []
        for row in rows:
            skill = self._skills.get(row["skill_id"])
            results.append(SkillSearchResult(
                skill_id=row["skill_id"],
                skill_name=skill.name if skill else row["skill_id"],
                source_path=row["source_path"],
                snippet=row["snippet"],
                rank=row["rank"]
            ))

        return results

    # ══════════════════════════════════════════════════════════════════════════
    # Skill Activation
    # ══════════════════════════════════════════════════════════════════════════

    def activate_skill(self, session_id: str, skill_id: str) -> bool:
        """Aktiviert einen Skill für eine Session."""
        if skill_id not in self._skills:
            return False

        if session_id not in self._active_skills:
            self._active_skills[session_id] = set()

        # Max. aktive Skills prüfen
        from app.core.config import settings
        max_active = settings.skills.max_active_skills
        if len(self._active_skills[session_id]) >= max_active:
            return False

        self._active_skills[session_id].add(skill_id)
        return True

    def deactivate_skill(self, session_id: str, skill_id: str) -> bool:
        """Deaktiviert einen Skill für eine Session."""
        if session_id not in self._active_skills:
            return False

        self._active_skills[session_id].discard(skill_id)
        return True

    def get_active_skills(self, session_id: str) -> List[Skill]:
        """Gibt alle aktiven Skills einer Session zurück."""
        if session_id not in self._active_skills:
            return []

        return [
            self._skills[sid]
            for sid in self._active_skills[session_id]
            if sid in self._skills
        ]

    def get_active_skill_ids(self, session_id: str) -> Set[str]:
        """Gibt die IDs der aktiven Skills zurück."""
        return self._active_skills.get(session_id, set()).copy()

    def set_active_skills(self, session_id: str, skill_ids: List[str]) -> None:
        """Setzt die aktiven Skills für eine Session."""
        valid_ids = {sid for sid in skill_ids if sid in self._skills}
        self._active_skills[session_id] = valid_ids

    def clear_session(self, session_id: str) -> None:
        """Löscht alle aktiven Skills einer Session."""
        self._active_skills.pop(session_id, None)

    # ══════════════════════════════════════════════════════════════════════════
    # System Prompt Building
    # ══════════════════════════════════════════════════════════════════════════

    def build_system_prompt(self, session_id: str) -> str:
        """
        Baut den kombinierten System-Prompt aus allen aktiven Skills.
        """
        active_skills = self.get_active_skills(session_id)
        if not active_skills:
            return ""

        prompts = []
        for skill in active_skills:
            if skill.system_prompt:
                prompts.append(f"=== Skill: {skill.name} ===\n{skill.system_prompt}")

        if not prompts:
            return ""

        return "\n\n".join(prompts)

    def get_knowledge_context(
        self,
        session_id: str,
        query: str,
        top_k: int = 5
    ) -> str:
        """
        Sucht in den Wissensbasen der aktiven Skills und gibt Kontext zurück.
        """
        active_ids = list(self.get_active_skill_ids(session_id))
        if not active_ids:
            return ""

        results = self.search_knowledge(query, skill_ids=active_ids, top_k=top_k)
        if not results:
            return ""

        context_parts = ["=== Relevantes Wissen aus Skills ==="]
        for r in results:
            context_parts.append(f"\n[{r.skill_name}]\n{r.snippet}")

        return "\n".join(context_parts)

    # ══════════════════════════════════════════════════════════════════════════
    # PDF to Skill
    # ══════════════════════════════════════════════════════════════════════════

    async def create_skill_from_pdf(
        self,
        pdf_path: str,
        name: str,
        description: str = "",
        trigger_words: Optional[List[str]] = None,
        system_prompt: str = "Beantworte Fragen basierend auf dem folgenden Dokument.",
        chunk_size: int = 1000,
        chunk_overlap: int = 100,
        selected_pages: Optional[List[int]] = None,
    ) -> Skill:
        """
        Erstellt einen neuen Skill aus einer PDF-Datei.

        Args:
            pdf_path: Pfad zur PDF-Datei
            name: Name des neuen Skills
            description: Beschreibung
            trigger_words: Wörter für Auto-Aktivierung
            system_prompt: System-Prompt für den Skill
            chunk_size: Tokens pro Chunk
            chunk_overlap: Überlappung zwischen Chunks
            selected_pages: Nur bestimmte Seiten verwenden (1-basiert)
        """
        pdf_file = Path(pdf_path)
        if not pdf_file.exists():
            raise FileNotFoundError(f"PDF nicht gefunden: {pdf_path}")

        skill_id = slugify(name)

        # ID-Kollision vermeiden
        counter = 1
        original_id = skill_id
        while skill_id in self._skills:
            skill_id = f"{original_id}-{counter}"
            counter += 1

        # PDF in skills/data/ kopieren
        data_dir = self.skills_dir / "data"
        data_dir.mkdir(exist_ok=True)
        pdf_dest = data_dir / f"{skill_id}.pdf"
        shutil.copy(pdf_file, pdf_dest)

        # Text extrahieren (ggf. nur ausgewählte Seiten)
        text = self._extract_pdf_with_pages(pdf_dest, selected_pages)

        # Skill erstellen
        skill = self.create_skill(
            name=name,
            description=description,
            skill_type=SkillType.KNOWLEDGE,
            activation_mode=ActivationMode.ON_DEMAND,
            trigger_words=trigger_words,
            system_prompt=system_prompt,
            knowledge_sources=[
                KnowledgeSource(
                    type=KnowledgeSourceType.PDF,
                    path=f"data/{skill_id}.pdf",
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap
                )
            ],
            tags=["pdf", "dokument"]
        )

        # Metadaten ergänzen
        skill.metadata.source_file = pdf_file.name

        return skill

    def _extract_pdf_with_pages(
        self,
        pdf_path: Path,
        selected_pages: Optional[List[int]] = None
    ) -> str:
        """Extrahiert Text aus PDF, optional nur bestimmte Seiten."""
        try:
            import pdfplumber
            text_parts = []
            with pdfplumber.open(pdf_path) as pdf:
                for i, page in enumerate(pdf.pages, start=1):
                    if selected_pages and i not in selected_pages:
                        continue
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(f"--- Seite {i} ---\n{page_text}")
            return "\n\n".join(text_parts)
        except Exception as e:
            print(f"[SkillManager] PDF-Extraktion fehlgeschlagen: {e}")
            return ""

    # ══════════════════════════════════════════════════════════════════════════
    # Statistics
    # ══════════════════════════════════════════════════════════════════════════

    def get_stats(self) -> Dict:
        """Gibt Statistiken über Skills zurück."""
        with self._connect() as con:
            chunk_count = con.execute(
                "SELECT COUNT(*) FROM skill_knowledge_fts"
            ).fetchone()[0]

        return {
            "total_skills": len(self._skills),
            "skills_with_knowledge": sum(1 for s in self._skills.values() if s.has_knowledge()),
            "skills_with_prompt": sum(1 for s in self._skills.values() if s.has_prompt()),
            "total_knowledge_chunks": chunk_count,
            "active_sessions": len(self._active_skills),
        }


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_skill_manager: Optional[SkillManager] = None


def get_skill_manager() -> SkillManager:
    """Gibt die Singleton-Instanz des SkillManagers zurück."""
    global _skill_manager
    if _skill_manager is None:
        from app.core.config import settings
        _skill_manager = SkillManager(
            skills_dir=settings.skills.directory,
            db_path=str(Path(settings.index.directory) / "skills_index.db")
        )
    return _skill_manager
