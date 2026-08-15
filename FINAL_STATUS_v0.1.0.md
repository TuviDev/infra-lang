# Infra Lang v0.1.0 — Release Status

## Status: READY

## Metryki
| Metryka | Wartość |
|---------|---------|
| Testy | 1203 / 0 failed |
| Coverage | 92% |
| ruff | 0 |
| mypy | 0 |
| Wheel | 104 KB |
| Smoke test | PASS (12/12 komend) |
| Stabilność | 1203 passed × 5 (bez flakes) |

## Zbudowane features
- Core DSL z gramatyką LALR(1) — 11 struktur (service, database, cache,
  queue, storage, network, secret, config, pipeline, environment, cluster).
- 4 backendy: Kubernetes, Docker Compose, Terraform HCL, GitHub Actions.
- Linter security SEC001–SEC010 i reliability REL001–REL014.
- Zasoby K8s: Deployment, Service, Ingress, StatefulSet, PVC, Secret,
  ConfigMap, CronJob, HPA, PodDisruptionBudget, NetworkPolicy, ResourceQuota,
  Namespace, ServiceAccount, ClusterRole, ClusterRoleBinding,
  TopologySpreadConstraints.
- `schedule` → CronJobs + HPA + RBAC; `autoscale` → HPA;
  `disruption` → PDB; `network_policy` → NetworkPolicy;
  `topology` → TopologySpreadConstraints; `affinity` → pod scheduling.
- `environment.quotas` → ResourceQuota; `environment.extends` → inheritance.
- System importów z wykrywaniem cykli; interpolacja template stringów.
- CLI: compile, validate, fmt, diff, graph, docs, repl, init, check.
- `--var`, `--watch`, `--validate-output`.
- Idempotentny formatter; silnik diff oparty o AST; interaktywny REPL.
- Rozszerzenie VS Code (kolorowanie składni + 12 snippetów).
- Workflows GitHub Actions (ci.yml, publish.yml).
- Tutorial, demo project, language spec, release notes, publishing checklist.

## Znane ograniczenia
- Reguły linterów to heurystyki, nie zastępują admission controllerów K8s.
- `--validate-output` wykonuje tylko kontrole strukturalne (nie pełną walidację
  OpenAPI).
- Brak serwera LSP (planowany w v0.2.0).
- Brak automatycznych testów deploymentu na żywym klastrze.
- Output Terraform jest podstawowy (klaster/zasoby; generowanie modułów
  minimalne).

## Instrukcja release (ręczna)
1. `git tag v0.1.0`
2. `git push --tags`
3. GitHub Actions `publish.yml` buduje wheel i wgrywa na PyPI
   (sekrety: `PYPI_TOKEN`).
   lub ręcznie:
   ```bash
   twine upload dist/*
   ```
4. `pip install infra-lang` — działa (potwierdzone smoke testem w czystym venv).
