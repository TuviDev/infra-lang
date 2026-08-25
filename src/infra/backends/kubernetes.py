# ruff: noqa: E501
"""Kubernetes YAML backend.

Maps Infra definitions to Kubernetes objects:
Service -> Deployment + Service [+ Ingress]
Database -> StatefulSet + Service + Secret + ConfigMap [+ CronJob for backups]
Cache -> Deployment + Service
Queue -> StatefulSet + Service + ConfigMap
Storage -> PersistentVolumeClaim (PVC/EFS) or a stub for object storage
Network -> NetworkPolicy
Secret -> Secret (Opaque)
Config -> ConfigMap
Environment -> Namespace + ResourceQuota
"""
# mypy: disable-error-code="no-untyped-def,no-untyped-call,no-any-return,index,attr-defined,type-arg,misc,union-attr,assignment"

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from infra.backends._images import CACHE_IMAGES as _CACHE_IMAGES
from infra.backends._images import QUEUE_IMAGES as _QUEUE_IMAGES
from infra.backends.base import (
    Backend,
    BaseYAMLBackend,
    CompileContext,
    CompileResult,
    evaluate_expression,
    generated_header,
)
from infra.errors.exceptions import InfraCompileError
from infra.parser import ast_nodes as n

_DB_IMAGES = {
    "postgres": "postgres",
    "mysql": "mysql",
    "mongodb": "mongo",
    "redis": "redis",
    "mariadb": "mariadb",
}



