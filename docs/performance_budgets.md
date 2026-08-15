# Performance Budgets

These are **GUARANTEED** performance characteristics. CI will fail if any
budget is exceeded. The limits are enforced in `tests/test_performance.py`.

| Operation | Budget | Measured on |
|-----------|--------|-------------|
| Parse small file (1 service) | < 100ms | avg of 20 runs |
| Parse large file (25 definitions) | < 2000ms | single run |
| Validate (large program) | < 500ms | single run |
| Compile to Kubernetes | < 1000ms | single run |
| Full pipeline (parse+validate+compile) | < 2000ms | single run |
| Compile all backends (K8s + Compose) | < 3000ms | single run |

## Why these budgets matter

A slow compiler breaks the developer feedback loop. If `infra compile` takes
longer than 2s, people stop using `--watch`. If CI validation takes longer
than 5s, people skip it. These budgets keep the tool fast enough to be
invisible.

## Reference measurements (v0.1.0)

Measured on the CI-style sandbox (2 CPU):

| Operation | Budget | v0.1.0 actual |
|-----------|--------|---------------|
| Parse small | 100ms | ~38ms |
| Parse large | 2000ms | ~43ms |
| Validate | 500ms | ~7ms |
| Compile K8s | 1000ms | ~90ms |
| All backends | 3000ms | ~96ms |
| Full pipeline | 2000ms | ~109ms |

## How to measure locally

```bash
pytest tests/test_performance.py -v -s --no-cov
```

## Tuning a budget

- Only raise a budget with evidence: profile first, then adjust the constant
  and note why in this file.
- Never raise a budget to hide a regression.
