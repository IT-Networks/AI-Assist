"""
Database API Routes - DB2-Datenbankverbindung verwalten.

Features:
- Verbindungstest
- Tabellen auflisten
- Tabellen-Schema anzeigen
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from app.core.config import settings


router = APIRouter(prefix="/api/database", tags=["database"])


@router.post("/test")
async def test_connection() -> Dict[str, Any]:
    """
    Testet die DB2-Datenbankverbindung.

    Returns:
        Dict mit success, message oder error
    """
    if not settings.database.enabled:
        return {
            "success": False,
            "error": "Datenbank ist nicht aktiviert. Setze database.enabled=true"
        }

    # Prüfe ob notwendige Felder gesetzt sind
    if not settings.database.host:
        return {"success": False, "error": "Kein Host konfiguriert"}
    if not settings.database.database:
        return {"success": False, "error": "Kein Datenbankname konfiguriert"}
    if not settings.database.username:
        return {"success": False, "error": "Kein Benutzername konfiguriert"}

    # Für JDBC: Prüfe ob JAR-Pfad gesetzt ist
    if settings.database.driver == "jaydebeapi":
        if not settings.database.jdbc_driver_path:
            return {
                "success": False,
                "error": "Kein JDBC-Treiber-Pfad (jdbc_driver_path) konfiguriert"
            }

        from pathlib import Path
        jar_path = Path(settings.database.jdbc_driver_path)
        if not jar_path.exists():
            return {
                "success": False,
                "error": f"JDBC-Treiber nicht gefunden: {settings.database.jdbc_driver_path}"
            }

    # Debug-Info sammeln
    config_info = {
        "driver": settings.database.driver,
        "host": settings.database.host,
        "port": settings.database.port,
        "database": settings.database.database,
        "schema": settings.database.db_schema or "(nicht gesetzt)",
        "username": settings.database.username,
        "jdbc_url": f"jdbc:db2://{settings.database.host}:{settings.database.port}/{settings.database.database}",
    }

    if settings.database.driver == "jaydebeapi":
        config_info["jdbc_driver_path"] = settings.database.jdbc_driver_path
        config_info["jdbc_driver_class"] = settings.database.jdbc_driver_class

    try:
        from app.services.db_client import get_db_client
        client = get_db_client()

        if not client:
            return {"success": False, "error": "DB-Client konnte nicht erstellt werden", "config": config_info}

        success, message = client.test_connection()

        if success:
            return {
                "success": True,
                "message": message,
                "config": config_info
            }
        else:
            return {"success": False, "error": message, "config": config_info}

    except Exception as e:
        import traceback
        return {
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
            "config": config_info
        }


@router.get("/tables")
async def list_tables(schema: Optional[str] = None) -> Dict[str, Any]:
    """
    Listet alle Tabellen im Schema auf.
    """
    if not settings.database.enabled:
        raise HTTPException(status_code=400, detail="Datenbank nicht aktiviert")

    try:
        from app.services.db_client import get_db_client
        client = get_db_client()

        if not client:
            raise HTTPException(status_code=500, detail="DB-Client nicht verfügbar")

        tables = await client.get_tables(schema)

        return {
            "schema": schema or settings.database.db_schema,
            "tables": tables,
            "count": len(tables)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tables/{table_name}")
async def describe_table(table_name: str, schema: Optional[str] = None) -> Dict[str, Any]:
    """
    Gibt die Struktur einer Tabelle zurück.
    """
    if not settings.database.enabled:
        raise HTTPException(status_code=400, detail="Datenbank nicht aktiviert")

    try:
        from app.services.db_client import get_db_client
        client = get_db_client()

        if not client:
            raise HTTPException(status_code=500, detail="DB-Client nicht verfügbar")

        info = await client.describe_table(table_name, schema)

        if "error" in info:
            raise HTTPException(status_code=404, detail=info["error"])

        return info
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
