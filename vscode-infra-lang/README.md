# Infra Lang for Visual Studio Code

Syntax highlighting and snippets for **Infra Language** `.infra` files.

## Features

- Syntax highlighting for all 11 Infra structures (service, database, cache,
  queue, storage, network, secret, config, pipeline, environment, cluster).
- Distinct colors for keywords, built-in types, decorators, template strings,
  numbers + units, booleans/null, comments and field names.
- 12 code snippets to scaffold services, databases, caches, pipelines,
  resources and environments.

## Install

1. Install the [Visual Studio Code Extension (VSIX)](https://code.visualstudio.com/)
   or copy the `vscode-infra-lang/` folder into your `.vscode/extensions/`
   directory.
2. Restart VS Code.
3. Open a `.infra` file — highlighting applies automatically.

## Snippets

Type the prefix and press Tab:

| Prefix      | Inserts                                       |
|-------------|-----------------------------------------------|
| `svc`       | Full service (port/health/resources)          |
| `svc-full`  | Service + autoscale + security + network policy |
| `db`        | Database with SSL and backup                  |
| `cache`     | Redis cache with memory and persistence       |
| `pipeline`  | CI/CD pipeline (test → build → deploy)        |
| `secret`    | Secret from env                               |
| `environment`| Environment that extends another              |
| `micro`     | 3 services + database + cache                 |
| `health`    | Health check shorthand                        |
| `res-s`     | Small resource block                          |
| `res-m`     | Medium resource block                         |
| `autoscale` | HPA block                                     |

## Development

- Grammar: `syntaxes/infra.tmLanguage.json`
- Snippets: `snippets/infra.json`
- Language config: `language-configuration.json`

## Installation

### Option A: From VS Code Marketplace (coming soon)
Search "Infra Lang" in Extensions.

### Option B: From source
```bash
npm install
npm run compile
code --install-extension infra-lang-0.1.0.vsix
```

### Requirements
- Python 3.11+ with infra-lang installed:
  ```bash
  pip install 'git+https://github.com/kakukpl/infra-lang.git'
  ```
- Or with LSP support:
  ```bash
  pip install 'git+https://github.com/kakukpl/infra-lang.git[lsp]'
  ```

## Language Server (LSP)

The extension launches the Infra Lang language server for live diagnostics
(errors and warnings inline as you type) and keyword hover documentation.

Make sure the `infra.lsp.server` module is importable from your Python
interpreter. Configure it in VS Code if needed:

```json
{
  "python.defaultInterpreterPath": "/path/to/python"
}
```
