"""Datenquellen Sub-Agent – ruft konfigurierte HTTP-Datenquellen (ds_*) ab."""

from typing import List

from app.agent.sub_agent import SubAgent


class DatasourceAgent(SubAgent):
    """
    Spezialisiert auf konfigurierte interne Datenquellen (Jenkins, GitHub, etc.).
    Erlaubte Tools werden dynamisch aus der Tool-Registry gelesen (ds_*-Prefix).
    """

    name = "datasource_agent"
    display_name = "Datenquellen-Agent"
    description = (
        "Du befragst konfigurierte interne Datenquellen (z.B. Jenkins-Jobs, GitHub-Repos, "
        "interne REST-APIs). Nutze ds_*-Tools um relevante Informationen abzurufen. "
        "Extrahiere konkrete Fakten: Status, Versionen, Ergebnisse, Links."
    )

    @property
    def allowed_tools(self) -> List[str]:  # type: ignore[override]
        """Liest dynamisch alle ds_*-Tools aus der Registry."""
        try:
            from app.agent.tools import get_tool_registry
            registry = get_tool_registry()
            return [name for name in registry.tools if name.startswith("ds_")]
        except Exception:
            return []
