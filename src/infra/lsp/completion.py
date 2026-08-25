"""Context-aware completion engine for the Infra Lang LSP.

The completion suggestions are computed **heuristically** from the text around
the cursor, NOT from a full parse. This is intentional: while the user is
typing, the document is frequently incomplete / malformed, so a strict parser
cannot be relied on. The heuristic:

- the last open block on the line (or the block we are currently inside),
- whether the cursor is on a field label (before a `:`) or a value (after),
- whether we are at top level (suggesting block types).

The engine is pure (takes source + position, returns items) so it is easy to
unit test without a running LSP server.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from lsprotocol.types import (
    CompletionItem,
    CompletionItemKind,
    InsertTextFormat,
)

# fields whose values are references to other named blocks in the document
REFERENCE_FIELDS = {
    "depends": True,
    "depends_on": True,
    "store": True,
    "allow_from": True,
    "allow_egress": True,
    "needs": True,
}

# --------------------------------------------------------------------------- #
# Static maps: block -> its fields / sub-blocks
# --------------------------------------------------------------------------- #

TOP_LEVEL_BLOCKS = [
    "service",
    "database",
    "cache",
    "queue",
    "storage",
    "network",
    "network_policy",
    "secret",
    "config",
    "pipeline",
    "environment",
    "cluster",
    "secret_store",
    "resource",
]

# block name -> field names valid inside it (field labels that come before `:`)
BLOCK_FIELDS = {
    "service": [
        "image", "build", "port", "ports", "env", "envFrom", "command",
        "args", "replicas", "resources", "health", "probes", "volumes",
        "depends", "depends_on", "labels", "annotations", "strategy",
        "security",
        "lifecycle", "ingress", "schedule", "autoscale", "disruption",
        "network_policy", "topology", "affinity", "expose",
    ],
    "database": [
        "type", "version", "replicas", "storage", "size", "ssl", "ha",
        "backup", "users",
    ],
    "cache": [
        "type", "version", "maxmemory", "policy", "persistence", "replicas",
    ],
    "queue": [
        "type", "version", "replicas", "topics", "config", "users",
    ],
    "storage": [
        "type", "size", "class", "accessMode", "lifecycle", "bucket", "region",
    ],
    "network": ["cidr", "subnets", "policy"],
    "secret": ["key", "store"],
    "config": ["key"],
    "pipeline": ["trigger", "stages", "artifacts", "cache", "concurrency"],
    "environment": [
        "namespace", "provider", "region", "resources", "labels", "quotas",
    ],
    "cluster": ["provider", "region", "version", "nodes", "networking", "iam"],
    "secret_store": ["provider", "address", "path", "region", "namespace"],
    "resource": ["api_version", "kind", "spec"],
    "network_policy": [
        "target", "allow_ingress", "allow_egress", "block_all_ingress",
    ],
}

# sub-block names valid inside a block (they open a nested `{}`)
BLOCK_SUBBLOCKS = {
    "service": [
        "build", "resources", "health", "probes", "ingress", "schedule",
        "autoscale", "disruption", "network_policy", "topology", "affinity",
        "security", "lifecycle", "strategy",
    ],
    "database": ["backup", "users"],
    "queue": ["topics", "config", "users"],
    "storage": ["lifecycle"],
    "network": ["subnets", "policy"],
    "environment": ["resources", "quotas"],
    "cluster": ["nodes", "networking", "iam"],
}

# field label -> suggested value completions (after `:`)
FIELD_VALUE_HINTS = {
    "type": [
        "postgres", "mysql", "mariadb", "mongodb", "redis",
        "rabbitmq", "kafka", "elasticsearch",
    ],
    "strategy": ["rolling", "recreate", "blue_green", "canary"],
    "ssl": ["true", "false"],
    "expose": ["true", "false"],
    "persistence": ["true", "false"],
    "ha": ["true", "false"],
    "accessMode": ["rw", "ro", "rwo", "rwx"],
    "cpu": ["100m", "200m", "500m", "1000m"],
    "memory": ["128Mi", "256Mi", "512Mi", "1Gi"],
    "storage": ["10Gi", "20Gi", "50Gi", "100Gi"],
    "size": ["10Gi", "20Gi", "50Gi", "100Gi"],
    "replicas": ["1", "2", "3", "5"],
}

#: block -> field -> value hints, consulted before FIELD_VALUE_HINTS so a
#: shared label (e.g. ``provider``) can get block-specific suggestions.
FIELD_VALUE_HINTS_BY_BLOCK = {
    "secret_store": {"provider": ["vault", "aws", "gcp", "kubernetes"]},
}

# --------------------------------------------------------------------------- #
# Heuristic context detection
# --------------------------------------------------------------------------- #


def _current_block(lines: List[str], line: int) -> Optional[str]:
    """Return the name of the block the cursor is inside (or None at top level).

    Uses a lightweight brace-stack scan of all lines up to the cursor line,
    stopping at the cursor's own line. Tolerates incomplete input.
    """
    block_stack: List[str] = []
    for i in range(line + 1):
        text = lines[i] if i < len(lines) else ""
        stripped = text.split("#", 1)[0]  # drop line comments
        # handle the current line up to the cursor: only count braces before
        # the cursor so we know the context *at* the cursor.
        if i == line:
            pass  # we still scan the whole line; the cursor heuristic below is fine
        # opening blocks: a known keyword followed by an identifier and `{`
        # e.g. `service api {`, `environment prod extends dev {`; v0.5.0
        # tolerates quoted names (`secret_store "v" {`) and the two-name
        # custom-resource form (`resource "crd" "x" {`).
        import re as _re

        named = (
            r"([a-zA-Z_][a-zA-Z0-9_-]*)"
            r'\s+(?:"?[A-Za-z_][A-Za-z0-9_-]*"?\s+)?'
            r'"?[A-Za-z_][A-Za-z0-9_-]*"?\s*\{'
        )
        for m in _re.finditer(named, stripped):
            word = m.group(1)
            if word in BLOCK_FIELDS or word in TOP_LEVEL_BLOCKS:
                block_stack.append(word)
        # also handle `service {` (no name)
        anon = r"\b(%s)\s*\{" % "|".join(TOP_LEVEL_BLOCKS)
        for m in _re.finditer(anon, stripped):
            if not any(b == m.group(1) for b in block_stack):
                block_stack.append(m.group(1))
        # count closing braces
        closes = stripped.count("}")
        for _ in range(closes):
            if block_stack:
                block_stack.pop()
    return block_stack[-1] if block_stack else None


def _cursor_token(line_text: str, char: int) -> Tuple[str, str]:
    """Return (token_before_cursor, token_after_colon).

    token_before_cursor: the word being typed on the current line.
    token_after_colon: if the cursor follows a `:`, the label it belongs to.
    """
    prefix = line_text[:char]
    # if cursor is after a `:`, we're editing a value
    # find the last `:` before cursor on this line
    colon = prefix.rfind(":")
    if colon != -1:
        after = prefix[colon + 1 :]
        # treat as value context if only whitespace / a list opener / a quote
        # is between the colon and the cursor
        if not after.strip() or after.strip().startswith(("[", '"', "'")):
            label = ""
            left = prefix[:colon]
            for ch in reversed(left):
                if ch.isalnum() or ch == "_":
                    label = ch + label
                else:
                    break
            return ("", label)
    # otherwise we're typing a new token
    word = ""
    for ch in reversed(prefix):
        if ch.isalnum() or ch == "_" or ch == "-":
            word = ch + word
        else:
            break
    return (word, "")


def _document_symbols(source: str) -> List[str]:
    """Extract the names of all top-level blocks in the document.

    This is a light heuristic (regex over ``<keyword> <name> {``), tolerant of
    incomplete input, and used for symbol-aware completions (e.g. `depends`).
    """
    names: List[str] = []
    for line in source.splitlines():
        stripped = line.split("#", 1)[0]
        m = re.match(
            r"\s*(?:service|database|cache|queue|storage|network|secret|config"
            r"|pipeline|environment|cluster|secret_store|resource)"
            r'\s+"?([A-Za-z_][A-Za-z0-9_-]*)"?',
            stripped,
        )
        if m:
            names.append(m.group(1))
    return names


def completions_at(source: str, line: int, char: int) -> List[CompletionItem]:
    """Return completion items for the given (0-based) line/char position."""
    lines = source.splitlines()
    if not lines:
        lines = [""]
    block = _current_block(lines, line)
    line_text = lines[line] if line < len(lines) else ""
    token, label = _cursor_token(line_text, char)

    items: List[CompletionItem] = []

    if label:
        # symbol-aware: reference fields suggest names defined in the document
        if label in REFERENCE_FIELDS:
            for name in _document_symbols(source):
                if not token or name.startswith(token):
                    items.append(
                        CompletionItem(
                            label=name,
                            kind=CompletionItemKind.Struct,
                            detail=f"reference to '{name}'",
                            sort_text="1",
                        )
                    )
            return items
        # value completions for an enum/bool/quantity field; block-scoped
        # hints win over the generic table (e.g. provider inside secret_store)
        scoped = FIELD_VALUE_HINTS_BY_BLOCK.get(block or "", {})
        hints = scoped.get(label) if label in scoped else None
        if hints is None:
            hints = FIELD_VALUE_HINTS.get(label, [])
        for h in hints:
            items.append(
                CompletionItem(
                    label=h,
                    kind=CompletionItemKind.Value,
                    detail=f"value for '{label}'",
                    sort_text="0",
                )
            )
        return items

    if block:
        # inside a block: suggest sub-blocks first, then plain fields.
        # de-duplicate: a name that is both a sub-block and a field appears once.
        subblocks = BLOCK_SUBBLOCKS.get(block, [])
        fields = BLOCK_FIELDS.get(block, [])
        seen: set[str] = set()
        for sub in subblocks:
            if not token or sub.startswith(token):
                seen.add(sub)
                items.append(
                    CompletionItem(
                        label=sub,
                        kind=CompletionItemKind.Struct,
                        detail="block",
                        insert_text=f"{sub} {{\n    $0\n}}",
                        insert_text_format=InsertTextFormat.Snippet,
                    )
                )
        for field in fields:
            if field in seen:
                continue  # already offered as a sub-block snippet
            if not token or field.startswith(token):
                items.append(
                    CompletionItem(
                        label=field,
                        kind=CompletionItemKind.Field,
                        detail=f"field of '{block}'",
                        insert_text=f"{field}: ",
                    )
                )
        return items

    # top level: suggest block types
    for b in TOP_LEVEL_BLOCKS:
        if not token or b.startswith(token):
            items.append(
                CompletionItem(
                    label=b,
                    kind=CompletionItemKind.Struct,
                    detail="top-level block",
                    insert_text=f"{b} $0 {{\n    \n}}",
                    insert_text_format=InsertTextFormat.Snippet,
                )
            )
    return items
