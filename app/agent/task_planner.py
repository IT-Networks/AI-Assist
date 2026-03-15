"""
Task Planner - Analysiert und zerlegt User-Anfragen in Tasks.

Der TaskPlanner:
1. Analysiert die User-Anfrage
2. Entscheidet ob Klaerungsfragen noetig sind
3. Zerlegt in spezialisierte Tasks (nur wenn noetig)
4. Definiert Abhaengigkeiten zwischen Tasks
"""

import json
import logging
import re
from typing import Optional

from app.agent.task_models import Task, TaskPlan, TaskType, TaskStatus
from app.core.config import settings
from app.services.llm_client import LLMClient, llm_client as default_llm_client

logger = logging.getLogger(__name__)


PLANNER_SYSTEM_PROMPT = """Du bist ein Task-Planner fuer ein KI-Assistenzsystem.

AUFGABE:
Analysiere die User-Anfrage und erstelle einen Ausfuehrungsplan.

KONTEXT-NUTZUNG:
Falls im KONTEXT-Block bereits gesammelte Informationen vorhanden sind (z.B. aus Wiki,
Dokumentation, Code-Analyse), NUTZE diese aktiv bei der Planung:
- Keine Research-Task noetig wenn Kontext bereits relevante Informationen enthaelt
- Kontext-Inhalte koennen direkt in Task-Beschreibungen referenziert werden
- Bei unklarem Kontext: priorisiere die User-Anfrage

WICHTIGE REGELN:
1. Zerlege NUR wenn noetig - eine einzelne Task reicht oft aus!
2. Bei einfachen Anfragen (z.B. "schreibe eine Funktion") -> 1 Task
3. Bei komplexen Anfragen mit mehreren unabhaengigen Teilen -> mehrere Tasks
4. Research-Tasks NUR wenn explizit gefragt oder zwingend noetig
5. Definiere klare Abhaengigkeiten wenn ein Task Ergebnisse eines anderen braucht
6. Stelle Klaerungsfragen wenn die Anfrage zu unklar ist

TASK-TYPEN:
- research: Informationen suchen (Code, Wiki, Docs) - NUR wenn explizit noetig
- code: Code schreiben oder editieren - DEFAULT fuer Programmieraufgaben
- analyst: Code analysieren/reviewen
- devops: CI/CD, Deployment, Docker, Jenkins
- docs: Dokumentation erstellen
- debug: Fehler debuggen und testen (lokal oder remote Test-Tool)

WANN RESEARCH NOETIG IST:
- User fragt explizit nach Recherche ("suche", "finde", "was gibt es zu")
- User referenziert externe Quellen ("laut Wiki", "wie im Handbuch", "bestehende Implementierung")
- Aufgabe erfordert Wissen das nicht in der Anfrage steht

WANN NICHT ZERLEGEN:
- Einfache Code-Aufgabe: "Schreibe Funktion X" -> 1 code Task
- Einfache Analyse: "Review diese Datei" -> 1 analyst Task
- Klare einzelne Aufgabe ohne Abhaengigkeiten

WANN ZERLEGEN:
- Mehrere unabhaengige Features: "Implementiere Login UND Dashboard" -> 2 code Tasks
- Research + Implementation: "Implementiere wie im Wiki beschrieben" -> research + code
- Komplexes System: "Baue komplette App mit DB, API, Frontend" -> mehrere Tasks

OUTPUT FORMAT (JSON):
{
  "needs_clarification": false,
  "clarification_questions": [],
  "reasoning": "Kurze Begruendung der Entscheidung",
  "tasks": [
    {
      "id": "T1",
      "type": "code",
      "description": "Beschreibung der Aufgabe",
      "depends_on": []
    }
  ]
}

BEISPIELE:

User: "Schreibe eine Fibonacci-Funktion in Python"
{
  "needs_clarification": false,
  "reasoning": "Einfache Code-Aufgabe, keine Zerlegung noetig",
  "tasks": [{"id": "T1", "type": "code", "description": "Fibonacci-Funktion in Python implementieren", "depends_on": []}]
}

User: "Implementiere das Design aus unserem Wiki"
{
  "needs_clarification": false,
  "reasoning": "Benoetigt erst Wiki-Recherche, dann Implementation",
  "tasks": [
    {"id": "T1", "type": "research", "description": "Wiki nach Design-Dokumentation durchsuchen", "depends_on": []},
    {"id": "T2", "type": "code", "description": "Design gemaess Wiki-Dokumentation implementieren", "depends_on": ["T1"]}
  ]
}

User: "Baue eine App"
{
  "needs_clarification": true,
  "clarification_questions": ["Welche Art von App (Web/Mobile/Desktop)?", "Welche Programmiersprache?", "Welche Features soll die App haben?"],
  "reasoning": "Anfrage zu unspezifisch fuer sinnvolle Planung",
  "tasks": []
}
"""


