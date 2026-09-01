"""Auto-fix engine for common security & reliability warnings (v0.9.0).

Rules V1 — each is a pure AST→AST transform (frozen dataclasses replaced,
never mutated), so the result can be re-printed safely by
:mod:`infra.analyzer.source_editor`:

* **SEC001** hardcoded secret in ``env`` → ``from secret "auto_secrets".<VAR>``
  plus a top-of-file ``secret_store "auto_secrets"`` block (created once,
  only when missing; provider ``kubernetes`` — re-point it at your real
  secrets backend).
* **SEC003** mutable image tag → *advisory only*: an inline ``# FIXME``
  comment is emitted by the source editor (we never guess a version).
* **REL003** missing memory limit → ``resources { limits { memory: 512Mi } }``
  (default configurable, preserves existing requests/limits entries).
* **REL004** missing health check → ``health http("/health")`` (services
  without a declared port are skipped).
* **REL006** database without backup → ``backup { enabled: true … }``.
* **REL009** missing preStop hook (replicas > 1 only, mirroring the
  checker) → ``lifecycle { preStop { exec: ["sleep", "5"] } }``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, List, Optional, Sequence, Tuple

from infra.analyzer.security import MUTABLE_TAGS, SECRET_ENV_NAMES
from infra.parser import ast_nodes as n

#: Rule ids understood by ``--only`` (order = application order).
FIXABLE_CODES: Tuple[str, ...] = (
    "SEC001",
    "SEC003",
    "REL003",
    "REL004",
    "REL006",
    "REL009",
)

#: Default memory limit injected for REL003.
DEFAULT_MEMORY = "512Mi"

#: Name of the secret store generated for SEC001 fixes.
AUTO_SECRET_STORE = "auto_secrets"

#: Inline comment appended next to mutable-tag images (SEC003 advisory).
SEC003_COMMENT = (
    "# FIXME: pin to a specific version, e.g., :1.25.3 "
    "(image version is your responsibility)"
)

_MEMORY_RE = re.compile(r"^(\d+(?:\.\d+)?)(Ki|Mi|Gi|Ti)$")


@dataclass(frozen=True)
class Fix:
    """One applied (or skipped) auto-fix action."""

    code: str
    target: str
    description: str


@dataclass
class FixResult:
    """Outcome of :func:`apply_fixes`."""

    program: n.Program
    applied: List[Fix] = field(default_factory=list)
    skipped: List[Fix] = field(default_factory=list)
    #: ``(image, comment)`` pairs to append next to the image line on print.
    comments: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """True when any rule actually modified the program or comments."""
        return bool(self.applied or self.comments)


def parse_memory_value(text: str) -> n.ResourceValue:
    """Parse ``512Mi``-style memory into a ResourceValue.

    Raises ``ValueError`` for anything outside Ki/Mi/Gi/Ti quantities —
    the CLI converts that into a friendly error message.
    """
    match = _MEMORY_RE.match(text.strip())
    if match is None:
        raise ValueError(
            f"Invalid memory value '{text}'. Expected e.g. 256Mi, 1Gi "
            "(units: Ki, Mi, Gi, Ti)."
        )
    raw = match.group(1)
    value: float = float(raw) if "." in raw else float(int(raw))
    return n.ResourceValue(value=value, unit=match.group(2))


def _has_memory_limit(svc: n.ServiceDef) -> bool:
    if svc.resources is None:
        return False
    limits = svc.resources.limits
    if limits is not None and limits.memory is not None:
        return True
    requests = svc.resources.requests
    return requests is not None and requests.memory is not None


def _has_mutable_tag(image: Optional[str]) -> bool:
    """Mirror of :meth:`SecurityChecker._sec003_mutable_tag`."""
    if not image or "@sha256:" in image:
        return False
    tag = image.split(":")[-1] if ":" in image else "latest"
    return tag in MUTABLE_TAGS


#: Provider used by the generated store. The validator accepts only
#: vault/aws/gcp/kubernetes; ``kubernetes`` keeps manifests deployable
#: out-of-the-box while users re-point the store at their real backend.
AUTO_STORE_PROVIDER = "kubernetes"


def _ensure_auto_store(program: n.Program) -> n.Program:
    """Prepend the shared secret store unless one already exists."""
    for stmt in program.statements:
        if isinstance(stmt, n.SecretStoreDef) and stmt.name == AUTO_SECRET_STORE:
            return program
    store = n.SecretStoreDef(
        name=AUTO_SECRET_STORE, provider=AUTO_STORE_PROVIDER
    )
    return replace(program, statements=(store, *program.statements))


# --------------------------------------------------------------------------- #
# Rule implementations (SEC001, SEC003, REL003, REL004, REL006, REL009)
# --------------------------------------------------------------------------- #


def _fix_sec001(
    service: n.ServiceDef, result: FixResult, program: n.Program
) -> n.ServiceDef:
    env: List[n.EnvEntry] = []
    changed = False
    for entry in service.env:
        if (
            entry.value is not None
            and isinstance(entry.value, n.Literal)
            and isinstance(entry.value.value, str)
            and entry.name.lower() in SECRET_ENV_NAMES
        ):
            env.append(
                replace(
                    entry,
                    value=None,
                    from_secret=f"{AUTO_SECRET_STORE}.{entry.name}",
                )
            )
            result.applied.append(
                Fix(
                    "SEC001",
                    service.name,
                    f"env {entry.name} now references "
                    f'secret "{AUTO_SECRET_STORE}".{entry.name}',
                )
            )
            changed = True
        else:
            env.append(entry)
    if not changed:
        return service
    return replace(service, env=tuple(env))


def _fix_rel003(
    service: n.ServiceDef, memory: n.ResourceValue
) -> Optional[n.ServiceDef]:
    if _has_memory_limit(service):
        return None
    resources = service.resources
    if resources is None:
        new_resources = n.ResourcesSpec(
            limits=n.ResourceMap(memory=memory),
        )
    else:
        limits = resources.limits
        if limits is None:
            new_resources = replace(
                resources, limits=n.ResourceMap(memory=memory)
            )
        else:
            new_resources = replace(
                resources, limits=replace(limits, memory=memory)
            )
    return replace(service, resources=new_resources)


def _fix_rel004(service: n.ServiceDef) -> Optional[n.ServiceDef]:
    if service.health is not None or service.probes is not None:
        return None
    if not service.ports:
        return None
    return replace(
        service,
        health=n.HealthSpec(
            kind="http",
            path="/health",
            interval=n.Duration(value=30.0, unit="s"),
            timeout=n.Duration(value=5.0, unit="s"),
        ),
    )


def _fix_rel009(service: n.ServiceDef) -> Optional[n.ServiceDef]:
    if service.replicas <= 1:
        return None
    lifecycle = service.lifecycle
    if lifecycle is not None and lifecycle.pre_stop is not None:
        return None
    hook = n.HookSpec(kind="exec", command=("sleep", "5"))
    if lifecycle is None:
        new_lifecycle = n.LifecycleSpec(pre_stop=hook)
    else:
        new_lifecycle = replace(lifecycle, pre_stop=hook)
    return replace(service, lifecycle=new_lifecycle)


def _fix_rel006(db: n.DatabaseDef) -> Optional[n.DatabaseDef]:
    backup = db.backup
    if backup is not None and backup.enabled:
        return None
    if backup is None:
        new_backup = n.BackupSpec(
            enabled=True,
            schedule="0 2 * * *",
            retention=n.Duration(value=7.0, unit="d"),
        )
    else:
        new_backup = replace(
            backup,
            enabled=True,
            schedule=backup.schedule or "0 2 * * *",
            retention=backup.retention or n.Duration(value=7.0, unit="d"),
        )
    return replace(db, backup=new_backup)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def apply_fixes(
    program: n.Program,
    *,
    only: Optional[Sequence[str]] = None,
    default_memory: str = DEFAULT_MEMORY,
) -> FixResult:
    """Apply every enabled auto-fix rule to *program* (pure, AST-in/AST-out).

    ``only`` restricts the rule set (``None`` = all rules). Comments for
    SEC003 are collected, never applied to the AST itself.
    """
    wanted = set(only) if only is not None else set(FIXABLE_CODES)
    result = FixResult(program=program)
    memory = parse_memory_value(default_memory)

    def enabled(code: str) -> bool:
        return code in wanted

    new_statements: List[Any] = []
    used_sec001 = False
    for stmt in program.statements:
        if isinstance(stmt, n.ServiceDef):
            svc = stmt
            if enabled("SEC001"):
                before = len(result.applied)
                svc = _fix_sec001(svc, result, program)
                used_sec001 = used_sec001 or len(result.applied) > before
            if enabled("SEC003") and _has_mutable_tag(svc.image):
                result.comments.append((svc.image or "", SEC003_COMMENT))
                result.applied.append(
                    Fix(
                        "SEC003",
                        svc.name,
                        "added inline FIXME comment to pin the image version",
                    )
                )
            if enabled("REL003"):
                fixed = _fix_rel003(svc, memory)
                if fixed is not None:
                    result.applied.append(
                        Fix("REL003", svc.name, "injected memory limit")
                    )
                    svc = fixed
            if enabled("REL004"):
                fixed = _fix_rel004(svc)
                if fixed is not None:
                    result.applied.append(
                        Fix("REL004", svc.name, "injected health check")
                    )
                    svc = fixed
                elif (
                    svc.health is None
                    and svc.probes is None
                    and not svc.ports
                ):
                    result.skipped.append(
                        Fix(
                            "REL004",
                            svc.name,
                            "skipped: no port declared, cannot pick a health "
                            "endpoint",
                        )
                    )
            if enabled("REL009"):
                fixed = _fix_rel009(svc)
                if fixed is not None:
                    result.applied.append(
                        Fix("REL009", svc.name, "injected preStop hook")
                    )
                    svc = fixed
            new_statements.append(svc)
            continue
        if isinstance(stmt, n.DatabaseDef):
            db = stmt
            if enabled("REL006"):
                db_fixed = _fix_rel006(db)
                if db_fixed is not None:
                    result.applied.append(
                        Fix("REL006", db.name, "enabled daily backups")
                    )
                    db = db_fixed
            new_statements.append(db)
            continue
        new_statements.append(stmt)

    result.program = replace(program, statements=tuple(new_statements))
    if used_sec001:
        result.program = _ensure_auto_store(result.program)
    return result


__all__ = [
    "AUTO_SECRET_STORE",
    "DEFAULT_MEMORY",
    "FIXABLE_CODES",
    "SEC003_COMMENT",
    "Fix",
    "FixResult",
    "apply_fixes",
    "parse_memory_value",
]
