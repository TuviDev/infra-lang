"""
Infra Lang LSP Server.

Implements the Language Server Protocol for .infra files.
Provides: diagnostics (errors and warnings on-the-fly) and keyword hover docs.

Start with: python -m infra.lsp.server
Or via CLI: infra lsp

Protocol: JSON-RPC over stdio (standard LSP transport).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional, cast

from lsprotocol.types import (
    INITIALIZED,
    TEXT_DOCUMENT_CODE_ACTION,
    TEXT_DOCUMENT_COMPLETION,
    TEXT_DOCUMENT_DEFINITION,
    TEXT_DOCUMENT_DID_CHANGE,
    TEXT_DOCUMENT_DID_CLOSE,
    TEXT_DOCUMENT_DID_OPEN,
    TEXT_DOCUMENT_DID_SAVE,
    TEXT_DOCUMENT_DOCUMENT_HIGHLIGHT,
    TEXT_DOCUMENT_DOCUMENT_SYMBOL,
    TEXT_DOCUMENT_FOLDING_RANGE,
    TEXT_DOCUMENT_FORMATTING,
    TEXT_DOCUMENT_HOVER,
    TEXT_DOCUMENT_PREPARE_RENAME,
    TEXT_DOCUMENT_PUBLISH_DIAGNOSTICS,
    TEXT_DOCUMENT_REFERENCES,
    TEXT_DOCUMENT_RENAME,
    TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL,
    TEXT_DOCUMENT_SIGNATURE_HELP,
    WORKSPACE_SYMBOL,
    CodeAction,
    CodeActionParams,
    CodeDescription,
    CompletionList,
    CompletionParams,
    DefinitionParams,
    Diagnostic,
    DiagnosticRelatedInformation,
    DiagnosticSeverity,
    DidChangeTextDocumentParams,
    DidCloseTextDocumentParams,
    DidOpenTextDocumentParams,
    DidSaveTextDocumentParams,
    DocumentFormattingParams,
    DocumentHighlight,
    DocumentHighlightKind,
    DocumentHighlightParams,
    DocumentSymbol,
    DocumentSymbolParams,
    FoldingRange,
    FoldingRangeParams,
    Hover,
    HoverParams,
    InitializedParams,
    Location,
    MarkupContent,
    MarkupKind,
    Position,
    PrepareRenameParams,
    PrepareRenameResult,
    PublishDiagnosticsParams,
    Range,
    ReferenceParams,
    RenameParams,
    SemanticTokens,
    SemanticTokensLegend,
    SemanticTokensParams,
    SignatureHelp,
    SignatureHelpOptions,
    SignatureHelpParams,
    SymbolKind,
    TextEdit,
    WorkspaceEdit,
    WorkspaceSymbol,
    WorkspaceSymbolParams,
)

from ..errors.exceptions import InfraLexError, InfraParseError
from ..lsp.completion import completions_at
from ..lsp.quickfix import quick_fixes
from ..lsp.semantic_tokens import TOKEN_TYPES, encode_delta, tokenize_source
from ..lsp.symbols import (
    document_symbols,
    find_definition,
    highlight_ranges,
    reference_ranges,
    rename_edits,
    symbol_at,
    symbol_range,
)
from ..lsp.workspace_index import (
    KIND_TO_SYMBOL_KIND,
    WorkspaceIndex,
    find_references_in_sources,
    iterable_symbol_locations,
)
from ..lsp.workspace_symbols import block_definitions, build_index, resolve_location
from ..parser.location import SourceLocation

try:
    # pygls 2.x exposure point for the high-level server class.
    from pygls.lsp.server import LanguageServer
except ImportError:  # pragma: no cover - tied to pygls 1.3.x installs
    # pygls 1.3.x keeps the class at the legacy location; pygls 2.x removed
    # the re-export, so mypy (running against 2.x) needs both ignores.
    from pygls.server import LanguageServer  # type: ignore[no-redef, attr-defined]

try:
    # attr-defined: lsprotocol 2023.x (pygls 1.3.x stack) does not ship this
    # symbol at all; the ignore is inert (and suppressed per-module in
    # pyproject.toml) on lsprotocol 2024+/pygls 2.x, where the import works.
    from lsprotocol.types import PrepareRenamePlaceholder  # type: ignore[attr-defined]
except ImportError:
    # pygls 1.3.1 pins lsprotocol 2023.x, which predates this type; the
    # prepare-rename handler then falls back to returning a plain Range.
    PrepareRenamePlaceholder = None  # type: ignore[assignment, misc]

try:
    # attr-defined: lsprotocol 2023.x (pygls 1.3.x stack) lacks this alias;
    # see the PrepareRenamePlaceholder import above for the ignore policy.
    from lsprotocol.types import (  # type: ignore[attr-defined]
        TextDocumentContentChangePartial,  # noqa: F401
    )
except ImportError:  # pragma: no cover - tied to lsprotocol 2023.x installs
    # Compatibility probe: lsprotocol 2023.x (pygls 1.3.1) ships only the
    # anonymous union shapes ``TextDocumentContentChangeEvent_Type1/_Type2``
    # and lacks this named alias entirely. Importing it must therefore never
    # be hard-required at module scope. ``did_change`` intentionally reads
    # ``change.text`` (a field both union members expose on every lsprotocol
    # generation), so no runtime reference to this symbol is needed here;
    # the guarded import keeps feature detection possible for embedders and
    # documents the porting contract next to the PrepareRenamePlaceholder one.
    TextDocumentContentChangePartial = None  # type: ignore[assignment, misc]

server = LanguageServer(
    name="infra-lang",
    version="0.7.1",
)

#: Project-wide on-disk symbol index. Scanned after initialization; consulted by
#: definition / references / workspace-symbol handlers for files not open in the
#: editor. Guarded internally by a lock; safe to touch from the event loop.
workspace_index = WorkspaceIndex()


def _shutdown_release_index() -> None:
    """Release the on-disk index when the server shuts down."""
    workspace_index.clear()


# Wrap pygls's own shutdown so the in-memory index is freed on exit, without
# interfering with pygls's protocol-level shutdown handling.
_orig_shutdown: Callable[[], None] = server.shutdown


def _shutdown_with_cleanup() -> None:
    try:
        _shutdown_release_index()
    finally:
        _orig_shutdown()


server.shutdown = _shutdown_with_cleanup  # type: ignore[method-assign]

_ERR_SEC = {"SEC001", "SEC002", "SEC004", "SEC007"}

#: Base URL for the hosted language-spec docs, used for diagnostic code links.
_DOCS_BASE = "https://TuviDev.github.io/infra-lang/language_spec/"

#: Codes that indicate a duplicate definition -> point at the sibling(s).
_DUPLICATE_CODES = {"E002", "E001"}


def _code_href(code: str) -> str:
    """Return a docs URL for a diagnostic code (used for clickable links)."""
    return f"{_DOCS_BASE}#{code.lower()}"


def _related_for_duplicate(
    source: str, uri: str, code: str, message: str, current_line: int
) -> list[DiagnosticRelatedInformation]:
    """Find sibling definitions of the same name for duplicate-name errors.

    Returns DiagnosticRelatedInformation pointing at every other block
    definition with the duplicated name.
    """
    import re

    if code not in _DUPLICATE_CODES:
        return []
    m = re.search(r"'([^']+)'", message)
    if not m:
        return []
    name = m.group(1)
    related: list[DiagnosticRelatedInformation] = []
    for other_name, line in block_definitions(source):
        if other_name == name and line != current_line:
            related.append(
                DiagnosticRelatedInformation(
                    location=Location(
                        uri=uri,
                        range=Range(
                            start=Position(line=line, character=0),
                            end=Position(line=line, character=len(name)),
                        ),
                    ),
                    message=f"Earlier definition of '{name}'",
                )
            )
    return related


def _severity(code: str | None) -> DiagnosticSeverity:
    """Map an Infra error/warning code to an LSP severity."""
    if code is None:
        return DiagnosticSeverity.Warning
    if code.startswith("E"):
        return DiagnosticSeverity.Error
    if code.startswith("SEC") and code in _ERR_SEC:
        return DiagnosticSeverity.Error
    return DiagnosticSeverity.Warning


def _location_to_range(location: SourceLocation | None, source: str) -> Range:
    if location is None:
        return Range(
            start=Position(line=0, character=0),
            end=Position(line=0, character=1),
        )
    line = max(0, location.line - 1)
    col = max(0, location.column)
    end_col = col + 10
    lines = source.splitlines()
    if line < len(lines):
        end_col = max(col + 1, len(lines[line]))
    return Range(
        start=Position(line=line, character=col),
        end=Position(line=line, character=end_col),
    )


def _diagnose(source: str, uri: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    try:
        from ..analyzer.validator import SemanticValidator
        from ..parser import parse

        program = parse(source, filename=uri)
        result = SemanticValidator().validate(program)

        for error in result.errors:
            code = error.code or "E000"
            rng = _location_to_range(error.location, source)
            cur_line = rng.start.line
            related = _related_for_duplicate(
                source, uri, code, error.message, cur_line
            )
            diagnostics.append(
                Diagnostic(
                    range=rng,
                    message=error.message,
                    severity=DiagnosticSeverity.Error,
                    code=code,
                    source="infra-lang",
                    code_description=CodeDescription(href=_code_href(code)),
                    related_information=related or None,
                )
            )

        for warning in result.warnings:
            code = warning.code or "W000"
            rng = _location_to_range(warning.location, source)
            cur_line = rng.start.line
            related = _related_for_duplicate(
                source, uri, code, warning.message, cur_line
            )
            diagnostics.append(
                Diagnostic(
                    range=rng,
                    message=warning.message,
                    severity=DiagnosticSeverity.Warning,
                    code=code,
                    source="infra-lang",
                    code_description=CodeDescription(href=_code_href(code)),
                    related_information=related or None,
                )
            )

    except (InfraParseError, InfraLexError) as e:
        line = max(0, (getattr(e, "line", 1) or 1) - 1)
        col = max(0, getattr(e, "column", 0) or 0)
        diagnostics.append(
            Diagnostic(
                range=Range(
                    start=Position(line=line, character=col),
                    end=Position(line=line, character=col + 5),
                ),
                message=f"Parse error: {e}",
                severity=DiagnosticSeverity.Error,
                code="PARSE",
                source="infra-lang",
                code_description=CodeDescription(href=_code_href("parse")),
            )
        )

    except Exception as e:  # pragma: no cover - defensive
        diagnostics.append(
            Diagnostic(
                range=Range(
                    start=Position(line=0, character=0),
                    end=Position(line=0, character=1),
                ),
                message=f"Internal error: {e}",
                severity=DiagnosticSeverity.Error,
                code="INTERNAL",
                source="infra-lang",
            )
        )

    return diagnostics


def _publish(ls: LanguageServer, uri: str, source: str) -> None:
    diagnostics = _diagnose(source, uri)
    legacy = getattr(ls, "publish_diagnostics", None)
    if callable(legacy):
        # pygls 1.x convenience API (also used by unit-test fakes)
        legacy(uri, diagnostics)
        return
    # pygls 2.x: server->client notifications go through the protocol layer
    ls.protocol.notify(
        TEXT_DOCUMENT_PUBLISH_DIAGNOSTICS,
        PublishDiagnosticsParams(uri=uri, diagnostics=diagnostics),
    )


def _workspace_documents(ls: LanguageServer) -> dict[str, str]:
    """Return ``{uri: source}`` for every known document.

    Merges the on-disk index with documents open in the editor; the open
    (live) version wins over the stale on-disk copy. Returns an empty dict when
    the workspace exposes no document store (keeps the single-document handlers
    backward compatible in unit tests).
    """
    sources: dict[str, str] = dict(workspace_index.sources())
    docs: dict[str, Any] = {}
    # pygls 2.x exposes ``text_documents``; 1.x used ``documents`` — take the
    # first one that exists.
    for attr in ("text_documents", "documents"):
        try:
            candidate = getattr(ls.workspace, attr)
        except Exception:  # noqa: BLE001 - defensive, missing attribute
            continue
        if isinstance(candidate, dict):
            docs = candidate
            break
    for uri, doc in docs.items():
        src = doc.source if hasattr(doc, "source") else "\n".join(doc.lines)
        sources[uri] = src  # open document wins
    return sources


def _doc_source(ls: LanguageServer, uri: str) -> str:
    try:
        doc = ls.workspace.get_text_document(uri)
        return doc.source if hasattr(doc, "source") else "\n".join(doc.lines)
    except Exception:  # noqa: BLE001 - fall back to the on-disk index
        return workspace_index.sources().get(uri, "")


def _root_dir(ls: LanguageServer) -> Optional[Path]:
    """Return the workspace root directory as a Path, if known."""
    try:
        root = ls.workspace.root_uri or ""
        if root:
            from urllib.parse import unquote, urlparse

            parsed = urlparse(root)
            return Path(unquote(parsed.path))
        root = ls.workspace.root_path
        if root:
            return Path(root)
    except Exception:  # noqa: BLE001 - defensive
        return None
    return None


@server.feature(INITIALIZED)
def initialized(ls: LanguageServer, params: InitializedParams) -> None:
    """Kick off a non-blocking scan of the workspace root for *.infra files.

    Runs on the server's thread pool so disk I/O never blocks the event loop.
    Any failure is swallowed (the server simply serves the open documents).
    """
    root = _root_dir(ls)
    if root is None:
        return
    try:
        # pygls 1.x: thread_pool_executor; pygls 2.x: thread_pool
        executor = getattr(ls, "thread_pool_executor", None) or getattr(
            ls, "thread_pool", None
        )
    except Exception:  # noqa: BLE001
        executor = None
    scan: Callable[[], None] = lambda: workspace_index.scan_directory(root)  # noqa: E731
    if executor is not None:
        executor.submit(scan)
    else:
        try:
            scan()
        except Exception:  # noqa: BLE001 - scanning must never raise
            pass


@server.feature(WORKSPACE_SYMBOL)
def workspace_symbol(
    ls: LanguageServer,
    params: WorkspaceSymbolParams,
) -> list[WorkspaceSymbol]:
    """Return every top-level resource in the whole project (Ctrl+T)."""
    query = (params.query or "").lower()
    symbols = workspace_index.all_symbols()
    if query:
        symbols = [s for s in symbols if query in s.name.lower()]
    symbols.sort(key=lambda s: (s.kind, s.name))
    return [
        WorkspaceSymbol(
            name=s.name,
            kind=getattr(SymbolKind, KIND_TO_SYMBOL_KIND.get(s.kind, "Class")),
            location=iterable_symbol_locations([s])[0],
            container_name=s.uri,
        )
        for s in symbols
    ]


@server.feature(
    TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL,
    options=SemanticTokensLegend(
        token_types=list(TOKEN_TYPES), token_modifiers=[]
    ),
)
def semantic_tokens_full(
    ls: LanguageServer,
    params: SemanticTokensParams,
) -> SemanticTokens:
    """Return LSP semantic tokens for a document.

    Uses a deterministic, line-based tokenizer so malformed / incomplete input
    never raises — the editor always gets a (possibly partial) token stream.
    """
    source = _doc_source(ls, params.text_document.uri)
    tokens = tokenize_source(source)
    return SemanticTokens(data=encode_delta(tokens))


@server.feature(
    TEXT_DOCUMENT_SIGNATURE_HELP,
    options=SignatureHelpOptions(
        trigger_characters=["{", "\n", "."], retrigger_characters=[","]
    ),
)
def signature_help(
    ls: LanguageServer,
    params: SignatureHelpParams,
) -> Optional[SignatureHelp]:
    """Show the fields available inside the block the cursor is in."""
    source = _doc_source(ls, params.text_document.uri)
    from ..lsp.signature import signature_help_at

    return signature_help_at(
        source,
        max(0, params.position.line),
        max(0, params.position.character),
    )


@server.feature(TEXT_DOCUMENT_DOCUMENT_HIGHLIGHT)
def document_highlight(
    ls: LanguageServer,
    params: DocumentHighlightParams,
) -> list[DocumentHighlight]:
    """Highlight every occurrence of the symbol under the cursor in the file."""
    source = _doc_source(ls, params.text_document.uri)
    line = max(0, params.position.line)
    char = max(0, params.position.character)
    name, ranges = highlight_ranges(source, line, char)
    if not name:
        return []
    return [
        DocumentHighlight(
            range=rng,
            kind=(
                DocumentHighlightKind.Write
                if kind == "write"
                else DocumentHighlightKind.Read
            ),
        )
        for rng, kind in ranges
    ]


@server.feature(TEXT_DOCUMENT_FOLDING_RANGE)
def folding_range(
    ls: LanguageServer,
    params: FoldingRangeParams,
) -> list[FoldingRange]:
    """Return foldable regions (blocks + comment runs) for the document."""
    source = _doc_source(ls, params.text_document.uri)
    from ..lsp.folding import folding_ranges

    return folding_ranges(source)


@server.feature(TEXT_DOCUMENT_DID_OPEN)
def did_open(
    ls: LanguageServer,
    params: DidOpenTextDocumentParams,
) -> None:
    _publish(ls, params.text_document.uri, params.text_document.text)


@server.feature(TEXT_DOCUMENT_DID_CHANGE)
def did_change(
    ls: LanguageServer,
    params: DidChangeTextDocumentParams,
) -> None:
    source = params.content_changes[-1].text
    _publish(ls, params.text_document.uri, source)


@server.feature(TEXT_DOCUMENT_DID_SAVE)
def did_save(
    ls: LanguageServer,
    params: DidSaveTextDocumentParams,
) -> None:
    source = params.text or ""
    _publish(ls, params.text_document.uri, source)
    if params.text:  # keep the on-disk index in sync with the saved file
        workspace_index.add_file(params.text_document.uri, params.text)


@server.feature(TEXT_DOCUMENT_DID_CLOSE)
def did_close(ls: LanguageServer, params: DidCloseTextDocumentParams) -> None:
    """On close, restore the file's on-disk state into the index.

    The editor's in-memory copy is gone, so re-read the disk version so
    cross-file navigation stays correct for the saved content.
    """
    uri = params.text_document.uri
    try:
        from urllib.parse import unquote, urlparse
        from urllib.request import url2pathname

        # url2pathname converts a file:// path to the native filesystem path on
        # every platform (on Windows it handles the leading-slash drive form
        # ``/C:/...`` and UNC paths; on POSIX it is just unquote). Without it,
        # Windows would see ``Path("/C:/...")`` which never exists, so the
        # on-disk file would be wrongly dropped from the index on close.
        path = Path(url2pathname(unquote(urlparse(uri).path)))
        if path.exists():
            workspace_index.add_file(uri, path.read_text(encoding="utf-8"))
        else:
            workspace_index.remove_file(uri)
    except Exception:  # noqa: BLE001 - closing must never raise
        workspace_index.remove_file(uri)


@server.feature(TEXT_DOCUMENT_COMPLETION)
def completion(
    ls: LanguageServer,
    params: CompletionParams,
) -> CompletionList:
    """Provide context-aware completion suggestions.

    Heuristic, tolerant of incomplete input (the user is mid-edit). Uses the
    text around the cursor; never relies on a full parse succeeding.
    """
    doc = ls.workspace.get_text_document(params.text_document.uri)
    source = doc.source if hasattr(doc, "source") else "\n".join(doc.lines)
    line = max(0, params.position.line)
    char = max(0, params.position.character)
    items = completions_at(source, line, char)
    return CompletionList(is_incomplete=False, items=items)


@server.feature(TEXT_DOCUMENT_DOCUMENT_SYMBOL)
def document_symbol(
    ls: LanguageServer,
    params: DocumentSymbolParams,
) -> list[DocumentSymbol]:
    """Provide a document outline (top-level blocks)."""
    doc = ls.workspace.get_text_document(params.text_document.uri)
    source = doc.source if hasattr(doc, "source") else "\n".join(doc.lines)
    return document_symbols(source)


@server.feature(TEXT_DOCUMENT_DEFINITION)
def definition(
    ls: LanguageServer,
    params: DefinitionParams,
) -> Location | None:
    """Go-to-definition for block names and references, including cross-file."""
    uri = params.text_document.uri
    source = _doc_source(ls, uri)
    line = max(0, params.position.line)
    char = max(0, params.position.character)
    resolved = find_definition(source, line, char)
    if resolved is not None:
        name, def_line = resolved
        def_line = max(0, def_line)
        return Location(
            uri=uri,
            range=Range(
                start=Position(line=def_line, character=0),
                end=Position(line=def_line, character=len(name)),
            ),
        )
    # not resolvable within this document — try across the open workspace
    cross_name = symbol_at(source, line, char)
    if not cross_name:
        return None
    docs = _workspace_documents(ls)
    if not docs:
        return None
    return resolve_location(build_index(docs), docs, uri, cross_name)


@server.feature(TEXT_DOCUMENT_REFERENCES)
def references(
    ls: LanguageServer,
    params: ReferenceParams,
) -> list[Location]:
    """Find references to a symbol, including across the open workspace."""
    uri = params.text_document.uri
    source = _doc_source(ls, uri)
    line = max(0, params.position.line)
    char = max(0, params.position.character)
    resolved = find_definition(source, line, char)
    name: str | None = resolved[0] if resolved else symbol_at(source, line, char)
    if not name:
        return []
    docs = _workspace_documents(ls)
    if not docs:
        # single-document fallback: reference_ranges yields Range objects, but
        # the LSP references result is a list of Location (uri + range), so wrap
        # each range with the current document's uri.
        return [
            Location(uri=uri, range=rng) for rng in reference_ranges(source, name)
        ]
    # definition sites (so "find references" includes the declaration),
    # derived from the merged (disk + open) source map.
    locations: list[Location] = []
    for defn in build_index(docs).get(name, []):
        locations.append(
            Location(
                uri=defn.uri,
                range=Range(
                    start=Position(line=defn.line, character=0),
                    end=Position(line=defn.line, character=len(defn.name)),
                ),
            )
        )
    # all reference occurrences, with a fast "name present?" pre-filter
    locations.extend(find_references_in_sources(docs, name))
    return locations


@server.feature(TEXT_DOCUMENT_PREPARE_RENAME)
def prepare_rename(
    ls: LanguageServer,
    params: PrepareRenameParams,
) -> PrepareRenameResult | None:
    """Validate that the position is a renameable symbol.

    Returns the range of the symbol under the cursor plus its current name as
    the pre-filled placeholder, or ``None`` if the position is not renameable
    (so the editor keeps rename disabled / reports "cannot rename").
    """
    uri = params.text_document.uri
    source = _doc_source(ls, uri)
    line = max(0, params.position.line)
    char = max(0, params.position.character)
    resolved = find_definition(source, line, char)
    if resolved is None:
        return None
    name, _ = resolved
    rng = symbol_range(source, line, char)
    if rng is None:
        return None
    if PrepareRenamePlaceholder is not None:
        # cast: on the legacy lsprotocol 2023.x stack the guarded import
        # above left this name as ``Any``, which --strict would report as
        # returning Any from a typed function; on the modern stack the
        # expression is already a ``PrepareRenamePlaceholder`` (a valid
        # ``PrepareRenameResult`` member), so the cast is a no-op there.
        return cast(
            PrepareRenameResult,
            PrepareRenamePlaceholder(range=rng, placeholder=name),
        )
    # lsprotocol 2023 (pygls 1.3.1): PrepareRenamePlaceholder does not exist;
    # a plain Range is a valid prepare-rename response there.
    return rng


@server.feature(TEXT_DOCUMENT_RENAME)
def rename(
    ls: LanguageServer,
    params: RenameParams,
) -> WorkspaceEdit | None:
    """Rename a block (and its references) across the open workspace."""
    uri = params.text_document.uri
    source = _doc_source(ls, uri)
    line = max(0, params.position.line)
    char = max(0, params.position.character)
    resolved = find_definition(source, line, char)
    if resolved is None:
        return None
    old_name, _ = resolved
    new_name = params.new_name
    if new_name == old_name:
        return WorkspaceEdit(changes={})
    changes: dict[str, list[TextEdit]] = {}
    current_edits = [
        TextEdit(range=rng, new_text=text)
        for rng, text in rename_edits(source, old_name, new_name)
    ]
    if current_edits:
        changes[uri] = current_edits
    for other_uri, other_source in _workspace_documents(ls).items():
        if other_uri == uri:
            continue
        edits = [
            TextEdit(range=rng, new_text=text)
            for rng, text in rename_edits(other_source, old_name, new_name)
        ]
        if edits:
            changes[other_uri] = edits
    return WorkspaceEdit(changes=changes)


@server.feature(TEXT_DOCUMENT_FORMATTING)
def formatting(
    ls: LanguageServer,
    params: DocumentFormattingParams,
) -> list[TextEdit]:
    """Format the whole document via the existing AST pretty-printer."""
    from infra.cli.printer import format_source

    doc = ls.workspace.get_text_document(params.text_document.uri)
    source = doc.source if hasattr(doc, "source") else "\n".join(doc.lines)
    try:
        formatted = format_source(source)
    except Exception:  # noqa: BLE001 - don't break the editor on bad input
        return []
    if formatted == source:
        return []
    lines = formatted.splitlines()
    return [
        TextEdit(
            range=Range(
                start=Position(line=0, character=0),
                end=Position(line=max(0, len(lines) - 1), character=10**6),
            ),
            new_text=formatted,
        )
    ]


@server.feature(TEXT_DOCUMENT_CODE_ACTION)
def code_action(
    ls: LanguageServer,
    params: CodeActionParams,
) -> list[CodeAction]:
    """Provide quick fixes for diagnostics in the requested range."""
    doc = ls.workspace.get_text_document(params.text_document.uri)
    source = doc.source if hasattr(doc, "source") else "\n".join(doc.lines)
    return quick_fixes(
        params.text_document.uri,
        source,
        list(params.context.diagnostics),
    )


# --------------------------------------------------------------------------- #
# Hover documentation
# --------------------------------------------------------------------------- #

FIELD_DOCS = {
    "image": "Docker image to use.\nExample: `nginx:1.25.3`",
    "replicas": "Number of pod replicas.\nMust be >= 1.",
    "port": "Port the container listens on (1-65535).",
    "health": "Health check configuration.\nExample: `http(\"/health\")`",
    "resources": "CPU and memory requests/limits.",
    "autoscale": (
        "Horizontal Pod Autoscaler config.\n"
        "Example: `{ min: 2, max: 10, target_cpu: 70 }`"
    ),
    "disruption": "Pod Disruption Budget.\nExample: `{ min_available: 1 }`",
    "network_policy": (
        "Network policy declaration. Per-service sub-block, or the top-level "
        "v0.5.1 form.\n"
        'Example: `network_policy "app_sec" { target: "api", ... }`'
    ),
    "schedule": "Time-based scaling schedule.",
    "affinity": "Pod affinity/anti-affinity rules.",
    "topology": "TopologySpreadConstraints.",
    "type": (
        "Database/cache/queue type.\n"
        "For database: postgres, mysql, mariadb, mongodb, redis"
    ),
    "storage": "Storage size. Example: `20Gi`",
    "ssl": "Enable SSL/TLS. Recommended: `true`",
    "backup": "Backup configuration for databases.",
    "service": "Service definition block.",
    "database": "Database definition block.",
    "cache": "Cache definition block.",
    "queue": "Message queue definition block.",
    "pipeline": "CI/CD pipeline definition.",
    "environment": "Environment/namespace definition.",
    "cluster": "Kubernetes cluster definition.",
    "secret": "Secret definition. Values loaded from external sources.",
    "config": "ConfigMap definition.",
    "build": "Container image build config.",
    "ports": "List of ports.\nExample: `[8080, 9090]`",
    "env": "Environment variables for the container.",
    "envFrom": "Bulk env source (ConfigMap/Secret).",
    "command": "Override the container entrypoint command.",
    "args": "Arguments passed to the container command.",
    "probes": "Liveness/readiness/startup probe config.",
    "volumes": "Storage volumes mounted into the container.",
    "depends": "Other services this one depends on.\nExample: `[db, cache]`",
    "depends_on": (
        "Hard service-start ordering (v0.4.5). Targets may be services or "
        "resources.\nExample: `[db, cache]`"
    ),
    "target": "Service the policy applies to.",
    "allow_ingress": "Workloads allowed to initiate connections to the target.",
    "allow_egress": "Workloads the target is allowed to connect to.",
    "block_all_ingress": "Drop all inbound traffic to the target (deny-all).",
    "secret_store": (
        "External secret store declaration (v0.5.0).\n"
        'Example: `secret_store "vault_store" { provider: "vault", ... }`'
    ),
    "store": (
        "Reference to a `secret_store` backing this secret (v0.5.0).\n"
        'Example: `store: "vault_store"`'
    ),
    "resource": (
        "Generic custom resource (CRD) declaration (v0.5.0).\n"
        'Example: `resource "crd" "name" { api_version: ..., kind: ... }`'
    ),
    "labels": "Kubernetes labels for the resource.\nExample: `{ tier: \"web\" }`",
    "annotations": "Kubernetes annotations.\nExample: `{ team: \"platform\" }`",
    "strategy": "Deployment strategy.\nValues: rolling, recreate, blue_green, canary",
    "security": "Security context: user, group, capabilities, seccomp, selinux.",
    "lifecycle": "Pod lifecycle hooks (postStart/preStop).",
    "ingress": "Ingress/exposure config.\nExample: `{ host: \"api.example.com\" }`",
    "expose": "Expose the service externally (LoadBalancer).\nValue: true/false",
    "version": "Version of the software / engine.",
    "size": "Storage/volume size. Example: `20Gi`",
    "ha": "High-availability mode.\nValue: true/false",
    "users": "Database/queue users.\nExample: `{ admin: \"secret\" }`",
    "quotas": "Namespace resource quotas.\nExample: `{ max_cpu: 10cores }`",
    "namespace": "Kubernetes namespace for this environment.",
}



@server.feature(TEXT_DOCUMENT_HOVER)
def hover(
    ls: LanguageServer,
    params: HoverParams,
) -> Hover | None:
    doc = ls.workspace.get_text_document(params.text_document.uri)
    line_text = (
        doc.lines[params.position.line]
        if params.position.line < len(doc.lines)
        else ""
    )

    word = _get_word_at(line_text, params.position.character)

    if word and word in FIELD_DOCS:
        return Hover(
            contents=MarkupContent(
                kind=MarkupKind.Markdown,
                value=f"**{word}**\n\n{FIELD_DOCS[word]}",
            )
        )
    return None


def _get_word_at(line: str, char: int) -> str | None:
    if not line:
        return None
    char = max(0, min(char, len(line)))
    start = char
    while start > 0 and (line[start - 1].isalnum() or line[start - 1] == "_"):
        start -= 1
    end = char
    while end < len(line) and (line[end].isalnum() or line[end] == "_"):
        end += 1
    word = line[start:end]
    return word if word else None


def main() -> None:
    server.start_io()


if __name__ == "__main__":
    main()
