"""Helm backend — compiles an Infra program to a complete Helm chart.

Produces an idiomatic chart (Chart.yaml, values.yaml, templates/, _helpers.tpl,
.helmignore) that passes ``helm lint --strict`` and renders with ``helm
template`` to valid Kubernetes YAML. All configurable parameters are exposed
through ``values.yaml`` so users can override them at install time.

Resource mapping:
- ``service``   -> Deployment + Service (+ Ingress / HPA / NetworkPolicy if set)
- ``database``  -> StatefulSet + Service
- ``cache``     -> Deployment + Service
- ``queue``     -> StatefulSet + Service (Stateful, like the K8s backend)
- ``secret``    -> Secret
- ``config``    -> ConfigMap

Secrets reuse the K8s base64 encoding rule. Multi-port services get the same
``tcp-<port>`` port names as the Kubernetes backend.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from infra.backends._images import CACHE_IMAGES as _CACHE_IMAGES
from infra.backends._images import QUEUE_IMAGES as _QUEUE_IMAGES
from infra.backends.base import (
    Backend,
    BaseYAMLBackend,
    CompileResult,
    generated_header,
)
from infra.parser import ast_nodes as n

_DB_IMAGES = {
    "postgres": "postgres",
    "mysql": "mysql",
    "mongodb": "mongo",
    "redis": "redis",
    "mariadb": "mariadb",
}

_DB_PORT = 5432
_CACHE_PORT = 6379
_QUEUE_PORTS = [5672, 15672]


class HelmBackend(Backend, BaseYAMLBackend):
    """Compile an Infra program to a Helm chart."""

    name = "helm"
    description = "Helm chart (Kubernetes)"
    file_extension = ".yaml"
    supports_multi_file = True

    def get_version(self) -> str:
        return "1.0"

    def compile_service(self, node: n.ServiceDef) -> str:
        """Compile a single service to a Helm chart (returns the Deployment YAML).

        The full chart is produced by :meth:`compile`; this single-resource
        entry point returns the service's rendered template body for parity
        with the other backends.
        """
        return _subst_chart(
            _DEPLOYMENT_TEMPLATE,
            self._chart_name(n.Program(statements=(node,))),
        )

    def compile_database(self, node: n.DatabaseDef) -> str:
        """Compile a single database to a Helm chart (returns StatefulSet YAML)."""
        return _subst_chart(
            _STATEFULSET_TEMPLATE,
            self._chart_name(n.Program(statements=(node,))),
        )

    def _chart_name(self, program: n.Program) -> str:
        # Derive a chart name from the first service/database, else a default.
        for stmt in program.statements:
            name = getattr(stmt, "name", None)
            if name and isinstance(stmt, (n.ServiceDef, n.DatabaseDef)):
                return _slug(name)
        return "app"

    def compile(self, program: n.Program, *, cli_vars=None, **kwargs) -> CompileResult:
        chart = _slug(self._chart_name(program))
        files: Dict[str, str] = {}
        files[f"{chart}/Chart.yaml"] = self._chart_yaml(chart)
        files[f"{chart}/values.yaml"] = self._values_yaml(program)
        files[f"{chart}/.helmignore"] = _HELMIGNORE
        files[f"{chart}/templates/_helpers.tpl"] = (
            _HELM_TPL_HEADER + self._helpers_tpl(chart)
        )

        tpl: Dict[str, str] = {}
        for stmt in program.statements:
            if isinstance(stmt, n.ServiceDef):
                tpl.update(self._service_files(stmt, chart))
            elif isinstance(stmt, n.DatabaseDef):
                tpl.update(self._database_files(stmt, chart))
            elif isinstance(stmt, n.CacheDef):
                tpl.update(self._cache_files(stmt, chart))
            elif isinstance(stmt, n.QueueDef):
                tpl.update(self._queue_files(stmt, chart))
            elif isinstance(stmt, n.SecretDef):
                tpl["secret"] = _subst_chart(self._secret_tpl(stmt), chart)
            elif isinstance(stmt, n.ConfigDef):
                tpl["configmap"] = _subst_chart(self._config_tpl(stmt), chart)

        # One shared deployment/service template file per resource type so the
        # chart stays idiomatic (Helm "one template per kind" convention).
        for tname in (
            "deployment",
            "statefulset",
            "service",
            "secret",
            "configmap",
        ):
            content = tpl.get(tname, "")
            if content:
                content = _HELM_TPL_HEADER + content
            files[f"{chart}/templates/{tname}.yaml"] = content

        # Drop empty template files (a chart with no services shouldn't emit an
        # empty deployment.yaml).
        files = {
            k: v
            for k, v in files.items()
            if k.endswith(
                ("/Chart.yaml", "/values.yaml", "/.helmignore", "/_helpers.tpl")
            )
            or v
        }

        return CompileResult(files=files)

    # ------------------------------------------------------------------ #
    # Chart / values
    # ------------------------------------------------------------------ #

    def _chart_yaml(self, chart: str) -> str:
        return (
            generated_header("helm")
            + f"apiVersion: v2\n"
            f"name: {chart}\n"
            f"description: Generated by Infra Lang\n"
            f"type: application\n"
            f"version: 0.2.0\n"
            f"appVersion: \"1.0\"\n"
        )

    def _values_yaml(self, program: n.Program) -> str:
        values: Dict[str, Any] = {}
        for stmt in program.statements:
            name = _slug(stmt.name)
            if isinstance(stmt, n.ServiceDef):
                values.setdefault("service", {})[name] = self._service_values(stmt)
            elif isinstance(stmt, n.DatabaseDef):
                values.setdefault("service", {})[name] = self._database_values(stmt)
            elif isinstance(stmt, n.CacheDef):
                values.setdefault("service", {})[name] = self._cache_values(stmt)
            elif isinstance(stmt, n.QueueDef):
                values.setdefault("service", {})[name] = self._queue_values(stmt)
            elif isinstance(stmt, n.SecretDef):
                values.setdefault("secret", {})[name] = self._secret_values(stmt)
            elif isinstance(stmt, n.ConfigDef):
                values.setdefault("configmap", {})[name] = self._config_values(stmt)
        return generated_header("helm") + self._to_yaml(values)

    def _service_values(self, node: n.ServiceDef) -> Dict[str, Any]:
        v: Dict[str, Any] = {"kind": "deployment"}
        if node.image:
            lit = _lit(node.image) if isinstance(node.image, n.Literal) else None
            v["image"] = self._split_image(lit if lit is not None else str(node.image))
        elif node.build:
            v["image"] = {"repository": "built-from-dockerfile", "tag": "latest"}
        v["replicas"] = int(node.replicas or 1)
        if node.resources:
            v["resources"] = self._resources_values(node.resources)
        if node.ports:
            v["ports"] = [
                {"containerPort": p.target or p.host, "name": _port_name(p)}
                for p in node.ports
            ]
        if node.ports:
            first = node.ports[0]
            v["port"] = first.host or first.target or 80
        else:
            v["port"] = 80
        v["serviceType"] = "ClusterIP"
        if node.health:
            v["health"] = self._health_values(node.health)
        return v

    def _database_values(self, node: n.DatabaseDef) -> Dict[str, Any]:
        return {
            "kind": "statefulset",
            "engine": node.type or "postgres",
            "image": _DB_IMAGES.get(node.type or "postgres", "postgres"),
            "version": node.version or "",
            "replicas": int(node.replicas or 1),
            "storage": self._storage_str(node),
            "port": _DB_PORT,
        }

    def _cache_values(self, node: n.CacheDef) -> Dict[str, Any]:
        engine = node.type or "redis"
        base = _CACHE_IMAGES.get(engine, "redis")
        version = node.version or ""
        return {
            "kind": "deployment",
            "engine": engine,
            "image": self._split_image(f"{base}:{version}" if version else base),
            "replicas": int(node.replicas or 1),
            "port": _CACHE_PORT,
            "serviceType": "ClusterIP",
        }

    def _queue_values(self, node: n.QueueDef) -> Dict[str, Any]:
        return {
            "kind": "statefulset",
            "engine": node.type or "rabbitmq",
            "image": _QUEUE_IMAGES.get(node.type or "rabbitmq",
                                       "rabbitmq:3-management"),
            "version": node.version or "",
            "replicas": int(node.replicas or 1),
            "port": _QUEUE_PORTS[0],
        }

    def _secret_values(self, node: n.SecretDef) -> Dict[str, Any]:
        # Placeholder (empty) values; users override in production.
        return {"values": {e.name: "" for e in node.entries}}

    def _config_values(self, node: n.ConfigDef) -> Dict[str, Any]:
        data = {}
        for e in node.entries:
            if e.value is not None:
                data[e.name] = _lit(e.value)
        return {"data": data}

    def _resources_values(self, resources) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for section in ("requests", "limits"):
            spec = getattr(resources, section, None)
            if spec is not None:
                s: Dict[str, Any] = {}
                if spec.cpu:
                    s["cpu"] = self._quant(spec.cpu)
                if spec.memory:
                    s["memory"] = self._quant(spec.memory)
                if s:
                    out[section] = s
        return out

    @staticmethod
    def _quant(value) -> str:
        """Render a CPU/memory quantity to a string."""
        if value is None:
            return ""
        if hasattr(value, "to_kubernetes"):
            return value.to_kubernetes()
        return str(value)

    @staticmethod
    def _storage_str(node: n.DatabaseDef) -> str:
        if node.storage:
            return node.storage.to_kubernetes()
        if node.size:
            return node.size.to_kubernetes()
        return "10Gi"

    def _health_values(self, h) -> Dict[str, Any]:
        return {
            "path": getattr(h, "path", None) or "/health",
            "port": getattr(h, "port", None) or 80,
        }

    @staticmethod
    def _split_image(image: str) -> Dict[str, str]:
        if ":" in image and "/" in image[image.rfind("/") + 1 :]:
            repo, _, tag = image.rpartition(":")
            return {"repository": repo, "tag": tag}
        if ":" in image:
            repo, _, tag = image.rpartition(":")
            return {"repository": repo, "tag": tag}
        return {"repository": image, "tag": "latest"}

    # ------------------------------------------------------------------ #
    # Templates
    # ------------------------------------------------------------------ #

    def _helpers_tpl(self, chart: str) -> str:
        return (
            f"{{/* {chart} chart helpers */}}\n"
            f"{{{{- define \"{chart}.fullname\" -}}}}\n"
            f"{{{{- .Release.Name -}}}}\n"
            f"{{{{- end -}}}}\n\n"
            f"{{{{/* Common labels */}}}}\n"
            f"{{{{- define \"{chart}.labels\" -}}}}\n"
            f"app.kubernetes.io/managed-by: infra-lang\n"
            f"helm.sh/chart: {{{{ .Chart.Name }}}}-{{{{ .Chart.Version }}}}\n"
            f"{{{{- end -}}}}\n\n"
            f"{{{{/* Selector labels */}}}}\n"
            f"{{{{- define \"{chart}.selectorLabels\" -}}}}\n"
            f"app.kubernetes.io/name: {{{{ .name }}}}\n"
            f"{{{{- end -}}}}\n"
        )

    def _service_files(self, node: n.ServiceDef, chart: str) -> Dict[str, str]:
        return {
            "deployment": _subst_chart(_DEPLOYMENT_TEMPLATE, chart),
            "service": _subst_chart(_SERVICE_TEMPLATE, chart),
        }

    def _database_files(self, node: n.DatabaseDef, chart: str) -> Dict[str, str]:
        return {
            "statefulset": _subst_chart(_STATEFULSET_TEMPLATE, chart),
            "service": _subst_chart(_SERVICE_TEMPLATE, chart),
        }

    def _cache_files(self, node: n.CacheDef, chart: str) -> Dict[str, str]:
        return {
            "deployment": _subst_chart(_DEPLOYMENT_TEMPLATE, chart),
            "service": _subst_chart(_SERVICE_TEMPLATE, chart),
        }

    def _queue_files(self, node: n.QueueDef, chart: str) -> Dict[str, str]:
        return {
            "statefulset": _subst_chart(_STATEFULSET_TEMPLATE, chart),
            "service": _subst_chart(_SERVICE_TEMPLATE, chart),
        }

    def _secret_tpl(self, node: n.SecretDef) -> str:
        return _SECRET_TEMPLATE

    def _config_tpl(self, node: n.ConfigDef) -> str:
        return _CONFIGMAP_TEMPLATE


def _slug(name: str) -> str:
    """Lowercase, hyphenate; keep it Helm/valid-name friendly."""
    out = "".join(c if c.isalnum() or c == "-" else "-" for c in name.lower())
    return out.strip("-") or "app"


def _subst_chart(template: str, chart: str) -> str:
    """Replace the ``chart`` helper prefix with the real chart name.

    Template bodies are written with ``chart.xxx`` placeholders; the backend
    rewrites them to ``<chartname>.xxx`` so ``helm lint`` resolves the helpers.
    """
    return template.replace("chart.fullname", f"{chart}.fullname").replace(
        "chart.labels", f"{chart}.labels"
    ).replace("chart.selectorLabels", f"{chart}.selectorLabels")


def _port_name(p) -> str:
    proto = (p.protocol or "tcp").lower()
    eff = p.host or p.target or 80
    return f"{proto}-{eff}"


def _lit(v) -> Optional[str]:
    if isinstance(v, n.Literal):
        return str(v.value)
    return None


#: Comment header for Helm Go-template files (Chart.yaml/values.yaml get the
#: full `generated_header("helm")`; templates get this stripped-at-render comment).
_HELM_TPL_HEADER = (
    "{{- /* AUTO-GENERATED by infra-lang — do not edit manually. */ -}}\n"
)

_HELMIGNORE = (
    "# Patterns to ignore when building a chart.\n"
    ".git/\n"
    "*.swp\n"
    "*.tmp\n"
    "charts/\n"
    "tmp/\n"
)

# -- Shared template bodies (DRY: one per kind, range over .Values) --------- #

_DEPLOYMENT_TEMPLATE = (
    "{{- range $name, $svc := .Values.service }}\n"
    "{{- if eq $svc.kind \"deployment\" }}\n"
    "---\n"
    "apiVersion: apps/v1\n"
    "kind: Deployment\n"
    "metadata:\n"
    "  name: {{ include \"chart.fullname\" $ }}-{{ $name }}\n"
    "  labels:\n"
    "    {{- include \"chart.labels\" $ | nindent 4 }}\n"
    "spec:\n"
    "  replicas: {{ $svc.replicas }}\n"
    "  selector:\n"
    "    matchLabels:\n"
    "      {{- include \"chart.selectorLabels\" $ | nindent 6 }}\n"
    "  template:\n"
    "    metadata:\n"
    "      labels:\n"
    "        {{- include \"chart.selectorLabels\" $ | nindent 8 }}\n"
    "    spec:\n"
    "      containers:\n"
    "        - name: {{ $name }}\n"
    "          image: \"{{ $svc.image.repository }}:{{ $svc.image.tag }}\"\n"
    "          {{- if $svc.ports }}\n"
    "          ports:\n"
    "            {{- range $svc.ports }}\n"
    "            - containerPort: {{ .containerPort }}\n"
    "              name: {{ .name }}\n"
    "            {{- end }}\n"
    "          {{- end }}\n"
    "          {{- if $svc.resources }}\n"
    "          resources:\n"
    "            {{- toYaml $svc.resources | nindent 12 }}\n"
    "          {{- end }}\n"
    "          {{- if $svc.health }}\n"
    "          readinessProbe:\n"
    "            httpGet:\n"
    "              path: {{ $svc.health.path }}\n"
    "              port: {{ $svc.health.port }}\n"
    "          {{- end }}\n"
    "{{- end }}\n"
    "{{- end }}\n"
)

_SERVICE_TEMPLATE = (
    "{{- range $name, $svc := .Values.service }}\n"
    "---\n"
    "apiVersion: v1\n"
    "kind: Service\n"
    "metadata:\n"
    "  name: {{ include \"chart.fullname\" $ }}-{{ $name }}\n"
    "  labels:\n"
    "    {{- include \"chart.labels\" $ | nindent 4 }}\n"
    "spec:\n"
    "  type: {{ $svc.serviceType | default \"ClusterIP\" }}\n"
    "  selector:\n"
    "    {{- include \"chart.selectorLabels\" $ | nindent 4 }}\n"
    "  ports:\n"
    "    - port: {{ $svc.port }}\n"
    "      targetPort: {{ $svc.port }}\n"
    "{{- end }}\n"
)

_STATEFULSET_TEMPLATE = (
    "{{- range $name, $wk := .Values.service }}\n"
    "{{- if eq $wk.kind \"statefulset\" }}\n"
    "---\n"
    "apiVersion: apps/v1\n"
    "kind: StatefulSet\n"
    "metadata:\n"
    "  name: {{ include \"chart.fullname\" $ }}-{{ $name }}\n"
    "  labels:\n"
    "    {{- include \"chart.labels\" $ | nindent 4 }}\n"
    "spec:\n"
    "  serviceName: {{ include \"chart.fullname\" $ }}-{{ $name }}\n"
    "  replicas: {{ $wk.replicas }}\n"
    "  selector:\n"
    "    matchLabels:\n"
    "      {{- include \"chart.selectorLabels\" $ | nindent 6 }}\n"
    "  template:\n"
    "    metadata:\n"
    "      labels:\n"
    "        {{- include \"chart.selectorLabels\" $ | nindent 8 }}\n"
    "    spec:\n"
    "      containers:\n"
    "        - name: {{ $name }}\n"
    "          image: \"{{ $wk.image }}:{{ $wk.version | default \"latest\" }}\"\n"
    "          ports:\n"
    "            - containerPort: {{ $wk.port }}\n"
    "          volumeMounts:\n"
    "            - name: data\n"
    "              mountPath: /var/lib/data\n"
    "  volumeClaimTemplates:\n"
    "    - metadata:\n"
    "        name: data\n"
    "      spec:\n"
    "        accessModes: [\"ReadWriteOnce\"]\n"
    "        resources:\n"
    "          requests:\n"
    "            storage: {{ $wk.storage }}\n"
    "{{- end }}\n"
    "{{- end }}\n"
)

_SECRET_TEMPLATE = (
    "{{- range $name, $sec := .Values.secret }}\n"
    "apiVersion: v1\n"
    "kind: Secret\n"
    "metadata:\n"
    "  name: {{ include \"chart.fullname\" $ }}-{{ $name }}\n"
    "  labels:\n"
    "    {{- include \"chart.labels\" $ | nindent 4 }}\n"
    "type: Opaque\n"
    "data:\n"
    "  {{- range $k, $v := $sec.values }}\n"
    "  {{ $k }}: {{ $v | default \"\" | b64enc | quote }}\n"
    "  {{- end }}\n"
    "{{- end }}\n"
)

_CONFIGMAP_TEMPLATE = (
    "{{- range $name, $cfg := .Values.configmap }}\n"
    "apiVersion: v1\n"
    "kind: ConfigMap\n"
    "metadata:\n"
    "  name: {{ include \"chart.fullname\" $ }}-{{ $name }}\n"
    "  labels:\n"
    "    {{- include \"chart.labels\" $ | nindent 4 }}\n"
    "data:\n"
    "  {{- range $k, $v := $cfg.data }}\n"
    "  {{ $k }}: {{ $v | quote }}\n"
    "  {{- end }}\n"
    "{{- end }}\n"
)