class KubernetesBackend(Backend, BaseYAMLBackend):
    name = "kubernetes"
    description = "Kubernetes YAML manifests"
    file_extension = ".yaml"
    supports_multi_file = True

    def __init__(self, split: bool = False) -> None:
        self.split = split

    def get_version(self) -> str:
        return "1.28"

    # ------------------------------------------------------------------ #
    def compile(
        self,
        program: n.Program,
        *,
        cli_vars: Optional[Dict[str, str]] = None,
        **kwargs: Any,
    ) -> CompileResult:
        result = CompileResult(metadata={"kubernetes_version": self.get_version()})
        ns = None

        for stmt in program.statements:
            if isinstance(stmt, n.EnvironmentDef):
                ns = stmt.namespace or stmt.name
            elif isinstance(stmt, n.ClusterDef):
                continue
        namespace = ns or "default"
        ctx = CompileContext.from_program(
            program,
            symbol_table=type("S", (), {})(),
            target_namespace=namespace,
            cli_vars=cli_vars,
        )

        has_schedule = any(isinstance(s, n.ServiceDef) and s.schedule for s in program.statements)  # noqa: E501

        single = []
        for stmt in program.statements:
            objs = self._compile_definition(stmt, ctx)
            # emit schedule RBAC once if any service defines a schedule
            if isinstance(stmt, n.ServiceDef) and stmt.schedule and has_schedule:
                for rbac in self.schedule_rbac():
                    objs.append((stmt.name, rbac))
                has_schedule = False
            for name, manifest in objs:
                if self.split:
                    kind = manifest.get("kind", "resource").lower()
                    fname = f"{name}-{kind}.yaml"
                    result.files[fname] = (
                        generated_header("kubernetes") + self._to_yaml(manifest)
                    )
                else:
                    single.append(manifest)

        if not self.split and single:
            docs = self._to_yaml_multi(single)
            result.files["infra.yaml"] = generated_header("kubernetes") + docs + "\n"
        return result

    def _compile_definition(self, stmt: n.ASTNode, ctx: CompileContext):
        """Return a list of (filename, manifest-dict) for a definition."""
        out: List[Any] = []
        if isinstance(stmt, n.ServiceDef):
            for item in self._compile_service(stmt, ctx):
                out.append((stmt.name, item))
        elif isinstance(stmt, n.DatabaseDef):
            for item in self._compile_database(stmt, ctx):
                out.append((stmt.name, item))
        elif isinstance(stmt, n.CacheDef):
            for item in self._compile_cache(stmt, ctx):
                out.append((stmt.name, item))
        elif isinstance(stmt, n.QueueDef):
            for item in self._compile_queue(stmt, ctx):
                out.append((stmt.name, item))
        elif isinstance(stmt, n.StorageDef):
            for item in self._compile_storage(stmt, ctx):
                out.append((stmt.name, item))
        elif isinstance(stmt, n.NetworkDef):
            for item in self._compile_network(stmt, ctx):
                out.append((stmt.name, item))
        elif isinstance(stmt, n.SecretStoreDef):
            out.append((stmt.name, self._compile_secret_store(stmt)))
        elif isinstance(stmt, n.CustomResourceSpec):
            out.append((stmt.name, self._compile_custom_resource(stmt)))
        elif isinstance(stmt, n.NetworkPolicyDef):
            out.append((stmt.name, self._compile_network_policy_def(stmt)))
        elif isinstance(stmt, n.SecretDef):
            if stmt.store:
                store_defs = [
                    s
                    for s in ctx.program.statements
                    if isinstance(s, n.SecretStoreDef)
                ]
                store = next(
                    (s for s in store_defs if s.name == stmt.store), None
                )
                out.append((stmt.name, self._compile_external_secret(stmt, store)))
            else:
                out.append((stmt.name, self._compile_secret(stmt)))
        elif isinstance(stmt, n.ConfigDef):
            out.append((stmt.name, self._compile_config(stmt)))
        elif isinstance(stmt, n.EnvironmentDef):
            out.append((stmt.name, self._compile_environment(stmt)))
            if stmt.quotas:
                out.append((f"{stmt.name}-quota", self._compile_quota(stmt)))
        return out

    def _compile_quota(self, node: n.EnvironmentDef) -> Dict[str, Any]:
        """Generate a ResourceQuota for an environment namespace."""
        ns_name = node.namespace or node.name
        hard: Dict[str, str] = {}
        if node.quotas.max_cpu:
            hard["requests.cpu"] = node.quotas.max_cpu.to_kubernetes()
            hard["limits.cpu"] = node.quotas.max_cpu.to_kubernetes()
        if node.quotas.max_memory:
            hard["requests.memory"] = node.quotas.max_memory.to_kubernetes()
            hard["limits.memory"] = node.quotas.max_memory.to_kubernetes()
        if node.quotas.max_pods is not None:
            hard["pods"] = str(node.quotas.max_pods)
        return {
            "apiVersion": "v1",
            "kind": "ResourceQuota",
            "metadata": {
                "name": f"{ns_name}-quota",
                "namespace": ns_name,
                "labels": {"app.kubernetes.io/managed-by": "infra-lang"},
            },
            "spec": {"hard": hard},
        }

    # -- Service ------------------------------------------------------- #
    def compile_service(self, node: n.ServiceDef) -> str:
        ctx = CompileContext(
            program=n.Program(),
            symbol_table=type("S", (), {})(),
            target_namespace="default",
        )
        return "\n".join(self._to_yaml(m) for m in self._compile_service(node, ctx))

    @staticmethod
    def _labels(name: str) -> Dict[str, str]:
        """Standard Kubernetes labels applied to every generated resource."""
        return {
            "app.kubernetes.io/name": name,
            "app.kubernetes.io/managed-by": "infra-lang",
        }

    def _ensure_service_port_names(self, ports: List[Dict[str, Any]]) -> None:
        """Mutate ``ports`` in place, adding stable unique names when >1 port.

        Kubernetes requires a ``name`` on every port when a Service exposes
        more than one port. Names are derived as ``<proto>-<port>`` (e.g.
        ``tcp-5672``); on collision an index suffix is appended (``tcp-80-1``).
        Single-port Services are left unchanged (name is optional there).
        """
        if len(ports) <= 1:
            return
        used: set[str] = set()
        for i, p in enumerate(ports):
            proto = (p.get("protocol") or "tcp").lower()
            eff = p.get("port") or p.get("targetPort") or 80
            base = f"{proto}-{eff}"
            name = base if base not in used else f"{base}-{i}"
            used.add(name)
            p["name"] = name

    def _compile_service(self, node: n.ServiceDef, ctx: CompileContext):
        image = self._resolve_image(node, ctx)
        if image is None and node.build is not None:
            image = "built-from-dockerfile"
        if image is None:
            raise InfraCompileError(
                f"Service '{node.name}' has neither image nor build",
                backend=self.name,
                node=node,
            )

        # resolve template-string / identifier replicas, env, etc.
        node = self._resolve_exprs(node, ctx)

        labels = dict(node.labels)
        labels["app.kubernetes.io/name"] = node.name
        labels["app.kubernetes.io/managed-by"] = "infra-lang"

        container: Dict[str, Any] = {
            "name": node.name,
            "image": image,
        }
        if node.command:
            container["command"] = list(node.command)
        if node.args:
            container["args"] = list(node.args)
        if node.ports:
            container["ports"] = [
                {"containerPort": p.target or p.host, "name": f"port-{i}"}
                for i, p in enumerate(node.ports)
            ]
        if node.env:
            container["env"] = [self._env_entry(e) for e in node.env]
        if node.resources:
            container["resources"] = self._resources(node.resources, ctx)
        if node.health:
            container["livenessProbe"] = self._probe(node.health)
            container["readinessProbe"] = self._probe(node.health)
        if node.security:
            container["securityContext"] = self._security(node.security)
        if node.lifecycle:
            container["lifecycle"] = self._lifecycle(node.lifecycle)
        if node.volumes:
            container["volumeMounts"] = [
                {"name": v.name, "mountPath": v.mount_path or "/data"}
                for v in node.volumes
            ]

        deployment = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": node.name,
                "labels": labels,
                "annotations": dict(node.annotations) or None,
            },
            "spec": {
                "replicas": int(node.replicas or 1),
                "selector": {"matchLabels": {"app.kubernetes.io/name": node.name}},
                "template": {
                    "metadata": {"labels": {"app.kubernetes.io/name": node.name}},
                    "spec": {"containers": [container]},
                },
            },
        }
        init_containers = self._dependency_init_containers(node, ctx)
        if init_containers:
            deployment["spec"]["template"]["spec"]["initContainers"] = init_containers
        if node.strategy and node.strategy.type == "recreate":
            deployment["spec"]["strategy"] = {"type": "Recreate"}
        elif node.strategy and node.strategy.type in (
            "rolling",
            "blue_green",
            "canary",
        ):
            deployment["spec"]["strategy"] = {"type": "RollingUpdate"}
        if node.volumes:
            deployment["spec"]["template"]["spec"]["volumes"] = [
                (
                    {"name": v.name, "persistentVolumeClaim": {"claimName": v.claim}}
                    if v.claim
                    else {"name": v.name, "emptyDir": {}}
                )
                for v in node.volumes
            ]

        if node.topology:
            deployment = self._apply_topology(deployment, node.topology)
        if node.affinity:
            deployment = self._apply_affinity(deployment, node.affinity)
        manifests = [self._clean_none(deployment)]

        service_type = "LoadBalancer" if node.expose else "ClusterIP"
        ports = [
            {
                "port": p.host or p.target or 80,
                "targetPort": p.target or p.host or 80,
                **({"protocol": p.protocol} if p.protocol else {}),
            }
            for p in node.ports
        ] or [{"port": 80, "targetPort": 80}]
        self._ensure_service_port_names(ports)
        svc = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": node.name, "labels": labels},
            "spec": {
                "type": service_type,
                "selector": {"app.kubernetes.io/name": node.name},
                "ports": ports,
            },
        }
        manifests.append(self._clean_none(svc))

        if node.ingress:
            manifests.append(self._clean_none(self._ingress(node)))
        if node.schedule:
            manifests.extend(self._compile_schedule(node))
        if node.autoscale:
            manifests.append(self._clean_none(self._compile_autoscale(node)))
        if node.disruption:
            manifests.append(self._clean_none(self._compile_disruption(node)))
        if node.network_policy:
            manifests.append(self._clean_none(self._compile_network_policy(node)))
        return manifests

    def _dependency_init_containers(
        self, node: n.ServiceDef, ctx: CompileContext
    ) -> List[Dict[str, Any]]:
        """One ``wait-for-<dep>`` init container per declared dependency.

        The init loop blocks the pod until TCP ``<dep>:<port>`` accepts a
        connection, giving deterministic start-up ordering for
        ``depends_on`` without relying on crash-loop retries (v0.4.5).
        Targets that are not network-reachable (or undeclared — the
        validator reports those via DEPENDENCY_NOT_FOUND) are skipped.
        """
        defs: Dict[str, n.ASTNode] = {}
        for stmt in ctx.program.statements:
            name = getattr(stmt, "name", "")
            if name:
                defs.setdefault(name, stmt)

        containers: List[Dict[str, Any]] = []
        for dep in node.dependencies:
            target = defs.get(dep)
            port: Optional[int]
            if isinstance(target, n.ServiceDef):
                # mirror the Service port mapping: host or target or 80
                first = target.ports[0] if target.ports else None
                port = (first.host or first.target or 80) if first else 80
            elif isinstance(target, n.DatabaseDef):
                port = 5432
            elif isinstance(target, n.CacheDef):
                port = 6379
            elif isinstance(target, n.QueueDef):
                port = 5672
            else:
                continue
            containers.append(
                {
                    "name": f"wait-for-{dep}",
                    "image": "busybox:1.36",
                    "command": [
                        "sh",
                        "-c",
                        f"until nc -z {dep} {port}; do "
                        f'echo "waiting for {dep}:{port}"; sleep 2; done',
                    ],
                }
            )
        return containers

    def _compile_network_policy(self, node: n.ServiceDef) -> Dict[str, Any]:
        """Generate a per-service NetworkPolicy."""
        np_: Dict[str, Any] = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": f"{node.name}-network-policy",
                "labels": {"app.kubernetes.io/managed-by": "infra-lang"},
            },
            "spec": {
                "podSelector": {"matchLabels": {"app.kubernetes.io/name": node.name}},
            },
        }
        ingress = []
        if node.network_policy.allow_from:
            ingress.append({
                "from": [
                    {"podSelector": {"matchLabels": {"app.kubernetes.io/name": s}}}
                    if s != "*"
                    else {}
                    for s in node.network_policy.allow_from
                ]
            })
        if node.network_policy.deny_from:
            # wildcard deny_from means no ingress allowed
            if "*" in node.network_policy.deny_from:
                ingress = []
        if ingress:
            np_["spec"]["ingress"] = ingress
        egress = []
        if node.network_policy.allow_egress:
            egress.append({
                "to": [
                    {"podSelector": {"matchLabels": {"app.kubernetes.io/name": s}}}
                    if s != "*"
                    else {}
                    for s in node.network_policy.allow_egress
                ]
            })
            np_["spec"]["egress"] = egress
        return np_

    def _compile_network_policy_def(
        self, node: n.NetworkPolicyDef
    ) -> Dict[str, Any]:
        """K8s ``NetworkPolicy`` from a top-level ``network_policy`` (v0.5.1).

        Mapping: ``target`` selects pods via the standard
        ``app.kubernetes.io/name`` label; each ``allow_ingress`` entry yields
        an ingress peer, each ``allow_egress`` entry an egress peer;
        ``block_all_ingress`` with an empty allow-list renders
        ``ingress: []`` (deny-all inbound). With no ingress rules at all the
        ``ingress`` key is omitted, leaving inbound traffic unrestricted —
        same for outbound when ``allow_egress`` is empty.
        """

        def _peers(names: Tuple[str, ...]) -> List[Dict[str, Any]]:
            return [
                {"podSelector": {"matchLabels": {"app.kubernetes.io/name": s}}}
                for s in names
            ]

        target = node.target or node.name
        spec: Dict[str, Any] = {
            "podSelector": {"matchLabels": {"app.kubernetes.io/name": target}}
        }
        policy_types: List[str] = []
        if node.block_all_ingress and not node.allow_ingress:
            spec["ingress"] = []  # empty rule set == deny-all inbound
            policy_types.append("Ingress")
        elif node.allow_ingress:
            spec["ingress"] = [{"from": _peers(node.allow_ingress)}]
            policy_types.append("Ingress")
        if node.allow_egress:
            spec["egress"] = [{"to": _peers(node.allow_egress)}]
            policy_types.append("Egress")
        spec["policyTypes"] = policy_types
        return {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": node.name, "labels": self._labels(node.name)},
            "spec": spec,
        }

    @staticmethod
    def _apply_topology(deployment: Dict[str, Any], topo: n.TopologySpec) -> Dict[str, Any]:
        """Add TopologySpreadConstraints to a Deployment template."""
        label_key = "topology.kubernetes.io/zone" if topo.spread_by == "zone" else "kubernetes.io/hostname"
        spec = deployment["spec"]["template"]["spec"]
        constraints = [{
            "maxSkew": topo.max_skew,
            "topologyKey": label_key,
            "whenUnsatisfiable": "DoNotSchedule",
            "labelSelector": {
                "matchLabels": deployment["spec"]["selector"].get("matchLabels", {}),
            },
        }]
        spec["topologySpreadConstraints"] = constraints
        return deployment

    @staticmethod
    def _apply_affinity(deployment: Dict[str, Any], aff: n.AffinitySpec) -> Dict[str, Any]:
        """Add podAffinity / podAntiAffinity preferences to a Deployment template."""
        spec = deployment["spec"]["template"]["spec"]

        def _preference(name: str) -> Dict[str, Any]:
            return {
                "weight": 100,
                "podAffinityTerm": {
                    "labelSelector": {
                        "matchLabels": {"app.kubernetes.io/name": name},
                    },
                    "topologyKey": "kubernetes.io/hostname",
                },
            }

        affinity: Dict[str, Any] = {}
        if aff.prefer_same:
            affinity["podAffinity"] = {
                "preferredDuringSchedulingIgnoredDuringExecution": [
                    _preference(n) for n in aff.prefer_same
                ]
            }
        if aff.avoid_same:
            affinity["podAntiAffinity"] = {
                "preferredDuringSchedulingIgnoredDuringExecution": [
                    _preference(n) for n in aff.avoid_same
                ]
            }
        spec["affinity"] = affinity
        return deployment

    def _compile_autoscale(self, node: n.ServiceDef) -> Dict[str, Any]:
        """Generate a HorizontalPodAutoscaler for a service."""
        hpa: Dict[str, Any] = {
            "apiVersion": "autoscaling/v2",
            "kind": "HorizontalPodAutoscaler",
            "metadata": {
                "name": f"hpa-{node.name}",
                "labels": {"app.kubernetes.io/managed-by": "infra-lang"},
            },
            "spec": {
                "scaleTargetRef": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": node.name,
                },
                "minReplicas": node.autoscale.min_replicas,
                "maxReplicas": node.autoscale.max_replicas,
                "metrics": [{
                    "type": "Resource",
                    "resource": {
                        "name": "cpu",
                        "target": {
                            "type": "Utilization",
                            "averageUtilization": node.autoscale.target_cpu,
                        },
                    },
                }],
            },
        }
        if node.autoscale.target_memory is not None:
            hpa["spec"]["metrics"].append({
                "type": "Resource",
                "resource": {
                    "name": "memory",
                    "target": {
                        "type": "Utilization",
                        "averageUtilization": node.autoscale.target_memory,
                    },
                },
            })
        return hpa

    def _compile_disruption(self, node: n.ServiceDef) -> Dict[str, Any]:
        """Generate a PodDisruptionBudget for a service."""
        pdb: Dict[str, Any] = {
            "apiVersion": "policy/v1",
            "kind": "PodDisruptionBudget",
            "metadata": {
                "name": f"pdb-{node.name}",
                "labels": {"app.kubernetes.io/managed-by": "infra-lang"},
            },
            "spec": {"selector": {"matchLabels": {"app": node.name}}},
        }
        if node.disruption.min_available is not None:
            pdb["spec"]["minAvailable"] = self._pdb_value(
                node.disruption.min_available
            )
        elif node.disruption.max_unavailable is not None:
            pdb["spec"]["maxUnavailable"] = self._pdb_value(
                node.disruption.max_unavailable
            )
        return pdb

    @staticmethod
    def _pdb_value(v) -> Any:
        """Render a PDB min/max available value, handling percentages."""
        if isinstance(v, n.Percentage):
            return f"{v.value:g}%"
        return v

    def _compile_schedule(self, node: n.ServiceDef) -> list[Dict[str, Any]]:
        """Generate CronJobs (scale-on-schedule) and an HPA for a service."""
        out: list[Dict[str, Any]] = []
        schedule = node.schedule
        for idx, slot in enumerate(schedule.slots):
            if not slot.cron:
                continue
            cj = {
                "apiVersion": "batch/v1",
                "kind": "CronJob",
                "metadata": {
                    "name": f"scale-{node.name}-{idx}",
                    "labels": {
                        "app.kubernetes.io/managed-by": "infra-lang",
                        "infra.lang/schedule": "true",
                    },
                },
                "spec": {
                    "schedule": slot.cron,
                    "successfulJobsHistoryLimit": 3,
                    "failedJobsHistoryLimit": 1,
                    "jobTemplate": {
                        "spec": {
                            "template": {
                                "spec": {
                                    "serviceAccountName": "infra-scheduler",
                                    "restartPolicy": "OnFailure",
                                    "containers": [{
                                        "name": "scaler",
                                        "image": "bitnami/kubectl:latest",
                                        "command": [
                                            "kubectl", "scale",
                                            f"deployment/{node.name}",
                                            f"--replicas={slot.config.replicas}",
                                        ],
                                        "resources": {
                                            "requests": {"cpu": "50m", "memory": "32Mi"},  # noqa: E501
                                            "limits": {"cpu": "100m", "memory": "64Mi"},
                                        },
                                    }],
                                }
                            }
                        }
                    },
                },
            }
            out.append(self._clean_none(cj))

        # HPA — bounds from default (min) and peak slot (max)
        if schedule.slots:
            min_replicas = schedule.default.replicas if schedule.default else 1
            max_replicas = max(slot.config.replicas for slot in schedule.slots)
            hpa = {
                "apiVersion": "autoscaling/v2",
                "kind": "HorizontalPodAutoscaler",
                "metadata": {"name": f"hpa-{node.name}"},
                "spec": {
                    "scaleTargetRef": {
                        "apiVersion": "apps/v1",
                        "kind": "Deployment",
                        "name": node.name,
                    },
                    "minReplicas": min_replicas,
                    "maxReplicas": max_replicas,
                    "metrics": [{
                        "type": "Resource",
                        "resource": {
                            "name": "cpu",
                            "target": {"type": "Utilization", "averageUtilization": 70},
                        },
                    }],
                },
            }
            out.append(self._clean_none(hpa))
        return out

    @staticmethod
    def schedule_rbac() -> list[Dict[str, Any]]:
        """RBAC resources (ServiceAccount + ClusterRole + Binding) for scaling."""
        return [
            {
                "apiVersion": "v1",
                "kind": "ServiceAccount",
                "metadata": {"name": "infra-scheduler"},
            },
            {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "ClusterRole",
                "metadata": {"name": "infra-scheduler"},
                "rules": [{
                    "apiGroups": ["apps"],
                    "resources": ["deployments", "deployments/scale"],
                    "verbs": ["get", "update", "patch"],
                }],
            },
            {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "ClusterRoleBinding",
                "metadata": {"name": "infra-scheduler"},
                "roleRef": {
                    "apiGroup": "rbac.authorization.k8s.io",
                    "kind": "ClusterRole",
                    "name": "infra-scheduler",
                },
                "subjects": [{
                    "kind": "ServiceAccount",
                    "name": "infra-scheduler",
                    "namespace": "default",
                }],
            },
        ]

    def _resolve_image(self, node: n.ServiceDef, ctx: CompileContext):
        """Return the resolved image string for a service (handles templates/identifiers)."""  # noqa: E501
        image = node.image
        if image is None:
            return None
        if isinstance(image, str):
            return image
        if isinstance(image, n.TemplateString):
            return evaluate_expression(image, ctx)
        if isinstance(image, n.Identifier):
            resolved = evaluate_expression(image, ctx)
            return resolved if isinstance(resolved, str) else None
        if isinstance(image, n.Literal):
            return str(image.value)
        return str(image)

    def _resolve_exprs(self, node: n.ServiceDef, ctx: CompileContext) -> n.ServiceDef:
        """Resolve expression-valued fields (env values, resources) in place."""
        if not node.env:
            return node
        env = []
        for e in node.env:
            if e.value is not None and not isinstance(e.value, n.Literal):
                val = evaluate_expression(e.value, ctx)
                env.append(
                    n.EnvEntry(
                        name=e.name,
                        value=n.Literal(value=val) if val is not None else None,
                    )
                )
            else:
                env.append(e)
        from dataclasses import replace

        return replace(node, env=tuple(env))

    def _env_entry(self, e: n.EnvEntry) -> Dict[str, Any]:
        value_from = None
        if e.from_secret:
            name, _, key = e.from_secret.partition(".")
            value_from = {"secretKeyRef": {"name": name, "key": key or e.name}}
        elif e.from_config:
            name, _, key = e.from_config.partition(".")
            value_from = {"configMapKeyRef": {"name": name, "key": key or e.name}}
        elif e.from_field:
            value_from = {"fieldRef": {"fieldPath": e.from_field}}
        elif e.from_env:
            value_from = {"fieldRef": {"fieldPath": e.from_env}}
        out: Dict[str, Any] = {"name": e.name}
        if value_from:
            out["valueFrom"] = value_from
        elif e.value is not None:
            out["value"] = str(_lit(e.value))
        return out

    def _resources(
        self, r: n.ResourcesSpec, ctx: Optional[CompileContext] = None
    ) -> Dict[str, Any]:
        out = {}
        if r.requests:
            out["requests"] = self._resource_map(r.requests, ctx)
        if r.limits:
            out["limits"] = self._resource_map(r.limits, ctx)
        return out

    def _eval_resource(self, val, ctx: CompileContext):
        """Resolve a resource value that may be a ResourceValue or an Identifier."""
        if isinstance(val, n.ResourceValue):
            return val.to_kubernetes()
        if isinstance(val, n.Identifier):
            resolved = evaluate_expression(val, ctx)
            if isinstance(resolved, n.ResourceValue):
                return resolved.to_kubernetes()
            if isinstance(resolved, str):
                return resolved
        return str(val) if val is not None else None

    def _resource_map(
        self, m: n.ResourceMap, ctx: Optional[CompileContext] = None
    ) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if m.cpu:
            out["cpu"] = (
                self._eval_resource(m.cpu, ctx) if ctx else m.cpu.to_kubernetes()
            )
        if m.memory:
            out["memory"] = (
                self._eval_resource(m.memory, ctx) if ctx else m.memory.to_kubernetes()
            )
        return out

    def _probe(self, h: n.HealthSpec) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if h.kind == "http":
            out["httpGet"] = {"path": h.path or "/", "port": h.port or 80}
        elif h.kind == "tcp":
            out["tcpSocket"] = {"port": h.port or 80}
        elif h.kind == "exec":
            out["exec"] = {"command": list(h.command)}
        elif h.kind == "grpc":
            out["grpc"] = {"port": h.port or 80}
        if h.initial_delay:
            out["initialDelaySeconds"] = int(h.initial_delay.to_seconds())
        if h.interval:
            out["periodSeconds"] = int(h.interval.to_seconds())
        if h.timeout:
            out["timeoutSeconds"] = int(h.timeout.to_seconds())
        if h.retries:
            out["failureThreshold"] = h.retries
        return out

    def _security(self, s: n.SecuritySpec) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if s.user is not None:
            out["runAsUser"] = s.user
        if s.group is not None:
            out["runAsGroup"] = s.group
        if s.capabilities:
            out["capabilities"] = {"drop": ["ALL"], "add": list(s.capabilities)}
        if s.read_only_root_filesystem:
            out["readOnlyRootFilesystem"] = True
        return out

    def _lifecycle(self, lc: n.LifecycleSpec) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if lc.pre_stop and lc.pre_stop.command:
            out["preStop"] = {"exec": {"command": list(lc.pre_stop.command)}}
        if lc.post_start and lc.post_start.command:
            out["postStart"] = {"exec": {"command": list(lc.post_start.command)}}
        return out

    def _ingress(self, node: n.ServiceDef) -> Dict[str, Any]:
        host = node.ingress.host or node.ingress.domain or node.name
        ann = {}
        if node.ingress.rate_limit:
            ann["nginx.ingress.kubernetes.io/limit-rps"] = str(
                node.ingress.rate_limit.rps or ""
            )
        if node.ingress.cors:
            ann["nginx.ingress.kubernetes.io/enable-cors"] = "true"
        meta = {"name": f"{node.name}-ingress", "annotations": ann or None}
        if node.ingress.tls:
            meta["annotations"] = {
                "cert-manager.io/cluster-issuer": "letsencrypt",
            }
        spec: Dict[str, Any] = {
            "rules": [
                {
                    "host": host,
                    "http": {
                        "paths": [
                            {
                                "path": "/",
                                "pathType": "Prefix",
                                "backend": {
                                    "service": {
                                        "name": node.name,
                                        "port": {
                                            "number": (
                                                node.ports[0].host
                                                or node.ports[0].target
                                                if node.ports
                                                else 80
                                            )
                                        },
                                    }
                                },
                            }
                        ]
                    },
                }
            ]
        }
        if node.ingress.tls:
            spec["tls"] = [{"hosts": [host], "secretName": f"{node.name}-tls"}]
        return {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": meta,
            "spec": spec,
        }

    # -- Database ------------------------------------------------------ #
    def compile_database(self, node: n.DatabaseDef) -> str:
        ctx = CompileContext(
            program=n.Program(),
            symbol_table=type("S", (), {})(),
            target_namespace="default",
        )
        return "\n".join(self._to_yaml(m) for m in self._compile_database(node, ctx))

    def _compile_database(self, node: n.DatabaseDef, ctx: CompileContext):
        image = _DB_IMAGES.get(node.type, "postgres")
        version = node.version or ""
        image_tag = f"{image}:{version}" if version else f"{image}:latest"
        labels = self._labels(node.name)
        sts = {
            "apiVersion": "apps/v1",
            "kind": "StatefulSet",
            "metadata": {"name": node.name, "labels": labels},
            "spec": {
                "serviceName": node.name,
                "replicas": int(node.replicas or 1),
                "selector": {"matchLabels": {"app.kubernetes.io/name": node.name}},
                "template": {
                    "metadata": {"labels": {"app.kubernetes.io/name": node.name}},
                    "spec": {
                        "containers": [
                            {
                                "name": node.name,
                                "image": image_tag,
                                "env": self._db_env(node),
                            }
                        ]
                    },
                },
            },
        }
        storage_str = None
        if node.size:
            storage_str = node.size.to_kubernetes()
        elif node.storage:
            storage_str = (
                node.storage.to_kubernetes()
                if isinstance(node.storage, n.ResourceValue)
                else str(node.storage)
            )
        if storage_str:
            sts["spec"]["volumeClaimTemplates"] = [
                {
                    "metadata": {"name": "data"},
                    "spec": {
                        "accessModes": ["ReadWriteOnce"],
                        "resources": {"requests": {"storage": storage_str}},
                    },
                }
            ]
        svc = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": node.name, "labels": labels},
            "spec": {
                "selector": {"app.kubernetes.io/name": node.name},
                "ports": [{"port": 5432, "targetPort": 5432}],
            },
        }
        manifests = [self._clean_none(sts), self._clean_none(svc)]
        if node.users:
            data = {u.name: u.password or "change-me" for u in node.users}
            secret = {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": f"{node.name}-credentials"},
                "stringData": data,
                "type": "Opaque",
            }
            manifests.append(self._clean_none(secret))
        return manifests

    def _db_env(self, node: n.DatabaseDef) -> List[Dict[str, Any]]:
        env: List[Dict[str, Any]] = [
            {"name": "POSTGRES_DB", "value": node.name},
            {"name": "POSTGRES_USER", "value": node.name},
        ]
        if node.users:
            env.append(
                {
                    "name": "POSTGRES_PASSWORD",
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": f"{node.name}-credentials",
                            "key": node.users[0].name,
                        }
                    },
                }
            )
        return env

    # -- Cache --------------------------------------------------------- #
    def _compile_cache(self, node: n.CacheDef, ctx: CompileContext):
        image = _CACHE_IMAGES.get(node.type, "redis")
        version = node.version or ("7" if node.type in ("redis", "valkey") else "1.6")
        labels = self._labels(node.name)
        cmd = None
        if node.type == "redis" and node.maxmemory:
            cmd = [
                "redis-server",
                "--maxmemory",
                node.maxmemory.to_kubernetes(),
                "--maxmemory-policy",
                node.policy or "allkeys-lru",
            ]
        dep = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": node.name, "labels": labels},
            "spec": {
                "replicas": int(node.replicas or 1),
                "selector": {"matchLabels": {"app.kubernetes.io/name": node.name}},
                "template": {
                    "metadata": {"labels": {"app.kubernetes.io/name": node.name}},
                    "spec": {
                        "containers": [
                            {
                                "name": node.name,
                                "image": f"{image}:{version}",
                                "command": cmd or None,
                            }
                        ]
                    },
                },
            },
        }
        svc = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": node.name, "labels": labels},
            "spec": {
                "selector": {"app.kubernetes.io/name": node.name},
                "ports": [{"port": 6379, "targetPort": 6379}],
            },
        }
        return [self._clean_none(dep), self._clean_none(svc)]

    # -- Queue --------------------------------------------------------- #
    def _compile_queue(self, node: n.QueueDef, ctx: CompileContext):
        image = _QUEUE_IMAGES.get(node.type, "rabbitmq:3-management")
        labels = self._labels(node.name)
        sts = {
            "apiVersion": "apps/v1",
            "kind": "StatefulSet",
            "metadata": {"name": node.name, "labels": labels},
            "spec": {
                "serviceName": node.name,
                "replicas": int(node.replicas or 1),
                "selector": {"matchLabels": {"app.kubernetes.io/name": node.name}},
                "template": {
                    "metadata": {"labels": {"app.kubernetes.io/name": node.name}},
                    "spec": {"containers": [{"name": node.name, "image": image}]},
                },
            },
        }
        ports = [
            {"port": 5672, "targetPort": 5672},
            {"port": 15672, "targetPort": 15672},
        ]
        self._ensure_service_port_names(ports)
        svc = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": node.name, "labels": labels},
            "spec": {
                "selector": {"app.kubernetes.io/name": node.name},
                "ports": ports,
            },
        }
        return [self._clean_none(sts), self._clean_none(svc)]

    # -- Storage ------------------------------------------------------- #
    def _compile_storage(self, node: n.StorageDef, ctx: CompileContext):
        labels = self._labels(node.name)
        if node.type == "s3":
            # object storage handled by a Secret placeholder for credentials
            return [
                self._clean_none(
                    {
                        "apiVersion": "v1",
                        "kind": "Secret",
                        "metadata": {"name": f"{node.name}-credentials"},
                        "stringData": {
                            "bucket": node.bucket or node.name,
                            "region": node.region or "",
                        },
                        "type": "Opaque",
                    }
                )
            ]
        pvc = {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": node.name, "labels": labels},
            "spec": {
                "accessModes": [node.access_mode or "ReadWriteOnce"],
                "resources": {
                    "requests": {
                        "storage": node.size.to_kubernetes() if node.size else "10Gi"
                    }
                },
            },
        }
        return [self._clean_none(pvc)]

    # -- Network ------------------------------------------------------- #
    def _compile_network(self, node: n.NetworkDef, ctx: CompileContext):
        labels = self._labels(node.name)
        rules = []
        if node.policy:
            for rule in node.policy.rules:
                spec: Dict[str, Any] = {}
                if rule.from_:
                    spec["from"] = [{"ipBlock": {"cidr": rule.from_}}]
                if rule.ports:
                    spec["ports"] = [{"port": p} for p in rule.ports]
                rules.append({"name": rule.name, "spec": spec})
        np = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": node.name, "labels": labels},
            "spec": {"podSelector": {}, "ingress": rules},
        }
        return [self._clean_none(np)]

    # -- Secret / Config ---------------------------------------------- #
    def _compile_secret(self, node: n.SecretDef) -> Dict[str, Any]:
        import base64

        data = {}
        for e in node.entries:
            # Kubernetes requires every value in `data:` to be base64. Plain
            # placeholders (from-env/from-vault) and empty values are encoded
            # too so `kubectl apply` always accepts the manifest.
            if e.value is not None:
                data[e.name] = base64.b64encode(e.value.encode()).decode()
            elif e.from_vault:
                data[e.name] = base64.b64encode(
                    f"from-vault:{e.from_vault}".encode()
                ).decode()
            elif e.from_env:
                data[e.name] = base64.b64encode(
                    f"from-env:{e.from_env}".encode()
                ).decode()
            else:
                data[e.name] = ""
        return {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": node.name, "labels": self._labels(node.name)},
            "data": data,
            "type": "Opaque",
        }

    def _compile_secret_store(self, node: n.SecretStoreDef) -> Dict[str, Any]:
        """ExternalSecret-Operator ``SecretStore`` for a ``secret_store`` (v0.5.0)."""
        provider_block: Dict[str, Any] = {}
        if node.provider == "vault":
            vault: Dict[str, Any] = {"server": node.address or "http://vault:8200"}
            if node.path:
                vault["path"] = node.path
            provider_block["vault"] = vault
        elif node.provider == "aws":
            aws: Dict[str, Any] = {"service": "SecretsManager"}
            if node.region:
                aws["region"] = node.region
            provider_block["aws"] = aws
        elif node.provider == "gcp":
            provider_block["gcpsm"] = {"projectID": node.project or "PROJECT_ID"}
        elif node.provider == "kubernetes":
            kube: Dict[str, Any] = {
                "server": node.address or "https://kubernetes.default"
            }
            if node.namespace:
                kube["remoteNamespace"] = node.namespace
            provider_block["kubernetes"] = kube
        else:
            # validator reports INVALID_STORE_PROVIDER; stay permissive here
            provider_block[node.provider or "generic"] = {}
        return {
            "apiVersion": "external-secrets.io/v1beta1",
            "kind": "SecretStore",
            "metadata": {"name": node.name, "labels": self._labels(node.name)},
            "spec": {"provider": provider_block},
        }

    def _compile_external_secret(
        self, node: n.SecretDef, store: Optional[n.SecretStoreDef]
    ) -> Dict[str, Any]:
        """``ExternalSecret`` bound to a ``SecretStore`` (v0.5.0).

        Mapping: ``remoteRef.key`` is the store ``path`` when set, else the
        secret's own name; ``property`` selects the JSON key (vault KV-v2 /
        AWS/GCP JSON payloads alike).
        """
        key = (store.path if store and store.path else None) or node.name
        data = [
            {
                "secretKey": e.name,
                "remoteRef": {"key": key, "property": e.key or e.name},
            }
            for e in node.entries
        ]
        return {
            "apiVersion": "external-secrets.io/v1beta1",
            "kind": "ExternalSecret",
            "metadata": {"name": node.name, "labels": self._labels(node.name)},
            "spec": {
                "secretStoreRef": {"name": node.store, "kind": "SecretStore"},
                "target": {"name": node.name},
                "data": data,
            },
        }

    def _compile_custom_resource(self, node: n.CustomResourceSpec) -> Dict[str, Any]:
        """Render a generic custom resource (CRD) manifest directly (v0.5.0).

        ``api_version``/``kind`` map onto the manifest's ``apiVersion``/
        ``kind``; every other property (``spec`` included) is passed through
        verbatim. When ``api_version`` is missing we default to ``v1``; when
        ``kind`` is missing we reuse the declaration's type label so the
        manifest stays loadable — the validator nudges the user with W010 /
        W011 in both cases.
        """
        manifest: Dict[str, Any] = {
            "apiVersion": node.api_version or "v1",
            "kind": node.kind or node.kind_name,
            "metadata": {"name": node.name, "labels": self._labels(node.name)},
        }
        for key, value in node.properties:
            if key in ("api_version", "kind"):
                continue
            rendered = self._custom_value(value)
            if key == "metadata" and isinstance(rendered, dict):
                merged = dict(manifest["metadata"])
                merged.update(rendered)
                rendered = merged
            manifest[key] = rendered
        return manifest

    def _custom_value(self, value: n.Expression) -> Any:
        """Literal-evaluate an expression into plain YAML-ready data."""
        if isinstance(value, n.Literal):
            return value.value
        if isinstance(value, n.Identifier):
            return value.name
        if isinstance(value, n.Map):
            out: Dict[str, Any] = {}
            for entry in value.entries:
                key = entry.key
                if isinstance(key, n.Literal):
                    k = str(key.value)
                elif isinstance(key, n.Identifier):
                    k = key.name
                else:
                    k = _lit(key)
                out[k] = self._custom_value(entry.value)
            # keep manifests valid when a value resolves to None (e.g. null)
            return self._clean_none(out)
        if isinstance(value, n.List):
            return [self._custom_value(item) for item in value.items]
        if isinstance(value, n.TemplateString):
            return "".join(
                p if isinstance(p, str) else str(self._custom_value(p))
                for p in value.parts
            )
        return _lit(value)

    def _compile_config(self, node: n.ConfigDef) -> Dict[str, Any]:
        data = {}
        for e in node.entries:
            if e.value is not None:
                data[e.name] = str(_lit(e.value))
            elif e.from_file:
                data[e.name] = e.from_file
        return {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": node.name, "labels": self._labels(node.name)},
            "data": data,
        }

    # -- Environment --------------------------------------------------- #
    def _compile_environment(self, node: n.EnvironmentDef) -> Dict[str, Any]:
        ns_name = node.namespace or node.name
        labels = dict(node.labels)
        labels["app.kubernetes.io/managed-by"] = "infra-lang"
        return {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": ns_name, "labels": labels},
        }


def _lit(v: Any) -> str:
    if isinstance(v, n.Literal):
        return str(v.value)
    return str(v)
