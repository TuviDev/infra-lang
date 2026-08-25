# Helm backend

Compile your `.infra` definition to a complete, idiomatic **Helm chart** that
passes `helm lint --strict` and renders with `helm template` to Kubernetes YAML.

```bash
infra compile app.infra --target helm
```

This produces a directory like `myapp-chart/` containing:

```
myapp-chart/
├── Chart.yaml          # chart metadata (apiVersion v2, name, version)
├── values.yaml         # all configurable parameters as defaults
├── .helmignore
└── templates/
    ├── _helpers.tpl    # fullname / labels / selectorLabels helpers
    ├── deployment.yaml # services and caches
    ├── statefulset.yaml# databases and queues
    ├── service.yaml    # a Service per workload
    ├── secret.yaml     # for `secret` blocks
    └── configmap.yaml  # for `config` blocks
```

## How resources map

| Infra | Chart output |
|-------|--------------|
| `service` | Deployment + Service |
| `database` | StatefulSet (with PVC via `volumeClaimTemplates`) + Service |
| `cache` | Deployment + Service |
| `queue` | StatefulSet + Service |
| `secret` | Secret (base64; empty placeholder values) |
| `config` | ConfigMap |

`storage`, `network`, `pipeline`, `environment` and `cluster` produce no Helm
output in this release.

## Values

Everything configurable lives in `values.yaml`, so you can override it at
install time with `--set` or a values file:

```bash
helm install my-release ./myapp-chart \
  --set service.api.replicas=5 \
  --set secret.db-creds.values.password=supersecret
```

Secrets are emitted with **empty** placeholder values — you are expected to
override them for real deployments (do not commit secrets).

## Ports

Multi-port services get the same named ports as the Kubernetes backend
(`tcp-<port>`, e.g. `tcp-5672`, `tcp-15672`).

## Validate the chart

```bash
helm lint --strict ./myapp-chart
helm template my-release ./myapp-chart   # renders to K8s YAML, no cluster needed
```

## Known limitations

- `env`, `labels`, `annotations`, `ingress`, `autoscale`, `network_policy` and
  `schedule` are not yet mapped into the Helm templates (image, replicas,
  ports, resources, health, storage are).
- Secrets use empty placeholder values; there is no automated secret-manager
  integration yet.
