# FOUNDATION AUDIT — v0.5.4-foundation (Faza 1, tylko raport; zero zmian w kodzie)

**Data:** 2026-08-30 · **Basis:** `677ed0f` (tip po milestone test-perf) · **Autor:** Pracuś
**Zakres:** audyt jakości/testów/CI/długu/deps/bezpieczeństwa + rekomendacje P0/P1/P2.
**Środowisko pomiarów:** sandbox Linux, Python 3.13, helm v3.21.4, dual-stack LSP (pygls 2.1.1 dev / 1.3.1 venv131).

---

## 1. Baseline jakości (świeże, ten sam dzień)

| Pomiar | Komenda | Wynik |
|---|---|---|
| Full dev | `pytest tests/ -q -o addopts=""` | **2839 passed, 10 skipped, 0 failed — 93.48 s** |
| Windows-CI-like | `pytest tests/ -q -o addopts="" -m "not slow"` | **2795 passed, 44 deselected, 10 skipped, 0 failed — 34.36 s** (Δ −59.1 s) |
| Coverage (LINE/BRANCH, wiersz TOTAL) | `--cov=infra --cov-branch` | Stmts 9475, Miss 231, Branch 3798, BrPart 246 → **LINE 97.56% / BRANCH 93.52%** |
| Full legacy (pygls 1.3.1) | to samo w venv131 | **2818 passed, 31 skipped, 0 failed — 79.51 s** |
| Lint | `ruff check src/` | **0 naruszeń** |
| Typy | `MYPYPATH=src mypy -p infra --strict` | **0 + 0** (dev i legacy, 76 plików) |
| Pakiet | `python -m build && twine check dist/*` | **PASSED ×2** (`infra_lang-0.5.3`) |

Moduły < 90% pokrycia: **tylko `src/infra/__init__.py` (88%)** — missy: linie 31-33 (wrapper `parse_file`) i 67 (gałąź `compile(source: str)`).

## 2. Testy

- **Skala:** 2849 zebranych (`--collect-only`), 107 plików `test_*.py` (111 `.py` z helpersami), 4 testy `@given` (hypothesis, 1 plik).
- **Markery (wystąpienia):** `slow` 16, `skipif` 10, `live_e2e` 5, `parametrize` 48, `contracts` 2, `e2e` 1, `behavioral` 1. Zarejestrowane w `pyproject.toml:85-92` ✓.
- **Skips:** dev 10 (docker ×6, compose ×4 — env, brak binarek); legacy +21 (dual-stack: 20 × `test_lsp_pygls2` wymaga pygls≥2 + 1 placeholder). Zero xfail.
- **TOP 30 wolnych (junit F1, call-time):**

  | Czas | Test | Powód |
  |---|---|---|
  | 9.04 s | watch_mode::test_watch_shows_error_without_crash | 3× sleep(3) + Popen |
  | 7.41 s | publish_readiness::test_clean_venv_install | pip do czystego venv |
  | 5.47 s | chaos_audit::TestParallelCompilation | storm 20 wątków |
  | 5.02 s | watch_mode::test_watch_compiles_on_startup | sleep(5) + Popen |
  | 4.21 s | chaos_audit::TestRepeatedCompile | 10× kompilacja K8s |
  | 3.06 s | distribution::TestCLISubprocess::test_fmt_check_valid_file | spawn CLI |
  | 2.53 s | watch_mode::test_watch_recompiles_on_change | polling deadline |
  | ~1.3–1.9 s ×6 | distribution::TestCLISubprocess (reszta) | spawn CLI |
  | 1.64 s | helm_backend::TestHelmUtf8Encoding | 3 przykłady × backend |
  | 1.56 s | integration_audit::…k8s | pełny pipeline |
  | 1.55 s | resolver_imports::…own_lark | budowa gramatyki |
  | 1.27 s | chaos_audit::TestLargeFileStress | duży .infra end-to-end |
  | 0.5–1.1 s ×18 | interpolation/var_interpolation battery | multi-compile |

  Razem: 18 testów ≥1 s (suma ≈ 41 s), 4 testy ≥5 s (suma ≈ 26 s) — wszystkie w klasach oznaczonych `slow`.
