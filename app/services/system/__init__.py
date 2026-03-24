"""
System Services - System-Utilities und Infrastruktur.

Dieses Paket gruppiert System-Services:
- UpdateService (Auto-Updates)
- SelfHealing (Error Recovery)
- ExternalAccessLogger (Audit-Logging)
- Anonymizer (Daten-Anonymisierung)
- ScriptManager (Script-Ausführung)
- DbClient (Datenbank)

Verwendung:
    from app.services.system import get_self_healing_engine

    engine = get_self_healing_engine()
    suggestion = engine.suggest_fix(error)
"""

from app.services.update_service import (
    UpdateService,
    get_update_service,
)

from app.services.self_healing import (
    SelfHealingEngine,
    get_self_healing_engine,
)

from app.services.external_access_logger import (
    ExternalAccessLogger,
    get_access_logger,
)

from app.services.anonymizer import (
    Anonymizer,
    AnonymizerConfig,
    get_anonymizer,
)

from app.services.script_manager import (
    ScriptManager,
    get_script_manager,
)

from app.services.db_client import (
    DB2Client,
    get_db_client,
)

__all__ = [
    # Updates
    "UpdateService",
    "get_update_service",
    # Self-Healing
    "SelfHealingEngine",
    "get_self_healing_engine",
    # Access Logging
    "ExternalAccessLogger",
    "get_access_logger",
    # Anonymization
    "Anonymizer",
    "AnonymizerConfig",
    "get_anonymizer",
    # Scripts
    "ScriptManager",
    "get_script_manager",
    # Database
    "DB2Client",
    "get_db_client",
]
