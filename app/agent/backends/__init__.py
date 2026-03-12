"""
Container Backend - WSL Podman.

Provides container execution via Podman inside WSL2 Ubuntu.

Usage:
    from app.agent.backends import WSLPodmanBackend

    backend = WSLPodmanBackend("Ubuntu")
    if await backend.is_available():
        result = await backend.run("python:3.11", ["python", "-c", "print('hello')"])
"""

from app.agent.backends.base import (
    BackendType,
    ContainerResult,
    ContainerBackend,
)
from app.agent.backends.wsl_podman import (
    WSLPodmanBackend,
    detect_wsl_distros,
    find_best_wsl_distro,
)

__all__ = [
    "BackendType",
    "ContainerResult",
    "ContainerBackend",
    "WSLPodmanBackend",
    "detect_wsl_distros",
    "find_best_wsl_distro",
]
