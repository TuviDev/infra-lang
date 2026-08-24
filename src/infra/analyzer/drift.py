"""Drift detection: on-disk (generated files) and live (cluster / daemon).

Two complementary checks live here:

* **On-disk drift** (:func:`detect_drift`): users edit the generated manifests
  (e.g. ``infra-out/infra.yaml``) by hand after compiling, which silently
  diverges from the source ``.infra`` file. ``detect_drift`` recompiles the
  source in memory and compares each expected output file against what is on
  disk, reporting any differences as unified diffs.

* **Live drift** (:func:`detect_live_drift`, v0.4.2): the running
  infrastructure itself diverges from the specification — someone scaled a
  Deployment with ``kubectl scale``, hot-patched an image, or restarted a
  Compose stack with edited settings. ``detect_live_drift`` reads the live
  state (``kubectl get`` for Kubernetes, ``docker compose ps`` +
  ``docker inspect`` for Compose — strictly read-only, never mutating) and
  compares replicas, image, ports and declared environment variables against
  the ``.infra`` source, returning a structural :class:`DriftReport`.
"""

from __future__ import annotations

import difflib
import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from infra.backends import get_backend
from infra.parser import ast_nodes as n
from infra.parser import parse_file


@dataclass
class DriftResult:
    """Result of a drift check against on-disk generated output."""

    has_drift: bool
    #: list of (relative file path, unified diff text) for files that differ.
    modified_files: List[Tuple[str, str]] = field(default_factory=list)
    #: relative paths of expected output files that are missing on disk.
    missing_files: List[str] = field(default_factory=list)
    #: target backend used for the comparison.
    target: str = "kubernetes"

    @property
    def clean(self) -> bool:
        """True when there is no drift (no modified and no missing files)."""
        return not self.has_drift


def _unified_diff(name: str, expected: str, actual: str) -> str:
    """Return a unified diff between *expected* and *actual* for *name*."""
    from_lines = expected.splitlines(keepends=True)
    to_lines = actual.splitlines(keepends=True)
    diff = difflib.unified_diff(
        from_lines,
        to_lines,
        fromfile=f"{name} (compiled)",
        tofile=f"{name} (on disk)",
        lineterm="",
    )
    return "".join(diff)


def detect_drift(
    infra_path: Path,
    out_dir: Path,
    target: str = "kubernetes",
) -> DriftResult:
    """Compile *infra_path* for *target* and compare against *out_dir*.

    Returns a :class:`DriftResult`. Raises on parse errors (propagated from the
    parser) or on an unknown backend (``InfraCompileError``).
    """
    path = Path(infra_path)
    out = Path(out_dir)
    program = parse_file(path)
    backend = get_backend(target)
    compiled = backend.compile(program)

    modified: List[Tuple[str, str]] = []
    missing: List[str] = []

    for name, expected in compiled.files.items():
        dest = out / name
        if not dest.exists():
            missing.append(name)
            continue
        actual = dest.read_text(encoding="utf-8")
        if actual != expected:
            modified.append((name, _unified_diff(name, expected, actual)))

    has_drift = bool(missing) or bool(modified)
    return DriftResult(
        has_drift=has_drift,
        modified_files=modified,
        missing_files=missing,
        target=target,
    )


