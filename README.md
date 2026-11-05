# tiny-mcp-client

**Zero-dependency Model Context Protocol client for Python.** Connects to any MCP server — including [tiny-mcp-server](https://github.com/hussain-alsaibai/tiny-mcp-server) — via stdlib `http.client` + `json`. No external dependencies.

> **Part of the [tiny-\* ecosystem](https://github.com/hussain-alsaibai/tiny-mcp-server)** — tiny-mcp-server, tiny-mcp-registry, tiny-mcp-observability, and now tiny-mcp-client.

## Install

```bash
pip install tiny-mcp-client
```

Or copy `tiny_mcp_client.py` — it's one file, no dependencies.

## Features

| Feature | Description |
|---------|-------------|
| **Zero deps** | Stdlib only (`http.client`, `json`, `uuid`) — Python 3.8+ |
| **Async + Sync** | `MCPClient` (async) and `MCPSyncClient` (sync wrapper) |
| **Full MCP API** | Tools, resources, prompts, sampling |
| **Auth ready** | Bearer token via `api_key` parameter |
| **Timeout control** | Per-request timeout, `MCPTimeoutError` on breach |
| **Context manager** | `async with` lifecycle management |

## Quick Start

### Async

```python
import asyncio
from tiny_mcp_client import MCPClient

async def main():
    async with MCPClient("http://localhost:8765") as client:
        print(f"Server: {client.server_info.name} v{client.server_info.version}")

        # List available tools
        tools = await client.list_tools()
        for tool in tools:
            print(f"  {tool.name}: {tool.description}")

        # Call a tool
        result = await client.call_tool("fetch_url", {"url": "https://example.com"})
        print(result)

asyncio.run(main())
```

### Sync

```python
from tiny_mcp_client import MCPSyncClient

with MCPSyncClient("http://localhost:8765") as client:
    tools = client.list_tools()
    result = client.call_tool("search", {"query": "LLM trends"})
    print(result)
```

### With Auth

```python
client = MCPClient(
    "https://api.example.com/mcp",
    api_key="your-token-here",
    timeout=120.0,
)
```

### Connect to tiny-mcp-server

```python
# Start a tiny-mcp-server first:
#   python -m tiny_mcp_server --port 8765

from tiny_mcp_client import MCPClient
import asyncio

async def main():
    async with MCPClient("http://localhost:8765") as client:
        tools = await client.list_tools()
        print(f"Connected to {client.server_info.name}")
        print(f"Available tools: {[t.name for t in tools]}")

asyncio.run(main())
```

## API Reference

### `MCPClient` (async)

```python
client = MCPClient(
    url="http://localhost:8765",    # MCP server URL
    headers={"X-Custom": "value"},  # Extra HTTP headers
    timeout=60.0,                    # Request timeout (seconds)
    api_key="token",                 # Bearer auth
    auto_initialize=True,            # Call initialize() on __aenter__
    client_info={"name": "my-app", "version": "1.0"},
)
```

**Methods:**
- `await client.initialize()` → `ServerInfo` — Send handshake
- `await client.close()` — End session
- `await client.list_tools()` → `List[ToolDefinition]`
- `await client.call_tool(name, arguments)` → `Any`
- `await client.list_resources()` → `List[ResourceDefinition]`
- `await client.read_resource(uri)` → `Any`
- `await client.list_prompts()` → `List[PromptDefinition]`
- `await client.get_prompt(name, arguments)` → `Any`
- `await client.create_sampling_message(...)` → `Any`

### `MCPSyncClient`

Same interface, synchronous. Uses a thread pool under the hood.

### `JSONRPCTransport`

Low-level transport if you need it:

```python
transport = JSONRPCTransport("http://localhost:8765", api_key="...")
result, error = transport.request("tools/list")
transport.notify("resources/subscribe", {"uri": "file:///data.json"})
```

## Architecture

```
your code          tiny-mcp-client          MCP server
   │                      │                      │
   │─── MCPClient ────────►│                      │
   │                       │─── HTTP POST ────────►│
   │                       │◄── JSON-RPC response ──│
   │◄── Python result ──────│                      │
   │                       │                      │
```

## tiny-\* Ecosystem

| Package | Description |
|---------|-------------|
| [tiny-mcp-server](https://github.com/hussain-alsaibai/tiny-mcp-server) | Build MCP servers in one file |
| [tiny-mcp-registry](https://github.com/hussain-alsaibai/tiny-mcp-registry) | Discover and manage MCP servers |
| [tiny-mcp-observability](https://github.com/hussain-alsaibai/tiny-mcp-observability) | Tracing and metrics for MCP pipelines |
| **tiny-mcp-client** | Connect to any MCP server |

## License

MIT — see [LICENSE](LICENSE)

## Pin and diff tool contracts

Reference MCP servers change tool contracts silently — a description is
reworded, an input property becomes required, a tool disappears. A consumer
that installed a published package cannot run the server repo's CI to notice.

`tiny_mcp_contract` pins what a server exposed and diffs two versions:

```python
import asyncio
from tiny_mcp_client import MCPClient
from tiny_mcp_contract import Snapshot, diff, has_breaking

async def pin(url, path, server, version):
    async with MCPClient(url) as client:
        snap = Snapshot.build(await client.list_tools(), server=server, version=version)
    with open(path, "w") as f:
        f.write(snap.dumps())

asyncio.run(pin("https://example.com/mcp", "filesystem-2.2.0.json", "filesystem", "2.2.0"))

# later, against a new release: check the old snapshot against the live server
async def check(url, path):
    old = Snapshot.loads(open(path).read())
    async with MCPClient(url) as client:
        new = Snapshot.build(await client.list_tools(), server=old.server, version="latest")
    changes = diff(old, new)
    for c in changes:
        print(c)
    return not has_breaking(changes)

raise SystemExit(0 if asyncio.run(check("https://example.com/mcp", "filesystem-2.2.0.json")) else 1)
```

Detected classes: `ADDED`, `GONE`, `CHANGED` (description or outputSchema),
and `BREAKING` — a new required input property, a removed property, a changed
property shape, or a narrowed `enum`. Digests are sha-256 over a canonical
(key-sorted) JSON form, so formatting changes do not register as drift.
