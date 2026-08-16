# Infra Lang Language Server (LSP)

## What it provides
- **Diagnostics**: errors and warnings shown inline as you type. No need to
  run `infra validate`.
- **Hover**: documentation for block keywords and fields.
- **Completion**: context-aware autocompletion —
  - top-level block keywords (with snippet expansion),
  - per-block fields (`service`, `database`, `cache`, `queue`, ...),
  - enum / bool / quantity value hints after `:`,
  - sub-block suggestions (`resources`, `ingress`, `backup`, ...),
  - **symbol-aware**: `depends`, `allow_from`, `allow_egress` suggest the
    names of blocks already defined in the document.
  Completion is heuristic and works even on incomplete / malformed input
  while you type.
- **Document symbols**: an outline of all top-level blocks (Ctrl+Shift+O).
- **Workspace symbols**: list/search every top-level resource in the whole
  project (Ctrl+T in VS Code). Results are grouped by resource type.
- **Go to definition**: jump from a reference (`depends: [db]`) to the block
  definition, or from a block name to its definition line. **Cross-file
  (whole project)**: on startup the server scans the workspace root for
  `*.infra` files and indexes their blocks, so definition resolves across any
  file in the project — open in the editor or not.
- **Find references**: locate all references to a symbol across the whole
  project, not just the current file.
- **Rename symbol**: rename a block and all of its references in one action
  (F2 in VS Code). Rename applies to the current document and any other open
  document that references the symbol. Comments are left untouched. A
  `prepareRename` step validates the position and pre-fills the current name
  in the rename box.

## Whole-project indexing

On the `initialized` notification the server scans the workspace root
recursively for `*.infra` files and builds a **WorkspaceIndex** (block name →
file + line). Design:

- **Non-blocking**: the scan runs on pygls's thread pool, never on the event
  loop; the server keeps serving the editor meanwhile.
- **Tolerant**: files with syntax errors or unreadable files are skipped
  silently; a bad file never breaks navigation for the rest of the project.
- **Bounded**: caps indexing at 1000 files and 1 MB per file, so a huge
  workspace cannot exhaust memory. Files in hidden directories (`.git`,
  `.venv`, …) are ignored.
- **Live**: `didSave` refreshes the index for the saved file; `didClose`
  restores the on-disk version. The in-memory index is freed on shutdown.
- The current editor buffers always take precedence over the on-disk copy.

## Not yet supported
- Cross-file rename across files on disk that are not open in the editor
  (rename applies to documents currently open in the workspace).
- **Formatting**: `infra fmt` formatting available as document formatting
  (format-on-save via the extension).
- **Code actions (quick fixes)**: safe, automatic fixes for common findings —
  e.g. E011 `replicas: 0` → `replicas: 1`, E012 port out of range → a valid
  port. Only deterministic, safe rewrites are offered.

## Not yet supported
- Cross-file rename across files on disk that are not open in the editor
  (rename applies to documents currently open in the workspace).

## Installation

### 1. Install infra-lang with LSP support

```bash
pip install 'git+https://github.com/kakukpl/infra-lang.git[lsp]'
```

### 2. Install the VS Code extension
(see `vscode-infra-lang/README.md`)

### 3. Configure Python path (if needed)
VS Code setting:

```json
{
  "python.defaultInterpreterPath": "/path/to/python"
}
```

## Running the server manually (for debugging)

### stdio mode (used by editors)

```bash
python -m infra.lsp.server
```

### TCP mode

```bash
infra lsp --tcp --port 2087
```

## Error codes in diagnostics

| Code | Severity | Description |
|------|----------|-------------|
| E001 | Error | Undefined variable |
| E002 | Error | Duplicate definition |
| E003 | Error | Invalid replicas |
| E004 | Error | Port out of range |
| E011 | Error | Replicas must be >= 1 |
| E012 | Error | Port out of range (1-65535) |
| SEC001 | Error | Hardcoded secret |
| SEC002 | Error | Credential pattern |
| SEC003 | Warning | Mutable image tag |
| SEC004 | Error | Privileged container |
| SEC006 | Warning | SSL disabled |
| REL001 | Warning | Thundering herd risk |
| REL003 | Warning | No memory limit |
| REL006 | Warning | No backup |

(All 28 SEC/REL rules are covered; severity follows the same ERROR/WARNING
split as `infra validate`.)

## Architecture

```
Editor (VS Code)
    ↕ JSON-RPC over stdio
infra lsp (Python process)
    ↓ calls
infra.parser.parse()
    ↓ calls
infra.analyzer.SemanticValidator.validate()
    ↓ returns
errors + warnings
    ↓ converted to
LSP Diagnostics
    ↑ sent to editor
publishDiagnostics notification
```

## Optional anonymous error reporting (feedback)

Error reporting is **disabled by default**. When enabled, a minimal,
non-identifying error summary (product, version, error type, sanitized
message) may be sent to a collector.

It never sends:
- source code,
- file paths,
- PII (user name, hostname, environment variables).

Enable it locally by adding to `<project>/.infra-config.yaml`:

```yaml
feedback:
  enabled: true
```

or `~/.config/infra/config.yaml`, or with the env var `INFRA_FEEDBACK=1`.
Set `INFRA_FEEDBACK_OFF=1` to force-disable. A collector or network failure
never affects the CLI or LSP.
