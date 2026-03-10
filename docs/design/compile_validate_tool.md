# Design: Compile & Validate Tool

## Übersicht

Ein universelles Tool zur Validierung und Kompilierung geänderter Dateien verschiedener Typen. Unterstützt Python, Java, SQL, SQLJ und weitere relevante Dateitypen.

---

## Architektur

```
┌─────────────────────────────────────────────────────────────────────┐
│                      compile_files Tool                              │
│         (Haupttool - orchestriert alle Validatoren)                  │
├─────────────────────────────────────────────────────────────────────┤
│                        Validator Registry                            │
├──────────┬──────────┬──────────┬──────────┬──────────┬─────────────┤
│  Python  │   Java   │   SQL    │   SQLJ   │   XML    │   Config    │
│ Validator│ Validator│ Validator│ Validator│ Validator│  Validator  │
├──────────┴──────────┴──────────┴──────────┴──────────┴─────────────┤
│                      File Change Detector                            │
│         (Git-basiert oder Timestamp-basiert)                         │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Tool 1: `compile_files` - Haupttool

### Zweck
Validiert/kompiliert geänderte Dateien und gibt strukturiertes Feedback.

### Parameter

| Name | Typ | Required | Beschreibung |
|------|-----|----------|--------------|
| `files` | string | nein | Kommagetrennte Dateipfade oder Glob-Pattern |
| `changed_only` | boolean | nein | Nur Git-geänderte Dateien (default: true) |
| `types` | string | nein | Dateitypen: python,java,sql,xml,config,all (default: all) |
| `fix` | boolean | nein | Auto-Fix wenn möglich (Linting, Formatting) |
| `strict` | boolean | nein | Strenger Modus: Warnings als Errors |
| `repo` | string | nein | Repo-Pfad (default: aktives Java/Python Repo) |

### Output-Format

```yaml
=== Compile/Validate Results ===
Repository: /path/to/repo
Files checked: 12
Time: 2.3s

✓ Python (5 files)
  ✓ src/service.py - OK
  ✓ src/utils.py - OK
  ⚠ src/handler.py - 2 warnings
    Line 45: W503 line break before binary operator
    Line 89: E501 line too long (92 > 88)
  ✗ src/parser.py - 1 error
    Line 23: SyntaxError: unexpected indent

✓ Java (4 files)
  ✓ src/main/java/Service.java - OK
  ✓ src/main/java/Handler.java - OK

⚠ SQL (2 files)
  ⚠ src/main/resources/query.sql - 1 warning
    Line 12: Missing semicolon at end of statement

✓ Config (1 file)
  ✓ config.yaml - Valid YAML

═══════════════════════════════════════
Summary: 11 passed, 1 warning, 1 error
═══════════════════════════════════════
```

---

## Validatoren

### 1. Python Validator

**Prüfungen:**
1. **Syntax-Check** (`py_compile`) - Pflicht
2. **Linting** (`ruff` oder `flake8`) - Optional
3. **Type-Check** (`mypy`) - Optional
4. **Import-Check** - Prüft ob Imports auflösbar

**Auto-Fix:**
- `ruff --fix` oder `autopep8` für Formatting
- Import-Sortierung mit `isort`

```python
class PythonValidator:
    def validate(self, file_path: str, fix: bool = False) -> ValidationResult:
        # 1. Syntax check (immer)
        # 2. Linting (wenn Tool verfügbar)
        # 3. Type hints (wenn mypy verfügbar)
```

### 2. Java Validator

**Prüfungen:**
1. **Syntax-Check** (`javac -Xlint:all`) - Pflicht
2. **Maven Compile** (wenn pom.xml vorhanden)
3. **Gradle Compile** (wenn build.gradle vorhanden)

**Modi:**
- `quick`: Nur javac Syntax-Check (schnell)
- `full`: Maven/Gradle compile (langsamer, vollständig)

```python
class JavaValidator:
    def validate(self, file_path: str, mode: str = "quick") -> ValidationResult:
        # Quick: javac -d /dev/null -Xlint:all File.java
        # Full: mvn compile -pl :module
