import ast
import os
from pathlib import Path
from typing import Dict, List, Optional


class PythonReader:
    """
    Liest Python-Repositories: Dateibaum, AST-Analyse, Symbol-Suche.
    Analogon zu java_reader.py, aber nutzt das eingebaute ast-Modul.
    """

    DEFAULT_EXCLUDE = {"__pycache__", ".venv", ".git", "node_modules", ".mypy_cache", ".pytest_cache", "dist", "build"}

    def __init__(self, repo_path: str, exclude_dirs: Optional[List[str]] = None, max_file_size_kb: int = 500):
        self.repo_path = Path(repo_path).resolve() if repo_path else Path(".")
        self.exclude_dirs = set(exclude_dirs or self.DEFAULT_EXCLUDE)
        self.max_file_size = max_file_size_kb * 1024

    def _is_excluded(self, path: Path) -> bool:
        return any(part in self.exclude_dirs for part in path.parts)

    def get_all_files(self) -> List[str]:
        """Gibt alle .py Dateipfade relativ zum repo_path zurück."""
        if not self.repo_path.is_dir():
            return []
        result = []
        for p in sorted(self.repo_path.rglob("*.py")):
            if not self._is_excluded(p.relative_to(self.repo_path)):
                if p.stat().st_size <= self.max_file_size:
                    result.append(str(p.relative_to(self.repo_path)))
        return result

    def get_file_tree(self) -> dict:
        """Verschachtelter Verzeichnisbaum mit .py Dateien."""
        if not self.repo_path.is_dir():
            return {"name": str(self.repo_path), "type": "directory", "children": []}

        def _build(directory: Path) -> dict:
            name = directory.name or str(directory)
            children = []
            try:
                entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name))
            except PermissionError:
                return {"name": name, "type": "directory", "children": []}

            for entry in entries:
                rel = entry.relative_to(self.repo_path)
                if self._is_excluded(rel):
                    continue
                if entry.is_dir():
                    sub = _build(entry)
                    if sub.get("children") or sub.get("type") == "file":
                        children.append(sub)
                elif entry.is_file() and entry.suffix == ".py":
                    size_kb = round(entry.stat().st_size / 1024, 1)
                    children.append({
                        "name": entry.name,
                        "type": "file",
                        "path": str(rel),
                        "size_kb": size_kb,
                    })

            return {"name": name, "type": "directory", "children": children}

        return _build(self.repo_path)

    def read_file(self, rel_path: str) -> str:
        """Liest eine Datei (path-traversal-sicher)."""
        target = (self.repo_path / rel_path).resolve()
        if not str(target).startswith(str(self.repo_path)):
            raise ValueError(f"Path traversal verhindert: {rel_path}")
        if not target.exists():
            raise FileNotFoundError(f"Datei nicht gefunden: {rel_path}")
        if target.stat().st_size > self.max_file_size:
            raise ValueError(f"Datei zu groß: {rel_path}")
        return target.read_text(encoding="utf-8", errors="replace")

    def summarize_file(self, rel_path: str) -> dict:
        """
        Analysiert eine .py Datei per ast.parse() und extrahiert:
        - module_docstring
        - classes: [{name, bases, methods, docstring}]
        - functions: [{name, args, decorators, docstring}]
        - imports: [module-Namen]
        """
        try:
            content = self.read_file(rel_path)
        except Exception as e:
            return {"error": str(e), "file_path": rel_path}

        result: dict = {
            "file_path": rel_path,
            "module_docstring": "",
            "classes": [],
            "functions": [],
            "imports": [],
        }

        try:
            tree = ast.parse(content, filename=rel_path)
        except SyntaxError as e:
            result["error"] = f"Syntaxfehler: {e}"
            return result

        # Module docstring
        result["module_docstring"] = ast.get_docstring(tree) or ""

        for node in ast.walk(tree):
            # Imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    result["imports"].append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    result["imports"].append(f"{module}.{alias.name}" if module else alias.name)

        # Top-level classes and functions only
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                bases = [_name(b) for b in node.bases]
                methods = []
                for item in ast.iter_child_nodes(node):
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.append({
                            "name": item.name,
                            "args": _format_args(item.args),
                            "decorators": [_name(d) for d in item.decorator_list],
                            "docstring": ast.get_docstring(item) or "",
                        })
                result["classes"].append({
                    "name": node.name,
                    "bases": bases,
                    "methods": methods,
                    "docstring": ast.get_docstring(node) or "",
                    "line": node.lineno,
                })
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                result["functions"].append({
                    "name": node.name,
                    "args": _format_args(node.args),
                    "decorators": [_name(d) for d in node.decorator_list],
                    "docstring": ast.get_docstring(node) or "",
                    "line": node.lineno,
                })

        return result

    def search_symbol(self, name: str) -> List[dict]:
        """
        Sucht Klassen und Funktionen nach Name (exakt + Teilstring).
        Gibt [{file_path, type, symbol_name, line_no}] zurück.
        """
        results = []
        name_lower = name.lower()

        for rel_path in self.get_all_files():
            try:
                content = self.read_file(rel_path)
                tree = ast.parse(content, filename=rel_path)
            except Exception:
                continue

            for node in ast.walk(tree):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    sym_name = node.name
                    if name_lower in sym_name.lower():
                        sym_type = "class" if isinstance(node, ast.ClassDef) else "function"
                        results.append({
                            "file_path": rel_path,
                            "type": sym_type,
                            "symbol_name": sym_name,
                            "line_no": node.lineno,
                        })

        return results


def _name(node) -> str:
    """Extrahiert einen lesbaren Namen aus einem AST-Knoten."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name(node.value)}.{node.attr}"
    if isinstance(node, ast.Constant):
        return str(node.value)
    return ast.unparse(node) if hasattr(ast, "unparse") else "?"


def _format_args(args: ast.arguments) -> str:
    """Formatiert Funktionsargumente als lesbaren String."""
    parts = []
    # positional args
    for arg in args.args:
        parts.append(arg.arg)
    if args.vararg:
        parts.append(f"*{args.vararg.arg}")
    for arg in args.kwonlyargs:
        parts.append(arg.arg)
    if args.kwarg:
        parts.append(f"**{args.kwarg.arg}")
    return ", ".join(parts)
