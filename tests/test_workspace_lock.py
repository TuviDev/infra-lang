"""Tests for atomic deploy locking (`infra.workspace.lock` + `workspace unlock`).

Covers atomic creation, concurrency, auto-release on exceptions, stale-lock
detection (POSIX ``os.kill`` + the Windows ``ctypes``/``kernel32`` probe,
both fully mocked) and the CLI helper. No real cross-process mutation is
performed.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import List

import pytest
from typer.testing import CliRunner

from infra.cli.main import app
from infra.workspace import lock as lock_mod
from infra.workspace.lock import (
    LockError,
    LockInfo,
    WorkspaceLock,
    is_stale,
    lock_path,
    lock_status,
    read_lock,
)

runner = CliRunner()

DEAD_PID = 2 ** 24  # far above any real pid (used with mocked probes)


def _flat(text: str) -> str:
    clean = re.sub(r"\x1b\[[0-9;]*[mGKH]", "", text)
    return re.sub(r"\s+", " ", clean)


def _fake_lock_file(
    tmp_path: Path,
    *,
    project: str = "demo",
    operation: str = "deploy",
    pid: int = DEAD_PID,
    hostname: str = "otherhost",
    content=None,
) -> Path:
    path = lock_path(tmp_path, project, operation)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = content
    if payload is None:
        payload = json.dumps(
            LockInfo(
                pid=pid,
                hostname=hostname,
                timestamp="2026-09-02T10:00:00+00:00",
                operation=operation,
            ).to_dict()
        )
    path.write_text(payload, encoding="utf-8")
    return path


@pytest.fixture
def dead_owner(monkeypatch):
    """Make _pid_alive always report a dead process."""
    monkeypatch.setattr(lock_mod, "_pid_alive", lambda pid: False)


@pytest.fixture
def live_owner(monkeypatch):
    """Make _pid_alive always report a live process."""
    monkeypatch.setattr(lock_mod, "_pid_alive", lambda pid: True)


# --------------------------------------------------------------------------- #
# Paths & payloads
# --------------------------------------------------------------------------- #


class TestPathsAndPayloads:
    def test_lock_path_layout(self, tmp_path):
        path = lock_path(tmp_path, "demo")
        assert path == tmp_path / "demo" / "locks" / "deploy.lock"

    def test_lock_path_custom_operation(self, tmp_path):
        path = lock_path(tmp_path, "demo", "rollback")
        assert path.name == "rollback.lock"

    def test_lock_info_roundtrip(self):
        info = LockInfo(
            pid=123, hostname="host", timestamp="t", operation="deploy"
        )
        assert LockInfo.from_dict(info.to_dict()) == info

    def test_read_lock_missing(self, tmp_path):
        assert read_lock(tmp_path / "nope.lock") is None

    def test_read_lock_corrupt_json(self, tmp_path):
        path = _fake_lock_file(tmp_path, content="{not json")
        assert read_lock(path) is None

    def test_read_lock_missing_key(self, tmp_path):
        path = _fake_lock_file(tmp_path, content='{"pid": 1}')
        assert read_lock(path) is None

    def test_read_lock_wrong_type(self, tmp_path):
        path = _fake_lock_file(tmp_path, content='["pid", 1]')
        assert read_lock(path) is None

    def test_read_lock_valid(self, tmp_path):
        path = _fake_lock_file(tmp_path, pid=4321)
        info = read_lock(path)
        assert info is not None
        assert info.pid == 4321
        assert info.operation == "deploy"

    def test_lock_status(self, tmp_path, dead_owner):
        _fake_lock_file(tmp_path, project="demo", pid=4321)
        info = lock_status(tmp_path, "demo")
        assert info is not None and info.pid == 4321
        assert lock_status(tmp_path, "other") is None


# --------------------------------------------------------------------------- #
# Liveness probes (stdlib only)
# --------------------------------------------------------------------------- #


class _FakeKernel32:
    """Minimal ``kernel32`` stand-in for the Windows ctypes liveness probe."""

    def __init__(self, handle, exit_code, res):
        self._handle = handle
        self._exit_code = exit_code
        self._res = res
        self.open_args: List = []
        self.closed_handles: List = []

    def OpenProcess(self, access, inherit, pid):  # noqa: N802 - WinAPI name
        self.open_args.append((access, inherit, pid))
        return self._handle

    def GetExitCodeProcess(self, handle, ref):  # noqa: N802 - WinAPI name
        ref._obj.value = self._exit_code  # ctypes.byref -> CArgObject
        return self._res

    def CloseHandle(self, handle):  # noqa: N802 - WinAPI name
        self.closed_handles.append(handle)
        return 1


def _patch_windows_kernel(monkeypatch, kernel) -> None:
    """Simulate a Windows host: ``os.name == 'nt'`` plus a fake ``windll``."""
    import ctypes
    import types

    monkeypatch.setattr(lock_mod.os, "name", "nt")
    monkeypatch.setattr(
        ctypes,
        "windll",
        types.SimpleNamespace(kernel32=kernel),
        raising=False,
    )


class TestPidAlive:
    def test_current_process_is_alive(self):
        assert lock_mod._pid_alive(os.getpid()) is True

    def test_invalid_pid_not_alive(self):
        assert lock_mod._pid_alive(0) is False
        assert lock_mod._pid_alive(-5) is False

    def test_posix_dead_pid(self, monkeypatch):
        monkeypatch.setattr(lock_mod.os, "name", "posix")

        def dead(pid, sig):
            raise ProcessLookupError()

        monkeypatch.setattr(lock_mod.os, "kill", dead)
        assert lock_mod._pid_alive(1234) is False

    def test_posix_permission_means_alive(self, monkeypatch):
        monkeypatch.setattr(lock_mod.os, "name", "posix")

        def denied(pid, sig):
            raise PermissionError()

        monkeypatch.setattr(lock_mod.os, "kill", denied)
        assert lock_mod._pid_alive(1234) is True

    def test_posix_other_oserror_fails_safe(self, monkeypatch):
        monkeypatch.setattr(lock_mod.os, "name", "posix")

        def broken(pid, sig):
            raise OSError(22, "EINVAL")

        monkeypatch.setattr(lock_mod.os, "kill", broken)
        assert lock_mod._pid_alive(1234) is True

    def test_windows_ctypes_still_active_means_alive(self, monkeypatch):
        kernel = _FakeKernel32(handle=77, exit_code=259, res=1)
        _patch_windows_kernel(monkeypatch, kernel)
        assert lock_mod._pid_alive(4321) is True
        assert kernel.open_args == [(0x1000, False, 4321)]
        assert kernel.closed_handles == [77]

    def test_windows_ctypes_exited_means_dead(self, monkeypatch):
        kernel = _FakeKernel32(handle=77, exit_code=1, res=1)
        _patch_windows_kernel(monkeypatch, kernel)
        assert lock_mod._pid_alive(4321) is False

    def test_windows_ctypes_open_failure_means_dead(self, monkeypatch):
        kernel = _FakeKernel32(handle=None, exit_code=259, res=1)
        _patch_windows_kernel(monkeypatch, kernel)
        assert lock_mod._pid_alive(4321) is False

    def test_windows_ctypes_exitcode_failure_means_dead(self, monkeypatch):
        kernel = _FakeKernel32(handle=77, exit_code=259, res=0)
        _patch_windows_kernel(monkeypatch, kernel)
        assert lock_mod._pid_alive(4321) is False

    def test_is_stale(self):
        info = LockInfo(pid=1, hostname="h", timestamp="t", operation="deploy")
        import unittest.mock as mock

        with mock.patch.object(lock_mod, "_pid_alive", return_value=False):
            assert is_stale(info) is True
        with mock.patch.object(lock_mod, "_pid_alive", return_value=True):
            assert is_stale(info) is False


# --------------------------------------------------------------------------- #
# WorkspaceLock behaviour
# --------------------------------------------------------------------------- #


class TestWorkspaceLock:
    def test_acquire_creates_json_payload(self, tmp_path):
        with WorkspaceLock("demo", state_root=tmp_path) as lock:
            assert lock.lock_file.is_file()
            data = json.loads(lock.lock_file.read_text(encoding="utf-8"))
            assert data["pid"] == os.getpid()
            assert data["hostname"]
            assert data["operation"] == "deploy"
            assert data["timestamp"].endswith("+00:00")
            while_alive = lock_status(tmp_path, "demo")
            assert while_alive is not None
        assert not lock.lock_file.exists()

    def test_custom_operation_and_project(self, tmp_path):
        with WorkspaceLock("api", operation="rollback",
                           state_root=tmp_path) as lock:
            assert lock.lock_file.name == "rollback.lock"
            data = json.loads(lock.lock_file.read_text(encoding="utf-8"))
            assert data["operation"] == "rollback"

    def test_default_state_root_uses_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with WorkspaceLock("demo") as lock:
            expected = (
                tmp_path / ".infra-state" / "demo" / "locks" / "deploy.lock"
            )
            assert lock.lock_file.resolve() == expected
            assert expected.is_file()

    def test_auto_release_on_exception(self, tmp_path):
        lock_path_ = lock_path(tmp_path, "demo")
        with pytest.raises(RuntimeError, match="boom"):
            with WorkspaceLock("demo", state_root=tmp_path):
                assert lock_path_.is_file()
                raise RuntimeError("boom")
        assert not lock_path_.exists()

    def test_double_lock_same_process_raises(self, tmp_path):
        # our own pid is alive, so the second acquire sees a live lock
        with WorkspaceLock("demo", state_root=tmp_path):
            with pytest.raises(LockError, match="locked by pid"):
                WorkspaceLock("demo", state_root=tmp_path).acquire()

    def test_lock_error_message_details(self, tmp_path, live_owner):
        path = _fake_lock_file(
            tmp_path, pid=777, hostname="builder-1", operation="deploy"
        )
        with pytest.raises(LockError) as excinfo:
            WorkspaceLock("demo", state_root=tmp_path).acquire()
        message = str(excinfo.value)
        assert "777" in message
        assert "builder-1" in message
        assert "deploy" in message
        assert path.is_file()  # live lock untouched

    def test_stale_lock_reclaimed(self, tmp_path, dead_owner):
        _fake_lock_file(tmp_path, pid=DEAD_PID)
        with WorkspaceLock("demo", state_root=tmp_path) as lock:
            data = json.loads(lock.lock_file.read_text(encoding="utf-8"))
            assert data["pid"] == os.getpid()  # reclaimed by us

    def test_corrupt_lock_reclaimed(self, tmp_path):
        path = _fake_lock_file(tmp_path, content="{corrupted!!")
        # old corrupt locks (e.g. from a crash) are reclaimed; backdate it
        past = 1_700_000_000
        os.utime(path, (past, past))
        with WorkspaceLock("demo", state_root=tmp_path) as lock:
            assert lock.lock_file.is_file()

    def test_fresh_corrupt_lock_refused(self, tmp_path):
        # a *just-created* corrupt file means another process is mid-startup
        _fake_lock_file(tmp_path, content="{partial")
        with pytest.raises(LockError, match="retry shortly"):
            WorkspaceLock("demo", state_root=tmp_path).acquire()

    def test_lock_file_vanishes_between_create_and_stat(self, tmp_path):
        from unittest import mock

        with mock.patch.object(
            lock_mod, "_try_create", side_effect=[False, True]
        ):
            lock = WorkspaceLock("demo", state_root=tmp_path).acquire()
        assert lock._held

    def test_concurrent_retry_loss_raises(self, tmp_path, dead_owner):
        _fake_lock_file(tmp_path, pid=DEAD_PID)
        from unittest import mock

        with mock.patch.object(
            lock_mod, "_try_create", side_effect=[False, False]
        ):
            with pytest.raises(LockError, match="concurrently"):
                WorkspaceLock("demo", state_root=tmp_path).acquire()

    def test_release_without_hold_is_noop(self, tmp_path):
        lock = WorkspaceLock("demo", state_root=tmp_path)
        lock.release()  # never acquired — must not fail
        lock.release()
        assert not lock.lock_file.exists()

    def test_release_after_external_delete(self, tmp_path):
        lock = WorkspaceLock("demo", state_root=tmp_path).acquire()
        lock.lock_file.unlink()  # someone removed it behind our back
        lock.release()  # FileNotFoundError is swallowed
        assert not lock.lock_file.exists()

    def test_concurrency_single_winner(self, tmp_path):
        barrier = threading.Barrier(8)
        winners: List[str] = []
        errors: List[Exception] = []

        def worker(index: int) -> None:
            barrier.wait(timeout=10)
            try:
                WorkspaceLock("demo", state_root=tmp_path).acquire()
                winners.append(str(index))
            except (LockError, PermissionError, OSError) as exc:
                # LockError: lost the atomic create race; PermissionError/
                # OSError: Windows may surface WinError 32 from a mid-acquire
                # handle instead of a clean LockError — still a lost race.
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(i,)) for i in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert len(winners) == 1
        assert len(errors) == 7
        # the winner's lock is still held; clean it up manually (tolerant —
        # on Windows the file may briefly be held open by the last thread)
        try:
            lock_path(tmp_path, "demo").unlink()
        except (PermissionError, FileNotFoundError, OSError):
            pass


# --------------------------------------------------------------------------- #
# CLI: infra workspace unlock
# --------------------------------------------------------------------------- #


class TestCliUnlock:
    def test_unlock_no_lock(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["workspace", "unlock", "demo"])
        assert result.exit_code == 0, result.output
        assert "No 'deploy' lock" in result.output

    def test_unlock_stale_lock(self, tmp_path, monkeypatch, dead_owner):
        path = _fake_lock_file(tmp_path / ".infra-state")
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["workspace", "unlock", "demo"])
        assert result.exit_code == 0, result.output
        assert "[OK] Removed 'deploy' lock" in result.output
        assert not path.exists()

    def test_unlock_live_lock_refused(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with WorkspaceLock("demo"):  # held by this (live) process
            result = runner.invoke(app, ["workspace", "unlock", "demo"])
            assert result.exit_code == 1
            flat = _flat(result.output)
            assert "locked by a live process" in flat
            assert str(os.getpid()) in flat
            assert lock_path(Path(".infra-state"), "demo").exists()

    def test_unlock_force_live_lock(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with WorkspaceLock("demo"):
            result = runner.invoke(
                app, ["workspace", "unlock", "demo", "--force"]
            )
            assert result.exit_code == 0, result.output
            assert "(forced)." in result.output
            assert not lock_path(Path(".infra-state"), "demo").exists()

    def test_unlock_corrupt_lock(self, tmp_path, monkeypatch, live_owner):
        path = _fake_lock_file(tmp_path / ".infra-state", content="{junk")
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["workspace", "unlock", "demo"])
        assert result.exit_code == 0, result.output
        assert not path.exists()

    def test_unlock_custom_operation(self, tmp_path, monkeypatch, dead_owner):
        _fake_lock_file(tmp_path / ".infra-state", operation="rollback")
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app, ["workspace", "unlock", "demo", "-o", "rollback"]
        )
        assert result.exit_code == 0, result.output
        assert "Removed 'rollback' lock" in result.output

    def test_unlock_vanished_lock_is_tolerated(self, tmp_path, monkeypatch):
        path = _fake_lock_file(tmp_path / ".infra-state", content="{junk")
        monkeypatch.chdir(tmp_path)
        from unittest import mock

        with mock.patch.object(
            Path, "unlink", side_effect=FileNotFoundError()
        ):
            result = runner.invoke(app, ["workspace", "unlock", "demo"])
        assert result.exit_code == 0, result.output
        assert "[OK] Removed" in result.output
        assert path.exists()  # unlink was patched away

    def test_unlock_help_robust(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "120")
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.setenv("FORCE_COLOR", "0")
        result = runner.invoke(app, ["workspace", "unlock", "--help"])
        assert result.exit_code == 0, result.output
        assert "--force" in _flat(result.output)
