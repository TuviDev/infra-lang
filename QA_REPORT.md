# QA Completeness Report — Infra Language

## Result
- **Total tests:** 756
- **Passed:** 756 (3× — no flakes)
- **Failed:** 0
- **Coverage:** 82% total (up from 80%)
- **ruff:** All checks passed
- **mypy:** Success — 0 issues in 43 source files

## New QA test files (6)

| File | Focus |
|------|-------|
| `tests/test_ast_completeness.py` | Every field of every AST node (Literal, Duration, ResourceValue, BinaryOp, UnaryOp, Call, Index, Attribute, List, Map, TemplateString, IfExpr, Match, VariableDecl, Import, Decorator, ServiceDef, DatabaseDef, Cache, Queue, Storage, Network, Secret, Config, Pipeline, Environment, Cluster) |
| `tests/test_error_messages.py` | Every error code E001–E033 and warning W001–W004 + SEC/REL messages & hints |
| `tests/test_backend_completeness.py` | All backend output paths (k8s cache/queue/network/storage/env/split/ingress; compose secret/config/network/minio/mysql/mongo; terraform VPC/SQS; github actions/dependabot/concurrency; base helpers) |
| `tests/test_stdlib_complete.py` | Every stdlib builtin + unit conversion (`to_seconds`, `to_kubernetes`, `to_bytes`) |
| `tests/test_parser_complete.py` | Parser errors, `parse_expression`, full type system |
| `tests/test_analyzer_complete.py` | Exception hierarchy, reporter, diff engine, linter internals |

## Real bugs found & fixed (in code, not tests)
1. **`from "./x.infra" import A, B` produced `names == ('A', ',', 'B')`**
   — the transformer's `import_names` did not drop `COMMA` tokens.
   Fix: filter comma tokens in `import_names`.
2. **`storage { lifecycle { ... } }` was silently dropped**
   — `storage_item` only inspected `children[0]`, but the `StorageLifecycle`
   node appears at `children[1]` (after the `lifecycle` keyword token).
   Fix: scan all children for a `StorageLifecycle` node.

## Coverage by core module (after QA suite)
| Module | Coverage |
|--------|----------|
| analyzer/types | 96% |
| errors/reporter | 94% |
| backends/terraform | 92% |
| backends/compose | 86% |
| backends/kubernetes | 84% |
| backends/base | 81% |
| backends/github | 78% |
| **cli/printer** | **22%** (biggest remaining gap) |

## Known gap (future work)
`cli/printer.py` (the `infra fmt` pretty-printer) is at 22% coverage — it is
the least critical module (a formatter) and the largest remaining coverage
opportunity. Everything else is ≥ 78%.