- **Duplikaty scenariuszy:** (a) `helm lint --strict` + `helm template` — 2× (TestHelmBinaryIntegration 2 testy vs test_live_helm_e2e na 02_web_app; live pokrywa wszystkie przykłady) — koszt ~1 s; (b) konwersje values helm powielają się między test_helm_backend a test_helm_edges (celowe edge-kopy z v0.5.3 — różne poziomy: unit vs binary).
- **Flaky candidates:** brak aktywnych (historyczne: watch-recompile — ma skipif win32 + deadline; serve port-conflict — usztywnione `SO_EXCLUSIVEADDRUSE`; live_* zawsze gated na binarki). Hypothesis: deterministyczne seedem (default).
- **Timeouty:** wszystkie `subprocess` w tests/ i src/ mają `timeout=` (wykaz zbiorczo niżej). Jedyny brakujący system: **globalny `pytest-timeout`** (dziś 60 s+ wisi tylko przynowe scenariusze— P0 już naprawione).

## 3. CI / GitHub Actions

- **Inwentarz akcji (5 workflowów):** `checkout@v5` ×5, `setup-python@v6` ×3, `setup-node@v5` ×2, `upload-artifact@v5` ×2, `upload-pages-artifact@v4` ×1, `deploy-pages@v4` ×1 — **wszystkie Node-24-ready** ✓; `node-version: "22"` w extension/marketplace ✓.
- **Joby:** `ci.yml` 9 jobów (3 OS × py3.11–3.13) — ostatni zielony run (kontekst Kierownika): ~3 min; docs/extension/marketplace/publish — triggerowane tagami/gałęziami.
- **Sekrety (nazwy):** `PYPI_TOKEN`, `VSCE_PAT`, `OVSX_TOKEN` — użyte przez `env:` + `if: env.* != ''` ✓.
- **`timeout-minutes`:** tylko `ci.yml:test` (15). **Braki:** docs/extension/marketplace/publish — 0 timeoutów → P1.
- **`publish.yml:38`:** `twine upload dist/*` — **brak `--skip-existing`** → force-retag tej samej wersji = HTTP 400 (znany incydent z kontekstu) → P0.
- **Nightly:** nie istnieje → P1 (pełna suita `slow` rusza tylko PR-e).
- **Matrix rozsądek:** Windows-sim kosztuje 34 s sandbox / szac. 2–3 min GHA × 3 py — redukcja macierzy **nieuzasadniona** (zostaje, P2-obserwacja).

## 4. Kod / dług techniczny

