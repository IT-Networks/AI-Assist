"""
MCP (Model Context Protocol) - Lokale Implementation.

Dieses Modul implementiert MCP-ähnliche Funktionalität lokal,
ohne Abhängigkeit von externen MCP-Servern.

Features:
- MCPClient: JSON-RPC 2.0 Client für stdio-basierte Server
- MCPManager: Verwaltung mehrerer MCP-Server
- SequentialThinking: Lokale Implementation für strukturiertes Denken
- ToolBridge: Integration von MCP-Tools in das Agent-System

MIGRATION (2026-03-23):
Alle Command-Capabilities (brainstorm, design, implement, analyze, research)
wurden zu Skills migriert. Das System folgt nun dem SuperClaude-Ansatz:
- Skills werden als YAML-Dateien geladen (skills/*.yaml)
- Behavioral Instructions statt Python-Backend
- Command-Trigger für automatische Aktivierung

Für neue Skills siehe: skills/enterprise-*.yaml
Für SuperClaude Referenz: ~/.claude/commands/sc/*.md
"""

from app.mcp.client import MCPClient
from app.mcp.manager import MCPManager, get_mcp_manager
from app.mcp.sequential_thinking import SequentialThinking, ThinkingStep
from app.mcp.thinking_engine import ThinkingEngine, ThinkingMode, ThinkingResult, get_thinking_engine
from app.mcp.tool_bridge import MCPToolBridge

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
]
