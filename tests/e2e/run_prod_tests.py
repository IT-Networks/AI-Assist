#!/usr/bin/env python3
"""
Run E2E tests in production mode.

Usage:
    python run_prod_tests.py                          # All scenarios
    python run_prod_tests.py --scenario local_files   # Specific scenario
    python run_prod_tests.py --quick                  # Only quick tests
    python run_prod_tests.py --dry-run                # Show what would run
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tests.e2e.test_runner import E2ETestRunner
from tests.e2e.framework import E2EReporter


def load_config(env_file: str = "config_prod.env"):
    """Load configuration from env file."""
    env_path = Path(__file__).parent / env_file
    if env_path.exists():
        load_dotenv(env_path)
        print(f"[+] Loaded config from {env_file}")
    else:
        print(f"[!] No {env_file} found, using defaults/environment")

    config = {
        "ai_assist_url": os.getenv("AI_ASSIST_URL", "http://localhost:8000"),
        "proxy_url": os.getenv("PROXY_URL", ""),  # Empty = production mode
        "model": os.getenv("TEST_MODEL", "gpt-4o-mini"),
        "timeout": float(os.getenv("TEST_TIMEOUT", "180")),
        "workspace": os.getenv("TEST_WORKSPACE", ""),
    }

    print(f"[*] AI-Assist URL: {config['ai_assist_url']}")
    print(f"[*] Mode: {'TEST (with proxy)' if config['proxy_url'] else 'PRODUCTION'}")
    print(f"[*] Model: {config['model']}")

    return config


async def run_tests(
    scenario: str = None,
    quick: bool = False,
    dry_run: bool = False,
    config: dict = None,
):
    """Run E2E tests."""

    scenarios_dir = Path(__file__).parent / "scenarios"
    reports_dir = Path(__file__).parent.parent.parent / "reports"
    reports_dir.mkdir(exist_ok=True)

    # Find scenario files
    if scenario:
        scenario_files = list(scenarios_dir.glob(f"*{scenario}*.yaml"))
    else:
        scenario_files = list(scenarios_dir.glob("*.yaml"))

    if not scenario_files:
        print(f"[!] No scenarios found matching: {scenario}")
        return

    print(f"\n[*] Found {len(scenario_files)} scenario file(s):")
    for sf in scenario_files:
        print(f"    - {sf.name}")

    if dry_run:
        print("\n[DRY-RUN] Would execute the above scenarios")
        return

    # Initialize runner
    runner = E2ETestRunner(
        ai_assist_url=config["ai_assist_url"],
        proxy_url=config["proxy_url"] or None,
        model=config["model"],
        timeout=config["timeout"],
    )

    reporter = E2EReporter(output_dir=reports_dir)

    # Run each scenario file
    all_results = []

    for scenario_file in scenario_files:
        print(f"\n{'='*60}")
        print(f"[*] Running: {scenario_file.name}")
        print(f"{'='*60}")

        try:
            result = await runner.run_scenario_file(scenario_file)
            all_results.append(result)

            # Print summary
            print(f"\n[RESULT] {result.name}")
            print(f"    Passed: {result.passed}/{result.total} ({result.success_rate:.1f}%)")
            print(f"    Duration: {result.total_duration_ms/1000:.1f}s")

            # Generate report
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            json_path = reporter.save_json(result, f"prod_{result.name}_{timestamp}")
            html_path = reporter.save_html(result, f"prod_{result.name}_{timestamp}")

            print(f"    Reports: {json_path.name}, {html_path.name}")

        except Exception as e:
            print(f"[ERROR] Failed to run {scenario_file.name}: {e}")
            import traceback
            traceback.print_exc()

    # Final summary
    if all_results:
        print(f"\n{'='*60}")
        print("[FINAL SUMMARY]")
        print(f"{'='*60}")

        total_passed = sum(r.passed for r in all_results)
        total_tests = sum(r.total for r in all_results)

        print(f"Total: {total_passed}/{total_tests} tests passed")
        print(f"Overall: {total_passed/total_tests*100:.1f}%" if total_tests else "No tests")


def main():
    parser = argparse.ArgumentParser(description="Run E2E tests in production mode")
    parser.add_argument("--scenario", "-s", help="Scenario name filter")
    parser.add_argument("--quick", "-q", action="store_true", help="Run quick tests only")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Show what would run")
    parser.add_argument("--env", "-e", default="config_prod.env", help="Config file")

    args = parser.parse_args()

    print("="*60)
    print("E2E Test Runner - Production Mode")
    print("="*60)

    config = load_config(args.env)

    asyncio.run(run_tests(
        scenario=args.scenario,
        quick=args.quick,
        dry_run=args.dry_run,
        config=config,
    ))


if __name__ == "__main__":
    main()
