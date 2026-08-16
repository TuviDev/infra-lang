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

### Live E2E (real Docker / kind / kubectl)

`tests/test_live_e2e.py` compiles `examples/*.infra` to Kubernetes and **really
applies** it to a `kind` cluster with `kubectl apply`, guarding the K8s output
contracts that earlier regressions (Secret base64, unnamed multi-port
Services) violated.

**Always optional.** The tests are marked `live_e2e` and **silently skip** when
any of Docker, kind, kubectl or kubeconform is missing or not running. A normal
`pytest tests` (or CI's `-m "not live_e2e"`) never runs them.

```bash
# Install tools (macOS):
brew install docker kind kubectl kubeconform
#   ... or Linux:
#   curl -Lo kind https://kind.sigs.k8s.io/dl/v0.20.0/kind-linux-amd64
#   curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
#   kubeconform: see https://github.com/yannh/kubeconform
# Windows: install Docker Desktop + the three binaries via choco/scoop.

# Run only live E2E (needs a working Docker daemon; creates a kind cluster):
pytest tests -m live_e2e -q

# Run everything EXCEPT live E2E (default for CI):
pytest tests -m "not live_e2e" -q

# Or run the live file directly (still skips if tools missing):
pytest tests/test_live_e2e.py -v
```

The kind cluster is created once per session and **always deleted** (teardown
runs in a `finally`, so no zombie clusters survive even if a test fails).
Per-example resources are removed with `kubectl delete -f`. All subprocess
calls are timeout-bounded. If a test fails, check `docker ps` / `kind get
clusters` for leftovers and `kind delete cluster --name infra-lang-e2e`.

### Live Compose E2E (real `docker compose`)

`tests/test_live_compose_e2e.py` compiles `examples/*.infra` to Docker Compose
and **really runs** `docker compose` against a Docker daemon, guarding the
Compose output contracts that can look valid but never start. It is marked
`live_e2e` and **silently skips** when Docker (or a running daemon) is missing.
A normal `pytest tests` never runs it.

```bash
# Requires: a running Docker daemon (e.g. Docker Desktop).
pytest tests -m live_e2e -q          # runs K8s AND Compose live E2E
pytest tests/test_live_compose_e2e.py -v
```

Two levels of testing:

- **`docker compose config`** — validates every example that produces services
  (all examples except the pipeline-only `04_cicd_pipeline.infra`).
- **`docker compose up -d --wait`** — actually starts the stack, but only for
  examples whose images are **all public** (currently `01_hello_world` /
  nginx). Examples using private images (`myapp/*`) would fail with
  `image pull failed`, which is not a generator bug, so they are
  config-validated only.

Every `up` is followed by `docker compose down -v` in a `finally`, so no
containers, networks, or volumes leak. The Compose **regression** guards in the
same file (multi-port service, secret declaration + mounting) inspect the
generated YAML and run in the normal suite — no Docker needed.

## Adding a new test

1. Put it in the file matching its area (see layout).
2. Name it for the contract it protects.
3. Assert on behavior/contract, not internals.
4. Run the area, then the full suite.
5. Add a `tests/README.md` row only if you create a new file.
