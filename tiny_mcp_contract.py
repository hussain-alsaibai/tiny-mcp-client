"""Pin and diff MCP server tool contracts.

The MCP reference servers ship silent contract changes: a tool's description
is reworded, a property becomes required, a tool disappears.  None of that is
visible per-tool in a changelog, and a consumer cannot run the server repo's
CI.  This module gives a caller a stable digest of what a server exposed and
a diff between two snapshots.

Zero dependencies, stdlib only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

FORMAT_VERSION = 1

BREAKING = "BREAKING"
ADDED = "ADDED"
GONE = "GONE"
CHANGED = "CHANGED"


def _canonical(obj: Any) -> str:
    """Stable serialisation: sorted keys, no insignificant whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _digest(obj: Any) -> str:
    return hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()


@dataclass
class ToolContract:
    """The parts of a tool definition an agent actually depends on."""

    name: str
    description: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ToolContract":
        return cls(
            name=d.get("name", ""),
            description=d.get("description") or "",
            input_schema=d.get("inputSchema") or {},
            output_schema=d.get("outputSchema"),
        )

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "outputSchema": self.output_schema,
        }


@dataclass
class Change:
    kind: str
    tool: str
    detail: str

    def __str__(self) -> str:
        return f"{self.kind} {self.tool}: {self.detail}" if self.detail else f"{self.kind} {self.tool}"


@dataclass
class Snapshot:
    """A pinned view of one server at one version."""

    server: str = ""
    version: str = ""
    tools: Dict[str, ToolContract] = field(default_factory=dict)

    @classmethod
    def build(cls, tools: Iterable[Any], server: str = "", version: str = "") -> "Snapshot":
        snap = cls(server=server, version=version)
        for t in tools:
            contract = t if isinstance(t, ToolContract) else ToolContract.from_dict(
                t if isinstance(t, dict) else _tool_to_dict(t)
            )
            snap.tools[contract.name] = contract
        return snap

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "format": FORMAT_VERSION,
            "server": self.server,
            "version": self.version,
            "tools": {n: c.to_dict() for n, c in sorted(self.tools.items())},
        }

    def dumps(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    @classmethod
    def loads(cls, text: str) -> "Snapshot":
        raw = json.loads(text)
        snap = cls(server=raw.get("server", ""), version=raw.get("version", ""))
        for name, d in (raw.get("tools") or {}).items():
            snap.tools[name] = ToolContract.from_dict(d)
        return snap


def _tool_to_dict(t: Any) -> Dict[str, Any]:
    """Adapt tiny_mcp_client.ToolDefinition (or any object with the attrs)."""
    out: Dict[str, Any] = {"name": getattr(t, "name", "")}
    desc = getattr(t, "description", None)
    if desc is not None:
        out["description"] = desc
    schema = getattr(t, "input_schema", None)
    if schema is not None:
        out["inputSchema"] = schema
    oschema = getattr(t, "output_schema", None)
    if oschema is not None:
        out["outputSchema"] = oschema
    return out


def _required(schema: Dict[str, Any]) -> Sequence[str]:
    req = schema.get("required")
    return list(req) if isinstance(req, list) else []


def _props(schema: Dict[str, Any]) -> Dict[str, Any]:
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


def _schema_changes(old: Dict[str, Any], new: Dict[str, Any]) -> List[tuple[str, str]]:
    """Return (kind, detail) pairs describing an inputSchema delta."""
    out: List[tuple[str, str]] = []
    old_req, new_req = _required(old), _required(new)
    for r in new_req:
        if r not in old_req:
            out.append((BREAKING, f"input property {r!r} is now required"))
    for r in old_req:
        if r not in new_req:
            out.append((CHANGED, f"input property {r!r} is no longer required"))

    old_props, new_props = _props(old), _props(new)
    for p in new_props:
        if p not in old_props:
            out.append((ADDED, f"input property {p!r} added"))
    for p in old_props:
        if p not in new_props:
            out.append((BREAKING, f"input property {p!r} removed"))
        elif _canonical(old_props[p]) != _canonical(new_props[p]):
            out.append((CHANGED, f"input property {p!r} type/shape changed"))

    # A property that stays present but gains a narrower enum is breaking.
    for p, spec in new_props.items():
        old_spec = old_props.get(p)
        if not isinstance(spec, dict) or not isinstance(old_spec, dict):
            continue
        new_enum, old_enum = spec.get("enum"), old_spec.get("enum")
        if isinstance(new_enum, list) and isinstance(old_enum, list):
            dropped = [v for v in old_enum if v not in new_enum]
            if dropped:
                out.append((BREAKING, f"input property {p!r} no longer accepts {dropped!r}"))
    return out


def diff(old: Snapshot, new: Snapshot) -> List[Change]:
    """Compare two snapshots tool by tool."""
    changes: List[Change] = []

    for name in sorted(new.tools):
        if name not in old.tools:
            changes.append(Change(ADDED, name, "tool added"))
    for name in sorted(old.tools):
        if name not in new.tools:
            changes.append(Change(GONE, name, "tool removed"))

    for name in sorted(set(old.tools) & set(new.tools)):
        o, n = old.tools[name], new.tools[name]
        if o.description != n.description:
            changes.append(Change(CHANGED, name, "description changed"))
        for kind, detail in _schema_changes(o.input_schema, n.input_schema):
            changes.append(Change(kind, name, detail))
        if _canonical(o.output_schema) != _canonical(n.output_schema):
            changes.append(Change(CHANGED, name, "outputSchema changed"))
    return changes


def has_breaking(changes: Iterable[Change]) -> bool:
    return any(c.kind == BREAKING for c in changes)


def summary(changes: Iterable[Change]) -> Dict[str, int]:
    counts = {BREAKING: 0, ADDED: 0, GONE: 0, CHANGED: 0}
    for c in changes:
        counts[c.kind] = counts.get(c.kind, 0) + 1
    return counts
