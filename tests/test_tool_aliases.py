"""
Unit-Tests für den Alias-Mechanismus der ToolRegistry.

Teil der Lokal-Tool-Konsolidierung (Phase 1):
siehe claudedocs/plan_local_tool_consolidation_2026-04-23.md
"""

import pytest

from app.agent.tools import (
    AliasInfo,
    Tool,
    ToolCategory,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)


async def _echo_handler(**kwargs) -> ToolResult:
    return ToolResult(success=True, data=f"echo:{kwargs}")


def _make_tool(name: str = "canonical") -> Tool:
    return Tool(
        name=name,
        description=f"Test-Tool {name}",
        category=ToolCategory.FILE,
        parameters=[
            ToolParameter(name="value", type="string", description="test",
                          required=False),
        ],
        handler=_echo_handler,
    )


class TestAliasRegistration:
    def test_register_alias_to_existing_tool(self):
        reg = ToolRegistry()
        reg.register(_make_tool("canonical"))
        reg.register_alias("legacy_name", "canonical")

        assert "legacy_name" in reg.aliases
        assert reg.aliases["legacy_name"].target == "canonical"

    def test_register_alias_to_missing_tool_raises(self):
        reg = ToolRegistry()
        with pytest.raises(ValueError, match="Alias-Ziel"):
            reg.register_alias("legacy", "does_not_exist")

    def test_register_alias_colliding_with_tool_raises(self):
        reg = ToolRegistry()
        reg.register(_make_tool("canonical"))
        reg.register(_make_tool("other"))
        with pytest.raises(ValueError, match="kollidiert"):
            reg.register_alias("other", "canonical")

    def test_alias_with_deprecation_msg(self):
        reg = ToolRegistry()
        reg.register(_make_tool("canonical"))
        reg.register_alias("legacy", "canonical",
                           deprecation_msg="Bitte 'canonical' verwenden")

        info = reg.aliases["legacy"]
        assert info.deprecation_msg == "Bitte 'canonical' verwenden"
        assert info.surface_to_llm is False

    def test_alias_info_timestamp_is_set(self):
        info = AliasInfo(target="x")
        assert info.registered_at  # nicht leer


class TestResolveName:
    def test_resolve_canonical_name_returns_self(self):
        reg = ToolRegistry()
        reg.register(_make_tool("canonical"))
        name, info = reg.resolve_name("canonical")
        assert name == "canonical"
        assert info is None

    def test_resolve_alias_returns_target(self):
        reg = ToolRegistry()
        reg.register(_make_tool("canonical"))
        reg.register_alias("legacy", "canonical")
        name, info = reg.resolve_name("legacy")
        assert name == "canonical"
        assert info is not None
        assert info.target == "canonical"

    def test_resolve_unknown_returns_input(self):
        reg = ToolRegistry()
        name, info = reg.resolve_name("unknown_tool")
        assert name == "unknown_tool"
        assert info is None


class TestExecuteWithAlias:
    @pytest.mark.asyncio
    async def test_execute_canonical_name(self):
        reg = ToolRegistry()
        reg.register(_make_tool("canonical"))
        result = await reg.execute("canonical", value="hello")
        assert result.success
        assert "hello" in result.data

    @pytest.mark.asyncio
    async def test_execute_via_alias_routes_to_target(self):
        reg = ToolRegistry()
        reg.register(_make_tool("canonical"))
        reg.register_alias("legacy", "canonical")
        result = await reg.execute("legacy", value="world")
        assert result.success
        assert "world" in result.data

    @pytest.mark.asyncio
    async def test_alias_hits_are_counted(self):
        reg = ToolRegistry()
        reg.register(_make_tool("canonical"))
        reg.register_alias("legacy", "canonical")

        assert reg.alias_hits.get("legacy", 0) == 0
        await reg.execute("legacy", value="a")
        await reg.execute("legacy", value="b")
        await reg.execute("canonical", value="c")  # nicht zählen
        assert reg.alias_hits["legacy"] == 2

    @pytest.mark.asyncio
    async def test_surface_to_llm_prepends_hint(self):
        reg = ToolRegistry()
        reg.register(_make_tool("canonical"))
        reg.register_alias(
            "legacy", "canonical",
            deprecation_msg="Verwende 'canonical'",
            surface_to_llm=True,
        )
        result = await reg.execute("legacy", value="x")
        assert result.success
        assert result.data.startswith("[Verwende 'canonical']")

    @pytest.mark.asyncio
    async def test_surface_false_does_not_prepend(self):
        reg = ToolRegistry()
        reg.register(_make_tool("canonical"))
        reg.register_alias("legacy", "canonical", surface_to_llm=False)
        result = await reg.execute("legacy", value="x")
        assert result.success
        assert not result.data.startswith("[")


