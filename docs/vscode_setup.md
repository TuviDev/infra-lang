# VS Code Setup for Infra Lang

## Quick setup (5 minutes)

### Step 1: Install Python package

```bash
pip install 'infra-lang[lsp]'
```

### Step 2: Install the extension

**From the VS Code Marketplace** (recommended): search for **"Infra Lang"** in
the Extensions view, or run:

```bash
code --install-extension infra-lang
```

**From Open VSX** (e.g. VS Code-OSS / Cursor / Theia): install the `infra-lang`
extension from the Open VSX registry.

**From source** (development): see `vscode-infra-lang/README.md`.

### Step 3: Open a .infra file
Syntax highlighting activates automatically.
Diagnostics appear as you type.

## What you'll see
- **Red underlines**: errors (must fix)
- **Yellow underlines**: warnings (should fix)
- **Hover**: documentation for keywords

## Troubleshooting

### Diagnostics not showing
Check that infra-lang is installed:

```bash
pip show infra-lang
```

Check Python path in VS Code settings:
`Ctrl+Shift+P` → "Python: Select Interpreter"

Make sure the extension's Python has `infra-lang` (and `pygls` for LSP)
installed:

```bash
python -m infra.lsp.server   # should not raise ImportError
```

### Extension not activating
Check the `.infra` file extension (not `.inf` or `.infra.txt`).
