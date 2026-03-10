"""
MCP Client - JSON-RPC 2.0 Client für stdio-basierte MCP-Server.

Kommuniziert mit lokalen MCP-Servern über stdin/stdout mittels JSON-RPC 2.0.
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MCPTool:
    """Definition eines MCP-Tools."""
    name: str
    description: str
    input_schema: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPResponse:
    """Antwort vom MCP-Server."""
    success: bool
    result: Any = None
    error: Optional[str] = None
    error_code: Optional[int] = None


class MCPClient:
    """
    JSON-RPC 2.0 Client für stdio-basierte MCP-Server.

    Startet einen lokalen Prozess und kommuniziert via stdin/stdout.
    """

    def __init__(
        self,
        server_id: str,
        command: str,
        args: List[str] = None,
        env: Dict[str, str] = None,
        working_dir: str = None,
        timeout_seconds: int = 30
    ):
        self.server_id = server_id
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.working_dir = working_dir
        self.timeout = timeout_seconds

        self._process: Optional[asyncio.subprocess.Process] = None
        self._request_id = 0
        self._tools: List[MCPTool] = []
        self._initialized = False
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        """Prüft ob der Server-Prozess läuft."""
        return self._process is not None and self._process.returncode is None

    async def start(self) -> bool:
        """Startet den MCP-Server-Prozess."""
        if self.is_running:
            return True

        try:
            import os
            env = os.environ.copy()
            env.update(self.env)

            self._process = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=self.working_dir or None
            )

            # Initialize MCP connection
            await self._initialize()
            return True

        except FileNotFoundError:
            logger.error(f"[MCP:{self.server_id}] Command not found: {self.command}")
            return False
        except Exception as e:
            logger.error(f"[MCP:{self.server_id}] Failed to start: {e}")
            return False

    async def stop(self) -> None:
        """Stoppt den MCP-Server-Prozess."""
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
            except Exception as e:
                logger.warning(f"[MCP:{self.server_id}] Error stopping: {e}")
            finally:
                self._process = None
                self._initialized = False

    async def _initialize(self) -> None:
        """Initialisiert die MCP-Verbindung (Handshake)."""
        # MCP Initialize Request
        response = await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {}
            },
            "clientInfo": {
                "name": "AI-Assist",
                "version": "1.0.0"
            }
        })

        if response.success:
            # Send initialized notification
            await self._send_notification("notifications/initialized", {})

            # Get available tools
            tools_response = await self._send_request("tools/list", {})
            if tools_response.success and tools_response.result:
                tools_data = tools_response.result.get("tools", [])
                self._tools = [
                    MCPTool(
                        name=t.get("name", ""),
                        description=t.get("description", ""),
                        input_schema=t.get("inputSchema", {})
                    )
                    for t in tools_data
                ]

            self._initialized = True
            logger.info(f"[MCP:{self.server_id}] Initialized with {len(self._tools)} tools")
        else:
            logger.error(f"[MCP:{self.server_id}] Initialize failed: {response.error}")

    async def _send_request(self, method: str, params: Dict[str, Any]) -> MCPResponse:
        """Sendet einen JSON-RPC Request und wartet auf Antwort."""
        async with self._lock:
            if not self._process or not self._process.stdin or not self._process.stdout:
                return MCPResponse(success=False, error="Server not running")

            self._request_id += 1
            request = {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": method,
                "params": params
            }

            try:
                # Send request
                request_line = json.dumps(request) + "\n"
                self._process.stdin.write(request_line.encode())
                await self._process.stdin.drain()

                # Read response with timeout
                response_line = await asyncio.wait_for(
                    self._process.stdout.readline(),
                    timeout=self.timeout
                )

                if not response_line:
                    return MCPResponse(success=False, error="Empty response")

                response = json.loads(response_line.decode())

                if "error" in response:
                    return MCPResponse(
                        success=False,
                        error=response["error"].get("message", "Unknown error"),
                        error_code=response["error"].get("code")
                    )

                return MCPResponse(success=True, result=response.get("result"))

            except asyncio.TimeoutError:
                return MCPResponse(success=False, error="Request timeout")
            except json.JSONDecodeError as e:
                return MCPResponse(success=False, error=f"Invalid JSON: {e}")
            except Exception as e:
                return MCPResponse(success=False, error=str(e))

    async def _send_notification(self, method: str, params: Dict[str, Any]) -> None:
        """Sendet eine JSON-RPC Notification (keine Antwort erwartet)."""
        if not self._process or not self._process.stdin:
            return

        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params
        }

        try:
            notification_line = json.dumps(notification) + "\n"
            self._process.stdin.write(notification_line.encode())
            await self._process.stdin.drain()
        except Exception as e:
            logger.warning(f"[MCP:{self.server_id}] Failed to send notification: {e}")

    def get_tools(self) -> List[MCPTool]:
        """Gibt die verfügbaren Tools zurück."""
        return self._tools

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> MCPResponse:
        """Ruft ein MCP-Tool auf."""
        if not self._initialized:
            return MCPResponse(success=False, error="Client not initialized")

        response = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments
        })

        return response

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()
