"""
Workspace Manager for E2E Testing.

Manages test workspace with:
- State snapshots (backup/restore)
- File tracking (modifications, creations)
- Clean reset between tests
- Git-like diff tracking

Usage:
    manager = WorkspaceManager(Path("./test-workspace"))
    manager.snapshot("before_test")
    # ... run test that modifies files ...
    manager.restore("before_test")  # Reset to original state
"""

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set


@dataclass
class FileState:
    """State of a single file."""
    path: str
    content_hash: str
    size: int
    modified: datetime
    exists: bool = True

    @classmethod
    def from_file(cls, file_path: Path, base_path: Path) -> "FileState":
        """Create FileState from an actual file."""
        rel_path = str(file_path.relative_to(base_path))

        if not file_path.exists():
            return cls(
                path=rel_path,
                content_hash="",
                size=0,
                modified=datetime.now(),
                exists=False
            )

        content = file_path.read_bytes()
        content_hash = hashlib.md5(content).hexdigest()

        return cls(
            path=rel_path,
            content_hash=content_hash,
            size=len(content),
            modified=datetime.fromtimestamp(file_path.stat().st_mtime),
            exists=True
        )


@dataclass
class WorkspaceState:
    """Complete state of workspace at a point in time."""
    name: str
    timestamp: datetime
    files: Dict[str, FileState] = field(default_factory=dict)
    file_contents: Dict[str, bytes] = field(default_factory=dict)

    @property
    def file_count(self) -> int:
        return len([f for f in self.files.values() if f.exists])


@dataclass
class FileDiff:
    """Diff between two file states."""
    path: str
    status: str  # added, modified, deleted, unchanged
    old_hash: Optional[str] = None
    new_hash: Optional[str] = None


