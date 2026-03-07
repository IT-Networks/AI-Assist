"""Database-Agent – erkundet DB2-Datenbankstruktur und führt Abfragen aus."""

from app.agent.sub_agent import SubAgent


class DatabaseAgent(SubAgent):
    """
    Spezialisiert auf DB2-Datenbankrecherche.
    Zugriff auf: Tabellen-Listing, Schema-Beschreibung, SELECT-Abfragen (readonly).
    Schreiboperationen sind nicht erlaubt.
    """

    name = "database_agent"
    display_name = "Datenbank-Agent"
    description = (
        "Du untersuchst die DB2-Datenbankstruktur und führst SELECT-Abfragen aus. "
        "Liste relevante Tabellen auf, beschreibe deren Schema (Spalten, Typen). "
        "Führe gezielte SELECT-Abfragen aus um Beispieldaten oder Zusammenfassungen zu liefern. "
        "Nur lesende Operationen (SELECT) sind erlaubt – niemals INSERT, UPDATE, DELETE oder DDL. "
        "Extrahiere Tabellennamen, Spaltenstrukturen und repräsentative Datensätze."
    )
    allowed_tools = [
        "list_database_tables",
        "describe_database_table",
        "query_database",
    ]
