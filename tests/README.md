# Test Suite

This directory is the Infra Lang test suite. It is treated as a **product in
its own right**: every test should protect a real contract or behavior.

## Layout

Tests are grouped by area. Files follow `test_<module>.py` (unit/module
contracts) or `test_<feature>.py` (cross-module features).

### Core language
| File | Covers |
|------|--------|
| `test_lexer.py` | Tokenizer / grammar tokens |
| `test_transformer.py`, `test_transformer_rare.py` | AST construction and rare constructs |
| `test_parser_complete.py` | Parser completeness |
| `test_ast_completeness.py` | AST node coverage |
| `test_printer_completeness.py` | `infra fmt` printer round-trip & idempotency |
| `test_units.py`, `test_resource_expr.py` | Units and resource expressions |
| `test_collections.py`, `test_boundaries.py` | Edge cases |

### Analyzer / validation
| File | Covers |
|------|--------|
| `test_analyzer.py` | Error detection, warnings, suggestions |
| `test_analyzer_complete.py` | Exception hierarchy, reporter, diff engine internals |
| `test_security_checker.py`, `test_security_advanced.py` | SEC001–SEC010 rules |
| `test_reliability.py`, `test_reliability_audit.py`, `test_reliability_advanced.py` | REL001–REL014 rules |
| `test_types_system.py` | Type-system helpers |
| `test_error_messages.py` | Error codes and messages |
| `test_error_recovery_audit.py` | Parse-error readability, multi-error collection |
| `test_imports.py`, `test_extends.py` | Import / extends resolution |
| `test_interpolation.py`, `test_var_interpolation.py` | Template interpolation |

### Backends
| File | Covers |
|------|--------|
| `test_backends.py`, `test_backend_completeness.py` | Kubernetes / Compose / Terraform / GitHub |
| `test_k8s_audit.py`, `test_compose_audit.py`, `test_github_audit.py` | Per-backend output contracts |
| `test_k8s_validator.py`, `test_schema_validator.py` | K8s output validation |
| `test_contract_support_matrix.py` | Feature × backend support matrix |

### CLI / tools
| File | Covers |
|------|--------|
| `test_cli.py` | CLI commands |
| `test_cli_branches.py` | CLI branch coverage (docs, symbols, compose, k8s) |
| `test_feedback.py` | Opt-in telemetry config & reporting |
| `test_repl.py` | Interactive REPL |
| `test_graph.py` | Dependency graph output |
| `test_fmt.py` (in `test_printer_completeness`) | Formatter |
| `test_init_templates.py`, `test_demo.py`, `test_examples.py` | Templates / examples |

### LSP
| File | Covers |
|------|--------|
| `test_lsp.py` | Diagnostics, completion integration |
| `test_lsp_completion.py` | Completion engine |
| `test_lsp_hover.py`, `test_lsp_symbols.py`, `test_lsp_quickfix.py` | Hover, symbols, quick-fixes |

### Diff
| File | Covers |
|------|--------|
| `test_diff.py` | Diff engine (added/removed/changed/JSON) |

### Distribution / release
| File | Covers |
|------|--------|
| `test_publish_readiness.py` | Wheel contents, twine, clean venv install |
| `test_ci_workflows.py` | GitHub Actions workflows |
| `test_vscode_extension.py` | VS Code extension manifest |

### Quality gates
| File | Covers |
|------|--------|
| `test_contracts.py`, `test_corpus.py` | Docs/examples contracts |
| `test_performance.py` | Performance budgets |
| `test_chaos_audit.py` | Chaos / stress (large, parallel, repeated, malformed, LSP storm) |
| `test_behaviors.py` | Behavioral (Given/When/Then) |
| `test_property_based.py` | Hypothesis property tests |
| `test_watch_mode.py` | `--watch` mode (recompile-on-change skipped on Windows) |

## Running tests

```bash
# full suite (parallel)
pytest tests/ -n auto -q

# a single area
pytest tests/test_analyzer.py -q

# slow markers only (chaos / stress / performance)
pytest tests/ -m slow -q

# coverage
pytest tests/ --cov=src/infra --cov-report=term --cov-fail-under=90 -q

# benchmarks (not a pytest run)
python scripts/benchmark.py [--save | --compare]
```

## Conventions

- **Naming:** files `test_<module|feature>.py`; classes `Test<ContractName>`;
  methods `test_<what>_<expected>`.
- **One contract per test:** prefer one scenario per test over micro-splitting
  or giant multi-assertion tests.
- **Assert contracts, not implementation:** prefer `"key" in output` over exact
  long-string equality; check error codes + context over exact line numbers.
- **No session-artifact files** (`SESSION_*`, coverage-session leftovers) belong
  in this directory.

### LSP tests and the pygls version

The LSP server targets **pygls 1.x** / `lsprotocol==2023.0.1`. The LSP test
modules skip when pygls is not importable (a pygls 2.x install also fails the
import). Always pin before running the suite:

```bash
pip install "pygls==1.3.1" "lsprotocol==2023.0.1"
```

The **CLI registration contract** (`infra lsp --help`) deliberately lives in
`test_cli.py`, not an LSP module, so it runs deterministically even when pygls
is missing or a version mismatch skips the LSP modules.

## Adding a new test

1. Put it in the file matching its area (see layout).
2. Name it for the contract it protects.
3. Assert on behavior/contract, not internals.
4. Run the area, then the full suite.
5. Add a `tests/README.md` row only if you create a new file.
