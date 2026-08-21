# Infra Lang for Visual Studio Code

Syntax highlighting, snippets, and a live language server for **Infra Language**
`.infra` files — the Infrastructure-as-Code DSL that compiles one file to
Kubernetes, Docker Compose, Terraform, Helm, and GitHub Actions.

## Features

- **Syntax highlighting** for all 11 Infra structures (service, database, cache,
  queue, storage, network, secret, config, pipeline, environment, cluster) with
  distinct colors for keywords, built-in types, decorators, template strings,
  numbers + units, booleans/null, comments and field names.
- **Live diagnostics** — errors and warnings appear inline as you type (powered
  by the Infra Lang LSP server), no need to run `infra validate`.
- **Completion & hover** — context-aware suggestions for blocks, fields, values
  and sub-blocks, plus documentation on hover.
- **Go to definition / references / rename** — navigate and rename symbols,
  including across files in the same project.
- **12 code snippets** to scaffold services, databases, caches, pipelines,
  resources and environments.

## Requirements

- **Python 3.11+** with Infra Lang installed (the language server):

  ```bash
  pip install 'infra-lang[lsp]'
  ```

  (The `[lsp]` extra pulls in `pygls`, which the server needs.)

- **Visual Studio Code** 1.85 or newer.

## Configuration

The extension launches the Infra Lang language server automatically when you
open a `.infra` file. Make sure the `infra` module is importable from your
Python interpreter; if you use a virtual environment, point VS Code at it:

```json
{
  "python.defaultInterpreterPath": "/path/to/python"
}
```

## Usage

1. Install the extension from the **VS Code Marketplace** or **Open VSX**.
2. Ensure `pip install 'infra-lang[lsp]'` is available on your `PATH`.
3. Open a `.infra` file — highlighting applies automatically, and the language
   server provides live diagnostics, completion, and navigation.

## Publishing

The extension is distributed as a `.vsix` via the VS Code Marketplace and Open
VSX. Maintainers publish with the packaged npm scripts:

```bash
# Package a .vsix locally
npm run package

# Publish to the VS Code Marketplace (requires VSCE_PAT)
npm run publish:marketplace

# Publish to Open VSX (requires OVSX_TOKEN)
npm run publish:openvsx
```

The `.github/workflows/marketplace.yml` workflow publishes automatically on
version tags (`v*`) using the `VSCE_PAT` and `OVSX_TOKEN` repository secrets.

## Snippets

Type the prefix and press Tab:

| Prefix       | Inserts                                        |
|--------------|------------------------------------------------|
| `svc`        | Full service (port/health/resources)           |
| `svc-full`   | Service + autoscale + security + network policy|
| `db`         | Database with SSL and backup                   |
| `cache`      | Redis cache with memory and persistence        |
| `pipeline`   | CI/CD pipeline (test → build → deploy)         |
| `secret`     | Secret from env                                |
| `environment`| Environment that extends another               |
| `micro`      | 3 services + database + cache                  |
| `health`     | Health check shorthand                         |
| `res-s`      | Small resource block                           |
| `res-m`      | Medium resource block                          |
| `autoscale`  | HPA block                                      |

## Resources

- [Repository](https://github.com/TuviDev/infra-lang)
- [Documentation](https://TuviDev.github.io/infra-lang/)
- [Quickstart](https://TuviDev.github.io/infra-lang/quickstart/)
- [Issue tracker](https://github.com/TuviDev/infra-lang/issues)

## License

MIT — see the [LICENSE](https://github.com/TuviDev/infra-lang/blob/main/LICENSE).
