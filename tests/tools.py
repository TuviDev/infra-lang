"""Live E2E tool detection for the Infra Lang test suite.

Provides fast, session-cached, side-effect-free detection of the external tools
needed to run real Kubernetes E2E tests (Docker, kind, kubectl, kubeconform).
Tests that require live infrastructure call :func:`require_tools` (or use the
pytest fixture) and are silently skipped when a tool is missing, so a normal
``pytest tests`` run never blocks on environment setup.

Detection strategy (each call is bounded and cached for the whole session):

- ``which``-style PATH lookup for the binary, then
- a quick ``--version`` / ``version`` probe with a short timeout to confirm it
  actually runs (not just present in PATH).
"""

from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from typing import Collection, Sequence

_TOOL_TIMEOUT = 4.0  # seconds; fast enough to never block CI/local runs


@lru_cache(maxsize=None)
def _probe(binary: str, probe_args: Sequence[str] = ("--version",)) -> bool:
    """Return True if ``binary`` is on PATH and runs successfully."""
    path = shutil.which(binary)
    if path is None:
        return False
    try:
        result = subprocess.run(
            [binary, *probe_args],
            capture_output=True,
            text=True,
            timeout=_TOOL_TIMEOUT,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def have_docker() -> bool:
    """True if the Docker CLI is on PATH **and** the daemon responds.

    A bare `docker --version` / `docker version` only proves the CLI binary
    exists; the daemon may still be down (e.g. Docker Desktop not started on a
    Windows/macOS CI runner). We probe `docker info`, which exits non-zero
    when the daemon is unreachable, so live E2E skips instead of failing.
    """
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=_TOOL_TIMEOUT,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def have_kind() -> bool:
    return _probe("kind", ("version",))


def have_kubectl() -> bool:
    return _probe("kubectl", ("version", "--client=true"))


def have_kubeconform() -> bool:
    return _probe("kubeconform", ("-v",))


def have_all_tools() -> bool:
    """True if every live-E2E tool is available (Docker + kind + kubectl)."""
    return have_docker() and have_kind() and have_kubectl()


def require_tools(names: Collection[str]) -> str | None:
    """Return the name of the first missing tool, or None if all are present."""
    probes = {
        "docker": have_docker,
        "kind": have_kind,
        "kubectl": have_kubectl,
        "kubeconform": have_kubeconform,
    }
    for name in names:
        if not probes[name]():
            return name
    return None
