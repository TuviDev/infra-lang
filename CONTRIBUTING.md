# Contributing to Infra Lang

## Quick start

```bash
git clone https://github.com/TuviDev/infra-lang
cd infra-lang
pip install -e ".[dev]"
# pin the LSP stack (the server targets pygls 1.x / lsprotocol 2023.0.1):
pip install "pygls==1.3.1" "lsprotocol==2023.0.1"
pytest tests/ -n auto -q  # should pass
```

## How the code is organized

```
src/infra/
├── lexer/         # Grammar (grammar.lark) and tokens
├── parser/        # AST nodes and Lark transformer
├── analyzer/      # Semantic validation, security, reliability
├── backends/      # Compilation targets (K8s, Compose, TF, GH)
├── resolver/      # Import and extends resolution
├── diff/          # AST-based diff engine
├── validation/    # K8s output validation
├── stdlib/        # Built-in functions and prelude
├── cli/           # Command-line interface
└── errors/        # Error types and reporter
```

## Adding a new grammar rule

This is the most common contribution. Follow this checklist:

1. `lexer/grammar.lark` — add the rule
2. `parser/ast_nodes.py` — add the dataclass
3. `parser/transformer.py` — add transformer method
4. `backends/kubernetes.py` — handle in K8s backend
5. `tests/` — add tests for all of the above
6. `docs/language_spec.md` — update the spec

See the existing `autoscale` feature as a reference example.

## Adding a new linter rule

1. `analyzer/reliability.py` or `analyzer/security.py`
2. Add method `_rel0XX` or `_sec0XX`
3. Add to the `check()` call list
4. Add tests to `tests/test_reliability.py` or `tests/test_security_checker.py`
5. Add to the error codes table in `docs/language_spec.md`

Rule requirements:
- Zero false positives (must always be correct)
- Has a clear hint explaining how to fix
- Severity: ERROR blocks compilation, WARNING does not

## Adding a new backend

The backends live in `src/infra/backends/` and share a common interface
(`base.py`):

1. Create `src/infra/backends/<name>.py` with a class extending
   `backend.Backend` (and `backend.BaseYAMLBackend` if it emits YAML).
2. Implement `get_version()`, `compile()`, `compile_service()` and
   `compile_database()` (see the ABC in `base.py`).
3. Register the backend in `src/infra/backends/__init__.py` `get_backend()`.
4. Add tests under `tests/test_backends.py` (or a new `test_<name>.py`).
5. Update the support matrix in `docs/support_matrix.md`.

## Running tests

```bash
pytest tests/ -n auto -q           # fast (parallel)
pytest tests/ -m behavioral -v     # behavioral only
pytest tests/ -m slow              # performance tests
pytest tests/test_contracts.py -v  # contract tests
pytest tests -m "not live_e2e"     # everything except live kind tests
pytest tests -m live_e2e           # real Docker/kind/kubectl (tools required)
```

## Reporting a bug

1. Open an issue using the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md).
2. Include the **minimal** `.infra` file that reproduces the problem — redact
   any secrets or tokens.
3. Include the command you ran and the full output.
4. For valid-syntax-that-fails-to-parse, use the dedicated
   [parser bug template](.github/ISSUE_TEMPLATE/parser_bug.md).

## Code style

- `ruff` for linting (run: `ruff check src/`)
- `mypy` for type checking (run: `mypy src/infra`); it should pass with
  `--check-untyped-defs` too
- No magic numbers — use named constants
- Test names: `test_<what>_<when>_<expected>`
- One test per contract; assert on behavior, not internals
- Docstrings: Given/When/Then format

## PR process

1. Branch from `main`; keep the change focused.
2. Run the full suite and quality gates before pushing:
   `pytest tests/ -n auto`, `ruff check src/`, `mypy src/infra`.
3. Add a CHANGELOG entry under `## [Unreleased]` (or the next version).
4. Open a PR against `main` and fill in the pull request template.

## Commit message format

```
feat: add X support
fix: handle Y edge case
test: add contract tests for Z
docs: update tutorial with example
refactor: extract shared helper
```
