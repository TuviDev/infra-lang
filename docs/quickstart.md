# Infra Lang — 5-minute quickstart

## Install
```bash
pip install infra-lang
# with the language server (recommended for VS Code):
pip install 'infra-lang[lsp]'
```

Verify: `infra --version`

> Available on PyPI. Requires
> Python 3.11+.

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

## What's next
- Read the tutorial: `docs/tutorial.md`
- See examples: `examples/`
