"""
Task-Decomposition Models - Datenstrukturen fuer das Task-Agent-System.

Definiert:
- TaskType: Typen von spezialisierten Agenten
- TaskStatus: Ausfuehrungsstatus eines Tasks
- Task: Einzelne Aufgabe mit Abhaengigkeiten
- TaskPlan: Ausfuehrungsplan mit mehreren Tasks
- AgentConfig: Konfiguration eines spezialisierten Agenten
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class TaskType(str, Enum):
    """Typen von spezialisierten Agenten."""
    RESEARCH = "research"      # Informationen suchen/lesen
    CODE = "code"              # Code schreiben/editieren
    ANALYST = "analyst"        # Code analysieren/reviewen
    DEVOPS = "devops"          # CI/CD, Deployment
    DOCUMENTATION = "docs"     # Dokumentation erstellen
    DEBUG = "debug"            # Code debuggen und testen


class TaskStatus(str, Enum):
    """Ausfuehrungsstatus eines Tasks."""
    PENDING = "pending"        # Wartet auf Ausfuehrung
    BLOCKED = "blocked"        # Wartet auf Abhaengigkeiten
    RUNNING = "running"        # Wird ausgefuehrt
    COMPLETED = "completed"    # Erfolgreich abgeschlossen
    FAILED = "failed"          # Fehlgeschlagen
    RETRY = "retry"            # Wird mit anderem Ansatz wiederholt


class RetryStrategy(str, Enum):
    """Strategien fuer Retry bei Fehlern."""
    BROADEN_QUERY = "broaden_query"          # Suche erweitern
    ALTERNATIVE_APPROACH = "alternative_approach"  # Anderen Ansatz versuchen
    DIFFERENT_PERSPECTIVE = "different_perspective"  # Andere Perspektive
    CHECK_PREREQUISITES = "check_prerequisites"  # Voraussetzungen pruefen
    REPHRASE = "rephrase"                    # Umformulieren
    ISOLATE_AND_TEST = "isolate_and_test"    # Problem isolieren und testen


@dataclass
class Task:
    """
    Eine einzelne Aufgabe im Ausfuehrungsplan.

    Attributes:
        id: Eindeutige ID (z.B. "T1", "T2")
        type: Agent-Typ der diese Task ausfuehrt
        description: Beschreibung was gemacht werden soll
        depends_on: Liste von Task-IDs auf die gewartet werden muss
        status: Aktueller Ausfuehrungsstatus
        result: Ergebnis nach erfolgreicher Ausfuehrung
        error: Fehlermeldung bei Misserfolg
        retry_count: Anzahl bisheriger Retry-Versuche
        context_from: Zusaetzliche Kontext-Quellen (z.B. Phasen-Synthesen)
    """
    id: str
    type: TaskType
    description: str
    depends_on: List[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[str] = None
    error: Optional[str] = None
    retry_count: int = 0
    context_from: List[str] = field(default_factory=list)

    @property
    def is_ready(self) -> bool:
        """Task ist bereit wenn Status PENDING und keine offenen Abhaengigkeiten."""
        return self.status == TaskStatus.PENDING

    @property
    def is_terminal(self) -> bool:
        """Task ist in einem Endzustand (completed oder failed)."""
        return self.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)

    def to_dict(self) -> Dict[str, Any]:
        """Konvertiert Task zu Dictionary fuer Serialisierung."""
        return {
            "id": self.id,
            "type": self.type.value,
            "description": self.description,
            "depends_on": self.depends_on,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "retry_count": self.retry_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        """Erstellt Task aus Dictionary."""
        return cls(
            id=data["id"],
            type=TaskType(data["type"]),
            description=data["description"],
            depends_on=data.get("depends_on", []),
            status=TaskStatus(data.get("status", "pending")),
            result=data.get("result"),
            error=data.get("error"),
            retry_count=data.get("retry_count", 0),
        )


@dataclass
class TaskPlan:
    """
    Ausfuehrungsplan mit einer oder mehreren Tasks.

    Attributes:
        needs_clarification: True wenn Klaerungsfragen noetig sind
        clarification_questions: Liste der Klaerungsfragen
        tasks: Liste der auszufuehrenden Tasks
        original_query: Urspruengliche User-Anfrage
    """
    needs_clarification: bool = False
    clarification_questions: List[str] = field(default_factory=list)
    tasks: List[Task] = field(default_factory=list)
    original_query: str = ""

    def get_ready_tasks(self, completed_ids: Set[str]) -> List[Task]:
        """
        Gibt alle Tasks zurueck die ausgefuehrt werden koennen.

        Ein Task ist ready wenn:
        - Status ist PENDING
        - Alle Abhaengigkeiten sind in completed_ids

        Args:
            completed_ids: Set von bereits abgeschlossenen Task-IDs

        Returns:
            Liste von ready Tasks
        """
        ready = []
        for task in self.tasks:
            if task.status != TaskStatus.PENDING:
                continue
            # Alle Abhaengigkeiten muessen completed sein
            if all(dep in completed_ids for dep in task.depends_on):
                ready.append(task)
        return ready

    def get_tasks_by_type(self) -> Dict[TaskType, List[Task]]:
        """
        Gruppiert Tasks nach Typ.

        Returns:
            Dictionary mit TaskType als Key und Liste von Tasks als Value
        """
        by_type: Dict[TaskType, List[Task]] = {}
        for task in self.tasks:
            if task.type not in by_type:
                by_type[task.type] = []
            by_type[task.type].append(task)
        return by_type

    def get_pending_count(self) -> int:
        """Anzahl noch ausstehender Tasks."""
        return sum(1 for t in self.tasks if t.status == TaskStatus.PENDING)

    def get_completed_count(self) -> int:
        """Anzahl abgeschlossener Tasks."""
        return sum(1 for t in self.tasks if t.status == TaskStatus.COMPLETED)

    def get_failed_count(self) -> int:
        """Anzahl fehlgeschlagener Tasks."""
        return sum(1 for t in self.tasks if t.status == TaskStatus.FAILED)

    @property
    def is_complete(self) -> bool:
        """True wenn alle Tasks in einem Endzustand sind."""
        return all(t.is_terminal for t in self.tasks)

    @property
    def is_successful(self) -> bool:
        """True wenn alle Tasks erfolgreich abgeschlossen sind."""
        return all(t.status == TaskStatus.COMPLETED for t in self.tasks)

    def to_dict(self) -> Dict[str, Any]:
        """Konvertiert TaskPlan zu Dictionary."""
        return {
            "needs_clarification": self.needs_clarification,
            "clarification_questions": self.clarification_questions,
            "tasks": [t.to_dict() for t in self.tasks],
            "original_query": self.original_query,
        }


@dataclass
class AgentConfig:
    """
    Konfiguration eines spezialisierten Agenten.

    Attributes:
        type: Agent-Typ
        model: Primaeres LLM-Modell
        fallback_model: Fallback-Modell wenn primaeres nicht verfuegbar
        system_prompt: Spezialisierter System-Prompt
        tools: Liste erlaubter Tool-Namen
        max_iterations: Maximale Tool-Call-Iterationen
        temperature: LLM Temperature
        retry_strategy: Strategie bei Fehlern
        max_retries: Maximale Retry-Versuche
    """
    type: TaskType
    model: str
    fallback_model: str
    system_prompt: str
    tools: List[str]
    max_iterations: int = 5
    temperature: float = 0.2
    retry_strategy: RetryStrategy = RetryStrategy.REPHRASE
    max_retries: int = 3

    def get_tool_names(self) -> Set[str]:
        """Gibt erlaubte Tool-Namen als Set zurueck."""
        return set(self.tools)


@dataclass
class TaskExecutionResult:
    """
    Ergebnis der Task-Ausfuehrung.

    Attributes:
        success: True wenn alle Tasks erfolgreich
        results: Dictionary von Task-ID zu Ergebnis
        final_response: Synthetisierte finale Antwort
        failed_tasks: Liste fehlgeschlagener Task-IDs
        total_duration_ms: Gesamtdauer in Millisekunden
    """
    success: bool
    results: Dict[str, str] = field(default_factory=dict)
    final_response: str = ""
    failed_tasks: List[str] = field(default_factory=list)
    total_duration_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Konvertiert zu Dictionary."""
        return {
            "success": self.success,
            "results": self.results,
            "final_response": self.final_response,
            "failed_tasks": self.failed_tasks,
            "total_duration_ms": self.total_duration_ms,
        }
