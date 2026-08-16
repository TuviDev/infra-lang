# Troubleshooting

## Installation

- `pip install 'git+https://github.com/kakukpl/infra-lang.git'` fails → ensure Python 3.11+ (`python --version`).
- `infra: command not found` after install → the console-script dir isn't on
  `PATH`; install with `python -m pip install --user infra-lang` or use
  `python -m infra`.
- LSP not available → install with `pip install 'git+https://github.com/kakukpl/infra-lang.git[lsp]'`.

## LSP / VS Code

- Diagnostics not showing → check `infra-lang` is installed and the extension
  Python has `pygls`. Verify: `python -m infra.lsp.server` (must not raise
  ImportError). Select the interpreter in VS Code
  (`Ctrl+Shift+P` → "Python: Select Interpreter").
- Extension not activating → file must end in `.infra` (not `.inf`).
- No completion/symbols → confirm the extension version matches this package.

## Compile failures

- `Service 'x' has neither image nor build` → every `service` needs
  `image:` or `build`.
- `Compilation aborted: N error(s)` → run `infra validate` to see all errors
  at once (the validator collects multiple errors; the parser stops at the
  first syntax error).

## Output directory (`infra-out/`)

`infra-out/` (or a custom `--output` dir) **accumulates** artifacts from
previous compiles: compiling to a different backend does not clear the old
files. This is intentional — the compiler never deletes files it did not write
to avoid accidentally removing user content (e.g. with `--split`).

If you inspect the output directory and see stale files from an earlier target
(e.g. an `infra.yaml` left over after compiling to `compose`), that is expected.
To compare targets cleanly:

- use a separate output dir per backend, e.g. `--output infra-out/k8s` and
  `--output infra-out/compose`, or
- `rm -rf infra-out` before recompiling for a fresh comparison.

## Validation / lint

- `error[E011] replicas must be >= 1` → set `replicas: 1` or higher.
- `SEC003` (mutable tag) → use an immutable tag like `nginx:1.25.3`.
- `SEC001` (hardcoded secret) → use `from secret` or `from env`.
- These are guidance; only `Error`-severity findings block compilation.

## Telemetry / feedback

- `infra feedback` shows status. Enable with `infra feedback --on`.
- Feedback is **off by default** and never sends source code or paths.

## Reporting a bug

Use the issue templates in `.github/ISSUE_TEMPLATE/`. A good report includes:

- `infra --version`, Python version, OS, and the target backend.
- The **minimal** `.infra` file that reproduces the issue.
- The exact command run and the full output.
- Whether it happens always or intermittently.

Choose the right category (parser / compiler-backend / linter / LSP-VS Code /
CLI / docs) so it reaches the right maintainer quickly.
