"""Semantic validator — walks the AST and reports errors and warnings."""
# ruff: noqa: N802, N812

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import get_close_matches
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, cast

from infra.analyzer import types as T
from infra.analyzer.symbols import (
    Symbol,
    SymbolAlreadyDefinedError,
    SymbolKind,
    SymbolTable,
)
from infra.errors.exceptions import ValidationError, ValidationWarning
from infra.parser import ast_nodes as n


@dataclass
class ValidationResult:
    """Outcome of a semantic validation pass."""

    errors: List[ValidationError] = field(default_factory=list)
    warnings: List[ValidationWarning] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0


# --------------------------------------------------------------------------- #
# Type helper
# --------------------------------------------------------------------------- #


def _expr_type(expr: Optional[n.Expression]) -> T.InfraType:
    if expr is None:
        return T.UNKNOWN
    if isinstance(expr, n.Literal):
        return T.infer_literal_type(expr.value)
    if isinstance(expr, n.Duration):
        return T.DURATION
    if isinstance(expr, n.ResourceValue):
        return T.RESOURCE
    if isinstance(expr, n.Percentage):
        return T.PERCENTAGE
    if isinstance(expr, n.List):
        return T.ListType(T.ANY)
    if isinstance(expr, n.Map):
        return T.MapType(T.STRING, T.ANY)
    if isinstance(expr, n.Identifier):
        return T.UNKNOWN
    return T.ANY


_VALID_DB = {"postgres", "mysql", "mongodb", "redis", "mariadb", "sqlite"}
_VALID_CACHE = {"redis", "valkey", "memcached"}
_VALID_QUEUE = {"rabbitmq", "kafka", "nats"}
_VALID_STORAGE = {"s3", "gcs", "azure_blob", "minio", "pvc", "efs"}
_VALID_PROVIDER = {"aws", "gcp", "azure"}
_VALID_STRATEGY = {"rolling", "recreate", "blue_green", "canary"}


