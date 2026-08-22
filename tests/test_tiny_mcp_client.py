"""
Tests for tiny-mcp-client.
Run with: pytest tests/ -v
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from tiny_mcp_client import (
    MCPClient, MCPSyncClient, MCPError, MCPAuthError, MCPTimeoutError,
    ToolDefinition, ResourceDefinition, PromptDefinition, ServerInfo,
    JSONRPCTransport,
)


# ─── JSONRPCTransport ─────────────────────────────────────────────────────────
class TestJSONRPCTransport:
    def test_build_request_jsonrpc_format(self):
        t = JSONRPCTransport("http://localhost:8765")
        req = t._build_request("tools/list", {"filter": "web"})
        assert req.method == "POST"
        body = json.loads(req.data)
        assert body["jsonrpc"] == "2.0"
        assert body["method"] == "tools/list"
        assert body["params"] == {"filter": "web"}
        assert "id" in body

    def test_api_key_header(self):
        t = JSONRPCTransport("http://localhost:8765", api_key="secret123")
        assert t._headers["Authorization"] == "Bearer secret123"

    def test_parse_response_success(self):
        t = JSONRPCTransport("http://localhost:8765")
        raw = b'{"jsonrpc": "2.0", "id": "1", "result": {"ok": true}}'
        result, error = t._parse_response(raw), None
        assert json.loads(raw)["result"] == {"ok": True}

    def test_parse_response_error(self):
        t = JSONRPCTransport("http://localhost:8765")
        raw = b'{"jsonrpc": "2.0", "id": "1", "error": {"code": -32600, "message": "Invalid Request"}}'
        parsed = json.loads(raw)
        assert "error" in parsed

    @patch("tiny_mcp_client.urlopen")
    def test_request_success(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"jsonrpc":"2.0","id":"x","result":{"tools":[]}}'
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        t = JSONRPCTransport("http://localhost:8765")
        result, err = t.request("tools/list")
        assert err is None
        assert result == {"tools": []}

    @patch("tiny_mcp_client.urlopen")
    def test_request_mcp_error(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"jsonrpc":"2.0","id":"x","error":{"code":-32600,"message":"bad"}}'
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        t = JSONRPCTransport("http://localhost:8765")
        with pytest.raises(MCPError) as exc_info:
            t.request("tools/list")
        assert exc_info.value.code == -32600

    @patch("tiny_mcp_client.urlopen")
    def test_request_timeout(self, mock_urlopen):
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError("timed out")

        t = JSONRPCTransport("http://localhost:8765")
        with pytest.raises(MCPTimeoutError):
            t.request("tools/list")


# ─── Types ────────────────────────────────────────────────────────────────────
class TestTypes:
    def test_tool_definition_from_dict(self):
        d = {
            "name": "fetch_url",
            "description": "Fetch a URL",
            "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}},
        }
        t = ToolDefinition.from_dict(d)
        assert t.name == "fetch_url"
        assert t.description == "Fetch a URL"
        assert t.input_schema["type"] == "object"

    def test_resource_definition_from_dict(self):
        d = {"uri": "file:///tmp/test.txt", "name": "Test File", "mimeType": "text/plain"}
        r = ResourceDefinition.from_dict(d)
        assert r.uri == "file:///tmp/test.txt"
        assert r.mime_type == "text/plain"

    def test_prompt_definition_from_dict(self):
        d = {"name": "summarize", "description": "Summarization prompt"}
        p = PromptDefinition.from_dict(d)
        assert p.name == "summarize"

    def test_server_info_from_dict(self):
        d = {"name": "my-server", "version": "1.2.3", "protocolVersion": "2024-11-05"}
        s = ServerInfo.from_dict(d)
        assert s.name == "my-server"
        assert s.version == "1.2.3"
        assert s.protocol_version == "2024-11-05"


# ─── MCPClient ────────────────────────────────────────────────────────────────
class TestMCPClient:
    @pytest.mark.asyncio
    async def test_initialize_success(self):
        with patch.object(JSONRPCTransport, "request") as mock_req, \
             patch.object(JSONRPCTransport, "notify"):
            mock_req.return_value = (
                {"serverInfo": {"name": "test", "version": "1.0"}, "protocolVersion": "2024-11-05"},
                None,
            )
            client = MCPClient("http://localhost:8765", auto_initialize=False)
            info = await client.initialize()
            assert info.name == "test"
            assert info.version == "1.0"
            assert client.is_initialized

    @pytest.mark.asyncio
    async def test_initialize_auto(self):
        with patch.object(JSONRPCTransport, "request") as mock_req, \
             patch.object(JSONRPCTransport, "notify"):
            mock_req.return_value = (
                {"serverInfo": {"name": "test", "version": "1.0"}, "protocolVersion": "2024-11-05"},
                None,
            )
            client = MCPClient("http://localhost:8765", auto_initialize=True)
            async with client:
                assert client.is_initialized

    @pytest.mark.asyncio
    async def test_initialize_not_called(self):
        client = MCPClient("http://localhost:8765", auto_initialize=False)
        assert not client.is_initialized
        with pytest.raises(MCPError, match="not initialized"):
            await client.list_tools()

    @pytest.mark.asyncio
    async def test_list_tools(self):
        with patch.object(JSONRPCTransport, "request") as mock_req, \
             patch.object(JSONRPCTransport, "notify"):
            mock_req.return_value = (
                {
                    "tools": [
                        {"name": "web_search", "description": "Search the web"},
                        {"name": "fetch", "description": "Fetch content"},
                    ]
                },
                None,
            )
            client = MCPClient("http://localhost:8765", auto_initialize=False)
            await client.initialize()
            tools = await client.list_tools()
            assert len(tools) == 2
            assert tools[0].name == "web_search"
            assert tools[1].name == "fetch"

    @pytest.mark.asyncio
    async def test_call_tool(self):
        with patch.object(JSONRPCTransport, "request") as mock_req, \
             patch.object(JSONRPCTransport, "notify"):
            mock_req.return_value = ({"content": [{"type": "text", "text": "result"}]}, None)
            client = MCPClient("http://localhost:8765", auto_initialize=False)
            await client.initialize()
            result = await client.call_tool("fetch", {"url": "https://example.com"})
            assert result["content"][0]["text"] == "result"

    @pytest.mark.asyncio
    async def test_list_resources(self):
        with patch.object(JSONRPCTransport, "request") as mock_req, \
             patch.object(JSONRPCTransport, "notify"):
            mock_req.return_value = (
                {"resources": [{"uri": "file:///a.txt", "name": "a"}]},
                None,
            )
            client = MCPClient("http://localhost:8765", auto_initialize=False)
            await client.initialize()
            resources = await client.list_resources()
            assert len(resources) == 1
            assert resources[0].uri == "file:///a.txt"

    @pytest.mark.asyncio
    async def test_list_prompts(self):
        with patch.object(JSONRPCTransport, "request") as mock_req, \
             patch.object(JSONRPCTransport, "notify"):
            mock_req.return_value = (
                {"prompts": [{"name": "summarize", "description": "Summarize text"}]},
                None,
            )
            client = MCPClient("http://localhost:8765", auto_initialize=False)
            await client.initialize()
            prompts = await client.list_prompts()
            assert len(prompts) == 1
            assert prompts[0].name == "summarize"


# ─── MCPSyncClient ─────────────────────────────────────────────────────────────
class TestMCPSyncClient:
    def test_sync_initialize(self):
        with patch.object(JSONRPCTransport, "request") as mock_req, \
             patch.object(JSONRPCTransport, "notify"):
            mock_req.return_value = (
                {"serverInfo": {"name": "sync-test", "version": "2.0"}, "protocolVersion": "2024-11-05"},
                None,
            )
            client = MCPSyncClient("http://localhost:8765")
            info = client.initialize()
            assert info.name == "sync-test"
            assert client.is_initialized

    def test_sync_list_tools(self):
        with patch.object(JSONRPCTransport, "request") as mock_req, \
             patch.object(JSONRPCTransport, "notify"):
            mock_req.return_value = ({"tools": [{"name": "test_tool"}]}, None)
            client = MCPSyncClient("http://localhost:8765")
            client.initialize()
            tools = client.list_tools()
            assert len(tools) == 1
            assert tools[0].name == "test_tool"
