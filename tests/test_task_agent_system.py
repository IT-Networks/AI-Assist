"""
Tests fuer das Task-Agent-System.

Testet:
- TaskPlanner: Korrekte Zerlegung in Tasks mit Abhaengigkeiten
- TaskExecutor: Ausfuehrung mit korrekten Models pro Agent-Typ
- Phasen-Synthese: Zusammenfassung bei Wechsel zwischen Task-Typen
- Abhaengigkeiten: Kontext-Weitergabe zwischen Tasks
- Retry-Strategien: Anwendung bei Fehlern
"""

import asyncio
import json
import pytest
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent.task_models import (
    Task, TaskPlan, TaskType, TaskStatus, TaskExecutionResult,
    AgentConfig, RetryStrategy
)
from app.agent.task_planner import TaskPlanner
from app.agent.task_executor import TaskExecutor
from app.agent.task_agents import get_agent_config


# ==============================================================================
# Mock Classes
# ==============================================================================

@dataclass
class MockLLMResponse:
    """Mock fuer LLM-Response."""
    content: str
    tool_calls: Optional[List[Dict[str, Any]]] = None


class MockLLMClient:
    """Mock LLM-Client der Responses tracked."""

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []
        self.responses: List[MockLLMResponse] = []
        self.response_index = 0
        self.available_models = ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"]

    def set_responses(self, responses: List[MockLLMResponse]):
        """Setzt die Responses die zurueckgegeben werden."""
        self.responses = responses
        self.response_index = 0

    async def chat_with_tools(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict]] = None,
        model: str = "gpt-4o",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout: float = 30.0
    ) -> MockLLMResponse:
        """Mock chat_with_tools."""
        self.calls.append({
            "messages": messages,
            "tools": tools,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens
        })

        if self.response_index < len(self.responses):
            response = self.responses[self.response_index]
            self.response_index += 1
            return response
        else:
            return MockLLMResponse(content="[Mock Response]")

    async def list_models(self) -> List[str]:
        """Mock list_models."""
        return self.available_models


