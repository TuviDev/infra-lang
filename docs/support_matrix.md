# Infra Lang — Support Matrix

## Runtime requirements

| Component | Version | Notes |
|-----------|---------|-------|
| Python | 3.11, 3.12, 3.13 | Tested |
| pip | 21.0+ | For installation |

## Kubernetes backend

| K8s version | Support | Notes |
|-------------|---------|-------|
| 1.27+ | Full | All 17 resources |
| 1.24–1.26 | Partial | PDB policy/v1 may differ |
| < 1.24 | Not supported | |

### Generated resources

| Resource | apiVersion | Since K8s |
|----------|-----------|-----------|
| Deployment | apps/v1 | 1.9 |
| Service | v1 | 1.0 |
| Ingress | networking.k8s.io/v1 | 1.19 |
| StatefulSet | apps/v1 | 1.9 |
| PersistentVolumeClaim | v1 | 1.0 |
| Secret | v1 | 1.0 |
| ConfigMap | v1 | 1.0 |
| CronJob | batch/v1 | 1.21 |
| HorizontalPodAutoscaler | autoscaling/v2 | 1.23 |
| PodDisruptionBudget | policy/v1 | 1.21 |
| NetworkPolicy | networking.k8s.io/v1 | 1.7 |
| ResourceQuota | v1 | 1.0 |
| Namespace | v1 | 1.0 |
| ServiceAccount | v1 | 1.0 |
| ClusterRole | rbac.authorization.k8s.io/v1 | 1.8 |
| ClusterRoleBinding | rbac.authorization.k8s.io/v1 | 1.8 |
| TopologySpreadConstraints | (spec field) | 1.19 |

## Helm backend

| Resource | Template file | Notes |
|----------|---------------|-------|
| `service` | `deployment.yaml`, `service.yaml` | image/replicas/ports/resources/health via `values.yaml` |
| `database` | `statefulset.yaml`, `service.yaml` | StatefulSet + PVC via `volumeClaimTemplates` |
| `cache` | `deployment.yaml`, `service.yaml` | Deployment |
| `queue` | `statefulset.yaml`, `service.yaml` | StatefulSet |
| `secret` | `secret.yaml` | base64-encoded; empty placeholders in `values.yaml` |
| `config` | `configmap.yaml` | data from `values.yaml` |

Compile any example:

```bash
infra compile examples/02_web_app.infra --target helm
```

See [docs/backends/helm.md](backends/helm.md).

## Feature × backend support (verified against code)

Legend: ✅ supported · ⚠️ partial / backend-specific · ❌ ignored or not emitted

| Structure | Kubernetes | Compose | Terraform | GitHub Actions | Helm |
|-----------|-----------|---------|-----------|----------------|------|
| `service` | ✅ | ✅ | ❌ | ❌ | ✅ |
| `database` | ✅ | ✅ | ✅ | ❌ | ✅ |
| `cache` | ✅ | ✅ | ❌ | ❌ | ✅ |
| `queue` | ✅ | ✅ | ❌ (no SQS) | ❌ | ✅ |
| `storage` | ✅ | ⚠️ (only `minio`) | ✅ (S3/GCS/Azure) | ❌ | ❌ |
| `network` | ✅ | ❌ | ✅ (VPC) | ❌ | ❌ |
| `secret` | ✅ | ✅ | ✅ | ❌ | ✅ |
| `config` | ✅ | ✅ | ❌ | ❌ | ✅ |
| `pipeline` | ❌ | ❌ | ❌ | ✅ | ❌ |
| `environment` | ✅ (Namespace + quota) | ✅ | ❌ | ❌ | ❌ |
| `cluster` | ❌ | ❌ | ✅ | ❌ | ❌ |

> **Notes**
> - `pipeline` is GitHub-Actions-only; `cluster` is Terraform-only.
> - `service`/`cache`/`config` are accepted by the Terraform parser but produce
>   no Terraform resources (structural stub only).
> - All other backends ignore constructs they do not support and emit only the
>   resources they understand.

## Docker Compose backend

| Compose version | Support |
|-----------------|---------|
| v2 (Compose v2.x) | Full |
| v3 YAML format | Full |
| v1 (deprecated) | Not supported |

## Terraform backend

| Provider | Support level | Resources |
|----------|--------------|-----------|
| AWS | Good | EKS, RDS, S3, VPC, SQS |
| GCP | Basic | GKE, CloudSQL, GCS |
| Azure | Basic | AKS, PostgreSQL, Storage |

## GitHub Actions backend

| Feature | Support |
|---------|---------|
| push/PR triggers | Full |
| schedule (cron) | Full |
| workflow_dispatch | Full |
| matrix strategy | Full |
| concurrency | Full |
| artifacts | Full |
| caching | Full |
| reusable workflows | Not supported |
| workflow_call | Not supported |

## Operating systems

| OS | Support |
|----|---------|
| Linux | Full |
| macOS | Full |
| Windows | Untested |

## Known limitations

- Terraform output is structural only (no modules, data sources, remote state)
- GitHub Actions reusable workflows not supported
- No live cluster validation (`kubectl apply`) — schema validation via kubeconform only
- LSP: diagnostics, completion, hover, symbols, definition, references,
  formatting and quick-fixes are available; no rename, no cross-file navigation
- Terraform backend does not emit Kubernetes workloads (`service`/`cache` are
  supported in Compose/K8s only; `cluster`/`database`/`network`/`secret`/`queue`
  are emitted)
