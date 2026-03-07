"""Code-Explorer Sub-Agent – durchsucht Java/Python-Quellcode und SQL-Dateien."""

from app.agent.sub_agent import SubAgent


class CodeExplorerAgent(SubAgent):
    """
    Spezialisiert auf das Durchsuchen von Quellcode.
    Zugriff auf: Java, Python, SQL/SQLJ, Dateilisting, Java-Referenz-Tracing.
    """

    name = "code_explorer"
    display_name = "Code-Explorer"
    description = (
        "Du durchsuchst Java- und Python-Quellcode sowie SQL/SQLJ-Dateien. "
        "Finde relevante Klassen, Methoden, Interfaces und SQL-Abfragen zur Anfrage. "
        "Lies die wichtigsten Dateien vollständig. "
        "Trace Klassen-Hierarchien wenn relevant (extends, implements). "
        "Extrahiere konkrete Methoden-Signaturen, Zeilennummern und Datei-Pfade."
    )
    allowed_tools = [
        "search_code",
        "read_file",
        "list_files",
        "trace_java_references",
        "read_sqlj_file",
    ]
