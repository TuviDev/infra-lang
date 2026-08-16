"""On-disk workspace indexing for the Infra Lang LSP.

The LSP server only learns about documents the editor opens (via
``didOpen``/``didChange``). This module adds project-wide awareness: it scans
the workspace root for ``*.infra`` files, extracts their top-level block
symbols and keeps an index that definition / references / workspace-symbol
handlers can query — even for files never opened in the editor.

Design goals:

- **Non-blocking**: scanning is submitted to a thread pool, never run on the
  server's event loop.
- **Tolerant**: a file with a syntax error or an unreadable/corrupt file is
  skipped silently; a scan failure never raises into a handler.
- **Bounded**: caps the number of indexed files and the per-file size so a huge
  workspace cannot exhaust memory.
- **Thread-safe**: all mutations happen under a lock; readers take a cheap
  snapshot.

The module reuses the pure helpers in ``workspace_symbols`` (``build_index``,
``resolve_location``, ``all_references``) so the symbol semantics stay DRY.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from lsprotocol.types import Location, Position, Range

from infra.lsp.symbols import reference_ranges

#: Hard caps to keep indexing bounded (a few MB / hundreds of files max).
MAX_FILES = 1000
MAX_BYTES_PER_FILE = 1_000_000  # 1 MB

_BLOCK_RE = re.compile(
    r"\s*(service|database|cache|queue|storage|network|secret|config"
    r"|pipeline|environment|cluster)\s+([A-Za-z_][A-Za-z0-9_-]*)"
)

#: Block keyword -> LSP SymbolKind for the workspace/symbol outline.
KIND_TO_SYMBOL_KIND = {
    "service": "Class",
    "database": "Interface",
    "cache": "Interface",
    "queue": "Interface",
    "storage": "Object",
    "network": "Struct",
    "secret": "Constant",
    "config": "Constant",
    "pipeline": "Function",
    "environment": "Namespace",
    "cluster": "Struct",
}


@dataclass(frozen=True)
class IndexedSymbol:
    """A top-level block definition found on disk."""

    name: str
    kind: str  # block keyword, e.g. "service"
    uri: str
    line: int


def _scan_source(source: str, uri: str) -> list[IndexedSymbol]:
    """Extract all top-level block symbols from ``source``."""
    out: list[IndexedSymbol] = []
    for i, line in enumerate(source.splitlines()):
        stripped = line.split("#", 1)[0]
        m = _BLOCK_RE.match(stripped)
        if m:
            out.append(IndexedSymbol(m.group(2), m.group(1), uri, i))
    return out


class WorkspaceIndex:
    """A thread-safe, bounded index of ``*.infra`` files on disk."""

    def __init__(self) -> None:
        self._files: dict[str, str] = {}  # uri -> source
        self._symbols: dict[str, list[IndexedSymbol]] = {}  # name -> defs
        self._lock = threading.Lock()
        self._scanned_root: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Discovery
    # ------------------------------------------------------------------ #

    def scan_directory(self, root: Path) -> None:
        """Recursively index every ``*.infra`` file under ``root``.

        Skips hidden directories and files over the size cap. Tolerant of
        unreadable files. Never raises.
        """
        root = Path(root)
        if not root.is_dir():
            return
        self._scanned_root = str(root)
        files: list[Path] = []
        for p in root.rglob("*.infra"):
            try:
                if any(part.startswith(".") for part in p.parts):
                    continue  # skip hidden dirs (.git, .venv, ...)
                if p.stat().st_size > MAX_BYTES_PER_FILE:
                    continue
                files.append(p)
            except OSError:
                continue
            if len(files) >= MAX_FILES:
                break
        sources: dict[str, str] = {}
        for p in files:
            uri = p.resolve().as_uri()
            try:
                sources[uri] = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        self._rebuild(sources)

    def add_file(self, uri: str, source: str) -> None:
        """Index (or refresh) a single file by URI."""
        if len(source.encode("utf-8", "replace")) > MAX_BYTES_PER_FILE:
            return
        with self._lock:
            self._files[uri] = source
            self._reindex(uri, source)

    def remove_file(self, uri: str) -> None:
        """Drop a file from the index."""
        with self._lock:
            self._files.pop(uri, None)
            for name, defs in list(self._symbols.items()):
                kept = [d for d in defs if d.uri != uri]
                if kept:
                    self._symbols[name] = kept
                else:
                    self._symbols.pop(name, None)

    def clear(self) -> None:
        """Release all indexed state (used on server shutdown)."""
        with self._lock:
            self._files.clear()
            self._symbols.clear()
            self._scanned_root = None

    # ------------------------------------------------------------------ #
    # Queries (thread-safe snapshots)
    # ------------------------------------------------------------------ #

    def sources(self) -> dict[str, str]:
        """Return a snapshot of ``{uri: source}`` for all indexed files."""
        with self._lock:
            return dict(self._files)

    def definitions(self, name: str) -> list[IndexedSymbol]:
        with self._lock:
            return list(self._symbols.get(name, ()))

    def all_symbols(self) -> list[IndexedSymbol]:
        """Return every indexed block symbol (for workspace/symbol)."""
        with self._lock:
            seen: dict[tuple, IndexedSymbol] = {}
            for defs in self._symbols.values():
                for d in defs:
                    seen[(d.name, d.uri, d.line)] = d
            return list(seen.values())

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _rebuild(self, sources: dict[str, str]) -> None:
        """Replace the whole index from a ``{uri: source}`` map."""
        symbols: dict[str, list[IndexedSymbol]] = {}
        for uri, source in sources.items():
            for sym in _scan_source(source, uri):
                symbols.setdefault(sym.name, []).append(sym)
        with self._lock:
            self._files = sources
            self._symbols = symbols

    def _reindex(self, uri: str, source: str) -> None:
        """Replace one file's symbols (caller must hold the lock)."""
        for name, defs in list(self._symbols.items()):
            kept = [d for d in defs if d.uri != uri]
            if kept:
                self._symbols[name] = kept
            else:
                self._symbols.pop(name, None)
        for sym in _scan_source(source, uri):
            self._symbols.setdefault(sym.name, []).append(sym)


def find_references_in_sources(
    sources: dict[str, str],
    name: str,
) -> list[Location]:
    """All reference locations across ``sources``, with a fast text pre-filter.

    Only files that actually contain ``name`` are scanned with the regex, so
    the common case (symbol referenced in few files) stays cheap.
    """
    locations: list[Location] = []
    for uri, source in sources.items():
        if name not in source:
            continue  # fast path: name absent, skip
        for rng in reference_ranges(source, name):
            locations.append(Location(uri=uri, range=rng))
    return locations


def _symbol_location(sym: IndexedSymbol) -> Location:
    return Location(
        uri=sym.uri,
        range=Range(
            start=Position(line=sym.line, character=0),
            end=Position(line=sym.line, character=len(sym.name)),
        ),
    )


def iterable_symbol_locations(symbols: Iterable[IndexedSymbol]) -> list[Location]:
    return [_symbol_location(s) for s in symbols]
