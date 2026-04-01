#!/usr/bin/env python3
"""
Test: Error output display

Tests if stderr/errors are correctly captured and displayed
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from app.services.script_manager import get_script_manager

async def test():
    print("=" * 70)
    print("TEST: Error Output Display")
    print("=" * 70)
    print()

    manager = get_script_manager()

    # Script that fails with an error
    code = '''
import sys
print("Starting script...")

# This will cause an error
x = 10
y = 0
try:
    result = x / y
except ZeroDivisionError as e:
    print(f"Caught error: {e}")
    sys.stderr.write("ERROR: Division by zero detected\\n")
    raise

print("This should not be reached")
'''

    script, validation = await manager.generate_and_save(
        code=code,
        name="test_error_output",
        description="Test error handling",
        parameters=None,
        requirements=None
    )

    print(f"Script ID: {script.id}")
    print()

    # Execute
    print("Executing script...")
    print()

    output_events = []
    async def capture(stream_type, chunk):
        output_events.append((stream_type, chunk))

    result = await manager.execute(
        script.id,
        args={},
        input_data=None,
        on_output_chunk=capture
    )

    print("-" * 70)
    print("EXECUTION RESULT:")
    print("-" * 70)
    print(f"Success: {result.success}")
    print(f"Return code: {getattr(result, 'returncode', 'N/A')}")
    print()

    print("STDOUT:")
    if result.stdout:
        for line in result.stdout.split('\n')[:10]:
            if line.strip():
                print(f"  {line}")
    else:
        print("  (empty)")
    print()

    print("STDERR:")
    if result.stderr:
        for line in result.stderr.split('\n')[:10]:
            if line.strip():
                print(f"  {line}")
    else:
        print("  (empty)")
    print()

    print("ERROR:")
    if result.error:
        print(f"  {result.error[:200]}")
    else:
        print("  (none)")
    print()

    print("-" * 70)
    print("STREAMING EVENTS:")
    print("-" * 70)
    print(f"Total events: {len(output_events)}")
    for i, (stream, chunk) in enumerate(output_events[:10], 1):
        print(f"  {i}. [{stream}] {chunk[:50]}...")
    print()

    # Analysis
    print("=" * 70)
    print("ANALYSIS:")
    print("=" * 70)
    if result.success:
        print("ERROR: Script succeeded but should have failed!")
    else:
        print("OK: Script failed as expected")

    if result.stderr:
        print("OK: stderr is captured")
    else:
        print("PROBLEM: stderr is empty")
        print("  Errors may not be displayed to user")

    if len(output_events) > 0:
        print(f"OK: Got {len(output_events)} streaming events")
    else:
        print("PROBLEM: No streaming events")
        print("  User doesn't see output in real-time")

asyncio.run(test())
