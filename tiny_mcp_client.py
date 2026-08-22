"""
tiny-mcp-client — Zero-dependency Model Context Protocol client for Python.

MIT License | https://github.com/hussain-alsaibai/tiny-mcp-client

Connects to any MCP server via stdlib http.client + json.
Compatible with the MCP spec (2024-11-05) and works seamlessly with
tiny-mcp-server, but also with any server implementing the MCP JSON-RPC API.

Usage:
    from tiny_mcp_client import MCPClient

    async with MCPClient("http://localhost:8765") as client:
        tools = await client.list_tools()
        result = await client.call_tool("fetch_url", {"url": "https://..."})
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import URLError

__version__ = "0.1.0"
__all__ = ["MCPClient", "MCPError", "MCPAuthError", "MCPTimeoutError"]


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────────────────────
class MCPError(Exception):
    """Base exception for MCP client errors."""

    def __init__(self, message: str, code: int = -32603, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


class MCPAuthError(MCPError):
    """Raised when the server returns an authentication error."""

    def __init__(self, message: str = "Authentication failed", data: Any = None):
        super().__init__(message, code=-32600, data=data)


class MCPTimeoutError(MCPError):
    """Raised when a request times out."""

    def __init__(self, message: str = "Request timed out", data: Any = None):
        super().__init__(message, code=-32603, data=data)


# ──────────────────────────────────────────────────────────────────────────────
# Types
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class ToolDefinition:
    name: str
    description: Optional[str] = None
    input_schema: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ToolDefinition":
        return cls(
            name=d.get("name", ""),
            description=d.get("description"),
            input_schema=d.get("inputSchema", {}),
        )


@dataclass
class ResourceDefinition:
    uri: str
    name: Optional[str] = None
    description: Optional[str] = None
    mime_type: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ResourceDefinition":
        return cls(
            uri=d.get("uri", ""),
            name=d.get("name"),
            description=d.get("description"),
            mime_type=d.get("mimeType"),
        )


@dataclass
class PromptDefinition:
    name: str
    description: Optional[str] = None
    arguments: List[Any] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PromptDefinition":
        return cls(
            name=d.get("name", ""),
            description=d.get("description"),
            arguments=d.get("arguments", []),
        )


@dataclass
class ServerInfo:
    name: str
    version: str
    protocol_version: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ServerInfo":
        return cls(
            name=d.get("name", "unknown"),
            version=d.get("version", "0.0.0"),
            protocol_version=d.get("protocolVersion"),
        )


# ──────────────────────────────────────────────────────────────────────────────
# JSON-RPC transport
# ──────────────────────────────────────────────────────────────────────────────
class JSONRPCTransport:
    """Minimal JSON-RPC 2.0 transport over HTTP. Stdlib only."""

    def __init__(
        self,
        base_url: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 60.0,
        api_key: Optional[str] = None,
    ) -> None:
        base = base_url.rstrip("/")
        self._url = f"{base}/mcp"
        self._timeout = timeout
        self._headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **{(k or ""): (v or "") for k, v in (headers or {}).items()},
        }
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    def _build_request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Request:
        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        return Request(
            self._url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers,
            method="POST",
        )

    def _parse_response(self, raw: bytes) -> Dict[str, Any]:
        return json.loads(raw.decode("utf-8"))

    def request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Any, Optional[Dict[str, Any]]]:
        """Send a JSON-RPC request. Returns (result, error)."""
        req = self._build_request(method, params)
        try:
            with urlopen(req, timeout=self._timeout) as resp:
                data = resp.read()
        except URLError as e:
            reason = getattr(e, "reason", str(e))
            if "timed out" in str(reason).lower() or getattr(e, "code", 0) == 504:
                raise MCPTimeoutError(f"Request timed out: {reason}") from e
            raise MCPError(f"Connection error: {reason}", code=-32603) from e

        parsed = self._parse_response(data)

        if "error" in parsed:
            err = parsed["error"]
            code = err.get("code", -32603)
            msg = err.get("message", "Unknown error")
            data = err.get("data")
            if code == -32600:
                raise MCPAuthError(msg, data)
            raise MCPError(msg, code=code, data=data)

        return parsed.get("result"), None

    def notify(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Send a one-way notification (no response expected)."""
        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        req = Request(
            self._url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers,
            method="POST",
        )
        try:
            with urlopen(req, timeout=5.0) as resp:
                resp.read()
        except URLError:
            pass  # Notifications are fire-and-forget