```

### 3. SQL Validator

**Prüfungen:**
1. **Syntax-Check** (ohne DB-Verbindung)
   - Grundlegende SQL-Grammatik
   - Statement-Terminierung (`;`)
   - Klammer-Balance
   - String-Literal-Balance
2. **DB2-spezifisch** (wenn konfiguriert)
   - DB2 SQL Syntax
   - Bekannte Funktionen
3. **Best Practices**
   - SELECT * Warnung
   - Fehlende WHERE bei UPDATE/DELETE

```python
class SQLValidator:
    def validate(self, file_path: str, dialect: str = "db2") -> ValidationResult:
        # Regex-basierte Checks ohne DB-Verbindung
        # Optional: sqlparse für tiefere Analyse
```

### 4. SQLJ Validator

**Prüfungen:**
1. **Java-Syntax** (Host-Code)
2. **SQL-Syntax** (eingebettete Statements)
3. **SQLJ-Translator** (wenn verfügbar)
   - `sqlj -compile=false -ser2class=false`

```python
class SQLJValidator:
    def validate(self, file_path: str) -> ValidationResult:
        # 1. Java-Teil extrahieren und prüfen
        # 2. SQL-Statements extrahieren und prüfen
        # 3. Optional: SQLJ Translator
```

### 5. XML Validator

**Prüfungen:**
1. **Well-Formed** (XML-Syntax)
2. **Schema-Validierung** (wenn XSD referenziert)
3. **Spezifische Formate:**
   - `pom.xml` → Maven POM Schema
   - `web.xml` → Servlet Schema
   - `persistence.xml` → JPA Schema

```python
class XMLValidator:
    def validate(self, file_path: str) -> ValidationResult:
        # lxml für Parsing und Schema-Validierung
```

### 6. Config Validator

**Dateitypen:**
- **YAML** (`.yaml`, `.yml`)
- **JSON** (`.json`)
- **Properties** (`.properties`)
- **TOML** (`.toml`)

**Prüfungen:**
1. **Syntax** (Parsing)
2. **Schema** (wenn JSON-Schema vorhanden)
3. **Referenzen** (Environment-Variablen, Pfade)

```python
class ConfigValidator:
    def validate(self, file_path: str) -> ValidationResult:
        ext = Path(file_path).suffix
        if ext in ('.yaml', '.yml'):
            yaml.safe_load(content)
        elif ext == '.json':
            json.loads(content)
        elif ext == '.properties':
            # Key=Value Format prüfen
```

### 7. Shell Script Validator

**Prüfungen:**
1. **Shellcheck** (wenn installiert)
2. **Bash Syntax** (`bash -n`)

### 8. Build File Validator

**Dateitypen:**
- `pom.xml` → Maven
- `build.gradle` / `build.gradle.kts` → Gradle
- `Makefile`
- `Dockerfile`

---

## File Change Detector

### Git-basiert (Standard)

```python
class GitChangeDetector:
    def get_changed_files(self, repo_path: str, base: str = "HEAD") -> List[str]:
        """
        Findet geänderte Dateien:
        - Unstaged changes (git diff)
        - Staged changes (git diff --cached)
        - Untracked files (git ls-files --others)
        """

    def get_files_since_commit(self, repo_path: str, commit: str) -> List[str]:
        """Findet alle Änderungen seit einem Commit."""
```

### Timestamp-basiert (Fallback)

```python
class TimestampChangeDetector:
    def get_changed_files(self, repo_path: str, since_minutes: int = 60) -> List[str]:
        """Findet kürzlich geänderte Dateien nach mtime."""
