"""Environment overlay merging for Infra Lang.

A ``.infra`` file can define zero or more deploy-time environment overlays::

    service "web" {
      image: "nginx"
      replicas: 1
    }

    environment "prod" {
      service web {
        replicas: 5
        env { LOG_LEVEL: "info" }
      }
    }

The ``environment "name" { ... }`` block lists per-service *overrides* that are
merged on top of the base definitions by :func:`apply_environment_overlay`.
Selecting an environment at the CLI (``-e prod`` / ``--env prod``) is therefore
a pure, side-effect-free transformation of the AST: the base file stays the
source of truth and each environment only overrides what it needs to.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Tuple, Union

from infra.errors.exceptions import InfraError
from infra.parser import ast_nodes as n


class EnvironmentNotFoundError(InfraError):
    """Raised when ``-e/--env`` names an environment that is not defined."""

    def __init__(
        self,
        env_name: str,
        available: Tuple[str, ...] = (),
        file: str | None = None,
    ) -> None:
        self.env_name = env_name
        self.available = available
        if available:
            msg = (
                f"Environment '{env_name}' is not defined in this file. "
                f"Available environments: {', '.join(available) or '(none)'}."
            )
        else:
            msg = (
                f"Environment '{env_name}' is not defined in this file. "
                "Add an `environment \"<name>\" { ... }` block to use it."
            )
        super().__init__(msg, file=file)


def available_environments(program: n.Program) -> Tuple[str, ...]:
    """Return the names of every environment overlay defined in *program*."""
    return tuple(e.name for e in program.environments)


def apply_environment_overlay(
    program: n.Program, env_name: str
) -> n.Program:
    """Return a copy of *program* with the named environment overlay applied.

    Merges each service override on top of the matching :class:`ServiceDef`
    (scalar fields are replaced; ``env``/``labels``/``annotations`` are merged
    with the overlay winning on name collisions). Raises
    :class:`EnvironmentNotFoundError` if ``env_name`` is not defined in the
    file.
    """
    spec = _find_environment(program, env_name)
    overlay_map: dict[str, n.ServiceOverlay] = {o.name: o for o in spec.overrides}

    new_statements: list[Union[n.Statement, n.Definition]] = []
    for stmt in program.statements:
        if isinstance(stmt, n.ServiceDef) and stmt.name in overlay_map:
            new_statements.append(_apply_service_overlay(stmt, overlay_map[stmt.name]))
        else:
            new_statements.append(stmt)

    return replace(
        program,
        statements=tuple(new_statements),
        environments=(),
    )


def _find_environment(program: n.Program, env_name: str) -> n.EnvironmentSpec:
    for spec in program.environments:
        if spec.name == env_name:
            return spec
    raise EnvironmentNotFoundError(
        env_name, available=available_environments(program)
    )


def _apply_service_overlay(
    service: n.ServiceDef, overlay: n.ServiceOverlay
) -> n.ServiceDef:
    kwargs: dict[str, Any] = {}
    if overlay.replicas is not None:
        kwargs["replicas"] = overlay.replicas
    if overlay.image is not None:
        kwargs["image"] = overlay.image
    if overlay.command:
        kwargs["command"] = overlay.command
    if overlay.args:
        kwargs["args"] = overlay.args
    if overlay.env:
        kwargs["env"] = _merge_env(service.env, overlay.env)
    if overlay.labels:
        kwargs["labels"] = _merge_pairs(service.labels, overlay.labels)
    if overlay.annotations:
        kwargs["annotations"] = _merge_pairs(service.annotations, overlay.annotations)
    if overlay.resources is not None:
        kwargs["resources"] = overlay.resources
    if overlay.expose:
        kwargs["expose"] = True
    return replace(service, **kwargs)


def _merge_env(
    base: Tuple[n.EnvEntry, ...], overlay: Tuple[n.EnvEntry, ...]
) -> Tuple[n.EnvEntry, ...]:
    """Merge env entries, overlay winning on duplicate names."""
    merged = {e.name: e for e in base}
    for e in overlay:
        merged[e.name] = e
    return tuple(merged.values())


def _merge_pairs(
    base: Tuple[Tuple[str, str], ...], overlay: Tuple[Tuple[str, str], ...]
) -> Tuple[Tuple[str, str], ...]:
    """Merge string pairs, overlay winning on duplicate keys."""
    merged = dict(base)
    merged.update(dict(overlay))
    return tuple(merged.items())


__all__ = [
    "EnvironmentNotFoundError",
    "apply_environment_overlay",
    "available_environments",
]
