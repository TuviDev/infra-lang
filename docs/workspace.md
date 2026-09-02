# Multi-Project Workspaces (since 1.0.0)

A workspace groups several `.infra` projects under one
`infra-workspace.yaml` manifest so a whole platform can be validated,
policy-checked and compiled with a single command.

## Quickstart

```bash
infra workspace init --template micro   # scaffold manifest + projects
infra workspace list                    # table with per-project status
infra workspace check                   # validate + policy-check everything
infra workspace compile -o dist/        # compile each project with its target
```

## The manifest

```yaml
version: "1.0"

projects:
  api:
    path: services/api.infra
    target: kubernetes      # compose | kubernetes | helm | terraform
  web:
    path: services/web.infra
    target: compose

policies:                    # global policies — apply to EVERY project
  - infra-policy.yaml

environments:                # global overlays, merged onto every project
  prod: overlays/prod.infra
```

### Global policies take precedence

Every file listed under `policies:` (the same format as
`infra policy-check --policy …`) is evaluated for **every** project during
`infra workspace check`. A sub-project cannot opt out — guardrails like
"no `:latest` images" hold uniformly across the platform.

### Environment overlays with inheritance

`environments:` maps a name to an `.infra` file containing
`environment "<name>" { … }` blocks. When selected with `-e/--env`, those
blocks are merged onto each project program before validation/compilation.
A project file may define its own block of the same name — **the workspace
overlay wins** (mirroring the policies rule), so platform-wide production
values are the single source of truth.

## Commands

| Command | Behaviour |
|---|---|
| `init [--template basic\|micro\|full]` | Create the manifest + starter files. Refuses to overwrite. |
| `list` | Table: project, path, target, status (`valid` / `invalid` / `parse-error` / `missing`). |
| `check [-e ENV]` | Validate all projects (semantics + global policies), prints `[PASS]`/`[FAIL]` per project, **exit 1 on any failure**. |
| `compile [--project NAME] [-o DIR] [-e ENV]` | Compile one or all projects with their configured targets into `DIR/<name>/`. |

## Templates

- **basic** — one `app.infra`, kubernetes target.
- **micro** — three services (api/web/worker), mixed targets, one global
  policy (`no-latest-tag`).
- **full** — micro's content plus a global `prod` environment overlay.

All template projects pass `workspace check` immediately after `init`.

## Locking

Deployments into workspace projects should run under the state lock — see
[locking.md](locking.md). A stuck lock can be removed with:

```bash
infra workspace unlock <project>          # refuses if the owner is alive
infra workspace unlock <project> --force  # only when you are sure it is gone
```