- **Pliki > 500 LOC (11):** `parser/transformer.py` 2220 (zakaz refactoru), `backends/kubernetes.py` 1283, `parser/ast_nodes.py` 1225, `lsp/server.py` 921, `backends/terraform.py` 763, `analyzer/validator.py` 743, `importer/k8s.py` 725, `backends/helm.py` 714, `analyzer/drift.py` 633, `cli/printer.py` 586, `analyzer/ui_generator.py` 525. → P2 (mapa drobienia w przyszłych milestone'ach).
- **`# type: ignore` ×13:** `parser/transformer.py` 6, `lsp/server.py` 6 (dual-stack pygls — uzasadnione wierszowo), `lexer/tokens.py` 1. Wszystkie z kodami błędów ✓.
- **`# noqa` ×58:** terraform 14, lsp/server 9, reliability 6, security 5, feedback 3, serve_cmd 3… (nazewnictwo stdlib/N802 + celowe wyjątki) ✓.
- **TODO/FIXME/HACK/XXX w `.py`:** **0** (poprawka: wcześniejszy zlicz „2" to artefakt `.pyc`).
- **Gołe `pass`:** 6 miejsc; z komentarzem: `backends/github.py:191`, `lsp/server.py:420`. **Bez komentarza:** `cli/compile.py:190` (broad except przy zbieraniu importów), `cli/init.py:226` (best-effort `git init`), `cli/serve_cmd.py:202` (KeyboardInterrupt), `feedback.py:139` (fire-and-forget POST). → P2 (4 jednolinijkowe komentarze intencji).
- **Martwe/nieaktualne docs:** `docs/roadmap_v0.2.0.md` i `docs/release_notes_v0.1.0.md` — **banery ARCHIWALNE już są** (weryfikowane nagłówki) ✓. `docs/benchmark_baseline.md` (v0.1.0) — nadal żywy: używany przez `scripts/benchmark.py --compare` → zostaje bez banera. Inwentarz docs/: 16 plików, brak innych kandydatów.
- **Hardcode ownerów/URL:** kanon `TuviDev` spójny (`lsp/server.py:176`, `analyzer/cost.py:111`, docs, package.json, testy); `kakukpl` tylko w komentarzu-bonerze `tests/test_lsp_features.py:69` ✓.

## 5. Zależności

- **`pip list --outdated` (zbiór projektowy):** PUSTE — runtime i dev-tool aktualne (pytest 9.0.3, pytest-cov 7.1.0, pytest-xdist 3.8.0, hypothesis 6.167.0, ruff 0.16.5, mypy 2.3.1, lark 1.3.1, typer 0.27.0, rich 15.0.0, ruamel.yaml 0.19.1, PyYAML 6.0.3, watchdog 6.0.0, pygls 2.1.1, lsprotocol 2025.0.0, build 1.6.0, twine 7.0.0).
- **Capy runtime (pyproject:36-42):** wszystkie 7 zależności z górnymi capami ✓; `lsp` extra `pygls<3.0.0` ✓.
- **Ryzyka bez capa:** brak w runtime; dev-extra bez górnych capów (`pytest>=8`, `ruff>=0.4`, …) → P2-obserwacja (reprodukowalność CI; nie ruszać w tym PR bez osobnego audytu breaking changes).
- **`pytest-timeout`:** nieobecny → do dodania w Fazie 2 (globalnie 60 s + `timeout(300)` na klasach slow), jako dev-dep z capem `<3.0.0`.

## 6. Bezpieczeństwo (bez rewritingu)

- **Serve bind:** `serve_cmd.py:32` `_BIND_HOST = "127.0.0.1"` ✓ loopback-only; `allow_reuse_address = False` (v-test-perf) ✓.
- **`shell=True`:** 0 w `src/` ✓.
- **`subprocess` bez timeout:** 0 (weryfikowane multiline: `analyzer/drift.py:235`, `cli/doctor.py:31`, `cli/init.py:219` `git init` 60 s, `cli/up_cmd.py:62` `_SUBPROCESS_TIMEOUT`) ✓.
- **Sekrety w kodzie:** brak wzorca (skan regex) ✓; fixtures testowe NIE są sekretami (placeholder strings).
- **Telemetria egress:** `feedback.py:28` `COLLECTOR_URL = None` — no-op out-of-box ✓; jedyny egress opcjonalny z `urlopen(req, timeout=2)` (`:137-139`) ✓.

## 7. Rekomendacje P0/P1/P2 (posortowane)

| Priorytet | Co | PLIK:LINIA | Dlaczego |
|---|---|---|---|
| **P0** | `twine upload --skip-existing` | `.github/workflows/publish.yml:38` | retag tej samej wersji = HTTP 400 (udokumentowany incydent); idempotentny publish |
| **P1** | Nightly workflow (full suite, `slow` włączone) | nowy `.github/workflows/nightly.yml` | security/chaos/publish integracje ruszają dziś tylko PR-owo na unix; Windows pełny profil nigdzie |
| **P1** | `timeout-minutes` na jobach docs/extension/marketplace/publish | 4 workflowy | brak twardego limitu przy zawieszeniu runnera |
| **P1** | `pytest-timeout` 60 s globalnie + 300 s na klasach slow | `pyproject.toml` (+dev extra) | twardy parasol przeciwko regresom „wiszącego testu" (lekcja 369.6 s) |
| **P2** | 4 komentarze przy gołych `pass` | `cli/compile.py:190`, `cli/init.py:226`, `cli/serve_cmd.py:202`, `feedback.py:139` | intencja czytelna bez zmiany zachowania |
| **P2** | Edge-testy dla `infra/__init__.py` (moduł < 90%) | `src/infra/__init__.py:31-33,67` | jedyny moduł poniżej 90%; koszt: 2 testy |
| **P2** | Sekcja „Running tests on Windows vs Unix" | `CONTRIBUTING.md` | quick start zna tylko `-n auto` (unix) |
| **P2** | Dedup helm unit vs live_e2e | `tests/test_helm_backend.py:281-313` | bezpieczne do scalenia, ale zysk ~1 s — **odroczone** (zapisane tutaj na 0.5.5+) |
| **P2-obs** | Mapa plików >500 LOC; capy dev-extra; matrix Windows 3×py | — | tylko monit, bez prac w tym milestone |

**Decyzja Fazowa:** Faza 5 (updates) — po `pip list --outdated`=∅ przewidywany **brak commita deps** (odnotować w raporcie końcowym); jedyna nowa zależność to `pytest-timeout` z Fazy 2 (dev, z capem).
