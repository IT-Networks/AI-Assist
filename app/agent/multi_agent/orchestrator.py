"""
MultiAgentOrchestrator – Koordiniert ein Team aus Agenten fuer eine komplexe Aufgabe.

Pipeline:
1. Goal → TaskPlan (Coordinator LLM-Call)
2. TaskPlan → Scheduled Tasks (Topologische Sortierung)
3. Tasks → Parallele Ausfuehrung (AgentPool)
4. Ergebnisse → Synthesis (LLM-Zusammenfassung)
"""

import asyncio
import json
import logging
import re
import time
from typing import Awaitable, Callable, Dict, List, Optional

from app.agent.multi_agent.agent_pool import AgentPool
from app.agent.multi_agent.message_bus import MessageBus
from app.agent.multi_agent.models import (
    TeamConfig,
    TeamRunResult,
    TeamTask,
)
from app.agent.multi_agent.scheduler import TaskScheduler
from app.agent.multi_agent.team_agent import TeamAgent
from app.core.config import settings
from app.services.llm_client import llm_client as default_llm_client

logger = logging.getLogger(__name__)


class MultiAgentOrchestrator:
    """Koordiniert ein Team aus Agenten fuer eine komplexe Aufgabe."""

    def __init__(
        self,
        team_config: TeamConfig,
        on_progress: Optional[Callable[[Dict], Awaitable[None]]] = None,
    ):
        self._team = team_config
        self._on_progress = on_progress
        self._message_bus = MessageBus()
        self._shared_context: Dict[str, str] = {}  # task_id → result
        self._pool = AgentPool(max_concurrent=team_config.max_parallel)
        self._scheduler = TaskScheduler(strategy=team_config.strategy)
        self._model = settings.multi_agent.coordinator_model or settings.llm.default_model
        self._timeout = settings.multi_agent.task_timeout_seconds

        # Agents erstellen und im Pool registrieren
        for agent_config in team_config.agents:
            agent = TeamAgent(agent_config, self._message_bus)
            self._pool.register(agent)

        # Token-Tracking (aggregiert ueber alle LLM-Calls)
        self._total_tokens = 0
        self._total_llm_calls = 0
        # Agent-Diagramme: agent_name → (diagram, title)
        self._agent_diagrams: Dict[str, tuple] = {}

    async def run(self, goal: str) -> TeamRunResult:
        """Fuehrt den kompletten Team-Run aus."""
        start = time.time()
        self._goal = goal  # Fuer Durchreichung an Agents
        logger.info(f"[MultiAgent] Team '{self._team.name}' startet: {goal[:100]}")

        # Phase 1: Coordinator zerlegt Ziel in Tasks
        await self._emit({"phase": "planning", "message": "Zerlege Aufgabe in Tasks..."})
        tasks = await self._decompose_goal(goal)

        if not tasks:
            logger.warning(f"[MultiAgent] Coordinator konnte keine Tasks erstellen fuer: {goal[:100]}")
            return TeamRunResult(
                team_name=self._team.name,
                goal=goal,
                final_summary=(
                    "Konnte die Aufgabe nicht in Tasks zerlegen.\n"
                    f"Coordinator-Modell: {self._model}\n"
                    "Moeglicherweise konnte das LLM-Modell das Ziel nicht in ein JSON-Task-Array umwandeln.\n"
                    "Tipp: Versuche ein einfacheres Ziel oder pruefe ob das LLM-Modell verfuegbar ist."
                ),
                duration_seconds=time.time() - start,
            )

        logger.info(f"[MultiAgent] {len(tasks)} Tasks erstellt")
        await self._emit({
            "phase": "planned",
            "tasks": len(tasks),
            "agents": self._team.agent_names(),
        })

        # Phase 2: Scheduling
        ordered = self._scheduler.schedule(tasks, self._team.agents)

        # Phase 3: Execution Loop
        try:
            await self._execute_loop(ordered)
        except Exception as e:
            logger.error(f"[MultiAgent] Execution-Loop Fehler: {e}", exc_info=True)
            await self._emit({"phase": "task_failed", "error": f"Execution-Fehler: {e}"})

        # Phase 4: Synthesis
        await self._emit({"phase": "synthesizing", "message": "Fasse Ergebnisse zusammen..."})
        try:
            summary = await self._synthesize_results(goal, tasks)
        except Exception as e:
            logger.error(f"[MultiAgent] Synthesis Fehler: {e}", exc_info=True)
            # Fallback: Einfache Auflistung
            summary = "\n\n".join([
                f"{'OK' if t.status == 'completed' else 'FEHLER'}: {t.title} ({t.assignee})\n{(t.result or t.error or '')[:500]}"
                for t in tasks
            ])

        # Statistiken
        completed = sum(1 for t in tasks if t.status == "completed")
        failed = sum(1 for t in tasks if t.status == "failed")
        duration = time.time() - start

        logger.info(f"[MultiAgent] Fertig: {completed}/{len(tasks)} Tasks in {duration:.1f}s")

        await self._emit({
            "phase": "complete",
            "completed": completed,
            "failed": failed,
            "total": len(tasks),
            "duration": round(duration, 1),
        })

        logger.info(f"[MultiAgent] Token-Usage: {self._total_tokens} Tokens in {self._total_llm_calls} LLM-Calls")

        return TeamRunResult(
            team_name=self._team.name,
            goal=goal,
            tasks=tasks,
            messages=self._message_bus.get_all(),
            final_summary=summary,
            total_tasks=len(tasks),
            completed_tasks=completed,
            failed_tasks=failed,
            duration_seconds=duration,
            total_tokens=self._total_tokens,
            total_llm_calls=self._total_llm_calls,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # Phase 1: Goal Decomposition
    # ══════════════════════════════════════════════════════════════════════════

    async def _decompose_goal(self, goal: str) -> List[TeamTask]:
        """Coordinator: LLM zerlegt Ziel in Tasks mit Agent-Zuweisungen."""
        agents_desc = "\n".join([
            f"- {a.name}: {a.system_prompt or 'Allgemeiner Agent'} (Tools: {', '.join(a.tools) or 'keine'})"
            for a in self._team.agents
        ])

        prompt = (
            f"Du bist ein Task-Koordinator. Zerlege das folgende Ziel in konkrete Aufgaben "
            f"und weise sie den verfuegbaren Agenten zu.\n\n"
            f"ZIEL: {goal}\n\n"
            f"VERFUEGBARE AGENTEN:\n{agents_desc}\n\n"
            f"REGELN:\n"
            f"- Erstelle 2-8 Tasks (nicht mehr)\n"
            f"- Jeder Task muss einem Agenten zugewiesen sein (assignee)\n"
            f"- Tasks koennen von anderen Tasks abhaengen (dependsOn: [task-id])\n"
            f"- IDs muessen mit 't' beginnen: t1, t2, t3, ...\n"
            f"- WICHTIG: Tasks die NICHT voneinander abhaengen MUESSEN dependsOn:[] haben!\n"
            f"  Nur wenn ein Task das ERGEBNIS eines anderen Tasks braucht, setze dependsOn.\n"
            f"- WICHTIG: Weise nur Agenten zu deren Tools zum Ziel PASSEN!\n"
            f"  Schaue auf die Tools jedes Agenten und pruefe ob sie fuer das Ziel relevant sind.\n"
            f"  Nicht relevante Agenten WEGLASSEN statt ihnen sinnlose Tasks zu geben.\n"
            f"- Der letzte Task (Zusammenfassung/Review) sollte von den anderen abhaengen\n"
            f"- WICHTIG fuer Task-Beschreibungen:\n"
            f"  Die 'description' MUSS die SPEZIFISCHE Frage/Aufgabe enthalten, NICHT generisch!\n"
            f"  SCHLECHT: 'Suche Dokumentation zu Authentication'\n"
            f"  GUT: 'Suche in Confluence wie die Authentication in unseren Microservices funktioniert, "
            f"insbesondere Token-Handling und Service-to-Service Auth'\n"
            f"  Die description soll dem Agent genau sagen WAS er finden soll.\n\n"
            f"Antworte NUR mit JSON-Array:\n"
            f'[{{"id":"t1","title":"...","description":"SPEZIFISCHE Aufgabe bezogen auf das Ziel","assignee":"agent1","dependsOn":[]}},'
            f'{{"id":"t2","title":"Zusammenfassung","description":"Fasse Ergebnisse zusammen und beantworte: {goal[:80]}","assignee":"agent2","dependsOn":["t1"]}}]'
        )

        try:
            text, p_tk, c_tk = await default_llm_client.chat_quick_with_usage(
                messages=[{"role": "user", "content": prompt}],
                model=self._model,
                temperature=0.1,
                max_tokens=2048,
            )
            self._total_tokens += p_tk + c_tk
            self._total_llm_calls += 1
            return self._parse_tasks(text)
        except Exception as e:
            logger.error(f"[MultiAgent] Coordinator fehlgeschlagen: {e}")
            return []

    def _parse_tasks(self, text: str) -> List[TeamTask]:
        """Parst Task-JSON aus LLM-Antwort."""
        # JSON-Array extrahieren
        try:
            # Versuche direktes Parsing
            start = text.find("[")
            end = text.rfind("]") + 1
            if start >= 0 and end > start:
                raw_tasks = json.loads(text[start:end])
            else:
                # Versuche aus Code-Block
                import re
                match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', text, re.DOTALL)
                if match:
                    raw_tasks = json.loads(match.group(1))
                else:
                    logger.warning("[MultiAgent] Kein JSON-Array in Coordinator-Antwort gefunden")
                    return []
        except json.JSONDecodeError as e:
            logger.warning(f"[MultiAgent] JSON-Parse-Fehler: {e}")
            return []

        if not isinstance(raw_tasks, list):
            return []

        # Zu TeamTask-Objekten konvertieren
        tasks: List[TeamTask] = []
        valid_agents = set(self._team.agent_names())

        for raw in raw_tasks:
            if not isinstance(raw, dict):
                continue

            task_id = raw.get("id", f"t{len(tasks) + 1}")
            assignee = raw.get("assignee", "")

            # Assignee validieren
            if assignee not in valid_agents:
                assignee = self._team.agents[0].name if self._team.agents else ""

            depends_on = raw.get("dependsOn", raw.get("depends_on", []))
            if not isinstance(depends_on, list):
                depends_on = []

            tasks.append(TeamTask(
                id=task_id,
                title=raw.get("title", ""),
                description=raw.get("description", ""),
                assignee=assignee,
                depends_on=depends_on,
            ))

        return tasks

    # ══════════════════════════════════════════════════════════════════════════
    # Phase 3: Execution Loop
    # ══════════════════════════════════════════════════════════════════════════

    async def _execute_loop(self, tasks: List[TeamTask]):
        """
        Execution Loop:
        while pending tasks exist:
          1. Finde ready Tasks (dependencies satisfied)
          2. Dispatche an AgentPool (parallel)
          3. Ergebnisse → SharedContext
          4. Unblock abhaengige Tasks
        """
        task_map = {t.id: t for t in tasks}
        completed_ids: set = set()
        failed_ids: set = set()
        max_rounds = 20  # Sicherheits-Limit

        for round_num in range(max_rounds):
            # Ready Tasks finden (inkl. Tasks deren Dependencies teilweise failed sind)
            ready = [t for t in tasks if t.is_ready(completed_ids, failed_ids)]
            if not ready:
                # Pruefen ob noch pending Tasks da sind (blockiert?)
                pending = [t for t in tasks if t.status in ("pending", "blocked")]
                if not pending:
                    break  # Alle fertig
                logger.warning(f"[MultiAgent] Keine ready Tasks, aber {len(pending)} pending/blocked")
                break

            # Batch-Assignments erstellen
            assignments = []
            parallel_count = len(ready)
            for idx, task in enumerate(ready):
                task.status = "in_progress"
                # Context aus Dependencies zusammenbauen
                dep_context = self._build_dependency_context(task)

                await self._emit({
                    "phase": "executing",
                    "task": task.title,
                    "agent": task.assignee,
                    "round": round_num + 1,
                    "parallel": parallel_count,
                    "parallel_index": idx + 1,
                    "completed": len(completed_ids),
                    "total": len(tasks),
                })

                assignments.append((task.assignee, task, dep_context))

            # Parallel ausfuehren
            try:
                results = await self._pool.execute_batch(
                    assignments, timeout=self._timeout, original_goal=self._goal
                )
            except Exception as e:
                logger.error(f"[MultiAgent] Batch-Execution Fehler in Runde {round_num+1}: {e}", exc_info=True)
                for task in ready:
                    task.status = "failed"
                    task.error = f"Batch-Fehler: {e}"
                break

            # Token-Usage + Diagramme der Agents sammeln
            for agent_name, _, _ in assignments:
                agent = self._pool.get(agent_name)
                if agent and hasattr(agent, 'last_token_usage'):
                    self._total_tokens += agent.last_token_usage
                    self._total_llm_calls += 1
                if agent and getattr(agent, 'last_diagram', ''):
                    self._agent_diagrams[agent_name] = (
                        agent.last_diagram,
                        getattr(agent, 'last_diagram_title', '') or f"Diagramm von {agent_name}",
                    )

            # Ergebnisse verarbeiten
            for task in ready:
                result = results.get(task.id, "")

                if not result:
                    result = "FEHLER: Leere Antwort vom Agent (kein Ergebnis)"

                if result.startswith("FEHLER:"):
                    task.status = "failed"
                    task.error = result
                    failed_ids.add(task.id)
                    logger.warning(f"[MultiAgent] Task '{task.title}' fehlgeschlagen: {result}")
                    # Soft-Cascade: Abhaengige Tasks nur blockieren wenn ALLE
                    # ihrer Dependencies fehlgeschlagen sind
                    self._cascade_failure_soft(task.id, tasks, completed_ids)
                    await self._emit({
                        "phase": "task_failed",
                        "task": task.title,
                        "agent": task.assignee,
                        "error": result,
                    })
                else:
                    task.status = "completed"
                    task.result = result
                    completed_ids.add(task.id)
                    self._shared_context[task.id] = result
                    await self._emit({
                        "phase": "task_completed",
                        "task": task.title,
                        "agent": task.assignee,
                        "completed": len(completed_ids),
                        "total": len(tasks),
                    })

    def _build_dependency_context(self, task: TeamTask) -> str:
        """Baut Context aus Dependency-Ergebnissen."""
        parts = []
        for dep_id in task.depends_on:
            if dep_id in self._shared_context:
                parts.append(f"=== Ergebnis von {dep_id} ===\n{self._shared_context[dep_id][:2000]}")
        return "\n\n".join(parts)

    def _cascade_failure(self, failed_id: str, tasks: List[TeamTask]):
        """
        DEPRECATED: Harte Cascade — blockiert ALLE abhaengigen Tasks.
        Benutze _cascade_failure_soft() stattdessen.
        """
        self._cascade_failure_soft(failed_id, tasks, set())

    def _cascade_failure_soft(self, failed_id: str, tasks: List[TeamTask], completed_ids: set):
        """
        Soft Cascade: Ein abhaengiger Task wird nur blockiert wenn ALLE seine
        Dependencies fehlgeschlagen sind. Wenn mindestens eine Dependency
        erfolgreich war, kann der Task trotzdem laufen.

        Beispiel: Synthesizer haengt von wiki-researcher + code-analyst ab.
        Wenn nur code-analyst fehlschlaegt, laeuft Synthesizer trotzdem
        (mit den Ergebnissen von wiki-researcher).
        """
        failed_ids = {t.id for t in tasks if t.status == "failed"}
        failed_ids.add(failed_id)

        for task in tasks:
            if task.status not in ("pending", "blocked"):
                continue
            if not task.depends_on:
                continue

            # Pruefen ob ALLE Dependencies dieses Tasks fehlgeschlagen sind
            all_deps_failed = all(
                dep_id in failed_ids
                for dep_id in task.depends_on
            )
            # Oder: Mindestens eine Dependency ist noch pending/in_progress
            has_pending_dep = any(
                dep_id not in failed_ids and dep_id not in completed_ids
                for dep_id in task.depends_on
            )

            if all_deps_failed and not has_pending_dep:
                task.status = "failed"
                task.error = f"Alle Dependencies fehlgeschlagen ({', '.join(task.depends_on)})"
                logger.info(f"[MultiAgent] Soft-Cascade: Task '{task.title}' blockiert (alle Deps failed)")

    # ══════════════════════════════════════════════════════════════════════════
    # Phase 4: Synthesis
    # ══════════════════════════════════════════════════════════════════════════

    async def _synthesize_results(self, goal: str, tasks: List[TeamTask]) -> str:
        """
        Fasst alle Task-Ergebnisse zusammen mit:
        1. LLM-generierte inhaltliche Zusammenfassung (strukturiertes Markdown)
        2. Statistik-Tabelle (Tasks, Agents, Status)
        3. Mermaid-Flowchart (Task-Dependencies)
        4. Mermaid Pie-Chart (Erfolgsquote)
        """
        # ── LLM-Synthesis: Inhaltliche Zusammenfassung ──
        results_text = []
        for task in tasks:
            status_icon = "✅" if task.status == "completed" else "❌"
            task_output = (task.result or task.error or "(kein Ergebnis)")[:1500]
            results_text.append(f"{status_icon} **{task.title}** ({task.assignee}):\n{task_output}")

        prompt = (
            f"Der User hat folgende Frage gestellt:\n"
            f">>> {goal} <<<\n\n"
            f"Ein Team aus Agenten hat dazu recherchiert. Hier sind die Ergebnisse:\n\n"
            f"TASK-ERGEBNISSE:\n" + "\n\n---\n\n".join(results_text) + "\n\n"
            f"DEINE AUFGABE: Beantworte die Frage des Users basierend auf den Ergebnissen.\n\n"
            f"Format (Markdown):\n\n"
            f"## Antwort\n"
            f"Beantworte die Frage DIREKT und KONKRET basierend auf den gefundenen Daten.\n"
            f"Nenne Confluence-Seiten, Dateipfade, Funktionsnamen, IDs wo verfuegbar.\n\n"
            f"## Details\n"
            f"- Konkrete Findings mit Verweisen (`dateiname`, Page-ID, Funktionsname)\n"
            f"- Nur Informationen die zur Beantwortung der Frage relevant sind\n\n"
            f"## Offene Punkte\n"
            f"- Nur wenn die Frage nicht vollstaendig beantwortet werden konnte\n"
            f"- Was fehlt noch und wo koennte man es finden?\n\n"
            f"ANTI-PATTERNS (VERBOTEN):\n"
            f"- 'Es wurden relevante Informationen gefunden' → WAS genau?\n"
            f"- 'Die Analyse wurde durchgefuehrt' → WAS ergab sie?\n"
            f"- Informationen die NICHT zur Frage passen → WEGLASSEN\n\n"
            f"Nutze Markdown: ##, **, -, `code`. Maximal 2000 Zeichen."
        )

        logger.info(f"[MultiAgent] Synthesis: {len(tasks)} Tasks, Prompt {len(prompt)} Zeichen")

        try:
            llm_summary, p_tk, c_tk = await default_llm_client.chat_quick_with_usage(
                messages=[{"role": "user", "content": prompt}],
                model=self._model,
                temperature=0.2,
                max_tokens=2048,
            )
            self._total_tokens += p_tk + c_tk
            self._total_llm_calls += 1
        except Exception as e:
            logger.error(f"[MultiAgent] Synthesis fehlgeschlagen: {e}")
            llm_summary = "\n\n".join(results_text)

        # ── Statistik-Tabelle ──
        stats_table = self._build_stats_table(tasks)

        # ── Mermaid-Diagramme ──
        flow_chart = self._build_dependency_flowchart(tasks)
        pie_chart = self._build_result_pie_chart(tasks)

        # ── Alles zusammensetzen ──
        parts = [llm_summary]

        # Agent-generierte Diagramme (inhaltlich, z.B. Architektur, Sequenz)
        # Validierung: LLM-generierte Mermaid-Syntax kann kaputt sein
        for agent_name, (diagram, title) in self._agent_diagrams.items():
            if diagram and diagram.strip():
                sanitized = self._sanitize_mermaid(diagram.strip())
                if sanitized:
                    parts.append(f"\n\n### {title}\n\n```mermaid\n{sanitized}\n```")

        if stats_table:
            parts.append(f"\n\n---\n\n### Task-Statistik\n\n{stats_table}")

        if flow_chart:
            parts.append(f"\n\n### Task-Ablauf\n\n```mermaid\n{flow_chart}\n```")

        if pie_chart:
            parts.append(f"\n\n### Ergebnis\n\n```mermaid\n{pie_chart}\n```")

        return "".join(parts)

    def _build_stats_table(self, tasks: List[TeamTask]) -> str:
        """Generiert eine Markdown-Statistik-Tabelle."""
        if not tasks:
            return ""

        lines = [
            "| # | Task | Agent | Status | Ergebnis |",
            "|---|------|-------|--------|----------|",
        ]
        for i, task in enumerate(tasks, 1):
            status = "✅" if task.status == "completed" else "❌"
            # Ergebnis kuerzen fuer Tabelle
            result_preview = (task.result or task.error or "-")[:80].replace("\n", " ").replace("|", "\\|")
            title = task.title[:40].replace("|", "\\|")
            lines.append(f"| {i} | {title} | `{task.assignee}` | {status} | {result_preview} |")

        return "\n".join(lines)

    def _build_dependency_flowchart(self, tasks: List[TeamTask]) -> str:
        """Generiert einen Mermaid-Flowchart aus Task-Dependencies."""
        if not tasks:
            return ""

        lines = ["flowchart TD"]

        # Node-Definitionen mit Status-Styling
        for task in tasks:
            # Mermaid-sichere Labels: keine HTML-Tags, keine Sonderzeichen die Node-Shapes oeffnen
            label = re.sub(r'["\[\](){}/<>]', ' ', task.title[:30]).strip()
            label = re.sub(r'\s+', ' ', label)
            agent = task.assignee
            lines.append(f'    {task.id}["{label} | {agent}"]')

        # Dependency-Pfeile
        has_edges = False
        for task in tasks:
            for dep_id in task.depends_on:
                lines.append(f"    {dep_id} --> {task.id}")
                has_edges = True

        # Wenn keine Dependencies: Tasks einfach auflisten (kein Flowchart noetig)
        if not has_edges and len(tasks) <= 2:
            return ""

        # Styling nach Status via classDef + class (style unterstützt keine Komma-Trennung)
        completed = [t.id for t in tasks if t.status == "completed"]
        failed = [t.id for t in tasks if t.status == "failed"]

        if completed or failed:
            lines.append('    classDef ok fill:#4caf50,color:#fff')
            lines.append('    classDef err fill:#f44336,color:#fff')
        if completed:
            lines.append(f"    class {','.join(completed)} ok")
        if failed:
            lines.append(f"    class {','.join(failed)} err")

        return "\n".join(lines)

    def _build_result_pie_chart(self, tasks: List[TeamTask]) -> str:
        """Generiert ein Mermaid Pie-Chart fuer die Erfolgsquote."""
        if not tasks or len(tasks) < 2:
            return ""

        completed = sum(1 for t in tasks if t.status == "completed")
        failed = sum(1 for t in tasks if t.status == "failed")
        other = len(tasks) - completed - failed

        # Nur anzeigen wenn es etwas Interessantes zu zeigen gibt (nicht 100% Erfolg bei wenigen Tasks)
        if failed == 0 and other == 0 and len(tasks) <= 3:
            return ""

        lines = ['pie title Task-Ergebnisse']
        if completed:
            lines.append(f'    "Erfolgreich ({completed})" : {completed}')
        if failed:
            lines.append(f'    "Fehlgeschlagen ({failed})" : {failed}')
        if other:
            lines.append(f'    "Sonstige ({other})" : {other}')

        return "\n".join(lines)

    # ══════════════════════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _sanitize_mermaid(source: str) -> str:
        """Bereinigt LLM-generierten Mermaid-Code. Gibt '' zurueck wenn nicht reparierbar."""
        if not source or not source.strip():
            return ""

        lines = source.strip().split('\n')
        first_line = lines[0].strip().lower()

        # Muss mit gueltigem Mermaid-Keyword beginnen
        valid_starts = (
            'flowchart', 'graph', 'sequencediagram', 'classdiagram',
            'statediagram', 'erdiagram', 'gantt', 'pie', 'gitgraph',
            'mindmap', 'timeline', 'sankey', 'xychart', 'block-beta',
        )
        if not any(first_line.startswith(kw) for kw in valid_starts):
            return ""

        # Fuer Flowcharts: Node-Labels sanitizen
        if first_line.startswith(('flowchart', 'graph')):
            sanitized_lines = [lines[0]]
            for line in lines[1:]:
                # Labels in [...], (...), {...} sanitizen: Sonderzeichen in Quotes
                # Unquoted Labels mit Sonderzeichen fixen
                line = re.sub(
                    r'\[([^\]"]*[(){}/<>][^\]"]*)\]',
                    lambda m: '["' + re.sub(r'[(){}/<>]', ' ', m.group(1)).strip() + '"]',
                    line,
                )
                sanitized_lines.append(line)
            return '\n'.join(sanitized_lines)

        return source.strip()

    async def _emit(self, data: Dict):
        """Emittiert ein Progress-Event."""
        if self._on_progress:
            try:
                await self._on_progress(data)
            except Exception as e:
                logger.debug(f"[MultiAgent] Progress-Callback Fehler: {e}")
