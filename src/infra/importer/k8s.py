"""Kubernetes YAML -> Infra Lang reverse compiler.

Reads Kubernetes manifests (Deployments, StatefulSets, Services, Secrets,
ConfigMaps, Ingresses), groups related resources (a Service matching a
Deployment's pod labels is merged into the same ``service`` block), and emits
readable Infra source.

Multi-document YAML (``---``) is supported, as well as whole directories of
manifests. The output is intentionally a *best-effort* round-trip: it preserves
the high-level intent (image, replicas, ports, env, resources, probes, volumes,
ingress, secrets, configmaps) rather than every Kubernetes field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

#: Kubernetes resource kinds the importer knows how to map.
_SUPPORTED_KINDS = {
    "Deployment",
    "StatefulSet",
    "Service",
    "Secret",
    "ConfigMap",
    "Ingress",
}

#: Images that map to a `database` block.
_DB_IMAGE_HINTS = ("postgres", "mysql", "mongo", "postgresql")
#: Images that map to a `cache` block.
_CACHE_IMAGE_HINTS = ("redis",)

#: Infra identifiers may contain [a-zA-Z0-9_-] but must start alpha/_.
_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")
_INVALID_IDENT_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


class InfraImportError(Exception):
    """Raised when Kubernetes manifests cannot be imported."""


# --------------------------------------------------------------------------- #
# Intermediate representation
# --------------------------------------------------------------------------- #


@dataclass
class Container:
    """A single container extracted from a workload spec."""

    name: str = ""
    image: Optional[str] = None
    ports: List[Dict[str, Any]] = field(default_factory=list)
    env: List[Dict[str, Any]] = field(default_factory=list)
    resources: Dict[str, Any] = field(default_factory=dict)
    liveness: Optional[Dict[str, Any]] = None
    readiness: Optional[Dict[str, Any]] = None
    startup: Optional[Dict[str, Any]] = None
    volume_mounts: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class Workload:
    """A Deployment or StatefulSet."""

    kind: str  # "Deployment" | "StatefulSet"
    name: str
    namespace: Optional[str] = None
    containers: List[Container] = field(default_factory=list)
    replicas: Optional[int] = None
    pod_labels: Dict[str, str] = field(default_factory=dict)
    selector: Dict[str, str] = field(default_factory=dict)
    volumes: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class K8sService:
    """A Service manifest."""

    name: str
    namespace: Optional[str] = None
    selector: Dict[str, str] = field(default_factory=dict)
    ports: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class IngressRes:
    """An Ingress manifest."""

    name: str
    namespace: Optional[str] = None
    hosts: List[str] = field(default_factory=list)
    backends: List[str] = field(default_factory=list)


@dataclass
class SimpleResource:
    """A Secret or ConfigMap (key/value data)."""

    kind: str  # "Secret" | "ConfigMap"
    name: str
    namespace: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    string_data: Dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# YAML helpers
# --------------------------------------------------------------------------- #


def _get(d: Any, path: str, default: Any = None) -> Any:
    """Walk a dotted path through a dict (e.g. ``spec.template.metadata.labels``)."""
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def sanitize_name(name: str) -> str:
    """Make a Kubernetes object name a valid Infra identifier."""
    if not name:
        return "resource"
    if name.startswith("-"):
        name = "res" + name
    name = _INVALID_IDENT_CHARS.sub("-", name)
    if not re.match(r"^[a-zA-Z_]", name):
        name = "res-" + name
    return name or "resource"


def _quote(value: Any) -> str:
    """Render a scalar as a quoted Infra string literal."""
    text = str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _render_resource_value(value: Any) -> str:
    """Pass through a Kubernetes quantity (e.g. ``100m``, ``256Mi``, ``1``)."""
    text = str(value).strip()
    return text or "0"


def _kind_and_name(doc: Dict[str, Any]) -> tuple[str, str]:
    kind = str(doc.get("kind", "") or "")
    name = str(_get(doc, "metadata.name", "") or "")
    return kind, name


def _image_hint(image: str) -> str:
    """Classify an image into database / cache / service."""
    low = image.lower()
    if any(h in low for h in _DB_IMAGE_HINTS):
        return "database"
    if any(h in low for h in _CACHE_IMAGE_HINTS):
        return "cache"
    return "service"


def _labels_subset(sub: Dict[str, Any], super_: Dict[str, Any]) -> bool:
    """True if every key in ``sub`` equals its value in ``super_``."""
    return all(super_.get(k) == v for k, v in (sub or {}).items())


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #


def _extract_containers(spec: Dict[str, Any]) -> List[Container]:
    containers: List[Container] = []
    for c in _get(spec, "template.spec.containers", []) or []:
        if not isinstance(c, dict):
            continue
        ports = [
            {
                "name": p.get("name"),
                "containerPort": p.get("containerPort"),
                "protocol": (p.get("protocol") or "TCP").upper(),
            }
            for p in (c.get("ports") or [])
            if isinstance(p, dict) and p.get("containerPort")
        ]
        containers.append(
            Container(
                name=str(c.get("name", "") or ""),
                image=c.get("image"),
                ports=ports,
                env=c.get("env") or [],
                resources=c.get("resources") or {},
                liveness=_probe_to_dict(c.get("livenessProbe")),
                readiness=_probe_to_dict(c.get("readinessProbe")),
                startup=_probe_to_dict(c.get("startupProbe")),
                volume_mounts=c.get("volumeMounts") or [],
            )
        )
    return containers


def _probe_to_dict(probe: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(probe, dict):
        return None
    kind, value = _probe_kind(probe)
    if kind is None:
        return None
    return {
        "kind": kind,
        "value": value,
        "initialDelaySeconds": probe.get("initialDelaySeconds"),
        "periodSeconds": probe.get("periodSeconds"),
        "timeoutSeconds": probe.get("timeoutSeconds"),
        "failureThreshold": probe.get("failureThreshold"),
    }


def _probe_kind(probe: Dict[str, Any]) -> tuple[Optional[str], Any]:
    if "httpGet" in probe and isinstance(probe["httpGet"], dict):
        return "http", probe["httpGet"]
    if "tcpSocket" in probe and isinstance(probe["tcpSocket"], dict):
        return "tcp", probe["tcpSocket"].get("port")
    if "exec" in probe and isinstance(probe["exec"], dict):
        return "exec", probe["exec"].get("command")
    if "grpc" in probe and isinstance(probe["grpc"], dict):
        return "grpc", probe["grpc"].get("port")
    return None, None


def _extract_pod_volumes(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for v in _get(spec, "template.spec.volumes", []) or []:
        if not isinstance(v, dict):
            continue
        entry: Dict[str, Any] = {"name": v.get("name")}
        for source in (
            "persistentVolumeClaim",
            "configMap",
            "secret",
            "emptyDir",
            "hostPath",
        ):
            if source in v and isinstance(v[source], dict):
                ref = v[source].get("claimName") or v[source].get("name")
                if source == "persistentVolumeClaim":
                    entry["claim"] = ref
                elif source == "hostPath":
                    entry["host_path"] = v[source].get("path")
                else:
                    entry["source"] = source
                    entry["source_name"] = ref
        out.append(entry)
    return out


def _extract_workload(doc: Dict[str, Any], kind: str) -> Workload:
    name = str(_get(doc, "metadata.name", "") or "")
    spec = doc.get("spec") or {}
    wl = Workload(
        kind=kind,
        name=name,
        namespace=_get(doc, "metadata.namespace"),
        containers=_extract_containers(spec),
        replicas=spec.get("replicas"),
        pod_labels=_get(spec, "template.metadata.labels", {}) or {},
        selector=_get(spec, "selector.matchLabels", {}) or {},
        volumes=_extract_pod_volumes(spec),
    )
    return wl


def _extract_service(doc: Dict[str, Any]) -> K8sService:
    spec = doc.get("spec") or {}
    ports = []
    for p in spec.get("ports") or []:
        if isinstance(p, dict) and p.get("port"):
            ports.append(
                {
                    "name": p.get("name"),
                    "port": p.get("port"),
                    "targetPort": p.get("targetPort"),
                    "protocol": (p.get("protocol") or "TCP").upper(),
                }
            )
    return K8sService(
        name=str(_get(doc, "metadata.name", "") or ""),
        namespace=_get(doc, "metadata.namespace"),
        selector=spec.get("selector") or {},
        ports=ports,
    )


def _extract_ingress(doc: Dict[str, Any]) -> IngressRes:
    spec = doc.get("spec") or {}
    hosts: List[str] = []
    backends: List[str] = []
    for rule in spec.get("rules") or []:
        if not isinstance(rule, dict):
            continue
        if rule.get("host"):
            hosts.append(str(rule["host"]))
        for path in _get(rule, "http.paths", []) or []:
            if not isinstance(path, dict):
                continue
            svc = _get(path, "backend.service", {}) or {}
            if svc.get("name"):
                backends.append(str(svc["name"]))
    return IngressRes(
        name=str(_get(doc, "metadata.name", "") or ""),
        namespace=_get(doc, "metadata.namespace"),
        hosts=hosts,
        backends=backends,
    )


def _extract_simple(doc: Dict[str, Any], kind: str) -> SimpleResource:
    return SimpleResource(
        kind=kind,
        name=str(_get(doc, "metadata.name", "") or ""),
        namespace=_get(doc, "metadata.namespace"),
        data=dict((doc.get("data") or {}) or {}),
        string_data=dict((doc.get("stringData") or {}) or {}),
    )


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _render_ports(ports: List[Dict[str, Any]]) -> List[str]:
    lines: List[str] = []
    for p in ports:
        port = p.get("port") or p.get("containerPort")
        if not port:
            continue
        protocol = (p.get("protocol") or "TCP").upper()
        if protocol and protocol != "TCP":
            lines.append(f"port {port} {{ protocol: {_quote(protocol)} }}")
        else:
            lines.append(f"port {port}")
    return lines


def _render_env(env: List[Dict[str, Any]]) -> List[str]:
    lines: List[str] = []
    for item in env:
        if not isinstance(item, dict):
            continue
        key = item.get("name")
        if not key or not _IDENT_RE.match(str(key)):
            continue
        if "value" in item:
            lines.append(f"{key}: {_quote(item['value'])}")
            continue
        value_from = item.get("valueFrom")
        if isinstance(value_from, dict):
            secret_ref = value_from.get("secretKeyRef")
            if isinstance(secret_ref, dict):
                name = secret_ref.get("name")
                k = secret_ref.get("key")
                if name:
                    lines.append(f"{key}: from secret {_quote(str(name))}.{k}")
                    continue
            cm_ref = value_from.get("configMapKeyRef")
            if isinstance(cm_ref, dict):
                name = cm_ref.get("name")
                k = cm_ref.get("key")
                if name:
                    lines.append(f"{key}: from config {_quote(str(name))}.{k}")
                    continue
            field_ref = value_from.get("fieldRef")
            if isinstance(field_ref, dict) and field_ref.get("fieldPath"):
                lines.append(f"{key}: from field {_quote(str(field_ref['fieldPath']))}")
                continue
    if not lines:
        return []
    return ["env {", *["    " + line for line in lines], "}"]


def _render_resources(resources: Dict[str, Any]) -> List[str]:
    if not isinstance(resources, dict) or not resources:
        return []
    lines: List[str] = []
    requests = resources.get("requests") or {}
    limits = resources.get("limits") or {}
    for label, section in (("requests", requests), ("limits", limits)):
        if not isinstance(section, dict):
            continue
        entries = []
        if section.get("cpu"):
            entries.append(f"cpu: {_render_resource_value(section['cpu'])}")
        if section.get("memory"):
            entries.append(f"memory: {_render_resource_value(section['memory'])}")
        if entries:
            lines.append(f"{label}: {{ {', '.join(entries)} }}")
    return ["resources {", *["    " + line for line in lines], "}"]


def _render_probe(probe: Dict[str, Any]) -> str:
    kind = probe.get("kind")
    value = probe.get("value")
    opts: List[str] = []
    timing = (
        ("initialDelaySeconds", "initialDelay"),
        ("periodSeconds", "interval"),
        ("timeoutSeconds", "timeout"),
        ("failureThreshold", "retries"),
    )
    for k8s_key, infra_key in timing:
        if probe.get(k8s_key) is not None:
            opts.append(f"{infra_key}: {probe[k8s_key]}s")
    if kind == "http":
        path = _get(value, "path", "/")
        body = _quote(path)
    elif kind == "tcp":
        body = str(value)
    elif kind == "exec":
        cmd = value or []
        body = "[" + ", ".join(_quote(c) for c in cmd) + "]"
    elif kind == "grpc":
        body = str(value)
    else:
        body = _quote("/")
    if opts:
        return f"{kind}({body}) {{ {', '.join(opts)} }}"
    return f"{kind}({body})"


def _render_probes(container: Container) -> List[str]:
    present = [
        p
        for p in (container.liveness, container.readiness, container.startup)
        if p
    ]
    if not present:
        return []
    only_liveness = (
        container.liveness is not None
        and container.readiness is None
        and container.startup is None
    )
    if len(present) == 1 and only_liveness and container.liveness is not None:
        return [f"health {_render_probe(container.liveness)}"]
    entries = []
    probe_map = (
        ("liveness", "liveness"),
        ("readiness", "readiness"),
        ("startup", "startup"),
    )
    for attr, key in probe_map:
        probe = getattr(container, attr)
        if probe:
            entries.append(f"{key} {_render_probe(probe)}")
    return ["probes {", *["    " + e for e in entries], "}"]


def _render_volumes(workload: Workload, container: Container) -> List[str]:
    if not workload.volumes:
        return []
    mounts = {
        str(m.get("name")): str(m.get("mountPath"))
        for m in container.volume_mounts
        if isinstance(m, dict)
    }
    items: List[str] = []
    for v in workload.volumes:
        name = str(v.get("name") or "")
        if not name:
            continue
        fields = [f"name: {_quote(name)}"]
        if v.get("claim"):
            fields.append(f"claim: {_quote(str(v['claim']))}")
        mount = mounts.get(name)
        if mount:
            fields.append(f"mount_path: {_quote(mount)}")
        items.append("{ " + ", ".join(fields) + " }")
    return [f"volumes [{', '.join(items)}]"]


def _render_ingress(hosts: List[str]) -> List[str]:
    if not hosts:
        return []
    unique: List[str] = []
    for h in hosts:
        if h not in unique:
            unique.append(h)
    items = [f"host: {_quote(h)}" for h in unique]
    return ["ingress {", *["    " + item for item in items], "}"]


def _render_service_block(
    workload: Workload,
    container: Container,
    service_ports: Optional[List[Dict[str, Any]]],
    ingress: Optional[IngressRes],
) -> List[str]:
    name = sanitize_name(workload.name)
    body: List[str] = []
    if container.image:
        body.append(f"image: {_quote(container.image)}")
    if workload.replicas is not None and workload.kind == "Deployment":
        body.append(f"replicas: {workload.replicas}")
    ports = service_ports if service_ports is not None else container.ports
    body.extend(_render_ports(ports))
    body.extend(_render_env(container.env))
    body.extend(_render_resources(container.resources))
    body.extend(_render_probes(container))
    body.extend(_render_volumes(workload, container))
    if ingress:
        body.extend(_render_ingress(ingress.hosts))
    if not body:
        body.append("# no runnable fields extracted")
    return ["service %s {" % name, *["    " + line for line in body], "}"]


def _render_database_block(workload: Workload, container: Container) -> List[str]:
    name = sanitize_name(workload.name)
    image = container.image or ""
    body: List[str] = []
    if "postgres" in image.lower() or "postgresql" in image.lower():
        body.append("type: postgres")
    elif "mysql" in image.lower():
        body.append("type: mysql")
    elif "mongo" in image.lower():
        body.append("type: mongo")
    if workload.replicas and workload.replicas > 1:
        body.append(f"replicas: {workload.replicas}")
    if not body:
        body.append("type: postgres")
    return ["database %s {" % name, *["    " + line for line in body], "}"]


def _render_cache_block(workload: Workload, container: Container) -> List[str]:
    name = sanitize_name(workload.name)
    return ["cache %s {" % name, "    type: redis", "}"]


def _render_secret(secret: SimpleResource) -> List[str]:
    name = sanitize_name(secret.name)
    keys = sorted(set(secret.data) | set(secret.string_data))
    if not keys:
        return [
            f"secret {name} {{",
            "    # empty secret (imported from Kubernetes)",
            "}",
        ]
    lines = [f"secret {name} {{"]
    for k in keys:
        if not _IDENT_RE.match(k):
            continue
        if k in secret.string_data:
            lines.append(f"    {k}: {_quote(secret.string_data[k])}")
        else:
            lines.append(f"    {k}: from env {_quote(str(k).upper())}")
    lines.append("}")
    return lines


def _render_config(config: SimpleResource) -> List[str]:
    name = sanitize_name(config.name)
    keys = sorted(set(config.data) | set(config.string_data))
    if not keys:
        return [
            f"config {name} {{",
            "    # empty config map (imported from Kubernetes)",
            "}",
        ]
    lines = [f"config {name} {{"]
    for k in keys:
        if not _IDENT_RE.match(k):
            continue
        value = config.string_data.get(k, config.data.get(k))
        lines.append(f"    {k}: {_quote(value)}")
    lines.append("}")
    return lines


# --------------------------------------------------------------------------- #
# Importer
# --------------------------------------------------------------------------- #


class K8sImporter:
    """Convert parsed Kubernetes YAML documents into Infra source."""

    def import_documents(self, docs: List[Any], source_name: str = "k8s.yaml") -> str:
        workloads: List[Workload] = []
        services: List[K8sService] = []
        ingresses: List[IngressRes] = []
        simples: List[SimpleResource] = []

        for doc in docs:
            if not isinstance(doc, dict):
                continue
            kind, _ = _kind_and_name(doc)
            if kind == "Deployment":
                workloads.append(_extract_workload(doc, "Deployment"))
            elif kind == "StatefulSet":
                workloads.append(_extract_workload(doc, "StatefulSet"))
            elif kind == "Service":
                services.append(_extract_service(doc))
            elif kind == "Ingress":
                ingresses.append(_extract_ingress(doc))
            elif kind in ("Secret", "ConfigMap"):
                simples.append(_extract_simple(doc, kind))
            # unknown kinds are ignored silently

        blocks: List[str] = []

        # Attach ingresses to workloads by name (K8s Ingress backend.service.name
        # usually matches the workload name).
        for wl in workloads:
            container = wl.containers[0] if wl.containers else Container(name=wl.name)
            matched_svc = self._match_service(wl, services)
            service_ports = matched_svc.ports if matched_svc else None
            ingress = self._match_ingress(wl, matched_svc, ingresses)
            hint = _image_hint(container.image) if container.image else "service"
            if wl.kind == "StatefulSet" and hint == "database":
                blocks.extend(_render_database_block(wl, container))
            elif wl.kind == "StatefulSet" and hint == "cache":
                blocks.extend(_render_cache_block(wl, container))
            else:
                blocks.extend(
                    _render_service_block(wl, container, service_ports, ingress)
                )

        # Leftover Services that didn't match a workload become bare `service`
        # blocks exposing their ports (best-effort; may need an image added).
        matched: set[str] = set()
        for svc in services:
            if any(self._match_service(wl, [svc]) for wl in workloads):
                matched.add(svc.name)
        for svc in services:
            if svc.name in matched:
                continue
            blocks.extend(self._render_standalone_service(svc))

        for simple in simples:
            if simple.kind == "Secret":
                blocks.extend(_render_secret(simple))
            else:
                blocks.extend(_render_config(simple))

        if not blocks:
            return (
                f"# imported from {source_name}\n"
                "# (no supported Kubernetes resources found)\n"
            )

        return f"# imported from {source_name}\n" + "\n".join(blocks) + "\n"

    @staticmethod
    def _match_service(
        workload: Workload, services: List[K8sService]
    ) -> Optional[K8sService]:
        pod_labels = workload.pod_labels or workload.selector
        for svc in services:
            if svc.selector and _labels_subset(svc.selector, pod_labels):
                return svc
        return None

    @staticmethod
    def _match_ingress(
        workload: Workload,
        matched_service: Optional[K8sService],
        ingresses: List[IngressRes],
    ) -> Optional[IngressRes]:
        candidates = set()
        if matched_service:
            candidates.add(matched_service.name)
        candidates.add(workload.name)
        for ing in ingresses:
            if ing.backends and any(b in candidates for b in ing.backends):
                return ing
            if not ing.backends and ing.name in candidates:
                return ing
        return None

    @staticmethod
    def _render_standalone_service(svc: K8sService) -> List[str]:
        name = sanitize_name(svc.name)
        ports = _render_ports(svc.ports)
        body = ports or ["# service exposes no ports"]
        return ["service %s {" % name, *["    " + line for line in body], "}"]


def import_kubernetes(source: Any) -> str:
    """Import Kubernetes YAML from a file or directory and return Infra source.

    ``source`` may be a path to a single YAML file or to a directory of YAML
    files. Raises :class:`InfraImportError` on unreadable or invalid YAML.
    """
    path = Path(source)
    if path.is_dir():
        files = sorted(
            p for p in path.iterdir()
            if p.suffix in (".yaml", ".yml") and p.is_file()
        )
        if not files:
            raise InfraImportError(f"No YAML files found in '{path}'")
        blocks: List[str] = []
        for f in files:
            docs = _load_docs(f)
            blocks.append(K8sImporter().import_documents(docs, source_name=f.name))
        return "\n".join(blocks)

    docs = _load_docs(path)
    return K8sImporter().import_documents(docs, source_name=path.name)


def _load_docs(path: Path) -> List[Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InfraImportError(f"Cannot read '{path}': {exc}") from exc
    try:
        return [d for d in yaml.safe_load_all(text)]
    except yaml.YAMLError as exc:
        raise InfraImportError(f"Invalid YAML in '{path}': {exc}") from exc
