# Infra Lang in Neovim

Set up the Infra Lang language server in Neovim. The server speaks the standard
LSP protocol over stdio, so it works with Neovim's built-in LSP client — no
external plugin is strictly required.

## Requirements

- **Python 3.11+**
- `pip install 'infra-lang[lsp]'` — the `[lsp]` extra installs `pygls`, which the
  server needs.
- **Neovim 0.8+** with the built-in LSP client (`:h lsp`).
- Optional, recommended: [nvim-lspconfig](https://github.com/neovim/nvim-lspconfig).

> The `infra` CLI on your `PATH` is the process Neovim launches. If you use a
> virtual environment, install the package there and make sure that
> environment's `bin` is on your `PATH` (see Troubleshooting below).

## Quick check

Confirm the CLI and the LSP server are installed before touching Neovim:

```bash
infra lsp --help          # shows the lsp subcommand
infra lsp --tcp --port 2087  # optional manual smoke test (Ctrl+C to stop)
```

## Filetype detection

Tell Neovim that `*.infra` files are their own filetype so `FileType` autocommands
fire and the syntax is applied:

```lua
vim.filetype.add({
  extension = {
    infra = 'infra',
  },
})
```

## Configuration with nvim-lspconfig (recommended)

`nvim-lspconfig` does not ship an `infra_lang` entry, so register a custom server
first. The server is rooted at the project so cross-file navigation and
whole-project indexing work correctly.

```lua
local lspconfig = require('lspconfig')
local configs = require('lspconfig.configs')

if not configs.infra_lang then
  configs.infra_lang = {
    default_config = {
      cmd = { 'infra', 'lsp' },
      filetypes = { 'infra' },
      root_dir = lspconfig.util.root_pattern('.git', 'pyproject.toml'),
      settings = {},
    },
  }
end

lspconfig.infra_lang.setup({})
```

Put this in a `lua/plugins/infra.lua` file (or in `after/ftplugin/infra.lua`) so it
runs only when Infra files are opened.

If you already have a `setup_handlers`-style config, you can route all clients
through it:

```lua
lspconfig.infra_lang.setup({
  capabilities = require('cmp_nvim_lsp').default_capabilities(), -- optional
})
```

## Configuration without nvim-lspconfig (vanilla)

If you prefer to avoid nvim-lspconfig, start the client directly on `infra`
files. Neovim's built-in LSP client is enough:

```lua
vim.filetype.add({
  extension = { infra = 'infra' },
})

vim.api.nvim_create_autocmd('FileType', {
  pattern = 'infra',
  callback = function()
    vim.lsp.start({
      name = 'infra-lang',
      cmd = { 'infra', 'lsp' },
      root_dir = vim.fs.dirname(
        vim.fs.find({ '.git', 'pyproject.toml' }, { upward = true })[1]
      ),
    })
  end,
})
```

`root_dir` falls back to `nil` (the file's own directory) when no marker is
found; whole-project navigation then just covers the current directory.

## Verification

Open any `.infra` file and confirm the server is attached:

1. **Check attachment** — `:LspInfo` should list `infra-lang` as attached to the
   buffer.
2. **Diagnostics** — introduce an error (e.g. `replicas: 0`), and the diagnostic
   should appear inline and in `:lua vim.diagnostic.setloclist()`.
3. **Go to definition** — move the cursor onto a block name (or a reference in
   `depends: [db]`) and press `gd`.
4. **Outline** — `:Telescope lsp_document_symbols` (or `:lua
   vim.lsp.buf.document_symbol()`).

Useful default keybindings (add to your config if not already present):

```lua
vim.api.nvim_create_autocmd('LspAttach', {
  group = vim.api.nvim_create_augroup('infra-lsp', { clear = true }),
  callback = function(args)
    local buf = args.buf
    local map = function(keys, fn) vim.keymap.set('n', keys, fn, { buffer = buf }) end
    map('gd', vim.lsp.buf.definition)
    map('gr', vim.lsp.buf.references)
    map('K', vim.lsp.buf.hover)
    map('<F2>', vim.lsp.buf.rename)
    map('<leader>la', vim.lsp.buf.code_action)
  end,
})
```

## Optional: syntax highlighting

There is **no Tree-sitter grammar** for Infra Lang yet, so Neovim's Treesitter
highlighting won't apply. The server still provides **semantic tokens**, which
most `:set ft` set-ups consume for precise highlighting. To get basic highlighting
in the meantime, add a minimal Vim syntax file:

Create `~/.config/nvim/syntax/infra.vim`:

```vim
if exists("b:current_syntax")
  finish
endif

syntax match infraComment /#.*$/ contains=@Spell
syntax keyword infraKeyword service database cache queue storage network \
  secret config pipeline environment cluster
syntax match infraBlock /\v\{|\}/ 
syntax match infraString /\v"(\\.|[^"])*"/
syntax match infraNumber /\v\<\d+(\.\d+)?(m|cores|Ki|Mi|Gi|Ti|MB|GB|TB|ms|s|h|d|w)?\>/

highlight default link infraComment Comment
highlight default link infraKeyword Keyword
highlight default link infraBlock Delimiter
highlight default link infraString String
highlight default link infraNumber Number

let b:current_syntax = "infra"
```

Then register it in your config:

```lua
vim.filetype.add({
  extension = { infra = 'infra' },
})
vim.api.nvim_create_autocmd('FileType', {
  pattern = 'infra',
  callback = function()
    vim.bo.commentstring = '# %s'
  end,
})
```

Even without this file, semantic tokens give you structured highlighting as you
type — the syntax file is just a fallback. A Tree-sitter parser is on the
roadmap and will supersede both.

## Troubleshooting

- **`infra: command not found`** — the CLI isn't on `PATH`. Install with
  `pip install 'infra-lang[lsp]'`, then verify `which infra`. If you use a
  virtualenv, add it to `PATH` or point `cmd` at the full path, e.g.
  `cmd = { '/path/to/venv/bin/infra', 'lsp' }`.
- **No diagnostics appear** — check `:LspLog` for errors (does the `infra` binary
  start? is `pygls` installed?). Confirm `:LspInfo` shows the client attached; if
  it says "No client found", the filetype (`infra`) or `root_dir` is wrong.
- **Slow completion** — the server is a single-process LSP (one Python process
  per project root). This is expected; completion is served on the same process
  that validates the file. If it feels sluggish, make sure you aren't opening the
  whole home directory as a root (see `root_dir` above).
- **Whole-project navigation doesn't work across files** — ensure `root_dir`
  resolves to the project root (a shared `.git` or `pyproject.toml`), since the
  server scans the workspace root for `*.infra` files on startup.
- **Server crashes on startup** — run `infra lsp --tcp --port 2087` manually and
  watch the output; the TCP mode prints errors to the terminal.