def render_drift(result: DriftResult) -> str:
    """Return a human-readable summary of a drift check result."""
    if result.clean:
        return "No drift detected. On-disk files match source compilation."
    lines: List[str] = []
    if result.missing_files:
        lines.append("Missing generated files on disk:")
        for name in result.missing_files:
            lines.append(f"  - {name}")
    if result.modified_files:
        lines.append("Files differ from source compilation:")
        for name, diff in result.modified_files:
            lines.append(f"  {name}")
            for dline in diff.splitlines():
                lines.append(f"    {dline}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Live drift detection (v0.4.2)
# ---------------------------------------------------------------------------

#: Bounded timeout for the read-only live-state probes (kubectl / docker).
_LIVE_TIMEOUT = 30.0

#: Total wall-clock budget for the *whole* Docker Compose probe sequence
#: (``compose ps`` + one ``docker inspect`` per container). Probes run
#: sequentially; without a global cap a slow / hung daemon would stall the
#: CLI for N containers x per-probe timeout. When the budget is spent, the
#: remaining containers are reported as unprobed (partial state, never
#: false drift) and :attr:`DriftReport.error` explains what happened.
_COMPOSE_PROBE_BUDGET = 10.0

#: Drift item statuses.
STATUS_MODIFIED = "MODIFIED"
STATUS_MISSING = "MISSING"


@dataclass
class DriftItem:
    """A single detected difference between spec and live state."""

    resource: str
    parameter: str
    expected: str
    live: str
    status: str = STATUS_MODIFIED

    def render(self) -> str:
        """Render as ``[DRIFT] app: replicas expected 3, live 1 (MODIFIED)``."""
        return (
            f"[DRIFT] {self.resource}: {self.parameter} expected "
            f"{self.expected}, live {self.live} ({self.status})"
        )

    def to_dict(self) -> Dict[str, str]:
        return {
            "resource": self.resource,
            "parameter": self.parameter,
            "expected": self.expected,
            "live": self.live,
            "status": self.status,
        }


@dataclass
class DriftReport:
    """Structural report of a live drift check."""

    target: str
    items: List[DriftItem] = field(default_factory=list)
    #: Resource names verified as in sync with the specification.
    in_sync: List[str] = field(default_factory=list)
    #: Non-fatal probe error (missing tool, unreachable daemon, bad JSON).
    error: Optional[str] = None

    @property
    def has_drift(self) -> bool:
        return bool(self.items)

    @property
    def clean(self) -> bool:
        return not self.items and self.error is None

    def render_lines(self) -> List[str]:
        """Human-readable ``[DRIFT] ...`` lines for every detected item."""
        return [item.render() for item in self.items]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "has_drift": self.has_drift,
            "in_sync": list(self.in_sync),
            "drift": [item.to_dict() for item in self.items],
            "error": self.error,
        }


@dataclass
class _LiveState:
    """Normalized live state of a single workload."""

    replicas: Optional[int] = None
    image: Optional[str] = None
    ports: Optional[List[int]] = None
    env: Optional[Dict[str, str]] = None


@dataclass
class _ExpectedState:
    """Normalized expected state of a service declared in a .infra file."""

    name: str
    replicas: int
    image: Optional[str]
    ports: List[int]
    env: Dict[str, str]


def _probe(cmd: List[str], timeout: float) -> Tuple[Optional[str], Optional[str]]:
    """Run a read-only probe; return ``(stdout, error)`` — exactly one is set.

    ``subprocess.run`` kills **and reaps** a timed-out child, so a timeout
    never leaves a zombie / orphan process behind. ``TimeoutExpired`` and
    ``CalledProcessError`` are caught explicitly so a single hung step is
    reported as a readable message instead of stalling or crashing the scan.
    """
    label = " ".join(cmd[:2])
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"`{label}` timed out after {timeout:.1f}s"
    except subprocess.CalledProcessError as exc:  # defensive: check=False
        return None, f"`{label}` failed: {exc}"
    except OSError as exc:
        return None, f"`{label}` could not start: {exc.strerror or exc}"
    except subprocess.SubprocessError as exc:
        return None, f"`{label}` failed: {exc}"
    if result.returncode != 0:
        return None, f"`{label}` exited with code {result.returncode}"
    return result.stdout or "", None


def _run_readonly(cmd: List[str], timeout: Optional[float] = None) -> Optional[str]:
    """Run a read-only probe command; return stdout on success, else None.

    Kept for the single-shot probes (``kubectl get``, ``compose ps``) whose
    callers historically map any failure to their "tool unavailable" hint.
    """
    out, _ = _probe(cmd, _LIVE_TIMEOUT if timeout is None else timeout)
    return out


def _expected_services(program: n.Program) -> List[_ExpectedState]:
    """Extract the comparable expected state of every declared service."""
    expected: List[_ExpectedState] = []
    for stmt in program.statements:
        if not isinstance(stmt, n.ServiceDef):
            continue
        ports = [
            int(port)
            for port in (p.target or p.host for p in stmt.ports)
            if port is not None
        ]
        env: Dict[str, str] = {}
        for e in stmt.env:
            # Only plain literal values are comparable against live state;
            # secret/config/field references resolve cluster-side.
            if e.value is not None and isinstance(e.value, n.Literal):
                env[e.name] = str(e.value.value)
        expected.append(
            _ExpectedState(
                name=stmt.name,
                replicas=max(1, int(stmt.replicas or 1)),
                image=stmt.image,
                ports=sorted(ports),
                env=env,
            )
        )
    return expected


# -- Kubernetes live state ---------------------------------------------------


