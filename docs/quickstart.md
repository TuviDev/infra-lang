# Infra Lang — 5-minute quickstart

## Install
```bash
pip install infra-lang
# with the language server (recommended for VS Code):
pip install 'infra-lang[lsp]'
```

Verify: `infra --version`

> For the latest development version, install from Git:
> `pip install git+https://github.com/TuviDev/infra-lang.git`
> Requires Python 3.11+.

## Create your first .infra file
Save as `app.infra`:

```infra
service api {
    image: "nginx:1.25.3"
    port: 8080
    health: http("/health")
    resources {
        requests { cpu: 100m, memory: 128Mi }
        limits   { cpu: 500m,  memory: 256Mi }
    }
}

database db {
    type: postgres
    version: "15"
    storage: 10Gi
    ssl: true
    backup { enabled: true, schedule: "0 2 * * *" }
}

secret db-creds {
    url: from env "DATABASE_URL"
}
```

## Validate
```bash
infra validate app.infra
```

### Budget guardrail for CI/CD

Fail the pipeline when the estimated monthly cost exceeds your budget
(FinOps gate). On breach, validation exits 1 with a `COST_EXCEEDED` error:

```bash
# exit 1 when the estimate exceeds $200/month
infra validate app.infra --max-cost 200

# combines with environment overlays — prices the "prod" variant
infra validate app.infra -e prod --max-cost 500

# the same guardrail exists on the syntax-only check command
infra check app.infra --max-cost 200
```

Output on breach:

```
error[COST_EXCEEDED] app.infra: Estimated monthly cost $330.00 exceeds the --max-cost budget of $200.00
  Hint: Reduce CPU/RAM requests or database instances to fit budget
```

## Compile to Kubernetes
```bash
infra compile app.infra --target kubernetes
```

## Compile to Docker Compose
```bash
infra compile app.infra --target compose
```

## See what it looks like
```bash
ls infra-out/
```

## Check the diff between two configs
```bash
cp app.infra app_v2.infra
# change replicas to 5 in api
infra diff app.infra app_v2.infra
```

## Preview changes against the live infrastructure

Like `terraform plan`: `infra diff --live` compares your `.infra` spec with
the **live** state of a Kubernetes namespace or a running Docker Compose
stack and shows the planned changes *before* you deploy. The probes are
strictly read-only — nothing on the cluster is ever modified.

```bash
# Plan against a Kubernetes namespace (default target)
infra diff app.infra --live --namespace default

# Plan against a running Docker Compose stack
infra diff app.infra --live --target compose

# Plan for a specific environment overlay
infra diff app.infra --live -e prod
```

Example output:

```
~ service "app":
    replicas: 2 -> 5
    image: "myapi:v1.0" -> "myapi:v1.1"

Plan: 0 to create, 1 to change (2 field change(s) across 1 service(s)); 2 unchanged.
Hint: run `infra up <file>` to apply the planned changes.
```

The command exits 0 when the live state already matches the spec and 1 when
changes are pending, so it doubles as a CI/CD gate. Add `--format json` for
a structural report instead of the colored preview.

## Deploy it directly

Apply the compiled resources to a live platform (Kubernetes, Compose, or Helm)
with `infra up`, and remove them with `infra down`:

```bash
# Deploy to a Kubernetes cluster (requires kubectl on PATH)
infra up app.infra --target kubernetes

# Preview the commands without executing them
infra up app.infra --target kubernetes --dry-run

# Bring up a Docker Compose stack (requires the Docker daemon)
infra up app.infra --target compose

# Tear everything back down
infra down app.infra --target compose
```

Missing a tool? `infra up`/`infra down` tell you what's missing and point you
at `infra doctor`.

## Estimate the cost

Get a rough monthly cost estimate for your infrastructure:

```bash
infra cost app.infra                    # rich table
infra cost app.infra --json             # structured JSON for CI gates
infra cost app.infra --currency PLN     # other currencies
```

### FinOps reports for pull requests

Render the estimate as Markdown or HTML — ready to paste into a PR comment or
a CI job summary:

```bash
# Markdown table for a GitHub/GitLab PR comment
infra cost app.infra --format markdown

# HTML table (e.g. for a GitHub Actions job summary)
infra cost app.infra --format html

# Write the report straight to a file
infra cost app.infra --format markdown --output cost-report.md
```

In a GitHub Actions workflow you can post the report on every PR:

```yaml
- run: infra cost app.infra --format markdown --output cost.md
- uses: marocchino/sticky-pull-request-comment@v2
  with:
    path: cost.md
```

## Detect drift against the live infrastructure

After deploying, someone may `kubectl scale` a Deployment or hot-patch an
image — silently diverging from your `.infra` spec. Catch it with the live
drift check (read-only; it never mutates the cluster):

```bash
# Compare the spec against a live Kubernetes namespace
infra doctor --check-drift app.infra --live --target k8s --namespace default

# Compare against a running Docker Compose stack
infra doctor --check-drift app.infra --live --target compose

# Structured JSON for CI/CD gates (exit code 1 on drift)
infra doctor --check-drift app.infra --live --json
```

The check compares replicas, container image, ports and environment variables
and prints an In-Sync/Drifted table plus explicit drift lines:

```
[DRIFT] api: replicas expected 3, live 1 (MODIFIED)
```

Without `--live`, `infra doctor --check-drift` keeps its original behavior:
comparing the compiled output against on-disk generated files (`--out-dir`).

## What's next
- Read the tutorial: `docs/tutorial.md`
- See examples: `examples/`
