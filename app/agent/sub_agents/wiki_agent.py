"""Wiki-Agent – durchsucht Confluence-Dokumentation."""

from app.agent.sub_agent import SubAgent


class WikiAgent(SubAgent):
    """
    Spezialisiert auf Confluence-Wiki-Recherche.
    Zugriff auf: Confluence-Suche und vollständige Seiteninhalte.
    """

    name = "wiki_agent"
    display_name = "Wiki-Agent"
    description = (
        "Du durchsuchst Confluence-Wiki-Seiten nach relevanter Dokumentation. "
        "Suche nach technischen Beschreibungen, Architektur-Dokumenten und Prozess-Beschreibungen. "
        "Lies die relevantesten Seiten vollständig aus. "
        "Extrahiere konkrete Fakten, Konfigurationen und Prozessschritte aus der Dokumentation."
    )
    allowed_tools = [
        "search_confluence",
        "read_confluence_page",
    ]
