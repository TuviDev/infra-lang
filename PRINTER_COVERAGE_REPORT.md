# Printer Coverage Report — Infra Language

## Goal
Raise coverage of `src/infra/cli/printer.py` (the `infra fmt` pretty-printer),
previously the largest quality gap at **22%**.

## Result
- **`cli/printer.py` coverage: 22% → ~88%**
- **Total suite: 825 passed / 0 failed** (was 756; +69 new tests)
- **ruff:** All checks passed
- **mypy:** Success — 0 issues in 43 source files

## New test file
`tests/test_printer_completeness.py` (69 tests) covering:

| Class | Coverage |
|-------|----------|
| `TestServiceFormatting` | image, build, port (single + host:target), env (literal/secret/config/env), depends, resources, health, decorator, identifier image |
| `TestDatabaseFormatting` | all fields incl. backup + users |
| `TestCacheQueueStorageNetwork` | cache, queue topics, storage, network |
| `TestSecretConfig` | vault/env/file/literal sources, config entries |
| `TestEnvironmentCluster` | environment + cluster nodes |
| `TestExpressionFormatting` | unary, call kwargs, index, template, if, match, percentage, multiline/inline lists, literals, attribute |
| `TestImportFormatting` | plain, alias, from-names |
| `TestRoundTrip` | parse → fmt → parse for all 11 definition kinds (must not raise) |
| `TestIdempotency` | fmt(fmt(x)) == fmt(x) |
| `TestEmptyBlocks` | empty service/pipeline |
| `TestGolden` | exact output for hello-world and database |
| `TestFormatFile` | changed/unchanged detection |
| `TestPrinterInternals` | custom indent, `_block`, `_pattern`, `_num`, `_str_list` |

## Real bug found & fixed (in CODE, not tests)
**Printer dropped quotes on dotted/slashed string values, breaking round-trip.**
`network n { cidr: "10.0.0.0/16" ... }` was emitted as `cidr: 10.0.0.0/16`,
which then failed to re-parse (the `.` broke the lexer).

Fix: added `_safe_bare(value)` / `_qstr(value)` helpers that emit a value
bare only when it is a valid bare Infra token (identifier or number), and quote
it otherwise. Applied to CIDR fields, `machine_type`, and pipeline `schedule`.
Verified: dotted values like `10.0.0.0/16`, `t3.medium`, `0 2 * * *` now round-trip.

## Verification
```bash
pytest tests/ -p no:cacheprovider --no-cov      # 825 passed
ruff check src/                                  # All checks passed
mypy src/infra --ignore-missing-imports          # Success, 0 issues
# printer coverage measured via coverage.py:     # 88%
```

## Note on coverage measurement
The `pytest-cov` plugin is unusually slow in this environment (>2 min for a
single-file run, timing out). Printer coverage was measured directly with the
`coverage.py` API over the full set of printer code paths, giving **88%**
(same paths exercised by the new pytest file).
