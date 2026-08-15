# Publishing Infra Lang to PyPI

## Prerequisites
- [ ] PyPI account at pypi.org
- [ ] TestPyPI account at test.pypi.org
- [ ] API token generated

## Steps

### 1. Build
```bash
python -m build
twine check dist/*
```

### 2. Test on TestPyPI
```bash
twine upload --repository testpypi dist/*
pip install --index-url https://test.pypi.org/simple/ infra-lang
```

### 3. Smoke test
```bash
infra --version
infra validate <test-file.infra>
infra compile <test-file.infra> --target kubernetes --dry-run
```

### 4. Publish to PyPI
```bash
twine upload dist/*
pip install infra-lang
```

## GitHub Actions (automated)
- Merge to `main` → `ci.yml` runs tests, ruff, mypy and coverage.
- Push a `v*` tag → `publish.yml` builds the wheel and uploads it to PyPI
  using the `PYPI_TOKEN` secret.
