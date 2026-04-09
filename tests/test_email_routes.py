"""Tests für app.api.routes.email — API-Endpoint-Tests."""

import json
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """TestClient mit gemockten Email-Settings."""
    from main import app
    return TestClient(app)


@pytest.fixture
def enabled_settings():
    """Patch settings.email.enabled = True."""
    with patch("app.core.config.settings") as mock_settings:
        mock_settings.email.enabled = True
        mock_settings.email.ews_url = "https://mail.example.com/EWS/Exchange.asmx"
        mock_settings.email.smtp_address = "test@example.com"
        mock_settings.email.polling_interval_minutes = 5
        yield mock_settings


class TestConnectionTest:
    def test_disabled(self, client):
        with patch("app.core.config.settings") as mock:
            mock.email.enabled = False
            resp = client.post("/api/email/test")
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is False
            assert "nicht aktiviert" in data["error"]

    def test_no_url(self, client):
        with patch("app.core.config.settings") as mock:
            mock.email.enabled = True
            mock.email.ews_url = ""
            resp = client.post("/api/email/test")
            data = resp.json()
            assert data["success"] is False
            assert "URL" in data["error"]


class TestRulesAPI:
    def test_get_rules_empty(self, client):
        with patch("app.services.email_automation.get_email_automation") as mock_auto:
            mock_auto.return_value.get_rules.return_value = []
            resp = client.get("/api/email/rules")
            assert resp.status_code == 200
            assert resp.json()["rules"] == []

    def test_create_rule(self, client):
        from app.models.email_models import EmailRule

        with patch("app.services.email_automation.get_email_automation") as mock_auto:
            mock_rule = EmailRule(name="Test", description="Desc")
            mock_auto.return_value.add_rule.return_value = mock_rule

            resp = client.post("/api/email/rules", json={
                "name": "Test",
                "description": "Desc",
                "sender_filter": "",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            assert data["rule"]["name"] == "Test"

    def test_delete_rule(self, client):
        with patch("app.services.email_automation.get_email_automation") as mock_auto:
            mock_auto.return_value.delete_rule.return_value = True
            resp = client.delete("/api/email/rules/rule-abc123")
            assert resp.status_code == 200
            assert resp.json()["success"] is True

    def test_delete_rule_not_found(self, client):
        with patch("app.services.email_automation.get_email_automation") as mock_auto:
            mock_auto.return_value.delete_rule.return_value = False
            resp = client.delete("/api/email/rules/nonexistent")
            assert resp.status_code == 404


class TestTodosAPI:
    def test_get_todos_empty(self, client):
        with patch("app.services.todo_store.get_todo_store") as mock_store:
            mock_store.return_value.get_all.return_value = []
            mock_store.return_value.get_counts.return_value = {"new": 0, "read": 0, "done": 0, "total": 0}
            resp = client.get("/api/email/todos")
            assert resp.status_code == 200
            data = resp.json()
            assert data["todos"] == []
            assert data["counts"]["total"] == 0

    def test_update_todo_status(self, client):
        with patch("app.services.todo_store.get_todo_store") as mock_store:
            mock_store.return_value.update_status.return_value = True
            resp = client.put("/api/email/todos/todo-123/status", json={"status": "done"})
            assert resp.status_code == 200
            assert resp.json()["success"] is True

    def test_update_todo_status_not_found(self, client):
        with patch("app.services.todo_store.get_todo_store") as mock_store:
            mock_store.return_value.update_status.return_value = False
            resp = client.put("/api/email/todos/nonexistent/status", json={"status": "done"})
            assert resp.status_code == 404

    def test_delete_todo(self, client):
        with patch("app.services.todo_store.get_todo_store") as mock_store:
            mock_store.return_value.delete.return_value = True
            resp = client.delete("/api/email/todos/todo-123")
            assert resp.status_code == 200
            assert resp.json()["success"] is True


class TestAutomationAPI:
    def test_get_status(self, client):
        with patch("app.services.email_automation.get_email_automation") as mock_auto:
            mock_auto.return_value.get_status.return_value = {
                "running": False,
                "last_poll": None,
                "polling_interval_minutes": 5,
                "rules_count": 0,
                "active_rules": 0,
            }
            resp = client.get("/api/email/automation/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["running"] is False

    def test_search_disabled(self, client):
        with patch("app.core.config.settings") as mock:
            mock.email.enabled = False
            resp = client.post("/api/email/search", json={"query": "test"})
            assert resp.status_code == 400

    def test_draft_disabled(self, client):
        with patch("app.core.config.settings") as mock:
            mock.email.enabled = False
            resp = client.post("/api/email/draft", json={
                "to": "a@b.com", "subject": "S", "body": "B"
            })
            assert resp.status_code == 400