class WorkspaceManager:
    """
    Manages test workspace state with snapshot/restore capability.

    Features:
    - Take snapshots of workspace state
    - Restore workspace to previous snapshots
    - Track file modifications between snapshots
    - Automatic cleanup of test artifacts
    """

    # File patterns to exclude from tracking
    EXCLUDE_PATTERNS = {
        "__pycache__",
        ".git",
        "*.pyc",
        ".pytest_cache",
        ".coverage",
        "*.egg-info",
    }

    def __init__(self, workspace_path: Path):
        """
        Initialize workspace manager.

        Args:
            workspace_path: Path to the test workspace directory
        """
        self.workspace_path = Path(workspace_path).resolve()
        self.snapshots: Dict[str, WorkspaceState] = {}
        self._tracked_files: Set[str] = set()

        if not self.workspace_path.exists():
            raise ValueError(f"Workspace does not exist: {self.workspace_path}")

    def _should_track(self, path: Path) -> bool:
        """Check if a file should be tracked."""
        path_str = str(path)

        for pattern in self.EXCLUDE_PATTERNS:
            if pattern.startswith("*"):
                if path_str.endswith(pattern[1:]):
                    return False
            elif pattern in path_str:
                return False

        return True

    def _get_all_files(self) -> List[Path]:
        """Get all trackable files in workspace."""
        files = []

        for file_path in self.workspace_path.rglob("*"):
            if file_path.is_file() and self._should_track(file_path):
                files.append(file_path)

        return files

    def snapshot(self, name: str = "default") -> WorkspaceState:
        """
        Take a snapshot of current workspace state.

        Args:
            name: Name for the snapshot (for later restore)

        Returns:
            WorkspaceState with all file states and contents
        """
        state = WorkspaceState(
            name=name,
            timestamp=datetime.now(),
        )

        for file_path in self._get_all_files():
            rel_path = str(file_path.relative_to(self.workspace_path))

            # Store file state
            file_state = FileState.from_file(file_path, self.workspace_path)
            state.files[rel_path] = file_state

            # Store file content for restore
            if file_path.exists():
                state.file_contents[rel_path] = file_path.read_bytes()

            self._tracked_files.add(rel_path)

        self.snapshots[name] = state
        return state

    def restore(self, name: str = "default") -> List[FileDiff]:
        """
        Restore workspace to a previous snapshot.

        Args:
            name: Name of the snapshot to restore

        Returns:
            List of FileDiff showing what was changed
        """
        if name not in self.snapshots:
            raise ValueError(f"Snapshot '{name}' not found")

        snapshot = self.snapshots[name]
        diffs = []

        # Get current state for comparison
        current_files = {
            str(f.relative_to(self.workspace_path)): f
            for f in self._get_all_files()
        }

        # Restore files from snapshot
        for rel_path, file_state in snapshot.files.items():
            file_path = self.workspace_path / rel_path
            current_exists = rel_path in current_files

            if file_state.exists:
                # File should exist - restore content
                content = snapshot.file_contents.get(rel_path)
                if content is not None:
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_bytes(content)

                    if not current_exists:
                        diffs.append(FileDiff(
                            path=rel_path,
                            status="restored",
                            new_hash=file_state.content_hash
                        ))
                    else:
                        # Check if modified
                        current_hash = hashlib.md5(
                            current_files[rel_path].read_bytes()
                        ).hexdigest()
                        if current_hash != file_state.content_hash:
                            diffs.append(FileDiff(
                                path=rel_path,
                                status="reverted",
                                old_hash=current_hash,
                                new_hash=file_state.content_hash
                            ))
            else:
                # File should not exist - delete if present
                if current_exists:
                    file_path.unlink()
                    diffs.append(FileDiff(
                        path=rel_path,
                        status="deleted"
                    ))

        # Delete files that were created after snapshot
        for rel_path in current_files:
            if rel_path not in snapshot.files:
                file_path = self.workspace_path / rel_path
                if file_path.exists():
                    file_path.unlink()
                    diffs.append(FileDiff(
                        path=rel_path,
                        status="deleted"
                    ))

        return diffs

    def diff(self, from_name: str = "default", to_name: str = "current") -> List[FileDiff]:
        """
        Compare two snapshots.

        Args:
            from_name: Name of the baseline snapshot
            to_name: Name of the target snapshot (or "current" for current state)

        Returns:
            List of FileDiff showing differences
        """
        if from_name not in self.snapshots:
            raise ValueError(f"Snapshot '{from_name}' not found")

        from_snapshot = self.snapshots[from_name]
        diffs = []

        # Get target state
        if to_name == "current":
            to_files = {}
            for file_path in self._get_all_files():
                rel_path = str(file_path.relative_to(self.workspace_path))
                to_files[rel_path] = FileState.from_file(file_path, self.workspace_path)
        else:
            if to_name not in self.snapshots:
                raise ValueError(f"Snapshot '{to_name}' not found")
            to_files = self.snapshots[to_name].files

        # Compare files
        all_paths = set(from_snapshot.files.keys()) | set(to_files.keys())

        for rel_path in all_paths:
            from_state = from_snapshot.files.get(rel_path)
            to_state = to_files.get(rel_path)

            if from_state is None or not from_state.exists:
                if to_state and to_state.exists:
                    diffs.append(FileDiff(
                        path=rel_path,
                        status="added",
                        new_hash=to_state.content_hash
                    ))
            elif to_state is None or not to_state.exists:
                diffs.append(FileDiff(
                    path=rel_path,
                    status="deleted",
                    old_hash=from_state.content_hash
                ))
            elif from_state.content_hash != to_state.content_hash:
                diffs.append(FileDiff(
                    path=rel_path,
                    status="modified",
                    old_hash=from_state.content_hash,
                    new_hash=to_state.content_hash
                ))

        return diffs

    def get_modified_files(self, since: str = "default") -> List[str]:
        """Get list of files modified since a snapshot."""
        diffs = self.diff(from_name=since, to_name="current")
        return [d.path for d in diffs if d.status in ("modified", "added")]

    def has_changes(self, since: str = "default") -> bool:
        """Check if workspace has changes since a snapshot."""
        return len(self.diff(from_name=since, to_name="current")) > 0

    def clear_snapshots(self) -> None:
        """Clear all stored snapshots."""
        self.snapshots.clear()

    def save_state(self, output_path: Path) -> None:
        """Save current workspace state to JSON file."""
        state = self.snapshot("_export")

        export_data = {
            "workspace": str(self.workspace_path),
            "timestamp": state.timestamp.isoformat(),
            "files": {
                path: {
                    "hash": fs.content_hash,
                    "size": fs.size,
                    "exists": fs.exists,
                }
                for path, fs in state.files.items()
            }
        }

        output_path.write_text(json.dumps(export_data, indent=2))
        del self.snapshots["_export"]


