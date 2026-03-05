import re
from pathlib import Path
from typing import Dict, List, Optional

from app.core.config import settings
from app.core.exceptions import JavaReaderError, PathTraversalError


class JavaReader:
    def __init__(self, repo_path: str = None):
        self.repo_path = Path(repo_path or settings.java.repo_path).resolve()
        self.exclude_dirs = set(settings.java.exclude_dirs)
        self.max_file_size = settings.java.max_file_size_kb * 1024

    def _safe_resolve(self, relative_path: str) -> Path:
        """Resolve path and ensure it stays within repo_path (prevent path traversal)."""
        full = (self.repo_path / relative_path).resolve()
        if not str(full).startswith(str(self.repo_path)):
            raise PathTraversalError(f"Unzulässiger Pfad: {relative_path}")
        return full

    def get_file_tree(self) -> Dict:
        """Return nested dict representing the project directory structure."""
        if not self.repo_path.exists():
            raise JavaReaderError(f"Repository-Pfad nicht gefunden: {self.repo_path}")
        return self._walk_dir(self.repo_path, self.repo_path)

    def _walk_dir(self, path: Path, base: Path) -> Dict:
        result = {"name": path.name, "type": "dir", "children": []}
        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
        except PermissionError:
            return result

        for entry in entries:
            if entry.name in self.exclude_dirs or entry.name.startswith("."):
                continue
            if entry.is_dir():
                result["children"].append(self._walk_dir(entry, base))
            elif entry.suffix in (".java", ".xml", ".properties", ".yaml", ".yml"):
                rel = str(entry.relative_to(base))
                result["children"].append({
                    "name": entry.name,
                    "type": "file",
                    "path": rel,
                    "size_kb": round(entry.stat().st_size / 1024, 1),
                })
        return result

    def read_file(self, relative_path: str) -> str:
        """Read a file from the repo. Returns its content as string."""
        full_path = self._safe_resolve(relative_path)
        if not full_path.exists():
            raise JavaReaderError(f"Datei nicht gefunden: {relative_path}")
        if full_path.stat().st_size > self.max_file_size:
            raise JavaReaderError(f"Datei zu groß (max {settings.java.max_file_size_kb}KB): {relative_path}")
        return full_path.read_text(encoding="utf-8", errors="replace")

    def extract_signatures(self, content: str) -> str:
        """Extract class/interface/method declarations from Java source (regex-based)."""
        lines = content.splitlines()
        signatures = []
        in_block_comment = False

        for line in lines:
            stripped = line.strip()

            # Skip block comments
            if "/*" in stripped:
                in_block_comment = True
            if "*/" in stripped:
                in_block_comment = False
                continue
            if in_block_comment or stripped.startswith("//"):
                continue

            # Match class/interface/enum declarations
            if re.match(r"^(public|protected|private)?\s*(abstract|final)?\s*(class|interface|enum|record)\s+\w+", stripped):
                signatures.append(line)
            # Match method declarations (heuristic: contains ( and ) but not = or new)
            elif re.match(r"^\s*(public|protected|private)", stripped) and "(" in stripped and ")" in stripped and not "=" in stripped.split("(")[0]:
                # Avoid variable declarations mistaken for methods
                if re.search(r"\)\s*(throws\s+\w+)?\s*\{?\s*$", stripped):
                    signatures.append(line)

        return "\n".join(signatures)

    def summarize_file(self, relative_path: str) -> Dict:
        """Return AST-like summary: package, class name, method signatures."""
        content = self.read_file(relative_path)
        package_match = re.search(r"^package\s+([\w.]+)\s*;", content, re.MULTILINE)
        class_match = re.search(r"(public|abstract|final)?\s*(class|interface|enum|record)\s+(\w+)", content)
        imports = re.findall(r"^import\s+([\w.*]+)\s*;", content, re.MULTILINE)

        return {
            "path": relative_path,
            "package": package_match.group(1) if package_match else "",
            "class_name": class_match.group(3) if class_match else "",
            "class_type": class_match.group(2) if class_match else "",
            "imports": imports[:20],  # cap at 20
            "signatures": self.extract_signatures(content),
        }

    def search_class(self, class_name: str) -> List[str]:
        """Find all .java files containing a class/interface matching class_name."""
        matches = []
        pattern = re.compile(
            r"(class|interface|enum|record)\s+" + re.escape(class_name) + r"\b"
        )
        for java_file in self.repo_path.rglob("*.java"):
            rel = str(java_file.relative_to(self.repo_path))
            if any(exc in rel for exc in self.exclude_dirs):
                continue
            try:
                if java_file.stat().st_size > self.max_file_size:
                    continue
                content = java_file.read_text(encoding="utf-8", errors="replace")
                if pattern.search(content):
                    matches.append(rel)
            except (PermissionError, OSError):
                continue
        return matches

    def get_pom_files(self) -> List[str]:
        """Return all pom.xml file paths (absolute) in the repo."""
        poms = []
        for pom in self.repo_path.rglob("pom.xml"):
            rel = str(pom.relative_to(self.repo_path))
            if not any(exc in rel for exc in self.exclude_dirs):
                poms.append(str(pom))
        return poms
