"""
Lifespan Management - Startup/Shutdown Lifecycle für FastAPI.

Dieses Modul verwaltet den Anwendungs-Lebenszyklus:
- Service-Initialisierung beim Startup
- Graceful Shutdown mit Cleanup
- Health-Check Integration

Verwendung:
    from app.core.lifespan import create_lifespan

    app = FastAPI(lifespan=create_lifespan())

Features:
- Dependency-Injection-freundlich
- Async-First Design
- Modulare Service-Registrierung
- Error-Resilient (einzelne Fehler stoppen nicht den Start)
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Awaitable

from fastapi import FastAPI

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Service Registry for Lifecycle Management
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ServiceInfo:
    """Information über einen registrierten Service."""
    name: str
    startup: Optional[Callable[[], Awaitable[Any]]] = None
    shutdown: Optional[Callable[[], Awaitable[None]]] = None
    health_check: Optional[Callable[[], Awaitable[Dict[str, Any]]]] = None
    priority: int = 100  # Niedrigere Werte = früher gestartet
    instance: Any = None
    started: bool = False
    error: Optional[str] = None


class ServiceRegistry:
    """
    Registriert und verwaltet Service-Lifecycles.

    Services können sich für Startup/Shutdown registrieren.
    Die Registry sorgt für korrekte Reihenfolge und Error-Handling.
    """

    def __init__(self):
        self._services: Dict[str, ServiceInfo] = {}
        self._startup_order: List[str] = []
        self._started = False

    def register(
        self,
        name: str,
        startup: Optional[Callable[[], Awaitable[Any]]] = None,
        shutdown: Optional[Callable[[], Awaitable[None]]] = None,
        health_check: Optional[Callable[[], Awaitable[Dict[str, Any]]]] = None,
        priority: int = 100,
    ) -> None:
        """
        Registriert einen Service für Lifecycle-Management.

        Args:
            name: Eindeutiger Service-Name
            startup: Async-Funktion für Initialisierung
            shutdown: Async-Funktion für Cleanup
            health_check: Async-Funktion für Health-Status
            priority: Startreihenfolge (niedrigere = früher)
        """
        self._services[name] = ServiceInfo(
            name=name,
            startup=startup,
            shutdown=shutdown,
            health_check=health_check,
            priority=priority,
        )
        logger.debug(f"Service registriert: {name} (priority={priority})")

    async def startup_all(self) -> Dict[str, Any]:
        """
        Startet alle registrierten Services in Prioritäts-Reihenfolge.

        Returns:
            Dict mit Startup-Ergebnissen pro Service
        """
        results = {}

        # Nach Priorität sortieren
        sorted_services = sorted(
            self._services.values(),
            key=lambda s: s.priority
        )

        for service in sorted_services:
            if service.startup is None:
                continue

            try:
                logger.info(f"[startup] Starting {service.name}...")
                service.instance = await service.startup()
                service.started = True
                self._startup_order.append(service.name)
                results[service.name] = {"status": "started", "instance": service.instance}
                logger.info(f"[startup] {service.name} started successfully")
            except Exception as e:
                service.error = str(e)
                results[service.name] = {"status": "failed", "error": str(e)}
                logger.error(f"[startup] {service.name} failed: {e}")
                # Weitermachen mit anderen Services

        self._started = True
        return results

    async def shutdown_all(self) -> Dict[str, Any]:
        """
        Beendet alle gestarteten Services in umgekehrter Reihenfolge.

        Returns:
            Dict mit Shutdown-Ergebnissen pro Service
        """
        results = {}

        # Umgekehrte Startreihenfolge
        for name in reversed(self._startup_order):
            service = self._services.get(name)
            if service is None or service.shutdown is None:
                continue

            try:
                logger.info(f"[shutdown] Stopping {service.name}...")
                await service.shutdown()
                service.started = False
                results[service.name] = {"status": "stopped"}
                logger.info(f"[shutdown] {service.name} stopped successfully")
            except Exception as e:
                results[service.name] = {"status": "error", "error": str(e)}
                logger.error(f"[shutdown] {service.name} failed: {e}")

        self._started = False
        self._startup_order.clear()
        return results

    async def health_check_all(self) -> Dict[str, Any]:
        """
        Führt Health-Checks für alle Services durch.

        Returns:
            Dict mit Health-Status pro Service
        """
        results = {}

        for name, service in self._services.items():
            if service.health_check is None:
                results[name] = {
                    "status": "healthy" if service.started else "not_started",
                    "has_health_check": False,
                }
                continue

            try:
                health = await service.health_check()
                results[name] = {"status": "healthy", **health}
            except Exception as e:
                results[name] = {"status": "unhealthy", "error": str(e)}

        return results

    def get_service(self, name: str) -> Optional[Any]:
        """Gibt die Service-Instanz zurück."""
        service = self._services.get(name)
        return service.instance if service else None

    @property
    def is_started(self) -> bool:
        return self._started


# Globale Registry
_registry: Optional[ServiceRegistry] = None


def get_service_registry() -> ServiceRegistry:
    """Gibt die globale Service-Registry zurück."""
    global _registry
    if _registry is None:
        _registry = ServiceRegistry()
    return _registry


# ══════════════════════════════════════════════════════════════════════════════
# Standard Service Registrations
# ══════════════════════════════════════════════════════════════════════════════

def register_standard_services(registry: ServiceRegistry) -> None:
    """Registriert alle Standard-Services für Lifecycle-Management."""

    # LLM Client (Priorität 10 - früh)
    async def startup_llm():
        from app.services.llm_client import llm_client, get_llm_client
        client = get_llm_client()
        # Verbindung testen
        try:
            await client.list_models()
            return client
        except Exception:
            return client  # Weitermachen auch ohne Verbindung

    async def shutdown_llm():
        from app.services.llm_client import close_http_client
        await close_http_client()

    async def health_llm():
        from app.services.llm_client import get_llm_client
        from app.core.config import settings
        try:
            client = get_llm_client()
            models = await client.list_models()
            return {
                "base_url": settings.llm.base_url,
                "models_available": len(models),
            }
        except Exception as e:
            return {"error": str(e)}

    registry.register(
        "llm_client",
        startup=startup_llm,
        shutdown=shutdown_llm,
        health_check=health_llm,
        priority=10,
    )

    # Confluence Client (Priorität 50)
    async def shutdown_confluence():
        from app.services.confluence_client import close_confluence_client
        await close_confluence_client()

    registry.register(
        "confluence_client",
        shutdown=shutdown_confluence,
        priority=50,
    )

    # Java Indexer (Priorität 30)
    async def startup_java_indexer():
        from app.core.config import settings
        if not settings.index.auto_build_on_start:
            return None
        if not settings.java.get_active_path():
            return None

        from app.services.java_reader import JavaReader
        from app.services.java_indexer import get_java_indexer

        reader = JavaReader(settings.java.get_active_path())
        indexer = get_java_indexer()

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: indexer.build(settings.java.get_active_path(), reader, force=False)
        )
        return indexer

    async def health_java_indexer():
        from app.services.java_indexer import get_java_indexer
        indexer = get_java_indexer()
        return {"is_built": indexer.is_built()}

    registry.register(
        "java_indexer",
        startup=startup_java_indexer,
        health_check=health_java_indexer,
        priority=30,
    )

    # Handbook Indexer (Priorität 35)
    async def startup_handbook_indexer():
        from app.core.config import settings
        if not settings.handbook.enabled:
            return None
        if not settings.handbook.index_on_start:
            return None
        if not settings.handbook.path:
            return None

        from app.services.handbook_indexer import get_handbook_indexer
        indexer = get_handbook_indexer()

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: indexer.build(
                handbook_path=settings.handbook.path,
                functions_subdir=settings.handbook.functions_subdir,
                fields_subdir=settings.handbook.fields_subdir,
                exclude_patterns=settings.handbook.exclude_patterns,
                force=False,
            )
        )
        return indexer

    registry.register(
        "handbook_indexer",
        startup=startup_handbook_indexer,
        priority=35,
    )

    # Skill Manager (Priorität 40)
    async def startup_skills():
        from app.core.config import settings
        if not settings.skills.enabled:
            return None

        from app.services.skill_manager import get_skill_manager
        manager = get_skill_manager()
        return manager

    registry.register(
        "skill_manager",
        startup=startup_skills,
        priority=40,
    )

    # Agent Orchestrator (Priorität 60)
    async def startup_agent():
        from app.agent import get_tool_registry, get_agent_orchestrator
        from app.core.config import settings

        registry = get_tool_registry()
        orchestrator = get_agent_orchestrator()

        # Datenquellen-Tools
        try:
            from app.agent.datasource_tools import register_datasource_tools
            register_datasource_tools(registry)
        except Exception:
            pass

        # MQ-Tools
        try:
            from app.agent.mq_tools import register_mq_tools
            register_mq_tools(registry)
        except Exception:
            pass

        # WLP-Tools
        try:
            from app.agent.wlp_tools import register_wlp_tools
            register_wlp_tools(registry)
        except Exception:
            pass

        return {"registry": registry, "orchestrator": orchestrator}

    registry.register(
        "agent",
        startup=startup_agent,
        priority=60,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Lifespan Factory
# ══════════════════════════════════════════════════════════════════════════════

def create_lifespan(
    additional_startup: Optional[List[Callable[[], Awaitable[Any]]]] = None,
    additional_shutdown: Optional[List[Callable[[], Awaitable[None]]]] = None,
):
    """
    Erstellt einen Lifespan-Manager für FastAPI.

    Args:
        additional_startup: Extra Startup-Funktionen
        additional_shutdown: Extra Shutdown-Funktionen

    Returns:
        Async context manager für FastAPI lifespan

    Beispiel:
        app = FastAPI(lifespan=create_lifespan())
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        registry = get_service_registry()

        # Standard-Services registrieren
        register_standard_services(registry)

        # Alle Services starten
        results = await registry.startup_all()

        started = sum(1 for r in results.values() if r.get("status") == "started")
        failed = sum(1 for r in results.values() if r.get("status") == "failed")
        print(f"[startup] Services gestartet: {started}, fehlgeschlagen: {failed}")

        # Zusätzliche Startup-Funktionen
        if additional_startup:
            for func in additional_startup:
                try:
                    await func()
                except Exception as e:
                    logger.error(f"[startup] Additional startup failed: {e}")

        yield

        # Zusätzliche Shutdown-Funktionen
        if additional_shutdown:
            for func in additional_shutdown:
                try:
                    await func()
                except Exception as e:
                    logger.error(f"[shutdown] Additional shutdown failed: {e}")

        # Alle Services beenden
        results = await registry.shutdown_all()

        stopped = sum(1 for r in results.values() if r.get("status") == "stopped")
        print(f"[shutdown] Services beendet: {stopped}")

    return lifespan


# ══════════════════════════════════════════════════════════════════════════════
# FastAPI Dependency for Health Checks
# ══════════════════════════════════════════════════════════════════════════════

async def get_system_health() -> Dict[str, Any]:
    """
    FastAPI Dependency für System-Health.

    Verwendung:
        @router.get("/health")
        async def health(status: Dict = Depends(get_system_health)):
            return status
    """
    registry = get_service_registry()
    services = await registry.health_check_all()

    all_healthy = all(
        s.get("status") in ("healthy", "not_started")
        for s in services.values()
    )

    return {
        "status": "healthy" if all_healthy else "unhealthy",
        "services": services,
    }
