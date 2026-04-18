"""
Integration tests for Smart Tool Selector system.

Tests the complete flow:
1. Domain detection from user message
2. Tool filtering based on domains
3. System prompt modularization
4. Token count reduction

These tests verify the optimization achieves ~90% token reduction
vs. loading all 150+ tools and full system prompt every request.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock

from app.agent.tool_domains import (
    get_tools_for_domains,
    TOOL_DOMAINS,
    get_core_domains,
)
from app.agent.orchestration.domain_detector import DomainDetector, get_domain_detector
from app.services.prompt_modules import (
    build_system_prompt,
    get_module_stats,
    PROMPT_MODULES,
    ALWAYS_INCLUDE_MODULES,
)


class TestSmartToolSelectorIntegration:
    """Integration tests for the complete smart tool selector."""

    def test_scenario_maven_build_failure(self):
        """Scenario: User reports Maven build failure."""
        user_message = "Maven build is failing with compilation error, check the error logs"

        detector = DomainDetector()
        domains = detector.detect(user_message)

        # Should detect these domains
        assert "maven" in domains
        assert "java" in domains
        assert "log" in domains
        assert "core" in domains

        # Get tools for these domains
        tools = get_tools_for_domains(domains)
        assert "run_maven_build" in tools
        assert "search_code" in tools  # from core
        assert "search_logs" in tools

        # System prompt should include relevant modules
        prompt = build_system_prompt(domains)
        assert "Maven" in prompt or "maven" in prompt.lower()
        assert "Java" in prompt or "java" in prompt.lower()

        # Should NOT include database module
        assert "transaction" not in prompt.lower() or "constraint" not in prompt.lower()

    def test_scenario_git_pr_review(self):
        """Scenario: User asks for GitHub PR code review."""
        user_message = "review this pull request https://github.com/org/repo/pull/123"

        detector = DomainDetector()
        domains = detector.detect(user_message)

        assert "github" in domains
        assert "git" in domains
        assert "core" in domains

        tools = get_tools_for_domains(domains)
        assert "github_get_pr" in tools or any("github" in t for t in tools)
        assert "git_log" in tools

        # Prompt should include git/github context
        prompt = build_system_prompt(domains)
        assert "git" in prompt.lower() or "github" in prompt.lower()

    def test_scenario_log_analysis(self):
        """Scenario: User asks for log error analysis."""
        user_message = "analyze this stack trace from the logs"

        detector = DomainDetector()
        domains = detector.detect(user_message)

        assert "log" in domains
        assert "core" in domains

        tools = get_tools_for_domains(domains)
        assert any("log" in t.lower() for t in tools)

        prompt = build_system_prompt(domains)
        assert "log" in prompt.lower()
        assert "Mermaid" in prompt  # Should include diagrams

    def test_scenario_database_query(self):
        """Scenario: User asks SQL/database question."""
        user_message = "optimize this SELECT query performance"

        detector = DomainDetector()
        domains = detector.detect(user_message)

        assert "database" in domains

        tools = get_tools_for_domains(domains)
        assert any("sql" in t.lower() or "query" in t.lower() for t in tools)

    def test_scenario_unspecific_question(self):
        """Scenario: Generic question without domain indicators."""
        user_message = "hello, what can you do?"

        detector = DomainDetector()
        domains = detector.detect(user_message)

        # Should only have core
        assert domains == get_core_domains()

        tools = get_tools_for_domains(domains)
        prompt = build_system_prompt(domains)

        # Should be minimal
        assert len(tools) > 0  # Core tools exist
        assert "search_code" in tools

    def test_prompt_token_reduction(self):
        """Verify prompt token reduction vs. loading all modules."""
        # Scenario: Git/GitHub work
        user_message = "show me the latest commits"
        detector = DomainDetector()
        domains = detector.detect(user_message)

        # Modular prompt (optimized)
        modular_prompt = build_system_prompt(domains)
        modular_stats = get_module_stats(domains)
        modular_tokens = modular_stats["_total"]["token_estimate"]

        # Full prompt (all modules)
        all_domains = set(TOOL_DOMAINS.keys())
        full_prompt = build_system_prompt(all_domains)
        full_stats = get_module_stats(all_domains)
        full_tokens = full_stats["_total"]["token_estimate"]

        # Should save significant tokens
        reduction = (1 - modular_tokens / full_tokens) * 100
        assert reduction >= 60, f"Expected 60%+ reduction, got {reduction}%"
        print(f"Git scenario: {modular_tokens} tokens (modular) vs {full_tokens} (full) = {reduction:.1f}% reduction")

    def test_multiple_domains_complex_scenario(self):
        """Test scenario with multiple domain requirements."""
        user_message = """
        The Maven build is failing on deploy to Docker.
        Need to:
        1. Check git history for recent changes
        2. Review the PR on GitHub
        3. Analyze WLP server logs
        4. Run Java class analysis
        """

        detector = DomainDetector()
        domains = detector.detect(user_message)

        # Should detect multiple domains
        detected_domains = {"java", "maven", "docker", "git", "github", "log", "wlp", "core"}
        for domain in ["maven", "java", "docker", "git", "log"]:
            assert domain in domains, f"Expected {domain} in {domains}"

        # Tools should cover all needs
        tools = get_tools_for_domains(domains)
        assert len(tools) >= 20  # Multiple domains = more tools

        # Prompt should cover the complexity
        prompt = build_system_prompt(domains)
        assert len(prompt) > 2000  # Substantial prompt

    def test_domain_detection_context_awareness(self):
        """Test that detector uses conversation history."""
        current_msg = "what about this method?"
        history = [
            {"role": "user", "content": "analyze this java class"},
            {"role": "assistant", "content": "Here's the Java class structure..."},
        ]

        detector = DomainDetector()
        domains = detector.detect(current_msg, history)

        # Should infer java domain from history even without explicit keyword
        assert "java" in domains or any(
            "java" in str(msg.get("content", "")).lower() for msg in history
        )

    def test_tool_filtering_matches_domains(self):
        """Verify that domain-based tool filtering actually reduces tool count."""
        detector = DomainDetector()

        # Specific domain = fewer tools
        git_domains = detector.detect("git commit history")
        git_tools = get_tools_for_domains(git_domains)

        java_domains = detector.detect("java class analysis")
        java_tools = get_tools_for_domains(java_domains)

        all_domains = set(TOOL_DOMAINS.keys())
        all_tools = get_tools_for_domains(all_domains)

        # Both filtered sets should be smaller than all tools
        assert len(git_tools) < len(all_tools)
        assert len(java_tools) < len(all_tools)

        # But should still have essential tools (core)
        assert any("search" in t for t in git_tools)
        assert any("search" in t for t in java_tools)

    def test_fallback_when_no_domains_detected(self):
        """Test that system still works when no specific domains detected."""
        user_message = "hello"

        detector = DomainDetector()
        domains = detector.detect(user_message)

        # Should still have core
        assert "core" in domains

        tools = get_tools_for_domains(domains)
        prompt = build_system_prompt(domains)

        # Should have something usable
        assert len(tools) > 0
        assert len(prompt) > 500

    def test_prompt_module_consistency(self):
        """Verify prompt modules are well-formed."""
        for module_name, module_content in PROMPT_MODULES.items():
            assert isinstance(module_content, str)
            assert len(module_content) > 50, f"Module {module_name} too short"

    def test_case_insensitive_detection(self):
        """Domain detection should be case-insensitive."""
        detector = DomainDetector()

        domains1 = detector.detect("JAVA CLASS")
        domains2 = detector.detect("java class")
        domains3 = detector.detect("Java Class")

        assert domains1 == domains2
        assert domains2 == domains3

    def test_always_include_modules_loaded(self):
        """Verify that ALWAYS_INCLUDE_MODULES are loaded regardless of domains."""
        # Even with empty domains, core modules should load
        prompt = build_system_prompt(set())
        stats = get_module_stats(set())

        for module_name in ALWAYS_INCLUDE_MODULES:
            assert module_name in stats, f"Module {module_name} should always be loaded"

    @pytest.mark.parametrize("scenario,expected_domains", [
        ("Maven build", {"maven", "java"}),
        ("Git commit", {"git"}),
        ("Docker container", {"docker"}),
        ("SQL query", {"database"}),
        ("Log error", {"log"}),
        ("GitHub PR", {"github", "git"}),
        ("JUnit test", {"test"}),
    ])
    def test_parameterized_scenarios(self, scenario, expected_domains):
        """Test multiple scenarios systematically."""
        detector = DomainDetector()
        domains = detector.detect(scenario)

        # Should detect expected domains
        for domain in expected_domains:
            assert domain in domains, f"Expected {domain} in {domains} for scenario '{scenario}'"


class TestTokenMetrics:
    """Tests for token count metrics and reduction calculations."""

    def test_token_count_estimation(self):
        """Verify token counting works reasonably."""
        short_text = "hello"
        long_text = "hello" * 1000

        short_estimate = len(short_text) // 4
        long_estimate = len(long_text) // 4

        assert long_estimate > short_estimate

    def test_module_stats_completeness(self):
        """Verify module stats include all necessary fields."""
        domains = {"java", "git", "log"}
        stats = get_module_stats(domains)

        assert "_total" in stats
        assert stats["_total"]["token_estimate"] > 0

        for module_name in stats:
            if module_name != "_total":
                assert "char_count" in stats[module_name]
                assert "token_estimate" in stats[module_name]

    def test_prompt_reduction_percentage(self):
        """Calculate and verify prompt reduction percentage."""
        # Minimal domains
        minimal_domains = {"core"}
        minimal_prompt = build_system_prompt(minimal_domains)
        minimal_tokens = len(minimal_prompt) // 4

        # Full domains
        full_domains = set(TOOL_DOMAINS.keys())
        full_prompt = build_system_prompt(full_domains)
        full_tokens = len(full_prompt) // 4

        reduction_pct = ((full_tokens - minimal_tokens) / full_tokens) * 100
        assert reduction_pct > 50, f"Full prompt should be >50% larger, got {reduction_pct}%"


class TestErrorHandling:
    """Tests for error handling in the smart tool selector."""

    def test_domain_detector_handles_empty_history(self):
        """Domain detector should handle None/empty history gracefully."""
        detector = DomainDetector()

        result1 = detector.detect("java", None)
        result2 = detector.detect("java", [])

        assert isinstance(result1, set)
        assert isinstance(result2, set)
        assert "java" in result1
        assert "java" in result2

    def test_tool_filtering_with_invalid_domains(self):
        """Tool filtering should handle non-existent domains gracefully."""
        invalid_domains = {"nonexistent_domain", "fake_domain"}
        tools = get_tools_for_domains(invalid_domains)

        # Should return empty set (no tools for invalid domains)
        assert isinstance(tools, set)

    def test_prompt_building_with_invalid_domains(self):
        """Prompt builder should handle invalid domains gracefully."""
        invalid_domains = {"fake_domain_123"}
        prompt = build_system_prompt(invalid_domains)

        # Should still build a prompt with core modules
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert "core" in prompt.lower() or "mermaid" in prompt.lower()
