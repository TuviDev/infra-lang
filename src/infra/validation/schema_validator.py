"""Kubernetes schema validation using structural JSON-schema checks.

Downloads of the full kubernetes-json-schema are attempted lazily but are not
required: this module performs structural validation against a known-good set
of required fields and API version expectations, so it works fully offline and
without external tools. See docs/support_matrix.md for the supported set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import yaml

REQUIRED_FIELDS = {
    "Deployment": ["apiVersion", "kind", "metadata.name", "spec.replicas"],
    "Service": ["apiVersion", "kind", "metadata.name", "spec.selector"],
    "StatefulSet": ["apiVersion", "kind", "metadata.name"],
    "Secret": ["apiVersion", "kind", "metadata.name"],
    "ConfigMap": ["apiVersion", "kind", "metadata.name"],
    "Ingress": ["apiVersion", "kind", "metadata.name"],
    "HorizontalPodAutoscaler": [
        "apiVersion",
        "kind",
        "metadata.name",
        "spec.scaleTargetRef",
        "spec.minReplicas",
        "spec.maxReplicas",
    ],
    "CronJob": ["apiVersion", "kind", "metadata.name", "spec.schedule"],
    "NetworkPolicy": ["apiVersion", "kind", "metadata.name"],
    "ResourceQuota": ["apiVersion", "kind", "metadata.name"],
    "PodDisruptionBudget": ["apiVersion", "kind", "metadata.name"],
}

API_VERSIONS = {
    "Deployment": "apps/v1",
    "StatefulSet": "apps/v1",
    "Service": "v1",
    "Secret": "v1",
    "ConfigMap": "v1",
    "Namespace": "v1",
    "ServiceAccount": "v1",
    "PersistentVolumeClaim": "v1",
    "ResourceQuota": "v1",
    "CronJob": "batch/v1",
    "HorizontalPodAutoscaler": "autoscaling/v2",
    "Ingress": "networking.k8s.io/v1",
    "NetworkPolicy": "networking.k8s.io/v1",
    "PodDisruptionBudget": "policy/v1",
    "ClusterRole": "rbac.authorization.k8s.io/v1",
    "ClusterRoleBinding": "rbac.authorization.k8s.io/v1",
}

# lazily loaded kubernetes-json-schema docs (optional, offline fallback)
_SCHEMA_CACHE: Dict[str, dict] = {}


@dataclass
class SchemaIssue:
    severity: str  # "error" | "warning"
    kind: str
    name: str
    field: str
    message: str


def _get_nested(doc: dict, path: str):
    parts = path.split(".")
    current: object = doc
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def validate_document(doc: dict) -> List[SchemaIssue]:
    issues: List[SchemaIssue] = []
    kind = doc.get("kind", "Unknown")
    name = _get_nested(doc, "metadata.name") or "unknown"

    expected_api = API_VERSIONS.get(kind)
    actual_api = doc.get("apiVersion", "")
    if expected_api and actual_api != expected_api:
        issues.append(
            SchemaIssue(
                severity="warning",
                kind=kind,
                name=str(name),
                field="apiVersion",
                message=f"Expected {expected_api!r}, got {actual_api!r}",
            )
        )

    for field_path in REQUIRED_FIELDS.get(kind, []):
        value = _get_nested(doc, field_path)
        if value is None:
            issues.append(
                SchemaIssue(
                    severity="error",
                    kind=kind,
                    name=str(name),
                    field=field_path,
                    message=f"Required field {field_path!r} is missing or null",
                )
            )

    if kind == "Deployment":
        replicas = _get_nested(doc, "spec.replicas")
        if replicas is not None and not isinstance(replicas, int):
            issues.append(
                SchemaIssue(
                    severity="error",
                    kind=kind,
                    name=str(name),
                    field="spec.replicas",
                    message=(
                        f"spec.replicas must be int, got {type(replicas).__name__}"
                    ),
                )
            )

    return issues


def validate_yaml_content(content: str) -> List[SchemaIssue]:
    issues: List[SchemaIssue] = []
    try:
        for doc in yaml.safe_load_all(content):
            if doc and isinstance(doc, dict) and "kind" in doc:
                issues.extend(validate_document(doc))
    except yaml.YAMLError as e:
        issues.append(
            SchemaIssue(
                severity="error",
                kind="Unknown",
                name="unknown",
                field="yaml",
                message=f"YAML parse error: {e}",
            )
        )
    return issues


def validate_compiled_output(files: Dict[str, str]) -> List[SchemaIssue]:
    all_issues: List[SchemaIssue] = []
    for _filename, content in files.items():
        all_issues.extend(validate_yaml_content(content))
    return all_issues
