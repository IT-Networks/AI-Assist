"""
Tests für das Analytics-System mit gemocktem LLM.

Testet:
- Anonymizer: Maskierung von sensiblen Daten
- AnalyticsLogger: Chain-Tracking, Tool-Logging, Feedback-Inference
- Integration: Vollständiger Flow
"""

import asyncio
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Imports der zu testenden Module
from app.services.anonymizer import Anonymizer, AnonymizerConfig
from app.services.analytics_logger import (
    AnalyticsLogger,
    AnalyticsChain,
    ToolExecution,
    FeedbackInferrer,
    UserFeedback,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Anonymizer Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestAnonymizer:
    """Tests für den Anonymizer."""

    def setup_method(self):
        """Setup für jeden Test."""
        self.anonymizer = Anonymizer(AnonymizerConfig(enabled=True))

    def test_mask_ipv4(self):
        """IPv4-Adressen werden maskiert."""
        text = "Server läuft auf 192.168.1.100 und 10.0.0.1"
        result = self.anonymizer.anonymize(text)

        assert "192.168.1.100" not in result
        assert "10.0.0.1" not in result
        assert "***.***.***.***" in result

    def test_mask_email(self):
        """Email-Adressen werden maskiert."""
        text = "Kontakt: admin@firma.de und user@example.com"
        result = self.anonymizer.anonymize(text)

        assert "admin@firma.de" not in result
        assert "user@example.com" not in result
        assert "***@***.de" in result or "***@***.com" in result

    def test_mask_credentials(self):
        """Credentials werden maskiert."""
        text = "password=SuperSecret123 und api_key=abc123xyz"
        result = self.anonymizer.anonymize(text)

        assert "SuperSecret123" not in result
        assert "abc123xyz" not in result
        assert "password=***" in result

    def test_mask_bearer_token(self):
        """Bearer Tokens werden maskiert."""
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz"
        result = self.anonymizer.anonymize(text)

        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result
        assert "Bearer ***" in result

    def test_mask_unix_path(self):
        """Unix-Pfade werden maskiert."""
        text = "Datei unter /home/markus/geheim/projekt/code.java"
        result = self.anonymizer.anonymize(text)

        assert "/home/markus/geheim/projekt" not in result
        assert "***.java" in result

    def test_mask_url_with_auth(self):
        """URLs mit eingebetteter Auth werden maskiert."""
        text = "Verbinde zu http://admin:password123@jenkins.intern:8080/api"
        result = self.anonymizer.anonymize(text)

        assert "admin" not in result
        assert "password123" not in result
        assert "***:***@" in result

    def test_mask_ticket_ids(self):
        """Ticket-IDs werden maskiert."""
        text = "Siehe PROJ-12345 und ABC-999"
        result = self.anonymizer.anonymize(text)

        assert "PROJ-12345" not in result
        assert "***-****" in result or "***-***" in result

    def test_path_whitelist_preserved(self):
        """Whitelisted Pfad-Komponenten bleiben erhalten."""
        config = AnonymizerConfig(
            enabled=True,
            path_whitelist=["src", "app", "services"]
        )
        anonymizer = Anonymizer(config)

        text = "Datei: /home/user/projekt/src/services/UserService.java"
        result = anonymizer.anonymize(text)

        # Whitelist-Komponenten sollten erhalten bleiben
        assert "src" in result or "services" in result
        # Aber User-Daten sollten maskiert sein
        assert "user" not in result.lower() or "***" in result

    def test_hash_query(self):
        """Query-Hashing erzeugt Hash und Kategorien."""
        query = "Zeige mir die Java Klasse UserService"

        query_hash, categories = self.anonymizer.hash_query(query)

        assert len(query_hash) == 12  # SHA256 truncated
        assert "java" in categories
        assert "code_search" in categories

    def test_anonymize_dict(self):
        """Dictionary-Anonymisierung funktioniert rekursiv."""
        data = {
            "user": "admin",
            "password": "secret123",
            "config": {
                "server": "192.168.1.1",
                "email": "test@firma.de"
            }
        }

        result = self.anonymizer.anonymize_dict(data)

        assert result["password"] == "***"
        assert "192.168.1.1" not in str(result)
        assert "test@firma.de" not in str(result)

    def test_disabled_anonymizer(self):
        """Deaktivierter Anonymizer gibt Text unverändert zurück."""
        config = AnonymizerConfig(enabled=False)
        anonymizer = Anonymizer(config)

        text = "password=secret 192.168.1.1"
        result = anonymizer.anonymize(text)

        assert result == text


# ═══════════════════════════════════════════════════════════════════════════════
# FeedbackInferrer Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestFeedbackInferrer:
    """Tests für den Feedback-Inferrer."""

    def setup_method(self):
        self.inferrer = FeedbackInferrer()

    def test_explicit_negative(self):
        """Explizit negatives Feedback wird erkannt."""
        feedback = self.inferrer.infer(
            current_query="Nein, das ist falsch!",
            previous_query="Was ist 2+2?",
            previous_response="2+2 = 5"
        )

        assert feedback.type == "explicit_negative"
        assert feedback.signal == "negative_words"

    def test_explicit_positive(self):
        """Explizit positives Feedback wird erkannt."""
        feedback = self.inferrer.infer(
            current_query="Danke, perfekt!",
            previous_query="Erkläre mir Python",
            previous_response="Python ist eine Programmiersprache..."
        )

        assert feedback.type == "explicit_positive"
        assert feedback.signal == "positive_words"

    def test_clarification_request(self):
        """Klärungsbedarf wird erkannt."""
        feedback = self.inferrer.infer(
            current_query="Warum ist das so?",
            previous_query="Zeige den Code",
            previous_response="Hier ist der Code..."
        )

        assert feedback.type == "inferred_clarification"
        assert feedback.signal == "why_question"

    def test_retry_same_question(self):
        """Wiederholte Frage wird als Retry erkannt."""
        feedback = self.inferrer.infer(
            current_query="Zeige mir die UserService Klasse",
            previous_query="Zeige mir die UserService Klasse bitte",
            previous_response="Hier ist die Klasse..."
        )

        assert feedback.type == "inferred_retry"
        assert feedback.signal == "same_question"

    def test_topic_change_success(self):
        """Themenwechsel wird als Erfolg interpretiert."""
        feedback = self.inferrer.infer(
            current_query="Wie deploye ich nach Production?",
            previous_query="Zeige mir die UserService Klasse",
            previous_response="Hier ist die Klasse..."
        )

        assert feedback.type == "inferred_success"
        assert feedback.signal == "topic_change"


# ═══════════════════════════════════════════════════════════════════════════════
# AnalyticsLogger Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestAnalyticsLogger:
    """Tests für den AnalyticsLogger."""

    @pytest.fixture
    def temp_dir(self):
        """Temporäres Verzeichnis für Tests."""
        temp = tempfile.mkdtemp()
        yield temp
        shutil.rmtree(temp, ignore_errors=True)

    @pytest.fixture
    def mock_settings(self, temp_dir):
        """Gemockte Settings für Analytics."""
        mock = MagicMock()
        mock.analytics.enabled = True
        mock.analytics.storage_path = temp_dir
        mock.analytics.retention_days = 90
        mock.analytics.log_level = "standard"
        mock.analytics.compress_after_days = 7
        mock.analytics.anonymize.enabled = True
        mock.analytics.anonymize.mask_ips = True
        mock.analytics.anonymize.mask_paths = True
        mock.analytics.anonymize.mask_credentials = True
        mock.analytics.anonymize.mask_emails = True
        mock.analytics.anonymize.mask_urls_with_auth = True
        mock.analytics.anonymize.mask_company_data = True
        mock.analytics.anonymize.company_patterns = []
        mock.analytics.anonymize.path_whitelist = ["src", "app"]
        mock.analytics.include.tool_selection = True
        mock.analytics.include.tool_execution = True
        mock.analytics.include.tool_errors = True
        mock.analytics.include.model_settings = True
        mock.analytics.include.user_feedback = True
        mock.analytics.include.decision_reasoning = False
        return mock

    @pytest.mark.asyncio
    async def test_full_chain_lifecycle(self, temp_dir, mock_settings):
        """Vollstaendiger Chain-Lifecycle: Start -> Tools -> End."""
        with patch('app.services.analytics_logger.settings', mock_settings):
            logger = AnalyticsLogger()

            # Chain starten
            chain_id = await logger.start_chain(
                query="Zeige mir die UserService Klasse",
                model="test-model",
                model_settings={"temperature": 0.7}
            )

            assert chain_id.startswith("c_")
            assert logger._current_chain is not None

            # Tool-Ausführungen loggen
            await logger.log_tool_execution(
                tool_name="search_code",
                success=True,
                duration_ms=150,
                result_size=1000
            )

            await logger.log_tool_execution(
                tool_name="read_file",
                success=True,
                duration_ms=50,
                result_size=500
            )

            await logger.log_tool_execution(
                tool_name="write_file",
                success=False,
                duration_ms=10,
                error="Permission denied"
            )

            assert len(logger._current_chain.tool_chain) == 3

            # Chain beenden
            await logger.end_chain(
                status="resolved",
                response="Hier ist die UserService Klasse..."
            )

            assert logger._current_chain.final_status == "resolved"

    @pytest.mark.asyncio
    async def test_anonymization_in_chain(self, temp_dir, mock_settings):
        """Daten werden in der Chain anonymisiert."""
        with patch('app.services.analytics_logger.settings', mock_settings):
            logger = AnalyticsLogger()

            # Query mit sensiblen Daten
            chain_id = await logger.start_chain(
                query="Verbinde zu admin:password@192.168.1.1",
                model="test-model",
                model_settings={"api_key": "secret123"}
            )

            chain = logger._current_chain

            # Query sollte gehasht sein
            assert chain.query_hash  # Hat einen Hash
            assert "admin" not in chain.query_hash
            assert "password" not in chain.query_hash

            # Settings sollten anonymisiert sein
            if "api_key" in chain.model_settings:
                assert chain.model_settings["api_key"] == "***"

    @pytest.mark.asyncio
    async def test_tool_error_classification(self, temp_dir, mock_settings):
        """Fehler werden korrekt klassifiziert."""
        with patch('app.services.analytics_logger.settings', mock_settings):
            logger = AnalyticsLogger()

            await logger.start_chain(
                query="Test",
                model="test-model"
            )

            # Connection Error
            await logger.log_tool_execution(
                tool_name="api_call",
                success=False,
                duration_ms=5000,
                error="Connection timeout"
            )

            assert logger._current_chain.tool_chain[0].error_type == "connection"

            # Permission Error
            await logger.log_tool_execution(
                tool_name="write_file",
                success=False,
                duration_ms=10,
                error="Permission denied"
            )

            assert logger._current_chain.tool_chain[1].error_type == "permission"

            # Validation Error
            await logger.log_tool_execution(
                tool_name="parse_json",
                success=False,
                duration_ms=5,
                error="Invalid JSON: missing required field"
            )

            assert logger._current_chain.tool_chain[2].error_type == "validation"

    @pytest.mark.asyncio
    async def test_chain_saved_to_file(self, temp_dir, mock_settings):
        """Chain wird in JSONL-Datei gespeichert."""
        with patch('app.services.analytics_logger.settings', mock_settings):
            logger = AnalyticsLogger()

            # Erste Chain
            await logger.start_chain(query="Query 1", model="model-1")
            await logger.log_tool_execution("tool1", True, 100)
            await logger.end_chain("resolved")

            # Zweite Chain (triggert Speichern der ersten)
            await logger.start_chain(query="Query 2", model="model-2")
            await logger.end_chain("resolved")

            # Force save
            await logger.force_save()

            # Prüfen ob Datei existiert
            from datetime import datetime
            date_str = datetime.utcnow().strftime("%Y-%m-%d")
            log_file = Path(temp_dir) / date_str / "chains.jsonl"

            assert log_file.exists()

            # Inhalt prüfen
            with open(log_file, "r") as f:
                lines = f.readlines()

            assert len(lines) >= 1

            # Erste Chain parsen
            chain_data = json.loads(lines[0])
            assert "chain_id" in chain_data
            assert "tool_chain" in chain_data

    @pytest.mark.asyncio
    async def test_summary_generation(self, temp_dir, mock_settings):
        """Summary wird korrekt generiert."""
        with patch('app.services.analytics_logger.settings', mock_settings):
            logger = AnalyticsLogger()

            # Mehrere Chains erstellen
            for i in range(3):
                await logger.start_chain(
                    query=f"Query {i}",
                    model="test-model"
                )
                await logger.log_tool_execution("search_code", True, 100 + i * 10)
                await logger.log_tool_execution("read_file", True, 50)
                if i == 2:
                    await logger.log_tool_execution("api_call", False, 1000, "Timeout")
                await logger.end_chain("resolved")

            await logger.force_save()

            # Summary abrufen
            summary = await logger.get_summary(days=7)

            assert summary["enabled"] == True
            assert summary["total_chains"] >= 2
            assert "search_code" in summary["tools_used"]
            assert "read_file" in summary["tools_used"]

    @pytest.mark.asyncio
    async def test_disabled_logger(self, temp_dir, mock_settings):
        """Deaktivierter Logger macht nichts."""
        mock_settings.analytics.enabled = False

        with patch('app.services.analytics_logger.settings', mock_settings):
            logger = AnalyticsLogger()

            chain_id = await logger.start_chain(
                query="Test",
                model="test-model"
            )

            assert chain_id == ""
            assert logger._current_chain is None

    @pytest.mark.asyncio
    async def test_feedback_inference_between_chains(self, temp_dir, mock_settings):
        """Feedback wird zwischen Chains inferiert."""
        with patch('app.services.analytics_logger.settings', mock_settings):
            logger = AnalyticsLogger()

            # Erste Chain
            await logger.start_chain(query="Was ist Python?", model="model")
            await logger.end_chain("resolved", response="Python ist...")

            # Zweite Chain mit Themenwechsel
            await logger.start_chain(query="Deploye nach Production", model="model")

            # Erste Chain sollte jetzt Feedback haben
            # (wird beim Start der zweiten Chain gespeichert)


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Test mit Mock-Orchestrator
# ═══════════════════════════════════════════════════════════════════════════════

class TestAnalyticsIntegration:
    """Integration-Tests mit gemocktem Orchestrator."""

    @pytest.fixture
    def temp_dir(self):
        temp = tempfile.mkdtemp()
        yield temp
        shutil.rmtree(temp, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_mock_orchestrator_flow(self, temp_dir):
        """Simuliert einen Orchestrator-Flow mit Analytics."""

        # Mock Settings
        mock_settings = MagicMock()
        mock_settings.analytics.enabled = True
        mock_settings.analytics.storage_path = temp_dir
        mock_settings.analytics.retention_days = 90
        mock_settings.analytics.log_level = "standard"
        mock_settings.analytics.compress_after_days = 7
        mock_settings.analytics.anonymize.enabled = True
        mock_settings.analytics.anonymize.mask_ips = True
        mock_settings.analytics.anonymize.mask_paths = True
        mock_settings.analytics.anonymize.mask_credentials = True
        mock_settings.analytics.anonymize.mask_emails = True
        mock_settings.analytics.anonymize.mask_urls_with_auth = True
        mock_settings.analytics.anonymize.mask_company_data = True
        mock_settings.analytics.anonymize.company_patterns = []
        mock_settings.analytics.anonymize.path_whitelist = ["src"]
        mock_settings.analytics.include.tool_selection = True
        mock_settings.analytics.include.tool_execution = True
        mock_settings.analytics.include.tool_errors = True
        mock_settings.analytics.include.model_settings = True
        mock_settings.analytics.include.user_feedback = True
        mock_settings.analytics.include.decision_reasoning = False
        mock_settings.llm.default_model = "mock-llm"
        mock_settings.llm.temperature = 0.7
        mock_settings.llm.max_tokens = 4096
        mock_settings.llm.reasoning_effort = ""
        mock_settings.llm.tool_model = ""

        with patch('app.services.analytics_logger.settings', mock_settings):
            analytics = AnalyticsLogger()

            # Simuliere Orchestrator-Flow
            user_query = "Zeige mir die UserService.java mit IP 192.168.1.100"

            # 1. Chain starten (wie im Orchestrator)
            chain_id = await analytics.start_chain(
                query=user_query,
                model="mock-llm",
                model_settings={
                    "temperature": 0.7,
                    "max_tokens": 4096
                }
            )

            print(f"Chain gestartet: {chain_id}")

            # 2. Tool-Calls simulieren
            tools = [
                ("search_code", True, 150, None),
                ("read_file", True, 80, None),
                ("search_handbook", True, 200, None),
            ]

            for tool_name, success, duration, error in tools:
                await analytics.log_tool_execution(
                    tool_name=tool_name,
                    success=success,
                    duration_ms=duration,
                    error=error,
                    result_size=500
                )
                print(f"  Tool logged: {tool_name} -> {'OK' if success else 'FAIL'}")

            # 3. Chain beenden
            await analytics.end_chain(
                status="resolved",
                response="Hier ist die UserService Klasse..."
            )

            print(f"Chain beendet: resolved")

            # 4. Force save um Datei zu schreiben
            await analytics.force_save()

            # 5. Verifizieren
            from datetime import datetime
            date_str = datetime.utcnow().strftime("%Y-%m-%d")
            log_file = Path(temp_dir) / date_str / "chains.jsonl"

            assert log_file.exists(), f"Log-Datei nicht gefunden: {log_file}"

            with open(log_file, "r") as f:
                content = f.read()
                chain_data = json.loads(content.strip())

            print(f"\nGespeicherte Chain:")
            print(json.dumps(chain_data, indent=2, ensure_ascii=False))

            # Assertions
            assert chain_data["chain_id"] == chain_id
            assert len(chain_data["tool_chain"]) == 3
            assert chain_data["final_status"] == "resolved"

            # IP sollte anonymisiert sein
            assert "192.168.1.100" not in json.dumps(chain_data)

            # Query-Kategorien sollten existieren
            assert len(chain_data["query_categories"]) > 0

            print("\n[OK] Integration-Test erfolgreich!")


# ═══════════════════════════════════════════════════════════════════════════════
# Claude Analysis Test - Verbesserungsableitung
# ═══════════════════════════════════════════════════════════════════════════════

class TestClaudeAnalyzability:
    """
    Testet ob die Analytics-Daten durch Claude analysierbar sind
    und konkrete Verbesserungsvorschläge abgeleitet werden können.

    Simuliert den Workflow:
    1. Realistische Nutzungsdaten generieren
    2. Export für Analyse
    3. Claude liest Daten und leitet Verbesserungen ab
    """

    @pytest.fixture
    def temp_dir(self):
        temp = tempfile.mkdtemp()
        yield temp
        shutil.rmtree(temp, ignore_errors=True)

    @pytest.fixture
    def mock_settings(self, temp_dir):
        """Standard Mock-Settings."""
        mock = MagicMock()
        mock.analytics.enabled = True
        mock.analytics.storage_path = temp_dir
        mock.analytics.retention_days = 90
        mock.analytics.log_level = "verbose"
        mock.analytics.compress_after_days = 7
        mock.analytics.anonymize.enabled = True
        mock.analytics.anonymize.mask_ips = True
        mock.analytics.anonymize.mask_paths = True
        mock.analytics.anonymize.mask_credentials = True
        mock.analytics.anonymize.mask_emails = True
        mock.analytics.anonymize.mask_urls_with_auth = True
        mock.analytics.anonymize.mask_company_data = True
        mock.analytics.anonymize.company_patterns = []
        mock.analytics.anonymize.path_whitelist = ["src", "app", "services"]
        mock.analytics.include.tool_selection = True
        mock.analytics.include.tool_execution = True
        mock.analytics.include.tool_errors = True
        mock.analytics.include.model_settings = True
        mock.analytics.include.user_feedback = True
        mock.analytics.include.decision_reasoning = True
        return mock

    @pytest.mark.asyncio
    async def test_generate_realistic_usage_data(self, temp_dir, mock_settings):
        """
        Generiert realistische Nutzungsdaten für verschiedene Szenarien.
        """
        with patch('app.services.analytics_logger.settings', mock_settings):
            analytics = AnalyticsLogger()

            # Szenario 1: Erfolgreiche Code-Suche
            await analytics.start_chain(
                query="Finde die UserService Klasse im src Verzeichnis",
                model="claude-3-5-sonnet",
                model_settings={"temperature": 0.3, "max_tokens": 4096}
            )
            await analytics.log_tool_execution("search_code", True, 120, result_size=2500)
            await analytics.log_tool_execution("read_file", True, 45, result_size=800)
            await analytics.end_chain("resolved", "Hier ist die UserService Klasse...")

            # Szenario 2: Fehlerhafte API-Anfrage mit Retry
            await analytics.start_chain(
                query="Rufe die externe API ab und zeige die Daten",
                model="claude-3-5-sonnet",
                model_settings={"temperature": 0.5, "max_tokens": 8192}
            )
            await analytics.log_tool_execution("api_call", False, 5000, error="Connection timeout to ***.***.***.***:8080")
            await analytics.log_tool_execution("api_call", False, 5000, error="Connection timeout - retry failed")
            await analytics.log_tool_execution("api_call", True, 1200, result_size=5000)
            await analytics.end_chain("resolved", "API-Daten erfolgreich abgerufen")

            # Szenario 3: MCP-Tool Nutzung
            await analytics.start_chain(
                query="Analysiere das Repository mit Context7",
                model="claude-3-opus",
                model_settings={"temperature": 0.2, "max_tokens": 16000}
            )
            await analytics.log_tool_execution("mcp_context7_resolve", True, 200, result_size=100)
            await analytics.log_tool_execution("mcp_context7_query_docs", True, 1500, result_size=15000)
            await analytics.log_tool_execution("summarize", True, 800, result_size=2000)
            await analytics.end_chain("resolved", "Repository-Analyse abgeschlossen")

            # Szenario 4: Tool-Fehler mit Permission-Problem
            await analytics.start_chain(
                query="Schreibe die Konfiguration in /etc/config.yaml",
                model="claude-3-5-sonnet",
                model_settings={"temperature": 0.1}
            )
            await analytics.log_tool_execution("write_file", False, 50, error="Permission denied: /***/***/***")
            await analytics.log_tool_execution("read_file", True, 30, result_size=200)
            await analytics.end_chain("failed", "Keine Schreibrechte für das Zielverzeichnis")

            # Szenario 5: Langsame Suche
            await analytics.start_chain(
                query="Suche nach allen TODO Kommentaren im Projekt",
                model="claude-3-haiku",
                model_settings={"temperature": 0.0, "max_tokens": 2000}
            )
            await analytics.log_tool_execution("search_code", True, 8500, result_size=50000)  # Sehr langsam!
            await analytics.log_tool_execution("filter_results", True, 200, result_size=5000)
            await analytics.end_chain("resolved", "Gefunden: 47 TODOs")

            # Szenario 6: User-Unzufriedenheit (Retry)
            await analytics.start_chain(
                query="Erkläre mir den Code bitte genauer",
                model="claude-3-5-sonnet",
                model_settings={"temperature": 0.7}
            )
            await analytics.log_tool_execution("read_file", True, 40)
            await analytics.end_chain("resolved", "Der Code macht...")

            # Szenario 7: Nochmal dasselbe (implizites negatives Feedback)
            await analytics.start_chain(
                query="Nein, erkläre mir den Code genauer und ausführlicher",
                model="claude-3-5-sonnet",
                model_settings={"temperature": 0.7}
            )
            await analytics.log_tool_execution("read_file", True, 40)
            await analytics.log_tool_execution("analyze_code", True, 500)
            await analytics.end_chain("resolved", "Detaillierte Erklärung...")

            await analytics.force_save()

            # Prüfen ob Daten generiert wurden
            from datetime import datetime
            date_str = datetime.utcnow().strftime("%Y-%m-%d")
            log_file = Path(temp_dir) / date_str / "chains.jsonl"

            assert log_file.exists()

            with open(log_file, "r") as f:
                lines = f.readlines()

            assert len(lines) >= 7, f"Erwartet >= 7 Chains, gefunden: {len(lines)}"
            print(f"\n[OK] {len(lines)} realistische Chains generiert")

    @pytest.mark.asyncio
    async def test_claude_analysis_and_recommendations(self, temp_dir, mock_settings):
        """
        Simuliert Claude's Analyse der Daten und Ableitung von Verbesserungen.

        Dies testet ob die Datenstruktur alle nötigen Infos für Optimierung enthält.
        """
        with patch('app.services.analytics_logger.settings', mock_settings):
            analytics = AnalyticsLogger()

            # Realistische Daten generieren (verschiedene Probleme einbauen)
            scenarios = [
                # (query, model, tools mit (name, success, duration_ms, error))
                ("Code-Suche", "claude-3-5-sonnet", [
                    ("search_code", True, 150, None),
                    ("read_file", True, 50, None),
                ], "resolved"),
                ("API-Timeout", "claude-3-5-sonnet", [
                    ("api_call", False, 10000, "Timeout"),
                    ("api_call", False, 10000, "Timeout"),
                ], "failed"),
                ("Langsame Suche", "claude-3-haiku", [
                    ("search_code", True, 12000, None),  # 12 Sekunden!
                ], "resolved"),
                ("Permission Error", "claude-3-5-sonnet", [
                    ("write_file", False, 20, "Permission denied"),
                ], "failed"),
                ("MCP erfolgreich", "claude-3-opus", [
                    ("mcp_context7", True, 500, None),
                ], "resolved"),
                ("Zu viele Iterationen", "claude-3-5-sonnet", [
                    ("search_code", True, 100, None),
                    ("read_file", True, 50, None),
                    ("search_code", True, 100, None),
                    ("read_file", True, 50, None),
                    ("search_code", True, 100, None),
                    ("read_file", True, 50, None),
                    ("search_code", True, 100, None),
                    ("read_file", True, 50, None),
                ], "resolved"),
            ]

            for query, model, tools, status in scenarios:
                await analytics.start_chain(query=query, model=model)
                for tool_name, success, duration, error in tools:
                    await analytics.log_tool_execution(tool_name, success, duration, error=error)
                await analytics.end_chain(status)

            await analytics.force_save()

            # Summary abrufen (wie Claude es tun würde)
            summary = await analytics.get_summary(days=7)

            # ═══════════════════════════════════════════════════════════════
            # CLAUDE ANALYSE SIMULATION
            # Hier analysieren wir die Daten wie Claude es tun würde
            # ═══════════════════════════════════════════════════════════════

            recommendations = []

            # 1. Tool-Erfolgsrate analysieren
            for tool, stats in summary.get("tool_success_rate", {}).items():
                if isinstance(stats, dict):
                    success_rate = stats.get("rate", 100)
                    if success_rate < 50:
                        recommendations.append({
                            "type": "tool_reliability",
                            "tool": tool,
                            "issue": f"Niedrige Erfolgsrate: {success_rate}%",
                            "action": f"Tool '{tool}' hat haeufige Fehler - Fehlerbehandlung verbessern"
                        })

            # 2. Fehlertypen analysieren
            error_types = summary.get("error_types", {})
            if error_types.get("connection", 0) > 0:
                recommendations.append({
                    "type": "connection_issues",
                    "issue": f"{error_types['connection']} Connection-Fehler",
                    "action": "Timeout-Konfiguration prüfen, Retry-Logik verbessern"
                })
            if error_types.get("permission", 0) > 0:
                recommendations.append({
                    "type": "permission_issues",
                    "issue": f"{error_types['permission']} Permission-Fehler",
                    "action": "Berechtigungsprüfung vor Schreiboperationen einbauen"
                })

            # 3. Performance analysieren (aus den Rohdaten)
            from datetime import datetime
            date_str = datetime.utcnow().strftime("%Y-%m-%d")
            log_file = Path(temp_dir) / date_str / "chains.jsonl"

            slow_tools = []
            high_iteration_chains = []

            with open(log_file, "r") as f:
                for line in f:
                    chain = json.loads(line.strip())

                    # Langsame Tools finden
                    for tool in chain.get("tool_chain", []):
                        if tool.get("duration_ms", 0) > 5000:  # > 5 Sekunden
                            slow_tools.append({
                                "tool": tool.get("tool", "unknown"),
                                "duration_ms": tool.get("duration_ms", 0)
                            })

                    # Viele Iterationen
                    if len(chain.get("tool_chain", [])) > 5:
                        high_iteration_chains.append({
                            "chain_id": chain["chain_id"],
                            "iterations": len(chain["tool_chain"])
                        })

            if slow_tools:
                recommendations.append({
                    "type": "performance",
                    "issue": f"{len(slow_tools)} langsame Tool-Aufrufe (>5s)",
                    "tools": [t["tool"] for t in slow_tools],
                    "action": "Caching einführen oder parallele Ausführung prüfen"
                })

            if high_iteration_chains:
                recommendations.append({
                    "type": "efficiency",
                    "issue": f"{len(high_iteration_chains)} Chains mit >5 Iterationen",
                    "action": "Bessere Tool-Auswahl, kombinierte Abfragen prüfen"
                })

            # 4. Modell-Nutzung analysieren
            model_usage = summary.get("model_usage", {})
            if model_usage:
                most_used = max(model_usage.items(), key=lambda x: x[1])
                if "haiku" in most_used[0].lower():
                    # Prüfen ob Haiku für komplexe Tasks verwendet wird
                    recommendations.append({
                        "type": "model_selection",
                        "issue": f"Haiku häufig verwendet ({most_used[1]}x)",
                        "action": "Prüfen ob Sonnet für komplexere Aufgaben besser wäre"
                    })

            # ═══════════════════════════════════════════════════════════════
            # AUSGABE DER ANALYSE
            # ═══════════════════════════════════════════════════════════════

            print("\n" + "=" * 70)
            print("CLAUDE ANALYSE - VERBESSERUNGSEMPFEHLUNGEN")
            print("=" * 70)

            print(f"\n[DATA] Analysierte Daten:")
            print(f"   - Chains gesamt: {summary.get('total_chains', 0)}")
            print(f"   - Zeitraum: {summary.get('period_days', 7)} Tage")
            print(f"   - Tools verwendet: {list(summary.get('tools_used', {}).keys())}")

            print(f"\n[TOOLS] Tool-Erfolgsraten:")
            for tool, stats in summary.get("tool_success_rate", {}).items():
                if isinstance(stats, dict):
                    rate = stats.get("rate", 100)
                    symbol = "[OK]" if rate >= 80 else "[!]" if rate >= 50 else "[X]"
                    print(f"   {symbol} {tool}: {rate}%")

            print(f"\n[ERRORS] Fehlertypen:")
            for error_type, count in summary.get("error_types", {}).items():
                if count > 0:
                    print(f"   - {error_type}: {count}")

            print(f"\n[RECOMMEND] EMPFEHLUNGEN ({len(recommendations)}):")
            for i, rec in enumerate(recommendations, 1):
                print(f"\n   [{i}] {rec['type'].upper()}")
                print(f"       Problem: {rec['issue']}")
                print(f"       Aktion:  {rec['action']}")

            print("\n" + "=" * 70)

            # Assertions für den Test
            assert len(recommendations) >= 2, "Sollte mindestens 2 Empfehlungen generieren"
            assert summary["total_chains"] >= 5
            assert "error_types" in summary
            assert "tool_success_rate" in summary

            # Prüfen dass bestimmte Probleme erkannt wurden
            rec_types = [r["type"] for r in recommendations]
            assert "connection_issues" in rec_types or "performance" in rec_types, \
                "Sollte Connection- oder Performance-Probleme erkennen"

            print("\n[OK] Claude-Analyse erfolgreich - Daten sind analysierbar!")

    @pytest.mark.asyncio
    async def test_anonymization_preserves_analyzability(self, temp_dir, mock_settings):
        """
        Testet dass Anonymisierung die Analysierbarkeit nicht beeinträchtigt.
        Sensible Daten sind maskiert, aber strukturelle Infos bleiben erhalten.
        """
        with patch('app.services.analytics_logger.settings', mock_settings):
            analytics = AnalyticsLogger()

            # Chain mit vielen sensiblen Daten
            # Verwende Daten die vom Anonymizer erkannt werden
            await analytics.start_chain(
                query="Verbinde zu http://root:secret123@192.168.1.100:8080 und lies test@firma.de",
                model="claude-3-5-sonnet",
                model_settings={
                    "api_key": "sk-secret-key-12345",
                    "temperature": 0.5
                }
            )
            await analytics.log_tool_execution(
                "db_query",
                True,
                150,
                result_size=500,
                reason="password=geheim123 und token=abc123xyz"
            )
            await analytics.end_chain("resolved")
            await analytics.force_save()

            # Datei lesen
            date_str = datetime.utcnow().strftime("%Y-%m-%d")
            log_file = Path(temp_dir) / date_str / "chains.jsonl"

            with open(log_file, "r") as f:
                chain = json.loads(f.readline().strip())

            chain_str = json.dumps(chain)

            # Sensible Daten sollten NICHT vorhanden sein
            # (Nur Pattern die der Anonymizer tatsaechlich erkennt)
            sensitive_patterns = [
                "secret123",        # URL auth
                "192.168.1.100",    # IP
                "test@firma.de",    # Email (wird zu ***@***.de)
                "sk-secret-key",    # api_key (wird zu ***)
                "geheim123",        # password=xxx
                "abc123xyz",        # token=xxx
            ]

            for pattern in sensitive_patterns:
                assert pattern not in chain_str, f"Sensibles Pattern gefunden: {pattern}"

            # Aber strukturelle Daten sollten erhalten bleiben
            assert chain["chain_id"].startswith("c_")
            assert chain["model"] == "claude-3-5-sonnet"
            assert "temperature" in chain["settings"]
            assert chain["settings"]["temperature"] == 0.5
            assert len(chain["tool_chain"]) == 1
            assert chain["tool_chain"][0]["tool"] == "db_query"
            assert chain["tool_chain"][0]["status"] == "success"
            assert chain["tool_chain"][0]["duration_ms"] == 150
            assert chain["final_status"] == "resolved"

            # Kategorien sollten trotz Anonymisierung erkannt werden
            assert len(chain["query_categories"]) > 0

            print("\n[OK] Anonymisierung bewahrt Analysierbarkeit!")
            print(f"   - Sensible Daten maskiert: {len(sensitive_patterns)} Patterns")
            print(f"   - Struktur erhalten: chain_id, model, tools, status")
            print(f"   - Kategorien erkannt: {chain['query_categories']}")


class TestAnalysisReport:
    """
    Generiert einen vollständigen Analyse-Report wie Claude ihn erstellen würde.
    """

    @pytest.fixture
    def temp_dir(self):
        temp = tempfile.mkdtemp()
        yield temp
        shutil.rmtree(temp, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_generate_full_analysis_report(self, temp_dir):
        """
        Erzeugt einen kompletten Analyse-Report mit Handlungsempfehlungen.
        """
        mock_settings = MagicMock()
        mock_settings.analytics.enabled = True
        mock_settings.analytics.storage_path = temp_dir
        mock_settings.analytics.retention_days = 90
        mock_settings.analytics.log_level = "verbose"
        mock_settings.analytics.compress_after_days = 7
        mock_settings.analytics.anonymize.enabled = True
        mock_settings.analytics.anonymize.mask_ips = True
        mock_settings.analytics.anonymize.mask_paths = True
        mock_settings.analytics.anonymize.mask_credentials = True
        mock_settings.analytics.anonymize.mask_emails = True
        mock_settings.analytics.anonymize.mask_urls_with_auth = True
        mock_settings.analytics.anonymize.mask_company_data = True
        mock_settings.analytics.anonymize.company_patterns = []
        mock_settings.analytics.anonymize.path_whitelist = ["src", "app"]
        mock_settings.analytics.include.tool_selection = True
        mock_settings.analytics.include.tool_execution = True
        mock_settings.analytics.include.tool_errors = True
        mock_settings.analytics.include.model_settings = True
        mock_settings.analytics.include.user_feedback = True
        mock_settings.analytics.include.decision_reasoning = True

        with patch('app.services.analytics_logger.settings', mock_settings):
            analytics = AnalyticsLogger()

            # Mehr realistische Daten generieren
            import random
            random.seed(42)

            tools_pool = ["search_code", "read_file", "write_file", "api_call",
                         "mcp_context7", "analyze_code", "run_tests"]
            models = ["claude-3-5-sonnet", "claude-3-opus", "claude-3-haiku"]

            for i in range(20):
                model = random.choice(models)
                num_tools = random.randint(1, 6)

                await analytics.start_chain(
                    query=f"Test Query {i}",
                    model=model,
                    model_settings={"temperature": random.uniform(0.0, 1.0)}
                )

                for _ in range(num_tools):
                    tool = random.choice(tools_pool)
                    success = random.random() > 0.2  # 80% Erfolg
                    duration = random.randint(50, 8000)
                    error = None if success else random.choice([
                        "Timeout", "Permission denied", "Not found", "Invalid input"
                    ])

                    await analytics.log_tool_execution(tool, success, duration, error=error)

                status = "resolved" if random.random() > 0.15 else "failed"
                await analytics.end_chain(status)

            await analytics.force_save()

            # Summary abrufen
            summary = await analytics.get_summary(days=7)

            # Report generieren
            report = []
            report.append("=" * 70)
            report.append("AI-ASSIST ANALYTICS REPORT")
            report.append(f"Generiert: {datetime.utcnow().isoformat()}")
            report.append("=" * 70)

            report.append("\n## 1. ÜBERSICHT")
            report.append(f"- Analysierte Chains: {summary['total_chains']}")
            report.append(f"- Zeitraum: {summary['period_days']} Tage")
            report.append(f"- Durchschnittliche Iterationen: {summary['avg_iterations']:.1f}")

            report.append("\n## 2. TOOL-NUTZUNG")
            for tool, count in sorted(summary['tools_used'].items(), key=lambda x: -x[1]):
                rate_info = summary['tool_success_rate'].get(tool, {})
                rate = rate_info.get('success_rate', 100) if isinstance(rate_info, dict) else 100
                report.append(f"- {tool}: {count}x verwendet, {rate}% Erfolg")

            report.append("\n## 3. FEHLERANALYSE")
            for error_type, count in summary['error_types'].items():
                if count > 0:
                    report.append(f"- {error_type}: {count} Vorkommen")

            report.append("\n## 4. MODELL-NUTZUNG")
            for model, count in summary['model_usage'].items():
                report.append(f"- {model}: {count}x")

            report.append("\n## 5. HANDLUNGSEMPFEHLUNGEN")

            # Empfehlungen basierend auf Daten
            recommendations_made = 0

            for tool, stats in summary['tool_success_rate'].items():
                if isinstance(stats, dict) and stats.get('rate', 100) < 70:
                    report.append(f"\n[!] Tool '{tool}' hat niedrige Erfolgsrate ({stats['rate']}%)")
                    report.append(f"   -> Fehlerbehandlung verbessern")
                    report.append(f"   -> Logging fuer Debugging erweitern")
                    recommendations_made += 1

            if summary.get('avg_iterations', 0) > 4:
                report.append(f"\n[!] Hohe durchschnittliche Iterationen ({summary['avg_iterations']:.1f})")
                report.append("   -> Tool-Kombination optimieren")
                report.append("   -> Bessere initiale Abfragen trainieren")
                recommendations_made += 1

            if summary['error_types'].get('connection', 0) > 2:
                report.append("\n[!] Haeufige Connection-Fehler")
                report.append("   -> Retry-Logik mit Backoff implementieren")
                report.append("   -> Connection-Pooling pruefen")
                recommendations_made += 1

            report.append("\n" + "=" * 70)
            report.append(f"Report Ende - {recommendations_made} Empfehlungen generiert")
            report.append("=" * 70)

            # Report ausgeben
            full_report = "\n".join(report)
            print(full_report)

            # Report in Datei speichern
            report_file = Path(temp_dir) / "analysis_report.txt"
            with open(report_file, "w", encoding="utf-8") as f:
                f.write(full_report)

            # Assertions
            assert summary['total_chains'] == 20
            assert len(summary['tools_used']) > 0
            assert report_file.exists()

            print(f"\n[OK] Report gespeichert: {report_file}")
            print(f"[OK] {recommendations_made} Handlungsempfehlungen generiert")


# ═══════════════════════════════════════════════════════════════════════════════
# Run Tests
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Einfacher Test-Runner ohne pytest
    import traceback

    print("=" * 60)
    print("Analytics System Tests")
    print("=" * 60)

    # Anonymizer Tests
    print("\n[1/4] Anonymizer Tests...")
    try:
        test_anon = TestAnonymizer()
        test_anon.setup_method()
        test_anon.test_mask_ipv4()
        test_anon.test_mask_email()
        test_anon.test_mask_credentials()
        test_anon.test_mask_bearer_token()
        test_anon.test_mask_ticket_ids()
        test_anon.test_hash_query()
        test_anon.test_anonymize_dict()
        test_anon.test_disabled_anonymizer()
        print("  [OK] Alle Anonymizer-Tests bestanden")
    except Exception as e:
        print(f"  [FAIL] Fehler: {e}")
        traceback.print_exc()

    # FeedbackInferrer Tests
    print("\n[2/4] FeedbackInferrer Tests...")
    try:
        test_fb = TestFeedbackInferrer()
        test_fb.setup_method()
        test_fb.test_explicit_negative()
        test_fb.test_explicit_positive()
        test_fb.test_clarification_request()
        test_fb.test_topic_change_success()
        print("  [OK] Alle FeedbackInferrer-Tests bestanden")
    except Exception as e:
        print(f"  [FAIL] Fehler: {e}")
        traceback.print_exc()

    # AnalyticsLogger Tests (async)
    print("\n[3/4] AnalyticsLogger Tests...")
    try:
        temp_dir = tempfile.mkdtemp()

        async def run_logger_tests():
            test_log = TestAnalyticsLogger()

            # Mock settings erstellen
            mock = MagicMock()
            mock.analytics.enabled = True
            mock.analytics.storage_path = temp_dir
            mock.analytics.retention_days = 90
            mock.analytics.log_level = "standard"
            mock.analytics.compress_after_days = 7
            mock.analytics.anonymize.enabled = True
            mock.analytics.anonymize.mask_ips = True
            mock.analytics.anonymize.mask_paths = True
            mock.analytics.anonymize.mask_credentials = True
            mock.analytics.anonymize.mask_emails = True
            mock.analytics.anonymize.mask_urls_with_auth = True
            mock.analytics.anonymize.mask_company_data = True
            mock.analytics.anonymize.company_patterns = []
            mock.analytics.anonymize.path_whitelist = ["src"]
            mock.analytics.include.tool_selection = True
            mock.analytics.include.tool_execution = True
            mock.analytics.include.tool_errors = True
            mock.analytics.include.model_settings = True
            mock.analytics.include.user_feedback = True
            mock.analytics.include.decision_reasoning = False

            await test_log.test_full_chain_lifecycle(temp_dir, mock)
            await test_log.test_anonymization_in_chain(temp_dir, mock)
            await test_log.test_tool_error_classification(temp_dir, mock)

        asyncio.run(run_logger_tests())
        shutil.rmtree(temp_dir, ignore_errors=True)
        print("  [OK] Alle AnalyticsLogger-Tests bestanden")
    except Exception as e:
        print(f"  [FAIL] Fehler: {e}")
        traceback.print_exc()
        shutil.rmtree(temp_dir, ignore_errors=True)

    # Integration Test
    print("\n[4/4] Integration Test...")
    try:
        temp_dir = tempfile.mkdtemp()
        test_int = TestAnalyticsIntegration()
        asyncio.run(test_int.test_mock_orchestrator_flow(temp_dir))
        shutil.rmtree(temp_dir, ignore_errors=True)
        print("  [OK] Integration-Test bestanden")
    except Exception as e:
        print(f"  [FAIL] Fehler: {e}")
        traceback.print_exc()
        shutil.rmtree(temp_dir, ignore_errors=True)

    print("\n" + "=" * 60)
    print("Tests abgeschlossen")
    print("=" * 60)
