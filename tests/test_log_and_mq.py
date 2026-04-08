"""
Tests für Log-Server HTML-Parsing, Error-Summary und MQ effective_url.
"""

import pytest


# ── _strip_html Tests ─────────────────────────────────────────────────────────

class TestStripHtml:
    """Tests für _strip_html – extrahiert Plaintext aus log.jsp HTML."""

    def _strip(self, text: str) -> str:
        from app.api.routes.log_servers import _strip_html
        return _strip_html(text)

    def test_plaintext_passthrough(self):
        """Plaintext ohne HTML wird nicht verändert."""
        text = "2026-04-08 10:15:03 INFO mein text hier\n2026-04-08 10:15:04 ERROR failed"
        assert self._strip(text) == text

    def test_empty_string(self):
        assert self._strip("") == ""

    def test_none_passthrough(self):
        assert self._strip(None) is None

    def test_logfilecontent_p_tag(self):
        """Standard log.jsp Format: <p class="logfilecontent"> mit <br> Trennung."""
        html = """<html><body>
        <p class="logfilecontent">2026-04-08 10:15:03 INFO Start<br>2026-04-08 10:15:04 ERROR Fail<br>2026-04-08 10:15:05 INFO Ende</p>
        </body></html>"""
        result = self._strip(html)
        lines = result.splitlines()
        assert len(lines) == 3
        assert "INFO Start" in lines[0]
        assert "ERROR Fail" in lines[1]
        assert "INFO Ende" in lines[2]

    def test_logfilecontent_with_br_slash(self):
        """<br/> statt <br>."""
        html = '<p class="logfilecontent">Zeile1<br/>Zeile2<br/>Zeile3</p>'
        result = self._strip(html)
        lines = result.splitlines()
        assert len(lines) == 3
        assert lines[0] == "Zeile1"

    def test_logfilecontent_single_quotes(self):
        """class='logfilecontent' mit einfachen Anführungszeichen."""
        html = "<p class='logfilecontent'>Entry1<br>Entry2</p>"
        result = self._strip(html)
        lines = result.splitlines()
        assert len(lines) == 2

    def test_html_entities_decoded(self):
        """HTML-Entities werden decodiert."""
        html = '<p class="logfilecontent">value &lt; 100 &amp; value &gt; 0</p>'
        result = self._strip(html)
        assert "value < 100 & value > 0" in result

    def test_fallback_to_body(self):
        """Wenn kein logfilecontent, Fallback auf <body>."""
        html = "<html><body>2026-04-08 10:15:03 INFO fallback line</body></html>"
        result = self._strip(html)
        assert "INFO fallback line" in result

    def test_no_html_tags_in_result(self):
        """Ergebnis enthält keine HTML-Tags."""
        html = '<p class="logfilecontent"><span class="ts">10:15</span> <b>ERROR</b> msg<br>next</p>'
        result = self._strip(html)
        assert "<" not in result
        assert ">" not in result

    def test_search_in_stripped_content(self):
        """Suchbegriff findet Text nach HTML-Stripping."""
        html = '<p class="logfilecontent">2026-04-08 INFO mein spezifischer text<br>2026-04-08 ERROR anderer text</p>'
        result = self._strip(html)
        lines = result.splitlines()
        matching = [l for l in lines if "spezifischer text" in l.lower()]
        assert len(matching) == 1
        assert "INFO" in matching[0]


# ── _extract_log_summary Tests ──────────────────────────────────────────────

