"""Structural validation of generated Kubernetes YAML.

Performs lightweight, dependency-free checks that a set of YAML documents is
plausible for the Kubernetes API server: every document has ``apiVersion`` /
``kind`` / ``metadata.name``, scalar types are correct (``replicas`` int,
``containers`` list), and resource limits use Kubernetes quantity strings.
This is not a substitute for a real API-server dry-run, but it catches common
mistakes early.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List

import yaml

_DNS1123_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")
_QUANTITY = re.compile(r"^[0-9]+(\.[0-9]+)?(m|[kMGT]i?|Ki|Mi|Gi|Ti|[munpf]|)$")


@dataclass
class K8sValidationIssue:
    """A single problem found in a Kubernetes document."""

    severity: str  # "error" | "warning"
    document_kind: str
    document_name: str
    field: str
    message: str

    def __str__(self) -> str:
        prefix = "error" if self.severity == "error" else "warning"
        loc = (
            f" {self.document_kind}/{self.document_name}"
            if self.document_name
            else ""
        )
        field = f" ({self.field})" if self.field else ""
        return f"{prefix}{loc}{field}: {self.message}"


class KubernetesOutputValidator:
    """Validate generated Kubernetes YAML documents."""

    def validate(self, yaml_content: str) -> List[K8sValidationIssue]:
        try:
            docs = list(yaml.safe_load_all(yaml_content))
        except Exception as exc:  # noqa: BLE001 - surface any YAML error
            return [
                K8sValidationIssue(
                    severity="error",
                    document_kind="",
                    document_name="",
                    field="",
                    message=f"YAML is not parseable: {exc}",
                )
            ]

        issues: List[K8sValidationIssue] = []
        for doc in docs:
            if doc is None:
                continue
            if not isinstance(doc, dict):
                issues.append(
                    K8sValidationIssue(
                        severity="error",
                        document_kind="",
                        document_name="",
                        field="",
                        message="document is not a mapping",
                    )
                )
                continue
            issues.extend(self._check_document(doc))
        return issues

    def validate_files(self, files: Dict[str, str]) -> List[K8sValidationIssue]:
        """Validate a mapping of ``{filename: yaml_content}``."""
        issues: List[K8sValidationIssue] = []
        for fname, content in files.items():
            for issue in self.validate(content):
                issues.append(
                    K8sValidationIssue(
                        severity=issue.severity,
                        document_kind=issue.document_kind,
                        document_name=issue.document_name,
                        field=issue.field,
                        message=f"{fname}: {issue.message}",
                    )
                )
        return issues

    # ------------------------------------------------------------------ #

    def _check_document(self, doc: dict) -> List[K8sValidationIssue]:
        issues: List[K8sValidationIssue] = []
        api_version = doc.get("apiVersion")
        kind = doc.get("kind")

        if not isinstance(api_version, str) or not api_version:
            issues.append(
                K8sValidationIssue(
                    severity="error",
                    document_kind=str(kind),
                    document_name=self._doc_name(doc),
                    field="apiVersion",
                    message="missing or invalid 'apiVersion'",
                )
            )
        if not isinstance(kind, str) or not kind:
            issues.append(
                K8sValidationIssue(
                    severity="error",
                    document_kind="",
                    document_name=self._doc_name(doc),
                    field="kind",
                    message="missing or invalid 'kind'",
                )
            )
            return issues

        doc_name = self._doc_name(doc)
        if not doc_name:
            issues.append(
                K8sValidationIssue(
                    severity="error",
                    document_kind=kind,
                    document_name="",
                    field="metadata.name",
                    message="metadata.name is missing or not a string",
                )
            )

        # DNS-1123 label validity
        if doc_name and not _DNS1123_LABEL.match(doc_name):
            issues.append(
                K8sValidationIssue(
                    severity="error",
                    document_kind=kind,
                    document_name=doc_name,
                    field="metadata.name",
                    message=(
                        f"metadata.name '{doc_name}' is not a valid DNS-1123 "
                        "label (lowercase alphanumeric, '-', '.')"
                    ),
                )
            )

        issues.extend(self._check_kind(doc, kind, doc_name))
        return issues

    @staticmethod
    def _doc_name(doc: dict) -> str:
        meta = doc.get("metadata")
        if isinstance(meta, dict) and isinstance(meta.get("name"), str):
            return meta["name"]
        return ""

    # ------------------------------------------------------------------ #

    def _check_kind(
        self, doc: dict, kind: str, doc_name: str
    ) -> List[K8sValidationIssue]:
        issues: List[K8sValidationIssue] = []
        spec = doc.get("spec")
        if not isinstance(spec, dict):
            return issues

        if kind in ("Deployment", "StatefulSet"):
            if "replicas" in spec and not isinstance(spec["replicas"], int):
                issues.append(
                    self._issue(
                        kind, doc_name, "spec.replicas",
                        f"{kind}.spec.replicas must be an integer",
                    )
                )
            containers = (
                spec.get("template", {}).get("spec", {}).get("containers")
            )
            if "template" in spec and "containers" in (
                spec.get("template", {}).get("spec", {})
            ) and not isinstance(containers, list):
                issues.append(
                    self._issue(
                        kind, doc_name, "spec.template.spec.containers",
                        "spec.containers must be a list",
                    )
                )
            if isinstance(containers, list):
                for c in containers:
                    if not isinstance(c.get("image"), str) or not c.get("image"):
                        issues.append(
                            self._issue(
                                kind, doc_name, "containers[].image",
                                "container is missing an image",
                            )
                        )
                    issues.extend(self._check_resources(c, kind, doc_name))
        elif kind == "Service":
            ports = spec.get("ports")
            if not isinstance(ports, list) or not ports:
                issues.append(
                    self._issue(kind, doc_name, "spec.ports", "Service has no ports")
                )
            else:
                for p in ports:
                    if isinstance(p.get("port"), str):
                        issues.append(
                            self._issue(
                                kind, doc_name, "spec.ports[].port",
                                "Service port must be an integer",
                            )
                        )
        elif kind == "HorizontalPodAutoscaler":
            if not isinstance(spec.get("maxReplicas"), int):
                issues.append(
                    self._issue(
                        kind, doc_name, "spec.maxReplicas",
                        "HorizontalPodAutoscaler maxReplicas must be an integer",
                    )
                )
        elif kind == "NetworkPolicy":
            if "podSelector" not in spec:
                issues.append(
                    self._issue(
                        kind, doc_name, "spec.podSelector",
                        "NetworkPolicy missing spec.podSelector",
                    )
                )
        elif kind == "ResourceQuota":
            if "hard" not in spec:
                issues.append(
                    self._issue(
                        kind, doc_name, "spec.hard",
                        "ResourceQuota missing spec.hard",
                    )
                )
        return issues

    def _check_resources(
        self, container: dict, kind: str, doc_name: str
    ) -> List[K8sValidationIssue]:
        issues: List[K8sValidationIssue] = []
        resources = container.get("resources")
        if not isinstance(resources, dict):
            return issues
        for section in ("requests", "limits"):
            limits = resources.get(section)
            if not isinstance(limits, dict):
                continue
            for res, value in limits.items():
                if isinstance(value, (int, float)):
                    issues.append(
                        self._issue(
                            kind,
                            doc_name,
                            f"resources.{section}.{res}",
                            "resource quantity must be a string "
                            "(e.g. '500m', '256Mi'), not a plain number",
                        )
                    )
                elif isinstance(value, str) and not _QUANTITY.match(value):
                    issues.append(
                        self._issue(
                            kind,
                            doc_name,
                            f"resources.{section}.{res}",
                            f"'{value}' is not a valid Kubernetes resource quantity",
                        )
                    )
        return issues

    @staticmethod
    def _issue(kind: str, name: str, field: str, message: str) -> K8sValidationIssue:
        return K8sValidationIssue(
            severity="error",
            document_kind=kind,
            document_name=name,
            field=field,
            message=message,
        )

    def has_errors(self, yaml_content: str) -> bool:
        return any(i.severity == "error" for i in self.validate(yaml_content))
