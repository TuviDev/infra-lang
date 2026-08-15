# Quality Gate — Infra Lang

Every release (and PR merge) must pass all gates. No exceptions. No
"we'll fix it later".

## Automated gates (CI enforced)

### Tests
- [ ] All tests pass: 0 failures, 0 errors
- [ ] No flaky tests: suite passes 3x in a row
- [ ] Coverage >= 90%

### Code quality
- [ ] ruff: 0 errors (src/)
- [ ] mypy: 0 errors (src/infra/)

### Contracts
- [ ] All README code blocks parse
- [ ] All tutorial code blocks parse
- [ ] All examples/ files parse and validate
- [ ] All demo/ files compile to valid YAML

### Performance
- [ ] All benchmark budgets pass
  (see [docs/performance_budgets.md](docs/performance_budgets.md))

### Distribution
- [ ] wheel builds successfully
- [ ] wheel installs in clean venv
- [ ] smoke test passes (all 12 commands)

## Manual gates (before public release)

- [ ] [language_decisions.md](docs/language_decisions.md) reviewed
- [ ] [support_matrix.md](docs/support_matrix.md) accurate
- [ ] CHANGELOG.md updated
- [ ] version in pyproject.toml bumped
- [ ] git tag created

## Running all gates locally

### Fast gate (PR ready)
```bash
pytest tests/ -n auto -m "not slow and not e2e" -q
ruff check src/
mypy src/infra --ignore-missing-imports
```

### Full gate (before merge)
```bash
pytest tests/ -n auto -q
pytest tests/ --cov=src/infra --cov-fail-under=90
```

### Release gate (before tag)
```bash
pytest tests/test_contracts.py -v
pytest tests/test_performance.py -v
python -m build
# smoke test in a clean venv
```
