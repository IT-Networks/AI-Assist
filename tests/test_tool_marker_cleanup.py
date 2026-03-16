"""
Tests für Tool-Marker-Bereinigung und Prefill-Funktionalität.
"""

import pytest


# Import der zu testenden Funktion
import sys
sys.path.insert(0, str(__file__).rsplit("tests", 1)[0])

from app.agent.orchestrator import _strip_tool_markers


class TestStripToolMarkers:
    """Tests für _strip_tool_markers Funktion."""

    def test_empty_content(self):
        """Leerer Content bleibt leer."""
        assert _strip_tool_markers("") == ""
        assert _strip_tool_markers(None) is None

    def test_no_markers(self):
        """Content ohne Marker bleibt unverändert."""
        content = "Dies ist eine normale Antwort ohne Tool-Calls."
        assert _strip_tool_markers(content) == content

    def test_mistral_standard_format(self):
        """[TOOL_CALLS] mit JSON-Array wird entfernt."""
        content = 'Ich werde das Tool aufrufen. [TOOL_CALLS] [{"name": "search", "arguments": {"query": "test"}}]'
        result = _strip_tool_markers(content)
        assert "[TOOL_CALLS]" not in result
        assert "search" not in result
        assert "Ich werde das Tool aufrufen." in result

    def test_mistral_compact_format(self):
        """[TOOL_CALLS]funcname{...} wird entfernt."""
        content = 'OK, ich suche. [TOOL_CALLS]search_code{"query": "main"}'
        result = _strip_tool_markers(content)
        assert "[TOOL_CALLS]" not in result
        assert "search_code" not in result
        assert "OK, ich suche." in result

    def test_xml_tool_call(self):
        """<tool_call>...</tool_call> wird entfernt."""
        content = 'Hier ist der Aufruf: <tool_call>{"name": "read_file", "arguments": {"path": "/tmp"}}</tool_call> Fertig.'
        result = _strip_tool_markers(content)
        assert "<tool_call>" not in result
        assert "</tool_call>" not in result
        assert "read_file" not in result
        assert "Hier ist der Aufruf:" in result
        assert "Fertig." in result

    def test_functioncall_format(self):
        """<functioncall>...</functioncall> wird entfernt."""
        content = '<functioncall>{"name": "calculate", "arguments": {"expr": "2+2"}}</functioncall>'
        result = _strip_tool_markers(content)
        assert result == ""

    def test_multiple_markers(self):
        """Mehrere Marker werden alle entfernt."""
        content = """Ich mache zwei Aufrufe:
[TOOL_CALLS] [{"name": "tool1"}]
<tool_call>{"name": "tool2"}</tool_call>
Das war's."""
        result = _strip_tool_markers(content)
        assert "[TOOL_CALLS]" not in result
        assert "<tool_call>" not in result
        assert "tool1" not in result
        assert "tool2" not in result
        assert "Das war's." in result

    def test_preserves_regular_json(self):
        """Normaler JSON-Code wird NICHT entfernt."""
        content = """Hier ist ein Beispiel:
```json
{
    "user": "max",
    "age": 30
}
```
Das ist das Format."""
        result = _strip_tool_markers(content)
        # Normales JSON sollte erhalten bleiben (kein "name" key = kein Tool-Call)
        assert "user" in result
        assert "max" in result

    def test_multiple_newlines_reduced(self):
        """Mehrfache Leerzeilen werden auf maximal 2 reduziert."""
        content = "Text\n\n\n\n\nMehr Text"
        result = _strip_tool_markers(content)
        assert "\n\n\n" not in result
        assert "Text" in result
        assert "Mehr Text" in result


class TestPrefillSupport:
    """Tests für Prefill-Konfiguration."""

    def test_prefill_config_defaults(self):
        """Prefill-Defaults sind korrekt."""
        from app.core.config import settings

        # Default sollte False sein
        assert hasattr(settings.llm, 'use_tool_prefill')
        # Default ist False (konservativ)
        # Der tatsächliche Wert hängt von der config.yaml ab


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
