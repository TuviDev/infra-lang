# Infra Lang — Kompletny Przekaz Projektu dla Agenta Kierownika

> Status: **v0.1.0 COMPLETE / READY FOR PUBLIC RELEASE**
> Data: 2026-08-14 | Baseline: **1492 passed / 92.39% coverage / ruff 0 / mypy 0**

---

## 1. Co to jest

**Infra Lang** to język domenowy (DSL) do Infrastruktury jako Kodu. Jeden
plik `.infra` kompiluje się do **4 backendów**: Kubernetes YAML, Docker
Compose, Terraform HCL (AWS/GCP/Azure), GitHub Actions. Zbudowany w Pythonie
3.11+ na parserze Lark LALR(1), z wbudowanymi linterami bezpieczeństwa (SEC)
i niezawodności (REL), LSP serverem i rozszerzeniem VS Code.

---

## 2. Metryki końcowe (zweryfikowane 2026-08-14)

| Metryka | Wartość |
|---------|---------|
| Testy | **1492 passed / 0 failed** |
| Coverage | **92.39%** (fail_under=90) |
| ruff | **0** (src/) |
| mypy | **0** (51 plików) |
| Stabilność | 1492 × 3 (bez flakes) |
| Wheel | 111 KB (grammar.lark + prelude.infra + lsp/server.py) |
| twine check | PASS |
| Extended smoke | 17/17 PASS |
| kubeconform -strict (realne schematy K8s) | PASS — 0 invalid we wszystkich przykładach |
| Pliki testowe | 70 |

---

## 3. Struktura projektu

```
infra-lang/
├── src/infra/
│   ├── lexer/          grammar.lark + tokens (LALR(1))
│   ├── parser/         AST nodes + Lark transformer
│   ├── analyzer/       SemanticValidator, security (SEC), reliability (REL)
│   ├── backends/       kubernetes.py, compose.py, terraform.py, github.py
│   ├── resolver/       imports + extends resolution
│   ├── diff/           silnik diff oparty o AST
│   ├── validation/     k8s_validator.py, schema_validator.py
│   ├── lsp/            serwer LSP (pygls 1.3.1)
│   ├── stdlib/         funkcje + prelude
│   ├── cli/            9+ komend
│   └── errors/         typy błędów + reporter
├── tests/             70 plików, 1492 testów
├── vscode-infra-lang/  rozszerzenie (syntax + snippets + LSP client)
├── .github/workflows/  ci.yml + publish.yml
├── docs/               pełna dokumentacja
├── examples/           01-04 + demo/ + demo_script/
└── scripts/            extended_smoke_test.sh, manual_mutation.py
```

---

## 4. Funkcje języka

### Struktury (11)
service, database, cache, queue, storage, network, secret, config,
pipeline, environment, cluster

### Zaawansowane bloki
- `schedule {}` → CronJobs + HPA + RBAC
- `autoscale {}` → HorizontalPodAutoscaler
- `disruption {}` → PodDisruptionBudget
- `network_policy {}` → NetworkPolicy (inline per-serwis)
- `topology {}` → TopologySpreadConstraints
- `affinity {}` → pod affinity/anti-affinity
- `environment.quotas {}` → ResourceQuota
- `environment extends` → dziedziczenie
- Importy z wykrywaniem cykli
- Template stringi z interpolacją `{expr}` (backtick)
- `let`/`const`, dekoratory `@name(args)`, stdlib 25+ funkcji, prelude

### Backendy
- **Kubernetes**: 17 typów zasobów (Deployment, Service, Ingress,
  StatefulSet, PVC, Secret, ConfigMap, CronJob, HPA, PDB, NetworkPolicy,
  ResourceQuota, Namespace, ServiceAccount, ClusterRole, ClusterRoleBinding,
  TopologySpreadConstraints)
- **Docker Compose** (v3)
- **Terraform HCL** (AWS/GCP/Azure — strukturalny)
- **GitHub Actions**

### Lintery
- **SEC001–SEC010** (10 reguł security): hardcoded secrets, mutable tags,
  privileged, root, SSL, credential patterns, ingress bez netpol, Docker Hub
  image, env secret w prod
- **REL001–REL014** (14 reguł reliability): thundering herd, even HA
  replicas, brak limitów, brak backupu, Kafka single replica, itd.
- Zero false positives (mutation tested — 100% manualnie)

### CLI (komendy)
compile, validate, fmt, diff, graph, docs, repl, init, check, **lsp**

### Narzędzia
- `--var` interpolacja, `--watch`, `--validate-output`, `--split`, `--dry-run`
- LSP server (diagnostics + hover) — pygls 1.3.1
- Rozszerzenie VS Code (kolorowanie + 12 snippetów + LSP client, skompilowane JS)

---

## 5. Status sesji (historia pracy)

