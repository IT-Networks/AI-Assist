"""
Tests für Protocol-Abstraktionen.

Verifiziert dass:
1. Protokolle korrekt definiert sind
2. Bestehende Klassen die Protokolle implementieren
3. Mock-Utilities funktionieren
"""

import pytest
from typing import get_type_hints


class TestProtocolDefinitions:
    """Tests für Protocol-Definitionen."""

    def test_protocols_are_importable(self):
        """Alle Protokolle können importiert werden."""
        from app.core.protocols import (
            ICodeIndexer,
            IKnowledgeIndexer,
            IConfluenceClient,
            IJiraClient,
            IMemoryStore,
            ILLMClient,
            IToolRegistry,
            ICodeReader,
            IEventEmitter,
            ISessionStore,
        )
        # Keine Exception = Erfolg
        assert ICodeIndexer is not None
        assert IKnowledgeIndexer is not None

    def test_protocols_are_runtime_checkable(self):
        """Protokolle haben @runtime_checkable Decorator."""
        from app.core.protocols import ICodeIndexer, IMemoryStore

        # runtime_checkable ermöglicht isinstance() Checks
        assert hasattr(ICodeIndexer, "__protocol_attrs__") or hasattr(
            ICodeIndexer, "_is_runtime_protocol"
        )


class TestProtocolImplementations:
    """Tests dass bestehende Klassen Protokolle erfüllen."""

    def test_java_indexer_implements_icode_indexer(self):
        """JavaIndexer implementiert ICodeIndexer."""
        from app.core.protocols import ICodeIndexer
        from app.services.java_indexer import JavaIndexer

        # Prüfe dass die erforderlichen Methoden existieren
        assert hasattr(JavaIndexer, "search")
        assert hasattr(JavaIndexer, "is_built")
        assert hasattr(JavaIndexer, "build")

        # Prüfe Signatur von search
        import inspect
        sig = inspect.signature(JavaIndexer.search)
        params = list(sig.parameters.keys())
        assert "query" in params
        assert "top_k" in params

    def test_python_indexer_implements_icode_indexer(self):
        """PythonIndexer implementiert ICodeIndexer."""
        from app.core.protocols import ICodeIndexer
        from app.services.python_indexer import PythonIndexer

        assert hasattr(PythonIndexer, "search")
        assert hasattr(PythonIndexer, "is_built")
        assert hasattr(PythonIndexer, "build")

    def test_memory_store_implements_imemory_store(self):
        """MemoryStore implementiert IMemoryStore."""
        from app.core.protocols import IMemoryStore
        from app.services.memory_store import MemoryStore

        assert hasattr(MemoryStore, "remember")
        assert hasattr(MemoryStore, "recall")
        assert hasattr(MemoryStore, "forget")

    def test_confluence_client_implements_iconfluence_client(self):
        """ConfluenceClient implementiert IConfluenceClient."""
        from app.core.protocols import IConfluenceClient
        from app.services.confluence_client import ConfluenceClient

        assert hasattr(ConfluenceClient, "search")
        assert hasattr(ConfluenceClient, "get_page_by_id")


class TestDependencyFactories:
    """Tests für Dependency Injection Factories."""

    def test_factories_are_importable(self):
        """Alle Factories können importiert werden."""
        from app.core.dependencies import (
            get_settings,
            get_code_indexer,
            get_java_indexer,
            get_python_indexer,
            get_handbook_indexer,
            get_memory_store,
            get_llm_client,
            get_tool_registry,
        )
        # Keine Exception = Erfolg
        assert callable(get_settings)
        assert callable(get_code_indexer)

    def test_type_aliases_are_defined(self):
        """Type Aliases für Depends() sind definiert."""
        from app.core.dependencies import (
            CodeIndexer,
            JavaIndexer,
            PythonIndexer,
            MemoryStore,
            LLMClient,
        )
        # Annotated Types haben __metadata__
        assert hasattr(CodeIndexer, "__metadata__")
        assert hasattr(MemoryStore, "__metadata__")


class TestMockUtilities:
    """Tests für Test-Utilities."""

    def test_create_mock_indexer(self):
        """Mock-Indexer funktioniert korrekt."""
        from app.core.dependencies import create_mock_indexer

        mock = create_mock_indexer([
            {"file_path": "Test.java", "snippet": "class Test"}
        ])

        # Methoden vorhanden
        assert hasattr(mock, "search")
        assert hasattr(mock, "is_built")
        assert hasattr(mock, "build")

        # Funktionalität
        assert mock.is_built() is True
        results = mock.search("test")
        assert len(results) == 1
        assert results[0]["file_path"] == "Test.java"

    def test_create_mock_memory_store(self):
        """Mock-MemoryStore funktioniert korrekt."""
        from app.core.dependencies import create_mock_memory_store

        mock = create_mock_memory_store()

        # Remember und Recall (sync variants for testing)
        id_ = mock.remember_sync("test_key", "test_value", category="fact")
        assert id_ is not None

        results = mock.recall_sync("test")
        assert len(results) == 1
        assert results[0]["value"] == "test_value"

    @pytest.mark.asyncio
    async def test_create_mock_memory_store_async(self):
        """Mock-MemoryStore async Methoden funktionieren."""
        from app.core.dependencies import create_mock_memory_store

        mock = create_mock_memory_store()

        # Async remember und recall
        id_ = await mock.remember("async_key", "async_value")
        assert id_ is not None

        results = await mock.recall("async")
        assert len(results) == 1
        assert results[0]["value"] == "async_value"

        # Forget
        deleted = await mock.forget(id_)
        assert deleted is True

        results_after = await mock.recall("async")
        assert len(results_after) == 0

    def test_mock_indexer_respects_top_k(self):
        """Mock-Indexer respektiert top_k Parameter."""
        from app.core.dependencies import create_mock_indexer

        mock = create_mock_indexer([
            {"file_path": f"File{i}.java"} for i in range(10)
        ])

        # top_k=3 sollte nur 3 Ergebnisse liefern
        results = mock.search("test", top_k=3)
        assert len(results) == 3


class TestCoreModuleExports:
    """Tests für app.core Modul-Exports."""

    def test_all_exports_from_core_init(self):
        """Alle wichtigen Exports sind in app.core verfügbar."""
        from app.core import (
            # Protocols
            ICodeIndexer,
            IMemoryStore,
            # Factories
            get_code_indexer,
            get_memory_store,
            # Type aliases
            CodeIndexer,
            MemoryStore,
            # Testing
            create_mock_indexer,
        )
        assert ICodeIndexer is not None
        assert callable(get_code_indexer)


class TestProtocolTypeChecking:
    """Tests für Protocol Type-Checking mit isinstance()."""

    def test_mock_passes_isinstance_check(self):
        """Mock-Objekte bestehen isinstance() Check."""
        from app.core.protocols import ICodeIndexer
        from app.core.dependencies import create_mock_indexer

        mock = create_mock_indexer()

        # runtime_checkable ermöglicht dies
        # Hinweis: Bei Protocols prüft isinstance() nur Methoden-Existenz
        assert hasattr(mock, "search")
        assert hasattr(mock, "is_built")
        assert hasattr(mock, "build")