class SemanticValidator:
    """Performs semantic checks over a Program AST."""

    def __init__(self) -> None:
        self.result = ValidationResult()
        self.symbols = SymbolTable()
        self._defined_names: Set[str] = set()
        # All top-level definition names in the program (services and
        # resources), collected up-front so depends_on accepts forward
        # references and non-service targets such as databases or caches.
        self._program_defs: Set[str] = set()
        #: Names of declared ``secret_store`` blocks (v0.5.0).
        self._secret_stores: Set[str] = set()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def validate(
        self,
        program: n.Program,
        *,
        reliability: bool = True,
        security: bool = True,
        max_cost: Optional[float] = None,
    ) -> ValidationResult:
        self.result = ValidationResult()
        self.symbols = SymbolTable()
        self._defined_names = set()
        self._program_defs = {
            name
            for name in (getattr(s, "name", "") for s in program.statements)
            if name
        }
        # v0.5.0: secret stores, collected up-front for STORE_NOT_FOUND
        store_defs = [
            s for s in program.statements if isinstance(s, n.SecretStoreDef)
        ]
        self._secret_stores = {s.name for s in store_defs}

        for imp in program.imports:
            self._check_import(imp)

        for stmt in program.statements:
            self._visit(stmt)

        self._check_dependency_cycles(program)
        self._check_unused()

        if reliability:
            from infra.analyzer.reliability import ReliabilityChecker

            for w in ReliabilityChecker().check(program):
                self.result.warnings.append(
                    ValidationWarning(
                        message=w.message,
                        location=w.location,
                        code=w.code,
                        hint=w.hint,
                    )
                )
        if security:
            from infra.analyzer.security import SecurityChecker

            for finding in SecurityChecker().check(program):
                if isinstance(finding, ValidationError):
                    self.result.errors.append(finding)
                else:
                    self.result.warnings.append(finding)
        if max_cost is not None:
            self._check_max_cost(program, max_cost)
        return self.result

    def _check_dependency_cycles(self, program: n.Program) -> None:
        """Report a service-dependency cycle (``A -> B -> A``) as an error.

        Runs on the merged edge set (``depends`` + ``depends_on``) over
        declared services only — undeclared targets are already reported by
        DEPENDENCY_NOT_FOUND / W001 and are ignored here.
        """
        services = {
            s.name: s for s in program.statements if isinstance(s, n.ServiceDef)
        }
        white, gray, black = 0, 1, 2
        color: Dict[str, int] = {}
        reported: set[tuple[str, ...]] = set()

        def dfs(name: str, path: List[str]) -> None:
            color[name] = gray
            for dep in services[name].dependencies:
                if dep not in services:
                    continue
                state = color.get(dep, white)
                if state == gray:
                    # found a cycle: rotate so it starts at the dep
                    start = path.index(dep) if dep in path else 0
                    cycle = tuple(path[start:] + [dep])
                    if cycle not in reported:
                        reported.add(cycle)
                        svc = services[name]
                        self._err(
                            "Service dependency cycle detected: "
                            + " -> ".join(cycle),
                            svc,
                            "DEPENDENCY_CYCLE",
                            hint="Break the cycle by removing one of the "
                            "depends_on entries",
                        )
                    continue
                if state == white:
                    dfs(dep, path + [dep])
            color[name] = black

        for name in services:
            if color.get(name, white) == white:
                dfs(name, [name])

    def _check_max_cost(self, program: n.Program, max_cost: float) -> None:
        """Append a COST_EXCEEDED error when the estimate breaches the budget."""
        from infra.analyzer.cost import (
            COST_EXCEEDED_CODE,
            COST_EXCEEDED_HINT,
            budget_exceeded_message,
        )

        message = budget_exceeded_message(program, max_cost)
        if message is not None:
            self._err(message, None, COST_EXCEEDED_CODE, hint=COST_EXCEEDED_HINT)

    # ------------------------------------------------------------------ #
    # Error/warning helpers
    # ------------------------------------------------------------------ #

    def _err(
        self,
        message: str,
        node: Optional[n.ASTNode],
        code: str,
        hint: Optional[str] = None,
    ) -> None:
        self.result.errors.append(
            ValidationError(
                message=message,
                location=node.location if node else None,
                code=code,
                hint=hint,
            )
        )

    def _warn(
        self,
        message: str,
        node: Optional[n.ASTNode],
        code: str,
        hint: Optional[str] = None,
    ) -> None:
        self.result.warnings.append(
            ValidationWarning(
                message=message,
                location=node.location if node else None,
                code=code,
                hint=hint,
            )
        )

    def _suggest(self, value: str, candidates: Set[str]) -> Optional[str]:
        if value in candidates:
            return None
        matches = get_close_matches(value, list(candidates), n=1, cutoff=0.6)
        return matches[0] if matches else None

    # ------------------------------------------------------------------ #
    # Visitors
    # ------------------------------------------------------------------ #

    def _visit(self, node: Optional[n.ASTNode]) -> None:
        if node is None:
            return
        handler = getattr(self, f"_visit_{type(node).__name__}", None)
        if handler:
            handler(node)

    def _check_import(self, node: n.Import) -> None:
        # Duplicate import -> warning
        if node.path in self._defined_names:
            self._warn(f"Duplicate import of '{node.path}'", node, "W004")
        else:
            self._defined_names.add(node.path)

    # -- declarations -------------------------------------------------- #
    def _visit_VariableDecl(self, node: n.VariableDecl) -> None:
        if node.name == "":
            self._err("Empty variable name", node, "E040")
            return
        self._check_expression(node.value)
        kind = SymbolKind.CONST if node.const else SymbolKind.VARIABLE
        sym = Symbol(
            node.name,
            _expr_type(node.value),
            definition=node,
            kind=kind,
            mutable=not node.const,
        )
        try:
            self.symbols.define(sym)
        except SymbolAlreadyDefinedError:
            self._err(
                f"Variable '{node.name}' is already defined in this scope", node, "E001"
            )

    def _check_expression(self, expr: Optional[n.Expression]) -> None:
        if expr is None:
            return
        if isinstance(expr, n.Identifier):
            sym = self.symbols.lookup(expr.name)
            if sym is None:
                self._err(
                    f"Undefined variable '{expr.name}'",
                    expr,
                    "E001",
                    hint=self._suggest_identifier(expr.name),
                )
            else:
                sym.used = True
        elif isinstance(expr, n.BinaryOp):
            self._check_expression(expr.left)
            self._check_expression(expr.right)
        elif isinstance(expr, n.UnaryOp):
            self._check_expression(expr.operand)
        elif isinstance(expr, n.Call):
            self._check_expression(expr.callee)
            for a in expr.args:
                self._check_expression(a)
        elif isinstance(expr, n.Index):
            self._check_expression(expr.obj)
            self._check_expression(expr.index)
        elif isinstance(expr, n.Attribute):
            self._check_expression(expr.obj)
        elif isinstance(expr, n.List):
            for i in expr.items:
                self._check_expression(i)
        elif isinstance(expr, n.Map):
            for e in expr.entries:
                self._check_expression(e.key)
                self._check_expression(e.value)
        elif isinstance(expr, n.IfExpr):
            self._check_expression(expr.condition)
            self._check_expression(expr.then_branch)
            self._check_expression(expr.else_branch)
        elif isinstance(expr, n.MatchExpr):
            self._check_expression(expr.subject)
            for arm in expr.arms:
                self._check_expression(arm.body)

    def _suggest_identifier(self, name: str) -> Optional[str]:
        candidates = [s for s in self.symbols.get_all_definitions() if s != name]
        if not candidates:
            return None
        matches = get_close_matches(name, candidates, n=1, cutoff=0.6)
        return f"Did you mean '{matches[0]}'?" if matches else None

    # -- service ------------------------------------------------------- #
    def _visit_ServiceDef(self, node: n.ServiceDef) -> None:
        self._register_definition(node, SymbolKind.SERVICE)
        if node.image is None and node.build is None:
            self._err(
                f"Service '{node.name}' must define either 'image' or 'build'",
                node,
                "E010",
            )
        # image may be a variable reference -> check it is defined
        self._check_expression(cast(Any, node.image))
        if node.build is not None and node.build.context is not None:
            self._check_expression(cast(Any, node.build.context))
        self._check_ports(node)
        if node.replicas is not None and node.replicas < 1:
            self._err(
                f"Service '{node.name}' must have at least 1 replica", node, "E011"
            )
        if node.replicas == 1 and node.strategy and node.strategy.type == "rolling":
            self._warn(
                f"Service '{node.name}' uses rolling strategy with replicas=1",
                node,
                "W002",
            )
        depends = _string_list(node.depends)
        for dep in depends:
            if dep not in self._defined_names:
                self._warn(
                    f"Service '{node.name}' depends on undefined service '{dep}'",
                    node,
                    "W001",
                )
        # v0.4.5: depends_on is a hard contract — an undeclared target is an
        # error (the legacy `depends` list keeps its W001 warning above for
        # backward compatibility).
        for dep in _string_list(node.depends_on):
            if dep not in self._program_defs:
                self._err(
                    f"Service '{node.name}' depends on '{dep}' via depends_on, "
                    f"but '{dep}' is not declared in this file",
                    node,
                    "DEPENDENCY_NOT_FOUND",
                    hint=f"Declare service '{dep}' or fix spelling in depends_on",
                )
        if node.network_policy:
            self._check_network_policy(node)
        if node.schedule:
            for slot in node.schedule.slots:
                if slot.cron and not _is_valid_cron(slot.cron):
                    self._err(
                        f"Invalid schedule cron '{slot.cron}' for service '{node.name}'",  # noqa: E501
                        node,
                        "E010",
                        hint='Cron format: minute hour day month weekday, e.g. "0 9 * * 1-5"',  # noqa: E501
                    )
                if slot.config.replicas < 1:
                    self._err(
                        f"Schedule replicas must be >= 1 for service '{node.name}'",
                        node,
                        "E011",
                        hint="Use replicas: 1 or higher",
                    )
        self._check_env(node.env)

    def _check_network_policy(self, node: n.ServiceDef) -> None:
        np_ = node.network_policy
        if np_ is None:
            return
        known = self._defined_names
        for ref in list(np_.allow_from) + list(np_.allow_egress):
            if ref != "*" and ref not in known:
                self._warn(
                    f"Service '{node.name}' network policy references "
                    f"undefined service '{ref}'",
                    node,
                    "W001",
                )

    def _check_ports(self, node: n.ServiceDef) -> None:
        seen: Set[int] = set()
        for p in node.ports:
            if p.target is None:
                continue
            if p.target == 0 or p.target > 65535:
                self._err(f"Port {p.target} out of range (1-65535)", node, "E012")
            if p.target in seen:
                self._err(
                    f"Duplicate port {p.target} in service '{node.name}'", node, "E013"
                )
            seen.add(p.target)

    def _check_env(self, env: Tuple[n.EnvEntry, ...]) -> None:
        names: Set[str] = set()
        for e in env:
            if e.name in names:
                self._err(f"Duplicate env variable '{e.name}'", e, "E014")
            names.add(e.name)
            if e.value is not None:
                self._check_expression(e.value)

    # -- database ------------------------------------------------------ #
    def _visit_DatabaseDef(self, node: n.DatabaseDef) -> None:
        self._register_definition(node, SymbolKind.DATABASE)
        if node.type not in _VALID_DB:
            hint = self._suggest(node.type, _VALID_DB)
            self._err(
                f"Unknown database type '{node.type}'",
                node,
                "E020",
                hint=f"Did you mean '{hint}'?" if hint else None,
            )
        if node.replicas is not None and node.replicas < 1:
            self._err(
                f"Database '{node.name}' must have at least 1 replica", node, "E021"
            )
        if node.backup and node.backup.schedule:
            if not _is_valid_cron(node.backup.schedule):
                self._err(
                    f"Invalid backup cron schedule '{node.backup.schedule}'",
                    node.backup,
                    "E022",
                )
        seen: Set[str] = set()
        for u in node.users:
            if u.name in seen:
                self._err(f"Duplicate database user '{u.name}'", u, "E023")
            seen.add(u.name)

    # -- cache / queue / storage ---------------------------------------- #
    def _visit_CacheDef(self, node: n.CacheDef) -> None:
        self._register_definition(node, SymbolKind.CACHE)
        if node.type not in _VALID_CACHE:
            hint = self._suggest(node.type, _VALID_CACHE)
            self._err(
                f"Unknown cache type '{node.type}'",
                node,
                "E024",
                hint=f"Did you mean '{hint}'?" if hint else None,
            )

    def _visit_QueueDef(self, node: n.QueueDef) -> None:
        self._register_definition(node, SymbolKind.QUEUE)
        if node.type not in _VALID_QUEUE:
            hint = self._suggest(node.type, _VALID_QUEUE)
            self._err(
                f"Unknown queue type '{node.type}'",
                node,
                "E025",
                hint=f"Did you mean '{hint}'?" if hint else None,
            )

    def _visit_StorageDef(self, node: n.StorageDef) -> None:
        self._register_definition(node, SymbolKind.STORAGE)
        if node.type not in _VALID_STORAGE:
            hint = self._suggest(node.type, _VALID_STORAGE)
            self._err(
                f"Unknown storage type '{node.type}'",
                node,
                "E026",
                hint=f"Did you mean '{hint}'?" if hint else None,
            )

    # -- network / secret / config -------------------------------------- #
    def _visit_NetworkDef(self, node: n.NetworkDef) -> None:
        self._register_definition(node, SymbolKind.NETWORK)

    def _visit_NetworkPolicyDef(self, node: n.NetworkPolicyDef) -> None:
        self._register_definition(node, SymbolKind.NETWORK_POLICY)
        # every workload named by the policy must be declared in this file
        # (services and resources alike; forward references are fine)
        refs = [node.target, *node.allow_ingress, *node.allow_egress]
        for ref in refs:
            if ref and ref not in self._program_defs:
                self._err(
                    f"Network policy '{node.name}' references '{ref}', "
                    "which is not declared in this file",
                    node,
                    "POLICY_TARGET_NOT_FOUND",
                    hint=f"Declare service '{ref}' or fix the "
                    "network_policy reference",
                )
        if node.block_all_ingress and node.allow_ingress:
            self._warn(
                f"Network policy '{node.name}' sets 'block_all_ingress' "
                "but also declares 'allow_ingress' rules; the allow rules "
                "take precedence over the blanket block",
                node,
                "W012",
                hint="Drop 'block_all_ingress' or empty 'allow_ingress'",
            )

    _VALID_STORE_PROVIDERS = ("vault", "aws", "gcp", "kubernetes")

    def _visit_SecretStoreDef(self, node: n.SecretStoreDef) -> None:
        self._register_definition(node, SymbolKind.SECRET_STORE)
        if node.provider not in self._VALID_STORE_PROVIDERS:
            self._err(
                f"Secret store '{node.name}' has invalid provider "
                f"'{node.provider or '(empty)'}'",
                node,
                "INVALID_STORE_PROVIDER",
                hint="Supported providers: "
                + ", ".join(self._VALID_STORE_PROVIDERS),
            )

    def _visit_SecretDef(self, node: n.SecretDef) -> None:
        self._register_definition(node, SymbolKind.SECRET)
        if node.store is not None and node.store not in self._secret_stores:
            self._err(
                f"Secret '{node.name}' references secret store "
                f"'{node.store}', but no secret_store '{node.store}' "
                "is declared in this file",
                node,
                "STORE_NOT_FOUND",
                hint=f'Declare secret_store "{node.store}" or fix the '
                "store reference",
            )
        seen: Set[str] = set()
        for e in node.entries:
            if e.name in seen:
                self._err(f"Duplicate secret key '{e.name}'", e, "E027")
            seen.add(e.name)

    def _visit_CustomResourceSpec(self, node: n.CustomResourceSpec) -> None:
        self._register_definition(node, SymbolKind.CUSTOM_RESOURCE)
        # Not fatal: the backends fall back to sensible defaults, but a
        # proper CRD manifest needs both coordinates.
        if not node.api_version:
            self._warn(
                f"Custom resource '{node.name}' does not declare "
                "'api_version'",
                node,
                "W010",
                hint='Add api_version: "<group>/<version>" '
                '(e.g. "stable.example.com/v1")',
            )
        if not node.kind:
            self._warn(
                f"Custom resource '{node.name}' does not declare 'kind'",
                node,
                "W011",
                hint='Add kind: "<Kind>" (e.g. "MyKind")',
            )
        self._check_custom_resource_keys(node.properties, node)

    def _check_custom_resource_keys(
        self, props: Iterable[Tuple[str, n.Expression]], node: n.CustomResourceSpec
    ) -> None:
        """Flag duplicate keys at every nesting level of a custom resource.

        Kubernetes applies last-one-wins semantics to duplicate YAML keys,
        which silently drops user configuration — better to flag it here.
        """
        seen: Set[str] = set()
        for key, value in props:
            if key in seen:
                self._err(
                    f"Duplicate property '{key}' in custom resource "
                    f"'{node.name}'",
                    node,
                    "E050",
                )
            seen.add(key)
            if isinstance(value, n.Map):
                nested = []
                for e in value.entries:
                    if isinstance(e.key, n.Identifier):
                        nested.append((e.key.name, e.value))
                    elif isinstance(e.key, n.Literal):
                        nested.append((str(e.key.value), e.value))
                self._check_custom_resource_keys(nested, node)

    def _visit_ConfigDef(self, node: n.ConfigDef) -> None:
        self._register_definition(node, SymbolKind.CONFIG)

    # -- pipeline ------------------------------------------------------- #
    def _visit_PipelineDef(self, node: n.PipelineDef) -> None:
        self._register_definition(node, SymbolKind.PIPELINE)
        stage_names = [s.name for s in node.stages]
        if not node.stages:
            self._warn(f"Pipeline '{node.name}' has no stages", node, "W003")
        for st in node.stages:
            for need in st.needs:
                if need not in stage_names:
                    self._err(
                        f"Stage '{st.name}' depends on undefined stage '{need}'",
                        st,
                        "E030",
                    )
        if _has_cycle(node.stages):
            self._err(
                f"Pipeline '{node.name}' has a cyclic stage dependency", node, "E031"
            )
        if (
            node.trigger
            and node.trigger.schedule
            and not _is_valid_cron(node.trigger.schedule)
        ):
            self._err(f"Invalid cron schedule '{node.trigger.schedule}'", node, "E032")

    # -- environment / cluster ------------------------------------------ #
    def _visit_EnvironmentDef(self, node: n.EnvironmentDef) -> None:
        self._register_definition(node, SymbolKind.ENVIRONMENT)
        if node.provider and node.provider not in _VALID_PROVIDER:
            hint = self._suggest(node.provider, _VALID_PROVIDER)
            self._err(
                f"Unknown cloud provider '{node.provider}'",
                node,
                "E033",
                hint=f"Did you mean '{hint}'?" if hint else None,
            )

    def _visit_ClusterDef(self, node: n.ClusterDef) -> None:
        self._register_definition(node, SymbolKind.CLUSTER)
        if node.provider and node.provider not in _VALID_PROVIDER:
            hint = self._suggest(node.provider, _VALID_PROVIDER)
            self._err(
                f"Unknown cloud provider '{node.provider}'",
                node,
                "E033",
                hint=f"Did you mean '{hint}'?" if hint else None,
            )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _register_definition(self, node: n.ASTNode, kind: SymbolKind) -> None:
        name = getattr(node, "name", "")
        if name == "":
            self._err("Empty definition name", node, "E040")
            return
        sym = Symbol(name, T.ANY, definition=node, kind=kind, mutable=False)
        try:
            self.symbols.define(sym)
        except SymbolAlreadyDefinedError:
            self._err(f"Duplicate global definition '{name}'", node, "E002")
        self._defined_names.add(name)

    def _check_unused(self) -> None:
        for name, sym in self.symbols.get_all_definitions().items():
            if sym.kind not in (SymbolKind.VARIABLE, SymbolKind.CONST) or sym.used:
                continue
            # skip prelude constants (defined automatically, not by the user)
            loc = getattr(sym.definition, "location", None)
            if loc is not None and loc.file == "<prelude>":
                continue
            self._warn(f"Unused variable '{name}'", sym.definition, "W003")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _string_list(value: Any) -> Tuple[str, ...]:
    """Coerce an ``n.List``/tuple of literals into a tuple of strings."""
    items = value.items if isinstance(value, n.List) else (value or ())
    out = []
    for it in items:
        if isinstance(it, n.Literal):
            out.append(str(it.value))
        else:
            out.append(str(it))
    return tuple(out)


def _is_valid_cron(spec: str) -> bool:
    """Validate a 5-field cron expression in a lightweight way."""
    parts = spec.split()
    if len(parts) != 5:
        return False
    try:
        for part in parts:
            for field in part.split(","):
                field = field.strip()
                if not field:
                    return False
                if field == "*":
                    continue
                # allow ranges like 1-5, steps like */5, single values, names
                if "/" in field:
                    field = field.split("/")[0]
                if "-" in field:
                    lo, hi = field.split("-")
                    if not (lo.isdigit() and hi.isdigit()):
                        return False
                    continue
                if not (field.isdigit() or field.isalpha()):
                    return False
        return True
    except Exception:
        return False


def _has_cycle(stages: Tuple[n.StageSpec, ...]) -> bool:
    """Detect cycles in the stage dependency graph (DFS)."""
    graph: Dict[str, List[str]] = {s.name: list(s.needs) for s in stages}

    def visit(name: str, path: Set[str]) -> bool:
        if name in path:
            return True
        path.add(name)
        for dep in graph.get(name, []):
            if dep in graph and visit(dep, path):
                return True
        path.remove(name)
        return False

    for name in graph:
        if visit(name, set()):
            return True
    return False
