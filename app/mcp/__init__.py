"""
MCP (Model Context Protocol) - Lokale Implementation.

Dieses Modul implementiert MCP-ähnliche Funktionalität lokal,
ohne Abhängigkeit von externen MCP-Servern.

Features:
- MCPClient: JSON-RPC 2.0 Client für stdio-basierte Server
- MCPManager: Verwaltung mehrerer MCP-Server
- SequentialThinking: Lokale Implementation für strukturiertes Denken
- ToolBridge: Integration von MCP-Tools in das Agent-System
"""

from app.mcp.client import MCPClient
from app.mcp.manager import MCPManager, get_mcp_manager
from app.mcp.sequential_thinking import SequentialThinking, ThinkingStep
from app.mcp.tool_bridge import MCPToolBridge

__all__ = [
    "MCPClient",
    "MCPManager",
    "get_mcp_manager",
    "SequentialThinking",
    "ThinkingStep",
    "MCPToolBridge",
]
