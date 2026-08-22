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
