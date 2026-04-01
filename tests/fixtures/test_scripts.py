"""
Test script fixtures for script execution testing.

Provides simple test scripts for integration and E2E testing.
"""


# Simple hello world script
HELLO_WORLD_SCRIPT = """
print("Hello, World!")
"""

# Script with multiple output lines
MULTI_LINE_OUTPUT_SCRIPT = """
for i in range(5):
    print(f"Line {i+1}")
"""

# Script with stderr output
STDERR_SCRIPT = """
import sys
print("stdout line 1")
print("stderr line 1", file=sys.stderr)
print("stdout line 2")
print("stderr line 2", file=sys.stderr)
"""

# Script that takes arguments
ARGS_SCRIPT = """
import json
import sys

args = json.loads(sys.argv[1])
print(f"Received args: {args}")
print(f"Value: {args.get('value', 'default')}")
"""

# Script with failure
FAILING_SCRIPT = """
raise RuntimeError("Test error")
"""

# Script with timeout (sleeps for 10 seconds)
TIMEOUT_SCRIPT = """
import time
print("Starting long operation...")
time.sleep(10)
print("Done!")
"""

# Script with file operations
FILE_OPERATIONS_SCRIPT = """
import os
import tempfile

# Test file creation
test_dir = '{test_dir}'
test_file = os.path.join(test_dir, 'test.txt')
with open(test_file, 'w') as f:
    f.write('Test content')
print(f"File created: {test_file}")
"""

# Script requiring pandas
PANDAS_SCRIPT = """
import pandas as pd
print("Pandas version:", pd.__version__)
df = pd.DataFrame({'a': [1, 2, 3], 'b': [4, 5, 6]})
print(df)
"""

# Script with large output
LARGE_OUTPUT_SCRIPT = """
for i in range(1000):
    print(f"Output line {i}: " + "x" * 100)
"""

# Script with no newlines in output
NO_NEWLINE_SCRIPT = """
import sys
sys.stdout.write("Text without newline at end")
"""

# Script with special characters
SPECIAL_CHARS_SCRIPT = """
print("Special chars: <script>alert('xss')</script>")
print("Unicode: 中文 العربية 🚀")
print("Control chars: \\x00 \\x01")
"""
