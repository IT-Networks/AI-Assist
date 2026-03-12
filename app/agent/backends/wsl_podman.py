"""
WSL Podman Backend.

Runs Podman commands inside a WSL2 distribution instead of using
a separate Podman machine VM. This saves resources and reuses
the existing WSL environment.

Requirements:
- Windows 10/11 with WSL2
- A WSL distribution (e.g., Ubuntu-24.04)
- Podman installed in the WSL distribution
"""

import asyncio
import logging
import re
from typing import Dict, List, Optional

from app.agent.backends.base import BackendType, ContainerBackend, ContainerResult

logger = logging.getLogger(__name__)


class WSLPodmanBackend(ContainerBackend):
    """
    Container backend that runs Podman inside a WSL2 distribution.

    Benefits:
    - Reuses existing WSL environment
    - No separate Podman VM needed
    - Shares resources with WSL
    - Can access Windows filesystem via /mnt/

    Usage:
        backend = WSLPodmanBackend("Ubuntu-24.04")
        if await backend.is_available():
            result = await backend.run("python:3.11", ["python", "-c", "print('hi')"])
    """

    backend_type = BackendType.WSL_PODMAN

    def __init__(
        self,
        distro_name: str,
        podman_path: str = "/usr/bin/podman",
    ):
        """
        Initialize WSL Podman backend.

        Args:
            distro_name: Name of WSL distribution (e.g., "Ubuntu-24.04")
            podman_path: Path to podman binary inside WSL
        """
        self.distro_name = distro_name
        self.podman_path = podman_path
        self._validated = False
        self._version: Optional[str] = None

    def get_command_prefix(self) -> List[str]:
        """
        Get command prefix for WSL execution.

        Returns:
            ['wsl', '-d', 'Ubuntu-24.04', '/usr/bin/podman']
        """
        return ["wsl", "-d", self.distro_name, self.podman_path]

    async def is_available(self) -> bool:
        """
        Check if WSL and Podman are available.

        Validates:
        1. WSL2 is installed
        2. Specified distro exists
        3. Podman is installed in distro
        """
        if self._validated:
            return True

        # Check WSL exists
        wsl_check = await self._run_command(["wsl", "--status"])
        if not wsl_check.success:
            logger.debug("WSL not available: %s", wsl_check.stderr)
            return False

        # Check distro exists
        distro_check = await self._run_command(
            ["wsl", "-d", self.distro_name, "echo", "ok"],
            timeout=10
        )
        if not distro_check.success:
            logger.debug("WSL distro '%s' not available: %s", self.distro_name, distro_check.stderr)
            return False

        # Check podman in distro
        podman_check = await self._run_command(
            ["wsl", "-d", self.distro_name, self.podman_path, "--version"],
            timeout=10
        )
        if not podman_check.success:
            logger.debug("Podman not found in WSL distro: %s", podman_check.stderr)
            return False

        # Extract version
        self._version = podman_check.stdout.strip()
        self._validated = True
        logger.info("WSL Podman backend available: %s in %s", self._version, self.distro_name)
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
        """
        Run a container via WSL Podman.

        Windows paths in volumes are automatically converted to WSL paths.
        """
        args = self.get_command_prefix() + ["run", "--rm"]

        # Resource limits
        if memory_limit:
            args.extend(["-m", memory_limit])
        if cpu_limit:
            args.extend(["--cpus", str(cpu_limit)])

        # Security
        args.append("--no-new-privileges")

        # Network
        if not network_enabled:
            args.append("--network=none")

        # Working directory
        if working_dir:
            args.extend(["-w", working_dir])

        # Volume mounts (convert Windows paths to WSL paths)
        if volumes:
            for host_path, container_path in volumes.items():
                wsl_path = self._windows_to_wsl_path(host_path)
                args.extend(["-v", f"{wsl_path}:{container_path}"])

        # Environment variables
        if environment:
            for key, value in environment.items():
                args.extend(["-e", f"{key}={value}"])

        # Image and command
        args.append(image)
        args.extend(command)

        return await self._run_command(args, timeout=timeout)

    async def exec_in_container(
        self,
        container_id: str,
        command: List[str],
        timeout: int = 60,
    ) -> ContainerResult:
        """Execute command in running container via WSL Podman."""
        args = self.get_command_prefix() + ["exec", container_id] + command
        return await self._run_command(args, timeout=timeout)

    async def list_containers(self, all_containers: bool = False) -> ContainerResult:
        """List containers via WSL Podman."""
        args = self.get_command_prefix() + ["ps"]
        if all_containers:
            args.append("-a")
        args.extend(["--format", "table {{.ID}}\t{{.Image}}\t{{.Status}}\t{{.Names}}"])
        return await self._run_command(args, timeout=30)

    async def list_images(self, filter_name: Optional[str] = None) -> ContainerResult:
        """List images via WSL Podman."""
        args = self.get_command_prefix() + ["images"]
        if filter_name:
            args.extend(["--filter", f"reference=*{filter_name}*"])
        args.extend(["--format", "table {{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}"])
        return await self._run_command(args, timeout=30)

    def _windows_to_wsl_path(self, windows_path: str) -> str:
        """
        Convert Windows path to WSL path.

        Examples:
            C:\\Users\\marku\\code -> /mnt/c/Users/marku/code
            D:/data/files -> /mnt/d/data/files
            /already/unix/path -> /already/unix/path

        Args:
            windows_path: Windows-style path

        Returns:
            WSL-compatible path
        """
        # Already a Unix path
        if windows_path.startswith("/"):
            return windows_path

        # Normalize path separators
        path = windows_path.replace("\\", "/")

        # Check for drive letter (C: or C:/)
        if len(path) >= 2 and path[1] == ":":
            drive = path[0].lower()
            rest = path[2:].lstrip("/")
            return f"/mnt/{drive}/{rest}"

        return path


async def detect_wsl_distros() -> List[str]:
    """
    Detect available WSL distributions.

    Returns:
        List of distribution names
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "wsl", "-l", "-q",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)

        if proc.returncode != 0:
            return []

        # Parse output (might have null bytes on Windows)
        output = stdout.decode("utf-16-le", errors="ignore").strip()
        distros = [d.strip() for d in output.split("\n") if d.strip()]
        return distros

    except Exception as e:
        logger.debug("Failed to detect WSL distros: %s", e)
        return []


async def check_podman_in_distro(distro_name: str) -> bool:
    """
    Check if Podman is installed in a WSL distribution.

    Args:
        distro_name: Name of WSL distribution

    Returns:
        True if podman is available
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "wsl", "-d", distro_name, "which", "podman",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=10)
        return proc.returncode == 0
    except Exception:
        return False


async def find_best_wsl_distro() -> Optional[str]:
    """
    Find the best WSL distribution with Podman installed.

    Prefers:
    1. Ubuntu 24.04
    2. Ubuntu (any version)
    3. Any distro with podman

    Returns:
        Distribution name or None
    """
    distros = await detect_wsl_distros()

    if not distros:
        return None

    # Prefer Ubuntu 24.04
    for distro in distros:
        if "ubuntu" in distro.lower() and "24" in distro:
            if await check_podman_in_distro(distro):
                logger.info("Found preferred distro with Podman: %s", distro)
                return distro

    # Fallback to any Ubuntu
    for distro in distros:
        if "ubuntu" in distro.lower():
            if await check_podman_in_distro(distro):
                logger.info("Found Ubuntu distro with Podman: %s", distro)
                return distro

    # Fallback to any distro with podman
    for distro in distros:
        if await check_podman_in_distro(distro):
            logger.info("Found distro with Podman: %s", distro)
            return distro

    return None
