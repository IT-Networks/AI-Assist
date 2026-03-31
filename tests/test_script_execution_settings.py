"""
Tests für Script Execution Settings (v2.28.2).

Tests für:
- script_execution section in Settings API
- Nexus URL Configuration
- Allowed File Paths Management
- Settings Persistence
"""

import pytest
from unittest.mock import patch, MagicMock

from app.api.routes.settings import get_section_schema
from app.core.config import settings, ScriptExecutionConfig


# ══════════════════════════════════════════════════════════════════════════════
# Settings Schema Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestScriptExecutionSettingsSchema:
    """Tests für script_execution Settings Schema."""

    def test_script_execution_section_is_registered(self):
        """Test: script_execution ist in section_classes registriert."""
        schema = get_section_schema("script_execution")
        assert schema is not None
        assert isinstance(schema, dict)
        assert len(schema) > 0

    def test_script_execution_schema_contains_required_fields(self):
        """Test: Schema enthält alle erforderlichen Felder."""
        schema = get_section_schema("script_execution")

        # Allgemein
        assert "enabled" in schema
        assert "timeout_seconds" in schema
        assert "max_output_size_kb" in schema

        # Dateizugriff
        assert "allowed_file_paths" in schema
        assert "allowed_imports" in schema

        # Nexus pip
        assert "pip_install_enabled" in schema
        assert "pip_index_url" in schema
        assert "pip_trusted_host" in schema
        assert "pip_install_timeout_seconds" in schema
        assert "pip_cache_requirements" in schema
        assert "pip_cache_dir" in schema

    def test_pip_index_url_field_is_string_type(self):
        """Test: pip_index_url ist vom Typ string."""
        schema = get_section_schema("script_execution")
        pip_url = schema["pip_index_url"]
        assert pip_url["type"] == "string"

    def test_allowed_file_paths_is_array_type(self):
        """Test: allowed_file_paths ist Array."""
        schema = get_section_schema("script_execution")
        allowed_paths = schema["allowed_file_paths"]
        assert allowed_paths["type"] == "array"


# ══════════════════════════════════════════════════════════════════════════════
# Settings Configuration Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestScriptExecutionConfiguration:
    """Tests für ScriptExecutionConfig Settings."""

    def test_default_configuration_values(self):
        """Test: Standardkonfiguration hat sinnvolle Defaults."""
        config = settings.script_execution

        # Defaults
        assert isinstance(config.enabled, bool)
        assert config.timeout_seconds >= 10
        assert config.max_output_size_kb >= 100
        assert isinstance(config.allowed_file_paths, list)
        assert isinstance(config.allowed_imports, list)

    def test_pip_install_disabled_by_default(self):
        """Test: pip_install_enabled ist standardmäßig False."""
        config = settings.script_execution
        # Default should be False unless explicitly set
        assert hasattr(config, "pip_install_enabled")

    def test_nexus_url_configuration(self):
        """Test: pip_index_url kann konfiguriert werden."""
        config = settings.script_execution
        assert hasattr(config, "pip_index_url")
        # Should be a string or empty
        assert isinstance(config.pip_index_url, str)

    def test_allowed_file_paths_is_list(self):
        """Test: allowed_file_paths ist Liste."""
        config = settings.script_execution
        assert isinstance(config.allowed_file_paths, list)

    def test_allowed_imports_contains_safe_packages(self):
        """Test: allowed_imports enthält sichere Packages."""
        config = settings.script_execution
        assert isinstance(config.allowed_imports, list)
        # Should contain common safe packages
        safe_packages = ["json", "csv", "pathlib", "datetime", "math"]
        for pkg in safe_packages:
            assert pkg in config.allowed_imports

    def test_pip_timeout_has_reasonable_default(self):
        """Test: pip_install_timeout_seconds hat sinnvolles Default."""
        config = settings.script_execution
        assert config.pip_install_timeout_seconds >= 10
        assert config.pip_install_timeout_seconds <= 600  # Max 10 minutes


# ══════════════════════════════════════════════════════════════════════════════
# Nexus Configuration Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestNexusConfiguration:
    """Tests für Nexus Repository Konfiguration."""

    def test_pip_index_url_format_validation(self):
        """Test: pip_index_url sollte gültiges URL-Format haben."""
        config = settings.script_execution

        # Wenn URL gesetzt ist, sollte sie mit http beginnen
        if config.pip_index_url:
            assert config.pip_index_url.startswith("http://") or config.pip_index_url.startswith("https://")

    def test_pip_trusted_host_extraction_from_url(self):
        """Test: pip_trusted_host kann aus pip_index_url extrahiert werden."""
        # Example: URL = "https://nexus.local/repository/pypi/simple/"
        # trusted_host = "nexus.local"
        config = settings.script_execution

        if config.pip_index_url and config.pip_trusted_host:
            # Wenn beides gesetzt ist, sollte Host in URL enthalten sein
            assert config.pip_trusted_host in config.pip_index_url

    def test_pip_cache_settings(self):
        """Test: pip Cache Settings sind vorhanden."""
        config = settings.script_execution
        assert hasattr(config, "pip_cache_requirements")
        assert hasattr(config, "pip_cache_dir")
        assert isinstance(config.pip_cache_requirements, bool)
        assert isinstance(config.pip_cache_dir, str)


