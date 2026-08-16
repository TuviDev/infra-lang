# Testing Quality Report

This document reports the measured quality of the test suite beyond plain line
coverage — **branch coverage** and **mutation testing** — and how to reproduce
the measurements.

> Numbers are as of the pre-publication audit. They are a snapshot, not a
> guarantee. Re-measure after meaningful test changes.

## Branch coverage

Line coverage is 93.24%. Branch coverage (the `--cov-branch` metric, which
counts every `if/else`/`try` branch, not just executed lines) is **86.6%**
overall.

```bash
pytest tests/ --cov=src/infra --cov-branch --cov-report=term -q -m "not live_e2e"
```

### Modules with the largest line-vs-branch gap

| Module | Line % | Branch % | Gap | Assessment |
|--------|--------|----------|-----|------------|
| `errors/exceptions.py` | 97.3 | 68.2 | 29 | low value (exception `__str__`/`__init__` alternates) |
| `cli/init.py` | 92.7 | 71.4 | 21 | medium (scaffold branches) |
| `parser/__init__.py` | 97.3 | 80.0 | 17 | medium (parse-error branches) |
| `parser/ast_nodes.py` | 99.8 | 83.3 | 17 | low (property/method alternates) |
| `diff/engine.py` | 90.2 | 75.0 | 15 | medium (comparison branches) |
| `resolver/imports.py` | 91.7 | 80.0 | 12 | medium (import resolution branches) |
| `parser/transformer.py` | 86.3 | 78.9 | 7 | medium (many defensive fallbacks) |
| `backends/kubernetes.py` | 90.2 | 87.9 | 2 | good |

The overall branch figure (86.6%) is closer to the "true" coverage than the
93.24% line number, because it counts decision paths that are exercised in only
one direction.

## Mutation testing

Mutation testing automatically injects small faults into the source and checks
whether the tests catch them. The score below is **killed / (killed + survived)**
among mutants that a covering test actually runs ("tested mutants"). Untested
lines produce "no tests" mutants and are excluded.

### Results (mutmut, per module)

| Module | Tested mutants | Killed | Survived | Score |
|--------|---------------|--------|----------|-------|
| `analyzer/types.py` | 57 | 52 | 5 | 91.2% |
| `analyzer/security.py` | 248 | 156 | 92 | 62.9% |
| `backends/helm.py` | 538 | 329 | 209 | 61.2% |
| `analyzer/validator.py` | 671 | 406 | 265 | 60.5% |
| `backends/base.py` | 261 | 154 | 107 | 59.0% |
| `backends/terraform.py` | 479 | 279 | 200 | 58.2% |
| `analyzer/reliability.py` | 372 | 212 | 160 | 57.0% |
| `backends/kubernetes.py` | 2046 | 1000 | 1046 | 48.9% |
| `parser/transformer.py` | 278 | 130 | 148 | 46.8% |
| `backends/compose.py` | 585 | 204 | 381 | 34.9% |
| `backends/github.py` | 326 | 111 | 215 | 34.0% |

**Interpretation.** The weighted mutation score across the key modules is
roughly **50–60%**, well below the 70–85% typical of a "good" suite. The
backends — especially Compose (34.9%) and GitHub Actions (34.0%) — are the
weakest: the tests verify output *shape* (that a resource is present, that a
key exists) more than the *logic* that decides what is emitted. The analyzer
modules are better but still below target; `types.py` is the strongest at 91.2%.

### Categories of surviving mutants

- **Real test gaps (worth fixing, medium/high value):**
  - `security.py` `_sec001_hardcoded_env`: `or`→`and` survived — no test has a
    service env entry with `value is None` (`from_secret`/`from_env`), so the
    guard branch is never exercised.
  - `security.py` `_sec005_root_user`: `user is None`→`user is not None`
    survived — SEC005's root-user detection is not covered by a negation test.
  - `validator.py` `_expr_type`: `expr is None` guard + `infer_literal_type`
    argument mutations survived — the low-level type-inference helpers are not
    unit-tested directly.
  - `kubernetes.py`/`compose.py` output-emission mutations survive because tests
    assert presence/shape, not exact content for every field.
- **Semantically equivalent for tested inputs (ignore):** e.g. `+=` vs `=`
  when only one finding accumulates, message-string changes that don't affect
  the asserted error code.
- **Low value (ignore):** logging/message/formatting mutations.

## How to reproduce mutation testing

mutmut's coverage mapping requires a **flat package copy** (the `src/` layout
confuses its sys.path handling). Use:

```bash
pip install mutmut
mkdir -p /tmp/mut && cd /tmp/mut
cp -r <repo>/src/infra ./infra
cp -r <repo>/tests ./tests
cp <repo>/pyproject.toml .
# then configure [tool.mutmut]:
#   source_paths = ["infra"]           # whole package
#   pytest_add_cli_args_test_selection = ["tests/<covering tests>.py"]
rm -rf mutants
mutmut run
# per-module scores: parse mutants/<module>.py.meta -> exit_code_by_key
#   (0 = survived, non-zero = killed)
```

The committed `[tool.mutmut]` in `pyproject.toml` documents the intended
configuration; run it from the flat copy.

## Recommendation

The suite is **solid enough to ship**, but the 93% line coverage overstates
robustness: branch coverage is ~87% and mutation score ~50–60%. Before a
larger release, add tests that (a) exercise the one-direction branches in the
top-gap modules and (b) assert exact backend output for the Compose / GitHub
Actions emission logic. See the report for the concrete surviving-mutant
examples above.