class TestSchemaExclusion:
    def test_aliases_not_in_openai_schemas(self):
        reg = ToolRegistry()
        reg.register(_make_tool("canonical"))
        reg.register_alias("legacy_one", "canonical")
        reg.register_alias("legacy_two", "canonical")

        schemas = reg.get_openai_schemas(include_write_ops=True)
        names = [s["function"]["name"] for s in schemas]
        assert "canonical" in names
        assert "legacy_one" not in names
        assert "legacy_two" not in names

    def test_get_follows_alias(self):
        reg = ToolRegistry()
        reg.register(_make_tool("canonical"))
        reg.register_alias("legacy", "canonical")

        tool_via_alias = reg.get("legacy")
        tool_direct = reg.get("canonical")
        assert tool_via_alias is not None
        assert tool_via_alias is tool_direct


class TestArgMapper:
    """Phase-2-Alias mit Parameter-Transformation."""

    @pytest.mark.asyncio
    async def test_arg_mapper_injects_parameter(self):
        captured = {}

        async def capture_handler(**kwargs):
            captured.update(kwargs)
            return ToolResult(success=True, data="ok")

        reg = ToolRegistry()
        reg.register(Tool(
            name="search",
            description="search",
            category=ToolCategory.SEARCH,
            parameters=[
                ToolParameter("query", "string", "q"),
                ToolParameter("scope", "string", "s", required=False),
            ],
            handler=capture_handler,
        ))
        reg.register_alias(
            "search_code", "search",
            arg_mapper=lambda kw: {**kw, "scope": "code"},
        )
        await reg.execute("search_code", query="foo")
        assert captured["query"] == "foo"
        assert captured["scope"] == "code"

    @pytest.mark.asyncio
    async def test_arg_mapper_renames_parameter(self):
        captured = {}

        async def capture_handler(**kwargs):
            captured.update(kwargs)
            return ToolResult(success=True, data="ok")

        reg = ToolRegistry()
        reg.register(Tool(
            name="read",
            description="read",
            category=ToolCategory.FILE,
            parameters=[
                ToolParameter("path", "string", "p"),
                ToolParameter("pages", "string", "pg", required=False),
            ],
            handler=capture_handler,
        ))

        def mapper(kw):
            filename = kw.pop("filename", None)
            start = kw.pop("start_page", None)
            end = kw.pop("end_page", None)
            if filename:
                kw["path"] = filename
            if start and end:
                kw["pages"] = f"{start}-{end}"
            return kw

        reg.register_alias("read_pdf_pages", "read", arg_mapper=mapper)
        await reg.execute("read_pdf_pages", filename="doc.pdf",
                          start_page=1, end_page=5)
        assert captured["path"] == "doc.pdf"
        assert captured["pages"] == "1-5"
        assert "filename" not in captured
        assert "start_page" not in captured

    @pytest.mark.asyncio
    async def test_arg_mapper_exception_returns_error(self):
        reg = ToolRegistry()
        reg.register(_make_tool("canonical"))

        def broken_mapper(kw):
            raise RuntimeError("mapper exploded")

        reg.register_alias("legacy", "canonical", arg_mapper=broken_mapper)
        result = await reg.execute("legacy", value="x")
        assert not result.success
        assert "mapper exploded" in result.error


class TestPhase2Integration:
    """Integrationstest: echte Default-Registry mit Phase-2-Aliasen."""

    def test_core_tools_registered(self):
        from app.agent.tools import get_tool_registry
        reg = get_tool_registry()
        for name in ("read", "write", "edit", "ls", "glob", "grep", "search"):
            assert name in reg.tools, f"Core-Tool '{name}' fehlt"

    def test_legacy_names_are_aliases(self):
        from app.agent.tools import get_tool_registry
        reg = get_tool_registry()
        legacy = [
            "read_file", "read_pdf_pages", "get_pdf_info", "read_sqlj_file",
            "write_file", "create_directory", "edit_file",
            "list_files", "glob_files", "grep_content",
            "search_code", "search_handbook", "search_skills", "search_pdf",
        ]
        for name in legacy:
            assert name in reg.aliases, f"Alias '{name}' fehlt"
            assert name not in reg.tools, f"Legacy-Tool '{name}' sollte kein Tool sein"

    def test_legacy_names_not_in_schemas(self):
        from app.agent.tools import get_tool_registry
        reg = get_tool_registry()
        schemas = reg.get_openai_schemas(include_write_ops=True)
        names = [s["function"]["name"] for s in schemas]
        for legacy in ("read_file", "write_file", "search_code", "search_pdf"):
            assert legacy not in names, f"{legacy} sollte nicht im Schema sein"

    def test_aliases_have_deprecation_msgs(self):
        from app.agent.tools import get_tool_registry
        reg = get_tool_registry()
        for alias, info in reg.aliases.items():
            assert info.deprecation_msg, f"Alias '{alias}' hat keine Deprecation-Message"
            assert info.surface_to_llm is False, \
                f"Alias '{alias}' sollte surface_to_llm=False haben (Log-only)"


