"""
MCP Tool Bridge - Integration von MCP-Tools in das Agent-System.

Verbindet MCP-Tools mit dem bestehenden Tool-System des Agents.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Callable

from app.core.config import settings
from app.mcp.manager import get_mcp_manager, MCPResponse
from app.mcp.sequential_thinking import (
    get_sequential_thinking,
    SequentialThinking,
    ThinkingSession,
    ThinkingType
)

logger = logging.getLogger(__name__)


class MCPToolBridge:
    """
    Bridge zwischen MCP und Agent-Tool-System.

    Ermöglicht:
    - MCP-Tools als Agent-Tools zu registrieren
    - Sequential Thinking als Tool verfügbar zu machen
    - Unified Interface für alle MCP-Funktionen
    """

    def __init__(self, llm_callback: Optional[Callable] = None):
        """
        Args:
            llm_callback: Callback für LLM-Aufrufe (für Sequential Thinking)
        """
        self.mcp_manager = get_mcp_manager()
        self.sequential_thinking = get_sequential_thinking(llm_callback)
        self._tool_handlers: Dict[str, Callable] = {}

        # Registriere built-in Tools
        self._register_builtin_tools()

    def _register_builtin_tools(self) -> None:
        """Registriert die eingebauten MCP-Tools."""
        # Sequential Thinking Tool
        self._tool_handlers["sequential_thinking"] = self._handle_sequential_thinking
        self._tool_handlers["seq_think"] = self._handle_sequential_thinking  # Alias

        # Session Management
        self._tool_handlers["thinking_session_get"] = self._handle_get_session
        self._tool_handlers["thinking_session_add_step"] = self._handle_add_step

    def get_tool_definitions(self) -> List[Dict]:
        """
        Gibt alle MCP-Tool-Definitionen im Agent-Format zurück.

        Returns:
            Liste von Tool-Definitionen für das Agent-System
        """
        tools = []

        # Sequential Thinking Tool
        if settings.mcp.sequential_thinking_enabled:
            tools.append({
                "type": "function",
                "function": {
                    "name": "sequential_thinking",
                    "description": (
                        "Strukturiertes, schrittweises Denken für komplexe Aufgaben. "
                        "Verwende dieses Tool für: Fehleranalysen, Planungsaufgaben, "
                        "Multi-Step-Problemlösungen. Das Tool führt eine strukturierte "
                        "Analyse durch und liefert Schritt-für-Schritt Erkenntnisse."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Das zu analysierende Problem oder die Aufgabe"
                            },
                            "context": {
                                "type": "string",
                                "description": "Optional: Zusätzlicher Kontext (z.B. Fehlermeldungen, Code)"
                            },
                            "max_steps": {
                                "type": "integer",
                                "description": "Optional: Maximale Anzahl Denkschritte (default: 10)"
                            }
                        },
                        "required": ["query"]
                    }
                }
            })

        # MCP-Server Tools hinzufügen
        if settings.mcp.enabled:
            mcp_tools = self.mcp_manager.get_tool_definitions()
            tools.extend(mcp_tools)

        return tools

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ruft ein MCP-Tool auf.

        Args:
            tool_name: Name des Tools
            arguments: Tool-Argumente

        Returns:
            Tool-Ergebnis als Dict
        """
        # Built-in Tool?
        if tool_name in self._tool_handlers:
            handler = self._tool_handlers[tool_name]
            return await handler(arguments)

        # MCP-Server Tool?
        if tool_name.startswith("mcp_"):
            # Format: mcp_<server_id>_<tool_name>
            parts = tool_name.split("_", 2)
            if len(parts) >= 3:
                server_id = parts[1]
                actual_tool_name = parts[2]
                response = await self.mcp_manager.call_tool(
                    actual_tool_name,
                    arguments,
                    server_id=server_id
                )
                return self._format_mcp_response(response)

        return {
            "success": False,
            "error": f"Unknown tool: {tool_name}"
        }

    async def _handle_sequential_thinking(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Handler für Sequential Thinking Tool."""
        query = arguments.get("query", "")
        context = arguments.get("context")
        max_steps = arguments.get("max_steps")

        if not query:
            return {
                "success": False,
                "error": "Query is required"
            }

        try:
            session = await self.sequential_thinking.think(
                query=query,
                context=context,
                max_steps=max_steps
            )

            return {
                "success": True,
                "session_id": session.session_id,
                "steps_count": len(session.steps),
                "conclusion": session.final_conclusion,
                "formatted_output": self.sequential_thinking.format_session_for_context(session),
                "steps": [s.to_dict() for s in session.steps]
            }

        except Exception as e:
            logger.error(f"[MCPBridge] Sequential thinking error: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def _handle_get_session(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Handler für Session-Abruf."""
        session_id = arguments.get("session_id", "")

        session = self.sequential_thinking.get_session(session_id)
        if not session:
            return {
                "success": False,
                "error": f"Session not found: {session_id}"
            }

        return {
            "success": True,
            "session": session.to_dict()
        }

    async def _handle_add_step(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Handler für manuelles Hinzufügen eines Schritts."""
        session_id = arguments.get("session_id", "")
        step_type = arguments.get("type", "analysis")
        title = arguments.get("title", "")
        content = arguments.get("content", "")

        try:
            type_enum = ThinkingType(step_type.lower())
        except ValueError:
            type_enum = ThinkingType.ANALYSIS

        try:
            step = self.sequential_thinking.add_step(
                session_id=session_id,
                step_type=type_enum,
                title=title,
                content=content
            )

            return {
                "success": True,
                "step": step.to_dict()
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    def _format_mcp_response(self, response: MCPResponse) -> Dict[str, Any]:
        """Formatiert eine MCPResponse für das Agent-System."""
        if response.success:
            return {
                "success": True,
                "result": response.result
            }
        else:
            return {
                "success": False,
                "error": response.error,
                "error_code": response.error_code
            }

    def should_use_sequential_thinking(
        self,
        query: str,
        is_error: bool = False
    ) -> bool:
        """
        Prüft ob Sequential Thinking für diese Anfrage verwendet werden sollte.

        Args:
            query: Die Benutzeranfrage
            is_error: True wenn es sich um eine Fehleranalyse handelt
        """
        return self.sequential_thinking.should_auto_activate(query, is_error)

    async def analyze_with_thinking(
        self,
        query: str,
        context: Optional[str] = None
    ) -> str:
        """
        Führt eine Analyse mit Sequential Thinking durch und gibt
        formatierten Output zurück.

        Convenience-Methode für einfache Integration.
        """
        session = await self.sequential_thinking.think(query, context)
        return self.sequential_thinking.format_session_for_context(session)


# Singleton
_tool_bridge: Optional[MCPToolBridge] = None


def get_tool_bridge(llm_callback: Optional[Callable] = None) -> MCPToolBridge:
    """Gibt die Singleton-Instanz der Tool Bridge zurück."""
    global _tool_bridge
    if _tool_bridge is None:
        _tool_bridge = MCPToolBridge(llm_callback)
    elif llm_callback and _tool_bridge.sequential_thinking.llm_callback is None:
        _tool_bridge.sequential_thinking.llm_callback = llm_callback
    return _tool_bridge
