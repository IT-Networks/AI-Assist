"""
E2E Test Reporter.

Generates HTML and JSON reports from test results.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

from .models import TestResult, TestSuiteResult, TrackedToolCall

logger = logging.getLogger(__name__)


class E2EReporter:
    """
    Generates reports from E2E test results.

    Supports HTML and JSON output formats.
    """

    def __init__(self, output_dir: Union[str, Path] = "reports"):
        """
        Initialize reporter.

        Args:
            output_dir: Directory to save reports
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_json_report(
        self,
        suite_result: TestSuiteResult,
        filename: Optional[str] = None,
    ) -> Path:
        """
        Generate JSON report.

        Args:
            suite_result: Test suite result
            filename: Output filename (auto-generated if None)

        Returns:
            Path to generated report
        """
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"e2e_report_{timestamp}.json"

        report_data = {
            "suite_name": suite_result.name,
            "timestamp": suite_result.timestamp.isoformat(),
            "summary": {
                "total": suite_result.total,
                "passed": suite_result.passed,
                "failed": suite_result.failed,
                "success_rate": suite_result.success_rate,
                "duration_ms": suite_result.total_duration_ms,
            },
            "tests": [
                self._test_result_to_dict(test)
                for test in suite_result.tests
            ],
        }

        output_path = self.output_dir / filename
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, default=str)

        logger.info(f"JSON report generated: {output_path}")
        return output_path

    def generate_html_report(
        self,
        suite_result: TestSuiteResult,
        filename: Optional[str] = None,
    ) -> Path:
        """
        Generate HTML report with dark theme.

        Args:
            suite_result: Test suite result
            filename: Output filename (auto-generated if None)

        Returns:
            Path to generated report
        """
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"e2e_report_{timestamp}.html"

        html = self._generate_html(suite_result)

        output_path = self.output_dir / filename
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

        logger.info(f"HTML report generated: {output_path}")
        return output_path

    def _test_result_to_dict(self, test: TestResult) -> dict:
        """Convert TestResult to dictionary."""
        return {
            "name": test.name,
            "passed": test.passed,
            "prompt": test.prompt,
            "expected_tools": test.expected_tools,
            "actual_tools": [
                {
                    "name": t.name,
                    "arguments": t.arguments,
                    "status": t.status,
                    "order": t.order,
                    "error": t.error_message,
                }
                for t in test.actual_tools
            ],
            "response_preview": test.response_preview,
            "duration_ms": test.duration_ms,
            "errors": test.errors,
            "timestamp": test.timestamp.isoformat(),
        }

    def _generate_html(self, suite_result: TestSuiteResult) -> str:
        """Generate HTML report content."""
        tests_html = "\n".join([
            self._generate_test_html(test)
            for test in suite_result.tests
        ])

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>E2E Test Report - {suite_result.name}</title>
    <style>
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0d1117;
            color: #c9d1d9;
            line-height: 1.6;
            padding: 20px;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        h1 {{
            color: #58a6ff;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 1px solid #30363d;
        }}
        .summary {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }}
        .metric {{
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 15px;
            text-align: center;
        }}
        .metric-value {{
            font-size: 28px;
            font-weight: bold;
            color: #58a6ff;
        }}
        .metric-value.success {{
            color: #3fb950;
        }}
        .metric-value.failure {{
            color: #f85149;
        }}
        .metric-label {{
            font-size: 12px;
            color: #8b949e;
            text-transform: uppercase;
        }}
        .test-card {{
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 8px;
            margin-bottom: 15px;
            overflow: hidden;
        }}
        .test-header {{
            padding: 15px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            cursor: pointer;
            border-bottom: 1px solid #30363d;
        }}
        .test-header:hover {{
            background: #1f2937;
        }}
        .test-title {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .status-badge {{
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
        }}
        .status-badge.passed {{
            background: rgba(63, 185, 80, 0.2);
            color: #3fb950;
        }}
        .status-badge.failed {{
            background: rgba(248, 81, 73, 0.2);
            color: #f85149;
        }}
        .test-details {{
            padding: 15px;
            display: none;
        }}
        .test-details.open {{
            display: block;
        }}
        .detail-section {{
            margin-bottom: 15px;
        }}
        .detail-label {{
            font-size: 11px;
            color: #8b949e;
            text-transform: uppercase;
            margin-bottom: 5px;
        }}
        .prompt {{
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 4px;
            padding: 10px;
            font-family: monospace;
            font-size: 13px;
            white-space: pre-wrap;
        }}
        .tool-list {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }}
        .tool-badge {{
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 12px;
            font-family: monospace;
        }}
        .tool-badge.expected {{
            background: rgba(88, 166, 255, 0.2);
            color: #58a6ff;
            border: 1px solid rgba(88, 166, 255, 0.3);
        }}
        .tool-badge.actual {{
            background: rgba(63, 185, 80, 0.2);
            color: #3fb950;
            border: 1px solid rgba(63, 185, 80, 0.3);
        }}
        .tool-badge.error {{
            background: rgba(248, 81, 73, 0.2);
            color: #f85149;
            border: 1px solid rgba(248, 81, 73, 0.3);
        }}
        .tool-order {{
            background: #30363d;
            border-radius: 50%;
            width: 18px;
            height: 18px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-size: 10px;
        }}
        .errors {{
            background: rgba(248, 81, 73, 0.1);
            border: 1px solid rgba(248, 81, 73, 0.3);
            border-radius: 4px;
            padding: 10px;
            color: #f85149;
            font-size: 13px;
        }}
        .duration {{
            color: #8b949e;
            font-size: 13px;
        }}
        .response-preview {{
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 4px;
            padding: 10px;
            font-size: 13px;
            max-height: 150px;
            overflow-y: auto;
        }}
        .timestamp {{
            color: #8b949e;
            font-size: 12px;
            margin-top: 20px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>E2E Test Report: {suite_result.name}</h1>

        <div class="summary">
            <div class="metric">
                <div class="metric-value">{suite_result.total}</div>
                <div class="metric-label">Total Tests</div>
            </div>
            <div class="metric">
                <div class="metric-value success">{suite_result.passed}</div>
                <div class="metric-label">Passed</div>
            </div>
            <div class="metric">
                <div class="metric-value failure">{suite_result.failed}</div>
                <div class="metric-label">Failed</div>
            </div>
            <div class="metric">
                <div class="metric-value">{suite_result.success_rate:.1f}%</div>
                <div class="metric-label">Success Rate</div>
            </div>
            <div class="metric">
                <div class="metric-value">{suite_result.total_duration_ms / 1000:.1f}s</div>
                <div class="metric-label">Duration</div>
            </div>
        </div>

        <div class="tests">
            {tests_html}
        </div>

        <div class="timestamp">Generated: {suite_result.timestamp.strftime('%Y-%m-%d %H:%M:%S')}</div>
    </div>

    <script>
        document.querySelectorAll('.test-header').forEach(header => {{
            header.addEventListener('click', () => {{
                const details = header.nextElementSibling;
                details.classList.toggle('open');
            }});
        }});
    </script>
</body>
</html>"""

    def _generate_test_html(self, test: TestResult) -> str:
        """Generate HTML for a single test result."""
        status_class = "passed" if test.passed else "failed"
        status_text = "PASSED" if test.passed else "FAILED"

        # Expected tools
        expected_html = " ".join([
            f'<span class="tool-badge expected">{t}</span>'
            for t in test.expected_tools
        ])

        # Actual tools with order
        actual_html = " ".join([
            f'<span class="tool-badge {"actual" if t.status == "success" else "error"}">'
            f'<span class="tool-order">{t.order + 1}</span>{t.name}</span>'
            for t in test.actual_tools
        ])

        # Errors
        errors_html = ""
        if test.errors:
            error_list = "<br>".join(test.errors)
            errors_html = f'<div class="detail-section"><div class="detail-label">Errors</div><div class="errors">{error_list}</div></div>'

        return f"""
        <div class="test-card">
            <div class="test-header">
                <div class="test-title">
                    <span class="status-badge {status_class}">{status_text}</span>
                    <span>{test.name}</span>
                </div>
                <span class="duration">{test.duration_ms}ms</span>
            </div>
            <div class="test-details">
                <div class="detail-section">
                    <div class="detail-label">Prompt</div>
                    <div class="prompt">{test.prompt}</div>
                </div>
                <div class="detail-section">
                    <div class="detail-label">Expected Tools</div>
                    <div class="tool-list">{expected_html if expected_html else '<em>None</em>'}</div>
                </div>
                <div class="detail-section">
                    <div class="detail-label">Actual Tools</div>
                    <div class="tool-list">{actual_html if actual_html else '<em>None</em>'}</div>
                </div>
                {errors_html}
                <div class="detail-section">
                    <div class="detail-label">Response Preview</div>
                    <div class="response-preview">{test.response_preview or '<em>No response</em>'}</div>
                </div>
            </div>
        </div>"""

    def print_summary(self, suite_result: TestSuiteResult) -> None:
        """Print test summary to console."""
        print("\n" + "=" * 60)
        print(f"E2E Test Results: {suite_result.name}")
        print("=" * 60)
        print(f"Total:    {suite_result.total}")
        print(f"Passed:   {suite_result.passed}")
        print(f"Failed:   {suite_result.failed}")
        print(f"Rate:     {suite_result.success_rate:.1f}%")
        print(f"Duration: {suite_result.total_duration_ms / 1000:.2f}s")
        print("=" * 60)

        for test in suite_result.tests:
            status = "[PASS]" if test.passed else "[FAIL]"
            print(f"  {status} {test.name} ({test.duration_ms}ms)")
            if not test.passed and test.errors:
                for error in test.errors:
                    print(f"      -> {error}")

        print()
