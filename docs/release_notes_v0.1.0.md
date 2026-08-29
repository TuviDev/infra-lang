# Infra Lang v0.1.0

> **⚠️ DOKUMENT ARCHIWALNY.** Noty wydania pierwszej wersji publicznej (0.1.0).
> Bieżące informacje o wydaniach znajdują się w `CHANGELOG.md` oraz na
> stronie GitHub Releases. Plik zachowany dla historii projektu.

**Release date:** 2026-08-16 · **Version:** 0.1.0

Infra Lang is an Infrastructure-as-Code DSL that compiles a single `.infra`
file to Kubernetes YAML, Docker Compose, Terraform HCL, or GitHub Actions.

## Highlights

1. **One definition, multiple targets** — describe `service`, `database`,
   `cache`, `queue`, `storage`, `network`, `secret`, `config`, `pipeline`,
   `environment`, and `cluster` once; compile to the target you need.
2. **Kubernetes** — emits 17 resource kinds (Deployment, Service, Ingress,
   StatefulSet, PVC, ConfigMap, Secret, CronJob, HPA, PodDisruptionBudget,
   NetworkPolicy, ResourceQuota, Namespace, ServiceAccount, ClusterRole,
   ClusterRoleBinding, TopologySpreadConstraints). Generated YAML passes
   `kubeconform -strict` against the official schemas.
3. **Built-in security and reliability linting** — 10 security rules
   (SEC001–SEC010) and 13 reliability rules (REL001–REL014), each with an
   actionable hint. `Error`-severity findings block compilation.
4. **A language server and VS Code extension** — live diagnostics, hover docs,
   context-aware completion, go-to-definition, find-references, workspace
   symbols, and symbol rename across every `.infra` file in the project.
5. **Real Kubernetes E2E tests** — an opt-in suite (`pytest -m live_e2e`) that
   compiles the examples, applies them to a `kind` cluster with `kubectl`, and
   verifies the output contracts.

## What's included

**Backends**

| Target | Output |
|--------|--------|
| Kubernetes | Deployments, Services, Ingress, StatefulSets, PVCs, ConfigMaps, Secrets, CronJobs, HPA, PDBs, NetworkPolicies, ResourceQuotas, Namespaces, RBAC, TopologySpreadConstraints |
| Docker Compose | `docker-compose.yml`, `.env.example`, `Makefile` |
| Terraform | `main.tf`, `variables.tf`, `outputs.tf`, `providers.tf` (AWS/GCP/Azure) |
| GitHub Actions | `.github/workflows/*.yml`, `dependabot.yml` |

**Language & tooling**

- A hand-written LALR(1) grammar with clear `{}` blocks (no YAML).
- Semantic validation with 30+ error codes and source-located hints.
- Template-string interpolation, `import` with cycle detection, and `extends`
  inheritance.
- `infra fmt`, `infra repl`, `infra diff`, `infra graph`, and `infra init`.
- 25+ stdlib functions and a prelude of reusable constants.
- Opt-in anonymous error reporting (off by default, never sends source code,
  paths, or PII).

## Quality

- 1659 tests across lexer, parser, transformer, analyzer, backends, CLI, LSP,
  and live E2E; ~93% coverage.
- `ruff` and `mypy` clean (including `--check-untyped-defs`).
- Generated Kubernetes for all public examples validates with `kubeconform
  -strict` (0 invalid).
- Clean wheel install verified in a fresh virtualenv.

## Known limitations

- Terraform output is structural — no modules, data sources, or remote state.
- GitHub Actions output does not yet support reusable workflows
  (`workflow_call`).
- The LSP is single-process and cross-file operations act on files on disk,
  but rename across files not open in the editor is not yet supported.
- Live E2E requires Docker + kind + kubectl; it is opt-in and skipped when the
  tools are absent.

See [docs/known_limitations.md](known_limitations.md) for the full list.

## Roadmap

The next release targets editor polish and real-world deployment confidence:

- `kind`/`minikube` helper commands (`infra up`, `infra verify`)
- Terraform modules and more explicit outputs
- GitHub reusable workflows
- Richer LSP hover and cross-file rename
- A plugin system (based on community feedback)

See [docs/roadmap_v0.2.0.md](roadmap_v0.2.0.md).
