import json

import pytest

import tiny_mcp_client as sdk
from tiny_mcp_contract import (
    ADDED,
    BREAKING,
    CHANGED,
    GONE,
    Snapshot,
    ToolContract,
    diff,
    has_breaking,
    summary,
)


def tool(name, desc="does a thing", schema=None, output=None):
    return {
        "name": name,
        "description": desc,
        "inputSchema": schema or {"type": "object", "properties": {"path": {"type": "string"}}},
        **({"outputSchema": output} if output is not None else {}),
    }


def test_digest_is_stable_across_key_order():
    a = ToolContract.from_dict(tool("read", schema={"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "integer"}}}))
    b = ToolContract.from_dict(tool("read", schema={"properties": {"b": {"type": "integer"}, "a": {"type": "string"}}, "type": "object"}))
    assert a.digest == b.digest


def test_digest_changes_when_description_changes():
    a = ToolContract.from_dict(tool("read", desc="read a file"))
    b = ToolContract.from_dict(tool("read", desc="read a file, UTF-8 only"))
    assert a.digest != b.digest


def test_new_required_property_is_breaking():
    old = Snapshot.build([tool("read")])
    new = Snapshot.build([tool("read", schema={"type": "object", "properties": {"path": {"type": "string"}, "encoding": {"type": "string"}}, "required": ["path", "encoding"]})])
    changes = diff(old, new)
    assert has_breaking(changes)
    assert any(c.kind == BREAKING and "encoding" in c.detail for c in changes)


def test_removed_property_is_breaking():
    old = Snapshot.build([tool("read", schema={"type": "object", "properties": {"path": {}, "limit": {}}})])
    new = Snapshot.build([tool("read", schema={"type": "object", "properties": {"path": {}}})])
    changes = diff(old, new)
    assert any(c.kind == BREAKING and "limit" in c.detail for c in changes)


def test_narrowed_enum_is_breaking():
    old = Snapshot.build([tool("write", schema={"type": "object", "properties": {"mode": {"enum": ["w", "a", "x"]}}})])
    new = Snapshot.build([tool("write", schema={"type": "object", "properties": {"mode": {"enum": ["w", "a"]}}})])
    changes = diff(old, new)
    assert any(c.kind == BREAKING and "'x'" in c.detail for c in changes)


def test_added_and_gone_tools():
    old = Snapshot.build([tool("read"), tool("write")])
    new = Snapshot.build([tool("read"), tool("edit")])
    kinds = {c.kind for c in diff(old, new)}
    assert kinds == {ADDED, GONE}


def test_description_only_change_is_not_breaking():
    old = Snapshot.build([tool("read", desc="read a file")])
    new = Snapshot.build([tool("read", desc="read a text file")])
    changes = diff(old, new)
    assert [c.kind for c in changes] == [CHANGED]
    assert not has_breaking(changes)


def test_output_schema_is_diffed_separately():
    old = Snapshot.build([tool("read", output={"type": "string"})])
    new = Snapshot.build([tool("read", output={"type": "object"})])
    changes = diff(old, new)
    assert any("outputSchema" in c.detail for c in changes)


def test_no_changes_returns_empty():
    snap = Snapshot.build([tool("read"), tool("write")])
    assert diff(snap, snap) == []


def test_snapshot_roundtrip_preserves_digest():
    snap = Snapshot.build([tool("read"), tool("write")], server="filesystem", version="2.2.0")
    restored = Snapshot.loads(snap.dumps())
    assert restored.digest == snap.digest
    assert restored.version == "2.2.0"
    assert diff(snap, restored) == []


def test_accepts_sdk_tool_definition():
    """The client's own ToolDefinition must feed a snapshot directly."""
    td = sdk.ToolDefinition(name="read_file", description="grep me", input_schema={"type": "object", "properties": {"path": {}}})
    snap = Snapshot.build([td])
    assert "read_file" in snap.tools
    assert snap.tools["read_file"].input_schema["properties"]["path"] == {}


def test_summary_counts():
    old = Snapshot.build([tool("read"), tool("write")])
    new = Snapshot.build([tool("read", desc="changed"), tool("edit")])
    assert summary(diff(old, new)) == {BREAKING: 0, ADDED: 1, GONE: 1, CHANGED: 1}


def test_dumps_is_deterministic():
    a = Snapshot.build([tool("b"), tool("a")])
    b = Snapshot.build([tool("a"), tool("b")])
    assert a.dumps() == b.dumps()