```

---

## Implementierungsplan

### Phase 1: Core Framework

```
app/utils/validators/
├── __init__.py
├── base.py              # BaseValidator, ValidationResult
├── registry.py          # ValidatorRegistry
├── change_detector.py   # Git/Timestamp Change Detection
├── python_validator.py
├── java_validator.py
├── sql_validator.py
├── sqlj_validator.py
├── xml_validator.py
├── config_validator.py
└── shell_validator.py

app/agent/compile_tools.py  # Tool-Registrierung
```

### Phase 2: Data Classes

```python
@dataclass
class ValidationIssue:
    """Ein einzelnes Problem."""
    severity: str          # error, warning, info
    line: Optional[int]
    column: Optional[int]
    message: str
    rule: Optional[str]    # z.B. E501, W503
    fixable: bool = False

@dataclass
class ValidationResult:
    """Ergebnis einer Datei-Validierung."""
    file_path: str
    file_type: str
    success: bool
    issues: List[ValidationIssue]
    time_ms: int

@dataclass
class CompileResult:
    """Gesamtergebnis aller Validierungen."""
    repo_path: str
    files_checked: int
    results: List[ValidationResult]
    total_errors: int
    total_warnings: int
    time_ms: int
```

### Phase 3: Config-Erweiterung

```yaml
# config.yaml
compile_tool:
  enabled: true

  # Standard-Verhalten
  default_changed_only: true     # Nur geänderte Dateien
  default_fix: false             # Auto-Fix deaktiviert
  default_strict: false          # Warnings nicht als Errors

  # Timeout
  timeout_per_file_seconds: 30
  total_timeout_seconds: 300

  # Python-spezifisch
  python:
    enabled: true
    linter: "ruff"               # ruff | flake8 | pylint | none
    type_checker: "none"         # mypy | pyright | none
    auto_fix_tool: "ruff"        # ruff | autopep8 | black
    ignore_rules: []             # z.B. ["E501", "W503"]

  # Java-spezifisch
  java:
    enabled: true
    mode: "quick"                # quick | maven | gradle
    java_home: ""                # JAVA_HOME (leer = System)
    javac_options: "-Xlint:all"

  # SQL-spezifisch
  sql:
    enabled: true
    dialect: "db2"               # db2 | postgres | mysql | ansi
    check_best_practices: true

  # SQLJ-spezifisch
  sqlj:
    enabled: true
    sqlj_path: ""                # Pfad zum SQLJ Translator

  # XML-spezifisch
  xml:
    enabled: true
    validate_schemas: true

  # Config-Dateien
  config:
    enabled: true
    formats: ["yaml", "json", "properties", "toml"]

  # Shell-Scripts
  shell:
    enabled: true
    use_shellcheck: true
```

---

## Beispiel-Workflows

### 1. Schnelle Validierung vor Commit

```bash
# Agent-Aufruf
compile_files(changed_only=true)

# Prüft alle geänderten Dateien im aktiven Repo
```

### 2. Vollständige Prüfung eines Moduls

```bash
compile_files(
  files="src/main/java/com/example/service/**/*.java",
  types="java",
  strict=true
)
```

### 3. Auto-Fix für Python

```bash
compile_files(
  files="src/",
  types="python",
  fix=true
)
# Führt ruff --fix aus und zeigt verbleibende Issues
```

### 4. SQL-Validierung

```bash
compile_files(
  files="src/main/resources/**/*.sql",
  types="sql"
)
# Prüft SQL-Syntax und Best Practices
```

---

## Validator-Implementierung (Beispiel)

### BaseValidator

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional
import time

class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

@dataclass
class ValidationIssue:
    severity: Severity
    message: str
    line: Optional[int] = None
    column: Optional[int] = None
    rule: Optional[str] = None
    fixable: bool = False

    def __str__(self) -> str:
        loc = f"Line {self.line}" if self.line else ""
        if self.column:
            loc += f":{self.column}"
        rule = f" [{self.rule}]" if self.rule else ""
        return f"{loc}: {self.severity.value.upper()}{rule} {self.message}"

@dataclass
class ValidationResult:
    file_path: str
    file_type: str
    success: bool
    issues: List[ValidationIssue] = field(default_factory=list)
    time_ms: int = 0

    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

class BaseValidator(ABC):
    """Basis-Klasse für alle Validatoren."""

    file_extensions: List[str] = []
    name: str = "base"

    def can_validate(self, file_path: str) -> bool:
        """Prüft ob dieser Validator die Datei verarbeiten kann."""
        return Path(file_path).suffix.lower() in self.file_extensions

    @abstractmethod
    async def validate(
        self,
        file_path: str,
        fix: bool = False,
        strict: bool = False
    ) -> ValidationResult:
        """Validiert eine Datei."""
        pass

    def _create_result(
        self,
        file_path: str,
        issues: List[ValidationIssue],
        start_time: float
    ) -> ValidationResult:
        """Erstellt ein ValidationResult."""
        has_errors = any(i.severity == Severity.ERROR for i in issues)
        return ValidationResult(
            file_path=file_path,
            file_type=self.name,
            success=not has_errors,
            issues=issues,
            time_ms=int((time.time() - start_time) * 1000)
        )
```