class TaskPlanner:
    """
    Analysiert User-Anfragen und zerlegt sie in ausfuehrbare Tasks.
    """

    def __init__(self, llm_client: Optional[LLMClient] = None):
        """
        Initialisiert den TaskPlanner.

        Args:
            llm_client: LLM-Client fuer Anfragen (default: globaler Client)
        """
        self.llm = llm_client or default_llm_client
        self.model = settings.llm.analysis_model or settings.llm.default_model

    async def plan(
        self,
        user_query: str,
        context: Optional[str] = None
    ) -> TaskPlan:
        """
        Erstellt einen Ausfuehrungsplan fuer die User-Anfrage.

        Args:
            user_query: Die Anfrage des Users
            context: Optionaler Kontext (z.B. vorherige Konversation)

        Returns:
            TaskPlan mit Tasks oder Klaerungsfragen
        """
        logger.debug(f"[TaskPlanner] Planning for query: {user_query[:100]}...")

        messages = [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT}
        ]

        if context:
            messages.append({
                "role": "system",
                "content": f"KONTEXT:\n{context}"
            })

        messages.append({
            "role": "user",
            "content": user_query
        })

        try:
            response = await self.llm.chat_with_tools(
                messages=messages,
                model=self.model,
                temperature=0.1,
                max_tokens=2048,
                timeout=30.0
            )

            plan = self._parse_response(response.content, user_query)
            logger.info(
                f"[TaskPlanner] Created plan: {len(plan.tasks)} tasks, "
                f"needs_clarification={plan.needs_clarification}"
            )
            return plan

        except Exception as e:
            logger.error(f"[TaskPlanner] Planning failed: {e}")
            # Fallback: Einzelne Code-Task
            return self._create_fallback_plan(user_query)

    def _parse_response(self, content: str, original_query: str) -> TaskPlan:
        """
        Parst die LLM-Antwort in einen TaskPlan.

        Args:
            content: LLM-Response-Content
            original_query: Urspruengliche User-Anfrage

        Returns:
            Geparstes TaskPlan
        """
        if not content:
            return self._create_fallback_plan(original_query)

        try:
            # JSON aus Response extrahieren
            json_match = re.search(r'\{[\s\S]*\}', content)
            if not json_match:
                logger.warning("[TaskPlanner] No JSON found in response")
                return self._create_fallback_plan(original_query)

            data = json.loads(json_match.group())

            # Klaerungsfragen?
            needs_clarification = data.get("needs_clarification", False)
            clarification_questions = data.get("clarification_questions", [])

            if needs_clarification:
                return TaskPlan(
                    needs_clarification=True,
                    clarification_questions=clarification_questions,
                    tasks=[],
                    original_query=original_query
                )

            # Tasks parsen
            tasks = []
            for t in data.get("tasks", []):
                try:
                    task_type = TaskType(t["type"])
                except ValueError:
                    logger.warning(f"[TaskPlanner] Unknown task type: {t.get('type')}")
                    task_type = TaskType.CODE  # Fallback

                tasks.append(Task(
                    id=t.get("id", f"T{len(tasks) + 1}"),
                    type=task_type,
                    description=t.get("description", ""),
                    depends_on=t.get("depends_on", []),
                    status=TaskStatus.PENDING
                ))

            # Validierung: Mindestens eine Task
            if not tasks:
                return self._create_fallback_plan(original_query)

            # Validierung: Abhaengigkeiten pruefen
            task_ids = {t.id for t in tasks}
            for task in tasks:
                invalid_deps = [d for d in task.depends_on if d not in task_ids]
                if invalid_deps:
                    logger.warning(
                        f"[TaskPlanner] Task {task.id} has invalid dependencies: {invalid_deps}"
                    )
                    task.depends_on = [d for d in task.depends_on if d in task_ids]

            return TaskPlan(
                needs_clarification=False,
                tasks=tasks,
                original_query=original_query
            )

        except json.JSONDecodeError as e:
            logger.warning(f"[TaskPlanner] JSON parse error: {e}")
            return self._create_fallback_plan(original_query)

        except (KeyError, TypeError) as e:
            logger.warning(f"[TaskPlanner] Data structure error: {e}")
            return self._create_fallback_plan(original_query)

    def _create_fallback_plan(self, query: str) -> TaskPlan:
        """
        Erstellt einen Fallback-Plan mit einer einzelnen Code-Task.

        Args:
            query: User-Anfrage

        Returns:
            Einfacher TaskPlan mit einer Task
        """
        # Heuristik: Task-Typ aus Query ableiten
        task_type = self._detect_task_type(query)

        return TaskPlan(
            needs_clarification=False,
            tasks=[
                Task(
                    id="T1",
                    type=task_type,
                    description=query,
                    status=TaskStatus.PENDING
                )
            ],
            original_query=query
        )

    def _detect_task_type(self, query: str) -> TaskType:
        """
        Erkennt den wahrscheinlichsten Task-Typ aus der Query.

        Args:
            query: User-Anfrage

        Returns:
            Erkannter TaskType
        """
        query_lower = query.lower()

        # Debug Keywords (vor Research pruefen, da spezifischer)
        debug_keywords = [
            "debug", "debugge", "teste", "testen", "nachstellen",
            "reproduziere", "fehler", "bug", "error", "exception",
            "stacktrace", "traceback", "breakpoint", "step through",
            "root cause", "ursache", "warum funktioniert", "why does",
            "nicht funktioniert", "doesn't work", "kaputt", "broken"
        ]
        if any(kw in query_lower for kw in debug_keywords):
            return TaskType.DEBUG

        # Research Keywords
        research_keywords = [
            "suche", "finde", "recherchiere", "was gibt es",
            "search", "find", "look for", "research"
        ]
        if any(kw in query_lower for kw in research_keywords):
            return TaskType.RESEARCH

        # Analyst Keywords
        analyst_keywords = [
            "analysiere", "review", "pruefe", "check",
            "analyze", "analyse", "audit", "inspect"
        ]
        if any(kw in query_lower for kw in analyst_keywords):
            return TaskType.ANALYST

        # DevOps Keywords
        devops_keywords = [
            "deploy", "build", "jenkins", "docker", "ci/cd",
            "pipeline", "container", "kubernetes", "k8s"
        ]
        if any(kw in query_lower for kw in devops_keywords):
            return TaskType.DEVOPS

        # Documentation Keywords
        docs_keywords = [
            "dokumentiere", "readme", "docs", "dokumentation",
            "document", "documentation", "wiki"
        ]
        if any(kw in query_lower for kw in docs_keywords):
            return TaskType.DOCUMENTATION

        # Default: Code
        return TaskType.CODE


# Singleton-Instanz
_task_planner: Optional[TaskPlanner] = None


def get_task_planner() -> TaskPlanner:
    """
    Gibt die TaskPlanner-Instanz zurueck (Singleton).

    Returns:
        TaskPlanner-Instanz
    """
    global _task_planner
    if _task_planner is None:
        _task_planner = TaskPlanner()
    return _task_planner
