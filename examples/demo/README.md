# Demo Microservices Project

A complete, commented microservices example: an API service with autoscaling,
a background worker, a Postgres database with backups, and a Redis cache —
all connected through network policies.

## Structure

```
demo/
├── main.infra        entry point (imports everything below)
├── api.infra         public API service (HPA + network policy)
├── worker.infra      background worker (isolated)
├── databases.infra   Postgres with SSL + backup
├── cache.infra       Redis with persistence
└── prod.infra        production environment with quotas
```

## Usage

From this directory:

```bash
# Validate the whole project
infra validate main.infra

# Compile to Kubernetes
infra compile main.infra --target kubernetes

# Compile to Docker Compose
infra compile main.infra --target compose

# Inspect the dependency graph
infra graph main.infra

# Generate documentation
infra docs main.infra
```
