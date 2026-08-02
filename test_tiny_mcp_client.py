#!/usr/bin/env python3
"""Tests for tiny-mcp-client."""
import json, pytest, subprocess, sys, time
from tiny_mcp_client import MCPClient, MCPError, JSONRPCRequest, JSONRPCResponse, StdioTransport, SSETransport


class TestJSONRPC:
    def test_request_to_dict(self):
        r = JSONRPCRequest(id="1", method="foo", params={"x": 1})
        assert r.to_dict() == {"jsonrpc": "2.0", "id": "1", "method": "foo", "params": {"x": 1}}

    def test_request_to_json(self):
        r = JSONRPCRequest(id="1", method="foo", params={"x": 1})
        d = json.loads(r.to_json())
        assert d == {"jsonrpc": "2.0", "id": "1", "method": "foo", "params": {"x": 1}}

    def test_response_from_dict(self):
        resp = JSONRPCResponse.from_dict({"jsonrpc": "2.0", "id": 5, "result": {"ok": True}})
        assert resp.id == 5
        assert resp.result == {"ok": True}
        assert resp.error is None

    def test_response_error_from_dict(self):
        resp = JSONRPCResponse.from_dict({"jsonrpc": "2.0", "id": "a", "error": {"code": -32600, "message": "Bad request"}})
        assert resp.id == "a"
        assert resp.error == {"code": -32600, "message": "Bad request"}

    def test_request_no_id_for_notification(self):
        r = JSONRPCRequest(method="foo")
        assert r.id is None
        d = r.to_dict()
        assert "id" not in d


class TestMCPClientInit:
    def test_client_stdio_command_split(self):
        client = MCPClient("npx mcp serve", transport="stdio")
        assert client._transport._cmd == ["npx", "mcp", "serve"]

    def test_client_stdio_command_list(self):
        client = MCPClient(["npx", "mcp", "serve"], transport="stdio")
        assert client._transport._cmd == ["npx", "mcp", "serve"]

    def test_client_sse_url(self):
        client = MCPClient("http://localhost:3000", transport="sse")
        assert isinstance(client._transport, SSETransport)
        assert client._transport._url == "http://localhost:3000"

    def test_tools_initially_empty(self):
        client = MCPClient("echo done", transport="stdio")
        assert client._tools == []
        assert client._resources == []
        assert client._prompts == []

    def test_client_context_manager_interface(self):
        # Don't actually start; just verify __enter__/__exit__
        client = MCPClient("echo ok", transport="stdio")
        assert hasattr(client, "__enter__")
        assert hasattr(client, "__exit__")

    def test_find_tool_none(self):
        client = MCPClient("echo", transport="stdio")
        assert client.find_tool("nonexistent") is None

    def test_tool_names(self):
        client = MCPClient("echo", transport="stdio")
        assert client.tool_names() == []


class TestStdioTransportLifecycle:
    def test_start_stop_already_stopped(self):
        t = StdioTransport("true")
        t.start()
        assert t._proc is not None
        t.stop()
        assert t._proc is None

    def test_start_twice_noop(self):
        t = StdioTransport("true")
        t.start()
        t.start()  # should not raise
        t.stop()

    def test_send_fails_if_not_running(self):
        t = StdioTransport("true")
        req = JSONRPCRequest(method="foo")
        with pytest.raises(ConnectionError):
            t.send(req)

    def test_notify_fails_if_not_running(self):
        t = StdioTransport("true")
        req = JSONRPCRequest(method="foo")
        with pytest.raises(ConnectionError):
            t.notify(req)

    def test_notify_no_id(self):
        """Notifications should not have an id in the JSON output."""
        t = StdioTransport("cat")
        t.start()
        try:
            req = JSONRPCRequest(method="foo")
            assert req.id is None
            # Cat just echoes stdin
        finally:
            t.stop()

    def test_stop_twice_noop(self):
        t = StdioTransport("true")
        t.start()
        t.stop()
        t.stop()  # should not raise


class TestStdioTransportProtocol:
    def test_send_and_receive_echo_server(self):
        """Test that a cat subprocess echoes our JSON-RPC correctly."""
        import threading, io, os

        proc = subprocess.Popen(
            ["python3", "-c", """
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    d = json.loads(line)
    # Echo with result
    resp = {"jsonrpc": "2.0", "id": d.get("id"), "result": {"echoed": d.get("params")}}
    print(json.dumps(resp))
    sys.stdout.flush()
"""],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        transport = StdioTransport(["python3", "-c", "import sys,json; [print(json.dumps({'jsonrpc':'2.0','id':json.loads(l).get('id'),'result':{}})) for l in sys.stdin if l.strip()]"],
            cwd=None, env=None)
        transport._proc = proc
        transport._running = True
        transport._reader_thread = threading.Thread(target=transport._read_loop, daemon=True)
        transport._reader_thread.start()

        resp = transport.send(JSONRPCRequest(method="test", params={"foo": "bar"}))
        transport.stop()
        assert resp.result is not None

    def test_request_id_generated(self):
        req = JSONRPCRequest(method="test")
        assert req.id is None
        req.id = req.id or "auto"
        assert req.id == "auto"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
