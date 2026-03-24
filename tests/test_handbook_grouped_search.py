"""
Tests für die verbesserte Handbuch-Suche mit Gruppierung.

Testet:
1. search_grouped() Methode im HandbookIndexer
2. /api/handbook/search/grouped Endpoint
3. Response-Struktur
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient


class TestSearchGroupedMethod:
    """Tests für HandbookIndexer.search_grouped()"""

    def test_search_grouped_empty_query(self):
        """Leere Query gibt leere Liste zurück."""
        from app.services.handbook_indexer import HandbookIndexer

        indexer = HandbookIndexer(db_path=":memory:")

        result = indexer.search_grouped("")
        assert result == []

        result = indexer.search_grouped("  ")
        assert result == []

    def test_search_grouped_short_query(self):
        """Zu kurze Query (< 2 Zeichen) gibt leere Liste zurück."""
        from app.services.handbook_indexer import HandbookIndexer

        indexer = HandbookIndexer(db_path=":memory:")

        result = indexer.search_grouped("a")
        assert result == []

    def test_search_grouped_returns_correct_structure(self, tmp_path):
        """Ergebnis hat korrekte Struktur."""
        from app.services.handbook_indexer import HandbookIndexer

        db_file = tmp_path / "test_handbook.db"
        indexer = HandbookIndexer(db_path=str(db_file))

        # Leerer Index gibt leere Liste zurück (keine Matches)
        result = indexer.search_grouped("TestService")
        assert isinstance(result, list)
        assert len(result) == 0  # Leer weil keine Daten im Index


class TestSearchGroupedEndpoint:
    """Tests für /api/handbook/search/grouped Endpoint."""

    def test_grouped_search_requires_min_3_chars(self):
        """Endpoint erfordert mindestens 3 Zeichen."""
        from app.api.routes.handbook import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)

        client = TestClient(app)

        # Zu kurze Query sollte 422 zurückgeben
        response = client.get("/api/handbook/search/grouped?q=ab")
        assert response.status_code == 422

    @patch('app.api.routes.handbook.get_settings')
    @patch('app.api.routes.handbook.get_handbook_indexer')
    def test_grouped_search_returns_grouped_results(self, mock_indexer, mock_settings):
        """Endpoint gibt gruppierte Ergebnisse zurück."""
        from app.api.routes.handbook import router
        from fastapi import FastAPI

        # Mock Settings
        mock_settings.return_value.handbook.enabled = True

        # Mock Indexer
        indexer = MagicMock()
        indexer.is_built.return_value = True
        indexer.search_grouped.return_value = [
            {
                "service_id": "test-service",
                "service_name": "TestService",
                "description": "Test description",
                "match_count": 3,
                "matched_tabs": ["Fachlich", "Parameter"],
                "top_snippets": [
                    {"tab_name": "Fachlich", "text": ">>>Test<<< content"}
                ]
            }
        ]
        mock_indexer.return_value = indexer

        app = FastAPI()
        app.include_router(router)

        client = TestClient(app)

        response = client.get("/api/handbook/search/grouped?q=Test")
        assert response.status_code == 200

        data = response.json()
        assert len(data) == 1
        assert data[0]["service_id"] == "test-service"
        assert data[0]["match_count"] == 3
        assert "Fachlich" in data[0]["matched_tabs"]


class TestResponseModels:
    """Tests für Response Models."""

    def test_grouped_search_result_model(self):
        """GroupedSearchResult Model Validierung."""
        from app.api.routes.handbook import GroupedSearchResult, SnippetInfo

        # Valides Objekt
        result = GroupedSearchResult(
            service_id="test-id",
            service_name="TestService",
            description="Description",
            match_count=5,
            matched_tabs=["Tab1", "Tab2"],
            top_snippets=[
                SnippetInfo(tab_name="Tab1", text="Snippet text")
            ]
        )

        assert result.service_id == "test-id"
        assert result.match_count == 5
        assert len(result.matched_tabs) == 2
        assert len(result.top_snippets) == 1

    def test_snippet_info_model(self):
        """SnippetInfo Model Validierung."""
        from app.api.routes.handbook import SnippetInfo

        snippet = SnippetInfo(tab_name="Fachlich", text=">>>match<<< text")

        assert snippet.tab_name == "Fachlich"
        assert ">>>match<<<" in snippet.text