class TestUnregister:
    def test_unregister_existing_tool(self):
        reg = ToolRegistry()
        reg.register(_make_tool("t1"))
        assert "t1" in reg.tools
        assert reg.unregister("t1") is True
        assert "t1" not in reg.tools

    def test_unregister_missing_returns_false(self):
        reg = ToolRegistry()
        assert reg.unregister("does_not_exist") is False

    def test_register_alias_replace_existing(self):
        reg = ToolRegistry()
        reg.register(_make_tool("new_canonical"))
        reg.register(_make_tool("legacy"))  # separat
        # Ohne replace_existing schlägt es fehl
        with pytest.raises(ValueError, match="kollidiert"):
            reg.register_alias("legacy", "new_canonical")
        # Mit replace_existing wird Legacy entfernt, Alias registriert
        reg.register_alias(
            "legacy", "new_canonical", replace_existing=True,
        )
        assert "legacy" in reg.aliases
        assert "legacy" not in reg.tools


class TestPhase3Integration:
    """Phase 3: bash / bash_sessions / exec_python als Unified-Core."""

    def test_phase3_core_tools_registered(self):
        from app.agent.tools import get_tool_registry
        reg = get_tool_registry()
        for name in ("bash", "bash_sessions", "exec_python"):
            assert name in reg.tools, f"Phase-3-Core-Tool '{name}' fehlt"

    def test_phase3_legacy_aliased(self):
        from app.agent.tools import get_tool_registry
        reg = get_tool_registry()
        mapping = {
            "shell_execute": "bash",
            "shell_execute_local": "bash",
            "run_workspace_command": "bash",
            "shell_list_executions": "bash_sessions",
            "docker_session_list": "bash_sessions",
            "docker_session_close": "bash_sessions",
            "docker_upload_file": "bash_sessions",
            "docker_execute_python": "exec_python",
            "docker_session_execute": "exec_python",
            "generate_and_execute_python_script": "exec_python",
        }
        for alias, expected_target in mapping.items():
            assert alias in reg.aliases, f"Alias '{alias}' fehlt"
            assert reg.aliases[alias].target == expected_target, \
                f"'{alias}' sollte auf '{expected_target}' zeigen"
            assert alias not in reg.tools, \
                f"Legacy-Tool '{alias}' sollte aus LLM-Schema entfernt sein"

    def test_named_script_lifecycle_preserved(self):
        """User-Entscheidung: Named-Script-CRUD bleibt in Phase 3 erhalten."""
        from app.agent.tools import get_tool_registry
        reg = get_tool_registry()
        for name in ("generate_python_script", "execute_python_script",
                     "validate_python_script", "list_python_scripts",
                     "delete_python_script"):
            assert name in reg.tools, \
                f"Named-Script-Tool '{name}' wurde fälschlich entfernt"
            assert name not in reg.aliases, \
                f"Named-Script-Tool '{name}' sollte kein Alias sein"


