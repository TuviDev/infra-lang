"""Atomic local state locking for deployment safety.

Before mutating anything under ``.infra-state/{project}/`` (deploy,
rollback, restores) the caller takes a short-lived lock::

    with WorkspaceLock("my_project", operation="deploy"):
        ...  # only one process at a time can be inside

Design constraints (all stdlib, 100% offline, cross-platform):

- The lock file is ``.infra-state/{project}/locks/{operation}.lock``
  containing JSON: ``pid``, ``hostname``, ``timestamp``, ``operation``.
- Creation is **atomic** via ``open(path, "x")`` (``O_EXCL``) — it either
  succeeds or raises ``FileExistsError``; this works on Windows, macOS and
  Linux with no platform-specific code.
- A lock whose owning process is gone is **stale** and is removed
  automatically on the next acquire attempt. Process existence is probed
  with stdlib only: ``os.kill(pid, 0)`` on POSIX, the ``tasklist`` tool on
  Windows — never psutil or other third-party packages.
"""

from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

#: Default state directory (relative to the working directory).
STATE_DIR = ".infra-state"
#: Subdirectory holding lock files inside a project state dir.
LOCKS_DIR = "locks"
#: Default operation used for the lock file name / metadata.
DEFAULT_OPERATION = "deploy"
#: A corrupt lock file younger than this is *not* reclaimed: a concurrently
#: starting process may still be writing its payload (the file becomes
#: visible at ``open(...)`` time, before the JSON write finishes).
CORRUPT_LOCK_GRACE_S = 1.0


class LockError(Exception):
    """Raised when a lock cannot be acquired (held, or lost to a race)."""


@dataclass(frozen=True)
class LockInfo:
    """Payload of a lock file."""

    pid: int
    hostname: str
    timestamp: str
    operation: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pid": self.pid,
            "hostname": self.hostname,
            "timestamp": self.timestamp,
            "operation": self.operation,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "LockInfo":
        return LockInfo(
            pid=int(data["pid"]),
            hostname=str(data["hostname"]),
            timestamp=str(data["timestamp"]),
            operation=str(data["operation"]),
        )


def lock_path(
    state_root: Path, project: str, operation: str = DEFAULT_OPERATION
) -> Path:
    """Return ``<state_root>/<project>/locks/<operation>.lock``."""
    return state_root / project / LOCKS_DIR / f"{operation}.lock"


def read_lock(path: Path) -> Optional[LockInfo]:
    """Parse a lock file; return ``None`` when missing or corrupt."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return LockInfo.from_dict(data)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _pid_alive(pid: int) -> bool:
    """Check process liveness with stdlib only (POSIX + Windows)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by another user.
        return True
    except OSError:
        # Unexpected failure — assume alive (fail safe, never steal a lock).
        return True
    return True


def _pid_alive_windows(pid: int) -> bool:
    """Windows liveness probe via ``ctypes`` (stdlib, zero dependencies).

    Replaces the fragile ``tasklist`` subprocess probe: kernel handles are
    consulted directly and an exited process (``GetExitCodeProcess`` !=
    ``STILL_ACTIVE``) is correctly reported dead. On error paths we return
    ``False`` only when the process provably is not running — ``OpenProcess``
    succeeding is the positive signal.
    """
    import ctypes

    # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000; STILL_ACTIVE = 259
    # ctypes.windll exists only on Windows, which is exactly where this
    # function is ever dispatched (see _pid_alive) — tell the type checker.
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if handle:
        exit_code = ctypes.c_ulong()
        res = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        kernel32.CloseHandle(handle)
        if res:
            return exit_code.value == 259
    return False


def is_stale(info: LockInfo) -> bool:
    """A lock is stale when its owning process no longer exists."""
    return not _pid_alive(info.pid)


def lock_status(
    state_root: Path, project: str, operation: str = DEFAULT_OPERATION
) -> Optional[LockInfo]:
    """Return the current lock payload (alive or stale), or ``None``."""
    return read_lock(lock_path(state_root, project, operation))


def _try_create(path: Path, payload: str) -> bool:
    """Atomically create *path* with *payload*; False when it already exists."""
    try:
        with open(path, "x", encoding="utf-8") as handle:
            handle.write(payload)
        return True
    except FileExistsError:
        return False


def _file_is_fresh(path: Path) -> bool:
    """True when *path* was modified less than ``CORRUPT_LOCK_GRACE_S`` ago."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False  # vanished already — treat as reclaimable
    return (time.time() - mtime) < CORRUPT_LOCK_GRACE_S


class WorkspaceLock:
    """Context manager for an atomic project lock.

    ::

        with WorkspaceLock("my_project", operation="deploy"):
            deploy_things()

    ``state_root`` defaults to ``./.infra-state`` (the working directory).
    Acquiring raises :class:`LockError` when a live process holds the lock;
    stale locks (dead owner, corrupt payload) are reclaimed automatically.
    """

    def __init__(
        self,
        project: str,
        operation: str = DEFAULT_OPERATION,
        state_root: Optional[Path] = None,
    ) -> None:
        self.project = project
        self.operation = operation
        self.state_root = Path(state_root) if state_root else Path(STATE_DIR)
        self.lock_file = lock_path(self.state_root, project, operation)
        self._held = False

    # -- protocol ---------------------------------------------------------- #

    def acquire(self) -> "WorkspaceLock":
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            LockInfo(
                pid=os.getpid(),
                hostname=socket.gethostname(),
                timestamp=datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                ),
                operation=self.operation,
            ).to_dict()
        )
        if _try_create(self.lock_file, payload):
            self._held = True
            return self

        existing = read_lock(self.lock_file)
        if existing is not None and not is_stale(existing):
            raise LockError(
                f"Project '{self.project}' is locked by pid "
                f"{existing.pid} on {existing.hostname} "
                f"(operation: {existing.operation}, since "
                f"{existing.timestamp})."
            )
        if existing is None and _file_is_fresh(self.lock_file):
            raise LockError(
                f"Project '{self.project}' lock was created moments ago and "
                "its payload is not readable yet — another process is "
                "starting right now; retry shortly."
            )
        # Stale or old corrupt lock — reclaim it, then retry once.
        self._remove_file()
        if not _try_create(self.lock_file, payload):
            raise LockError(
                f"Could not acquire the '{self.operation}' lock for project "
                f"'{self.project}': created concurrently by another process."
            )
        self._held = True
        return self

    def release(self) -> None:
        """Remove the lock file; safe to call when not held."""
        if self._held:
            self._held = False
            self._remove_file()

    def __enter__(self) -> "WorkspaceLock":
        return self.acquire()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.release()

    # -- helpers ----------------------------------------------------------- #

    def _remove_file(self) -> None:
        try:
            if self.lock_file.exists():
                self.lock_file.unlink()
        except (PermissionError, FileNotFoundError, OSError):
            # WinError 32: the file is still open elsewhere (another process
            # is mid-acquire); FileNotFoundError/OSError: a race reclaimed it.
            pass


__all__ = [
    "DEFAULT_OPERATION",
    "LOCKS_DIR",
    "STATE_DIR",
    "LockError",
    "LockInfo",
    "WorkspaceLock",
    "is_stale",
    "lock_path",
    "lock_status",
    "read_lock",
]
