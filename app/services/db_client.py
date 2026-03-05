"""
DB2 Database Client - Sichere Datenbankabfragen mit Bestätigungs-Workflow.

Features:
- DB2 Host-Verbindung via ibm_db oder JDBC (jaydebeapi)
- Bestätigung vor jeder Query (konfigurierbar)
- Nur SELECT-Statements erlaubt (readonly mode)
- Row-Limit für sichere Abfragen
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from contextlib import contextmanager


@dataclass
class QueryResult:
    """Ergebnis einer Datenbankabfrage."""
    success: bool
    columns: List[str] = None
    rows: List[Tuple] = None
    row_count: int = 0
    error: Optional[str] = None
    query: str = ""
    truncated: bool = False  # True wenn max_rows erreicht


@dataclass
class QueryPreview:
    """Preview einer Query vor Ausführung."""
    query: str
    query_type: str  # SELECT, etc.
    tables: List[str]  # Betroffene Tabellen
    estimated_description: str


class DB2Client:
    """
    Client für DB2-Datenbankabfragen.

    Unterstützt:
    - ibm_db (native DB2 driver)
    - jaydebeapi (JDBC via Java)
    """

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str,
        schema: str = "",
        driver: str = "ibm_db",
        jdbc_driver_path: str = "",
        jdbc_driver_class: str = "com.ibm.db2.jcc.DB2Driver",
        max_rows: int = 1000,
        timeout_seconds: int = 30,
        readonly: bool = True
    ):
        self.host = host
        self.port = port
        self.database = database
        self.username = username
        self.password = password
        self.schema = schema
        self.driver = driver
        self.jdbc_driver_path = jdbc_driver_path
        self.jdbc_driver_class = jdbc_driver_class
        self.max_rows = max_rows
        self.timeout = timeout_seconds
        self.readonly = readonly
        self._connection = None

    def _get_connection_string(self) -> str:
        """Erstellt den Connection-String für ibm_db."""
        return (
            f"DATABASE={self.database};"
            f"HOSTNAME={self.host};"
            f"PORT={self.port};"
            f"PROTOCOL=TCPIP;"
            f"UID={self.username};"
            f"PWD={self.password};"
        )

    def _get_jdbc_url(self) -> str:
        """Erstellt die JDBC URL."""
        return f"jdbc:db2://{self.host}:{self.port}/{self.database}"

    @contextmanager
    def _connect(self):
        """Context Manager für Datenbankverbindung."""
        conn = None
        try:
            if self.driver == "ibm_db":
                import ibm_db
                conn = ibm_db.connect(self._get_connection_string(), "", "")
                if self.schema:
                    ibm_db.exec_immediate(conn, f"SET SCHEMA {self.schema}")
                yield conn
            elif self.driver == "jaydebeapi":
                import jaydebeapi
                conn = jaydebeapi.connect(
                    self.jdbc_driver_class,
                    self._get_jdbc_url(),
                    [self.username, self.password],
                    self.jdbc_driver_path
                )
                if self.schema:
                    cursor = conn.cursor()
                    cursor.execute(f"SET SCHEMA {self.schema}")
                    cursor.close()
                yield conn
            else:
                raise ValueError(f"Unbekannter Treiber: {self.driver}")
        finally:
            if conn:
                try:
                    if self.driver == "ibm_db":
                        import ibm_db
                        ibm_db.close(conn)
                    else:
                        conn.close()
                except Exception:
                    pass

    def validate_query(self, query: str) -> Tuple[bool, str]:
        """
        Validiert eine Query vor Ausführung.

        Returns:
            (is_valid, error_message)
        """
        # Whitespace normalisieren
        normalized = re.sub(r'\s+', ' ', query.strip().upper())

        # Nur SELECT erlauben im readonly mode
        if self.readonly:
            if not normalized.startswith('SELECT'):
                return False, "Nur SELECT-Statements erlaubt (readonly mode)"

            # Gefährliche Patterns prüfen
            dangerous = [
                r'\bINSERT\b', r'\bUPDATE\b', r'\bDELETE\b', r'\bDROP\b',
                r'\bTRUNCATE\b', r'\bALTER\b', r'\bCREATE\b', r'\bGRANT\b',
                r'\bREVOKE\b', r'\bEXEC\b', r'\bEXECUTE\b', r'\bCALL\b'
            ]
            for pattern in dangerous:
                if re.search(pattern, normalized):
                    return False, f"Statement-Typ nicht erlaubt: {pattern}"

        # SQL Injection Patterns prüfen
        injection_patterns = [
            r';\s*--',  # Statement terminator mit Comment
            r'UNION\s+ALL\s+SELECT',  # Union-basierte Injection
            r"'\s*OR\s*'1'\s*=\s*'1",  # Classic OR injection
        ]
        for pattern in injection_patterns:
            if re.search(pattern, normalized):
                return False, "Verdächtiges SQL-Pattern erkannt"

        return True, ""

    def preview_query(self, query: str) -> QueryPreview:
        """
        Erstellt eine Preview der Query für die Bestätigung.
        """
        normalized = re.sub(r'\s+', ' ', query.strip())
        upper = normalized.upper()

        # Query-Typ erkennen
        if upper.startswith('SELECT'):
            query_type = 'SELECT'
        elif upper.startswith('INSERT'):
            query_type = 'INSERT'
        elif upper.startswith('UPDATE'):
            query_type = 'UPDATE'
        elif upper.startswith('DELETE'):
            query_type = 'DELETE'
        else:
            query_type = 'OTHER'

        # Tabellen extrahieren
        tables = []
        # FROM clause
        from_match = re.search(r'\bFROM\s+([^\s,;(]+)', upper)
        if from_match:
            tables.append(from_match.group(1))
        # JOIN clauses
        join_matches = re.findall(r'\bJOIN\s+([^\s,;(]+)', upper)
        tables.extend(join_matches)

        # Beschreibung erstellen
        if query_type == 'SELECT':
            desc = f"Liest Daten aus: {', '.join(tables) if tables else 'unbekannt'}"
        else:
            desc = f"{query_type} auf: {', '.join(tables) if tables else 'unbekannt'}"

        return QueryPreview(
            query=normalized,
            query_type=query_type,
            tables=tables,
            estimated_description=desc
        )

    async def execute(self, query: str) -> QueryResult:
        """
        Führt eine Query aus (nach Validierung).
        """
        # Validieren
        is_valid, error = self.validate_query(query)
        if not is_valid:
            return QueryResult(success=False, error=error, query=query)

        try:
            if self.driver == "ibm_db":
                return await self._execute_ibm_db(query)
            else:
                return await self._execute_jdbc(query)
        except Exception as e:
            return QueryResult(success=False, error=str(e), query=query)

    async def _execute_ibm_db(self, query: str) -> QueryResult:
        """Führt Query mit ibm_db aus."""
        import ibm_db

        with self._connect() as conn:
            stmt = ibm_db.exec_immediate(conn, query)

            # Spalten holen
            columns = []
            num_cols = ibm_db.num_fields(stmt)
            for i in range(num_cols):
                columns.append(ibm_db.field_name(stmt, i))

            # Rows holen (mit Limit)
            rows = []
            truncated = False
            row = ibm_db.fetch_tuple(stmt)
            while row:
                rows.append(row)
                if len(rows) >= self.max_rows:
                    truncated = True
                    break
                row = ibm_db.fetch_tuple(stmt)

            return QueryResult(
                success=True,
                columns=columns,
                rows=rows,
                row_count=len(rows),
                query=query,
                truncated=truncated
            )

    async def _execute_jdbc(self, query: str) -> QueryResult:
        """Führt Query mit jaydebeapi aus."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(query)

            # Spalten holen
            columns = [desc[0] for desc in cursor.description] if cursor.description else []

            # Rows holen (mit Limit)
            rows = []
            truncated = False
            for row in cursor:
                rows.append(tuple(row))
                if len(rows) >= self.max_rows:
                    truncated = True
                    break

            cursor.close()

            return QueryResult(
                success=True,
                columns=columns,
                rows=rows,
                row_count=len(rows),
                query=query,
                truncated=truncated
            )

    async def get_tables(self, schema: str = None) -> List[str]:
        """Listet verfügbare Tabellen auf."""
        schema = schema or self.schema
        query = f"""
            SELECT TABNAME
            FROM SYSCAT.TABLES
            WHERE TABSCHEMA = '{schema}'
            AND TYPE = 'T'
            ORDER BY TABNAME
        """
        result = await self.execute(query)
        if result.success:
            return [row[0] for row in result.rows]
        return []

    async def describe_table(self, table_name: str, schema: str = None) -> Dict:
        """Gibt Tabellenstruktur zurück."""
        schema = schema or self.schema
        query = f"""
            SELECT COLNAME, TYPENAME, LENGTH, SCALE, NULLS, DEFAULT
            FROM SYSCAT.COLUMNS
            WHERE TABSCHEMA = '{schema}' AND TABNAME = '{table_name.upper()}'
            ORDER BY COLNO
        """
        result = await self.execute(query)
        if result.success:
            columns = []
            for row in result.rows:
                columns.append({
                    "name": row[0],
                    "type": row[1],
                    "length": row[2],
                    "scale": row[3],
                    "nullable": row[4] == 'Y',
                    "default": row[5]
                })
            return {"table": table_name, "schema": schema, "columns": columns}
        return {"error": result.error}

    def test_connection(self) -> Tuple[bool, str]:
        """Testet die Datenbankverbindung."""
        try:
            with self._connect() as conn:
                return True, "Verbindung erfolgreich"
        except Exception as e:
            return False, str(e)


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_db_client: Optional[DB2Client] = None


def get_db_client() -> Optional[DB2Client]:
    """Gibt die Singleton-Instanz des DB-Clients zurück."""
    global _db_client
    if _db_client is None:
        from app.core.config import settings
        if not settings.database.enabled:
            return None
        _db_client = DB2Client(
            host=settings.database.host,
            port=settings.database.port,
            database=settings.database.database,
            username=settings.database.username,
            password=settings.database.password,
            schema=settings.database.schema,
            driver=settings.database.driver,
            jdbc_driver_path=settings.database.jdbc_driver_path,
            jdbc_driver_class=settings.database.jdbc_driver_class,
            max_rows=settings.database.max_rows,
            timeout_seconds=settings.database.timeout_seconds,
            readonly=settings.database.readonly
        )
    return _db_client
