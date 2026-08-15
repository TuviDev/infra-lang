# Session 13 Report — Stabilizacja i Release

## Metryki końcowe
| Metryka | S12 | S13 | Delta |
|---------|-----|-----|-------|
| Testy | 1198 | **1203** | +5 |
| Coverage | 92% | **92%** | 0 |
| ruff | 0 | 0 | 0 |
| mypy | 0 | 0 | 0 |
| Wheel | — | **104 KB** | — |

Stabilność: `1203 passed` × 5 (bez flakes).

## Zadania

### Zadanie 1 + 2 — Smoke test w czystym venv
Zbudowano wheel i zainstalowano w czystym venv. Przetestowano wszystkie
komendy — **12/12 PASS** (version, help, validate, compile k8s/compose,
fmt, graph, docs, check, validate security, diff, diff json).
Wyniki w `SMOKE_TEST_v0.1.0.md`.

Naprawione bugi znalezione przez smoke test (z testami regresyjnymi w
`tests/test_smoke_fixes.py`):
1. `health: http("/")` (forma z dwukropkiem) nie parsowała — dodano regułę
   gramatyki `HEALTH COLON health_spec`.
2. Nieparsowalny plik dumpował surowy rich traceback zamiast czystego
   `error[PARSE]` — `validate` łapie teraz wyjątki i emituje czystą wiadomość
   (text/JSON/GitHub) z exit 1.
3. `infra docs` wyciekał wbudowane stałe preludu (`MANAGED_BY`, ...) —
   pomijane są teraz statementy z `location.file == "<prelude>"`.

### Zadanie 3 — README finalizacja
- Dodane badge: Python 3.11+, MIT License, status v0.1.0.
- Zaktualizowane listy SEC001–SEC010 i REL001–REL014.
- Zaktualizowane liczniki (1100+ testów), dodany `examples/demo/`.
- Wszystkie 6 bloków ```infra w README parsuje się (zweryfikowane skryptem).

### Zadanie 4 — Finalne porządki
- `test_examples.py` i `test_demo.py` istnieją i przechodzą; `test_tutorial.py`
  też.
- Usunięto `*.pyc`, `.coverage.*`, `infra-out`.
- Analiza coverage: **brak modułów poniżej 70%** (najniższy to 88%).
- `pyproject.toml`: `version = "0.1.0"`.

### Zadanie 5 — CHANGELOG
`CHANGELOG.md` — pełna lista Added / Fixed / Notes dla v0.1.0.

### Zadanie 6 — Finalna weryfikacja v0.1.0
```
pytest -n 2            # 1203 passed / 0 failed (×5 stabilnie)
pytest --cov --cov-fail-under=90  # TOTAL 92.35% PASS
ruff check src/        # All checks passed
mypy src/infra         # Success, 46 files
python -m build        # wheel 104 KB + sdist OK
```

## Status
**READY** — `FINAL_STATUS_v0.1.0.md` wystawione.
