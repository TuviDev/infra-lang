# ruff: noqa: E501
"""Extends resolver — resolves inheritance before compilation.

Supports ``environment X extends base { ... }`` and
``service X extends base-service { ... }``. The child inherits parent fields
and overrides them with its own (child wins). Labels are merged by key.
"""
# mypy: disable-error-code="no-untyped-def,no-untyped-call,misc,no-any-return,type-arg"

from __future__ import annotations

from dataclasses import replace

from infra.errors.exceptions import InfraError
from infra.parser import ast_nodes as n


class ExtendsCycleError(InfraError):
    """Raised when extends declarations form a cycle."""

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        super().__init__(f"Circular extends detected: {' -> '.join(cycle)}")


def _merge_labels(parent, child):
    """Merge label tuples by key; child wins."""
    merged = {k: v for k, v in parent}
    for k, v in child:
        merged[k] = v
    return tuple(sorted(merged.items()))


class ExtendsResolver:
    """Resolve inheritance of environment and service definitions."""

    def resolve(self, program: n.Program) -> n.Program:
        envs = {e.name: e for e in program.statements if isinstance(e, n.EnvironmentDef)}
        svcs = {s.name: s for s in program.statements if isinstance(s, n.ServiceDef)}

        resolved_envs = {name: self._resolve_environment(env, envs, ()) for name, env in envs.items()}
        resolved_svcs = {name: self._resolve_service(svc, svcs, ()) for name, svc in svcs.items()}

        new_stmts = []
        for stmt in program.statements:
            if isinstance(stmt, n.EnvironmentDef):
                new_stmts.append(resolved_envs[stmt.name])
            elif isinstance(stmt, n.ServiceDef):
                new_stmts.append(resolved_svcs[stmt.name])
            else:
                new_stmts.append(stmt)
        return replace(program, statements=tuple(new_stmts))

    # ------------------------------------------------------------------ #
    def _resolve_environment(self, env, all_envs, stack):
        if env.extends is None:
            return env
        parent_name = env.extends
        if parent_name in stack:
            raise ExtendsCycleError(list(stack) + [parent_name])
        if parent_name not in all_envs:
            raise InfraError(
                f"Environment '{env.name}' extends unknown environment '{parent_name}'"
            )
        parent = all_envs[parent_name]
        parent = self._resolve_environment(parent, all_envs, stack + (parent_name,))
        return n.EnvironmentDef(
            name=env.name,
            extends=None,
            provider=env.provider or parent.provider,
            region=env.region or parent.region,
            resources=env.resources or parent.resources,
            namespace=env.namespace or parent.namespace,
            labels=_merge_labels(parent.labels, env.labels),
            location=env.location,
        )

    def _resolve_service(self, svc, all_svcs, stack):
        if svc.extends is None:
            return svc
        parent_name = svc.extends
        if parent_name in stack:
            raise ExtendsCycleError(list(stack) + [parent_name])
        if parent_name not in all_svcs:
            raise InfraError(
                f"Service '{svc.name}' extends unknown service '{parent_name}'"
            )
        parent = all_svcs[parent_name]
        parent = self._resolve_service(parent, all_svcs, stack + (parent_name,))
        return n.ServiceDef(
            name=svc.name,
            extends=None,
            image=svc.image or parent.image,
            build=svc.build or parent.build,
            replicas=svc.replicas if svc.replicas != 1 else parent.replicas,
            ports=svc.ports or parent.ports,
            env=svc.env or parent.env,
            env_from=svc.env_from or parent.env_from,
            command=svc.command or parent.command,
            args=svc.args or parent.args,
            resources=svc.resources or parent.resources,
            health=svc.health or parent.health,
            probes=svc.probes or parent.probes,
            volumes=svc.volumes or parent.volumes,
            depends=svc.depends or parent.depends,
            labels=_merge_labels(parent.labels, svc.labels),
            annotations=_merge_labels(parent.annotations, svc.annotations),
            strategy=svc.strategy or parent.strategy,
            security=svc.security or parent.security,
            lifecycle=svc.lifecycle or parent.lifecycle,
            ingress=svc.ingress or parent.ingress,
            schedule=svc.schedule or parent.schedule,
            expose=svc.expose or parent.expose,
            network=svc.network or parent.network,
            location=svc.location,
        )