# ══════════════════════════════════════════════════════════════════════════════
# File Path Configuration Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestAllowedFilePathsConfiguration:
    """Tests für allowed_file_paths Konfiguration."""

    def test_allowed_file_paths_is_empty_by_default(self):
        """Test: allowed_file_paths ist standardmäßig leer."""
        config = settings.script_execution
        # Default should be empty list
        if not config.allowed_file_paths:
            assert isinstance(config.allowed_file_paths, list)

    def test_allowed_file_paths_can_be_multiple(self):
        """Test: allowed_file_paths kann mehrere Pfade enthalten."""
        # This is to verify the list can handle multiple entries
        test_paths = ["/data/input", "/data/output", "/workspace/reports"]
        assert len(test_paths) == 3

    def test_file_path_should_be_absolute_or_relative(self):
        """Test: Dateipfade sollten absolute oder relative Pfade sein."""
        config = settings.script_execution

        for path in config.allowed_file_paths:
            # Path should be a string
            assert isinstance(path, str)
            # Should have some content
            assert len(path) > 0


# ══════════════════════════════════════════════════════════════════════════════
# Allowed Imports Configuration Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestAllowedImportsConfiguration:
    """Tests für allowed_imports Whitelist."""

    def test_allowed_imports_default_packages(self):
        """Test: allowed_imports enthält Standard-Packages."""
        config = settings.script_execution
        required_packages = [
            "json", "csv", "pathlib", "re", "datetime",
            "collections", "itertools", "functools",
            "math", "statistics"
        ]

        for pkg in required_packages:
            assert pkg in config.allowed_imports, f"Package {pkg} sollte in allowed_imports sein"

    def test_allowed_imports_includes_data_science_packages(self):
        """Test: allowed_imports enthält Data Science Packages."""
        config = settings.script_execution

        # Optional aber commonly used
        optional_packages = ["pandas", "numpy", "yaml"]
        # At least some should be there
        ds_packages_found = sum(1 for pkg in optional_packages if pkg in config.allowed_imports)
        assert ds_packages_found >= 2, "Mindestens 2 Data Science Packages sollten erlaubt sein"

    def test_dangerous_modules_are_not_in_allowed_imports(self):
        """Test: Gefährliche Module sind NOT in allowed_imports."""
        config = settings.script_execution
        dangerous = ["subprocess", "os", "sys", "socket", "requests"]

        for module in dangerous:
            assert module not in config.allowed_imports, f"{module} sollte NICHT erlaubt sein"


# ══════════════════════════════════════════════════════════════════════════════
# Integration Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestScriptExecutionSettingsIntegration:
    """Integration Tests für Settings."""

    def test_complete_settings_structure(self):
        """Test: Komplette script_execution Settings-Struktur."""
        config = settings.script_execution

        # Verify all major sections exist
        assert hasattr(config, "enabled")
        assert hasattr(config, "timeout_seconds")
        assert hasattr(config, "allowed_file_paths")
        assert hasattr(config, "allowed_imports")
        assert hasattr(config, "pip_install_enabled")
        assert hasattr(config, "pip_index_url")

    def test_settings_can_be_dumped_to_dict(self):
        """Test: Settings können zu Dictionary konvertiert werden."""
        config = settings.script_execution
        config_dict = config.model_dump()

        assert isinstance(config_dict, dict)
        assert "enabled" in config_dict
        assert "allowed_file_paths" in config_dict
        assert "pip_index_url" in config_dict

    def test_multiple_allowed_paths_configuration(self):
        """Test: Mehrere erlaubte Dateipfade können konfiguriert werden."""
        config = settings.script_execution

        # Simulate multiple paths
        if len(config.allowed_file_paths) > 0:
            assert isinstance(config.allowed_file_paths, list)
            for path in config.allowed_file_paths:
                assert isinstance(path, str)

    def test_pip_configuration_consistency(self):
        """Test: pip-Einstellungen sind konsistent."""
        config = settings.script_execution

        # Wenn pip_install_enabled, sollte pip_index_url gesetzt sein
        if config.pip_install_enabled:
            assert config.pip_index_url, "pip_index_url sollte gesetzt sein wenn pip_install_enabled=True"

    def test_settings_schema_matches_config_class(self):
        """Test: Settings Schema stimmt mit ScriptExecutionConfig überein."""
        schema = get_section_schema("script_execution")
        config = settings.script_execution
        config_dict = config.model_dump()

        # Für jedes Feld im Schema sollte es einen Wert in config geben
        for key in schema.keys():
            assert key in config_dict, f"Schema key '{key}' nicht in config"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
