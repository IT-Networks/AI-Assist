"""
Tests für JUnit Test Generator Tools.

Testet:
- Java Analyzer (Klassen-Parsing)
- Test Finder (Test-Verzeichnis-Erkennung)
- JUnit Templates (Test-Generierung)
"""

import tempfile
from pathlib import Path

import pytest


class TestJavaAnalyzer:
    """Tests für JavaAnalyzer."""

    def test_import_java_analyzer(self):
        """JavaAnalyzer kann importiert werden."""
        from app.utils.java_analyzer import JavaAnalyzer, JavaClass, JavaMethod
        assert JavaAnalyzer is not None
        assert JavaClass is not None
        assert JavaMethod is not None

    def test_parse_simple_class(self):
        """Einfache Klasse parsen."""
        from app.utils.java_analyzer import get_java_analyzer

        java_code = """
package com.example;

public class UserService {
    private UserRepository repository;

    public User findById(Long id) {
        return repository.findById(id);
    }

    public void createUser(String name, String email) {
        // create user
    }
}
"""
        analyzer = get_java_analyzer()
        java_class = analyzer.parse_content(java_code)

        assert java_class is not None
        assert java_class.name == "UserService"
        assert java_class.package == "com.example"
        assert len(java_class.methods) == 2

    def test_parse_class_with_extends(self):
        """Klasse mit Vererbung parsen."""
        from app.utils.java_analyzer import get_java_analyzer

        java_code = """
package com.example;

public class AdminService extends UserService implements Auditable {
    public void deleteUser(Long id) {}
}
"""
        analyzer = get_java_analyzer()
        java_class = analyzer.parse_content(java_code)

        assert java_class.name == "AdminService"
        assert java_class.extends == "UserService"
        assert "Auditable" in java_class.implements

    def test_parse_method_parameters(self):
        """Methoden-Parameter extrahieren."""
        from app.utils.java_analyzer import get_java_analyzer

        java_code = """
public class Calculator {
    public int add(int a, int b) {
        return a + b;
    }
}
"""
        analyzer = get_java_analyzer()
        java_class = analyzer.parse_content(java_code)

        method = java_class.methods[0]
        assert method.name == "add"
        assert method.return_type == "int"
        assert len(method.parameters) == 2
        assert method.parameters[0].name == "a"
        assert method.parameters[0].type == "int"

    def test_get_testable_methods(self):
        """Testbare Methoden identifizieren."""
        from app.utils.java_analyzer import get_java_analyzer

        java_code = """
public class Service {
    public void publicMethod() {}
    private void privateMethod() {}
    public static void main(String[] args) {}
    protected void protectedMethod() {}
}
"""
        analyzer = get_java_analyzer()
        java_class = analyzer.parse_content(java_code)

        testable = java_class.get_testable_methods()
        names = [m.name for m in testable]

        assert "publicMethod" in names
        assert "privateMethod" not in names
        assert "main" not in names  # static main excluded

    def test_get_dependencies(self):
        """Dependencies für Mocking extrahieren."""
        from app.utils.java_analyzer import get_java_analyzer

        java_code = """
public class OrderService {
    private UserRepository userRepository;
    private EmailService emailService;
    private static final Logger log = Logger.getLogger();
    private String configValue;
}
"""
        analyzer = get_java_analyzer()
        java_class = analyzer.parse_content(java_code)

        deps = java_class.get_dependencies()

        assert "UserRepository" in deps
        assert "EmailService" in deps
        # static/final excluded
        assert "Logger" not in deps
        # String is primitive-like
        assert "String" not in deps