def _kubectl_live_state(namespace: str) -> Optional[Dict[str, _LiveState]]:
    """Fetch live Deployment state from the cluster (read-only).

    Returns a mapping of deployment name -> :class:`_LiveState`, or None when
    kubectl is unavailable or the cluster query fails.
    """
    if shutil.which("kubectl") is None:
        return None
    out = _run_readonly(
        ["kubectl", "get", "deployment,service", "-n", namespace, "-o", "json"]
    )
    if out is None:
        return None
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return None
    live: Dict[str, _LiveState] = {}
    for item in payload.get("items", []):
        if not isinstance(item, dict):
            continue
        if item.get("kind") != "Deployment":
            continue
        name = str(item.get("metadata", {}).get("name", ""))
        if not name:
            continue
        spec = item.get("spec", {}) or {}
        replicas = spec.get("replicas")
        containers = (
            spec.get("template", {}).get("spec", {}).get("containers", []) or []
        )
        image: Optional[str] = None
        ports: List[int] = []
        env: Dict[str, str] = {}
        if containers:
            first = containers[0] or {}
            image = first.get("image")
            for p in first.get("ports", []) or []:
                cp = p.get("containerPort")
                if cp is not None:
                    ports.append(int(cp))
            for entry in first.get("env", []) or []:
                # Only literal `value` entries are comparable (valueFrom
                # resolves cluster-side and is not part of the spec contract).
                if "value" in entry and entry.get("name"):
                    env[str(entry["name"])] = str(entry["value"])
        live[name] = _LiveState(
            replicas=int(replicas) if replicas is not None else None,
            image=image,
            ports=sorted(ports),
            env=env,
        )
    return live


# -- Docker Compose live state ------------------------------------------------


