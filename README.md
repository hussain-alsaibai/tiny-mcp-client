# tiny-mcp-client

> Zero-dependency Model Context Protocol client. Stdlib only · MIT · One file.

Connect to **any MCP server** — Claude Desktop, Cursor, Cline, custom servers — over stdio (local subprocess) or SSE/HTTP.

```bash
pip install tiny-mcp-client
# or: copy tiny_mcp_client.py into your project
```

## Features

- **Stdio transport** — Run MCP servers as subprocesses, communicate over stdin/stdout
- **SSE/HTTP transport** — Connect to remote MCP servers over HTTP with Server-Sent Events
- **Tool calls** — Call any MCP tool with typed arguments
- **Resource reading** — Read MCP resources by URI
- **Prompt rendering** — Get rendered prompts from MCP servers
- **Server introspection** — List tools, resources, and prompts automatically on connect
- **CLI included** — Inspect any MCP server from the command line

## Quick Start

### Python API

```python
from tiny_mcp_client import MCPClient

# Connect to a local MCP server (stdio)
with MCPClient(["npx", "@modelcontextprotocol/server-filesystem", "./data"]) as client:
    print(f"Tools: {client.tool_names()}")
    
    # Call a tool
    result = client.call_tool("read_file", {"path": "/tmp/test.txt"})
    print(result.content[0]["text"])
    
    # Read a resource
    resource = client.read_resource("file:///tmp/test.txt")
    print(resource.contents[0]["text"])

# Connect to a remote MCP server (SSE)
client = MCPClient("http://localhost:3000", transport="sse")
client.start()
tools = client.list_tools()
print(f"Available tools: {[t.name for t in tools]}")
client.stop()
```

### CLI

```bash
# List tools from a filesystem MCP server
python tiny_mcp_client.py npx @modelcontextprotocol/server-filesystem ./data --tools

# Call a specific tool
python tiny_mcp_client.py npx @modelcontextprotocol/server-filesystem ./data \
  --call read_file --args '{"path": "/tmp/foo"}'

# List resources
python tiny_mcp_client.py npx @modelcontextprotocol/server-filesystem ./data --resources --json
```

## Supported Providers

| Provider | Transport | Auth | Notes |
|----------|-----------|------|-------|
| Claude Desktop | Stdio | — | Use `claude_desktop_sse.py` path from config |
| Cursor | Stdio | — | MCP server command from settings |
| Cline | Stdio | — | `npx @modelcontextprotocol/server-filesystem` |
| Custom servers | Stdio or SSE | Any | Pass any command or URL |

## API Reference

### `MCPClient`

```python
client = MCPClient(
    server=["npx", "@mcp/server", "./data"],  # command list (stdio) or URL (SSE)
    transport="stdio",  # or "sse"
    cwd=None,           # working dir for subprocess
    env=None,           # extra env vars
    headers=None,       # HTTP headers for SSE
    timeout=30,         # seconds per call
)
client.start()          # Initialize + discover tools/resources/prompts
client.stop()           # Stop transport

# Tools
client.list_tools()     # → list[Tool]
client.call_tool(name, args)  # → ToolResult
client.find_tool(name)  # → Tool | None

# Resources
client.list_resources()  # → list[Resource]
client.read_resource(uri)   # → ResourceResult

# Prompts
client.list_prompts()    # → list[Prompt]
client.get_prompt(name, args)  # → dict

# Properties
client.tools            # List[Tool]
client.resources        # List[Resource]
client.prompts          # List[Prompt]
```

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Your Python Code / CLI                          │
│  MCPClient                                       │
│  ├─ stdio → StdioTransport → subprocess          │
│  └─ http → SSETransport → HTTP/SSE server        │
│                                                 │
│  JSON-RPC 2.0 over stdin/stdout or HTTP/SSE    │
│                                                 │
│  MCP Protocol: initialize, tools/list,           │
│  tools/call, resources/read, prompts/get        │
└─────────────────────────────────────────────────┘
```

## Why?

Most MCP client libraries are framework-shaped (LangChain MCP, modelcontextprotocol SDK). `tiny-mcp-client` is **one file, zero dependencies**, and works anywhere Python runs. No Node.js, no npm, no framework.

## Part of the tiny-* Ecosystem

```
tiny-mcp         — MCP server  (this repo)
tiny-mcp-client  — MCP client  (you are here)
tiny-agent       — Minimal agent framework
tiny-router      — HTTP routing
tiny-log         — Structured logging
tiny-validator   — Input validation
fast-cache       — LRU+TTL cache
```

See all 35+ repos at: [github.com/hussain-alsaibai](https://github.com/hussain-alsaibai)

## License

MIT — do anything with it. Built by [OpenClaw 🦞](https://github.com/hussain-alsaibai)
