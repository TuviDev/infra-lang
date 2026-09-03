"""Deployment engine: plan → apply → rollout-verify → auto-rollback → history.

**Fully offline for tests:** every external tool invocation (`docker`,
`kubectl`, `helm`, `terraform`) goes through the module-level
``subprocess.run`` call inside :func:`run_command`, so tests patch
``subprocess.run`` (or ``infra.deploy.engine.subprocess.run``) and never
touch a real cluster.

State layout (anchored at the caller-provided *state_root*)::

    .infra-state/<project>/
        history/<revision>.json          # one record per deploy attempt
        snapshots/<revision>/<files>     # compiled manifests per revision
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, List, Optional, Sequence, Tuple

from infra.errors.exceptions import InfraError

TARGETS = ("compose", "kubernetes", "helm", "terraform")

PLANNED = "planned"
SUCCESS = "success"
FAILED = "failed"
ROLLED_BACK = "rolled-back"
RESTORED = "restored"
_ALIASES = {"k8s": "kubernetes", "docker": "compose", "tf": "terraform"}
_TARGET_TOOL = {
    "compose": "docker",
    "kubernetes": "kubectl",
    "helm": "helm",
    "terraform": "terraform",
}

#: Subprocess guard so a hung tool never blocks a deploy forever.
_DEFAULT_TIMEOUT = 120


class DeployTargetError(InfraError, ValueError):
    """Unsupported deploy target.

    Inherits :class:`ValueError` so existing ``except ValueError`` callers
    keep working while the error also belongs to the unified
    :class:`infra.errors.exceptions.InfraError` family.
    """


class RevisionNotFoundError(InfraError, LookupError):
    """Requested revision (or its snapshot) is absent from local state.

    Inherits :class:`LookupError` for backwards compatibility with existing
    callers; it is also an :class:`InfraError`.
    """


def canonical_target(name: str) -> str:
    """Normalise target aliases (``k8s``/``docker``/``tf``) or raise."""
    canonical = _ALIASES.get(name.lower(), name.lower())
    if canonical not in TARGETS:
        raise DeployTargetError(
            message=f"Unsupported target '{name}'. Valid targets: {', '.join(TARGETS)}"
        )
    return canonical


def target_tool(target: str) -> str:
    return _TARGET_TOOL[canonical_target(target)]


def have_tool(binary: str) -> bool:
    """Return True when *binary* is on PATH."""
    return shutil.which(binary) is not None


def compile_hash(files: Dict[str, str]) -> str:
    """A stable sha256 over the compiled manifest set."""
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(files[name].encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


@dataclass(frozen=True)
class StepResult:
    """The outcome of one external command (or a planned step)."""

    label: str
    command: Tuple[str, ...]
    returncode: Optional[int] = None
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "command": list(self.command),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "StepResult":
        return StepResult(
            label=str(data["label"]),
            command=tuple(str(c) for c in data["command"]),
            returncode=data.get("returncode"),
            stdout=str(data.get("stdout", "")),
            stderr=str(data.get("stderr", "")),
        )


def run_command(
    cmd: Sequence[str], *, cwd: Optional[Path] = None, timeout: float
) -> StepResult:
    """Run *cmd* with ``subprocess.run``; never raises (timeout→rc None)."""
    label = " ".join(cmd)
    try:
        proc = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
        )
    except subprocess.TimeoutExpired as exc:
        return StepResult(
            label=label,
            command=tuple(cmd),
            returncode=None,
            stderr=f"timed out after {timeout}s",
            stdout=str(exc.stdout or ""),
        )
    except OSError as exc:  # missing binary, permission errors, ...
        return StepResult(
            label=label,
            command=tuple(cmd),
            returncode=None,
            stderr=str(exc),
        )
    return StepResult(
        label=label,
        command=tuple(cmd),
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )


# --------------------------------------------------------------------------- #
# command builders (per target)
# --------------------------------------------------------------------------- #


def _chart_dir(manifest_dir: Path, release: str) -> Path:
    chart = manifest_dir / release
    if chart.is_dir():
        return chart
    return manifest_dir


def apply_command_set(
    target: str, manifest_dir: Path, release: str
) -> List[Tuple[List[str], Optional[Path]]]:
    """``(cmd, cwd)`` pairs that apply *manifest_dir* for *target*."""
    canonical = canonical_target(target)
    if canonical == "compose":
        return [
            (
                [
                    "docker",
                    "compose",
                    "-f",
                    str(manifest_dir / "docker-compose.yml"),
                    "up",
                    "-d",
                ],
                None,
            )
        ]
    if canonical == "kubernetes":
        return [(["kubectl", "apply", "-f", str(manifest_dir / "infra.yaml")], None)]
    if canonical == "helm":
        chart = _chart_dir(manifest_dir, release)
        return [(["helm", "upgrade", "--install", release, str(chart)], None)]
    return [(["terraform", "apply", "-auto-approve"], manifest_dir)]


def rollout_command_set(
    target: str,
    manifest_dir: Path,
    release: str,
    service_names: Sequence[str],
    timeout: int,
) -> List[Tuple[List[str], Optional[Path]]]:
    """``(cmd, cwd)`` pairs that verify the rollout for *target*."""
    canonical = canonical_target(target)
    if canonical == "compose":
        return [
            (
                [
                    "docker",
                    "compose",
                    "-f",
                    str(manifest_dir / "docker-compose.yml"),
                    "ps",
                    "--format",
                    "json",
                ],
                None,
            )
        ]
    if canonical == "kubernetes":
        return [
            (
                [
                    "kubectl",
                    "rollout",
                    "status",
                    f"deployment/{name}",
                    f"--timeout={timeout}s",
                ],
                None,
            )
            for name in service_names
        ]
    if canonical == "helm":
        return [(["helm", "status", release], None)]
    return []  # terraform: the apply exit code IS the rollout signal


def undo_command_set(
    target: str,
    manifest_dir: Path,
    release: str,
    service_names: Sequence[str],
) -> List[Tuple[List[str], Optional[Path]]]:
    """Tool-native undo pairs when no previous snapshot can be re-applied."""
    canonical = canonical_target(target)
    if canonical == "kubernetes":
        return [
            (["kubectl", "rollout", "undo", f"deployment/{name}"], None)
            for name in service_names
        ]
    if canonical == "helm":
        return [(["helm", "rollback", release], None)]
    return []


# --------------------------------------------------------------------------- #
# records & history
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DeployRecord:
    """One deployment attempt (plan, apply, rollback or restore)."""

    revision: str
    timestamp: str
    project: str
    target: str
    tool: str
    status: str
    duration_s: float
    compile_hash: str
    environment: str = ""
    service_names: Tuple[str, ...] = ()
    files: Tuple[str, ...] = ()
    steps: Tuple[StepResult, ...] = ()
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "revision": self.revision,
            "timestamp": self.timestamp,
            "project": self.project,
            "target": self.target,
            "tool": self.tool,
            "status": self.status,
            "duration_s": self.duration_s,
            "compile_hash": self.compile_hash,
            "environment": self.environment,
            "service_names": list(self.service_names),
            "files": list(self.files),
            "steps": [s.to_dict() for s in self.steps],
            "message": self.message,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "DeployRecord":
        return DeployRecord(
            revision=str(data["revision"]),
            timestamp=str(data["timestamp"]),
            project=str(data["project"]),
            target=str(data["target"]),
            tool=str(data["tool"]),
            status=str(data["status"]),
            duration_s=float(data.get("duration_s", 0.0)),
            compile_hash=str(data.get("compile_hash", "")),
            environment=str(data.get("environment", "")),
            service_names=tuple(str(v) for v in data.get("service_names", [])),
            files=tuple(str(v) for v in data.get("files", [])),
            steps=tuple(StepResult.from_dict(s) for s in data.get("steps", [])),
            message=str(data.get("message", "")),
        )


def project_dir(state_root: Path, project: str) -> Path:
    return state_root / project


def history_dir(state_root: Path, project: str) -> Path:
    return project_dir(state_root, project) / "history"


def snapshot_dir(state_root: Path, project: str, revision: str) -> Path:
    return project_dir(state_root, project) / "snapshots" / revision


def _posix_key(name: str) -> str:
    """Normalize a manifest-relative key to forward slashes on every OS.

    Keys stored in snapshot dictionaries and history JSON must be
    byte-identical on Windows, macOS and Linux (FILAR 2) — otherwise
    ``load_snapshot`` round-trips ``a\\b.tf`` on Windows but ``a/b.tf``
    elsewhere.
    """
    return PureWindowsPath(name).as_posix()


def _read_history_files(state_root: Path, project: str) -> List[Path]:
    directory = history_dir(state_root, project)
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.json"))


def list_history(state_root: Path, project: str) -> List[DeployRecord]:
    """All records for *project*, oldest first (corrupt files skipped).

    Robustness contract: files that are unreadable, malformed JSON, or valid
    JSON that does not decode into a :class:`DeployRecord` (wrong shape,
    missing keys, non-coercible fields) are skipped instead of crashing the
    listing — a hand-edited or torn ``.infra-state`` file must never break
    `infra deploy history` / rollback lookups.
    """
    records: List[DeployRecord] = []
    for path in _read_history_files(state_root, project):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            records.append(DeployRecord.from_dict(data))
        except (OSError, ValueError, TypeError, KeyError):
            # OSError: unreadable file; ValueError: bad JSON/UnicodeDecode/
            # non-coercible field; TypeError: JSON shape is not a mapping;
            # KeyError: required record field missing.
            continue
    records.sort(key=lambda r: (r.timestamp, r.revision))
    return records


def next_revision(state_root: Path, project: str) -> str:
    """Next zero-padded revision id derived from existing history."""
    return f"r{len(_read_history_files(state_root, project)) + 1:04d}"


def save_record(state_root: Path, record: DeployRecord, files: Dict[str, str]) -> Path:
    """Persist *record* and the compiled *files* snapshot (utf-8)."""
    history = history_dir(state_root, record.project)
    snapshot = snapshot_dir(state_root, record.project, record.revision)
    history.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        dest = snapshot / _posix_key(name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content.lstrip("\ufeff"), encoding="utf-8")
    path = history / f"{record.revision}.json"
    path.write_text(json.dumps(record.to_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def load_snapshot(
    state_root: Path, project: str, revision: str
) -> Optional[Dict[str, str]]:
    """Read back a stored manifest snapshot (None when missing)."""
    directory = snapshot_dir(state_root, project, revision)
    if not directory.is_dir():
        return None
    return {
        str(path.relative_to(directory).as_posix()): path.read_text(encoding="utf-8")
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def find_record(
    state_root: Path, project: str, revision: str
) -> Optional[DeployRecord]:
    """Exact or unambiguous prefix lookup of a revision id."""
    history = list_history(state_root, project)
    for record in history:
        if record.revision == revision:
            return record
    matches = [r for r in history if r.revision.startswith(revision)]
    return matches[0] if len(matches) == 1 else None


def last_good_revision(state_root: Path, project: str) -> Optional[DeployRecord]:
    """Most recent record with a re-appliable snapshot (success/restored)."""
    for record in reversed(list_history(state_root, project)):
        if record.status in (SUCCESS, RESTORED):
            return record
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _compose_rollout_ok(step: StepResult) -> bool:
    """Compose rollout heuristic: running containers, nothing dead/exited."""
    if not step.ok:
        return False
    lowered = step.stdout.lower()
    return not any(
        marker in lowered for marker in ('"exited"', '"dead"', '"unhealthy"')
    )


def _run_set(
    pairs: Sequence[Tuple[List[str], Optional[Path]]],
    timeout: float,
) -> List[StepResult]:
    return [run_command(cmd, cwd=cwd, timeout=timeout) for cmd, cwd in pairs]


def _first_failure(steps: Sequence[StepResult]) -> Optional[StepResult]:
    return next((s for s in steps if not s.ok), None)


def execute_deploy(
    *,
    project: str,
    target: str,
    files: Dict[str, str],
    service_names: Sequence[str] = (),
    state_root: Path,
    environment: str = "",
    timeout: int = _DEFAULT_TIMEOUT,
    auto_rollback: bool = True,
    apply: bool = True,
) -> DeployRecord:
    """Apply *files* for *project*, verify the rollout, record everything.

    With ``apply=False`` only a PLANNED record is persisted (manifest
    snapshot included) — the engine performs no external calls. On apply or
    rollout failure and ``auto_rollback=True`` the previous successful
    snapshot is re-applied (compose/terraform) or the tool-native undo is
    executed (kubernetes/helm).
    """
    canonical = canonical_target(target)
    revision = next_revision(state_root, project)
    started = time.perf_counter()
    timestamp = _now_iso()
    steps: List[StepResult] = []
    digest = compile_hash(files)
    tool = target_tool(canonical)

    def finish(status: str, message: str) -> DeployRecord:
        record = DeployRecord(
            revision=revision,
            timestamp=timestamp,
            project=project,
            target=canonical,
            tool=tool,
            status=status,
            duration_s=round(time.perf_counter() - started, 3),
            compile_hash=digest,
            environment=environment,
            service_names=tuple(service_names),
            files=tuple(sorted(_posix_key(k) for k in files)),
            steps=tuple(steps),
            message=message,
        )
        save_record(state_root, record, files)
        return record

    if not apply:
        return finish(PLANNED, "dry-run plan recorded")

    manifest_dir = snapshot_dir(state_root, project, revision)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        dest = manifest_dir / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content.lstrip("\ufeff"), encoding="utf-8")

    apply_steps = _run_set(
        apply_command_set(canonical, manifest_dir, project),
        timeout=float(timeout) + 30.0,
    )
    steps.extend(apply_steps)
    failed = _first_failure(apply_steps)
    if failed is None and canonical in ("compose", "kubernetes", "helm"):
        rollout_steps = _run_set(
            rollout_command_set(
                canonical, manifest_dir, project, service_names, timeout
            ),
            timeout=float(timeout) + 30.0,
        )
        steps.extend(rollout_steps)
        failed = _first_failure(rollout_steps)
        if failed is None and canonical == "compose":
            ps = rollout_steps[0]
            if not _compose_rollout_ok(ps):
                failed = ps

    if failed is None:
        return finish(SUCCESS, "deployment applied and rollout verified")

    reason = f"step failed (rc={failed.returncode}): {failed.label}".rstrip()
    if not auto_rollback:
        return finish(FAILED, reason + " (auto-rollback disabled)")

    rollback_steps = _attempt_rollback(
        state_root=state_root,
        project=project,
        canonical=canonical,
        manifest_dir=manifest_dir,
        service_names=service_names,
        timeout=timeout,
    )
    steps.extend(rollback_steps)
    if rollback_steps and _first_failure(rollback_steps) is None:
        return finish(ROLLED_BACK, reason + "; auto-rollback restored previous state")
    return finish(FAILED, reason + "; auto-rollback could not complete")


def _attempt_rollback(
    *,
    state_root: Path,
    project: str,
    canonical: str,
    manifest_dir: Path,
    service_names: Sequence[str],
    timeout: int,
) -> List[StepResult]:
    """Re-apply the previous good snapshot, else tool-native undo."""
    previous = last_good_revision(state_root, project)
    if previous is not None:
        snapshot = snapshot_dir(state_root, project, previous.revision)
        if snapshot.is_dir():
            return _run_set(
                apply_command_set(canonical, snapshot, project),
                timeout=float(timeout) + 30.0,
            )
    return _run_set(
        undo_command_set(canonical, manifest_dir, project, service_names),
        timeout=float(timeout) + 30.0,
    )


def execute_rollback(
    *,
    state_root: Path,
    project: str,
    target: str,
    revision: str,
    timeout: int = _DEFAULT_TIMEOUT,
) -> DeployRecord:
    """Restore and re-apply the manifests of an earlier *revision*.

    Raises :class:`LookupError` when the revision (or its snapshot) does not
    exist — the CLI turns that into a clean exit code 1.
    """
    canonical = canonical_target(target)
    origin = find_record(state_root, project, revision)
    if origin is None:
        raise RevisionNotFoundError(
            message=f"revision '{revision}' not found in history"
        )
    snapshot = load_snapshot(state_root, project, origin.revision)
    if snapshot is None:
        raise RevisionNotFoundError(
            message=f"snapshot for revision '{origin.revision}' is missing"
        )

    new_revision = next_revision(state_root, project)
    started = time.perf_counter()
    timestamp = _now_iso()
    steps: List[StepResult] = []
    manifest_dir = snapshot_dir(state_root, project, new_revision)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    for name, content in snapshot.items():
        dest = manifest_dir / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")

    steps.extend(
        _run_set(
            apply_command_set(canonical, manifest_dir, project),
            timeout=float(timeout) + 30.0,
        )
    )
    failed = _first_failure(steps)
    status = FAILED if failed is not None else RESTORED
    message = (
        f"restored snapshot {origin.revision}"
        if failed is None
        else f"rollback apply failed (rc={failed.returncode}): {failed.label}"
    )
    record = DeployRecord(
        revision=new_revision,
        timestamp=timestamp,
        project=project,
        target=canonical,
        tool=target_tool(canonical),
        status=status,
        duration_s=round(time.perf_counter() - started, 3),
        compile_hash=compile_hash(snapshot),
        environment=origin.environment,
        service_names=origin.service_names,
        files=tuple(sorted(snapshot)),
        steps=tuple(steps),
        message=message,
    )
    save_record(state_root, record, snapshot)
    return record


__all__ = [
    "PLANNED",
    "RESTORED",
    "ROLLED_BACK",
    "SUCCESS",
    "FAILED",
    "TARGETS",
    "DeployRecord",
    "StepResult",
    "apply_command_set",
    "canonical_target",
    "compile_hash",
    "execute_deploy",
    "execute_rollback",
    "find_record",
    "have_tool",
    "last_good_revision",
    "list_history",
    "load_snapshot",
    "next_revision",
    "rollout_command_set",
    "run_command",
    "save_record",
    "snapshot_dir",
    "target_tool",
    "undo_command_set",
]