class TestPhase3Dispatch:
    """Dispatch-Tests für bash / bash_sessions / exec_python Handler."""

    @pytest.mark.asyncio
    async def test_bash_unknown_sandbox_returns_error(self):
        from app.agent.tools import bash
        result = await bash(command="ls", sandbox="moon")
        assert not result.success
        assert "sandbox" in result.error.lower()

    @pytest.mark.asyncio
    async def test_bash_local_without_execution_id(self):
        from app.agent.tools import bash
        result = await bash(command="ls", sandbox="local")
        assert not result.success
        assert "execution_id" in result.error.lower()

    @pytest.mark.asyncio
    async def test_bash_workspace_requires_cwd(self):
        from app.agent.tools import bash
        result = await bash(command="python --version", sandbox="workspace")
        assert not result.success
        assert "cwd" in result.error.lower()

    @pytest.mark.asyncio
    async def test_bash_workspace_empty_command(self):
        from app.agent.tools import bash
        result = await bash(command="", sandbox="workspace", cwd="C:/tmp")
        assert not result.success
        assert "leer" in result.error.lower()

    @pytest.mark.asyncio
    async def test_bash_sessions_unknown_action(self):
        from app.agent.tools import bash_sessions
        result = await bash_sessions(action="teleport")
        assert not result.success
        assert "action" in result.error.lower()

    @pytest.mark.asyncio
    async def test_bash_sessions_close_without_id(self):
        from app.agent.tools import bash_sessions
        result = await bash_sessions(action="close")
        assert not result.success
        assert "session_id" in result.error.lower()

    @pytest.mark.asyncio
    async def test_bash_sessions_upload_missing_args(self):
        from app.agent.tools import bash_sessions
        result = await bash_sessions(action="upload", session_id="s1")
        assert not result.success
        assert ("filename" in result.error.lower() or
                "content_base64" in result.error.lower())

    @pytest.mark.asyncio
    async def test_exec_python_empty_code(self):
        from app.agent.tools import exec_python
        result = await exec_python(code="")
        assert not result.success
        assert "leer" in result.error.lower()

    @pytest.mark.asyncio
    async def test_exec_python_syntax_error(self):
        from app.agent.tools import exec_python
        result = await exec_python(code="def broken(:\n    pass")
        assert not result.success
        assert "syntax" in result.error.lower()

    @pytest.mark.asyncio
    async def test_exec_python_valid_syntax_passes_check(self):
        """Compile-Check darf bei valider Syntax nicht blockieren.

        (Der Docker-Container selbst ist in Tests meist nicht verfügbar —
        deshalb akzeptieren wir success=False mit nicht-Syntax-Fehler.)
        """
        from app.agent.tools import exec_python
        result = await exec_python(code="print('ok')")
        # Syntax war gültig — Fehler darf NUR von der Exec-Runtime kommen, nicht vom Compile-Check
        if not result.success:
            assert "syntax" not in (result.error or "").lower()


class TestPhase3ArgMappers:
    """Alias-Parameter-Transformation für Phase 3."""

    @pytest.mark.asyncio
    async def test_shell_execute_alias_maps_working_dir(self):
        from app.agent.tools import get_tool_registry
        reg = get_tool_registry()
        _, info = reg.resolve_name("shell_execute")
        assert info is not None
        mapped = info.arg_mapper(
            {"command": "ls", "working_dir": "/tmp", "timeout": 30}
        )
        assert mapped["sandbox"] == "container"
        assert mapped["cwd"] == "/tmp"
        assert "working_dir" not in mapped

    @pytest.mark.asyncio
    async def test_run_workspace_command_alias_joins_command_list(self):
        from app.agent.tools import get_tool_registry
        reg = get_tool_registry()
        _, info = reg.resolve_name("run_workspace_command")
        assert info is not None
        mapped = info.arg_mapper({
            "path": "C:/proj",
            "command": ["python", "main.py", "--flag"],
            "timeout_seconds": 60,
        })
        assert mapped["sandbox"] == "workspace"
        assert mapped["cwd"] == "C:/proj"
        assert mapped["timeout"] == 60
        # Command wurde zu String joined; shlex.split wird es wieder aufdröseln
        import shlex
        parts = shlex.split(mapped["command"])
        assert parts == ["python", "main.py", "--flag"]

    @pytest.mark.asyncio
    async def test_docker_execute_python_alias_maps_packages(self):
        from app.agent.tools import get_tool_registry
        reg = get_tool_registry()
        _, info = reg.resolve_name("docker_execute_python")
        assert info is not None
        mapped = info.arg_mapper({
            "code": "print(1)",
            "packages": ["numpy"],
            "timeout": 60,
        })
        assert mapped["code"] == "print(1)"
        assert mapped["requirements"] == ["numpy"]
        assert "packages" not in mapped

    @pytest.mark.asyncio
    async def test_generate_and_execute_drops_lifecycle_params(self):
        from app.agent.tools import get_tool_registry
        reg = get_tool_registry()
        _, info = reg.resolve_name("generate_and_execute_python_script")
        assert info is not None
        mapped = info.arg_mapper({
            "code": "print(1)",
            "name": "foo",
            "description": "bar",
            "parameters": {"x": "int"},
            "requirements": ["numpy"],
            "execute_args": {"x": 1},
            "execute_input": "hello",
        })
        assert mapped["code"] == "print(1)"
        assert mapped["requirements"] == ["numpy"]
        for dropped in ("name", "description", "parameters",
                        "execute_args", "execute_input"):
            assert dropped not in mapped

    @pytest.mark.asyncio
    async def test_shell_list_executions_becomes_list_action(self):
        from app.agent.tools import get_tool_registry
        reg = get_tool_registry()
        _, info = reg.resolve_name("shell_list_executions")
        assert info is not None
        mapped = info.arg_mapper({})
        assert mapped == {"action": "list"}
