"""
MCP (Model Context Protocol) - Lokale Implementation.

Dieses Modul implementiert MCP-ähnliche Funktionalität lokal,
ohne Abhängigkeit von externen MCP-Servern.

Features:
- MCPClient: JSON-RPC 2.0 Client für stdio-basierte Server
- MCPManager: Verwaltung mehrerer MCP-Server
- SequentialThinking: Lokale Implementation für strukturiertes Denken
- ToolBridge: Integration von MCP-Tools in das Agent-System
- Capabilities: Strukturierte Capabilities (Brainstorm, Design, Implement, Analyze)
- CapabilityRegistry: Zentrale Verwaltung aller Capabilities
"""

from app.mcp.client import MCPClient
from app.mcp.manager import MCPManager, get_mcp_manager
from app.mcp.sequential_thinking import SequentialThinking, ThinkingStep
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
    BrainstormCapability,
    DesignCapability,
    ImplementCapability,
    AnalyzeCapability
)

__all__ = [
    # Client & Manager
    "MCPClient",
    "MCPManager",
    "get_mcp_manager",
    # Sequential Thinking
    "SequentialThinking",
    "ThinkingStep",
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
    # Concrete Capabilities
    "BrainstormCapability",
    "DesignCapability",
    "ImplementCapability",
    "AnalyzeCapability",
]
