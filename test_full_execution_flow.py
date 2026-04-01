#!/usr/bin/env python3
"""
Test: Full Script Execution Flow (Generate → Confirm → Execute)

This simulates what happens when:
1. User asks AI to generate a script
2. AI calls generate_python_script tool
3. User confirms execution
4. Script is executed
5. Output is displayed
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from app.agent.script_tools import (
    handle_generate_script,
    handle_execute_script,
    execute_script_after_confirmation
)
from app.services.script_manager import get_script_manager

async def test_full_flow():
    """Test the complete flow."""

    print("=" * 70)
    print("TEST: Full Script Execution Flow")
    print("=" * 70)
    print()

    # ========== STEP 1: Generate Script ==========
    print("STEP 1: AI generates a script (handle_generate_script)")
    print("-" * 70)

    gen_result = await handle_generate_script(
        code='''
print("Starting calculation...")
result = sum(range(100))
print(f"Sum result: {result}")
print("Done!")
''',
        name="test_sum_calculator",
        description="Calculates sum",
        parameters=None,
        requirements=None
    )

    print(f"Result success: {gen_result.success}")
    print(f"Data: {gen_result.data[:100]}...")
    if gen_result.confirmation_data:
        script_id = gen_result.confirmation_data.get("script_id")
        print(f"Script ID: {script_id}")
    print()

    # ========== STEP 2: User Confirms (Extract info) ==========
    print("STEP 2: User sees confirmation panel and approves")
    print("-" * 70)

    if gen_result.confirmation_data:
        confirmation_data = gen_result.confirmation_data
        print(f"Code to confirm:")
        for line in confirmation_data.get("code", "").split('\n')[:3]:
            if line.strip():
                print(f"  {line}")
        print()

    # ========== STEP 3: Execute After Confirmation ==========
    print("STEP 3: Execute script after confirmation (execute_script_after_confirmation)")
    print("-" * 70)

    if gen_result.confirmation_data:
        exec_result = await execute_script_after_confirmation(
            script_id=gen_result.confirmation_data.get("script_id"),
            args=gen_result.confirmation_data.get("args"),
            input_data=gen_result.confirmation_data.get("input_data"),
            on_output_chunk=None  # ← This is usually None in confirmation flow
        )

        print(f"Execution success: {exec_result.success}")
        print(f"ToolResult data:")
        print(exec_result.data)
    print()

    # ========== VERIFICATION ==========
    print("=" * 70)
    print("VERIFICATION")
    print("=" * 70)

    if exec_result.success:
        # Check if output is in the ToolResult
        if "(no output)" in exec_result.data:
            print("ERROR: Output shows '(no output)'")
            print("  This happens when ExecutionResult.stdout is empty")
            print()
            print("Check:")
            print("  1. Is script code correct?")
            print("  2. Are print statements in the code?")
            print("  3. Is ExecutionResult.stdout being populated?")
        else:
            print("OK: Output is displayed in ToolResult")
            print("  The 'print' statements appear in the result")
    else:
        print("ERROR: Execution failed")
        print(f"  Error: {exec_result.error}")

    print()

if __name__ == "__main__":
    asyncio.run(test_full_flow())
