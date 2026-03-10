"""
MCP Manager - Verwaltet mehrere MCP-Server und deren Lifecycle.
"""

import asyncio
import logging
from typing import Dict, List, Optional

from app.core.config import settings
from app.mcp.client import MCPClient, MCPResponse, MCPTool

logger = logging.getLogger(__name__)


class MCPManager:
    """
    Verwaltet mehrere MCP-Server und deren Lifecycle.

    Features:
    - Auto-Start konfigurierter Server
    - Tool-Discovery über alle Server
    - Unified Tool-Calling Interface
    """

    def __init__(self):
        self._clients: Dict[str, MCPClient] = {}
        self._initialized = False

    @property
    def is_enabled(self) -> bool:
        """Prüft ob MCP aktiviert ist."""
        return settings.mcp.enabled

    @property
    def clients(self) -> Dict[str, MCPClient]:
        """Gibt alle registrierten Clients zurück."""
        return self._clients

    async def initialize(self) -> None:
        """Initialisiert alle konfigurierten MCP-Server."""
        if not self.is_enabled:
            logger.debug("[MCP] MCP is disabled")
            return

        if self._initialized:
            return

        for server_config in settings.mcp.servers:
            if not server_config.auto_start:
                continue

            client = MCPClient(
                server_id=server_config.id,
                command=server_config.command,
                args=server_config.args,
                env=server_config.env,
                working_dir=server_config.working_dir,
                timeout_seconds=server_config.timeout_seconds
            )

            if await client.start():
                self._clients[server_config.id] = client
                logger.info(f"[MCP] Started server: {server_config.id}")
            else:
                logger.warning(f"[MCP] Failed to start server: {server_config.id}")

        self._initialized = True
        logger.info(f"[MCP] Initialized with {len(self._clients)} servers")

    async def shutdown(self) -> None:
        """Stoppt alle MCP-Server."""
        for server_id, client in self._clients.items():
            try:
                await client.stop()
                logger.info(f"[MCP] Stopped server: {server_id}")
            except Exception as e:
                logger.warning(f"[MCP] Error stopping {server_id}: {e}")

        self._clients.clear()
        self._initialized = False

    def get_client(self, server_id: str) -> Optional[MCPClient]:
        """Gibt einen spezifischen Client zurück."""
        return self._clients.get(server_id)

    def get_all_tools(self) -> Dict[str, List[MCPTool]]:
        """
        Gibt alle verfügbaren Tools gruppiert nach Server zurück.

        Returns:
            Dict[server_id, List[MCPTool]]
        """
        result = {}
        for server_id, client in self._clients.items():
            if client.is_running:
                result[server_id] = client.get_tools()
        return result

    def find_tool(self, tool_name: str) -> Optional[tuple[str, MCPTool]]:
        """
        Sucht ein Tool nach Namen über alle Server.

        Returns:
            Tuple[server_id, MCPTool] oder None
        """
        for server_id, client in self._clients.items():
            for tool in client.get_tools():
                if tool.name == tool_name:
                    return (server_id, tool)
        return None

    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict,
        server_id: Optional[str] = None
    ) -> MCPResponse:
        """
        Ruft ein MCP-Tool auf.

        Args:
            tool_name: Name des Tools
            arguments: Tool-Argumente
            server_id: Optional - Server-ID (sonst wird gesucht)

        Returns:
            MCPResponse
        """
        # Server-ID bestimmen
        if server_id:
            client = self._clients.get(server_id)
            if not client:
                return MCPResponse(success=False, error=f"Server not found: {server_id}")
        else:
            # Tool über alle Server suchen
            found = self.find_tool(tool_name)
            if not found:
                return MCPResponse(success=False, error=f"Tool not found: {tool_name}")
            server_id, _ = found
            client = self._clients[server_id]

        return await client.call_tool(tool_name, arguments)

    def get_tool_definitions(self) -> List[Dict]:
        """
        Gibt Tool-Definitionen im Agent-Tool-Format zurück.

        Für Integration mit dem bestehenden Tool-System.
        """
        definitions = []

        for server_id, client in self._clients.items():
            for tool in client.get_tools():
                definitions.append({
                    "type": "function",
                    "function": {
                        "name": f"mcp_{server_id}_{tool.name}",
                        "description": f"[MCP:{server_id}] {tool.description}",
                        "parameters": tool.input_schema
                    }
                })

        return definitions


# Singleton
_mcp_manager: Optional[MCPManager] = None


def get_mcp_manager() -> MCPManager:
    """Gibt die Singleton-Instanz des MCPManagers zurück."""
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = MCPManager()
    return _mcp_manager


async def initialize_mcp() -> None:
    """Initialisiert das MCP-System."""
    manager = get_mcp_manager()
    await manager.initialize()


async def shutdown_mcp() -> None:
    """Fährt das MCP-System herunter."""
    manager = get_mcp_manager()
    await manager.shutdown()
