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
        # Standard DB2 JDBC URL Format
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
                from pathlib import Path

                # Prüfe ob JAR existiert
                jar_path = Path(self.jdbc_driver_path)
                if not jar_path.exists():
                    raise FileNotFoundError(f"JDBC-Treiber nicht gefunden: {self.jdbc_driver_path}")

                jdbc_url = self._get_jdbc_url()
                print(f"[db] Connecting via JDBC: {jdbc_url}")
                print(f"[db] Driver class: {self.jdbc_driver_class}")
                print(f"[db] JAR: {self.jdbc_driver_path}")

                # JPype JVM starten falls nötig
                try:
                    import jpype
                    if not jpype.isJVMStarted():
                        # JVM mit dem JDBC-Treiber im Classpath starten
                        jpype.startJVM(classpath=[str(jar_path)])
                        print("[db] JVM started")
                except Exception as e:
                    print(f"[db] JPype note: {e}")

                conn = jaydebeapi.connect(
                    self.jdbc_driver_class,
                    jdbc_url,
                    [self.username, self.password],
                    str(jar_path)
                )
                print("[db] Connection established")

                if self.schema:
                    cursor = conn.cursor()
                    cursor.execute(f"SET CURRENT SCHEMA = '{self.schema}'")
                    cursor.close()
                    print(f"[db] Schema set to: {self.schema}")

                yield conn
            else:
                raise ValueError(f"Unbekannter Treiber: {self.driver}")
        except Exception as e:
            print(f"[db] Connection error: {type(e).__name__}: {e}")
            raise
        finally:
            if conn:
                try:
                    if self.driver == "ibm_db":
                        import ibm_db
                        ibm_db.close(conn)
                    else:
                        conn.close()
                        print("[db] Connection closed")
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
            all_rows = cursor.fetchall()
            for row in all_rows:
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

        # Verschiedene DB2-Varianten haben unterschiedliche Systemkataloge:
        # - DB2 LUW (Linux/Unix/Windows): SYSCAT.TABLES
        # - DB2 z/OS (Mainframe): SYSIBM.SYSTABLES
        # - DB2 iSeries (AS/400): QSYS2.SYSTABLES

        queries_with_schema = [
            # DB2 z/OS (Mainframe)
            f"SELECT NAME FROM SYSIBM.SYSTABLES WHERE CREATOR = '{(schema or '').upper()}' AND TYPE = 'T' ORDER BY NAME FETCH FIRST 100 ROWS ONLY",
            # DB2 LUW
            f"SELECT TABNAME FROM SYSCAT.TABLES WHERE TABSCHEMA = '{(schema or '').upper()}' AND TYPE = 'T' ORDER BY TABNAME FETCH FIRST 100 ROWS ONLY",
            # DB2 iSeries
            f"SELECT TABLE_NAME FROM QSYS2.SYSTABLES WHERE TABLE_SCHEMA = '{(schema or '').upper()}' AND TABLE_TYPE = 'T' ORDER BY TABLE_NAME FETCH FIRST 100 ROWS ONLY",
        ]

        queries_all = [
            # DB2 z/OS - alle Schemas
            "SELECT CREATOR, NAME FROM SYSIBM.SYSTABLES WHERE TYPE = 'T' AND CREATOR NOT LIKE 'SYS%' ORDER BY CREATOR, NAME FETCH FIRST 100 ROWS ONLY",
            # DB2 LUW - alle Schemas
            "SELECT TABSCHEMA, TABNAME FROM SYSCAT.TABLES WHERE TYPE = 'T' AND TABSCHEMA NOT LIKE 'SYS%' ORDER BY TABSCHEMA, TABNAME FETCH FIRST 100 ROWS ONLY",
            # DB2 iSeries
            "SELECT TABLE_SCHEMA, TABLE_NAME FROM QSYS2.SYSTABLES WHERE TABLE_TYPE = 'T' AND TABLE_SCHEMA NOT LIKE 'SYS%' ORDER BY TABLE_SCHEMA, TABLE_NAME FETCH FIRST 100 ROWS ONLY",
        ]

        # Wähle Query-Liste basierend auf Schema
        if schema:
            queries = queries_with_schema
        else:
            queries = queries_all

        # Versuche jede Query bis eine funktioniert
        last_error = None
        for query in queries:
            result = await self.execute(query)
            if result.success and result.rows:
                if schema:
                    return [row[0] for row in result.rows]
                else:
                    return [f"{row[0]}.{row[1]}" for row in result.rows]
            if result.error:
                last_error = result.error

        # Keine Query hat funktioniert
        if schema:
            return [f"Keine Tabellen in Schema '{schema}' gefunden. Fehler: {last_error}"]
        return [f"Konnte Tabellen nicht auflisten. Fehler: {last_error}. Versuche eine direkte Query."]

    async def describe_table(self, table_name: str, schema: str = None) -> Dict:
        """Gibt Tabellenstruktur zurück."""
        schema = schema or self.schema

        # Wenn Tabellenname Schema enthält (SCHEMA.TABLE)
        if '.' in table_name and not schema:
            parts = table_name.split('.', 1)
            schema = parts[0]
            table_name = parts[1]

        if not schema:
            return {"error": "Kein Schema angegeben. Nutze SCHEMA.TABELLE oder setze database.schema in config.yaml"}

        schema_upper = schema.upper()
        table_upper = table_name.upper()

        # Verschiedene DB2-Varianten
        queries = [
            # DB2 z/OS (Mainframe) - SYSIBM.SYSCOLUMNS
            f"""SELECT NAME, COLTYPE, LENGTH, SCALE, NULLS, DEFAULT
                FROM SYSIBM.SYSCOLUMNS
                WHERE TBCREATOR = '{schema_upper}' AND TBNAME = '{table_upper}'
                ORDER BY COLNO""",
            # DB2 LUW - SYSCAT.COLUMNS
            f"""SELECT COLNAME, TYPENAME, LENGTH, SCALE, NULLS, DEFAULT
                FROM SYSCAT.COLUMNS
                WHERE TABSCHEMA = '{schema_upper}' AND TABNAME = '{table_upper}'
                ORDER BY COLNO""",
            # DB2 iSeries - QSYS2.SYSCOLUMNS
            f"""SELECT COLUMN_NAME, DATA_TYPE, LENGTH, NUMERIC_SCALE, IS_NULLABLE, COLUMN_DEFAULT
                FROM QSYS2.SYSCOLUMNS
                WHERE TABLE_SCHEMA = '{schema_upper}' AND TABLE_NAME = '{table_upper}'
                ORDER BY ORDINAL_POSITION""",
        ]

        for query in queries:
            result = await self.execute(query)
            if result.success and result.rows:
                columns = []
                for row in result.rows:
                    nullable_val = row[4]
                    # Verschiedene Formate für NULLS/IS_NULLABLE
                    is_nullable = nullable_val in ('Y', 'YES', True, 1)
                    columns.append({
                        "name": row[0],
                        "type": row[1],
                        "length": row[2],
                        "scale": row[3],
                        "nullable": is_nullable,
                        "default": row[5]
                    })
                return {"table": table_name, "schema": schema, "columns": columns}

        return {"error": f"Tabelle '{schema}.{table_name}' nicht gefunden oder Systemkatalog nicht zugänglich"}

    def test_connection(self) -> Tuple[bool, str]:
        """Testet die Datenbankverbindung."""
        try:
            with self._connect() as conn:
                # Einfache Test-Query ausführen
                if self.driver == "jaydebeapi":
                    cursor = conn.cursor()
                    cursor.execute("SELECT 1 FROM SYSIBM.SYSDUMMY1")
                    result = cursor.fetchone()
                    cursor.close()
                    if result:
                        return True, f"Verbindung erfolgreich (JDBC: {self._get_jdbc_url()})"
                return True, "Verbindung erfolgreich"
        except FileNotFoundError as e:
            return False, f"JDBC-Treiber nicht gefunden: {e}"
        except Exception as e:
            error_msg = str(e)
            # Hilfreiche Hinweise bei typischen Fehlern
            if "ClassNotFoundException" in error_msg:
                error_msg += f"\n→ Prüfe jdbc_driver_class (aktuell: {self.jdbc_driver_class})"
            elif "No suitable driver" in error_msg:
                error_msg += f"\n→ JAR-Pfad prüfen: {self.jdbc_driver_path}"
            elif "authentication" in error_msg.lower() or "password" in error_msg.lower():
                error_msg += "\n→ Username/Passwort prüfen"
            elif "-204" in error_msg or "42704" in error_msg:
                error_msg += "\n→ Tabelle/Objekt nicht gefunden - Schema korrekt?"
            return False, error_msg


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
            schema=settings.database.db_schema,
            driver=settings.database.driver,
            jdbc_driver_path=settings.database.jdbc_driver_path,
            jdbc_driver_class=settings.database.jdbc_driver_class,
            max_rows=settings.database.max_rows,
            timeout_seconds=settings.database.timeout_seconds,
            readonly=settings.database.readonly
        )
    return _db_client