| Sesja | Zakres | Wynik |
|-------|--------|-------|
| S7 | Podstawy DSL | base |
| S8 / 8.1 | Coverage 90%, naprawa transformer | 978→1069, 91% |
| S9 | network_policy, topology, quotas, REL012-14, mutation 100% | 1042, 90% |
| S10 | affinity, init templates, SEC008-10, graph, diff, K8s validator | 1069→1148, 92% |
| S11 | testy behawioralne, tutorial, CI, demo | 1182 |
| S12 | VS Code extension, K8s validator | 1198 |
| S13 | smoke test, README, CHANGELOG, release prep | 1203 |
| S14 | spec freeze, contracts, support matrix, budgets, quality gate, corpus, versioning | 1272 |
| S15 | PyPI pipeline, schema validator, error messages, demo script | 1296 |
| S16 | LSP server + hover, CLI, VS Code client | 1319 |
| S17 | JS build, publish checklist, HN/reddit/devto drafts | 1322 |
| S18a | Deep audit: 4 bugi (managed-by, strategy rolling, bool false, compose volumes) | 1485 |
| S18b | Real K8s (kubeconform), chaos (thread-safety YAML), konsolidacja, roadmapa | 1492 |
| S19 | Final release prep: artifacts, CHANGELOG, quickstart, publish | 1492, READY |

---

## 6. Ważne uwagi operacyjne (dla przyszłych sesji)

### Środowisko resetuje się między sesjami
Pakiety MUSZĄ być reinstalowane na starcie. **Uwaga na kolejność:**
```bash
pip install pytest pytest-xdist pytest-cov hypothesis ruff mypy lark typer rich \
    ruamel.yaml watchdog prompt_toolkit build twine "pygls==1.3.1" "lsprotocol==2023.0.1" -q
pip install -e ".[dev]" -q
# POTEM obniż pygls z powrotem do 1.3.1 (dev extra podbija do 2.x!)
pip install "pygls==1.3.1" "lsprotocol==2023.0.1" -q
```
- `pip install -e ".[dev]"` podbija pygls do 2.x, który ma INNE API — LSP kod
  jest napisany pod **1.3.1** (`pygls.server.LanguageServer`).
- Artefakty znikają po resecie: **odbuduj** `python -m build` + `npm run
  compile` (vscode-infra-lang) — inaczej 9 testów publish_readiness/extension
  failuje.

### Komendy testowe
```bash
# pełna suite (fast)
timeout 200 python3 -m pytest tests/ -p no:cacheprovider --no-cov -n 2 --dist=loadfile -q
# coverage (WOLNE ~5min, może przekroczyć 300s)
timeout 360 python3 -m pytest tests/ -p no:cacheprovider --cov=src/infra \
  --cov-report=term --cov-fail-under=90 -q -n 2 --dist=loadfile
```

### Zasady (absolutne)
- Nigdy nie schodź poniżej aktualnej liczby testów (1492) ani coverage <90%.
- Zawsze kończ ruff 0 + mypy 0.
- **Sprawdzaj REALNE API przed pisaniem kodu** — nazwy AST/grammar/backend
  różnią się od intuicji. Np.: `replicas: 0` → E011 (nie E003);
  `TextDocumentContentChangeEvent` to Union (użyj `_Type1`);
  secret wymaga `key: from env` (dwukropek); `environment extends` bez
  dwukropka.
- Naprawiaj KOD gdy test failuje, nie osłabiaj testu.
- **UWAGA na pygls 2.x vs 1.x** — sprawdź `pip show pygls` przed pracą nad LSP.

### Git
- Projekt NIE jest repozytorium git (brak `.git`). Komendy `git status`/`git
  branch` nie działają. Sesja 20 zaczynała od "Git release" — wymaga
  `git init` przed użyciem.

---

## 7. Publikacja — stan

| Krok | Status |
|------|--------|
| wheel + sdist | OK (111 KB) |
| twine check | PASS |
| clean venv install | PASS |
| smoke test | 17/17 PASS |
| Realne schematy K8s (kubeconform) | PASS |
| **PyPI / TestPyPI upload** | **SKIPPED — brak tokena w środowisku** |
| HN / Reddit / Dev.to | gotowe drafty w docs/ |

**Do zrobienia ręcznie przez właściciela:** rejestracja na pypi.org, token API,
`twine upload dist/*` lub `git tag v0.1.0 && git push --tags` (uruchomi
`publish.yml`). Instrukcje: `MANUAL_PUBLISH_STEPS.md`, `PUBLISHING_CHECKLIST.md`.

---

## 8. Kluczowe raporty (dokumentacja pracy)

| Plik | Treść |
|------|-------|
| `SESSION_19_REPORT.md` | Final release prep (najnowszy) |
| `REAL_K8S_REPORT.md` | Walidacja na realnych schematach K8s |
| `CHAOS_TEST_REPORT.md` | Stress/chaos + thread-safety bug |
| `TEST_CONSOLIDATION_REPORT.md` | Audyt redundancji testów |
| `docs/roadmap_v0.2.0.md` | Plan rozwoju (LSP, kind helpers, Terraform) |
| `CHANGELOG.md` | Full changelog v0.1.0 |
| `QUALITY_GATE.md` | Bramki jakości przed release |

---

## 9. Znane ograniczenia (uczciwa ocena)

- **Brak realnego `kubectl apply`** na żywym klastrze (blokowane brakiem Dockera
  w sandboxie; kubeconform potwierdza strukturę, nie działanie).
- **LSP jest minimalny** (diagnostics + hover) — brak completion,
  go-to-definition, rename (planowane v0.2.0).
- **Terraform strukturalny** (bez modules/data sources/remote state).
- **GitHub Actions** — brak reusable workflows/workflow_call.
- Projekt **nie jest jeszcze repozytorium git** — brak historii commitów.
