"""
Tests für Service Layer Reorganization.

Verifiziert dass:
1. Alle Subpackages importierbar sind
2. Backwards-Kompatibilität erhalten bleibt
3. Re-Exports korrekt funktionieren
"""

import pytest


class TestSubpackageImports:
    """Tests für neue Subpackage-Struktur."""

    def test_indexing_subpackage(self):
        """Indexing-Subpackage ist importierbar."""
        from app.services.indexing import (
            JavaIndexer,
            get_java_indexer,
            PythonIndexer,
            get_python_indexer,
            PDFIndexer,
            get_pdf_indexer,
            HandbookIndexer,
            get_handbook_indexer,
        )
        assert callable(get_java_indexer)
        assert callable(get_python_indexer)

    def test_external_subpackage(self):
        """External-Subpackage ist importierbar."""
        from app.services.external import (
            ConfluenceClient,
            get_confluence_client,
            JiraClient,
            get_jira_client,
            ServiceNowClient,
            get_servicenow_client,
        )
        assert callable(get_confluence_client)
        assert callable(get_jira_client)

    def test_llm_subpackage(self):
        """LLM-Subpackage ist importierbar."""
        from app.services.llm import (
            llm_client,
            LLMClient,
            LLMCacheManager,
            get_cache_manager,
        )
        assert llm_client is not None

    def test_code_subpackage(self):
        """Code-Subpackage ist importierbar."""
        from app.services.code import (
            JavaReader,
            PythonReader,
            CodeSearchEngine,
            PomParser,
            FileManager,
        )
        assert JavaReader is not None
        assert PythonReader is not None

    def test_session_subpackage(self):
        """Session-Subpackage ist importierbar."""
        from app.services.session import (
            ContextManager,
            get_context_manager,
            MemoryStore,
            get_memory_store,
            load_chat,
            save_chat,
            TaskTracker,
            TranscriptLogger,
        )
        assert callable(get_memory_store)
        assert callable(load_chat)

    def test_analytics_subpackage(self):
        """Analytics-Subpackage ist importierbar."""
        from app.services.analytics import (
            AnalyticsLogger,
            get_analytics_logger,
            PerformanceTracker,
            TokenTracker,
            get_token_tracker,
        )
        assert callable(get_analytics_logger)

    def test_testing_subpackage(self):
        """Testing-Subpackage ist importierbar."""
        from app.services.testing import (
            TestExecutor,
            get_test_executor,
            TestExecutionService,
            get_test_execution_service,
            TestSessionManager,
            TestTemplateEngine,
        )
        assert callable(get_test_executor)

    def test_graph_subpackage(self):
        """Graph-Subpackage ist importierbar."""
        from app.services.graph import (
            KnowledgeGraphStore,
            get_knowledge_graph_store,
            GraphQueryService,
            get_graph_query_service,
            JavaGraphBuilder,
            PythonGraphBuilder,
        )
        assert callable(get_graph_query_service)

    def test_document_subpackage(self):
        """Document-Subpackage ist importierbar."""
        from app.services.document import (
            PDFReader,
            WLPLogParser,
            LogEntry,
            OutputFormatter,
        )
        assert PDFReader is not None

    def test_system_subpackage(self):
        """System-Subpackage ist importierbar."""
        from app.services.system import (
            UpdateService,
            SelfHealingEngine,
            get_self_healing_engine,
            Anonymizer,
            ScriptManager,
            DB2Client,
        )
        assert callable(get_self_healing_engine)


class TestBackwardsCompatibility:
    """Tests für Backwards-Kompatibilität."""

    def test_direct_java_indexer_import(self):
        """Direkter Import von java_indexer funktioniert."""
        from app.services.java_indexer import JavaIndexer, get_java_indexer
        assert callable(get_java_indexer)

    def test_direct_confluence_client_import(self):
        """Direkter Import von confluence_client funktioniert."""
        from app.services.confluence_client import (
            ConfluenceClient,
            get_confluence_client,
        )
        assert callable(get_confluence_client)

    def test_direct_llm_client_import(self):
        """Direkter Import von llm_client funktioniert."""
        from app.services.llm_client import llm_client, LLMClient
        assert llm_client is not None

    def test_direct_memory_store_import(self):
        """Direkter Import von memory_store funktioniert."""
        from app.services.memory_store import MemoryStore, get_memory_store
        assert callable(get_memory_store)


class TestMainModuleExports:
    """Tests für app.services Modul-Exports."""

    def test_subpackages_accessible_via_main_module(self):
        """Subpackages sind via app.services zugänglich."""
        from app.services import (
            indexing,
            external,
            llm,
            code,
            session,
            analytics,
            testing,
            graph,
            document,
            system,
        )
        assert indexing is not None
        assert external is not None

    def test_common_exports_via_main_module(self):
        """Häufig verwendete Exports sind via app.services zugänglich."""
        from app.services import (
            JavaIndexer,
            get_java_indexer,
            ConfluenceClient,
            llm_client,
            MemoryStore,
            get_memory_store,
        )
        assert callable(get_java_indexer)
        assert callable(get_memory_store)


class TestCrossImports:
    """Tests für korrekte Cross-Imports."""

    def test_no_circular_import_errors(self):
        """Keine zirkulären Import-Fehler."""
        # Alle Subpackages gleichzeitig importieren
        from app.services import (
            indexing,
            external,
            llm,
            code,
            session,
            analytics,
            testing,
            graph,
            document,
            system,
        )
        # Wenn wir hier ankommen, gibt es keine zirkulären Imports
        assert True

    def test_service_factories_return_correct_types(self):
        """Service-Factories geben korrekte Typen zurück."""
        from app.services.indexing import get_java_indexer, JavaIndexer

        indexer = get_java_indexer()
        assert isinstance(indexer, JavaIndexer)