# ──────────────────────────────────────────────────────────────────────────────
# MCP Client
# ──────────────────────────────────────────────────────────────────────────────
class MCPClient:
    """Model Context Protocol client.

    Args:
        url: Base URL of the MCP server (e.g. "http://localhost:8765").
        headers: Extra HTTP headers to send with every request.
        timeout: Request timeout in seconds (default 60).
        api_key: Bearer token for auth. Adds ``Authorization`` header.
        auto_initialize: If True (default), calls ``initialize`` on ``__aenter__``.
    """

    def __init__(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 60.0,
        api_key: Optional[str] = None,
        auto_initialize: bool = True,
        client_info: Optional[Dict[str, str]] = None,
    ) -> None:
        self._transport = JSONRPCTransport(url, headers, timeout, api_key)
        self._initialized = False
        self._auto_init = auto_initialize
        self._client_info = client_info or {
            "name": "tiny-mcp-client",
            "version": __version__,
        }
        self._server_info: Optional[ServerInfo] = None
        self._protocol_version: Optional[str] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    async def __aenter__(self) -> "MCPClient":
        if self._auto_init:
            await self.initialize()
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._initialized:
            await self.close()

    async def initialize(self) -> ServerInfo:
        """Send the initialize handshake. Call this before any other methods.

        Returns:
            ServerInfo with server name, version, and protocol version.
        """
        result, err = self._transport.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "roots": {"listChanged": True},
                    "sampling": {},
                },
                "clientInfo": self._client_info,
            },
        )
        if err:
            raise MCPError(f"Initialize failed: {err}", code=getattr(err, "code", -32603))

        self._protocol_version = result.get("protocolVersion", "2024-11-05")
        self._server_info = ServerInfo.from_dict(result.get("serverInfo", {}))
        self._initialized = True

        # Announce that the client is ready
        self._transport.notify("notifications/initialized")
        return self._server_info

    async def close(self) -> None:
        """Notify the server the session is ending."""
        self._transport.notify("notifications/close")
        self._initialized = False

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def server_info(self) -> Optional[ServerInfo]:
        return self._server_info

    # ── Tools ─────────────────────────────────────────────────────────────────
    async def list_tools(self) -> List[ToolDefinition]:
        """List all available tools from the server."""
        if not self._initialized:
            raise MCPError("Client not initialized. Call initialize() first.")
        result, err = self._transport.request("tools/list")
        if err:
            raise MCPError(f"list_tools failed: {err}", code=getattr(err, "code", -32603))
        raw: List[Dict[str, Any]] = result.get("tools", [])
        return [ToolDefinition.from_dict(t) for t in raw]

    async def call_tool(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Call a tool by name with the given arguments.

        Args:
            name: Tool name as returned by list_tools().
            arguments: Keyword arguments matching the tool's input schema.

        Returns:
            Tool result (structure varies by tool).
        """
        if not self._initialized:
            raise MCPError("Client not initialized. Call initialize() first.")
        result, err = self._transport.request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
        if err:
            raise MCPError(f"call_tool({name!r}) failed: {err}", code=getattr(err, "code", -32603))
        return result

    # ── Resources ──────────────────────────────────────────────────────────────
    async def list_resources(self) -> List[ResourceDefinition]:
        """List all available resources."""
        if not self._initialized:
            raise MCPError("Client not initialized. Call initialize() first.")
        result, err = self._transport.request("resources/list")
        if err:
            raise MCPError(f"list_resources failed: {err}", code=getattr(err, "code", -32603))
        raw: List[Dict[str, Any]] = result.get("resources", [])
        return [ResourceDefinition.from_dict(r) for r in raw]

    async def read_resource(self, uri: str) -> Any:
        """Read a resource by URI."""
        if not self._initialized:
            raise MCPError("Client not initialized. Call initialize() first.")
        result, err = self._transport.request(
            "resources/read",
            {"uri": uri},
        )
        if err:
            raise MCPError(f"read_resource({uri!r}) failed: {err}", code=getattr(err, "code", -32603))
        return result

    async def subscribe_resource(self, uri: str) -> None:
        """Subscribe to resource change notifications."""
        if not self._initialized:
            raise MCPError("Client not initialized. Call initialize() first.")
        self._transport.notify("resources/subscribe", {"uri": uri})

    async def unsubscribe_resource(self, uri: str) -> None:
        """Unsubscribe from resource change notifications."""
        if not self._initialized:
            raise MCPError("Client not initialized. Call initialize() first.")
        self._transport.notify("resources/unsubscribe", {"uri": uri})

    # ── Prompts ───────────────────────────────────────────────────────────────
    async def list_prompts(self) -> List[PromptDefinition]:
        """List all available prompts."""
        if not self._initialized:
            raise MCPError("Client not initialized. Call initialize() first.")
        result, err = self._transport.request("prompts/list")
        if err:
            raise MCPError(f"list_prompts failed: {err}", code=getattr(err, "code", -32603))
        raw: List[Dict[str, Any]] = result.get("prompts", [])
        return [PromptDefinition.from_dict(p) for p in raw]

    async def get_prompt(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Get a rendered prompt by name."""
        if not self._initialized:
            raise MCPError("Client not initialized. Call initialize() first.")
        result, err = self._transport.request(
            "prompts/get",
            {"name": name, "arguments": arguments or {}},
        )
        if err:
            raise MCPError(f"get_prompt({name!r}) failed: {err}", code=getattr(err, "code", -32603))
        return result

    # ── Sampling ──────────────────────────────────────────────────────────────
    async def create_sampling_message(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        include_context: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Any:
        """Create a sampling (LLM) request (server-side sampling)."""
        params: Dict[str, Any] = {
            "messages": messages,
        }
        if system_prompt:
            params["systemPrompt"] = system_prompt
        if include_context:
            params["includeContext"] = include_context
        if temperature is not None:
            params["temperature"] = temperature
        if max_tokens is not None:
            params["maxTokens"] = max_tokens

        result, err = self._transport.request("sampling/createMessage", params)
        if err:
            raise MCPError(f"create_sampling_message failed: {err}", code=getattr(err, "code", -32603))
        return result


# ──────────────────────────────────────────────────────────────────────────────
# Sync wrapper (for non-async contexts)
# ──────────────────────────────────────────────────────────────────────────────
class MCPSyncClient:
    """Synchronous wrapper around MCPClient.

    Provides the same interface as MCPClient but uses threading to run
    the async methods. Use this when you're not in an async context.
    """

    def __init__(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 60.0,
        api_key: Optional[str] = None,
        auto_initialize: bool = True,
        client_info: Optional[Dict[str, str]] = None,
    ) -> None:
        self._url = url
        self._headers = headers
        self._timeout = timeout
        self._api_key = api_key
        self._auto_init = auto_initialize
        self._client_info = client_info
        self._client: Optional[MCPClient] = None

    def _run(self, coro) -> Any:
        try:
            import asyncio
        except ImportError:
            raise MCPError("asyncio not available — use Python 3.7+")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(coro)
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(loop.run_until_complete, coro)
            return future.result()

    def __enter__(self) -> "MCPSyncClient":
        if self._auto_init:
            self.initialize()
        return self

    def __exit__(self, *_: Any) -> None:
        if self._client and self._client.is_initialized:
            try:
                self._run(self._client.close())
            except Exception:
                pass

    def initialize(self) -> ServerInfo:
        client = MCPClient(
            self._url, self._headers, self._timeout, self._api_key,
            auto_initialize=False, client_info=self._client_info,
        )
        self._client = client
        return self._run(client.initialize())

    @property
    def is_initialized(self) -> bool:
        return bool(self._client and self._client.is_initialized)

    @property
    def server_info(self) -> Optional[ServerInfo]:
        return self._client.server_info if self._client else None

    def list_tools(self) -> List[ToolDefinition]:
        return self._run(self._client.list_tools()) if self._client else []

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        return self._run(self._client.call_tool(name, arguments)) if self._client else None

    def list_resources(self) -> List[ResourceDefinition]:
        return self._run(self._client.list_resources()) if self._client else []

    def read_resource(self, uri: str) -> Any:
        return self._run(self._client.read_resource(uri)) if self._client else None

    def list_prompts(self) -> List[PromptDefinition]:
        return self._run(self._client.list_prompts()) if self._client else []

    def get_prompt(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        return self._run(self._client.get_prompt(name, arguments)) if self._client else None
