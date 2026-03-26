#!/usr/bin/env python3
"""
CLI script to run E2E tests.

Usage:
    python run_tests.py                     # Run all tests
    python run_tests.py --tags local_files  # Run only local file tests
    python run_tests.py --report            # Generate HTML report
    python run_tests.py --verbose           # Verbose output
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tests.e2e.test_runner import E2ETestRunner, run_e2e_tests


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Run AI-Assist E2E Tests")
    parser.add_argument(
        "--tags",
        nargs="+",
        help="Filter tests by tags (e.g., local_files, github, multi_tool)",
    )
    parser.add_argument(
        "--scenario",
        type=str,
        help="Run specific scenario file",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Generate HTML/JSON reports",
    )
    parser.add_argument(
        "--ai-assist-url",
        default="http://localhost:8000",
        help="AI-Assist server URL",
    )
    parser.add_argument(
        "--proxy-url",
        default="http://localhost:8080",
        help="LLM-Test-Proxy URL",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    # Determine scenarios directory
    scenarios_dir = Path(__file__).parent / "scenarios"

    print("=" * 60)
    print("AI-Assist E2E Test Runner")
    print("=" * 60)
    print(f"AI-Assist URL: {args.ai_assist_url}")
    print(f"Proxy URL:     {args.proxy_url}")
    print(f"Scenarios:     {scenarios_dir}")
    if args.tags:
        print(f"Tags filter:   {args.tags}")
    print("=" * 60)
    print()

    try:
        results = await run_e2e_tests(
            scenarios_dir=scenarios_dir,
            tags=args.tags,
            generate_report=args.report,
        )

        # Summary
        total_tests = sum(r.total for r in results)
        passed_tests = sum(r.passed for r in results)
        failed_tests = sum(r.failed for r in results)

        print()
        print("=" * 60)
        print("FINAL SUMMARY")
        print("=" * 60)
        print(f"Total Suites:  {len(results)}")
        print(f"Total Tests:   {total_tests}")
        print(f"Passed:        {passed_tests}")
        print(f"Failed:        {failed_tests}")

        if total_tests > 0:
            rate = (passed_tests / total_tests) * 100
            print(f"Success Rate:  {rate:.1f}%")

        print("=" * 60)

        # Exit with error code if any tests failed
        sys.exit(0 if failed_tests == 0 else 1)

    except ConnectionError as e:
        print(f"\nError: {e}")
        print("\nMake sure AI-Assist and LLM-Test-Proxy are running:")
        print("  1. Start AI-Assist:     cd AI-Assist && uvicorn app.main:app --port 8000")
        print("  2. Start Proxy:         cd LLM-Test-Proxy && uvicorn app.main:app --port 8080")
        sys.exit(1)

    except Exception as e:
        print(f"\nUnexpected error: {e}")
        logging.exception("Test run failed")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
