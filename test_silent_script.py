#!/usr/bin/env python3
"""
Test: Silent scripts (no print statements) - like docx2pdf conversion.

Problem: Scripts that don't produce output show "(no output)" in tool result.
This is EXPECTED behavior but might be confusing.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from app.services.script_manager import get_script_manager

async def test_silent_script():
    """Test a script that produces no stdout (like docx2pdf)."""

    print("=" * 60)
    print("TEST: Silent Script (No Output)")
    print("=" * 60)
    print()

    manager = get_script_manager()

    # Simulate docx2pdf: silent operation, no print statements
    silent_code = '''
# This simulates docx2pdf behavior:
# - Takes file as input
# - Converts it
# - No print statements
# - Returns silently

import json
input_file = SCRIPT_ARGS.get("input_file", "test.docx")
output_file = SCRIPT_ARGS.get("output_file", "test.pdf")

# Simulate work (silent processing - no output)
x = 0
for i in range(100):
    x += i

# Simulation: file would be written here (but we skip it for test)
result = json.dumps({"converted": True, "input": input_file, "output": output_file})
'''

    script, validation = await manager.generate_and_save(
        code=silent_code,
        name="test_silent_docx2pdf",
        description="Simulates docx2pdf - silent conversion with no output",
        parameters={"input_file": "Input DOCX file", "output_file": "Output PDF file"},
        requirements=None
    )

    print(f"Script created: {script.id}")
    print(f"Code: (silent script with no print statements)")
    print()

    # Execute with arguments
    print("Executing script...")
    result = await manager.execute(
        script.id,
        args={"input_file": "document.docx", "output_file": "document.pdf"},
        input_data=None,
        on_output_chunk=None  # No output callback
    )

    print()
    print("=" * 60)
    print("RESULT:")
    print("=" * 60)
    print(f"Success: {result.success}")
    print(f"Execution Time: {result.execution_time_ms}ms")
    print(f"Stdout: {repr(result.stdout)}")
    print(f"Stderr: {repr(result.stderr)}")
    print()

    # This is what the tool will show
    tool_output = result.stdout if result.stdout else "(no output)"
    print("=" * 60)
    print("WHAT TOOL WILL SHOW:")
    print("=" * 60)
    print(f"Output: {tool_output}")
    print()

    print("=" * 60)
    print("ANALYSIS:")
    print("=" * 60)
    print()
    print("This is EXPECTED behavior for:")
    print("  - docx2pdf conversion (silent operation)")
    print("  - Image processing (no output unless error)")
    print("  - File operations (write-only, no read)")
    print("  - Background tasks")
    print()
    print("Solution: Scripts should use print() for progress:")
    print("  - 'Converting document...'")
    print("  - 'Done! Output saved to: document.pdf'")
    print()

if __name__ == "__main__":
    asyncio.run(test_silent_script())
