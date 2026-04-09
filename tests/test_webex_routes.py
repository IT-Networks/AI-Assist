"""Tests für app.api.routes.webex — API-Endpoint-Tests."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """TestClient."""
    from main import app
    return TestClient(app)


class TestConnectionTest:
    def test_success(self, client):
        with patch("app.services.webex_client.get_webex_client") as mock:
            mock_inst = AsyncMock()
            mock_inst.test_connection = AsyncMock(return_value={
                "success": True,
                "display_name": "Bot User",
                "email": "bot@example.com",
                "org_id": "org-123",
            })
            mock.return_value = mock_inst

            resp = client.post("/api/webex/test")
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            assert data["display_name"] == "Bot User"

    def test_failure(self, client):
        with patch("app.services.webex_client.get_webex_client") as mock:
            mock.return_value.test_connection = AsyncMock(side_effect=Exception("Invalid token"))

            resp = client.post("/api/webex/test")
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is False
            assert "Invalid token" in data["error"]


class TestRoomsAPI:
    def test_list_rooms(self, client):
        with patch("app.services.webex_client.get_webex_client") as mock:
            mock_inst = AsyncMock()
            mock_inst.list_rooms = AsyncMock(return_value=[
                {"id": "r1", "title": "Team Chat", "type": "group",
                 "last_activity": "2026-04-09", "created": "", "is_locked": False},
            ])
            mock.return_value = mock_inst

            resp = client.get("/api/webex/rooms")
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 1
            assert data["rooms"][0]["title"] == "Team Chat"

    def test_list_rooms_with_type_filter(self, client):
        with patch("app.services.webex_client.get_webex_client") as mock:
            mock_inst = AsyncMock()
            mock_inst.list_rooms = AsyncMock(return_value=[])
            mock.return_value = mock_inst

            resp = client.get("/api/webex/rooms?type=direct")
            assert resp.status_code == 200
            mock_inst.list_rooms.assert_called_once_with(room_type="direct")


class TestMessagesAPI:
    def test_get_room_messages(self, client):
        with patch("app.services.webex_client.get_webex_client") as mock:
            mock_inst = AsyncMock()
            mock_inst.get_messages = AsyncMock(return_value=[
                {"id": "m1", "text": "Hallo", "person_email": "a@b.com",
                 "created": "2026-04-09", "room_id": "r1"},
            ])
            mock.return_value = mock_inst

            resp = client.get("/api/webex/rooms/r1/messages?limit=10")
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 1

    def test_get_single_message(self, client):
        with patch("app.services.webex_client.get_webex_client") as mock:
            mock_inst = AsyncMock()
            mock_inst.get_message = AsyncMock(return_value={
                "id": "m1", "text": "Details", "person_email": "a@b.com",
            })
            mock.return_value = mock_inst

            resp = client.get("/api/webex/messages/m1")
            assert resp.status_code == 200
            data = resp.json()
            assert data["message"]["text"] == "Details"


class TestRulesAPI:
    def test_get_rules_empty(self, client):
        with patch("app.services.webex_automation.get_webex_automation") as mock_auto:
            mock_auto.return_value.get_rules.return_value = []
            resp = client.get("/api/webex/rules")
            assert resp.status_code == 200
            assert resp.json()["rules"] == []

    def test_create_rule(self, client):
        from app.models.webex_models import WebexRule

        with patch("app.services.webex_automation.get_webex_automation") as mock_auto:
            mock_rule = WebexRule(name="Test", description="Desc")
            mock_auto.return_value.add_rule.return_value = mock_rule

            resp = client.post("/api/webex/rules", json={
                "name": "Test",
                "description": "Desc",
                "room_filter": "Team",
                "sender_filter": "",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["rule"]["name"] == "Test"

    def test_update_rule(self, client):
        from app.models.webex_models import WebexRule

        with patch("app.services.webex_automation.get_webex_automation") as mock_auto:
            mock_rule = WebexRule(name="Updated", description="Desc")
            mock_auto.return_value.update_rule.return_value = mock_rule

            resp = client.put("/api/webex/rules/wxr-abc", json={
                "name": "Updated",
            })
            assert resp.status_code == 200
            assert resp.json()["rule"]["name"] == "Updated"

    def test_update_rule_not_found(self, client):
        with patch("app.services.webex_automation.get_webex_automation") as mock_auto:
            mock_auto.return_value.update_rule.return_value = None
            resp = client.put("/api/webex/rules/nonexistent", json={"name": "X"})
            assert resp.status_code == 404

    def test_delete_rule(self, client):
        with patch("app.services.webex_automation.get_webex_automation") as mock_auto:
            mock_auto.return_value.delete_rule.return_value = True
            resp = client.delete("/api/webex/rules/wxr-abc")
            assert resp.status_code == 200
            assert resp.json()["success"] is True

    def test_delete_rule_not_found(self, client):
        with patch("app.services.webex_automation.get_webex_automation") as mock_auto:
            mock_auto.return_value.delete_rule.return_value = False
            resp = client.delete("/api/webex/rules/nonexistent")
            assert resp.status_code == 404


class TestAutomationAPI:
    def test_get_status(self, client):
        with patch("app.services.webex_automation.get_webex_automation") as mock_auto:
            mock_auto.return_value.get_status.return_value = {
                "running": False,
                "polling_enabled": False,
                "last_poll": None,
                "polling_interval_minutes": 5,
                "rules_count": 0,
                "active_rules": 0,
            }
            resp = client.get("/api/webex/automation/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["running"] is False

    def test_start(self, client):
        with patch("app.services.webex_automation.get_webex_automation") as mock_auto:
            mock_auto.return_value.start = AsyncMock()
            resp = client.post("/api/webex/automation/start")
            assert resp.status_code == 200
            assert resp.json()["status"] == "running"

    def test_stop(self, client):
        with patch("app.services.webex_automation.get_webex_automation") as mock_auto:
            mock_auto.return_value.stop = AsyncMock()
            resp = client.post("/api/webex/automation/stop")
            assert resp.status_code == 200
            assert resp.json()["status"] == "stopped"


class TestRuleTest:
    def test_rule_test_with_matches(self, client):
        with patch("app.services.webex_automation.get_webex_automation") as mock_auto:
            mock_auto.return_value.test_rule = AsyncMock(return_value=[
                {"text": "Bitte erledigen", "sender": "a@b.com", "room": "Team",
                 "date": "2026-04-09", "todo_text": "Erledigen", "priority": "medium",
                 "todo_created": True},
            ])
            resp = client.post("/api/webex/rules/wxr-abc/test")
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 1
            assert data["created"] == 1

    def test_rule_test_no_matches(self, client):
        with patch("app.services.webex_automation.get_webex_automation") as mock_auto:
            mock_auto.return_value.test_rule = AsyncMock(return_value=[])
            resp = client.post("/api/webex/rules/wxr-abc/test")
            assert resp.status_code == 200
            assert resp.json()["count"] == 0
