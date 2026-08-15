# Contributing to Infra Lang

## Quick start

```bash
git clone https://github.com/infra-lang/infra-lang
cd infra-lang
pip install -e ".[dev]"
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

## Running tests

```bash
pytest tests/ -n auto -q           # fast (parallel)
pytest tests/ -m behavioral -v     # behavioral only
pytest tests/ -m slow              # performance tests
pytest tests/test_contracts.py -v  # contract tests
```

## Code style

- `ruff` for linting (run: `ruff check src/`)
- `mypy` for type checking (run: `mypy src/infra`)
- No magic numbers — use named constants
- Test names: `test_<what>_<when>_<expected>`
- Docstrings: Given/When/Then format

## Commit message format

```
feat: add X support
fix: handle Y edge case
test: add contract tests for Z
docs: update tutorial with example
```