# ============================================================================
# Pre-built Test Workspace Templates
# ============================================================================

def create_python_project_workspace(base_path: Path) -> WorkspaceManager:
    """
    Create a standard Python project workspace for testing.

    Structure:
    - src/
      - __init__.py
      - calculator.py
      - math_operations.py
    - tests/
      - __init__.py
      - test_calculator.py
    - README.md
    - requirements.txt
    """
    workspace = base_path / "test_workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    # Create src directory
    src = workspace / "src"
    src.mkdir(exist_ok=True)

    (src / "__init__.py").write_text('"""Source package."""\n')

    (src / "calculator.py").write_text('''"""Calculator module."""

from typing import Optional, List


class Calculator:
    """A simple calculator with history."""

    def __init__(self, initial_value: float = 0):
        self.value = initial_value
        self._history: List[str] = []

    def add(self, x: float) -> float:
        """Add x to current value."""
        self.value += x
        self._history.append(f"add({x})")
        return self.value

    def subtract(self, x: float) -> float:
        """Subtract x from current value."""
        self.value -= x
        self._history.append(f"subtract({x})")
        return self.value

    def multiply(self, x: float) -> float:
        """Multiply current value by x."""
        self.value *= x
        self._history.append(f"multiply({x})")
        return self.value

    def divide(self, x: float) -> Optional[float]:
        """Divide current value by x."""
        if x == 0:
            return None  # BUG: Should handle this better
        self.value /= x
        self._history.append(f"divide({x})")
        return self.value

    def get_history(self) -> List[str]:
        """Get operation history."""
        return self._history.copy()

    def reset(self) -> None:
        """Reset calculator."""
        self.value = 0
        self._history.clear()
''')

    (src / "math_operations.py").write_text('''"""Math operations module."""


def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


def multiply(a: int, b: int) -> int:
    """Multiply two numbers."""
    return a * b


def greet(name: str) -> str:
    """Greet someone."""
    return f"Hello, {name}!"
''')

    # Create tests directory
    tests = workspace / "tests"
    tests.mkdir(exist_ok=True)

    (tests / "__init__.py").write_text('"""Tests package."""\n')

    (tests / "test_calculator.py").write_text('''"""Tests for Calculator."""

import pytest
from src.calculator import Calculator


class TestCalculator:
    """Test cases for Calculator class."""

    def test_add(self):
        calc = Calculator(10)
        assert calc.add(5) == 15

    def test_subtract(self):
        calc = Calculator(10)
        assert calc.subtract(3) == 7

    def test_multiply(self):
        calc = Calculator(10)
        assert calc.multiply(2) == 20

    def test_divide(self):
        calc = Calculator(10)
        assert calc.divide(2) == 5.0

    # TODO: Add test for divide by zero
''')

    # Create root files
    (workspace / "README.md").write_text('''# Test Project

A simple calculator project for E2E testing.

## Features

- Basic arithmetic operations
- Operation history
- Math utilities

## Usage

```python
from src.calculator import Calculator

calc = Calculator(10)
calc.add(5)  # 15
calc.multiply(2)  # 30
```
''')

    (workspace / "requirements.txt").write_text('''pytest>=7.0.0
pytest-cov>=4.0.0
''')

    return WorkspaceManager(workspace)
