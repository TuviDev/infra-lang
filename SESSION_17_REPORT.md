# Session 17 Report — Release Sprint

## Metryki
| | S16 | S17 | Delta |
|-|-----|-----|-------|
| Testy | 1319 | **1322** | +3 |
| Coverage | 92% | **92%** | 0 |
| ruff | 0 | 0 | 0 |
| mypy | 0 | 0 | 0 |

Stabilność: `1322 passed` × 3 (bez flakes). Wheel: 110 KB.

## Extension status
- Node.js dostępny: **TAK** (v20.20.2, npm 10.8.2)
- extension.ts skompilowana: **TAK** (`out/extension.js`, `tsc` 0 błędów po
  dodaniu `skipLibCheck` + `@types/node`)
- Stub JS: NIE (Node dostępny, więc pełny build z LSP client)
- Extension instalowalna: **TAK**
- `.gitignore`: dodane `vscode-infra-lang/node_modules/` i `out/`

## Publish status
- wheel: **OK** (110 KB, zawiera grammar.lark + prelude.infra + lsp/server.py)
- twine check: **PASS** (wheel + sdist)
- TestPyPI upload: **SKIPPED** (brak tokena)
- PyPI upload: **SKIPPED** (brak tokena)
- Clean venv install: **PASS** (`infra --version` → 0.1.0, `--help` działa)

## Marketing materials
- `docs/hn_post.md`: **gotowy** (tytuł, body, quick example, talking points)
- `docs/reddit_post.md`: **gotowy**
- `docs/devto_article.md`: **gotowy** (outline 7 sekcji)

## Release checklist
Wszystkie 16 punktów **OK** (tests, ruff, mypy, coverage, wheel, twine, smoke,
contracts, CHANGELOG, README, tutorial, examples, HN/reddit/devto, extension).

## Co trzeba zrobić ręcznie
1. Zarejestruj konto na pypi.org
2. Wygeneruj token API
3. `twine upload dist/*`
4. Opublikuj post na HN: `docs/hn_post.md`
5. Opublikuj na r/devops: `docs/reddit_post.md`
6. Napisz artykuł: `docs/devto_article.md`

## Status projektu po Sesji 17: **v0.1.0 READY**

## Weryfikacja końcowa
```
pytest -n 2            # 1322 passed / 0 failed (×3 stabilnie)
pytest --cov --cov-fail-under=90  # TOTAL 92.24% PASS
ruff check src/        # All checks passed
mypy src/infra         # Success, 51 files
python -m build        # wheel 110 KB + sdist OK
twine check            # PASS
bash scripts/extended_smoke_test.sh  # 17/17 PASS
```
