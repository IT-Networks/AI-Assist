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
        "Extrahiere Feld-Beschreibungen, Service-Funktionen und Geschäftsprozess-Informationen."
    )
    allowed_tools = [
        "search_handbook",
        "get_service_info",
        "search_pdf",
        "search_skills",
    ]