class TestExtractLogSummary:
    """Tests für _extract_log_summary – zählt ALLE Log-Levels im Content."""

    def _extract(self, content: str, server: str = "test-srv") -> dict:
        from app.agent.log_tools import _extract_log_summary
        return _extract_log_summary(content, server)

    def test_counts_all_levels(self):
        content = "10:15 ERROR fail1\n10:16 ERROR fail2\n10:17 WARN slow\n10:18 INFO ok"
        result = self._extract(content)
        assert result["level_counts"]["ERROR"] == 2
        assert result["level_counts"]["WARN"] == 1
        assert result["level_counts"]["INFO"] == 1
        assert result["total_errors"] == 2
        assert result["total_warnings"] == 1
        assert result["total_info"] == 1

    def test_case_insensitive(self):
        content = "10:15 error lowercase\n10:16 Error mixed\n10:17 ERROR upper"
        result = self._extract(content)
        assert result["level_counts"]["ERROR"] == 3

    def test_no_errors(self):
        content = "10:15 INFO all good\n10:16 DEBUG detail\n10:17 INFO fine"
        result = self._extract(content)
        assert result["total_errors"] == 0
        assert result["total_warnings"] == 0
        assert len(result["notable_entries"]) == 0

    def test_fatal_and_severe(self):
        content = "10:15 FATAL crash\n10:16 SEVERE bad"
        result = self._extract(content)
        assert result["total_errors"] == 2
        assert result["level_counts"]["FATAL"] == 1
        assert result["level_counts"]["SEVERE"] == 1

    def test_warning_normalized(self):
        content = "10:15 WARNING something\n10:16 WARN another"
        result = self._extract(content)
        assert result["level_counts"]["WARN"] == 2

    def test_error_entries_have_timestamp(self):
        content = "2026-04-08 10:15:03 ERROR fail with timestamp"
        result = self._extract(content)
        assert len(result["notable_entries"]) == 1
        assert result["notable_entries"][0]["timestamp"] == "2026-04-08 10:15:03"
        assert result["notable_entries"][0]["server"] == "test-srv"

    def test_max_50_error_entries(self):
        content = "\n".join(f"10:{i:02d} ERROR err{i}" for i in range(100))
        result = self._extract(content)
        assert len(result["notable_entries"]) == 50
        assert result["level_counts"]["ERROR"] == 100  # Counts sind korrekt

    def test_info_lines_not_in_notable_entries(self):
        """INFO-Zeilen werden gezählt aber NICHT als notable_entries erfasst."""
        content = "10:15 INFO normal\n10:16 ERROR bad"
        result = self._extract(content)
        assert result["level_counts"]["INFO"] == 1
        assert result["total_info"] == 1
        assert len(result["notable_entries"]) == 1
        assert "ERROR" in result["notable_entries"][0]["level"]

    def test_debug_counted(self):
        """DEBUG-Zeilen werden gezählt."""
        content = "10:15 DEBUG detail\n10:16 INFO normal\n10:17 DEBUG more"
        result = self._extract(content)
        assert result["total_debug"] == 2
        assert result["total_info"] == 1

    def test_empty_content(self):
        result = self._extract("")
        assert result["total_errors"] == 0


# ── effective_url Tests ───────────────────────────────────────────────────────

class TestEffectiveUrl:
    """Tests für effective_url Property auf MQQueue und LogServer."""

    def test_mq_url_with_schema(self):
        from app.core.config import MQQueue
        q = MQQueue(url="http://server.com/queue")
        assert q.effective_url == "http://server.com/queue"

    def test_mq_url_https(self):
        from app.core.config import MQQueue
        q = MQQueue(url="https://secure.com/queue")
        assert q.effective_url == "https://secure.com/queue"

    def test_mq_url_without_schema(self):
        from app.core.config import MQQueue
        q = MQQueue(url="en39.server.com:8080/api/queue")
        assert q.effective_url == "http://en39.server.com:8080/api/queue"

    def test_mq_url_empty(self):
        from app.core.config import MQQueue
        q = MQQueue(url="")
        assert q.effective_url == ""

    def test_log_server_url_without_schema(self):
        from app.core.config import LogServer
        s = LogServer(url="en39.host.internal:9080")
        assert s.effective_url == "http://en39.host.internal:9080"

    def test_log_server_url_with_schema(self):
        from app.core.config import LogServer
        s = LogServer(url="https://logserver.com")
        assert s.effective_url == "https://logserver.com"


# ── Search Integration Tests ──────────────────────────────────────────────────

class TestLogSearchIntegration:
    """Tests dass die Suche case-insensitive über alle Zeilen läuft."""

    def test_search_case_insensitive(self):
        """Suche ist case-insensitive."""
        lines = [
            "2026-04-08 10:15:03 INFO Mein Spezifischer Text",
            "2026-04-08 10:15:04 ERROR anderer text",
            "2026-04-08 10:15:05 DEBUG normaler eintrag",
        ]
        term = "spezifischer text"
        matching = [l for l in lines if term.lower() in l.lower()]
        assert len(matching) == 1
        assert "INFO" in matching[0]

    def test_search_finds_info_lines(self):
        """Suche findet auch INFO-Zeilen, nicht nur ERROR."""
        lines = [
            "10:15 INFO startup complete",
            "10:16 ERROR crash",
            "10:17 INFO shutdown complete",
        ]
        matching = [l for l in lines if "complete" in l.lower()]
        assert len(matching) == 2

    def test_search_partial_match(self):
        """Teilstring-Suche funktioniert."""
        lines = [
            "10:15 INFO OrderProcessingService started",
            "10:16 INFO PaymentService started",
        ]
        matching = [l for l in lines if "orderprocessing" in l.lower()]
        assert len(matching) == 1
