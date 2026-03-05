from pathlib import Path
from typing import Dict, List, Optional

from lxml import etree

from app.core.exceptions import JavaReaderError

NS = {"m": "http://maven.apache.org/POM/4.0.0"}


def _find_text(element, xpath: str, ns=NS) -> str:
    result = element.find(xpath, ns)
    if result is None:
        # Try without namespace
        tag = xpath.split(":")[-1].replace("m/", "").replace("m:", "")
        result = element.find(tag)
    return result.text.strip() if result is not None and result.text else ""


class PomParser:
    def parse(self, pom_path: str) -> Dict:
        try:
            tree = etree.parse(pom_path)
            root = tree.getroot()
        except Exception as e:
            raise JavaReaderError(f"POM konnte nicht geparst werden: {e}")

        # Strip namespace for easier parsing
        for elem in root.iter():
            if "}" in elem.tag:
                elem.tag = elem.tag.split("}")[1]

        def find(tag, parent=None):
            node = (parent or root).find(tag)
            return node.text.strip() if node is not None and node.text else ""

        def find_all(tag, parent=None):
            return (parent or root).findall(tag)

        project_info = {
            "group_id": find("groupId") or find("groupId", root.find("parent")),
            "artifact_id": find("artifactId"),
            "version": find("version") or find("version", root.find("parent")),
            "packaging": find("packaging") or "jar",
            "name": find("name"),
            "description": find("description"),
        }

        # Parent POM
        parent_node = root.find("parent")
        parent_info = None
        if parent_node is not None:
            parent_info = {
                "group_id": find("groupId", parent_node),
                "artifact_id": find("artifactId", parent_node),
                "version": find("version", parent_node),
            }

        # Dependencies
        dependencies = []
        deps_node = root.find("dependencies")
        if deps_node is not None:
            for dep in deps_node.findall("dependency"):
                scope = find("scope", dep) or "compile"
                dependencies.append({
                    "group_id": find("groupId", dep),
                    "artifact_id": find("artifactId", dep),
                    "version": find("version", dep) or "managed",
                    "scope": scope,
                })

        # Properties
        props_node = root.find("properties")
        properties = {}
        if props_node is not None:
            for prop in props_node:
                if prop.text:
                    properties[prop.tag] = prop.text.strip()

        return {
            "project": project_info,
            "parent": parent_info,
            "dependencies": dependencies,
            "properties": properties,
            "path": pom_path,
        }

    def format_for_context(self, pom_data: Dict) -> str:
        p = pom_data["project"]
        lines = [
            f"Maven Projekt: {p['group_id']}:{p['artifact_id']}:{p['version']}",
            f"Typ: {p['packaging']}",
        ]
        if p.get("name"):
            lines.append(f"Name: {p['name']}")
        if pom_data.get("parent"):
            par = pom_data["parent"]
            lines.append(f"Parent: {par['group_id']}:{par['artifact_id']}:{par['version']}")

        lines.append("\nAbhängigkeiten:")
        for dep in pom_data["dependencies"]:
            scope_str = f" [{dep['scope']}]" if dep["scope"] != "compile" else ""
            lines.append(f"  - {dep['group_id']}:{dep['artifact_id']}:{dep['version']}{scope_str}")

        if pom_data.get("properties"):
            lines.append("\nEigenschaften:")
            for k, v in list(pom_data["properties"].items())[:10]:
                lines.append(f"  {k} = {v}")

        return "\n".join(lines)