class TestTestFinder:
    """Tests für TestDirectoryFinder."""

    def test_import_test_finder(self):
        """TestDirectoryFinder kann importiert werden."""
        from app.utils.test_finder import TestDirectoryFinder, get_test_finder
        assert TestDirectoryFinder is not None
        assert get_test_finder is not None

    def test_find_test_root_maven(self):
        """Maven Test-Verzeichnis finden."""
        from app.utils.test_finder import get_test_finder

        with tempfile.TemporaryDirectory() as tmpdir:
            # Maven Struktur erstellen
            test_dir = Path(tmpdir) / "src" / "test" / "java"
            test_dir.mkdir(parents=True)

            finder = get_test_finder(tmpdir)
            found = finder.find_test_root()

            assert found is not None
            assert "src/test/java" in str(found).replace("\\", "/")

    def test_find_test_root_simple(self):
        """Einfaches test/ Verzeichnis finden."""
        from app.utils.test_finder import get_test_finder

        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = Path(tmpdir) / "test"
            test_dir.mkdir()

            finder = get_test_finder(tmpdir)
            found = finder.find_test_root()

            assert found is not None
            assert found.name == "test"

    def test_get_test_path_for_source(self):
        """Test-Pfad für Source-Datei berechnen."""
        from app.utils.test_finder import get_test_finder

        with tempfile.TemporaryDirectory() as tmpdir:
            # Maven Struktur
            src_dir = Path(tmpdir) / "src" / "main" / "java" / "com" / "example"
            src_dir.mkdir(parents=True)
            src_file = src_dir / "UserService.java"
            src_file.write_text("public class UserService {}")

            finder = get_test_finder(tmpdir)
            test_path, package = finder.get_test_path_for_source(str(src_file))

            assert "UserServiceTest.java" in str(test_path)
            assert "com.example" in package or "com/example" in str(test_path)

    def test_detect_junit_version_junit5(self):
        """JUnit 5 aus pom.xml erkennen."""
        from app.utils.test_finder import get_test_finder

        with tempfile.TemporaryDirectory() as tmpdir:
            pom = Path(tmpdir) / "pom.xml"
            pom.write_text("""
<project>
    <dependencies>
        <dependency>
            <groupId>org.junit.jupiter</groupId>
            <artifactId>junit-jupiter</artifactId>
            <version>5.9.0</version>
        </dependency>
    </dependencies>
</project>
""")

            finder = get_test_finder(tmpdir)
            version = finder.get_junit_version()

            assert version == "5"

    def test_detect_junit_version_junit4(self):
        """JUnit 4 aus pom.xml erkennen."""
        from app.utils.test_finder import get_test_finder

        with tempfile.TemporaryDirectory() as tmpdir:
            pom = Path(tmpdir) / "pom.xml"
            pom.write_text("""
<project>
    <dependencies>
        <dependency>
            <groupId>junit</groupId>
            <artifactId>junit</artifactId>
            <version>4.13.2</version>
        </dependency>
    </dependencies>
</project>
""")

            finder = get_test_finder(tmpdir)
            version = finder.get_junit_version()

            assert version == "4"


