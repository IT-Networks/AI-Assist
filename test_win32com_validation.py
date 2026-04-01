#!/usr/bin/env python3
"""
Test: win32com Import Validation

Tests what happens when script tries to use win32com
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from app.agent.script_tools import handle_generate_script

async def test():
    print("=" * 70)
    print("TEST: win32com Import Validation")
    print("=" * 70)
    print()

    # Code that imports win32com
    code = '''
from win32com.client import Dispatch

excel = Dispatch("Excel.Application")
print("Excel opened successfully")
'''

    print("Code to validate:")
    print(code)
    print()

    # Try to generate script
    result = await handle_generate_script(
        code=code,
        name="test_win32com",
        description="Test win32com import",
        parameters=None,
        requirements=None
    )

    print("-" * 70)
    print("Result:")
    print("-" * 70)
    print(f"Success: {result.success}")
    if not result.success:
        print(f"Error: {result.error}")
    else:
        print(f"Data: {result.data[:200]}...")
    print()

    # Diagnosis
    if not result.success and "Nicht erlaubter Import" in result.error:
        print("=" * 70)
        print("DIAGNOSIS:")
        print("=" * 70)
        print()
        print("The error message shows that 'win32com' is NOT in allowed_imports")
        print()
        print("To fix:")
        print("  1. Go to Settings → Python Scripts")
        print("  2. Scroll to 'allowed_imports' section")
        print("  3. Click 'Add' button")
        print("  4. Type: win32com")
        print("  5. Click 'Save' button at the bottom")
        print()
        print("IMPORTANT: Make sure to click 'Save' to persist the change!")
        print()

asyncio.run(test())
