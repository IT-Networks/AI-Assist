"""
MCP (Model Context Protocol) - Lokale Implementation.

Dieses Modul implementiert MCP-ähnliche Funktionalität lokal,
ohne Abhängigkeit von externen MCP-Servern.

Features:
- MCPClient: JSON-RPC 2.0 Client für stdio-basierte Server
- MCPManager: Verwaltung mehrerer MCP-Server
- SequentialThinking: Lokale Implementation für strukturiertes Denken
- ToolBridge: Integration von MCP-Tools in das Agent-System
- Capabilities: Strukturierte Capabilities (Analyze, Research)
- CapabilityRegistry: Zentrale Verwaltung aller Capabilities

NOTE: brainstorm, design, implement wurden zu Skills migriert.
Siehe ~/.claude/commands/sc/brainstorm.md, design.md, implement.md
"""

from app.mcp.client import MCPClient
from app.mcp.manager import MCPManager, get_mcp_manager
from app.mcp.sequential_thinking import SequentialThinking, ThinkingStep
from app.mcp.thinking_engine import ThinkingEngine, ThinkingMode, ThinkingResult, get_thinking_engine
from app.mcp.tool_bridge import MCPToolBridge
from app.mcp.registry import (
    CapabilityRegistry,
    get_capability_registry,
    register_default_capabilities
)
from app.mcp.capabilities import (
    BaseCapability,
    CapabilitySession,
    CapabilityPhase,
    CapabilityStatus,
    AnalyzeCapability,
    ResearchCapability,
    get_research_capability
)

__all__ = [
    # Client & Manager
    "MCPClient",
    "MCPManager",
    "get_mcp_manager",
    # Sequential Thinking
    "SequentialThinking",
    "ThinkingStep",
    # Thinking Engine
    "ThinkingEngine",
    "ThinkingMode",
    "ThinkingResult",
    "get_thinking_engine",
    # Tool Bridge
    "MCPToolBridge",
    # Registry
    "CapabilityRegistry",
    "get_capability_registry",
    "register_default_capabilities",
    # Capabilities Base
    "BaseCapability",
    "CapabilitySession",
    "CapabilityPhase",
    "CapabilityStatus",
    # Concrete Capabilities (analyze, research - brainstorm/design/implement sind Skills)
    "AnalyzeCapability",
    "ResearchCapability",
    "get_research_capability",
]