### PythonValidator (Beispiel)

```python
class PythonValidator(BaseValidator):
    file_extensions = [".py"]
    name = "python"

    async def validate(
        self,
        file_path: str,
        fix: bool = False,
        strict: bool = False
    ) -> ValidationResult:
        start = time.time()
        issues = []

        # 1. Syntax Check (immer)
        syntax_issues = await self._check_syntax(file_path)
        issues.extend(syntax_issues)

        # Bei Syntax-Fehlern: Sofort abbrechen
        if any(i.severity == Severity.ERROR for i in syntax_issues):
            return self._create_result(file_path, issues, start)

        # 2. Linting (optional)
        if self._has_linter():
            if fix:
                await self._run_autofix(file_path)
            lint_issues = await self._run_linter(file_path)
            issues.extend(lint_issues)

        # 3. Type Checking (optional)
        if self._has_type_checker():
            type_issues = await self._run_type_checker(file_path)
            issues.extend(type_issues)

        # Strict Mode: Warnings → Errors
        if strict:
            for issue in issues:
                if issue.severity == Severity.WARNING:
                    issue.severity = Severity.ERROR

        return self._create_result(file_path, issues, start)

    async def _check_syntax(self, file_path: str) -> List[ValidationIssue]:
        """Prüft Python-Syntax mit py_compile."""
        import py_compile
        try:
            py_compile.compile(file_path, doraise=True)
            return []
        except py_compile.PyCompileError as e:
            return [ValidationIssue(
                severity=Severity.ERROR,
                message=str(e.msg),
                line=e.lineno,
            )]

    async def _run_linter(self, file_path: str) -> List[ValidationIssue]:
        """Führt ruff/flake8 aus."""
        # Implementation...
```

---

## Abhängigkeiten

### Required
- `pathlib` (stdlib)
- `asyncio` (stdlib)
- `subprocess` (stdlib)

### Optional (für erweiterte Features)
- `ruff` / `flake8` - Python Linting
- `mypy` - Python Type Checking
- `sqlparse` - SQL Parsing
- `lxml` - XML Schema Validation
- `pyyaml` - YAML Parsing
- `toml` - TOML Parsing

---

## Nächste Schritte

1. [ ] `app/utils/validators/base.py` - Basis-Klassen
2. [ ] `app/utils/validators/python_validator.py`
3. [ ] `app/utils/validators/java_validator.py`
4. [ ] `app/utils/validators/sql_validator.py`
5. [ ] `app/utils/validators/sqlj_validator.py`
6. [ ] `app/utils/validators/xml_validator.py`
7. [ ] `app/utils/validators/config_validator.py`
8. [ ] `app/utils/validators/change_detector.py`
9. [ ] `app/agent/compile_tools.py` - Tool-Registrierung
10. [ ] Config-Schema erweitern
11. [ ] Tests schreiben
