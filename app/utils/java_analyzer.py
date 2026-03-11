"""
Java Class Analyzer - Parst Java-Dateien mit javalang.

Extrahiert:
- Klassen und Interfaces
- Methoden mit Parametern und Return-Typen
- Felder
- Imports
- Annotations

Verwendet für: JUnit-Test-Generierung, Code-Analyse.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# javalang importieren
try:
    import javalang
    from javalang.tree import (
        ClassDeclaration,
        InterfaceDeclaration,
        MethodDeclaration,
        ConstructorDeclaration,
        FieldDeclaration,
        FormalParameter,
        Annotation,
    )
    JAVALANG_AVAILABLE = True
except ImportError:
    JAVALANG_AVAILABLE = False
    # Dummy-Klassen für Type Hints wenn javalang nicht installiert
    ClassDeclaration = type("ClassDeclaration", (), {})
    InterfaceDeclaration = type("InterfaceDeclaration", (), {})
    MethodDeclaration = type("MethodDeclaration", (), {})
    ConstructorDeclaration = type("ConstructorDeclaration", (), {})
    FieldDeclaration = type("FieldDeclaration", (), {})
    FormalParameter = type("FormalParameter", (), {})
    Annotation = type("Annotation", (), {})
    logger.warning("javalang nicht installiert - Java-Analyse eingeschränkt")


@dataclass
class JavaParameter:
    """Ein Methoden-Parameter."""
    name: str
    type: str
    is_varargs: bool = False
    annotations: List[str] = field(default_factory=list)


@dataclass
class JavaMethod:
    """Eine Java-Methode oder Konstruktor."""
    name: str
    return_type: str
    parameters: List[JavaParameter] = field(default_factory=list)
    modifiers: List[str] = field(default_factory=list)
    annotations: List[str] = field(default_factory=list)
    throws: List[str] = field(default_factory=list)
    is_constructor: bool = False
    is_static: bool = False
    is_public: bool = True
    javadoc: Optional[str] = None
    line_number: int = 0

    @property
    def is_testable(self) -> bool:
        """Prüft ob die Methode testbar ist (public, nicht static main)."""
        if not self.is_public:
            return False
        if self.is_constructor:
            return True
        if self.name == "main" and self.is_static:
            return False
        # Getter/Setter können getestet werden, aber mit niedrigerer Priorität
        return True

    @property
    def signature(self) -> str:
        """Gibt die Methoden-Signatur zurück."""
        params = ", ".join(f"{p.type} {p.name}" for p in self.parameters)
        ret = f"{self.return_type} " if self.return_type and self.return_type != "void" else ""
        return f"{ret}{self.name}({params})"


@dataclass
class JavaField:
    """Ein Klassen-Feld."""
    name: str
    type: str
    modifiers: List[str] = field(default_factory=list)
    annotations: List[str] = field(default_factory=list)
    is_static: bool = False
    is_final: bool = False


@dataclass
class JavaClass:
    """Eine geparste Java-Klasse oder Interface."""
    name: str
    package: str = ""
    file_path: str = ""
    imports: List[str] = field(default_factory=list)
    methods: List[JavaMethod] = field(default_factory=list)
    fields: List[JavaField] = field(default_factory=list)
    constructors: List[JavaMethod] = field(default_factory=list)
    annotations: List[str] = field(default_factory=list)
    is_interface: bool = False
    is_abstract: bool = False
    extends: Optional[str] = None
    implements: List[str] = field(default_factory=list)
    inner_classes: List["JavaClass"] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        """Vollqualifizierter Klassenname."""
        if self.package:
            return f"{self.package}.{self.name}"
        return self.name

    @property
    def test_class_name(self) -> str:
        """Name der Test-Klasse."""
        return f"{self.name}Test"

    def get_testable_methods(self) -> List[JavaMethod]:
        """Gibt alle testbaren Methoden zurück."""
        return [m for m in self.methods if m.is_testable]

    def get_dependencies(self) -> List[str]:
        """Extrahiert Abhängigkeiten aus Feldern für Mocking."""
        deps = []
        for f in self.fields:
            # Nicht primitive, nicht static, nicht final → wahrscheinlich injectable
            if (not f.is_static and
                not f.is_final and
                f.type not in ("int", "long", "double", "float", "boolean", "byte", "char", "short", "String")):
                deps.append(f.type)
        return deps


class JavaAnalyzer:
    """
    Analysiert Java-Dateien und extrahiert Strukturinformationen.

    Verwendet javalang für AST-Parsing.
    """

    def __init__(self):
        if not JAVALANG_AVAILABLE:
            raise ImportError("javalang ist nicht installiert. Bitte 'pip install javalang' ausführen.")

    def parse_file(self, file_path: str) -> Optional[JavaClass]:
        """
        Parst eine Java-Datei und gibt die Hauptklasse zurück.

        Args:
            file_path: Pfad zur Java-Datei

        Returns:
            JavaClass oder None bei Fehler
        """
        path = Path(file_path)
        if not path.exists():
            logger.error(f"Datei nicht gefunden: {file_path}")
            return None

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            result = self.parse_content(content)
            if result:
                result.file_path = str(path)
            return result
        except Exception as e:
            logger.error(f"Fehler beim Parsen von {file_path}: {e}")
            return None

    def parse_content(self, content: str) -> Optional[JavaClass]:
        """
        Parst Java-Quellcode und gibt die Hauptklasse zurück.

        Args:
            content: Java-Quellcode als String

        Returns:
            JavaClass oder None bei Fehler
        """
        try:
            tree = javalang.parse.parse(content)
        except javalang.parser.JavaSyntaxError as e:
            logger.error(f"Java Syntax-Fehler: {e}")
            return None
        except Exception as e:
            logger.error(f"Parse-Fehler: {e}")
            return None

        # Package extrahieren
        package = ""
        if tree.package:
            package = tree.package.name

        # Imports extrahieren
        imports = []
        for imp in tree.imports:
            import_path = imp.path
            if imp.wildcard:
                import_path += ".*"
            if imp.static:
                import_path = f"static {import_path}"
            imports.append(import_path)

        # Hauptklasse finden (erste public class oder erste class)
        main_class = None
        for type_decl in tree.types:
            if isinstance(type_decl, (ClassDeclaration, InterfaceDeclaration)):
                java_class = self._parse_class_declaration(type_decl, package, imports)
                if main_class is None:
                    main_class = java_class
                # Public class bevorzugen
                if "public" in (type_decl.modifiers or []):
                    main_class = java_class
                    break

        return main_class

    def _parse_class_declaration(
        self,
        decl: Any,
        package: str,
        imports: List[str]
    ) -> JavaClass:
        """Parst eine Klassen- oder Interface-Deklaration."""
        is_interface = isinstance(decl, InterfaceDeclaration)

        # Basis-Info
        java_class = JavaClass(
            name=decl.name,
            package=package,
            imports=imports,
            is_interface=is_interface,
        )

        # Modifiers
        modifiers = decl.modifiers or []
        java_class.is_abstract = "abstract" in modifiers

        # Annotations
        if decl.annotations:
            java_class.annotations = [self._annotation_to_str(a) for a in decl.annotations]

        # Extends
        if hasattr(decl, "extends") and decl.extends:
            if is_interface:
                # Interface kann mehrere erweitern
                if isinstance(decl.extends, list):
                    java_class.implements = [self._type_to_str(e) for e in decl.extends]
                else:
                    java_class.extends = self._type_to_str(decl.extends)
            else:
                java_class.extends = self._type_to_str(decl.extends)

        # Implements
        if hasattr(decl, "implements") and decl.implements:
            java_class.implements = [self._type_to_str(i) for i in decl.implements]

        # Body parsen
        if decl.body:
            for member in decl.body:
                if isinstance(member, MethodDeclaration):
                    method = self._parse_method(member)
                    java_class.methods.append(method)
                elif isinstance(member, ConstructorDeclaration):
                    constructor = self._parse_constructor(member, decl.name)
                    java_class.constructors.append(constructor)
                elif isinstance(member, FieldDeclaration):
                    fields = self._parse_field(member)
                    java_class.fields.extend(fields)
                elif isinstance(member, (ClassDeclaration, InterfaceDeclaration)):
                    inner = self._parse_class_declaration(member, package, imports)
                    java_class.inner_classes.append(inner)

        return java_class

    def _parse_method(self, method: MethodDeclaration) -> JavaMethod:
        """Parst eine Methoden-Deklaration."""
        modifiers = method.modifiers or []

        java_method = JavaMethod(
            name=method.name,
            return_type=self._type_to_str(method.return_type) if method.return_type else "void",
            modifiers=list(modifiers),
            is_static="static" in modifiers,
            is_public="public" in modifiers,
            is_constructor=False,
        )

        # Position
        if hasattr(method, "position") and method.position:
            java_method.line_number = method.position.line

        # Annotations
        if method.annotations:
            java_method.annotations = [self._annotation_to_str(a) for a in method.annotations]

        # Parameters
        if method.parameters:
            for param in method.parameters:
                java_method.parameters.append(self._parse_parameter(param))

        # Throws
        if method.throws:
            java_method.throws = [self._type_to_str(t) for t in method.throws]

        # Javadoc (aus documentation, falls vorhanden)
        if hasattr(method, "documentation") and method.documentation:
            java_method.javadoc = method.documentation

        return java_method

    def _parse_constructor(self, ctor: ConstructorDeclaration, class_name: str) -> JavaMethod:
        """Parst einen Konstruktor."""
        modifiers = ctor.modifiers or []

        java_method = JavaMethod(
            name=class_name,
            return_type="",
            modifiers=list(modifiers),
            is_static=False,
            is_public="public" in modifiers,
            is_constructor=True,
        )

        # Position
        if hasattr(ctor, "position") and ctor.position:
            java_method.line_number = ctor.position.line

        # Annotations
        if ctor.annotations:
            java_method.annotations = [self._annotation_to_str(a) for a in ctor.annotations]

        # Parameters
        if ctor.parameters:
            for param in ctor.parameters:
                java_method.parameters.append(self._parse_parameter(param))

        # Throws
        if ctor.throws:
            java_method.throws = [self._type_to_str(t) for t in ctor.throws]

        return java_method

    def _parse_parameter(self, param: FormalParameter) -> JavaParameter:
        """Parst einen Parameter."""
        annotations = []
        if param.annotations:
            annotations = [self._annotation_to_str(a) for a in param.annotations]

        return JavaParameter(
            name=param.name,
            type=self._type_to_str(param.type),
            is_varargs=param.varargs if hasattr(param, "varargs") else False,
            annotations=annotations,
        )

    def _parse_field(self, field_decl: FieldDeclaration) -> List[JavaField]:
        """Parst ein Feld (kann mehrere Variablen deklarieren)."""
        fields = []
        modifiers = field_decl.modifiers or []
        field_type = self._type_to_str(field_decl.type)

        annotations = []
        if field_decl.annotations:
            annotations = [self._annotation_to_str(a) for a in field_decl.annotations]

        for declarator in field_decl.declarators:
            fields.append(JavaField(
                name=declarator.name,
                type=field_type,
                modifiers=list(modifiers),
                annotations=annotations,
                is_static="static" in modifiers,
                is_final="final" in modifiers,
            ))

        return fields

    def _type_to_str(self, type_node: Any) -> str:
        """Konvertiert einen Typ-Knoten zu String."""
        if type_node is None:
            return "void"

        if isinstance(type_node, str):
            return type_node

        # Basis-Name
        name = getattr(type_node, "name", str(type_node))

        # Array-Dimensionen
        dimensions = getattr(type_node, "dimensions", None)
        if dimensions:
            name += "[]" * len(dimensions)

        # Generics
        arguments = getattr(type_node, "arguments", None)
        if arguments:
            args = ", ".join(self._type_to_str(a) for a in arguments)
            name += f"<{args}>"

        return name

    def _annotation_to_str(self, annotation: Annotation) -> str:
        """Konvertiert eine Annotation zu String."""
        name = annotation.name
        # Element-Werte könnten hinzugefügt werden, aber für Test-Generierung reicht der Name
        return f"@{name}"


def get_java_analyzer() -> JavaAnalyzer:
    """Factory-Funktion für JavaAnalyzer."""
    return JavaAnalyzer()
