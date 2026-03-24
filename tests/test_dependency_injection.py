"""
Tests für erweiterte Dependency Injection.

Verifiziert dass:
1. Request-scoped Dependencies korrekt funktionieren
2. Lifespan-Management funktioniert
3. Service-Registry funktioniert
4. Alle DI-Typen korrekt importierbar sind
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestSessionContext:
    """Tests für SessionContext Request-Scoped Dependency."""

    def test_session_context_creation(self):
        """SessionContext kann erstellt werden."""
        from app.core.dependencies import SessionContext

        mock_memory = MagicMock()
        mock_settings = MagicMock()

        context = SessionContext(
            session_id="test-session-123",
            memory=mock_memory,
            settings=mock_settings,
        )

        assert context.session_id == "test-session-123"
        assert context.memory == mock_memory

    def test_session_context_lazy_loading(self):
        """SessionContext lädt Services lazy."""
        from app.core.dependencies import SessionContext

        mock_memory = MagicMock()
        mock_settings = MagicMock()

        context = SessionContext(
            session_id="test-session",
            memory=mock_memory,
            settings=mock_settings,
        )

        # _task_tracker sollte None sein bis zum ersten Zugriff
        assert context._task_tracker is None
        assert context._transcript_logger is None


class TestDependencyFactories:
    """Tests für Dependency Factories."""

    def test_get_session_context_factory(self):
        """get_session_context Factory funktioniert."""
        from app.core.dependencies import get_session_context, SessionContext

        # Mit expliziten Dependencies
        mock_memory = MagicMock()
        mock_settings = MagicMock()

        context = get_session_context(
            session_id="factory-test",
            memory=mock_memory,
            settings=mock_settings,
        )

        assert isinstance(context, SessionContext)
        assert context.session_id == "factory-test"

    def test_stores_are_dicts(self):
        """PDF/Log Stores sind Dicts."""
        from app.core.dependencies import get_pdf_store, get_log_store

        pdf_store = get_pdf_store()
        log_store = get_log_store()

        assert isinstance(pdf_store, dict)
        assert isinstance(log_store, dict)


class TestAttachmentCollector:
    """Tests für AttachmentCollector."""

    def test_attachment_collector_creation(self):
        """AttachmentCollector kann erstellt werden."""
        from app.core.dependencies import AttachmentCollector

        collector = AttachmentCollector(
            java_indexer=MagicMock(),
            python_indexer=MagicMock(),
            handbook_indexer=MagicMock(),
            pdf_store={},
            log_store={},
            settings=MagicMock(),
        )

        assert collector.java_indexer is not None
        assert collector.python_indexer is not None

    @pytest.mark.asyncio
    async def test_collect_pdf_attachments_empty(self):
        """collect_pdf_attachments gibt leere Liste bei fehlenden PDFs."""
        from app.core.dependencies import AttachmentCollector

        collector = AttachmentCollector(
            java_indexer=MagicMock(),
            python_indexer=MagicMock(),
            handbook_indexer=MagicMock(),
            pdf_store={},  # Leer
            log_store={},
            settings=MagicMock(),
        )

        attachments = await collector.collect_pdf_attachments(
            pdf_ids=["nonexistent"],
            query="test"
        )

        assert attachments == []

    @pytest.mark.asyncio
    async def test_collect_pdf_attachments_with_data(self):
        """collect_pdf_attachments findet vorhandene PDFs."""
        from app.core.dependencies import AttachmentCollector

        pdf_store = {
            "pdf-123": {
                "filename": "test.pdf",
                "text": "This is test content"
            }
        }

        mock_settings = MagicMock()
        mock_settings.index.max_search_results = 5

        collector = AttachmentCollector(
            java_indexer=MagicMock(),
            python_indexer=MagicMock(),
            handbook_indexer=MagicMock(),
            pdf_store=pdf_store,
            log_store={},
            settings=mock_settings,
        )

        attachments = await collector.collect_pdf_attachments(
            pdf_ids=["pdf-123"],
            query=""  # Kein Query = Volltext
        )

        assert len(attachments) == 1
        assert "test.pdf" in attachments[0].label


class TestServiceRegistry:
    """Tests für Service Registry."""

    def test_service_registry_singleton(self):
        """Service Registry ist Singleton."""
        from app.core.lifespan import get_service_registry

        registry1 = get_service_registry()
        registry2 = get_service_registry()

        assert registry1 is registry2

    def test_service_registration(self):
        """Services können registriert werden."""
        from app.core.lifespan import ServiceRegistry

        registry = ServiceRegistry()

        async def startup():
            return "started"

        async def shutdown():
            pass

        registry.register(
            "test_service",
            startup=startup,
            shutdown=shutdown,
            priority=50,
        )

        # Service sollte registriert sein
        assert "test_service" in registry._services

    @pytest.mark.asyncio
    async def test_startup_shutdown_order(self):
        """Services starten in Prioritäts-Reihenfolge."""
        from app.core.lifespan import ServiceRegistry

        startup_order = []

        async def make_startup(name):
            async def startup():
                startup_order.append(name)
                return name
            return startup

        registry = ServiceRegistry()

        # Registriere in falscher Reihenfolge
        registry.register("c", startup=await make_startup("c"), priority=30)
        registry.register("a", startup=await make_startup("a"), priority=10)
        registry.register("b", startup=await make_startup("b"), priority=20)

        await registry.startup_all()

        # Sollte in Prioritäts-Reihenfolge sein
        assert startup_order == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_shutdown_reverse_order(self):
        """Services beenden in umgekehrter Startreihenfolge."""
        from app.core.lifespan import ServiceRegistry

        shutdown_order = []

        async def make_startup(name):
            async def startup():
                return name
            return startup

        async def make_shutdown(name):
            async def shutdown():
                shutdown_order.append(name)
            return shutdown

        registry = ServiceRegistry()

        registry.register(
            "a",
            startup=await make_startup("a"),
            shutdown=await make_shutdown("a"),
            priority=10
        )
        registry.register(
            "b",
            startup=await make_startup("b"),
            shutdown=await make_shutdown("b"),
            priority=20
        )

        await registry.startup_all()
        await registry.shutdown_all()

        # Shutdown sollte umgekehrt sein
        assert shutdown_order == ["b", "a"]


class TestDependencyImports:
    """Tests dass alle Dependencies importierbar sind."""

    def test_all_dependencies_importable(self):
        """Alle DI-Typen können importiert werden."""
        from app.core.dependencies import (
            # Settings
            get_settings,
            Settings,
            # Code Indexers
            get_java_indexer,
            get_python_indexer,
            JavaIndexer,
            PythonIndexer,
            CodeIndexer,
            # Knowledge
            get_handbook_indexer,
            HandbookIndexer,
            # External
            get_confluence_client,
            get_jira_client,
            ConfluenceClient,
            JiraClient,
            # Storage
            get_memory_store,
            MemoryStore,
            # LLM
            get_llm_client,
            LLMClient,
            # Request-Scoped
            SessionContext,
            SessionContextDep,
            # Agent
            AgentOrchestrator,
            SkillManager,
            # Attachments
            AttachmentCollector,
            AttachmentCollectorDep,
        )

        # Alle Factories sollten callable sein
        assert callable(get_settings)
        assert callable(get_java_indexer)
        assert callable(get_confluence_client)
        assert callable(get_memory_store)

    def test_lifespan_imports(self):
        """Lifespan-Module können importiert werden."""
        from app.core.lifespan import (
            ServiceRegistry,
            get_service_registry,
            create_lifespan,
            get_system_health,
            register_standard_services,
        )

        assert callable(create_lifespan)
        assert callable(get_service_registry)
        assert callable(get_system_health)


class TestMockUtilities:
    """Tests für Test-Utilities."""

    def test_create_mock_indexer_with_results(self):
        """Mock-Indexer mit Ergebnissen."""
        from app.core.dependencies import create_mock_indexer

        mock = create_mock_indexer([
            {"file_path": "A.java"},
            {"file_path": "B.java"},
            {"file_path": "C.java"},
        ])

        # top_k limitiert Ergebnisse
        results = mock.search("test", top_k=2)
        assert len(results) == 2
        assert results[0]["file_path"] == "A.java"

    @pytest.mark.asyncio
    async def test_mock_memory_store_operations(self):
        """Mock-MemoryStore CRUD-Operationen."""
        from app.core.dependencies import create_mock_memory_store

        store = create_mock_memory_store()

        # Remember
        id1 = await store.remember("key1", "value1")
        id2 = await store.remember("key2", "value2")

        assert id1 is not None
        assert id2 is not None

        # Recall
        results = await store.recall("value1")
        assert len(results) == 1

        # Forget
        forgotten = await store.forget(id1)
        assert forgotten is True

        # Nach Forget nicht mehr findbar
        results_after = await store.recall("value1")
        assert len(results_after) == 0
