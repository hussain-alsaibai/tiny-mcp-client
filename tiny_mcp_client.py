#!/usr/bin/env python3
"""
tiny-mcp-client — Zero-dependency Model Context Protocol client.
stdlib only · MIT · https://github.com/hussain-alsaibai/tiny-mcp-client

Supports stdio (local subprocess) and SSE/HTTP transport.
Tool call, resource read, prompt render — all in one file.
Compatible with any MCP server (Claude Desktop, Cursor, Cline, etc.)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator
from enum import Enum

__version__ = "0.1.0"
__all__ = ["MCPClient", "MCPServer", "Request", "Response", "ToolResult", "ResourceResult"]


# ─── JSON-RPC types ────────────────────────────────────────────────────────────

@dataclass
class JSONRPCRequest:
    jsonrpc: str = "2.0"
    id: str | int | None = None
    method: str = ""
    params: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        d = {"jsonrpc": self.jsonrpc, "method": self.method}
        if self.id is not None:
            d["id"] = self.id
        if self.params is not None:
            d["params"] = self.params
        return d

    def to_json(self) -> bytes:
        return json.dumps(self.to_dict(), separators=(",", ":")).encode()


@dataclass
class JSONRPCResponse:
    id: str | int | None
    result: Any = None
    error: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, d: dict) -> JSONRPCResponse:
        return cls(
            id=d.get("id"),
            result=d.get("result"),
            error=d.get("error"),
        )


# ─── Transport: Stdio ──────────────────────────────────────────────────────────

class StdioTransport:
    """Run an MCP server as a subprocess and communicate via JSON-RPC over stdin/stdout."""

    def __init__(self, command: str | list[str], *, cwd: str | None = None, env: dict | None = None):
        self._cmd = command if isinstance(command, list) else command.split()
        self._cwd = cwd
        self._env = {**os.environ, **(env or {})}
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._pending: dict[str | int, threading.Event] = {}
        self._results: dict[str | int, JSONRPCResponse] = {}
        self._reader_thread: threading.Thread | None = None
        self._running = False

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._proc is not None:
            return
        self._proc = subprocess.Popen(
            self._cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self._cwd,
            env=self._env,
        )
        self._running = True
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        # Allow server to initialise
        time.sleep(0.25)

    def stop(self) -> None:
        self._running = False
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None

    # ── JSON-RPC round-trip ────────────────────────────────────────────────────

    def send(self, req: JSONRPCRequest) -> JSONRPCResponse:
        """Send a request and wait for the response."""
        if self._proc is None or self._proc.poll() is not None:
            raise ConnectionError("Server process not running")
        req.id = req.id or str(uuid.uuid4())
        evt = threading.Event()
        with self._lock:
            self._pending[req.id] = evt
        try:
            self._proc.stdin.write(req.to_json() + b"\n")
            self._proc.stdin.flush()
            evt.wait(timeout=30)
            with self._lock:
                return self._results.pop(req.id, JSONRPCResponse(req.id, error={"code": -1, "message": "no response"}))
        finally:
            with self._lock:
                self._pending.pop(req.id, None)

    def notify(self, req: JSONRPCRequest) -> None:
        """Fire-and-forget notification (no response expected)."""
        if self._proc is None or self._proc.poll() is not None:
            raise ConnectionError("Server process not running")
        self._proc.stdin.write(req.to_json() + b"\n")
        self._proc.stdin.flush()

    # ── internal reader ────────────────────────────────────────────────────────

    def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            if not self._running:
                break
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            resp = JSONRPCResponse.from_dict(d)
            with self._lock:
                if resp.id in self._pending:
                    self._results[resp.id] = resp
                    self._pending[resp.id].set()


# ─── Transport: SSE ───────────────────────────────────────────────────────────

class SSETransport:
    """Connect to an MCP server over HTTP with Server-Sent Events."""

    def __init__(self, url: str, *, headers: dict | None = None):
        self._url = url.rstrip("/")
        self._headers = {**(headers or {})}
        self._pending: dict[str, threading.Event] = {}
        self._results: dict[str, JSONRPCResponse] = {}
        self._lock = threading.Lock()
        self._running = False
        self._reader_thread: threading.Thread | None = None
        self._session_id: str | None = None

    def start(self) -> None:
        self._running = True
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def stop(self) -> None:
        self._running = False

    def send(self, req: JSONRPCRequest) -> JSONRPCResponse:
        req.id = req.id or str(uuid.uuid4())
        body = req.to_json()
        req_headers = {**self._headers, "Content-Type": "application/json"}
        if self._session_id:
            req_headers["MCP-Session-ID"] = self._session_id
        req_obj = urllib.request.Request(
            self._url + "/mcp",
            data=body,
            headers=req_headers,
            method="POST",
        )
        with urllib.request.urlopen(req_obj, timeout=30) as resp:
            raw = resp.read()
            d = json.loads(raw)
            return JSONRPCResponse.from_dict(d)

    def notify(self, req: JSONRPCRequest) -> None:
        req.id = None
        body = req.to_json()
        req_headers = {**self._headers, "Content-Type": "application/json"}
        if self._session_id:
            req_headers["MCP-Session-ID"] = self._session_id
        req_obj = urllib.request.Request(
            self._url + "/mcp",
            data=body,
            headers=req_headers,
            method="POST",
        )
        try:
            urllib.request.urlopen(req_obj, timeout=10)
        except Exception:
            pass

    def _read_loop(self) -> None:
        """Read SSE stream from the server and dispatch responses."""
        req_headers = {**self._headers, "Accept": "text/event-stream"}
        if self._session_id:
            req_headers["MCP-Session-ID"] = self._session_id
        req_obj = urllib.request.Request(self._url + "/sse", headers=req_headers)
        try:
            with urllib.request.urlopen(req_obj, timeout=60 * 30) as resp:
                buf = ""
                for chunk in resp:
                    if not self._running:
                        break
                    buf += chunk.decode("utf-8", errors="replace")
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if line.startswith("data:"):
                            data = line[5:].strip()
                            self._dispatch(data)
        except Exception:
            pass

    def _dispatch(self, data: str) -> None:
        try:
            d = json.loads(data)
        except json.JSONDecodeError:
            return
        resp = JSONRPCResponse.from_dict(d)
        if resp.id is None:
            return
        with self._lock:
            if resp.id in self._pending:
                self._results[resp.id] = resp
                self._pending[resp.id].set()


# ─── MCP Client ───────────────────────────────────────────────────────────────

@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> Tool:
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            input_schema=d.get("inputSchema", d.get("input_schema", {})),
        )


@dataclass
class Resource:
    uri: str
    name: str
    description: str = ""
    mime_type: str = "application/octet-stream"

    @classmethod
    def from_dict(cls, d: dict) -> Resource:
        return cls(
            uri=d["uri"],
            name=d.get("name", ""),
            description=d.get("description", ""),
            mime_type=d.get("mimeType", d.get("mime_type", "application/octet-stream")),
        )


@dataclass
class Prompt:
    name: str
    description: str
    arguments: list[dict] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> Prompt:
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            arguments=d.get("arguments", []),
        )


@dataclass
class ToolResult:
    content: list[dict]  # [{type: "text"|"image"|"audio", text?: str, data?: str, mimeType?: str}]
    is_error: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> ToolResult:
        return cls(
            content=d.get("content", []),
            is_error=d.get("isError", False),
        )


@dataclass
class ResourceResult:
    contents: list[dict]  # [{uri, mimeType, text?, blob?}]

    @classmethod
    def from_dict(cls, d: dict) -> ResourceResult:
        return cls(contents=d.get("contents", []))


class MCPClient:
    """
    Unified MCP client — works with any MCP server over stdio or SSE.

    Usage (stdio):

        async with MCPClient(["npx", "modelcontextprotocol", "servers", "run", "filesystem", "./data"]) as client:
            tools = client.list_tools()
            result = client.call_tool("read_file", {"path": "/tmp/foo"})
            print(result.content[0]["text"])

    Usage (HTTP/SSE):

        client = MCPClient("http://localhost:3000", transport="sse")
        client.start()
        tools = client.list_tools()
        ...
        client.stop()
    """

    def __init__(
        self,
        server: str | list[str],
        *,
        transport: str = "stdio",
        cwd: str | None = None,
        env: dict | None = None,
        headers: dict | None = None,
        timeout: float = 30,
    ):
        """
        Args:
            server: Command + args list (stdio) or base URL (SSE).
            transport: "stdio" or "sse".
            cwd: Working directory for the subprocess (stdio only).
            env: Extra env vars for the subprocess (stdio only).
            headers: Extra HTTP headers (SSE only).
            timeout: Seconds to wait for each call.
        """
        self._transport: StdioTransport | SSETransport
        if transport == "stdio":
            cmd = server if isinstance(server, list) else server.split()
            self._transport = StdioTransport(cmd, cwd=cwd, env=env)
        else:
            self._transport = SSETransport(server, headers=headers or {})
        self._timeout = timeout
        self._tools: list[Tool] = []
        self._resources: list[Resource] = []
        self._prompts: list[Prompt] = []
        self._initialized = False

    def start(self) -> None:
        self._transport.start()
        self.initialize()
        self._tools = self.list_tools()
        self._resources = self.list_resources()
        self._prompts = self.list_prompts()

    def stop(self) -> None:
        self._transport.stop()
        self._initialized = False

    def __enter__(self) -> MCPClient:
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop()

    # ── protocol methods ───────────────────────────────────────────────────────

    def initialize(self) -> dict:
        resp = self._call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "roots": {"listChanged": True},
                "sampling": {},
            },
            "clientInfo": {
                "name": "tiny-mcp-client",
                "version": __version__,
            },
        })
        self._initialized = True
        # Send initialised notification
        self._transport.notify(JSONRPCRequest(method="notifications/initialized"))
        return resp

    def list_tools(self) -> list[Tool]:
        resp = self._call("tools/list")
        tools = resp.get("tools", []) if isinstance(resp, dict) else []
        return [Tool.from_dict(t) for t in tools]

    def call_tool(self, name: str, arguments: dict | None = None) -> ToolResult:
        resp = self._call("tools/call", {"name": name, "arguments": arguments or {}})
        return ToolResult.from_dict(resp)

    def list_resources(self) -> list[Resource]:
        resp = self._call("resources/list")
        resources = resp.get("resources", []) if isinstance(resp, dict) else []
        return [Resource.from_dict(r) for r in resources]

    def read_resource(self, uri: str) -> ResourceResult:
        resp = self._call("resources/read", {"uri": uri})
        return ResourceResult.from_dict(resp)

    def list_prompts(self) -> list[Prompt]:
        resp = self._call("prompts/list")
        prompts = resp.get("prompts", []) if isinstance(resp, dict) else []
        return [Prompt.from_dict(p) for p in prompts]

    def get_prompt(self, name: str, arguments: dict | None = None) -> dict:
        return self._call("prompts/get", {"name": name, "arguments": arguments or {}})

    # ── introspection helpers ──────────────────────────────────────────────────

    def find_tool(self, name: str) -> Tool | None:
        for t in self._tools:
            if t.name == name:
                return t
        return None

    def tool_names(self) -> list[str]:
        return [t.name for t in self._tools]

    @property
    def tools(self) -> list[Tool]:
        return self._tools

    @property
    def resources(self) -> list[Resource]:
        return self._resources

    @property
    def prompts(self) -> list[Prompt]:
        return self._prompts

    # ── low-level ─────────────────────────────────────────────────────────────

    def _call(self, method: str, params: dict | None = None) -> Any:
        req = JSONRPCRequest(method=method, params=params)
        resp = self._transport.send(req)
        if resp.error:
            raise MCPError(resp.error["code"], resp.error.get("message", ""), resp.error)
        return resp.result


class MCPError(Exception):
    def __init__(self, code: int, message: str, data: dict | None = None):
        self.code = code
        super().__init__(f"[{code}] {message}")
        self.data = data


# ─── CLI entrypoint ────────────────────────────────────────────────────────────

def _cli() -> None:
    """tiny-mcp-client CLI — inspect a server without writing code."""
    import argparse

    parser = argparse.ArgumentParser(description="tiny-mcp-client: inspect MCP servers from the command line")
    parser.add_argument("command", nargs="+", help="Command to run the MCP server, e.g. npx @modelcontextprotocol/server-filesystem ./data")
    parser.add_argument("--tools", action="store_true", help="List available tools")
    parser.add_argument("--resources", action="store_true", help="List available resources")
    parser.add_argument("--prompts", action="store_true", help="List available prompts")
    parser.add_argument("--call", metavar="NAME", help="Call a tool by name")
    parser.add_argument("--args", default="{}", help="JSON arguments for --call")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    cmd = args.command
    try:
        with MCPClient(cmd, transport="stdio") as client:
            output = {}
            if args.tools:
                output["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in client.tools]
            if args.resources:
                output["resources"] = [{"uri": r.uri, "name": r.name, "description": r.description} for r in client.resources]
            if args.prompts:
                output["prompts"] = [{"name": p.name, "description": p.description} for p in client.prompts]
            if args.call:
                result = client.call_tool(args.call, json.loads(args.args))
                output["result"] = {"content": result.content, "is_error": result.is_error}
            if not output:
                output["info"] = "Use --tools, --resources, --prompts, or --call"
            print(json.dumps(output, indent=2) if args.json else json.dumps(output))
    except MCPError as e:
        print(json.dumps({"error": {"code": e.code, "message": str(e), "data": e.data}}))
        sys.exit(1)


if __name__ == "__main__":
    _cli()
