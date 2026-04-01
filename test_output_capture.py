#!/usr/bin/env python3
"""
Test: Verify that script output is correctly captured and returned.

Problem: Tools show "(no output)" even though scripts are executed.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from app.services.script_manager import get_script_manager

async def test_output_capture():
    """Test that stdout is captured in ExecutionResult."""

    print("=" * 60)
    print("TEST: Output Capture in Script Execution")
    print("=" * 60)
    print()

    manager = get_script_manager()

    # Create test script with explicit output
    test_code = '''
print("Line 1: Hello from Python Script!")
print("Line 2: Testing output capture")
x = 42 * 2
print(f"Line 3: Result is {x}")
print("Line 4: Done")
'''

    # Generate script
    script, validation = await manager.generate_and_save(
        code=test_code,
        name="test_output_simple",
        description="Test output capture",
        parameters=None,
        requirements=None
    )

    print(f"Script created: {script.id}")
    print(f"Code:")
    for i, line in enumerate(script.code.split('\n'), 1):
        if line.strip():
            print(f"  {i}: {line}")
    print()

    # Execute script
    print("Executing script...")
    output_events = []

    async def on_output(stream_type, chunk):
        output_events.append((stream_type, chunk))
        print(f"  EVENT [{stream_type}]: {chunk}")

    result = await manager.execute(
        script.id,
        args={},
        input_data=None,
        on_output_chunk=on_output
    )

    print()
    print("=" * 60)
    print("EXECUTION RESULT:")
    print("=" * 60)
    print(f"Success: {result.success}")
    print(f"Execution Time: {result.execution_time_ms}ms")
    print()

    print("STDOUT:")
    if result.stdout:
        for line in result.stdout.split('\n')[:10]:
            if line:
                print(f"  {line}")
        if len(result.stdout.split('\n')) > 10:
            print(f"  ... ({len(result.stdout.split(chr(10)))} total lines)")
    else:
        print("  (empty/None)")
    print()

    print("STDERR:")
    if result.stderr:
        for line in result.stderr.split('\n')[:5]:
            if line:
                print(f"  {line}")
    else:
        print("  (empty/None)")
    print()

    print("OUTPUT EVENTS RECEIVED:")
    print(f"  Total events: {len(output_events)}")
    for i, (stream, chunk) in enumerate(output_events[:5], 1):
        print(f"  {i}. [{stream}] {chunk}")
    if len(output_events) > 5:
        print(f"  ... ({len(output_events)} total)")
    print()

    # Diagnosis
    print("=" * 60)
    print("DIAGNOSIS:")
    print("=" * 60)

    if not result.stdout:
        print("ERROR: stdout is empty or None!")
        print("  This is why tools show '(no output)'")
        print("  Check if _run_local() is correctly joining stdout_lines")
    elif len(result.stdout.split('\n')) < 4:
        print("WARNING: stdout has fewer lines than expected")
        print("  Expected: 4 print statements")
        print(f"  Got: {len(result.stdout.split(chr(10)))} lines")
    else:
        print("OK: stdout appears to be captured correctly")
        print("  Problem might be in tool output formatting")

    if output_events:
        print(f"OK: Output events received: {len(output_events)}")
    else:
        print("ERROR: No output events received!")
        print("  on_output_chunk callback was never called")

    print()

if __name__ == "__main__":
    asyncio.run(test_output_capture())
