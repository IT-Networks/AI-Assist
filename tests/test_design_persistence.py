"""
Tests für den Design Persistence Service.

Testet Speicherung, Laden und Verwaltung von Design-Outputs.
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

from app.services.design_persistence import (
    DesignPersistence,
    DesignType,
    DesignStatus,
    get_design_persistence,
)


class TestDesignPersistence:
    """Tests für den DesignPersistence Service."""

    @pytest.fixture
    def temp_dir(self):
        """Erstellt ein temporäres Verzeichnis für Tests."""
        temp = tempfile.mkdtemp()
        yield Path(temp)
        shutil.rmtree(temp, ignore_errors=True)

    @pytest.fixture
    def persistence(self, temp_dir):
        """Erstellt eine DesignPersistence-Instanz mit temp-Verzeichnis."""
        return DesignPersistence(base_path=temp_dir)

    def test_directories_created(self, persistence, temp_dir):
        """Verzeichnisse werden automatisch erstellt."""
        assert (temp_dir / "brainstorm").exists()
        assert (temp_dir / "design").exists()

    def test_save_brainstorm(self, persistence):
        """Brainstorm kann gespeichert werden."""
        saved = persistence.save(
            design_type=DesignType.BRAINSTORM,
            title="Test Feature",
            content="# Test Feature\n\nThis is a test.",
            tags=["test", "feature"],
            sources=["skills/test"],
            command="brainstorm"
        )

        assert saved.metadata.id.startswith("brainstorm-")
        assert saved.metadata.title == "Test Feature"
        assert saved.metadata.type == DesignType.BRAINSTORM
        assert saved.metadata.status == DesignStatus.DRAFT
        assert "test" in saved.metadata.tags
        assert saved.file_path.endswith(".md")

    def test_save_design(self, persistence):
        """Design kann gespeichert werden."""
        saved = persistence.save(
            design_type=DesignType.DESIGN,
            title="Payment API",
            content="# Payment API\n\n## Overview\n\nAPI design.",
            tags=["api", "payment"],
            command="design"
        )

        assert saved.metadata.id.startswith("design-")
        assert saved.metadata.type == DesignType.DESIGN

    def test_save_creates_file(self, persistence, temp_dir):
        """Speichern erstellt eine MD-Datei."""
        persistence.save(
            design_type=DesignType.DESIGN,
            title="Test Design",
            content="Content here"
        )

        files = list((temp_dir / "design").glob("*.md"))
        assert len(files) == 1

        content = files[0].read_text(encoding="utf-8")
        assert "---" in content  # Frontmatter
        assert "id: design-" in content
        assert "Content here" in content

    def test_frontmatter_format(self, persistence, temp_dir):
        """Frontmatter ist korrekt formatiert."""
        persistence.save(
            design_type=DesignType.BRAINSTORM,
            title="Test",
            content="Body",
            tags=["a", "b"],
            sources=["source1"]
        )

        files = list((temp_dir / "brainstorm").glob("*.md"))
        content = files[0].read_text(encoding="utf-8")

        assert content.startswith("---")
        assert 'type: brainstorm' in content
        assert 'title: "Test"' in content
        assert 'tags: [a, b]' in content
        assert 'status: draft' in content

    def test_implementation_tracking_section(self, persistence, temp_dir):
        """Implementation Tracking Section wird hinzugefügt."""
        persistence.save(
            design_type=DesignType.DESIGN,
            title="Test",
            content="Content"
        )

        files = list((temp_dir / "design").glob("*.md"))
        content = files[0].read_text(encoding="utf-8")

        assert "## Implementation Tracking" in content
        assert "| Datei | Status | Commit |" in content

    def test_list_designs_empty(self, persistence):
        """Leere Liste bei keinen Designs."""
        designs = persistence.list_designs()
        assert len(designs) == 0

    def test_list_designs(self, persistence):
        """Designs werden gelistet."""
        persistence.save(DesignType.BRAINSTORM, "Test 1", "Content 1")
        persistence.save(DesignType.DESIGN, "Test 2", "Content 2")

        designs = persistence.list_designs()
        assert len(designs) == 2

    def test_list_designs_filter_type(self, persistence):
        """Filter nach Typ funktioniert."""
        persistence.save(DesignType.BRAINSTORM, "BS 1", "C1")
        persistence.save(DesignType.DESIGN, "D 1", "C2")

        brainstorms = persistence.list_designs(design_type=DesignType.BRAINSTORM)
        assert len(brainstorms) == 1
        assert brainstorms[0].type == DesignType.BRAINSTORM

    def test_list_designs_filter_tag(self, persistence):
        """Filter nach Tag funktioniert."""
        persistence.save(DesignType.DESIGN, "With Tag", "C", tags=["important"])
        persistence.save(DesignType.DESIGN, "No Tag", "C")

        tagged = persistence.list_designs(tag="important")
        assert len(tagged) == 1
        assert "important" in tagged[0].tags

    def test_get_design(self, persistence):
        """Design kann geladen werden."""
        saved = persistence.save(
            DesignType.DESIGN,
            "Loadable",
            "This is loadable content.",
            tags=["load"]
        )

        loaded = persistence.get_design(saved.metadata.id)

        assert loaded is not None
        assert loaded.metadata.id == saved.metadata.id
        assert loaded.metadata.title == "Loadable"
        assert "loadable content" in loaded.content

    def test_get_design_not_found(self, persistence):
        """None bei nicht existierendem Design."""
        result = persistence.get_design("nonexistent-id")
        assert result is None

    def test_update_status(self, persistence):
        """Status kann aktualisiert werden."""
        saved = persistence.save(DesignType.DESIGN, "Status Test", "C")
        assert saved.metadata.status == DesignStatus.DRAFT

        success = persistence.update_status(saved.metadata.id, DesignStatus.APPROVED)
        assert success

        loaded = persistence.get_design(saved.metadata.id)
        assert loaded.metadata.status == DesignStatus.APPROVED

    def test_update_status_not_found(self, persistence):
        """False bei nicht existierendem Design."""
        success = persistence.update_status("nonexistent", DesignStatus.APPROVED)
        assert not success

    def test_link_implementation(self, persistence):
        """Implementation kann verknüpft werden."""
        saved = persistence.save(DesignType.DESIGN, "Link Test", "C")

        success = persistence.link_implementation(
            saved.metadata.id,
            files=["app/services/payment.py", "app/api/routes/payment.py"],
            commit="abc1234"
        )
        assert success

        loaded = persistence.get_design(saved.metadata.id)
        assert len(loaded.metadata.implementation_refs) == 2
        assert loaded.metadata.implementation_refs[0].file_path == "app/services/payment.py"
        assert loaded.metadata.implementation_refs[0].commit == "abc1234"

    def test_link_implementation_updates_file(self, persistence, temp_dir):
        """Verknüpfung aktualisiert die MD-Datei."""
        saved = persistence.save(DesignType.DESIGN, "File Update Test", "C")

        persistence.link_implementation(
            saved.metadata.id,
            files=["test.py"],
            commit="xyz789"
        )

        files = list((temp_dir / "design").glob("*.md"))
        content = files[0].read_text(encoding="utf-8")

        assert "test.py" in content
        assert "xyz789" in content

    def test_unique_ids(self, persistence):
        """IDs sind eindeutig."""
        saved1 = persistence.save(DesignType.DESIGN, "First", "C")
        saved2 = persistence.save(DesignType.DESIGN, "Second", "C")

        assert saved1.metadata.id != saved2.metadata.id
        assert saved1.metadata.id.endswith("-001")
        assert saved2.metadata.id.endswith("-002")

    def test_stats(self, persistence):
        """Statistiken werden korrekt berechnet."""
        persistence.save(DesignType.BRAINSTORM, "BS1", "C")
        persistence.save(DesignType.DESIGN, "D1", "C")
        persistence.save(DesignType.DESIGN, "D2", "C")

        stats = persistence.get_stats()

        assert stats["total"] == 3
        assert stats["by_type"]["brainstorm"] == 1
        assert stats["by_type"]["design"] == 2
        assert stats["by_status"]["draft"] == 3

    def test_index_persistence(self, temp_dir):
        """Index wird persistent gespeichert."""
        persistence1 = DesignPersistence(base_path=temp_dir)
        persistence1.save(DesignType.DESIGN, "Persistent", "C")

        # Neue Instanz
        persistence2 = DesignPersistence(base_path=temp_dir)
        designs = persistence2.list_designs()

        assert len(designs) == 1
        assert designs[0].title == "Persistent"


class TestDesignPersistenceAPI:
    """Integration-Tests für die Designs API."""

    @pytest.fixture
    def test_client(self):
        from fastapi.testclient import TestClient
        from main import app
        return TestClient(app)

    def test_save_endpoint(self, test_client):
        """POST /api/designs/save funktioniert."""
        response = test_client.post(
            "/api/designs/save",
            json={
                "type": "design",
                "title": "API Test Design",
                "content": "# Test\n\nContent here.",
                "tags": ["test", "api"]
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert data["id"].startswith("design-")
        assert "file_path" in data

    def test_save_invalid_type(self, test_client):
        """Ungültiger Typ gibt Fehler."""
        response = test_client.post(
            "/api/designs/save",
            json={
                "type": "invalid",
                "title": "Test",
                "content": "C"
            }
        )
        assert response.status_code == 400

    def test_list_endpoint(self, test_client):
        """GET /api/designs funktioniert."""
        response = test_client.get("/api/designs")
        assert response.status_code == 200
        data = response.json()
        assert "designs" in data
        assert "total" in data

    def test_stats_endpoint(self, test_client):
        """GET /api/designs/stats funktioniert."""
        response = test_client.get("/api/designs/stats")
        assert response.status_code == 200
        data = response.json()
        assert "total" in data
        assert "by_type" in data
        assert "by_status" in data

    def test_get_design_not_found(self, test_client):
        """GET /api/designs/{id} gibt 404 für unbekannte ID."""
        response = test_client.get("/api/designs/nonexistent-id")
        assert response.status_code == 404

    def test_update_status_endpoint(self, test_client):
        """PUT /api/designs/{id}/status funktioniert."""
        # Erst speichern
        save_response = test_client.post(
            "/api/designs/save",
            json={
                "type": "brainstorm",
                "title": "Status Test",
                "content": "Content"
            }
        )
        design_id = save_response.json()["id"]

        # Dann Status ändern
        response = test_client.put(
            f"/api/designs/{design_id}/status",
            json={"status": "approved"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "approved"

    def test_link_implementation_endpoint(self, test_client):
        """POST /api/designs/{id}/link funktioniert."""
        # Erst speichern
        save_response = test_client.post(
            "/api/designs/save",
            json={
                "type": "design",
                "title": "Link Test",
                "content": "Content"
            }
        )
        design_id = save_response.json()["id"]

        # Dann verknüpfen
        response = test_client.post(
            f"/api/designs/{design_id}/link",
            json={
                "files": ["app/test.py", "app/test2.py"],
                "commit": "abc123"
            }
        )
        assert response.status_code == 200
        assert response.json()["linked_files"] == 2