class TestJUnitTemplates:
    """Tests für JUnitTemplateEngine."""

    def test_import_templates(self):
        """Template-Klassen können importiert werden."""
        from app.utils.junit_templates import (
            JUnitTemplateEngine,
            TestConfig,
            JUnitVersion,
            TestStyle,
        )
        assert JUnitTemplateEngine is not None
        assert TestConfig is not None

    def test_generate_simple_test_class(self):
        """Einfache Test-Klasse generieren."""
        from app.utils.java_analyzer import get_java_analyzer
        from app.utils.junit_templates import get_junit_template_engine, TestConfig, JUnitVersion

        java_code = """
public class Calculator {
    public int add(int a, int b) {
        return a + b;
    }
}
"""
        analyzer = get_java_analyzer()
        java_class = analyzer.parse_content(java_code)

        config = TestConfig(version=JUnitVersion.JUNIT5)
        engine = get_junit_template_engine(config)
        test_code = engine.generate_test_class(java_class)

        assert "class CalculatorTest" in test_code
        assert "@Test" in test_code
        assert "testAdd" in test_code or "add" in test_code

    def test_generate_junit4_test(self):
        """JUnit 4 Test generieren."""
        from app.utils.java_analyzer import get_java_analyzer
        from app.utils.junit_templates import get_junit_template_engine, TestConfig, JUnitVersion

        java_code = """
public class Service {
    public void process() {}
}
"""
        analyzer = get_java_analyzer()
        java_class = analyzer.parse_content(java_code)

        config = TestConfig(version=JUnitVersion.JUNIT4)
        engine = get_junit_template_engine(config)
        test_code = engine.generate_test_class(java_class)

        assert "import org.junit.Test" in test_code
        assert "public void" in test_code

    def test_generate_junit5_test(self):
        """JUnit 5 Test generieren."""
        from app.utils.java_analyzer import get_java_analyzer
        from app.utils.junit_templates import get_junit_template_engine, TestConfig, JUnitVersion

        java_code = """
public class Service {
    public void process() {}
}
"""
        analyzer = get_java_analyzer()
        java_class = analyzer.parse_content(java_code)

        config = TestConfig(version=JUnitVersion.JUNIT5)
        engine = get_junit_template_engine(config)
        test_code = engine.generate_test_class(java_class)

        assert "import org.junit.jupiter.api.Test" in test_code
        assert "void test" in test_code or "@Test" in test_code

    def test_generate_mockito_test(self):
        """Mockito Test generieren."""
        from app.utils.java_analyzer import get_java_analyzer
        from app.utils.junit_templates import (
            get_junit_template_engine,
            TestConfig,
            JUnitVersion,
            TestStyle,
        )

        java_code = """
public class OrderService {
    private UserRepository userRepository;

    public Order createOrder(Long userId) {
        return null;
    }
}
"""
        analyzer = get_java_analyzer()
        java_class = analyzer.parse_content(java_code)

        config = TestConfig(version=JUnitVersion.JUNIT5, style=TestStyle.MOCKITO)
        engine = get_junit_template_engine(config)
        test_code = engine.generate_test_class(java_class)

        assert "@Mock" in test_code
        assert "@InjectMocks" in test_code
        assert "MockitoExtension" in test_code

    def test_given_when_then_structure(self):
        """Given-When-Then Struktur in Tests."""
        from app.utils.java_analyzer import get_java_analyzer
        from app.utils.junit_templates import get_junit_template_engine, TestConfig

        java_code = """
public class Calculator {
    public int multiply(int a, int b) {
        return a * b;
    }
}
"""
        analyzer = get_java_analyzer()
        java_class = analyzer.parse_content(java_code)

        config = TestConfig(use_given_when_then=True)
        engine = get_junit_template_engine(config)
        test_code = engine.generate_test_class(java_class)

        assert "// Given" in test_code
        assert "// When" in test_code
        assert "// Then" in test_code


class TestJavaMethodProperties:
    """Tests für JavaMethod Eigenschaften."""

    def test_method_signature(self):
        """Methoden-Signatur generieren."""
        from app.utils.java_analyzer import JavaMethod, JavaParameter

        method = JavaMethod(
            name="findById",
            return_type="User",
            parameters=[JavaParameter(name="id", type="Long")],
        )

        assert method.signature == "User findById(Long id)"

    def test_method_is_testable_public(self):
        """Public Methode ist testbar."""
        from app.utils.java_analyzer import JavaMethod

        method = JavaMethod(name="process", return_type="void", is_public=True)
        assert method.is_testable is True

    def test_method_is_testable_private(self):
        """Private Methode ist nicht testbar."""
        from app.utils.java_analyzer import JavaMethod

        method = JavaMethod(name="helper", return_type="void", is_public=False)
        assert method.is_testable is False

    def test_constructor_is_testable(self):
        """Konstruktor ist testbar."""
        from app.utils.java_analyzer import JavaMethod

        ctor = JavaMethod(name="Service", return_type="", is_constructor=True, is_public=True)
        assert ctor.is_testable is True


class TestJavaClassProperties:
    """Tests für JavaClass Eigenschaften."""

    def test_full_name(self):
        """Vollqualifizierter Klassenname."""
        from app.utils.java_analyzer import JavaClass

        java_class = JavaClass(name="UserService", package="com.example.service")
        assert java_class.full_name == "com.example.service.UserService"

    def test_test_class_name(self):
        """Test-Klassenname generieren."""
        from app.utils.java_analyzer import JavaClass

        java_class = JavaClass(name="UserService")
        assert java_class.test_class_name == "UserServiceTest"
