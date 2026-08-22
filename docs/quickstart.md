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

## What's next
- Read the tutorial: `docs/tutorial.md`
- See examples: `examples/`
