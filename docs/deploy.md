# Deploy & Rollback (since 1.0.0)

`infra deploy` closes the loop between *defining* infrastructure and *running*
it — with a dry-run plan by default, rollout verification, automatic rollback
and a complete local history of every revision.

> **All external tools are invoked as plain subprocesses** (`docker`,
> `kubectl`, `helm`, `terraform`). Nothing is ever contacted in tests — the
> suite mocks `subprocess.run` entirely.

## Quickstart

```bash
# 1. Dry-run (DEFAULT): print the structured plan, touch nothing
infra deploy app.infra -t kubernetes

# 2. Apply for real (requires the tool on PATH)
infra deploy app.infra -t compose --apply
infra deploy app.infra -t k8s --apply --timeout 180

# 3. Something went wrong? Inspect and restore a previous revision
infra rollback app.infra                      # history table
infra rollback app.infra --to-revision r0001  # restore r0001
```

## The dry-run plan

Every deploy starts as a plan (safe by default — `--dry-run` is the default):

```
Deployment plan for app.infra (target: kubernetes)
  resources: 2
    - service api
    - database db
  estimated monthly cost: $142.40
  risk indicators: 2 warning(s)
    - [SEC003] Service 'api' uses mutable image tag 'latest'...
    - [REL004] Service 'api' has no health checks...
  files to apply: infra.yaml
  commands:
    $ kubectl apply -f <manifest-dir>
```

- **Cost** comes from the existing `infra cost` estimator.
- **Risks** come from the security & reliability checkers (`SEC*`/`REL*`).
- The plan itself is recorded in history as a `PLANNED` revision — zero
  subprocess calls are made.

## Targets

| Target        | Applied with                                  | Rollout check                                        |
|---------------|-----------------------------------------------|------------------------------------------------------|
| `compose`     | `docker compose -f <file> up -d`              | `ps --format json` — nothing `exited`/`dead`/`unhealthy` |
| `kubernetes`  | `kubectl apply -f <dir>`                      | `kubectl rollout status deployment/<svc>` per service    |
| `helm`        | `helm upgrade --install <release> <chart>`    | `helm status <release>`                                  |
| `terraform`   | `terraform apply -auto-approve` (cwd = dir)   | exit code of `apply`                                     |

Aliases: `k8s` → `kubernetes`, `docker` → `compose`, `tf` → `terraform`.

## Auto-rollback

With `--auto-rollback` (default), a failed apply or rollout check triggers
an automatic restore:

1. **Previous good snapshot** — the manifests of the last `SUCCESS`/`RESTORED`
   revision are re-applied (works for every target).
2. **Tool-native undo** — if there is no previous revision:
   `kubectl rollout undo` per service / `helm rollback <release>`.
   Compose and Terraform have no native undo, so without history the deploy
   ends as `FAILED` (with the reason recorded).

Opt out with `--no-auto-rollback`.

## Local history

Every revision is recorded under `.infra-state/<project>/`:

```
.infra-state/app/
├── history/r0001.json        # metadata: target, files, status, duration, hash
├── history/r0002.json
└── snapshots/
    ├── r0001/infra.yaml      # exact manifests that were applied
    └── r0002/infra.yaml
```

`infra rollback app.infra` prints the table; `--to-revision r0001` re-applies
the snapshot and records a new `RESTORED` revision. Revision ids are unique
prefixes, so `--to-revision r00` works when unambiguous.

## Options

```
--target, -t      compose | kubernetes (k8s) | helm | terraform  [kubernetes]
--environment, -e/--env   environment overlay name
--apply / --dry-run       execute vs. plan-only  [dry-run]
--force                   alias for --apply
--timeout                 rollout timeout per service in seconds  [120]
--auto-rollback / --no-auto-rollback  [--auto-rollback]
--to-revision REV         (rollback) revision to restore
```

## Safety invariants

- **Default is dry-run.** Applying requires an explicit `--apply`/`--force`.
- **Locked state directory** — see [locking.md](locking.md).
- **Exit codes**: `0` on `SUCCESS`/`RESTORED`, `1` otherwise (CI-friendly).
