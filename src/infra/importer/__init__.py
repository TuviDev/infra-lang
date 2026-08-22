"""Reverse compiler: Kubernetes YAML -> Infra Lang source.

The :mod:`infra.importer` package turns existing Kubernetes manifests (Deployments,
StatefulSets, Services, Secrets, ConfigMaps, Ingresses) back into readable
``.infra`` source, so you can migrate an existing cluster definition to Infra
Lang and start compiling from there.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence, Union

from infra.importer.k8s import (
    InfraImportError,
    K8sImporter,
    import_kubernetes,
)

__all__ = [
    "InfraImportError",
    "K8sImporter",
    "import_kubernetes",
]

PathLike = Union[str, Path]


def import_kubernetes_from_docs(
    docs: Sequence[object], source_name: str = "k8s.yaml"
) -> str:
    """Convert a sequence of parsed YAML documents into Infra source."""
    return K8sImporter().import_documents(list(docs), source_name=source_name)


def import_kubernetes_file(path: PathLike) -> str:
    """Convert a single YAML file (multi-doc aware) into Infra source."""
    return import_kubernetes(Path(path))