def _parse_compose_ps(output: str) -> List[Dict[str, Any]]:
    """Parse `docker compose ps --format json` output.

    Newer Docker emits NDJSON (one object per line); older versions emit a
    single JSON array. Handle both, skipping malformed lines.
    """
    text = output.strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        return [d for d in data if isinstance(d, dict)]
    rows: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _docker_inspect(
    container_id: str, timeout: Optional[float] = None
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return ``(inspect_object, probe_error)`` for *container_id*.

    A timed-out / failed probe is reported as a readable message in the
    second element; an empty or unparsable but successful response keeps
    the historical silent ``(None, None)`` — "unknown" live data must never
    produce false drift.
    """
    out, error = _probe(
        ["docker", "inspect", container_id],
        _LIVE_TIMEOUT if timeout is None else timeout,
    )
    if out is None:
        return None, error
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None, None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0], None
    return None, None


def _compose_live_state(
    budget: float = _COMPOSE_PROBE_BUDGET,
) -> Tuple[Optional[Dict[str, _LiveState]], Optional[str]]:
    """Fetch live Compose service state via `docker compose ps` (read-only).

    Returns ``(live, error)``. ``live`` maps compose service name ->
    :class:`_LiveState`, or is ``None`` when docker is unavailable or the
    ``ps`` probe fails. ``error`` is a readable message when the global
    probe *budget* was exhausted or individual ``docker inspect`` steps
    timed out / failed; the returned state is then partial (replicas and
    image come from ``ps``; ports/env of unprobed containers stay unknown,
    so a slow daemon degrades the report instead of stalling the CLI).
    """
    if shutil.which("docker") is None:
        return None, None
    deadline = time.monotonic() + budget
    out = _run_readonly(
        ["docker", "compose", "ps", "--format", "json"],
        timeout=min(_LIVE_TIMEOUT, budget),
    )
    if out is None:
        return None, None
    rows = _parse_compose_ps(out)
    live: Dict[str, _LiveState] = {}
    failures: List[str] = []
    unprobed = 0
    for row in rows:
        service = str(row.get("Service") or row.get("Name") or "")
        if not service:
            continue
        # ports/env start as None (= unknown); they are only filled in when a
        # successful `docker inspect` provides them, so a failed inspect never
        # produces false "missing env/port" drift.
        state = live.setdefault(
            service, _LiveState(replicas=0, image=None, ports=None, env=None)
        )
        state.replicas = (state.replicas or 0) + 1
        image = row.get("Image")
        if image and not state.image:
            state.image = str(image)
        container_id = str(row.get("ID") or row.get("Id") or "")
        if not container_id:
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # Global budget spent (slow/hung daemon): stop issuing probes —
            # the loop finishes immediately instead of blocking for another
            # N containers x per-probe timeout.
            unprobed += 1
            continue
        inspected, probe_error = _docker_inspect(container_id, timeout=remaining)
        if probe_error is not None:
            failures.append(probe_error)
            continue
        if inspected is None:
            continue
        config = inspected.get("Config", {}) or {}
        if not state.image and config.get("Image"):
            state.image = str(config["Image"])
        env_map: Dict[str, str] = {}
        for pair in config.get("Env", []) or []:
            key, sep, value = str(pair).partition("=")
            if sep:
                env_map[key] = value
        state.env = env_map
        ports: List[int] = []
        for spec_key in (config.get("ExposedPorts", {}) or {}):
            port_str = str(spec_key).split("/", 1)[0]
            if port_str.isdigit():
                ports.append(int(port_str))
        state.ports = sorted(ports)
    problems: List[str] = []
    if unprobed:
        problems.append(
            f"docker inspect skipped for {unprobed} container(s): "
            f"global probe budget of {budget:.1f}s exhausted"
        )
    if failures:
        problems.append("; ".join(failures))
    return live, "; ".join(problems) if problems else None


# -- Comparison ----------------------------------------------------------------


def _compare_service(
    expected: _ExpectedState, live: Optional[_LiveState], items: List[DriftItem]
) -> bool:
    """Compare one expected service against live state; append drift items.

    Returns True when the service is fully in sync.
    """
    if live is None:
        items.append(
            DriftItem(
                resource=expected.name,
                parameter="resource",
                expected="present",
                live="absent",
                status=STATUS_MISSING,
            )
        )
        return False

    clean = True
    if live.replicas is not None and live.replicas != expected.replicas:
        items.append(
            DriftItem(
                resource=expected.name,
                parameter="replicas",
                expected=str(expected.replicas),
                live=str(live.replicas),
            )
        )
        clean = False
    if expected.image and live.image and live.image != expected.image:
        items.append(
            DriftItem(
                resource=expected.name,
                parameter="image",
                expected=expected.image,
                live=live.image,
            )
        )
        clean = False
    if expected.ports and live.ports is not None and live.ports != expected.ports:
        items.append(
            DriftItem(
                resource=expected.name,
                parameter="ports",
                expected=",".join(str(p) for p in expected.ports),
                live=",".join(str(p) for p in live.ports) or "none",
            )
        )
        clean = False
    if expected.env and live.env is not None:
        for key, value in sorted(expected.env.items()):
            live_value = live.env.get(key)
            if live_value is None:
                items.append(
                    DriftItem(
                        resource=expected.name,
                        parameter=f"env:{key}",
                        expected=value,
                        live="unset",
                        status=STATUS_MISSING,
                    )
                )
                clean = False
            elif live_value != value:
                items.append(
                    DriftItem(
                        resource=expected.name,
                        parameter=f"env:{key}",
                        expected=value,
                        live=live_value,
                    )
                )
                clean = False
    return clean


def detect_live_drift_program(
    program: n.Program,
    target: str = "k8s",
    namespace: str = "default",
) -> DriftReport:
    """Compare the declared spec of a parsed *program* against the live state.

    Same contract as :func:`detect_live_drift`, but for callers that already
    hold a parsed (possibly environment-overlaid) Program — e.g.
    ``infra diff --live``. Strictly read-only: only ``kubectl get`` /
    ``docker compose ps`` / ``docker inspect`` probes are executed — never a
    mutation.
    """
    expected = _expected_services(program)

    probe_error: Optional[str] = None
    normalized = target.lower()
    if normalized in ("k8s", "kubernetes"):
        live = _kubectl_live_state(namespace)
        tool_hint = "kubectl is not available or the cluster is unreachable"
        report_target = "k8s"
    elif normalized in ("compose", "docker"):
        live, probe_error = _compose_live_state()
        tool_hint = "docker is not available or the daemon is not running"
        report_target = "compose"
    else:
        return DriftReport(
            target=normalized,
            error=f"Unknown drift target '{target}'. Valid targets: k8s, compose",
        )

    if live is None:
        return DriftReport(target=report_target, error=tool_hint)

    report = DriftReport(target=report_target)
    for exp in expected:
        if _compare_service(exp, live.get(exp.name), report.items):
            report.in_sync.append(exp.name)
    # A partial probe (global budget exhausted / some inspects timed out) is
    # surfaced as a readable error *alongside* whatever state was gathered.
    if probe_error is not None:
        report.error = probe_error
    return report


def detect_live_drift(
    infra_path: Path,
    target: str = "k8s",
    namespace: str = "default",
) -> DriftReport:
    """Compare the declared spec in *infra_path* against the live state.

    Strictly read-only: only ``kubectl get`` / ``docker compose ps`` /
    ``docker inspect`` probes are executed — never a mutation.

    ``target`` is ``k8s``/``kubernetes`` or ``compose``/``docker``. Raises on
    parse errors (propagated); probe failures (missing tool, unreachable
    daemon) are reported via :attr:`DriftReport.error`, never as an exception.
    """
    program = parse_file(Path(infra_path))
    return detect_live_drift_program(program, target=target, namespace=namespace)
