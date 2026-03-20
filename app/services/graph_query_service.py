"""
Graph Query Service.

Zentrale API für intelligente Graph-Abfragen:
- Impact-Analyse (Was ist betroffen wenn X ändert?)
- Kontext-Anreicherung (Umgebung eines Elements)
- Pfad-Finder (Wie hängen A und B zusammen?)
- Smart-Search (Natürlichsprachliche Suche)
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
from pathlib import Path

from app.services.knowledge_graph import (
    get_knowledge_graph_store,
    KnowledgeGraphStore,
    GraphNode,
    GraphEdge,
    NodeType,
    EdgeType,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Result Models
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ImpactResult:
    """Ergebnis einer Impact-Analyse."""
    target_id: str
    target_name: str
    target_type: str
    direct_impacts: List[Dict[str, Any]] = field(default_factory=list)
    transitive_impacts: List[Dict[str, Any]] = field(default_factory=list)
    affected_files: List[str] = field(default_factory=list)
    risk_score: float = 0.0  # 0.0-1.0
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_id": self.target_id,
            "target_name": self.target_name,
            "target_type": self.target_type,
            "direct_impacts": self.direct_impacts,
            "transitive_impacts": self.transitive_impacts,
            "affected_files": self.affected_files,
            "risk_score": self.risk_score,
            "summary": self.summary,
        }


@dataclass
class ContextResult:
    """Kontext-Informationen für ein Code-Element."""
    node_id: str
    node_type: str
    name: str
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    parent_class: Optional[str] = None
    implements: List[str] = field(default_factory=list)
    extends: Optional[str] = None
    uses: List[str] = field(default_factory=list)
    used_by: List[str] = field(default_factory=list)
    calls: List[str] = field(default_factory=list)
    called_by: List[str] = field(default_factory=list)
    related_tables: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "name": self.name,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "parent_class": self.parent_class,
            "implements": self.implements,
            "extends": self.extends,
            "uses": self.uses,
            "used_by": self.used_by,
            "calls": self.calls,
            "called_by": self.called_by,
            "related_tables": self.related_tables,
            "metadata": self.metadata,
        }

    def to_summary(self) -> str:
        """Kurze Zusammenfassung für Prompts."""
        parts = [f"{self.node_type} {self.name}"]
        if self.extends:
            parts.append(f"extends {self.extends}")
        if self.implements:
            parts.append(f"implements {', '.join(self.implements)}")
        if self.used_by:
            parts.append(f"verwendet von: {', '.join(self.used_by[:3])}")
        if self.uses:
            parts.append(f"verwendet: {', '.join(self.uses[:3])}")
        return " | ".join(parts)


@dataclass
class PathResult:
    """Ergebnis einer Pfad-Suche."""
    from_id: str
    to_id: str
    found: bool
    path: List[Dict[str, Any]] = field(default_factory=list)
    length: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from_id": self.from_id,
            "to_id": self.to_id,
            "found": self.found,
            "path": self.path,
            "length": self.length,
        }


# ══════════════════════════════════════════════════════════════════════════════
# Query Service
# ══════════════════════════════════════════════════════════════════════════════

class GraphQueryService:
    """
    Intelligente Query-Schicht über dem Knowledge Graph.

    Bietet High-Level-Abfragen für:
    - Impact-Analyse bei Code-Änderungen
    - Kontext-Anreicherung für besseres Code-Verständnis
    - Pfad-Finder für Abhängigkeits-Analyse
    - Natürlichsprachliche Suche
    """

    def __init__(self, store: Optional[KnowledgeGraphStore] = None):
        self.store = store or get_knowledge_graph_store()

    # ─────────────────────────────────────────────────────────────────
    # Impact Analysis
    # ─────────────────────────────────────────────────────────────────

    async def analyze_impact(
        self,
        target_id: str,
        max_depth: int = 2,
        include_transitive: bool = True
    ) -> ImpactResult:
        """
        Analysiert die Auswirkungen einer Änderung an einem Code-Element.

        Args:
            target_id: ID des zu ändernden Elements (z.B. "com.example.UserService")
            max_depth: Maximale Tiefe der Analyse (1-5)
            include_transitive: Auch indirekte Abhängigkeiten einbeziehen

        Returns:
            ImpactResult mit allen betroffenen Elementen und Risiko-Score

        Beispiel:
            result = await service.analyze_impact("UserService.save")
            # → Zeigt: UserController, AdminService, UserServiceTest sind betroffen
        """
        target_node = self.store.get_node(target_id)
        if not target_node:
            return ImpactResult(
                target_id=target_id,
                target_name=target_id.split(".")[-1],
                target_type="unknown",
                summary=f"Element '{target_id}' nicht im Graph gefunden."
            )

        # Direkte Abhängigkeiten (wer verwendet dieses Element?)
        direct_edges = self.store.get_edges_to(target_id)
        direct_impacts = []
        affected_files: Set[str] = set()

        for edge in direct_edges:
            node = self.store.get_node(edge.from_id)
            if node:
                direct_impacts.append({
                    "id": node.id,
                    "name": node.name,
                    "type": node.type.value,
                    "relation": edge.type.value,
                    "file_path": node.file_path,
                })
                if node.file_path:
                    affected_files.add(node.file_path)

        # Transitive Abhängigkeiten
        transitive_impacts = []
        if include_transitive and max_depth > 1:
            visited = {target_id} | {d["id"] for d in direct_impacts}
            queue = [d["id"] for d in direct_impacts]
            current_depth = 1

            while queue and current_depth < max_depth:
                next_queue = []
                for node_id in queue:
                    edges = self.store.get_edges_to(node_id)
                    for edge in edges:
                        if edge.from_id not in visited:
                            visited.add(edge.from_id)
                            node = self.store.get_node(edge.from_id)
                            if node:
                                transitive_impacts.append({
                                    "id": node.id,
                                    "name": node.name,
                                    "type": node.type.value,
                                    "depth": current_depth + 1,
                                    "file_path": node.file_path,
                                })
                                if node.file_path:
                                    affected_files.add(node.file_path)
                                next_queue.append(edge.from_id)
                queue = next_queue
                current_depth += 1

        # Risk Score berechnen (basierend auf Anzahl Abhängigkeiten)
        total_impacts = len(direct_impacts) + len(transitive_impacts)
        risk_score = min(1.0, total_impacts / 20.0)  # 20+ = max risk

        # Summary generieren
        summary_parts = []
        if direct_impacts:
            summary_parts.append(f"{len(direct_impacts)} direkte Abhängigkeiten")
        if transitive_impacts:
            summary_parts.append(f"{len(transitive_impacts)} indirekte")
        if affected_files:
            summary_parts.append(f"{len(affected_files)} Dateien betroffen")

        summary = f"Änderung an {target_node.name}: " + ", ".join(summary_parts) if summary_parts else "Keine Abhängigkeiten gefunden"

        return ImpactResult(
            target_id=target_id,
            target_name=target_node.name,
            target_type=target_node.type.value,
            direct_impacts=direct_impacts,
            transitive_impacts=transitive_impacts,
            affected_files=sorted(affected_files),
            risk_score=risk_score,
            summary=summary,
        )

    # ─────────────────────────────────────────────────────────────────
    # Context Retrieval
    # ─────────────────────────────────────────────────────────────────

    async def get_context(self, node_id: str) -> Optional[ContextResult]:
        """
        Holt reichhaltigen Kontext für ein Code-Element.

        Args:
            node_id: ID des Elements

        Returns:
            ContextResult mit allen Beziehungen und Metadaten

        Beispiel:
            ctx = await service.get_context("UserService")
            # → Zeigt: implements UserServiceInterface, uses UserRepository, etc.
        """
        node = self.store.get_node(node_id)
        if not node:
            return None

        # Ausgehende Kanten analysieren
        outgoing = self.store.get_edges_from(node_id)
        implements = []
        extends = None
        uses = []
        calls = []
        related_tables = []

        for edge in outgoing:
            target_node = self.store.get_node(edge.to_id)
            target_name = target_node.name if target_node else edge.to_id.split(".")[-1]

            if edge.type == EdgeType.IMPLEMENTS:
                implements.append(target_name)
            elif edge.type == EdgeType.EXTENDS:
                extends = target_name
            elif edge.type == EdgeType.USES:
                uses.append(target_name)
            elif edge.type == EdgeType.CALLS:
                calls.append(target_name)
            elif edge.type == EdgeType.QUERIES:
                related_tables.append(target_name)

        # Eingehende Kanten analysieren
        incoming = self.store.get_edges_to(node_id)
        used_by = []
        called_by = []

        for edge in incoming:
            source_node = self.store.get_node(edge.from_id)
            source_name = source_node.name if source_node else edge.from_id.split(".")[-1]

            if edge.type in (EdgeType.USES, EdgeType.DEPENDS_ON, EdgeType.IMPORTS):
                used_by.append(source_name)
            elif edge.type == EdgeType.CALLS:
                called_by.append(source_name)

        # Parent-Class ermitteln (für Methoden)
        parent_class = None
        if node.type == NodeType.METHOD:
            # Parent ist typischerweise im ID-Pfad
            parts = node_id.rsplit(".", 1)
            if len(parts) > 1:
                parent_class = parts[0].split(".")[-1]

        return ContextResult(
            node_id=node_id,
            node_type=node.type.value,
            name=node.name,
            file_path=node.file_path,
            line_number=node.line_number,
            parent_class=parent_class,
            implements=implements,
            extends=extends,
            uses=uses[:10],  # Limitieren
            used_by=used_by[:10],
            calls=calls[:10],
            called_by=called_by[:10],
            related_tables=related_tables,
            metadata=node.metadata,
        )

    async def get_context_for_file(self, file_path: str) -> List[ContextResult]:
        """
        Holt Kontext für alle Elemente in einer Datei.

        Args:
            file_path: Pfad zur Datei

        Returns:
            Liste von ContextResult für jedes Element in der Datei
        """
        # Normalisiere Pfad
        normalized_path = str(Path(file_path).resolve())

        # Alle Nodes dieser Datei finden
        # Da wir keinen direkten Index haben, durchsuchen wir mit Suche
        all_nodes = self.store.search_nodes("", limit=1000)  # Alle holen
        file_nodes = [n for n in all_nodes if n.file_path and
                      Path(n.file_path).resolve() == Path(normalized_path).resolve()]

        results = []
        for node in file_nodes:
            ctx = await self.get_context(node.id)
            if ctx:
                results.append(ctx)

        return results

    # ─────────────────────────────────────────────────────────────────
    # Path Finding
    # ─────────────────────────────────────────────────────────────────

    async def find_connection(
        self,
        from_id: str,
        to_id: str,
        max_hops: int = 5
    ) -> PathResult:
        """
        Findet die Verbindung zwischen zwei Code-Elementen.

        Args:
            from_id: Start-Element
            to_id: Ziel-Element
            max_hops: Maximale Pfadlänge

        Returns:
            PathResult mit dem gefundenen Pfad

        Beispiel:
            path = await service.find_connection("UserController", "DatabaseService")
            # → UserController → UserService → UserRepository → DatabaseService
        """
        edges = self.store.find_path(from_id, to_id, max_hops)

        if not edges:
            return PathResult(
                from_id=from_id,
                to_id=to_id,
                found=False,
            )

        path = []
        for edge in edges:
            from_node = self.store.get_node(edge.from_id)
            to_node = self.store.get_node(edge.to_id)
            path.append({
                "from": from_node.name if from_node else edge.from_id,
                "to": to_node.name if to_node else edge.to_id,
                "relation": edge.type.value,
            })

        return PathResult(
            from_id=from_id,
            to_id=to_id,
            found=True,
            path=path,
            length=len(edges),
        )

    # ─────────────────────────────────────────────────────────────────
    # Smart Search
    # ─────────────────────────────────────────────────────────────────

    async def smart_search(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 20
    ) -> List[GraphNode]:
        """
        Intelligente Suche mit Kontext-Verständnis.

        Args:
            query: Suchanfrage (natürliche Sprache oder Pattern)
            filters: Optional Filter (type, file_path, etc.)
            limit: Max. Ergebnisse

        Returns:
            Liste von passenden GraphNodes

        Beispiele:
            "REST Controller" → findet Klassen mit @RestController
            "verwendet UserService" → findet alle Klassen die UserService nutzen
            "implementiert Serializable" → findet Interface-Implementierungen
        """
        filters = filters or {}
        type_filter = filters.get("type")

        # Parse Query für spezielle Patterns
        query_lower = query.lower()

        # Pattern: "verwendet X" / "uses X"
        if query_lower.startswith("verwendet ") or query_lower.startswith("uses "):
            target = query.split(" ", 1)[1]
            return await self._find_users_of(target, limit)

        # Pattern: "implementiert X" / "implements X"
        if query_lower.startswith("implementiert ") or query_lower.startswith("implements "):
            interface = query.split(" ", 1)[1]
            return await self._find_implementors(interface, limit)

        # Pattern: "aufgerufen von X" / "called by X"
        if "aufgerufen von" in query_lower or "called by" in query_lower:
            parts = query_lower.replace("aufgerufen von", "|").replace("called by", "|").split("|")
            if len(parts) > 1:
                caller = parts[1].strip()
                return await self._find_callees(caller, limit)

        # Standard-Suche
        node_types = None
        if type_filter:
            try:
                node_types = [NodeType(type_filter)]
            except ValueError:
                pass

        return self.store.search_nodes(query, node_types, limit)

    async def _find_users_of(self, target_name: str, limit: int) -> List[GraphNode]:
        """Findet alle Nodes die das Target verwenden."""
        # Erst Target finden
        targets = self.store.search_nodes(target_name, limit=5)
        if not targets:
            return []

        users = []
        for target in targets:
            edges = self.store.get_edges_to(target.id)
            for edge in edges:
                if edge.type in (EdgeType.USES, EdgeType.IMPORTS, EdgeType.DEPENDS_ON):
                    node = self.store.get_node(edge.from_id)
                    if node and node not in users:
                        users.append(node)
                        if len(users) >= limit:
                            return users
        return users

    async def _find_implementors(self, interface_name: str, limit: int) -> List[GraphNode]:
        """Findet alle Implementierungen eines Interfaces."""
        interfaces = self.store.search_nodes(interface_name, [NodeType.INTERFACE], limit=5)
        if not interfaces:
            # Auch als Klasse suchen (könnte abstract class sein)
            interfaces = self.store.search_nodes(interface_name, [NodeType.CLASS], limit=5)

        implementors = []
        for interface in interfaces:
            edges = self.store.get_edges_to(interface.id)
            for edge in edges:
                if edge.type == EdgeType.IMPLEMENTS:
                    node = self.store.get_node(edge.from_id)
                    if node and node not in implementors:
                        implementors.append(node)
                        if len(implementors) >= limit:
                            return implementors
        return implementors

    async def _find_callees(self, caller_name: str, limit: int) -> List[GraphNode]:
        """Findet alle Methoden die von einem Caller aufgerufen werden."""
        callers = self.store.search_nodes(caller_name, limit=5)
        if not callers:
            return []

        callees = []
        for caller in callers:
            edges = self.store.get_edges_from(caller.id)
            for edge in edges:
                if edge.type == EdgeType.CALLS:
                    node = self.store.get_node(edge.to_id)
                    if node and node not in callees:
                        callees.append(node)
                        if len(callees) >= limit:
                            return callees
        return callees

    # ─────────────────────────────────────────────────────────────────
    # Dependents / Dependencies
    # ─────────────────────────────────────────────────────────────────

    async def get_dependents(
        self,
        node_id: str,
        include_tests: bool = True
    ) -> List[GraphNode]:
        """
        Gibt alle Elemente zurück, die das angegebene Element verwenden.

        Args:
            node_id: ID des Elements
            include_tests: Auch Test-Klassen einbeziehen

        Returns:
            Liste von abhängigen Nodes
        """
        nodes = self.store.get_dependents(node_id)

        if not include_tests:
            nodes = [n for n in nodes if not self._is_test_class(n)]

        return nodes

    async def get_dependencies(self, node_id: str) -> List[GraphNode]:
        """
        Gibt alle Elemente zurück, die das angegebene Element verwendet.

        Args:
            node_id: ID des Elements

        Returns:
            Liste von verwendeten Nodes
        """
        return self.store.get_dependencies(node_id)

    def _is_test_class(self, node: GraphNode) -> bool:
        """Prüft ob ein Node eine Test-Klasse ist."""
        name_lower = node.name.lower()
        if "test" in name_lower:
            return True
        if node.file_path:
            path_lower = node.file_path.lower()
            if "/test/" in path_lower or "\\test\\" in path_lower:
                return True
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_service: Optional[GraphQueryService] = None


def get_graph_query_service(store: Optional[KnowledgeGraphStore] = None) -> GraphQueryService:
    """Gibt die singleton GraphQueryService-Instanz zurück."""
    global _service
    if _service is None or store is not None:
        _service = GraphQueryService(store)
    return _service
