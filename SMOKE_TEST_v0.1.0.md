# Infra Lang v0.1.0 — Smoke Test Results

Smoke test performed against the built wheel installed in a clean virtualenv.

Build: `python -m build` → `infra_lang-0.1.0-py3-none-any.whl` (101 KB) +
`infra_lang-0.1.0.tar.gz` (84 KB).

## Results

| Command | Result | Notes |
|---------|--------|-------|
| `infra --version` | ✅ PASS | prints `infra 0.1.0`, exit 0 |
| `infra --help` | ✅ PASS | full CLI help, exit 0 |
| `infra validate hello.infra` | ✅ PASS | "Found 2 warnings", exit 0 |
| `infra compile hello.infra --target kubernetes --dry-run` | ✅ PASS | emits Deployment + Service, exit 0 |
| `infra compile hello.infra --target compose --dry-run` | ✅ PASS | emits docker-compose.yml, exit 0 |
| `infra fmt hello.infra --check` | ✅ PASS | correctly reports "would reformat" (exit 1 is documented behavior) |
| `infra graph hello.infra` | ✅ PASS | renders graph, exit 0 |
| `infra docs hello.infra` | ✅ PASS | renders inventory, exit 0 |
| `infra check hello.infra` | ✅ PASS | "1 file(s) syntactically valid", exit 0 |
| `infra validate bad.infra` | ✅ PASS | SEC001 (error) + SEC003 (warning), exit 1 as documented |
| `infra diff hello.infra bad.infra` | ✅ PASS | SUMMARY + added/removed, exit 0 |
| `infra diff --format json` | ✅ PASS | valid JSON with summary, exit 0 |

## Bugs found and fixed during the smoke test

1. **`health: http("/")` (colon form) failed to parse.** The grammar only
   accepted `health http("/")`. Added the `HEALTH COLON health_spec` grammar
   rule so the documented/public colon form works. Regression tests added.
2. **Unparseable files dumped a raw rich traceback instead of a clean
   message.** `validate` didn't catch parse exceptions. Now it catches them,
   emits `error[PARSE] file:line:col: ...` (text, JSON and GitHub formats) and
   exits 1. Regression tests added.
3. **`infra docs` leaked prelude built-in constants** (`MANAGED_BY`,
   `K8S_VERSION`, ...) into the inventory. Docs now skip `<prelude>`
   statements. Regression test added.

## Final
All commands PASS after fixes. Package installs and runs correctly from a
clean venv.
