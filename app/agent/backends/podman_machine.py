"""
Podman Machine Manager.

Manages Podman machine lifecycle on Windows, including initialization
with custom image paths for internal registries.

The Podman machine is a lightweight VM that runs Linux containers
on Windows without WSL.
"""

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.agent.backends.base import BackendType, ContainerBackend, ContainerResult

logger = logging.getLogger(__name__)


@dataclass
class MachineStatus:
    """Status information for a Podman machine."""
    name: str
    running: bool
    cpus: int = 0
    memory_mb: int = 0
    disk_size_gb: int = 0
    last_up: str = ""
    error: Optional[str] = None


class PodmanMachineManager:
    """
    Manages Podman machine lifecycle on Windows.

    Supports:
    - Custom image path for internal image provisioning
    - Resource configuration (CPUs, memory, disk)
    - Auto-start on first use
    - Status monitoring

    Usage:
        from app.core.config import settings
        cfg = settings.docker_sandbox.podman_machine

        manager = PodmanMachineManager(
            name=cfg.name,
            podman_path=settings.docker_sandbox.podman_path or "podman"
        )

        # Initialize if needed
        if not await manager.exists():
            await manager.init(
                cpus=cfg.cpus,
                memory_mb=cfg.memory_mb,
                disk_size_gb=cfg.disk_size_gb,
                image_path=cfg.image_path,
            )

        # Start if not running
        await manager.ensure_running()
    """

    def __init__(
        self,
        name: str = "podman-machine-default",
        podman_path: str = "podman",
    ):
        """
        Initialize machine manager.

        Args:
            name: Name of the Podman machine
            podman_path: Path to podman executable
        """
        self.name = name
        self.podman_path = podman_path or shutil.which("podman") or "podman"

    async def exists(self) -> bool:
        """Check if machine exists."""
        status = await self.status()
        return status.error is None or "does not exist" not in (status.error or "")

    async def is_running(self) -> bool:
        """Check if machine is running."""
        status = await self.status()
        return status.running

    async def status(self) -> MachineStatus:
        """
        Get machine status.

        Returns:
            MachineStatus with current state
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                self.podman_path, "machine", "inspect", self.name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

            if proc.returncode != 0:
                error = stderr.decode("utf-8", errors="replace").strip()
                return MachineStatus(
                    name=self.name,
                    running=False,
                    error=error,
                )

            # Parse JSON output
            data = json.loads(stdout.decode())
            if isinstance(data, list) and len(data) > 0:
                data = data[0]

            return MachineStatus(
                name=self.name,
                running=data.get("State") == "running",
                cpus=data.get("Resources", {}).get("CPUs", 0),
                memory_mb=data.get("Resources", {}).get("Memory", 0),
                disk_size_gb=data.get("Resources", {}).get("DiskSize", 0),
                last_up=data.get("LastUp", ""),
            )

        except asyncio.TimeoutError:
            return MachineStatus(name=self.name, running=False, error="Timeout")
        except json.JSONDecodeError as e:
            return MachineStatus(name=self.name, running=False, error=f"JSON parse error: {e}")
        except Exception as e:
            return MachineStatus(name=self.name, running=False, error=str(e))

    async def init(
        self,
        cpus: int = 2,
        memory_mb: int = 2048,
        disk_size_gb: int = 20,
        image_url: Optional[str] = None,
        image_path: Optional[str] = None,
    ) -> ContainerResult:
        """
        Initialize a new Podman machine.

        Args:
            cpus: Number of CPUs
            memory_mb: Memory in MB
            disk_size_gb: Disk size in GB
            image_url: Remote image URL for --image (e.g., docker://registry/image:tag)
            image_path: Local image path for --image-path (alternative to image_url)

        Returns:
            ContainerResult with init output

        Example:
            await manager.init(
                cpus=2,
                memory_mb=2048,
                image_url="docker://registry.example.com/podman/machine-os:5.0"
            )
        """
        args = [self.podman_path, "machine", "init"]

        # Machine name
        args.append(self.name)

        # Resources
        args.extend(["--cpus", str(cpus)])
        args.extend(["--memory", str(memory_mb)])
        args.extend(["--disk-size", str(disk_size_gb)])

        # Custom image URL (for internal registry) - takes precedence
        if image_url:
            args.extend(["--image", image_url])
            logger.info("Using custom image URL: %s", image_url)
        # Fallback to local image path
        elif image_path:
            path = Path(image_path)
            if not path.exists():
                return ContainerResult(
                    success=False,
                    error=f"Image path does not exist: {image_path}",
                    backend=BackendType.PODMAN_MACHINE,
                )
            args.extend(["--image-path", str(path)])
            logger.info("Using custom image path: %s", image_path)

        logger.info("Initializing Podman machine: %s", " ".join(args))

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=600)  # 10 min timeout
            output = stdout.decode("utf-8", errors="replace")

            return ContainerResult(
                success=proc.returncode == 0,
                stdout=output.strip(),
                exit_code=proc.returncode or 0,
                backend=BackendType.PODMAN_MACHINE,
                command=" ".join(args),
                error=None if proc.returncode == 0 else f"Init failed (exit {proc.returncode})",
            )

        except asyncio.TimeoutError:
            return ContainerResult(
                success=False,
                error="Machine init timeout (10 minutes)",
                backend=BackendType.PODMAN_MACHINE,
            )
        except Exception as e:
            return ContainerResult(
                success=False,
                error=str(e),
                backend=BackendType.PODMAN_MACHINE,
            )

    async def start(self) -> ContainerResult:
        """Start the Podman machine."""
        logger.info("Starting Podman machine: %s", self.name)

        try:
            proc = await asyncio.create_subprocess_exec(
                self.podman_path, "machine", "start", self.name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
            output = stdout.decode("utf-8", errors="replace")

            return ContainerResult(
                success=proc.returncode == 0,
                stdout=output.strip(),
                exit_code=proc.returncode or 0,
                backend=BackendType.PODMAN_MACHINE,
                error=None if proc.returncode == 0 else f"Start failed (exit {proc.returncode})",
            )

        except asyncio.TimeoutError:
            return ContainerResult(
                success=False,
                error="Machine start timeout (2 minutes)",
                backend=BackendType.PODMAN_MACHINE,
            )
        except Exception as e:
            return ContainerResult(
                success=False,
                error=str(e),
                backend=BackendType.PODMAN_MACHINE,
            )

    async def stop(self) -> ContainerResult:
        """Stop the Podman machine."""
        logger.info("Stopping Podman machine: %s", self.name)

        try:
            proc = await asyncio.create_subprocess_exec(
                self.podman_path, "machine", "stop", self.name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            output = stdout.decode("utf-8", errors="replace")

            return ContainerResult(
                success=proc.returncode == 0,
                stdout=output.strip(),
                exit_code=proc.returncode or 0,
                backend=BackendType.PODMAN_MACHINE,
            )

        except asyncio.TimeoutError:
            return ContainerResult(
                success=False,
                error="Machine stop timeout (1 minute)",
                backend=BackendType.PODMAN_MACHINE,
            )
        except Exception as e:
            return ContainerResult(
                success=False,
                error=str(e),
                backend=BackendType.PODMAN_MACHINE,
            )

    async def remove(self, force: bool = False) -> ContainerResult:
        """
        Remove the Podman machine.

        Args:
            force: Force removal even if running
        """
        logger.info("Removing Podman machine: %s", self.name)

        args = [self.podman_path, "machine", "rm"]
        if force:
            args.append("-f")
        args.append(self.name)

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            output = stdout.decode("utf-8", errors="replace")

            return ContainerResult(
                success=proc.returncode == 0,
                stdout=output.strip(),
                exit_code=proc.returncode or 0,
                backend=BackendType.PODMAN_MACHINE,
            )

        except Exception as e:
            return ContainerResult(
                success=False,
                error=str(e),
                backend=BackendType.PODMAN_MACHINE,
            )

    async def ensure_running(self) -> ContainerResult:
        """
        Ensure machine is initialized and running.

        Returns:
            ContainerResult indicating success/failure
        """
        status = await self.status()

        # Machine doesn't exist - need to initialize first
        if status.error and "does not exist" in status.error:
            return ContainerResult(
                success=False,
                error=f"Machine '{self.name}' does not exist. Initialize first with init().",
                backend=BackendType.PODMAN_MACHINE,
            )

        # Already running
        if status.running:
            return ContainerResult(
                success=True,
                stdout=f"Machine '{self.name}' is already running",
                backend=BackendType.PODMAN_MACHINE,
            )

        # Start the machine
        return await self.start()


class PodmanMachineBackend(ContainerBackend):
    """
    Container backend using Podman with managed machine.

    This backend uses the native Podman command which connects
    to the Podman machine VM.
    """

    backend_type = BackendType.PODMAN_MACHINE

    def __init__(
        self,
        podman_path: str = "podman",
        machine_name: str = "podman-machine-default",
        auto_start: bool = True,
    ):
        self.podman_path = podman_path or shutil.which("podman") or "podman"
        self.machine_name = machine_name
        self.auto_start = auto_start
        self._manager = PodmanMachineManager(machine_name, self.podman_path)
        self._validated = False
        self._version: Optional[str] = None

    def get_command_prefix(self) -> List[str]:
        """Get podman command prefix."""
        return [self.podman_path]

    async def is_available(self) -> bool:
        """Check if Podman and machine are available."""
        if self._validated:
            return True

        # Check podman exists
        if not shutil.which(self.podman_path) and self.podman_path == "podman":
            logger.debug("Podman not found in PATH")
            return False

        # Check version
        try:
            proc = await asyncio.create_subprocess_exec(
                self.podman_path, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                return False
            self._version = stdout.decode().strip()
        except Exception as e:
            logger.debug("Podman version check failed: %s", e)
            return False

        # Check machine status
        if self.auto_start:
            status = await self._manager.status()
            if not status.running and status.error is None:
                logger.info("Auto-starting Podman machine...")
                start_result = await self._manager.start()
                if not start_result.success:
                    logger.warning("Failed to auto-start machine: %s", start_result.error)
                    return False

        self._validated = True
        logger.info("Podman machine backend available: %s", self._version)
        return True

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
        """Run container via Podman machine."""
        args = self.get_command_prefix() + ["run", "--rm"]

        if memory_limit:
            args.extend(["-m", memory_limit])
        if cpu_limit:
            args.extend(["--cpus", str(cpu_limit)])

        args.append("--no-new-privileges")

        if not network_enabled:
            args.append("--network=none")

        if working_dir:
            args.extend(["-w", working_dir])

        if volumes:
            for host_path, container_path in volumes.items():
                args.extend(["-v", f"{host_path}:{container_path}"])

        if environment:
            for key, value in environment.items():
                args.extend(["-e", f"{key}={value}"])

        args.append(image)
        args.extend(command)

        return await self._run_command(args, timeout=timeout)

    async def exec_in_container(
        self,
        container_id: str,
        command: List[str],
        timeout: int = 60,
    ) -> ContainerResult:
        """Execute in running container."""
        args = self.get_command_prefix() + ["exec", container_id] + command
        return await self._run_command(args, timeout=timeout)

    async def list_containers(self, all_containers: bool = False) -> ContainerResult:
        """List containers."""
        args = self.get_command_prefix() + ["ps"]
        if all_containers:
            args.append("-a")
        args.extend(["--format", "table {{.ID}}\t{{.Image}}\t{{.Status}}\t{{.Names}}"])
        return await self._run_command(args, timeout=30)

    async def list_images(self, filter_name: Optional[str] = None) -> ContainerResult:
        """List images."""
        args = self.get_command_prefix() + ["images"]
        if filter_name:
            args.extend(["--filter", f"reference=*{filter_name}*"])
        args.extend(["--format", "table {{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}"])
        return await self._run_command(args, timeout=30)

    @property
    def manager(self) -> PodmanMachineManager:
        """Get the machine manager for advanced operations."""
        return self._manager
