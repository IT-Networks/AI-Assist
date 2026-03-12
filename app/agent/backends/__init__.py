"""
Container Backend Abstraction Layer.

Provides a unified interface for different container runtimes:
- Docker (native)
- Podman (native)
- Podman via WSL (wsl-podman)
- Podman Machine (Windows VM)

Usage:
    from app.agent.backends import ContainerBackendFactory

    backend = await ContainerBackendFactory.create()
    if backend:
        result = await backend.run("python:3.11", ["python", "-c", "print('hello')"])
"""

from app.agent.backends.base import (
    BackendType,
    ContainerResult,
    ContainerBackend,
)
from app.agent.backends.factory import ContainerBackendFactory

__all__ = [
    "BackendType",
    "ContainerResult",
    "ContainerBackend",
    "ContainerBackendFactory",
]
