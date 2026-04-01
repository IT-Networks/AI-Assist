"""Knowledge-Agent – durchsucht Handbuch, PDFs und Skill-Wissensbasis."""

from app.agent.sub_agent import SubAgent


class KnowledgeAgent(SubAgent):
    """
    Spezialisiert auf interne Wissensbasis-Recherche.
    Zugriff auf: HTML-Handbuch, PDF-Dokumente, Skill-Wissensbasis.
    """

    name = "knowledge_agent"
    display_name = "Wissens-Agent"
    description = (
        "Du durchsuchst die interne Wissensbasis: HTML-Handbuch, PDF-Dokumente und Skill-Einträge. "
        "Suche nach Service-Beschreibungen, Feld-Definitionen und Prozess-Dokumentation. "
        "Nutze get_service_info für konkrete Service-Details aus dem Handbuch. "
        "Für große PDFs: Nutze get_pdf_info um die Seitenanzahl zu ermitteln, dann lies relevante "
        "Abschnitte mit read_pdf_pages seitenweise (max 30 Seiten pro Aufruf). "
        "Suche zuerst mit search_pdf nach relevanten Seiten, dann lies den Kontext drumherum. "
        "Extrahiere Feld-Beschreibungen, Service-Funktionen und Geschäftsprozess-Informationen."
    )
    allowed_tools = [
        "search_handbook",
        "get_service_info",
        "search_pdf",
        "get_pdf_info",
        "read_pdf_pages",
        "search_skills",
        "search_knowledge",
    ]
