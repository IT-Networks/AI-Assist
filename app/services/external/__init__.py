"""
External Services - Clients für externe APIs.

Dieses Paket gruppiert alle Clients für externe Systeme:
- Confluence
- JIRA
- ServiceNow
- Datasource API

Verwendung:
    from app.services.external import get_confluence_client

    client = get_confluence_client()
    pages = await client.search("API Documentation")
"""

# Confluence
from app.services.confluence_client import (
    ConfluenceClient,
    get_confluence_client,
    close_confluence_client,
)

from app.services.confluence_cache import (
    ConfluenceCache,
    get_confluence_cache,
)

# JIRA
from app.services.jira_client import (
    JiraClient,
    get_jira_client,
)

# ServiceNow
from app.services.servicenow_client import (
    ServiceNowClient,
    get_servicenow_client,
)

# Datasource
from app.services.datasource_client import (
    make_datasource_request,
)

__all__ = [
    # Confluence
    "ConfluenceClient",
    "get_confluence_client",
    "close_confluence_client",
    "ConfluenceCache",
    "get_confluence_cache",
    # JIRA
    "JiraClient",
    "get_jira_client",
    # ServiceNow
    "ServiceNowClient",
    "get_servicenow_client",
    # Datasource
    "make_datasource_request",
]
