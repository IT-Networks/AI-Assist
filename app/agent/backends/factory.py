"""
Container Backend Factory.

Creates the appropriate container backend based on configuration
and system availability.

Detection order (for backend: auto):
1. WSL with Podman (if enabled and available)
2. Native Podman with machine
3. Native Docker
"""

import logging
import shutil
from typing import Optional

from app.agent.backends.base import BackendType, ContainerBackend, ContainerResult
from app.agent.backends.wsl_podman import WSLPodmanBackend, find_best_wsl_distro
from app.agent.backends.podman_machine import PodmanMachineBackend, PodmanMachineManager

logger = logging.getLogger(__name__)


class DockerBackend(ContainerBackend):
    """
    Native Docker backend.

    Uses Docker directly (docker command).
    """

    backend_type = BackendType.DOCKER

    def __init__(self, docker_path: str = "docker"):
        self.docker_path = docker_path or shutil.which("docker") or "docker"
        self._validated = False
        self._version: Optional[str] = None

    def get_command_prefix(self):
        return [self.docker_path]

    async def is_available(self) -> bool:
        if self._validated:
            return True

        if not shutil.which(self.docker_path) and self.docker_path == "docker":
            return False

        result = await self._run_command([self.docker_path, "--version"], timeout=10)
        if result.success:
            self._version = result.stdout.strip()
            self._validated = True
            logger.info("Docker backend available: %s", self._version)
        return result.success

    async def run(self, image, command, timeout=120, memory_limit=None, cpu_limit=None,
                  network_enabled=True, volumes=None, working_dir=None, environment=None):
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

    async def exec_in_container(self, container_id, command, timeout=60):
        args = self.get_command_prefix() + ["exec", container_id] + command
        return await self._run_command(args, timeout=timeout)

    async def list_containers(self, all_containers=False):
        args = self.get_command_prefix() + ["ps"]
        if all_containers:
            args.append("-a")
        args.extend(["--format", "table {{.ID}}\t{{.Image}}\t{{.Status}}\t{{.Names}}"])
        return await self._run_command(args, timeout=30)

    async def list_images(self, filter_name=None):
        args = self.get_command_prefix() + ["images"]
        if filter_name:
            args.extend(["--filter", f"reference=*{filter_name}*"])
        args.extend(["--format", "table {{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}"])
        return await self._run_command(args, timeout=30)


class ContainerBackendFactory:
    """
    Factory for creating container backends.

    Usage:
        backend = await ContainerBackendFactory.create()
        if backend:
            result = await backend.run("python:3.11", ["python", "-c", "print('hi')"])
    """

    # Cache for created backend
    _cached_backend: Optional[ContainerBackend] = None

    @classmethod
    async def create(cls, force_refresh: bool = False) -> Optional[ContainerBackend]:
        """
        Create the appropriate container backend.

        Args:
            force_refresh: Ignore cached backend and create new

        Returns:
            ContainerBackend instance or None if none available
        """
        if cls._cached_backend and not force_refresh:
            return cls._cached_backend

        from app.core.config import settings
        cfg = settings.docker_sandbox

        if not cfg.enabled:
            logger.debug("Container sandbox disabled")
            return None

        backend = None

        # Explicit backend selection
        if cfg.backend == "docker":
            backend = await cls._try_docker(cfg.docker_path)
        elif cfg.backend == "podman":
            backend = await cls._try_podman_machine(cfg)
        elif cfg.backend == "wsl-podman":
            backend = await cls._try_wsl_podman(cfg)
        elif cfg.backend == "auto":
            backend = await cls._auto_detect(cfg)

        cls._cached_backend = backend
        return backend

    @classmethod
    async def _auto_detect(cls, cfg) -> Optional[ContainerBackend]:
        """
        Auto-detect the best available backend.

        Priority:
        1. WSL Podman (if enabled and available)
        2. Podman with machine
        3. Docker
        """
        # 1. Try WSL Podman if enabled
        if cfg.wsl_integration.enabled:
            backend = await cls._try_wsl_podman(cfg)
            if backend:
                logger.info("Auto-detected: WSL Podman")
                return backend

        # 2. Try Podman with machine
        backend = await cls._try_podman_machine(cfg)
        if backend:
            logger.info("Auto-detected: Podman Machine")
            return backend

        # 3. Try Docker
        backend = await cls._try_docker(cfg.docker_path)
        if backend:
            logger.info("Auto-detected: Docker")
            return backend

        logger.warning("No container backend available")
        return None

    @classmethod
    async def _try_wsl_podman(cls, cfg) -> Optional[WSLPodmanBackend]:
        """Try to create WSL Podman backend."""
        wsl_cfg = cfg.wsl_integration

        if not wsl_cfg.enabled:
            return None

        # Get distro name
        distro = wsl_cfg.distro_name
        if wsl_cfg.auto_detect and not distro:
            distro = await find_best_wsl_distro()

        if not distro:
            logger.debug("No suitable WSL distro found")
            return None

        backend = WSLPodmanBackend(
            distro_name=distro,
            podman_path=wsl_cfg.podman_path_in_wsl,
        )

        if await backend.is_available():
            return backend

        return None

    @classmethod
    async def _try_podman_machine(cls, cfg) -> Optional[PodmanMachineBackend]:
        """Try to create Podman machine backend."""
        machine_cfg = cfg.podman_machine

        podman_path = cfg.podman_path or shutil.which("podman")
        if not podman_path:
            return None

        backend = PodmanMachineBackend(
            podman_path=podman_path,
            machine_name=machine_cfg.name,
            auto_start=machine_cfg.auto_start,
        )

        if await backend.is_available():
            return backend

        return None

    @classmethod
    async def _try_docker(cls, docker_path: str = "") -> Optional[DockerBackend]:
        """Try to create Docker backend."""
        backend = DockerBackend(docker_path=docker_path)

        if await backend.is_available():
            return backend

        return None

    @classmethod
    def clear_cache(cls):
        """Clear the cached backend."""
        cls._cached_backend = None

    @classmethod
    def get_cached(cls) -> Optional[ContainerBackend]:
        """Get cached backend without creating new."""
        return cls._cached_backend


async def get_container_backend() -> Optional[ContainerBackend]:
    """
    Convenience function to get container backend.

    Returns:
        ContainerBackend instance or None
    """
    return await ContainerBackendFactory.create()