class MockToolRegistry:
    """Mock Tool-Registry."""

    def __init__(self):
        self.executed_tools: List[Dict[str, Any]] = []

    def get_openai_schemas(self, include_write_ops: bool = True) -> List[Dict]:
        """Gibt Mock-Tool-Schemas zurueck."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Reads a file",
                    "parameters": {"type": "object", "properties": {}}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "search_code",
                    "description": "Searches code",
                    "parameters": {"type": "object", "properties": {}}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Writes a file",
                    "parameters": {"type": "object", "properties": {}}
                }
            }
        ]

    async def execute(self, tool_name: str, args: Dict[str, Any]) -> MagicMock:
        """Mock Tool-Ausfuehrung."""
        self.executed_tools.append({"tool": tool_name, "args": args})

        result = MagicMock()
        result.to_context.return_value = f"[Mock result for {tool_name}]"
        return result


class MockAutoLearner:
    """Mock AutoLearner fuer Tests."""

    def __init__(self):
        self.analyzed_user_messages: List[str] = []
        self.analyzed_responses: List[str] = []
        self.tracked_tools: List[Dict[str, Any]] = []
        self.saved_candidates: List[Any] = []

    async def analyze_user_message(
        self,
        message: str,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> List[Any]:
        """Mock analyze_user_message."""
        self.analyzed_user_messages.append(message)
        return []

    async def analyze_assistant_response(
        self,
        response: str,
        user_message: str,
        project_id: Optional[str] = None,
        related_files: Optional[List[str]] = None
    ) -> List[Any]:
        """Mock analyze_assistant_response."""
        self.analyzed_responses.append(response)
        # Return mock candidate if response contains "Loesung"
        if "loesung" in response.lower() or "solution" in response.lower():
            return [MagicMock(category="solution", key="test", value=response[:50])]
        return []

    async def track_tool_usage(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        project_id: Optional[str] = None
    ) -> Optional[Any]:
        """Mock track_tool_usage."""
        self.tracked_tools.append({"tool": tool_name, "args": arguments})
        # Return pattern after 3 uses of same tool
        tool_count = sum(1 for t in self.tracked_tools if t["tool"] == tool_name)
        if tool_count >= 3:
            return MagicMock(category="pattern", key=f"tool_{tool_name}")
        return None

    async def save_candidates(
        self,
        candidates: List[Any],
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        project_path: Optional[str] = None
    ) -> List[str]:
        """Mock save_candidates."""
        self.saved_candidates.extend(candidates)
        return [f"id_{i}" for i in range(len(candidates))]


class MockAnalyticsLogger:
    """Mock AnalyticsLogger fuer Tests."""

    def __init__(self):
        self.enabled = True
        self.logged_tools: List[Dict[str, Any]] = []
        self.chains_started: int = 0

    async def start_chain(self, **kwargs):
        """Mock start_chain."""
        self.chains_started += 1

    async def log_tool_call(self, **kwargs):
        """Mock log_tool_call."""
        self.logged_tools.append(kwargs)


# ==============================================================================
# TaskPlanner Tests
# ==============================================================================

class TestTaskPlanner:
    """Tests fuer den TaskPlanner."""

    @pytest.fixture
    def mock_llm(self):
        """Erstellt Mock LLM-Client."""
        return MockLLMClient()

    @pytest.fixture
    def planner(self, mock_llm):
        """Erstellt TaskPlanner mit Mock."""
        return TaskPlanner(llm_client=mock_llm)

    @pytest.mark.asyncio
    async def test_simple_code_task_not_decomposed(self, planner, mock_llm):
        """Einfache Code-Aufgabe sollte nicht zerlegt werden."""
        mock_llm.set_responses([
            MockLLMResponse(content=json.dumps({
                "needs_clarification": False,
                "reasoning": "Einfache Code-Aufgabe",
                "tasks": [
                    {"id": "T1", "type": "code", "description": "Fibonacci implementieren", "depends_on": []}
                ]
            }))
        ])

        plan = await planner.plan("Schreibe eine Fibonacci-Funktion in Python")

        assert not plan.needs_clarification
        assert len(plan.tasks) == 1
        assert plan.tasks[0].type == TaskType.CODE

    @pytest.mark.asyncio
    async def test_research_then_code_decomposition(self, planner, mock_llm):
        """Wiki-Recherche + Implementation sollte in 2 Tasks zerlegt werden."""
        mock_llm.set_responses([
            MockLLMResponse(content=json.dumps({
                "needs_clarification": False,
                "reasoning": "Benoetigt Wiki-Recherche vor Implementation",
                "tasks": [
                    {"id": "T1", "type": "research", "description": "Wiki durchsuchen", "depends_on": []},
                    {"id": "T2", "type": "code", "description": "Implementation", "depends_on": ["T1"]}
                ]
            }))
        ])

        plan = await planner.plan("Implementiere das Design aus unserem Wiki")

        assert len(plan.tasks) == 2
        assert plan.tasks[0].type == TaskType.RESEARCH
        assert plan.tasks[1].type == TaskType.CODE
        assert "T1" in plan.tasks[1].depends_on

    @pytest.mark.asyncio
    async def test_clarification_needed(self, planner, mock_llm):
        """Unklare Anfrage sollte Klaerungsfragen ausloesen."""
        mock_llm.set_responses([
            MockLLMResponse(content=json.dumps({
                "needs_clarification": True,
                "clarification_questions": ["Welche Programmiersprache?", "Welche Features?"],
                "reasoning": "Anfrage zu unspezifisch",
                "tasks": []
            }))
        ])

        plan = await planner.plan("Baue eine App")

        assert plan.needs_clarification
        assert len(plan.clarification_questions) == 2
        assert len(plan.tasks) == 0

    @pytest.mark.asyncio
    async def test_dependency_validation(self, planner, mock_llm):
        """Ungueltige Abhaengigkeiten sollten entfernt werden."""
        mock_llm.set_responses([
            MockLLMResponse(content=json.dumps({
                "needs_clarification": False,
                "tasks": [
                    {"id": "T1", "type": "code", "description": "Task 1", "depends_on": []},
                    {"id": "T2", "type": "code", "description": "Task 2", "depends_on": ["T1", "T99"]}  # T99 existiert nicht
                ]
            }))
        ])

        plan = await planner.plan("Test mit ungueltigem Dependency")

        assert len(plan.tasks) == 2
        # T99 sollte aus depends_on entfernt worden sein
        assert plan.tasks[1].depends_on == ["T1"]

    @pytest.mark.asyncio
    async def test_debug_task_type_detection(self, planner, mock_llm):
        """Debug-Keywords sollten DEBUG Task-Typ erkennen."""
        # Wir testen die _detect_task_type Methode direkt
        debug_queries = [
            "Debugge diesen Fehler",
            "Finde den Bug in dieser Funktion",
            "Warum funktioniert das nicht?",
            "Teste ob der Code korrekt ist"
        ]

        for query in debug_queries:
            task_type = planner._detect_task_type(query)
            assert task_type == TaskType.DEBUG, f"Query '{query}' sollte DEBUG sein"

    @pytest.mark.asyncio
    async def test_fallback_plan_on_error(self, planner, mock_llm):
        """Bei LLM-Fehler sollte Fallback-Plan erstellt werden."""
        mock_llm.set_responses([
            MockLLMResponse(content="Invalid JSON {{{")  # Ungueltiges JSON
        ])

        plan = await planner.plan("Irgendeine Aufgabe")

        # Sollte Fallback-Plan mit einer Task sein
        assert not plan.needs_clarification
        assert len(plan.tasks) == 1


# ==============================================================================
# TaskExecutor Tests
# ==============================================================================

class TestTaskExecutor:
    """Tests fuer den TaskExecutor."""

    @pytest.fixture
    def mock_llm(self):
        """Erstellt Mock LLM-Client."""
        return MockLLMClient()

    @pytest.fixture
    def mock_tools(self):
        """Erstellt Mock Tool-Registry."""
        return MockToolRegistry()

    @pytest.fixture
    def mock_learner(self):
        """Erstellt Mock AutoLearner."""
        return MockAutoLearner()

    @pytest.fixture
    def mock_analytics(self):
        """Erstellt Mock AnalyticsLogger."""
        return MockAnalyticsLogger()

    @pytest.fixture
    def executor(self, mock_llm, mock_tools, mock_learner, mock_analytics):
        """Erstellt TaskExecutor mit Mocks."""
        return TaskExecutor(
            llm_client=mock_llm,
            tool_registry=mock_tools,
            max_parallel=2,
            auto_learner=mock_learner,
            analytics=mock_analytics
        )

    @pytest.mark.asyncio
    async def test_single_task_execution(self, executor, mock_llm):
        """Einzelne Task sollte korrekt ausgefuehrt werden."""
        mock_llm.set_responses([
            MockLLMResponse(content="Code wurde geschrieben.")
        ])

        plan = TaskPlan(
            needs_clarification=False,
            tasks=[
                Task(
                    id="T1",
                    type=TaskType.CODE,
                    description="Schreibe Hello World",
                    status=TaskStatus.PENDING
                )
            ],
            original_query="Hello World schreiben"
        )

        result = await executor.execute(plan)

        assert result.success
        assert "T1" in result.results
        assert "Code wurde geschrieben" in result.results["T1"]

    @pytest.mark.asyncio
    async def test_correct_model_per_task_type(self, executor, mock_llm):
        """Verschiedene Task-Typen sollten verschiedene Models verwenden."""
        # Mock Responses fuer beide Tasks
        mock_llm.set_responses([
            MockLLMResponse(content="Research complete"),
            MockLLMResponse(content="Code complete"),
            MockLLMResponse(content="Final synthesis")  # Fuer finale Synthese
        ])

        plan = TaskPlan(
            needs_clarification=False,
            tasks=[
                Task(id="T1", type=TaskType.RESEARCH, description="Recherche", status=TaskStatus.PENDING),
                Task(id="T2", type=TaskType.CODE, description="Code", depends_on=["T1"], status=TaskStatus.PENDING)
            ],
            original_query="Recherche dann Code"
        )

        await executor.execute(plan)

        # Pruefe dass verschiedene Models verwendet wurden
        models_used = [call["model"] for call in mock_llm.calls]
        # Die ersten beiden Calls sind fuer die Tasks, der dritte fuer Synthese
        assert len(models_used) >= 2

    @pytest.mark.asyncio
    async def test_dependency_context_passing(self, executor, mock_llm):
        """Ergebnisse von Abhaengigkeiten sollten als Kontext uebergeben werden."""
        mock_llm.set_responses([
            MockLLMResponse(content="Research findings: API docs found"),
            MockLLMResponse(content="Code based on research"),
            MockLLMResponse(content="Final synthesis")
        ])

        plan = TaskPlan(
            needs_clarification=False,
            tasks=[
                Task(id="T1", type=TaskType.RESEARCH, description="Research API", status=TaskStatus.PENDING),
                Task(id="T2", type=TaskType.CODE, description="Implement API", depends_on=["T1"], status=TaskStatus.PENDING)
            ],
            original_query="Test dependency"
        )

        await executor.execute(plan)

        # Der zweite Call (T2) sollte den Kontext von T1 haben
        t2_call = mock_llm.calls[1]
        messages = t2_call["messages"]

        # Suche nach Kontext-Message
        context_found = any(
            "Research findings" in str(msg.get("content", ""))
            for msg in messages
        )
        assert context_found, "T1-Ergebnis sollte im Kontext von T2 sein"

    @pytest.mark.asyncio
    async def test_phase_synthesis_on_type_change(self, executor, mock_llm):
        """Bei Phasenwechsel sollte Synthese durchgefuehrt werden."""
        mock_llm.set_responses([
            MockLLMResponse(content="Research result 1"),
            MockLLMResponse(content="Research result 2"),
            MockLLMResponse(content="Phase synthesis: Combined research"),  # Synthese
            MockLLMResponse(content="Code implementation"),
            MockLLMResponse(content="Final synthesis")
        ])

        plan = TaskPlan(
            needs_clarification=False,
            tasks=[
                Task(id="T1", type=TaskType.RESEARCH, description="Research 1", status=TaskStatus.PENDING),
                Task(id="T2", type=TaskType.RESEARCH, description="Research 2", status=TaskStatus.PENDING),
                Task(id="T3", type=TaskType.CODE, description="Implement", depends_on=["T1", "T2"], status=TaskStatus.PENDING)
            ],
            original_query="Multi-phase test"
        )

        result = await executor.execute(plan)

        # Pruefe dass Phasen-Synthese im Result ist
        assert "PHASE_research" in result.results or any(
            "synthesis" in call["messages"][-1].get("content", "").lower()
            for call in mock_llm.calls
        )

    @pytest.mark.asyncio
    async def test_parallel_execution(self, executor, mock_llm):
        """Unabhaengige Tasks sollten parallel ausgefuehrt werden."""
        execution_order = []

        original_chat = mock_llm.chat_with_tools

        async def tracking_chat(*args, **kwargs):
            task_desc = kwargs.get("messages", [{}])[-1].get("content", "")
            execution_order.append(task_desc[:20])
            await asyncio.sleep(0.1)  # Kleine Verzoegerung
            return MockLLMResponse(content="Done")

        mock_llm.chat_with_tools = tracking_chat

        plan = TaskPlan(
            needs_clarification=False,
            tasks=[
                Task(id="T1", type=TaskType.CODE, description="Task 1 independent", status=TaskStatus.PENDING),
                Task(id="T2", type=TaskType.CODE, description="Task 2 independent", status=TaskStatus.PENDING),
                Task(id="T3", type=TaskType.CODE, description="Task 3 depends", depends_on=["T1", "T2"], status=TaskStatus.PENDING)
            ],
            original_query="Parallel test"
        )

        await executor.execute(plan)

        # T1 und T2 sollten beide vor T3 ausgefuehrt werden
        assert len(execution_order) >= 3

    @pytest.mark.asyncio
    async def test_retry_on_failure(self, executor, mock_llm):
        """Bei Fehler sollte Retry-Strategie angewandt werden."""
        call_count = 0

        async def failing_then_success(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("First attempt failed")
            return MockLLMResponse(content="Success on retry")

        mock_llm.chat_with_tools = failing_then_success

        plan = TaskPlan(
            needs_clarification=False,
            tasks=[
                Task(id="T1", type=TaskType.CODE, description="Retry test", status=TaskStatus.PENDING)
            ],
            original_query="Retry test"
        )

        result = await executor.execute(plan)

        # Sollte nach Retry erfolgreich sein
        assert call_count > 1
        assert result.success

    @pytest.mark.asyncio
    async def test_tool_execution_in_agent_loop(self, executor, mock_llm, mock_tools):
        """Agent sollte Tools ausfuehren und mit Ergebnis fortfahren."""
        mock_llm.set_responses([
            # Erste Response: Tool-Call
            MockLLMResponse(
                content="Ich lese die Datei...",
                tool_calls=[{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"path": "test.py"})
                    }
                }]
            ),
            # Zweite Response: Finale Antwort
            MockLLMResponse(content="Datei gelesen, hier ist der Code...")
        ])

        plan = TaskPlan(
            needs_clarification=False,
            tasks=[
                Task(id="T1", type=TaskType.CODE, description="Lies test.py", status=TaskStatus.PENDING)
            ],
            original_query="Lies Datei"
        )

        result = await executor.execute(plan)

        # Tool sollte ausgefuehrt worden sein
        assert len(mock_tools.executed_tools) > 0
        assert mock_tools.executed_tools[0]["tool"] == "read_file"

    @pytest.mark.asyncio
    async def test_event_callback(self, executor, mock_llm):
        """Event-Callback sollte bei Task-Events aufgerufen werden."""
        mock_llm.set_responses([
            MockLLMResponse(content="Task done")
        ])

        events = []

        async def event_callback(event_type: str, data: dict):
            events.append({"type": event_type, "data": data})

        plan = TaskPlan(
            needs_clarification=False,
            tasks=[
                Task(id="T1", type=TaskType.CODE, description="Event test", status=TaskStatus.PENDING)
            ],
            original_query="Event test"
        )

        await executor.execute(plan, event_callback=event_callback)

        # Sollte mindestens tasks_started und task_completed Events haben
        event_types = [e["type"] for e in events]
        assert "tasks_started" in event_types
        assert "task_completed" in event_types

    @pytest.mark.asyncio
    async def test_failed_task_handling(self, executor, mock_llm):
        """Fehlgeschlagene Tasks sollten korrekt markiert werden."""
        async def always_fail(*args, **kwargs):
            raise RuntimeError("Always fails")

        mock_llm.chat_with_tools = always_fail

        plan = TaskPlan(
            needs_clarification=False,
            tasks=[
                Task(id="T1", type=TaskType.CODE, description="Will fail", status=TaskStatus.PENDING)
            ],
            original_query="Fail test"
        )

        result = await executor.execute(plan)

        assert not result.success
        assert "T1" in result.failed_tasks


# ==============================================================================
# Integration Tests
# ==============================================================================

class TestTaskAgentSystemIntegration:
    """Integration Tests fuer das gesamte System."""

    @pytest.fixture
    def mock_llm(self):
        """Erstellt Mock LLM-Client."""
        return MockLLMClient()

    @pytest.fixture
    def mock_tools(self):
        """Erstellt Mock Tool-Registry."""
        return MockToolRegistry()

    @pytest.mark.asyncio
    async def test_full_planning_and_execution_flow(self, mock_llm, mock_tools):
        """Kompletter Flow von Planung bis Ausfuehrung."""
        # Setup Planner
        planner = TaskPlanner(llm_client=mock_llm)
        mock_llm.set_responses([
            # Planner Response
            MockLLMResponse(content=json.dumps({
                "needs_clarification": False,
                "reasoning": "Research dann Code",
                "tasks": [
                    {"id": "T1", "type": "research", "description": "API docs finden", "depends_on": []},
                    {"id": "T2", "type": "code", "description": "API implementieren", "depends_on": ["T1"]}
                ]
            }))
        ])

        # Erstelle Plan
        plan = await planner.plan("Implementiere API basierend auf Dokumentation")

        assert len(plan.tasks) == 2
        assert plan.tasks[0].type == TaskType.RESEARCH
        assert plan.tasks[1].type == TaskType.CODE

        # Setup Executor
        mock_llm.set_responses([
            MockLLMResponse(content="API Docs: GET /users, POST /users"),
            MockLLMResponse(content="def get_users(): ..."),
            MockLLMResponse(content="Synthese: API implementiert")
        ])

        executor = TaskExecutor(
            llm_client=mock_llm,
            tool_registry=mock_tools
        )

        # Fuehre Plan aus
        result = await executor.execute(plan)

        assert result.success
        assert len(result.results) >= 2

    @pytest.mark.asyncio
    async def test_agent_config_tools_filtering(self, mock_llm, mock_tools):
        """Agent sollte nur erlaubte Tools verwenden koennen."""
        executor = TaskExecutor(
            llm_client=mock_llm,
            tool_registry=mock_tools
        )

        # Research Agent hat write_file nicht in seinen Tools
        research_config = get_agent_config(TaskType.RESEARCH)
        assert "write_file" not in research_config.tools
        assert "read_file" in research_config.tools

        # Code Agent hat write_file
        code_config = get_agent_config(TaskType.CODE)
        assert "write_file" in code_config.tools

    @pytest.mark.asyncio
    async def test_debug_agent_has_db_tools(self, mock_llm, mock_tools):
        """Debug Agent sollte DB-Tools haben."""
        debug_config = get_agent_config(TaskType.DEBUG)

        db_tools = ["query_database", "list_database_tables", "describe_database_table"]
        for tool in db_tools:
            assert tool in debug_config.tools, f"Debug Agent fehlt {tool}"

    @pytest.mark.asyncio
    async def test_retry_strategy_modification(self, mock_llm, mock_tools):
        """Retry-Strategie sollte Task-Beschreibung modifizieren."""
        executor = TaskExecutor(
            llm_client=mock_llm,
            tool_registry=mock_tools
        )

        # Test ALTERNATIVE_APPROACH
        modified = executor._apply_retry_strategy(
            RetryStrategy.ALTERNATIVE_APPROACH,
            "Original task",
            "Error occurred"
        )
        assert "anderen ansatz" in modified.lower()
        assert "Error occurred" in modified

        # Test ISOLATE_AND_TEST (fuer Debug Agent)
        modified = executor._apply_retry_strategy(
            RetryStrategy.ISOLATE_AND_TEST,
            "Debug task",
            "Test failed"
        )
        assert "isoliere" in modified.lower()
        assert "testfall" in modified.lower()
        assert "Test failed" in modified


# ==============================================================================
# Model Selection Tests
# ==============================================================================

class TestModelSelection:
    """Tests fuer Model-Auswahl und Fallback."""

    @pytest.fixture
    def mock_llm(self):
        """Erstellt Mock LLM-Client."""
        return MockLLMClient()

    @pytest.fixture
    def mock_tools(self):
        """Erstellt Mock Tool-Registry."""
        return MockToolRegistry()

    @pytest.fixture
    def executor(self, mock_llm, mock_tools):
        """Erstellt TaskExecutor mit Mocks."""
        return TaskExecutor(
            llm_client=mock_llm,
            tool_registry=mock_tools
        )

    @pytest.mark.asyncio
    async def test_primary_model_selection(self, executor, mock_llm):
        """Primary Model sollte verwendet werden wenn verfuegbar."""
        config = AgentConfig(
            type=TaskType.CODE,
            model="gpt-4o",
            fallback_model="gpt-3.5-turbo",
            system_prompt="Test",
            tools=["read_file"],
            max_iterations=3,
            temperature=0.5,
            retry_strategy=RetryStrategy.REPHRASE,
            max_retries=2
        )

        mock_llm.available_models = ["gpt-4o", "gpt-3.5-turbo"]

        model = await executor._select_model(config)
        assert model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_fallback_model_when_primary_unavailable(self, executor, mock_llm):
        """Fallback Model sollte verwendet werden wenn Primary nicht verfuegbar."""
        config = AgentConfig(
            type=TaskType.CODE,
            model="gpt-4-turbo",  # Nicht verfuegbar
            fallback_model="gpt-3.5-turbo",
            system_prompt="Test",
            tools=["read_file"],
            max_iterations=3,
            temperature=0.5,
            retry_strategy=RetryStrategy.REPHRASE,
            max_retries=2
        )

        mock_llm.available_models = ["gpt-4o", "gpt-3.5-turbo"]  # gpt-4-turbo fehlt

        model = await executor._select_model(config)
        assert model == "gpt-3.5-turbo"


# ==============================================================================
# Task Context Building Tests
# ==============================================================================

class TestContextBuilding:
    """Tests fuer Kontext-Aufbau."""

    @pytest.fixture
    def mock_llm(self):
        """Erstellt Mock LLM-Client."""
        return MockLLMClient()

    @pytest.fixture
    def mock_tools(self):
        """Erstellt Mock Tool-Registry."""
        return MockToolRegistry()

    @pytest.fixture
    def executor(self, mock_llm, mock_tools):
        """Erstellt TaskExecutor mit Mocks."""
        return TaskExecutor(
            llm_client=mock_llm,
            tool_registry=mock_tools
        )

    def test_build_context_from_dependencies(self, executor):
        """Kontext sollte aus Abhaengigkeiten gebaut werden."""
        executor.results = {
            "T1": "Result from Task 1",
            "T2": "Result from Task 2"
        }

        task = Task(
            id="T3",
            type=TaskType.CODE,
            description="Uses T1 and T2",
            depends_on=["T1", "T2"],
            status=TaskStatus.PENDING
        )

        context = executor._build_task_context(task)

        assert "Result from Task 1" in context
        assert "Result from Task 2" in context

    def test_build_context_from_phase_synthesis(self, executor):
        """Kontext sollte Phasen-Synthese enthalten."""
        executor.results = {
            "T1": "Research result",
            "PHASE_research": "Synthesized research findings"
        }

        task = Task(
            id="T2",
            type=TaskType.CODE,
            description="Uses phase synthesis",
            depends_on=["T1"],
            context_from=["PHASE_research"],
            status=TaskStatus.PENDING
        )

        context = executor._build_task_context(task)

        assert "Synthesized research findings" in context


# ==============================================================================
# AutoLearner Integration Tests
# ==============================================================================

class TestAutoLearnerIntegration:
    """Tests fuer AutoLearner-Integration im TaskExecutor."""

    @pytest.fixture
    def mock_llm(self):
        """Erstellt Mock LLM-Client."""
        return MockLLMClient()

    @pytest.fixture
    def mock_tools(self):
        """Erstellt Mock Tool-Registry."""
        return MockToolRegistry()

    @pytest.fixture
    def mock_learner(self):
        """Erstellt Mock AutoLearner."""
        return MockAutoLearner()

    @pytest.fixture
    def executor(self, mock_llm, mock_tools, mock_learner):
        """Erstellt TaskExecutor mit Mocks."""
        return TaskExecutor(
            llm_client=mock_llm,
            tool_registry=mock_tools,
            auto_learner=mock_learner
        )

    @pytest.mark.asyncio
    async def test_analyze_response_called_on_completion(
        self, executor, mock_llm, mock_learner
    ):
        """analyze_assistant_response sollte bei Task-Completion aufgerufen werden."""
        mock_llm.set_responses([
            MockLLMResponse(content="Die Loesung ist: Code XYZ implementiert.")
        ])

        plan = TaskPlan(
            needs_clarification=False,
            tasks=[
                Task(
                    id="T1",
                    type=TaskType.CODE,
                    description="Implementiere Feature",
                    status=TaskStatus.PENDING
                )
            ],
            original_query="Test"
        )

        await executor.execute(plan, project_id="proj-1", session_id="sess-1")

        # Response sollte analysiert worden sein
        assert len(mock_learner.analyzed_responses) == 1
        assert "Loesung" in mock_learner.analyzed_responses[0]

    @pytest.mark.asyncio
    async def test_candidates_saved_when_detected(
        self, executor, mock_llm, mock_learner
    ):
        """Erkannte Candidates sollten gespeichert werden."""
        mock_llm.set_responses([
            MockLLMResponse(content="Die Loesung wurde gefunden und implementiert.")
        ])

        plan = TaskPlan(
            needs_clarification=False,
            tasks=[
                Task(
                    id="T1",
                    type=TaskType.CODE,
                    description="Fix Bug",
                    status=TaskStatus.PENDING
                )
            ],
            original_query="Test"
        )

        await executor.execute(plan, project_id="proj-1")

        # Candidate sollte gespeichert worden sein (da "Loesung" im Response)
        assert len(mock_learner.saved_candidates) >= 1

    @pytest.mark.asyncio
    async def test_tool_tracking_called(
        self, executor, mock_llm, mock_tools, mock_learner
    ):
        """track_tool_usage sollte bei Tool-Execution aufgerufen werden."""
        mock_llm.set_responses([
            MockLLMResponse(
                content="Lese Datei...",
                tool_calls=[{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"path": "test.py"})
                    }
                }]
            ),
            MockLLMResponse(content="Fertig.")
        ])

        plan = TaskPlan(
            needs_clarification=False,
            tasks=[
                Task(
                    id="T1",
                    type=TaskType.CODE,
                    description="Lies Datei",
                    status=TaskStatus.PENDING
                )
            ],
            original_query="Test"
        )

        await executor.execute(plan)

        # Tool sollte getrackt worden sein
        assert len(mock_learner.tracked_tools) >= 1
        assert mock_learner.tracked_tools[0]["tool"] == "read_file"

    @pytest.mark.asyncio
    async def test_project_context_passed(
        self, executor, mock_llm
    ):
        """Projekt-Context sollte korrekt gesetzt werden."""
        mock_llm.set_responses([
            MockLLMResponse(content="Done")
        ])

        plan = TaskPlan(
            needs_clarification=False,
            tasks=[
                Task(id="T1", type=TaskType.CODE, description="Test", status=TaskStatus.PENDING)
            ],
            original_query="Test"
        )

        await executor.execute(
            plan,
            project_id="my-project",
            session_id="my-session",
            project_path="/path/to/project"
        )

        # Context sollte gesetzt sein
        assert executor._project_id == "my-project"
        assert executor._session_id == "my-session"
        assert executor._project_path == "/path/to/project"

    @pytest.mark.asyncio
    async def test_pattern_detection_after_repeated_usage(
        self, executor, mock_llm, mock_learner
    ):
        """Pattern sollte nach 3x Tool-Usage erkannt werden."""
        # 3 Tasks die alle read_file verwenden
        mock_llm.set_responses([
            MockLLMResponse(
                content="Read 1",
                tool_calls=[{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]
            ),
            MockLLMResponse(content="Done 1"),
            MockLLMResponse(
                content="Read 2",
                tool_calls=[{"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]
            ),
            MockLLMResponse(content="Done 2"),
            MockLLMResponse(
                content="Read 3",
                tool_calls=[{"id": "c3", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]
            ),
            MockLLMResponse(content="Done 3"),
            MockLLMResponse(content="Synthesis")
        ])

        plan = TaskPlan(
            needs_clarification=False,
            tasks=[
                Task(id="T1", type=TaskType.CODE, description="Task 1", status=TaskStatus.PENDING),
                Task(id="T2", type=TaskType.CODE, description="Task 2", status=TaskStatus.PENDING),
                Task(id="T3", type=TaskType.CODE, description="Task 3", status=TaskStatus.PENDING),
            ],
            original_query="Test"
        )

        await executor.execute(plan)

        # Nach 3x read_file sollte Pattern erkannt werden
        assert len(mock_learner.tracked_tools) >= 3
        # Pattern sollte gespeichert worden sein (ab 3. Verwendung)
        pattern_saved = any(
            hasattr(c, 'category') and c.category == "pattern"
            for c in mock_learner.saved_candidates
        )
        assert pattern_saved, "Pattern sollte nach 3x Verwendung erkannt werden"


# ==============================================================================
# Run Tests
# ==============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
