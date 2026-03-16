# Research Report: LiteLLM Tool Calling & MCP Integration

**Date:** 2026-03-16
**Query:** How tools are passed to LiteLLM client, MCP parameter options

---

## Executive Summary

LiteLLM uses a `tools` parameter in the `/chat/completions` endpoint, identical to OpenAI's format. The current AI-Assist implementation correctly passes tools via this parameter. **LiteLLM now supports a `type: "mcp"` tool type** that enables direct MCP server integration, which could simplify the architecture.

---

## Current Implementation (AI-Assist)

### How Tools Are Passed Today

**File:** `app/services/llm_client.py` (lines 357-448)

```python
async def chat_with_tools(
    self,
    messages: List[Dict],
    tools: Optional[List[Dict]] = None,  # <-- Tool definitions
    model: Optional[str] = None,
    tool_choice: str = "auto",
    ...
) -> LLMResponse:

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }

    if tools:
        payload["tools"] = tools           # <-- Added to payload
        payload["tool_choice"] = tool_choice

    response = await client.post(
        f"{self.base_url}/chat/completions",
        json=payload,
    )
```

### Tool Definition Format (OpenAI-compatible)

```python
tools = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"}
                },
                "required": ["path"]
            }
        }
    }
]
```

---

## LiteLLM MCP Integration (New Feature)

### Option 1: MCP Tool Type in `/chat/completions`

LiteLLM now supports `type: "mcp"` alongside `type: "function"`:

```python
tools = [
    {
        "type": "mcp",                    # <-- NEW: MCP type
        "server_label": "my-mcp-server",
        "server_url": "http://localhost:3000/mcp",
        "require_approval": "never"       # or "always"
    },
    {
        "type": "function",               # Traditional function tools
        "function": { ... }
    }
]

response = completion(
    model="gpt-4",
    messages=messages,
    tools=tools,
    tool_choice="auto"
)
```

### Option 2: URL-based MCP Tools (Anthropic models)

```python
tools = [
    {
        "type": "url",
        "url": "https://mcp.deepwiki.com/mcp",
        "name": "deepwiki-mcp"
    }
]
```

### Option 3: LiteLLM Proxy MCP Gateway

Configure MCP servers in `config.yaml`:

```yaml
model_list:
  - model_name: gpt-4
    litellm_params:
      model: openai/gpt-4

mcp_servers:
  - server_label: "context7"
    server_url: "http://localhost:3001/mcp"
```

Then use via `/v1/responses` endpoint:

```python
response = client.responses.create(
    model="gpt-4",
    input=[{"role": "user", "content": "..."}],
    tools=[
        {
            "type": "mcp",
            "server_label": "context7",
            "server_url": "litellm_proxy",  # Uses proxy-registered server
            "require_approval": "never"
        }
    ]
)
```

---

## Comparison: Current vs. MCP Integration

| Aspect | Current (function tools) | MCP Integration |
|--------|-------------------------|-----------------|
| Tool definitions | Defined in Python code | Defined by MCP server |
| Tool execution | Manual in orchestrator | Handled by LiteLLM/MCP |
| Tool updates | Requires code changes | Dynamic from MCP server |
| Complexity | Full control | Simplified |
| Architecture | Monolithic | Distributed |

---

## Recommendation

### Does MCP Parameter Make Sense?

**Yes, but with considerations:**

1. **Hybrid Approach Recommended**
   - Keep `type: "function"` for core tools (file ops, code analysis)
   - Add `type: "mcp"` for external integrations (Context7, web search)

2. **Implementation Path**
   ```python
   # Enhanced chat_with_tools
   async def chat_with_tools(
       self,
       messages: List[Dict],
       tools: Optional[List[Dict]] = None,
       mcp_servers: Optional[List[Dict]] = None,  # NEW
       ...
   ):
       payload = { ... }

       combined_tools = []
       if tools:
           combined_tools.extend(tools)
       if mcp_servers:
           for mcp in mcp_servers:
               combined_tools.append({
                   "type": "mcp",
                   "server_label": mcp["label"],
                   "server_url": mcp["url"],
                   "require_approval": mcp.get("approval", "never")
               })

       if combined_tools:
           payload["tools"] = combined_tools
   ```

3. **Benefits**
   - Reduces code for MCP tool bridging
   - Automatic tool discovery from MCP servers
   - Consistent with LiteLLM's direction

4. **Risks**
   - Requires LiteLLM proxy or compatible gateway
   - Less control over tool execution flow
   - May not work with all LLM providers

---

## Sources

- [LiteLLM Function Calling Documentation](https://docs.litellm.ai/docs/completion/function_call)
- [LiteLLM MCP Integration](https://docs.litellm.ai/docs/mcp)
- [LiteLLM MCP Usage Guide](https://docs.litellm.ai/docs/mcp_usage)
- [LiteLLM Input Parameters](https://docs.litellm.ai/docs/completion/input)
- [LiteLLM Anthropic Programmatic Tool Calling](https://docs.litellm.ai/docs/providers/anthropic_programmatic_tool_calling)
- [DeepWiki: Tool Calling and Function Integration](https://deepwiki.com/BerriAI/litellm/8.1-tool-calling-and-function-integration)

---

## Next Steps

1. **Evaluate** if LiteLLM proxy is deployed or planned
2. **Prototype** MCP tool type with one external service (e.g., Context7)
3. **Design** hybrid tool configuration in settings
4. **Implement** via `/sc:design` then `/sc:implement`
