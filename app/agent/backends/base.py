"""
Container Backend Base Classes.

Defines the abstract interface for container runtime backends.
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BackendType(Enum):
    """Supported container backend types."""
    DOCKER = "docker"
    PODMAN_NATIVE = "podman"
    PODMAN_MACHINE = "podman-machine"
    WSL_PODMAN = "wsl-podman"


@dataclass
class ContainerResult:
    """Standardized result from container operations."""
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    error: Optional[str] = None
    duration_seconds: float = 0.0
    backend: Optional[BackendType] = None
    command: Optional[str] = None  # For debugging

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for tool results."""
        return {
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
            "backend": self.backend.value if self.backend else None,
        }


class ContainerBackend(ABC):
    """
    Abstract base class for container runtime backends.

    Implementations:
    - DockerBackend: Native Docker
    - PodmanNativeBackend: Native Podman
    - WSLPodmanBackend: Podman inside WSL2
    - PodmanMachineBackend: Podman with managed VM
    """

    backend_type: BackendType

    @abstractmethod
    async def is_available(self) -> bool:
        """
        Check if this backend is available and ready.

        Returns:
            True if backend can execute containers
        """
        pass

    @abstractmethod
    async def run(
        self,
        image: str,
        command: List[str],
        timeout: int = 120,
        memory_limit: Optional[str] = None,
        cpu_limit: Optional[float] = None,
        network_enabled: bool = True,
        volumes: Optional[Dict[str, str]] = None,
        working_dir: Optional[str] = None,
        environment: Optional[Dict[str, str]] = None,
    ) -> ContainerResult:
        """
        Run a container with the given image and command.

        Args:
            image: Container image (e.g., "python:3.11-slim")
            command: Command to run (e.g., ["python", "-c", "print('hello')"])
            timeout: Timeout in seconds
            memory_limit: Memory limit (e.g., "512m")
            cpu_limit: CPU limit (e.g., 1.0)
            network_enabled: Allow network access
            volumes: Volume mounts {host_path: container_path}
            working_dir: Working directory in container
            environment: Environment variables

        Returns:
            ContainerResult with stdout, stderr, exit_code
        """
        pass

    @abstractmethod
    async def exec_in_container(
        self,
        container_id: str,
        command: List[str],
        timeout: int = 60,
    ) -> ContainerResult:
        """
        Execute command in a running container.

        Args:
            container_id: ID of running container
            command: Command to execute
            timeout: Timeout in seconds

        Returns:
            ContainerResult with execution output
        """
        pass

    @abstractmethod
    async def list_containers(self, all_containers: bool = False) -> ContainerResult:
        """
        List containers.

        Args:
            all_containers: Include stopped containers

        Returns:
            ContainerResult with container list in stdout
        """
        pass

    @abstractmethod
    async def list_images(self, filter_name: Optional[str] = None) -> ContainerResult:
        """
        List available images.

        Args:
            filter_name: Filter by image name

        Returns:
            ContainerResult with image list in stdout
        """
        pass

    @abstractmethod
    def get_command_prefix(self) -> List[str]:
        """
        Get the command prefix for this backend.

        Returns:
            Command prefix list (e.g., ["podman"] or ["wsl", "-d", "Ubuntu", "podman"])
        """
        pass

    async def _run_command(
        self,
        args: List[str],
        timeout: int = 120,
        cwd: Optional[str] = None,
    ) -> ContainerResult:
        """
        Run a command and return the result.

        Helper method for subclasses.
        """
        start_time = time.time()
        command_str = " ".join(args)

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout
            )

            duration = time.time() - start_time
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            return ContainerResult(
                success=proc.returncode == 0,
                stdout=stdout.strip(),
                stderr=stderr.strip(),
                exit_code=proc.returncode or 0,
                duration_seconds=round(duration, 2),
                backend=self.backend_type,
                command=command_str,
            )

        except asyncio.TimeoutError:
            return ContainerResult(
                success=False,
                error=f"Timeout nach {timeout} Sekunden",
                exit_code=-1,
                duration_seconds=timeout,
                backend=self.backend_type,
                command=command_str,
            )
        except FileNotFoundError as e:
            return ContainerResult(
                success=False,
                error=f"Befehl nicht gefunden: {args[0]}",
                exit_code=-1,
                backend=self.backend_type,
                command=command_str,
            )
        except Exception as e:
            return ContainerResult(
                success=False,
                error=str(e),
                exit_code=-1,
                duration_seconds=round(time.time() - start_time, 2),
                backend=self.backend_type,
                command=command_str,
            )

    def get_version(self) -> Optional[str]:
        """Get backend version (cached from is_available check)."""
        return getattr(self, "_version", None)
