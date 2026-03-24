"""
Services Module - Alle Backend-Services für AI-Assist.

Dieses Modul bietet zwei Zugriffsmöglichkeiten:

1. Direkt-Import (bestehend, backwards-kompatibel):
   from app.services.java_indexer import get_java_indexer

2. Gruppierter Import (neu, empfohlen):
   from app.services.indexing import get_java_indexer
   from app.services.external import get_confluence_client

Service-Kategorien:
- indexing/   : Code- und Dokument-Indexierung (FTS5)
- external/   : Clients für externe APIs (Confluence, JIRA, etc.)
- llm/        : LLM-Integration und Caching
- code/       : Code-Analyse und -Lesen
- session/    : Session- und Kontext-Management
- analytics/  : Analyse, Tracking und Reporting
- testing/    : Test-Ausführung und -Management
- graph/      : Knowledge Graph und Code-Beziehungen
- document/   : Dokument-Verarbeitung (PDF, Logs)
- system/     : System-Utilities und Infrastruktur
"""

# ══════════════════════════════════════════════════════════════════════════════
# Subpackage Exports (neue Struktur)
# ══════════════════════════════════════════════════════════════════════════════

from app.services import indexing
from app.services import external
from app.services import llm
from app.services import code
from app.services import session
from app.services import analytics
from app.services import testing
from app.services import graph
from app.services import document
from app.services import system


# ══════════════════════════════════════════════════════════════════════════════
# Backwards-Kompatible Re-Exports
# Import nur die häufig verwendeten - vollständige Liste in Subpackages
# ══════════════════════════════════════════════════════════════════════════════

# Indexing
from app.services.java_indexer import JavaIndexer, get_java_indexer
from app.services.python_indexer import PythonIndexer, get_python_indexer
from app.services.pdf_indexer import PDFIndexer, get_pdf_indexer
from app.services.handbook_indexer import HandbookIndexer, get_handbook_indexer

# External
from app.services.confluence_client import (
    ConfluenceClient,
    get_confluence_client,
)
from app.services.jira_client import JiraClient, get_jira_client
from app.services.servicenow_client import ServiceNowClient, get_servicenow_client

# LLM
from app.services.llm_client import llm_client, LLMClient, LLMResponse
from app.services.llm_cache import LLMCacheManager, get_cache_manager, get_cache_stats

# Code
from app.services.java_reader import JavaReader
from app.services.python_reader import PythonReader
from app.services.code_search import CodeSearchEngine, get_code_search_engine
from app.services.pom_parser import PomParser
from app.services.file_manager import FileManager, get_file_manager

# Session
from app.services.context_manager import ContextManager, get_context_manager
from app.services.memory_store import (
    MemoryStore,
    MemoryEntry,
    get_memory_store,
)
from app.services.chat_store import load_chat, save_chat, list_chats
from app.services.task_tracker import TaskTracker, TaskArtifact, get_task_tracker
from app.services.transcript_logger import TranscriptLogger, get_transcript_logger

# Analytics
from app.services.analytics_logger import AnalyticsLogger, get_analytics_logger
from app.services.performance_tracker import PerformanceTracker
from app.services.token_tracker import TokenTracker, get_token_tracker

# Testing
from app.services.test_executor import TestExecutor, get_test_executor
from app.services.test_execution import TestExecutionService, get_test_execution_service

# Graph
from app.services.knowledge_graph import KnowledgeGraphStore, get_knowledge_graph_store
from app.services.graph_query_service import GraphQueryService, get_graph_query_service

# Document
from app.services.pdf_reader import PDFReader
from app.services.log_parser import WLPLogParser

# System
from app.services.update_service import UpdateService, get_update_service
from app.services.self_healing import SelfHealingEngine, get_self_healing_engine
from app.services.anonymizer import Anonymizer, get_anonymizer
from app.services.script_manager import ScriptManager, get_script_manager
from app.services.db_client import DB2Client, get_db_client

# Other frequently used
from app.services.auto_learner import AutoLearner, get_auto_learner
from app.services.skill_manager import SkillManager, get_skill_manager


__all__ = [
    # Subpackages
    "indexing",
    "external",
    "llm",
    "code",
    "session",
    "analytics",
    "testing",
    "graph",
    "document",
    "system",
    # Indexing
    "JavaIndexer",
    "get_java_indexer",
    "PythonIndexer",
    "get_python_indexer",
    "PDFIndexer",
    "get_pdf_indexer",
    "HandbookIndexer",
    "get_handbook_indexer",
    # External
    "ConfluenceClient",
    "get_confluence_client",
    "JiraClient",
    "get_jira_client",
    # LLM
    "llm_client",
    "LLMClient",
    "LLMResponse",
    # Code
    "JavaReader",
    "PythonReader",
    "CodeSearchEngine",
    "PomParser",
    "FileManager",
    # Session
    "ContextManager",
    "MemoryStore",
    "get_memory_store",
    "load_chat",
    "save_chat",
    "TaskTracker",
    "TranscriptLogger",
    # Analytics
    "AnalyticsLogger",
    "get_analytics_logger",
    "PerformanceTracker",
    "TokenTracker",
    # Testing
    "TestExecutor",
    "TestExecutionService",
    # Graph
    "KnowledgeGraphStore",
    "GraphQueryService",
    # Document
    "PDFReader",
    "WLPLogParser",
    # System
    "UpdateService",
    "SelfHealingEngine",
    "Anonymizer",
    "ScriptManager",
    "DB2Client",
    # Other
    "AutoLearner",
    "SkillManager",
]
