"""Cross-file workspace symbol index for the Infra Lang LSP.

Builds an index of top-level block definitions across all known ``.infra``
documents (imports and references can point across files, e.g. ``depends`` on a
service defined in an imported file). Provides:

- ``build_index``: map block name -> list of definition sites (uri + line).
- ``resolve_location``: find the definition of a name, preferring the current
  document, else any other open document.
- ``all_references``: every definition and reference range across all files.

This module is pure (takes ``{uri: source}`` mappings) so it is directly unit
testable without a live LSP server.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Optional

from lsprotocol.types import Location, Position, Range

from infra.lsp.symbols import reference_ranges

_BLOCK_RE = re.compile(
    r"\s*(service|database|cache|queue|storage|network|secret|config"
    r"|pipeline|environment|cluster)\s+([A-Za-z_][A-Za-z0-9_-]*)"
)


@dataclass(frozen=True)
class SymbolDef:
    """A single definition site for a top-level block."""

    name: str
    uri: str
    line: int


def block_definitions(source: str) -> list[tuple[str, int]]:
    """Return ``(name, line)`` for every top-level block in ``source``."""
    out: list[tuple[str, int]] = []
    for i, line in enumerate(source.splitlines()):
        stripped = line.split("#", 1)[0]
        m = _BLOCK_RE.match(stripped)
        if m:
            out.append((m.group(2), i))
    return out


def build_index(sources: Mapping[str, str]) -> dict[str, list[SymbolDef]]:
    """Index block definitions across every document in ``sources``."""
    index: dict[str, list[SymbolDef]] = {}
    for uri, source in sources.items():
        for name, line in block_definitions(source):
            index.setdefault(name, []).append(SymbolDef(name, uri, line))
    return index


def _def_location(defn: SymbolDef) -> Location:
    return Location(
        uri=defn.uri,
        range=Range(
            start=Position(line=defn.line, character=0),
            end=Position(line=defn.line, character=len(defn.name)),
        ),
    )


def resolve_location(
    index: Mapping[str, list[SymbolDef]],
    sources: Mapping[str, str],
    current_uri: str,
    name: str,
) -> Optional[Location]:
    """Resolve ``name`` to a definition, preferring the current document."""
    defs = index.get(name)
    if not defs:
        return None
    for defn in defs:
        if defn.uri == current_uri:
            return _def_location(defn)
    return _def_location(defs[0])


def all_references(
    index: Mapping[str, list[SymbolDef]],
    sources: Mapping[str, str],
    name: str,
) -> list[Location]:
    """Return every definition and reference site for ``name`` across files."""
    locations: list[Location] = []
    for defn in index.get(name, []):
        locations.append(_def_location(defn))
    for uri, source in sources.items():
        for rng in reference_ranges(source, name):
            locations.append(Location(uri=uri, range=rng))
    return locations
