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
        if not Path(settings.database.jdbc_driver_path).exists():
            return {
                "success": False,
                "error": f"JDBC-Treiber nicht gefunden: {settings.database.jdbc_driver_path}"
            }

    try:
        from app.services.db_client import get_db_client
        client = get_db_client()

        if not client:
            return {"success": False, "error": "DB-Client konnte nicht erstellt werden"}

        success, message = client.test_connection()

        if success:
            return {
                "success": True,
                "message": f"Verbindung erfolgreich zu {settings.database.host}:{settings.database.port}/{settings.database.database}"
            }
        else:
            return {"success": False, "error": message}

    except Exception as e:
        return {"success": False, "error": str(e)}


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
            "schema": schema or settings.database.schema,
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
