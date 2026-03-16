"""
Integration Tests für MCP-Enhancement.

Testet das Zusammenspiel aller Komponenten:
- Skill-Loading mit Command-Trigger
- Research mit Query-Klassifikation
- Output-Formatierung mit Diagrammen
"""

import pytest
from fastapi.testclient import TestClient


class TestMCPIntegration:
    """Integration-Tests für das MCP-Enhancement."""

    @pytest.fixture
    def test_client(self):
        """Erzeugt einen Test-Client für die API."""
        from main import app
        return TestClient(app)

    def test_research_classify_endpoint(self, test_client):
        """Research /classify Endpoint funktioniert."""
        response = test_client.post(
            "/api/research/classify",
            json={"query": "How to implement REST API in Spring Boot"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["classification"] == "technical"
        assert data["web_allowed"] is True

    def test_research_sanitize_endpoint(self, test_client):
        """Research /sanitize Endpoint funktioniert."""
        response = test_client.post(
            "/api/research/sanitize",
            json={"query": "Wie rufe ich OrderService auf? PROJ-123"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "OrderService" not in data["sanitized"]
        assert "PROJ-123" not in data["sanitized"]
        assert len(data["removed_terms"]) > 0

    def test_research_sources_endpoint(self, test_client):
        """Research /sources Endpoint listet verfügbare Quellen."""
        response = test_client.get("/api/research/sources")
        assert response.status_code == 200
        data = response.json()
        assert "sources" in data
        assert "scopes" in data
        source_ids = [s["id"] for s in data["sources"]]
        assert "skills" in source_ids
        assert "web" in source_ids

    def test_output_diagram_endpoint(self, test_client):
        """Output /diagram Endpoint generiert Diagramme."""
        response = test_client.post(
            "/api/output/diagram",
            json={
                "type": "sequence",
                "format": "mermaid",
                "title": "Test Sequence",
                "placeholders": {
                    "actor1": "User",
                    "actor1_label": "User",
                    "actor2": "API",
                    "actor2_label": "API",
                    "actor3": "DB",
                    "actor3_label": "Database",
                    "action1": "Request",
                    "action2": "Query",
                    "response1": "Response",
                    "response2": "Data"
                }
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "sequence"
        assert data["format"] == "mermaid"
        assert "sequenceDiagram" in data["content"]
        assert "```mermaid" in data["markdown"]

    def test_output_diagram_types_endpoint(self, test_client):
        """Output /diagram-types listet verfügbare Typen."""
        response = test_client.get("/api/output/diagram-types")
        assert response.status_code == 200
        data = response.json()
        assert "types" in data
        assert "formats" in data
        type_ids = [t["id"] for t in data["types"]]
        assert "sequence" in type_ids
        assert "component" in type_ids
        assert "erd" in type_ids

    def test_output_brainstorm_endpoint(self, test_client):
        """Output /brainstorm formatiert Brainstorm-Output."""
        response = test_client.post(
            "/api/output/brainstorm",
            json={
                "title": "Test Feature",
                "use_cases": [
                    {
                        "title": "User Login",
                        "actor": "User",
                        "trigger": "Click login",
                        "steps": ["Enter credentials", "Submit"],
                        "result": "Logged in",
                        "priority": "high"
                    }
                ],
                "stakeholders": [
                    {"name": "Admin", "role": "Administrator", "interest": "High", "influence": "High"}
                ],
                "risks": ["Security issues"],
                "assumptions": ["Users have accounts"],
                "open_questions": ["2FA required?"],
                "sources": ["Requirements doc"]
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert "# Test Feature" in data["markdown"]
        assert "## Use Cases" in data["markdown"]
        assert "UC-01: User Login" in data["markdown"]
        assert "## Stakeholder-Mapping" in data["markdown"]

    def test_output_design_endpoint(self, test_client):
        """Output /design formatiert Design-Output."""
        response = test_client.post(
            "/api/output/design",
            json={
                "title": "API Design",
                "overview": "REST API for user management",
                "components": [
                    {
                        "name": "UserController",
                        "responsibility": "Handle user requests",
                        "technology": "FastAPI"
                    }
                ],
                "decisions": [
                    {
                        "decision": "Use REST",
                        "alternatives": "GraphQL",
                        "rationale": "Simpler for our use case"
                    }
                ],
                "sources": ["Architecture docs"]
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert "# API Design" in data["markdown"]
        assert "## Design Overview" in data["markdown"]
        assert "## Komponenten" in data["markdown"]
        assert "## Entscheidungsprotokoll" in data["markdown"]

    def test_skills_command_triggers_endpoint(self, test_client):
        """Skills /command-triggers Endpoint funktioniert."""
        response = test_client.get("/api/skills/command-triggers")
        # 200 wenn Skills aktiviert, 404 wenn deaktiviert
        assert response.status_code in [200, 404]
        if response.status_code == 200:
            data = response.json()
            assert "triggers" in data
            assert isinstance(data["triggers"], dict)

    def test_output_invalid_diagram_type(self, test_client):
        """Ungültiger Diagramm-Typ gibt Fehler zurück."""
        response = test_client.post(
            "/api/output/diagram",
            json={
                "type": "invalid_type",
                "format": "mermaid",
                "title": "Test"
            }
        )
        assert response.status_code == 400
        assert "Ungültiger Diagramm-Typ" in response.json()["detail"]

    def test_output_format_endpoint(self, test_client):
        """Output /format formatiert mit Skill-Config."""
        response = test_client.post(
            "/api/output/format",
            json={
                "command": "brainstorm",
                "title": "Test Format",
                "raw_content": "## Summary\nTest summary content.\n\n## Details\nMore details here.",
                "sources": ["Source A"]
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["command"] == "brainstorm"
        assert data["title"] == "Test Format"
        assert "markdown" in data
        assert isinstance(data["is_valid"], bool)


class TestQueryClassificationIntegration:
    """Tests für Query-Klassifikation im Kontext."""

    @pytest.fixture
    def test_client(self):
        from main import app
        return TestClient(app)

    def test_technical_queries_allow_web(self, test_client):
        """Technische Queries erlauben Web-Recherche."""
        queries = [
            "Best practices for Spring Boot",
            "How to implement authentication in React",
            "Docker container networking",
        ]
        for query in queries:
            response = test_client.post(
                "/api/research/classify",
                json={"query": query}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["web_allowed"] is True, f"Query '{query}' should allow web"

    def test_internal_queries_block_web(self, test_client):
        """Interne Queries blockieren Web-Recherche."""
        queries = [
            "How to call OrderService",
            "PROJ-123 deployment status",
        ]
        for query in queries:
            response = test_client.post(
                "/api/research/classify",
                json={"query": query}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["classification"] == "internal", f"Query '{query}' should be internal"
            assert data["web_allowed"] is False, f"Query '{query}' should not allow web"


class TestDiagramGenerationIntegration:
    """Tests für Diagramm-Generierung."""

    @pytest.fixture
    def test_client(self):
        from main import app
        return TestClient(app)

    def test_all_diagram_types_generate(self, test_client):
        """Alle Diagramm-Typen können generiert werden."""
        types = ["sequence", "component", "usecase", "erd", "class", "context"]

        for diagram_type in types:
            response = test_client.post(
                "/api/output/diagram",
                json={
                    "type": diagram_type,
                    "format": "mermaid",
                    "title": f"Test {diagram_type}"
                }
            )
            assert response.status_code == 200, f"Failed for type: {diagram_type}"
            data = response.json()
            assert data["type"] == diagram_type
            assert len(data["content"]) > 0

    def test_ascii_format_generates(self, test_client):
        """ASCII-Format generiert korrekt."""
        response = test_client.post(
            "/api/output/diagram",
            json={
                "type": "component",
                "format": "ascii",
                "title": "Architecture"
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["format"] == "ascii"
        # ASCII diagrams use box-drawing characters
        assert "┌" in data["content"] or "[" in data["content"]
