"""
JUnit Template Engine - Generiert JUnit-Tests.

Unterstützt:
- JUnit 4 und 5
- Verschiedene Stile (Basic, Mockito, Spring)
- Given-When-Then Struktur
- Automatische Mock-Erstellung
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from app.utils.java_analyzer import JavaClass, JavaMethod, JavaParameter

logger = logging.getLogger(__name__)


class JUnitVersion(str, Enum):
    """JUnit-Version."""
    JUNIT4 = "4"
    JUNIT5 = "5"


class TestStyle(str, Enum):
    """Test-Stil."""
    BASIC = "basic"           # Einfache Assertions
    MOCKITO = "mockito"       # Mit Mocking
    SPRING = "spring"         # SpringBootTest
    PARAMETERIZED = "param"   # Parametrisierte Tests


@dataclass
class TestConfig:
    """Konfiguration für Test-Generierung."""
    version: JUnitVersion = JUnitVersion.JUNIT5
    style: TestStyle = TestStyle.BASIC
    generate_negative_tests: bool = True
    generate_edge_cases: bool = True
    use_given_when_then: bool = True
    add_todo_comments: bool = True


class JUnitTemplateEngine:
    """
    Generiert JUnit-Tests basierend auf Java-Klassen-Analyse.
    """

    # JUnit 4 Imports
    JUNIT4_IMPORTS = [
        "org.junit.Test",
        "org.junit.Before",
        "org.junit.After",
        "org.junit.BeforeClass",
        "org.junit.AfterClass",
        "static org.junit.Assert.*",
    ]

    # JUnit 5 Imports
    JUNIT5_IMPORTS = [
        "org.junit.jupiter.api.Test",
        "org.junit.jupiter.api.BeforeEach",
        "org.junit.jupiter.api.AfterEach",
        "org.junit.jupiter.api.BeforeAll",
        "org.junit.jupiter.api.AfterAll",
        "org.junit.jupiter.api.DisplayName",
        "static org.junit.jupiter.api.Assertions.*",
    ]

    # Mockito Imports
    MOCKITO_IMPORTS = [
        "org.mockito.Mock",
        "org.mockito.InjectMocks",
        "org.mockito.Mockito",
        "static org.mockito.Mockito.*",
        "static org.mockito.ArgumentMatchers.*",
    ]

    MOCKITO_JUNIT4_IMPORTS = [
        "org.mockito.junit.MockitoJUnitRunner",
        "org.junit.runner.RunWith",
    ]

    MOCKITO_JUNIT5_IMPORTS = [
        "org.mockito.junit.jupiter.MockitoExtension",
        "org.junit.jupiter.api.extension.ExtendWith",
    ]

    # Spring Imports
    SPRING_IMPORTS = [
        "org.springframework.boot.test.context.SpringBootTest",
        "org.springframework.beans.factory.annotation.Autowired",
        "org.springframework.boot.test.mock.mockito.MockBean",
    ]

    def __init__(self, config: Optional[TestConfig] = None):
        """
        Args:
            config: Test-Konfiguration
        """
        self.config = config or TestConfig()

    def generate_test_class(
        self,
        java_class: JavaClass,
        methods: Optional[List[JavaMethod]] = None,
    ) -> str:
        """
        Generiert eine vollständige Test-Klasse.

        Args:
            java_class: Analysierte Java-Klasse
            methods: Zu testende Methoden (None = alle testbaren)

        Returns:
            Java-Quellcode der Test-Klasse
        """
        if methods is None:
            methods = java_class.get_testable_methods()

        # Imports zusammenstellen
        imports = self._build_imports(java_class)

        # Class-Annotation
        class_annotation = self._build_class_annotation()

        # Felder (Mocks, Subject)
        fields = self._build_fields(java_class)

        # Setup-Methode
        setup = self._build_setup(java_class)

        # Test-Methoden
        test_methods = []
        for method in methods:
            test_methods.extend(self._generate_test_methods(method, java_class))

        # Zusammenbauen
        lines = []

        # Package
        if java_class.package:
            lines.append(f"package {java_class.package};")
            lines.append("")

        # Imports
        for imp in sorted(imports):
            if imp.startswith("static "):
                lines.append(f"import {imp};")
            else:
                lines.append(f"import {imp};")
        lines.append("")

        # Class
        if class_annotation:
            lines.append(class_annotation)
        lines.append(f"class {java_class.test_class_name} {{")
        lines.append("")

        # Fields
        if fields:
            lines.extend(f"    {line}" for line in fields)
            lines.append("")

        # Setup
        if setup:
            lines.extend(f"    {line}" for line in setup)
            lines.append("")

        # Test methods
        for test_method in test_methods:
            lines.extend(f"    {line}" for line in test_method)
            lines.append("")

        lines.append("}")

        return "\n".join(lines)

    def _build_imports(self, java_class: JavaClass) -> List[str]:
        """Baut die Import-Liste."""
        imports = []

        # JUnit Imports
        if self.config.version == JUnitVersion.JUNIT5:
            imports.extend(self.JUNIT5_IMPORTS)
        else:
            imports.extend(self.JUNIT4_IMPORTS)

        # Style-spezifische Imports
        if self.config.style in (TestStyle.MOCKITO, TestStyle.SPRING):
            imports.extend(self.MOCKITO_IMPORTS)
            if self.config.version == JUnitVersion.JUNIT5:
                imports.extend(self.MOCKITO_JUNIT5_IMPORTS)
            else:
                imports.extend(self.MOCKITO_JUNIT4_IMPORTS)

        if self.config.style == TestStyle.SPRING:
            imports.extend(self.SPRING_IMPORTS)

        # Original-Klasse importieren (falls anderes Package)
        if java_class.package:
            imports.append(java_class.full_name)

        # Dependencies für Mocks
        deps = java_class.get_dependencies()
        # Hier könnten wir die vollen Import-Pfade aus den Original-Imports extrahieren
        # Für jetzt nehmen wir an, sie sind im selben Package oder importiert

        return imports

    def _build_class_annotation(self) -> str:
        """Baut die Klassen-Annotation."""
        if self.config.style == TestStyle.SPRING:
            return "@SpringBootTest"
        elif self.config.style == TestStyle.MOCKITO:
            if self.config.version == JUnitVersion.JUNIT5:
                return "@ExtendWith(MockitoExtension.class)"
            else:
                return "@RunWith(MockitoJUnitRunner.class)"
        return ""

    def _build_fields(self, java_class: JavaClass) -> List[str]:
        """Baut die Feld-Deklarationen."""
        lines = []

        if self.config.style in (TestStyle.MOCKITO, TestStyle.SPRING):
            # Mocks für Dependencies
            for dep in java_class.get_dependencies():
                if self.config.style == TestStyle.SPRING:
                    lines.append(f"@MockBean")
                else:
                    lines.append(f"@Mock")
                lines.append(f"private {dep} {self._to_camel_case(dep)};")
                lines.append("")

            # Subject under test
            if self.config.style == TestStyle.MOCKITO:
                lines.append("@InjectMocks")
            else:
                lines.append("@Autowired")
            lines.append(f"private {java_class.name} {self._to_camel_case(java_class.name)};")
        else:
            # Einfache Instanz
            lines.append(f"private {java_class.name} {self._to_camel_case(java_class.name)};")

        return lines

    def _build_setup(self, java_class: JavaClass) -> List[str]:
        """Baut die Setup-Methode."""
        lines = []

        if self.config.version == JUnitVersion.JUNIT5:
            lines.append("@BeforeEach")
            lines.append("void setUp() {")
        else:
            lines.append("@Before")
            lines.append("public void setUp() {")

        if self.config.style == TestStyle.BASIC:
            # Direkte Instanziierung
            var_name = self._to_camel_case(java_class.name)
            if java_class.constructors:
                # Konstruktor mit Parametern
                ctor = java_class.constructors[0]
                params = ", ".join(self._default_value(p.type) for p in ctor.parameters)
                lines.append(f"    {var_name} = new {java_class.name}({params});")
            else:
                # Default-Konstruktor
                lines.append(f"    {var_name} = new {java_class.name}();")
        else:
            # Mockito/Spring - Injection passiert automatisch
            lines.append("    // Mocks werden automatisch injiziert")

        lines.append("}")

        return lines

    def _generate_test_methods(
        self,
        method: JavaMethod,
        java_class: JavaClass
    ) -> List[List[str]]:
        """Generiert Test-Methoden für eine Methode."""
        test_methods = []

        # Happy Path Test
        test_methods.append(self._generate_happy_path_test(method, java_class))

        # Negative Tests
        if self.config.generate_negative_tests:
            for test in self._generate_negative_tests(method, java_class):
                test_methods.append(test)

        # Edge Cases
        if self.config.generate_edge_cases:
            for test in self._generate_edge_case_tests(method, java_class):
                test_methods.append(test)

        return test_methods

    def _generate_happy_path_test(
        self,
        method: JavaMethod,
        java_class: JavaClass
    ) -> List[str]:
        """Generiert einen Happy-Path-Test."""
        lines = []

        # Test-Name
        test_name = f"test{self._capitalize(method.name)}_shouldSucceed"
        display_name = f"{method.name} should succeed with valid input"

        # Annotation
        lines.append("@Test")
        if self.config.version == JUnitVersion.JUNIT5:
            lines.append(f'@DisplayName("{display_name}")')

        # Signatur
        if self.config.version == JUnitVersion.JUNIT5:
            lines.append(f"void {test_name}() {{")
        else:
            lines.append(f"public void {test_name}() {{")

        if self.config.use_given_when_then:
            lines.append("    // Given")
            lines.extend(self._generate_given_section(method))
            lines.append("")
            lines.append("    // When")
            lines.extend(self._generate_when_section(method, java_class))
            lines.append("")
            lines.append("    // Then")
            lines.extend(self._generate_then_section(method))
        else:
            lines.extend(self._generate_given_section(method))
            lines.extend(self._generate_when_section(method, java_class))
            lines.extend(self._generate_then_section(method))

        if self.config.add_todo_comments:
            lines.append("    // TODO: Add specific assertions for your use case")

        lines.append("}")

        return lines

    def _generate_negative_tests(
        self,
        method: JavaMethod,
        java_class: JavaClass
    ) -> List[List[str]]:
        """Generiert negative Tests."""
        tests = []

        # Null-Parameter Test
        if method.parameters:
            lines = []
            test_name = f"test{self._capitalize(method.name)}_shouldHandleNullInput"

            lines.append("@Test")
            if self.config.version == JUnitVersion.JUNIT5:
                lines.append(f'@DisplayName("{method.name} should handle null input")')

            if self.config.version == JUnitVersion.JUNIT5:
                lines.append(f"void {test_name}() {{")
            else:
                lines.append(f"public void {test_name}() {{")

            lines.append("    // Given")
            # Ersten Parameter als null
            for i, param in enumerate(method.parameters):
                if self._is_nullable(param.type):
                    lines.append(f"    {param.type} {param.name} = null;")
                else:
                    lines.append(f"    {param.type} {param.name} = {self._default_value(param.type)};")

            lines.append("")
            lines.append("    // When / Then")
            var_name = self._to_camel_case(java_class.name)
            params = ", ".join(p.name for p in method.parameters)

            if method.throws and any("Exception" in t for t in method.throws):
                # Exception erwartet
                exc_type = next((t for t in method.throws if "Exception" in t), "Exception")
                if self.config.version == JUnitVersion.JUNIT5:
                    lines.append(f"    assertThrows({exc_type}.class, () -> {{")
                    lines.append(f"        {var_name}.{method.name}({params});")
                    lines.append("    });")
                else:
                    lines.append(f"    // @Test(expected = {exc_type}.class) - oder try-catch")
                    lines.append(f"    {var_name}.{method.name}({params});")
            else:
                lines.append(f"    // Verify behavior with null input")
                lines.append(f"    // {var_name}.{method.name}({params});")

            lines.append("}")
            tests.append(lines)

        return tests

    def _generate_edge_case_tests(
        self,
        method: JavaMethod,
        java_class: JavaClass
    ) -> List[List[str]]:
        """Generiert Edge-Case-Tests."""
        tests = []

        # Leere Strings/Collections
        for param in method.parameters:
            if param.type == "String":
                lines = []
                test_name = f"test{self._capitalize(method.name)}_withEmptyString"

                lines.append("@Test")
                if self.config.version == JUnitVersion.JUNIT5:
                    lines.append(f'@DisplayName("{method.name} should handle empty string")')

                if self.config.version == JUnitVersion.JUNIT5:
                    lines.append(f"void {test_name}() {{")
                else:
                    lines.append(f"public void {test_name}() {{")

                lines.append(f"    // Given")
                lines.append(f'    String {param.name} = "";')
                lines.append("")
                lines.append(f"    // When / Then")
                lines.append(f"    // TODO: Test behavior with empty string")
                lines.append("}")

                tests.append(lines)
                break  # Nur einen Edge-Case-Test

        return tests

    def _generate_given_section(self, method: JavaMethod) -> List[str]:
        """Generiert den Given-Abschnitt."""
        lines = []
        for param in method.parameters:
            default = self._default_value(param.type)
            lines.append(f"    {param.type} {param.name} = {default};")
        return lines

    def _generate_when_section(
        self,
        method: JavaMethod,
        java_class: JavaClass
    ) -> List[str]:
        """Generiert den When-Abschnitt."""
        lines = []
        var_name = self._to_camel_case(java_class.name)
        params = ", ".join(p.name for p in method.parameters)

        if method.return_type and method.return_type != "void":
            lines.append(f"    {method.return_type} result = {var_name}.{method.name}({params});")
        else:
            lines.append(f"    {var_name}.{method.name}({params});")

        return lines

    def _generate_then_section(self, method: JavaMethod) -> List[str]:
        """Generiert den Then-Abschnitt."""
        lines = []

        if method.return_type and method.return_type != "void":
            if method.return_type == "boolean":
                lines.append("    assertTrue(result);")
            elif self._is_nullable(method.return_type):
                lines.append("    assertNotNull(result);")
            else:
                lines.append("    assertNotNull(result);")
        else:
            lines.append("    // Verify side effects or mock interactions")
            if self.config.style == TestStyle.MOCKITO:
                lines.append("    // verify(mockObject).someMethod(any());")

        return lines

    def _default_value(self, type_str: str) -> str:
        """Gibt einen Default-Wert für einen Typ zurück."""
        defaults = {
            "int": "0",
            "long": "0L",
            "double": "0.0",
            "float": "0.0f",
            "boolean": "false",
            "byte": "(byte) 0",
            "short": "(short) 0",
            "char": "'\\0'",
            "String": '"test"',
            "Integer": "1",
            "Long": "1L",
            "Double": "1.0",
            "Float": "1.0f",
            "Boolean": "true",
        }

        # Basis-Typ (ohne Generics)
        base_type = type_str.split("<")[0].strip()

        if base_type in defaults:
            return defaults[base_type]

        # Collections
        if "List" in base_type:
            return "new ArrayList<>()"
        if "Set" in base_type:
            return "new HashSet<>()"
        if "Map" in base_type:
            return "new HashMap<>()"

        # Optional
        if "Optional" in base_type:
            return "Optional.empty()"

        # Arrays
        if type_str.endswith("[]"):
            inner = type_str[:-2]
            return f"new {inner}[0]"

        # Default für Objekte
        return "null"

    def _is_nullable(self, type_str: str) -> bool:
        """Prüft ob ein Typ nullable ist (Referenztyp)."""
        primitives = {"int", "long", "double", "float", "boolean", "byte", "short", "char"}
        base_type = type_str.split("<")[0].strip()
        return base_type not in primitives

    def _to_camel_case(self, name: str) -> str:
        """Konvertiert zu camelCase."""
        if not name:
            return name
        return name[0].lower() + name[1:]

    def _capitalize(self, name: str) -> str:
        """Kapitalisiert den ersten Buchstaben."""
        if not name:
            return name
        return name[0].upper() + name[1:]


def get_junit_template_engine(config: Optional[TestConfig] = None) -> JUnitTemplateEngine:
    """Factory-Funktion für JUnitTemplateEngine."""
    return JUnitTemplateEngine(config)
