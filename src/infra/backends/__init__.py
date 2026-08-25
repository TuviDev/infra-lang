"""Backend registry."""

from __future__ import annotations

from typing import Any, Dict, Type

from infra.backends.base import Backend
from infra.errors.exceptions import InfraCompileError


def get_backend(name: str, **opts: Any) -> Backend:
    """Instantiate the backend registered under *name* (case-insensitive)."""
    from infra.backends.compose import DockerComposeBackend
    from infra.backends.github import GitHubActionsBackend
    from infra.backends.helm import HelmBackend
    from infra.backends.kubernetes import KubernetesBackend
    from infra.backends.terraform import TerraformBackend

    registry: Dict[str, Type[Backend]] = {
        "kubernetes": KubernetesBackend,
        "k8s": KubernetesBackend,
        "compose": DockerComposeBackend,
        "docker": DockerComposeBackend,
        "terraform": TerraformBackend,
        "tf": TerraformBackend,
        "github": GitHubActionsBackend,
        "actions": GitHubActionsBackend,
        "github_actions": GitHubActionsBackend,
        "helm": HelmBackend,
    }
    cls = registry.get(name.lower())
    if cls is None:
        raise InfraCompileError(
            f"Unknown backend '{name}'. Valid targets: kubernetes, compose, terraform, github",  # noqa: E501
            backend=name,
        )
    # Only pass options the specific backend accepts.
    import inspect

    sig = inspect.signature(cls.__init__)
    kwargs = {k: v for k, v in opts.items() if k in sig.parameters}
    return cls(**kwargs)


__all__ = ["get_backend"]
