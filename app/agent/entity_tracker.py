"""
Entity Tracker - Verfolgt Entitäten (Klassen, Services, Dateipfade) über Tool-Results hinweg.

Speichert Quell-Mappings: Welche Entität wurde in welcher Quelle gefunden?
Wird verwendet um:
1. Cross-Source-Querverweise aufzubauen (Java ↔ Handbuch ↔ PDF)
2. Proaktive Anreicherung zu steuern (welche Quelle fehlt noch?)
3. Session-Kontext über viele Turns zu erhalten
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# Patterns für relevante Entitäten
ENTITY_PATTERNS = [
    # Java/Python Klassen- und Service-Namen (CamelCase, mindestens 3 Buchstaben)
    r'\b[A-Z][a-zA-Z]{2,}(?:Service|Controller|Manager|Client|Handler|Repository|Bean|Impl|Factory|Adapter|Facade)?\b',
    # Datei-Pfade mit Endung
    r'[\w/\\-]+\.(?:java|py|xml|yaml|yml|properties|json)\b',
    # Package-Pfade
    r'\b[a-z]+(?:\.[a-z]+){2,}\b',
]

# Tool-Name → Quell-Typ Mapping
TOOL_TO_SOURCE = {
    "search_code": "java",
    "read_file": "java",
    "list_files": "java",
    "search_handbook": "handbuch",
    "get_service_info": "handbuch",
    "search_skills": "skill",
    "search_pdfs": "pdf",
}

# Entitäten die zu allgemein sind (ignorieren)
IGNORE_ENTITIES = {
    "String", "List", "Map", "Set", "Optional", "Integer", "Boolean",
    "Object", "Class", "Type", "Value", "Result", "Data", "Info",
    "Error", "Exception", "Logger", "Override", "Autowired",
    "Service", "Controller", "Manager", "Repository", "Bean",
    "True", "False", "None", "Null", "This", "Self",
}


@dataclass
class EntityEntry:
    """Eine bekannte Entität mit ihren Quell-Mappings."""
    name: str
    sources: Dict[str, str] = field(default_factory=dict)
    # Beispiel: {"java": "src/services/OrderService.java", "handbuch": "Bestellservice"}
    mention_count: int = 0
    first_seen_tool: str = ""


class EntityTracker:
    """
    Verfolgt Entitäten über Tool-Results in einer Session.

    Für jede gefundene Entität wird gespeichert:
    - In welcher Quelle sie gefunden wurde (java, handbuch, pdf, skill)
    - Wie oft sie erwähnt wurde
    - Welche Quellen noch fehlen (für proaktive Anreicherung)
    """

    def __init__(self):
        self.entities: Dict[str, EntityEntry] = {}

    def extract_from_tool_result(
        self,
        tool_name: str,
        result_text: str,
        source_path: str = ""
    ) -> List[str]:
        """
        Extrahiert Entitäten aus einem Tool-Result und speichert das Quell-Mapping.

        Args:
            tool_name: Name des ausgeführten Tools (z.B. "search_code")
            result_text: Inhalt des Tool-Results
            source_path: Pfad/Name der Quelle (z.B. Dateipfad oder Query)

        Returns:
            Liste neu gefundener Entitäten
        """
        source_type = TOOL_TO_SOURCE.get(tool_name, "")
        new_entities = []

        for pattern in ENTITY_PATTERNS:
            for match in re.findall(pattern, result_text):
                # Ignoriere zu allgemeine oder zu kurze Entitäten
                if match in IGNORE_ENTITIES or len(match) < 4:
                    continue
                # Ignoriere rein numerische oder Pfad-only Matches
                if match.isdigit():
                    continue

                if match not in self.entities:
                    self.entities[match] = EntityEntry(
                        name=match,
                        first_seen_tool=tool_name
                    )
                    new_entities.append(match)

                entry = self.entities[match]
                entry.mention_count += 1

                # Quell-Mapping speichern
                if source_type and match not in entry.sources.get(source_type, ""):
                    # Datei-Pfade direkt speichern, sonst Query als Kontext
                    if source_path and ("/" in source_path or "\\" in source_path or source_path.endswith((".java", ".py"))):
                        entry.sources[source_type] = source_path
                    elif source_type not in entry.sources:
                        entry.sources[source_type] = match  # Entitätsname als Referenz

        return new_entities

    def get_entities_for_enrichment(
        self,
        last_tool_name: str,
        result_text: str
    ) -> List[EntityEntry]:
        """
        Gibt Entitäten zurück die noch nicht in allen relevanten Quellen gesucht wurden.

        Verwendet für proaktive Cross-Source-Anreicherung:
        - Wenn search_code Java-Klasse findet → Entitäten ohne Handbuch-Eintrag
        - Wenn search_handbook findet → Entitäten ohne Java-Eintrag

        Args:
            last_tool_name: Zuletzt ausgeführtes Tool
            result_text: Letztes Tool-Result (für Relevanz-Filter)

        Returns:
            Liste von Entitäten die angereichert werden sollten
        """
        result_lower = result_text.lower()
        candidates = []

        for entry in self.entities.values():
            # Nur Entitäten die im aktuellen Result vorkommen
            if entry.name.lower() not in result_lower:
                continue
            # Nur Entitäten die mindestens einmal gesehen wurden
            if entry.mention_count < 1:
                continue

            source_type = TOOL_TO_SOURCE.get(last_tool_name, "")

            # Java → Handbuch: Klasse gefunden aber noch kein Handbuch-Eintrag
            if source_type == "java" and "handbuch" not in entry.sources:
                candidates.append(entry)

            # Handbuch → Java: Service gefunden aber noch kein Java-Code
            elif source_type == "handbuch" and "java" not in entry.sources:
                candidates.append(entry)

        # Sortiere nach mention_count (häufiger = relevanter), max 3
        candidates.sort(key=lambda e: e.mention_count, reverse=True)
        return candidates[:3]

    def get_entities_matching_query(self, query: str) -> List[EntityEntry]:
        """Gibt bekannte Entitäten zurück die im Query-Text vorkommen."""
        query_lower = query.lower()
        return [
            e for e in self.entities.values()
            if e.name.lower() in query_lower or query_lower in e.name.lower()
        ]

    def get_context_hint(self) -> str:
        """
        Kompakter Kontext-Hinweis für den System-Prompt.

        Listet bekannte Entitäten mit ihren Quell-Mappings auf.
        Wird bei jedem LLM-Call als Kontext eingefügt.
        """
        if not self.entities:
            return ""

        # Nur Entitäten mit Quell-Mappings anzeigen (sonst zu viel Rauschen)
        mapped = [e for e in self.entities.values() if e.sources]
        if not mapped:
            return ""

        # Sortiere nach mention_count
        mapped.sort(key=lambda e: e.mention_count, reverse=True)

        lines = ["=== BEKANNTE ENTITÄTEN (diese Session) ==="]
        for entry in mapped[:12]:  # Max 12 Einträge
            src_str = ", ".join(f"{k}: {v}" for k, v in entry.sources.items())
            lines.append(f"• {entry.name}: {src_str}")
        lines.append("=== ENDE ENTITÄTEN ===")

        return "\n".join(lines)

    def clear(self):
        """Leert alle Entitäten (bei Session-Reset)."""
        self.entities.clear()

    def track_source(
        self,
        source_type: str,
        source_id: str,
        source_title: str
    ) -> None:
        """
        Speichert eine explizite Quellen-Referenz.

        Wird von ResultValidator aufgerufen wenn Source-Metadata
        aus Tool-Ergebnissen extrahiert wurde.

        Args:
            source_type: Art der Quelle (confluence, code, jira, etc.)
            source_id: Eindeutige ID (Page-ID, Dateipfad, Ticket-Key)
            source_title: Lesbare Bezeichnung
        """
        # Verwende source_title als Entitätsname
        entity_name = source_title[:50] if source_title else source_id

        if entity_name not in self.entities:
            self.entities[entity_name] = EntityEntry(
                name=entity_name,
                first_seen_tool=f"source:{source_type}"
            )

        entry = self.entities[entity_name]
        entry.mention_count += 1
        entry.sources[source_type] = source_id

    @property
    def entity_count(self) -> int:
        return len(self.entities)
