"""Infra diff engine — compares two Program ASTs field by field."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from infra.parser import ast_nodes as n

Definition = Union[
    n.ServiceDef,
    n.DatabaseDef,
    n.CacheDef,
    n.QueueDef,
    n.StorageDef,
    n.NetworkDef,
    n.SecretDef,
    n.ConfigDef,
    n.PipelineDef,
    n.EnvironmentDef,
    n.ClusterDef,
]

DEFINITION_TYPES = (
    n.ServiceDef,
    n.DatabaseDef,
    n.CacheDef,
    n.QueueDef,
    n.StorageDef,
    n.NetworkDef,
    n.SecretDef,
    n.ConfigDef,
    n.PipelineDef,
    n.EnvironmentDef,
    n.ClusterDef,
)


@dataclass
class FieldChange:
    field_path: str
    before: Any
    after: Any

    def _fmt(self, v: Any) -> str:
        if v is None:
            return "null"
        if isinstance(v, bool):
            return str(v).lower()
        return str(v)

    def format(self) -> str:
        return (
            f"  ~ {self.field_path}: {self._fmt(self.before)} → {self._fmt(self.after)}"
        )


@dataclass
class DiffItem:
    kind: str
    name: str
    location: Optional[n.SourceLocation] = None


@dataclass
class ChangedItem:
    kind: str
    name: str
    changes: list[FieldChange] = field(default_factory=list)

    def format(self, color: bool = True) -> str:
        prefix = "\033[33m" if color else ""
        reset = "\033[0m" if color else ""
        lines = [
            f"{prefix}~ {self.kind} {self.name} ({len(self.changes)} change(s)){reset}"
        ]
        for ch in self.changes:
            lines.append(ch.format())
        return "\n".join(lines)


@dataclass
class DiffResult:
    added: list[DiffItem] = field(default_factory=list)
    removed: list[DiffItem] = field(default_factory=list)
    changed: list[ChangedItem] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    def format(self, color: bool = True, only_changes: bool = False) -> str:
        lines = []
        g, r, y, gray, reset = (
            ("\033[32m", "\033[31m", "\033[33m", "\033[37m", "\033[0m")
            if color
            else ("", "", "", "", "")
        )
        if self.has_changes:
            lines.append(
                f"{y}SUMMARY: {len(self.changed)} changed, "
                f"{len(self.added)} added, {len(self.removed)} removed{reset}"
            )
            lines.append("")
            for added_item in self.added:
                lines.append(f"{g}+ {added_item.kind} {added_item.name} (new){reset}")
            for removed_item in self.removed:
                lines.append(f"{r}- {removed_item.kind} {removed_item.name} (removed){reset}")  # noqa: E501
            for changed_item in self.changed:
                lines.append(changed_item.format(color=color))
            if not only_changes:
                for name in self.unchanged:
                    lines.append(f"{gray}= {name} (unchanged){reset}")
        else:
            lines.append("✅ No differences found")
            if not only_changes:
                for name in self.unchanged:
                    lines.append(f"{gray}= {name} (unchanged){reset}")
        return "\n".join(lines)

    def format_json(self) -> str:
        def item_dict(i: DiffItem) -> dict[str, str]:
            return {"kind": i.kind, "name": i.name}

        def changed_dict(c: ChangedItem) -> dict[str, object]:
            return {
                "kind": c.kind,
                "name": c.name,
                "changes": [
                    {
                        "field": ch.field_path,
                        "before": str(ch.before) if ch.before is not None else None,
                        "after": str(ch.after) if ch.after is not None else None,
                    }
                    for ch in c.changes
                ],
            }

        return json.dumps(
            {
                "summary": {
                    "added": len(self.added),
                    "removed": len(self.removed),
                    "changed": len(self.changed),
                },
                "added": [item_dict(i) for i in self.added],
                "removed": [item_dict(i) for i in self.removed],
                "changed": [changed_dict(c) for c in self.changed],
                "unchanged": self.unchanged,
                "has_changes": self.has_changes,
            },
            indent=2,
        )


def _node_value(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, n.Literal):
        return val.value
    if isinstance(val, n.ResourceValue):
        return f"{val.value}{val.unit or ''}"
    if isinstance(val, n.Duration):
        return f"{val.value}{val.unit}"
    if isinstance(val, n.Identifier):
        return val.name
    if isinstance(val, (int, float, str, bool)):
        return val
    return repr(val)


def _compare_field(path: str, before: Any, after: Any) -> list[FieldChange]:
    bv, av = _node_value(before), _node_value(after)
    return [FieldChange(path, bv, av)] if bv != av else []


def _compare_nodes(before: Any, after: Any, prefix: str = "") -> list[FieldChange]:
    if type(before) is not type(after):
        return [
            FieldChange(prefix or "type", type(before).__name__, type(after).__name__)
        ]
    if not dataclasses.is_dataclass(before):
        bv, av = _node_value(before), _node_value(after)
        return [FieldChange(prefix, bv, av)] if bv != av else []

    changes = []
    for f in dataclasses.fields(before):
        if f.name == "location":
            continue
        bval, aval = getattr(before, f.name), getattr(after, f.name)
        field_path = f"{prefix}.{f.name}" if prefix else f.name
        if isinstance(bval, (tuple, list)):
            if len(bval) != len(aval):
                changes.append(
                    FieldChange(
                        field_path, f"({len(bval)} items)", f"({len(aval)} items)"
                    )
                )
        elif dataclasses.is_dataclass(bval) and bval is not None and aval is not None:
            changes.extend(_compare_nodes(bval, aval, field_path))
        else:
            changes.extend(_compare_field(field_path, bval, aval))
    return changes


def _get_definitions(program: n.Program) -> dict[str, Definition]:
    return {s.name: s for s in program.statements if isinstance(s, DEFINITION_TYPES)}


def _kind(node: Definition) -> str:
    return type(node).__name__.replace("Def", "").lower()


class InfraDiff:
    def diff(self, before: n.Program, after: n.Program) -> DiffResult:
        bdefs, adefs = _get_definitions(before), _get_definitions(after)
        added = set(adefs) - set(bdefs)
        removed = set(bdefs) - set(adefs)
        common = set(bdefs) & set(adefs)

        result = DiffResult()
        for name in sorted(added):
            node = adefs[name]
            result.added.append(
                DiffItem(_kind(node), name, getattr(node, "location", None))
            )
        for name in sorted(removed):
            node = bdefs[name]
            result.removed.append(
                DiffItem(_kind(node), name, getattr(node, "location", None))
            )
        for name in sorted(common):
            b, a = bdefs[name], adefs[name]
            changes = _compare_nodes(b, a)
            if changes:
                result.changed.append(ChangedItem(_kind(b), name, changes))
            else:
                result.unchanged.append(name)
        return result
