"""Jira-Agent – durchsucht Jira-Tickets und Issues."""

from app.agent.sub_agent import SubAgent


class JiraAgent(SubAgent):
    """
    Spezialisiert auf Jira-Recherche.
    Zugriff auf: Jira-Suche (JQL/Freitext) und vollständige Issue-Details.
    """

    name = "jira_agent"
    display_name = "Jira-Agent"
    description = (
        "Du durchsuchst Jira-Tickets nach relevanten Bugs, Stories und Aufgaben. "
        "Suche mit geeigneten JQL-Queries oder Stichwörtern. "
        "Lies wichtige Tickets vollständig inklusive Kommentare. "
        "Extrahiere Issue-Keys, Status, Assignee, Priorität und Beschreibungen."
    )
    allowed_tools = [
        "search_jira",
        "read_jira_issue",
    ]
