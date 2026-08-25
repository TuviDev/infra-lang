# Infra Lang v0.2.0 Roadmap

## Theme
"Developer experience and real-world adoption"

## Priority 1 — IDE experience
- LSP completion (keyword + field suggestions)
- Richer hover docs
- Diagnostics improvements (quick-fix actions for common SEC/REL issues)

## Priority 2 — real deployment confidence
- `kind`/`minikube` helper commands (`infra up`, `infra verify`)
- Better output validation (full Kubernetes schema checks)
- Deployment smoke checks (apply + wait + health assertions)

## Priority 3 — Terraform maturity
- Modules support
- Better provider mapping
- More explicit outputs

## Priority 2b — drift detection (implemented MVP)
- **`infra doctor --check-drift`** — recompiles a `.infra` file for a target,
  compares against on-disk generated output (`--out-dir`, default `infra-out`),
  and reports any modified/missing files as unified diffs. Exit code 0 when
  clean, 1 on drift. This addresses post-launch feedback that users hand-edit
  generated manifests, silently diverging from source.

## Priority 4 — based on community feedback
- To be decided after the first users give feedback on
  - cost estimation
  - additional providers/backends
  - a plugin system

## Explicitly out of scope
- General-purpose programming language features
- Full replacement for Helm / Pulumi / Terraform
- Kubernetes operator generation
- A runtime engine / VM
