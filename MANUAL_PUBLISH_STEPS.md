# Kroki do publikacji na PyPI

> Ten dokument opisuje **ręczne** kroki publikacji, gdy w środowisku nie ma
> tokena API (np. w CI/sandboxie). Wykonuje się je lokalnie przez
> właściciela pakietu.

## 1. Zarejestruj się
- pypi.org/account/register
- test.pypi.org/account/register

## 2. Wygeneruj token API
- pypi.org/manage/account/token/
- Uprawnienia: "Entire account" dla pierwszego uploadu

## 3. Zainstaluj twine

```bash
pip install twine
```

## 4. Upload na TestPyPI

```bash
twine upload --repository testpypi dist/*
Username: __token__
Password: pypi-AgEN...
```

## 5. Test instalacji z TestPyPI

```bash
pip install --index-url https://test.pypi.org/simple/ infra-lang
```

## 6. Smoke test

```bash
infra --version
infra validate examples/01_hello_world.infra
```

## 7. Upload na PyPI (produkcja)

```bash
twine upload dist/*
```

## 8. Weryfikacja

```bash
pip install infra-lang
infra --version
```

## Automatyczna wersja (preferowana)

Popchnięcie taga `v*` uruchamia `.github/workflows/publish.yml`, który buduje
wheel i wgrywa na PyPI przy użyciu sekretu `PYPI_TOKEN`:
```bash
git tag v0.1.0
git push --tags
```
