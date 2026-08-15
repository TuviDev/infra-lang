# Test Consolidation Report

## Inventory
| file | tests | category | value |
|------|-------|----------|-------|
| test_lexer.py | 119 | unit | high — grammar tokens |
| test_transformer.py | 53 | unit | high — AST building |
| test_printer_completeness.py | 48 | unit | high |
| test_ast_completeness.py | 43 | unit | high |
| test_error_messages.py | 38 | regression | high — error codes |
| test_backends.py | 32 | integration | high |
| test_k8s_audit.py | 31 | integration | high (S18) |
| test_cli.py | 31 | integration | high |
| test_autoscale_disruption.py | 30 | unit | high |
| test_analyzer.py | 30 | unit | high |
| test_network_topology_quota.py | 28 | unit | high |
| test_backend_completeness.py | 28 | integration | high |
| test_stdlib.py | 27 | unit | high |
| test_reliability_audit.py | 24 | regression | high (S18) |
| test_reliability.py | 23 | regression | high |
| test_lsp.py | 23 | unit | high (S16) |
| test_security_audit.py | 19 | regression | high (S18) |
| test_vscode_extension.py | 19 | e2e | medium |
| test_security_checker.py | 20 | regression | high |
| test_distribution.py | 20 | e2e | medium |
| test_schedule.py | 19 | unit | high |
| test_parser_complete.py | 19 | unit | high |
| test_behaviors.py | 15 | behavioral | high |
| test_integration_audit.py | 14 | integration | high (S18) |
| test_chaos_audit.py | 11 | e2e/slow | high (S18) |
| test_corpus.py | 4 | integration | high |
| test_contracts.py | 3 | contracts | high (parametrized) |
| ... (41 more files) | ... | mixed | medium-high |

Total: 71 test files, 1485 tests (before consolidation).

The "coverage session" files (`test_coverage_boost`, `_s8/_s9/_s10/_s81`,
`_gaps`) each cover distinct behaviors (REPL, docs, graph, eval builtins,
watch helpers) — they are **not** mass duplicates; each targets a specific
module/branch. Leave them.

## Redundant tests
| file | test | action | reason |
|------|------|--------|--------|
| test_parse_error_messages.py | test_parse_error_has_line_number | MERGE→removed | fully covered by test_error_recovery_audit.test_error_shows_line_number |
| test_parse_error_messages.py | test_parse_error_shows_context_lines | MERGE→removed | covered by audit context+caret tests |
| test_parse_error_messages.py | test_parse_error_has_expected | MERGE→removed | covered by audit expected+got |
| test_parse_error_messages.py | test_parse_error_has_got | MERGE→removed | covered by audit expected+got |
| test_parse_error_messages.py | test_semantic_error_has_hint | REWRITE→moved | unique assertion; preserved in audit |

## Tests rewritten
| old name | new name | why |
|----------|----------|-----|
| (file merged) test_semantic_error_has_hint | test_error_recovery_audit.TestSemanticErrorHints.test_semantic_error_has_hint | preserved the only unique test from the duplicated file |

## Final recommendation
- **Leave as is** for the vast majority: the suite is large but each file
  protects a distinct behavior, and coverage is stable at 92%.
- **Merged 1 file** (`test_parse_error_messages.py` → `test_error_recovery_audit.py`):
  the older file's 4 assertions were fully duplicated by the Session-18 audit
  file; its 1 unique assertion was preserved. This removed 4 redundant tests
  with zero coverage loss and zero unique-scenario loss.
- No other files warrant merging/removal — the apparent "coverage session"
  files are not duplicates (each targets distinct modules).
- Recommended future cleanup (low priority, not blocking): none.
