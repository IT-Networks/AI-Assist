"""Spezialisierte Sub-Agenten für parallele Datenquellen-Erkundung."""

from app.agent.sub_agents.code_explorer import CodeExplorerAgent
from app.agent.sub_agents.wiki_agent import WikiAgent
from app.agent.sub_agents.jira_agent import JiraAgent
from app.agent.sub_agents.database_agent import DatabaseAgent
from app.agent.sub_agents.knowledge_agent import KnowledgeAgent
from app.agent.sub_agent import SubAgentDispatcher

_dispatcher_instance = None


def get_sub_agent_dispatcher() -> SubAgentDispatcher:
    """Gibt den Sub-Agent-Dispatcher als Singleton zurück."""
    global _dispatcher_instance
    if _dispatcher_instance is None:
        agents = {
            "code_explorer": CodeExplorerAgent(),
            "wiki_agent": WikiAgent(),
            "jira_agent": JiraAgent(),
            "database_agent": DatabaseAgent(),
            "knowledge_agent": KnowledgeAgent(),
        }
        _dispatcher_instance = SubAgentDispatcher(agents)
    return _dispatcher_instance


__all__ = [
    "CodeExplorerAgent",
    "WikiAgent",
    "JiraAgent",
    "DatabaseAgent",
    "KnowledgeAgent",
    "SubAgentDispatcher",
    "get_sub_agent_dispatcher",
]
