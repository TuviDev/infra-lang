# GitHub Codespaces

This container gives you a ready-to-use Infra Lang dev environment — no local
installation needed. Open the repo in Codespaces and you get Python 3.12,
Docker-in-Docker (for the live E2E suites), `kubectl`/`helm`/`minikube`, and the
Python/Ruff/Mypy VS Code extensions pre-installed.

## Run the tests

```bash
pytest tests/ -n auto -q
```

Run only the fast suite (skip the optional live E2E):

```bash
pytest tests -m "not live_e2e" -q
```

## Run the live E2E suites (need a Docker daemon)

```bash
# Kubernetes on a kind cluster
pytest tests -m live_e2e -q

# Helm chart validation
pytest tests/test_live_helm_e2e.py -v
```

`docker-in-docker` is enabled, so `kind` can create clusters inside the
container. The first run pulls images and may take a few minutes.

## Known limitations

- Codespaces have limited CPU/RAM compared to a local machine; the full suite
  can take a few minutes, and image pulls are slower.
- The VS Code extension is built automatically on container creation
  (`npm run compile`), so syntax highlighting and the language server are
  available in the editor.

## Quality gates

```bash
ruff check src/
mypy src/infra --ignore-missing-imports
python -m build && python -m twine check dist/*
```
