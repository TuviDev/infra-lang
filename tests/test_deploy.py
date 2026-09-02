"""Tests for `infra deploy` / `infra rollback` — engine + CLI (v1.0.0).

All external tool calls are mocked at ``subprocess.run`` (as called from
``infra.deploy.engine``) — the suite never touches a real cluster.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

import pytest
from typer.testing import CliRunner

from infra.cli.main import app
from infra.deploy.engine import (
    FAILED,
    PLANNED,
    RESTORED,
    ROLLED_BACK,
    SUCCESS,
    DeployRecord,
    StepResult,
    apply_command_set,
    canonical_target,
    compile_hash,
    execute_deploy,
    execute_rollback,
    find_record,
    have_tool,
    last_good_revision,
    list_history,
    load_snapshot,
    next_revision,
    rollout_command_set,
    run_command,
    save_record,
    snapshot_dir,
    target_tool,
    undo_command_set,
)

runner = CliRunner()

DEMO = """\
service api {
  image: "registry.example.com/api:2.1"
  replicas: 2
  port: 8080
  health http("/health") {
    interval: 30s
    timeout: 5s
  }
  resources {
    limits: {memory: 512Mi}
  }
}

database db {
  type: postgres
  version: "16"
  storage: 20Gi
  backup { enabled: true }
}

environment "prod" {
  service api {
    replicas: 4
  }
}
"""

FILES_COMPOSE = {"docker-compose.yml": "services: {}\n"}
FILES_K8S = {"infra.yaml": "kind: Deployment\n"}
FILES_HELM = {"demo/Chart.yaml": "name: demo\n", "demo/values.yaml": "x: 1\n"}
FILES_TF = {"main.tf": "resource {}\n"}


def _proc(rc: int = 0, stdout: str = "", stderr: str = "") -> Any:
    return subprocess.CompletedProcess(
        args=["x"], returncode=rc, stdout=stdout, stderr=stderr
    )


def _flat(text: str) -> str:
    clean = re.sub(r"\x1b\[[0-9;]*[mGKH]", "", text)
    return re.sub(r"\s+", " ", clean)


def _write(tmp_path: Path, name: str = "demo.infra",
           source: str = DEMO) -> Path:
    f = tmp_path / name
    f.write_text(source, encoding="utf-8")
    return f


# --------------------------------------------------------------------------- #
# targets / tools / hashing
# --------------------------------------------------------------------------- #


class TestTargets:
    @pytest.mark.parametrize(
        "alias,canonical",
        [("compose", "compose"), ("docker", "compose"),
         ("kubernetes", "kubernetes"), ("k8s", "kubernetes"),
         ("helm", "helm"), ("terraform", "terraform"), ("tf", "terraform"),
         ("K8S", "kubernetes")],
    )
    def test_aliases(self, alias, canonical):
        assert canonical_target(alias) == canonical

    def test_invalid_target(self):
        with pytest.raises(ValueError, match="Unsupported target"):
            canonical_target("mesos")

    def test_target_tool(self):
        assert target_tool("k8s") == "kubectl"
        assert target_tool("docker") == "docker"
        assert target_tool("tf") == "terraform"
        assert target_tool("helm") == "helm"

    def test_have_tool(self, monkeypatch):
        import shutil

        monkeypatch.setattr(shutil, "which", lambda b: "/usr/bin/" + b)
        assert have_tool("docker") is True
        monkeypatch.setattr(shutil, "which", lambda b: None)
        assert have_tool("docker") is False


class TestCompileHash:
    def test_deterministic_and_order_insensitive(self):
        first = compile_hash({"b": "2", "a": "1"})
        second = compile_hash({"a": "1", "b": "2"})
        assert first == second
        assert len(first) == 64

    def test_changes_with_content(self):
        assert compile_hash({"a": "1"}) != compile_hash({"a": "2"})


# --------------------------------------------------------------------------- #
# command builders
# --------------------------------------------------------------------------- #


class TestCommandBuilders:
    def test_apply_compose(self, tmp_path):
        pairs = apply_command_set("compose", tmp_path, "demo")
        cmd, cwd = pairs[0]
        assert cwd is None
        assert cmd[:3] == ["docker", "compose", "-f"]
        assert cmd[-2:] == ["up", "-d"]
        assert "docker-compose.yml" in cmd[3]

    def test_apply_kubernetes(self, tmp_path):
        cmd, cwd = apply_command_set("k8s", tmp_path, "demo")[0]
        assert cmd == ["kubectl", "apply", "-f", str(tmp_path / "infra.yaml")]
        assert cwd is None

    def test_apply_helm_chart_subdir(self, tmp_path):
        (tmp_path / "demo").mkdir()
        cmd, _ = apply_command_set("helm", tmp_path, "demo")[0]
        assert cmd[:3] == ["helm", "upgrade", "--install"]
        assert cmd[3] == "demo"
        assert cmd[4] == str(tmp_path / "demo")

    def test_apply_helm_chart_fallback(self, tmp_path):
        cmd, _ = apply_command_set("helm", tmp_path, "demo")[0]
        assert cmd[4] == str(tmp_path)

    def test_apply_terraform_uses_cwd(self, tmp_path):
        cmd, cwd = apply_command_set("terraform", tmp_path, "demo")[0]
        assert cmd == ["terraform", "apply", "-auto-approve"]
        assert cwd == tmp_path

    def test_rollout_compose(self, tmp_path):
        cmd, _ = rollout_command_set("compose", tmp_path, "demo", [], 60)[0]
        assert cmd[-2:] == ["--format", "json"]

    def test_rollout_kubernetes_per_service(self, tmp_path):
        pairs = rollout_command_set(
            "kubernetes", tmp_path, "demo", ["api", "web"], 77
        )
        assert len(pairs) == 2
        assert pairs[0][0][3] == "deployment/api"
        assert pairs[1][0][3] == "deployment/web"
        assert pairs[0][0][4] == "--timeout=77s"

    def test_rollout_helm(self, tmp_path):
        cmd, _ = rollout_command_set("helm", tmp_path, "demo", [], 60)[0]
        assert cmd == ["helm", "status", "demo"]

    def test_rollout_terraform_empty(self, tmp_path):
        assert rollout_command_set("terraform", tmp_path, "demo", [], 5) == []

    def test_undo_commands(self, tmp_path):
        k8s = undo_command_set("kubernetes", tmp_path, "demo", ["api"])
        assert k8s[0][0][:3] == ["kubectl", "rollout", "undo"]
        helm = undo_command_set("helm", tmp_path, "demo", [])
        assert helm[0][0] == ["helm", "rollback", "demo"]
        assert undo_command_set("compose", tmp_path, "demo", []) == []
        assert undo_command_set("terraform", tmp_path, "demo", []) == []


# --------------------------------------------------------------------------- #
# run_command
# --------------------------------------------------------------------------- #


class TestRunCommand:
    def test_success(self):
        with mock.patch(
            "infra.deploy.engine.subprocess.run",
            return_value=_proc(0, "ok"),
        ):
            step = run_command(["docker", "ps"], timeout=10)
        assert step.ok and step.stdout == "ok"
        assert step.returncode == 0

    def test_patched_globally_too(self):
        # The spec contract: unittest.mock.patch("subprocess.run") works.
        with mock.patch(
            "subprocess.run", return_value=_proc(0, "global")
        ):
            step = run_command(["kubectl", "get", "pods"], timeout=10)
        assert step.ok and step.stdout == "global"

    def test_nonzero_rc(self):
        with mock.patch(
            "infra.deploy.engine.subprocess.run",
            return_value=_proc(3, stderr="boom"),
        ):
            step = run_command(["helm", "status"], timeout=10)
        assert not step.ok and step.stderr == "boom"

    def test_timeout(self):
        with mock.patch(
            "infra.deploy.engine.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="x", timeout=10),
        ):
            step = run_command(["sleep", "99"], timeout=10)
        assert step.returncode is None
        assert "timed out" in step.stderr

    def test_oserror(self):
        with mock.patch(
            "infra.deploy.engine.subprocess.run",
            side_effect=OSError("no such file"),
        ):
            step = run_command(["nope"], timeout=10)
        assert step.returncode is None
        assert "no such file" in step.stderr

    def test_cwd_passed_through(self, tmp_path):
        captured: Dict[str, Any] = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            return _proc(0)

        with mock.patch("infra.deploy.engine.subprocess.run", fake_run):
            run_command(["terraform", "apply"], cwd=tmp_path, timeout=5)
        assert captured["cwd"] == str(tmp_path)
        assert captured["encoding"] == "utf-8"


# --------------------------------------------------------------------------- #
# history & records
# --------------------------------------------------------------------------- #


class TestComposeRolloutOk:
    def test_failed_step_is_not_ok(self):
        from infra.deploy.engine import _compose_rollout_ok

        step = StepResult(label="ps", command=("docker",), returncode=1)
        assert _compose_rollout_ok(step) is False

    def test_running_state_is_ok(self):
        from infra.deploy.engine import _compose_rollout_ok

        step = StepResult(
            label="ps", command=("docker",), returncode=0,
            stdout='{"State":"running"}',
        )
        assert _compose_rollout_ok(step) is True

    def test_unhealthy_marker_is_not_ok(self):
        from infra.deploy.engine import _compose_rollout_ok

        step = StepResult(
            label="ps", command=("docker",), returncode=0,
            stdout='{"Health":"unhealthy"}',
        )
        assert _compose_rollout_ok(step) is False


def _record(
    revision: str = "r0001",
    status: str = SUCCESS,
    timestamp: str = "2026-09-02T10:00:00+00:00",
    project: str = "demo",
    target: str = "compose",
) -> DeployRecord:
    return DeployRecord(
        revision=revision,
        timestamp=timestamp,
        project=project,
        target=target,
        tool="docker",
        status=status,
        duration_s=1.5,
        compile_hash="abc",
        environment="",
        service_names=("api",),
        files=("docker-compose.yml",),
        steps=(StepResult(label="x", command=("docker",), returncode=0),),
        message="ok",
    )


class TestHistory:
    def test_save_and_list_roundtrip(self, tmp_path):
        save_record(tmp_path, _record(), FILES_COMPOSE)
        history = list_history(tmp_path, "demo")
        assert len(history) == 1
        rec = history[0]
        assert rec.revision == "r0001"
        assert rec.status == SUCCESS
        assert rec.steps[0].label == "x"
        assert rec.service_names == ("api",)

    def test_list_history_sorted_and_skips_corrupt(self, tmp_path):
        later = "2026-09-02T11:00:00+00:00"
        save_record(tmp_path, _record("r0002", timestamp=later), {})
        save_record(tmp_path, _record("r0001"), {})
        bad = tmp_path / "demo" / "history" / "broken.json"
        bad.write_text("{not json", encoding="utf-8")
        history = list_history(tmp_path, "demo")
        assert [r.revision for r in history] == ["r0001", "r0002"]

    def test_next_revision_increments(self, tmp_path):
        assert next_revision(tmp_path, "demo") == "r0001"
        save_record(tmp_path, _record("r0001"), {})
        assert next_revision(tmp_path, "demo") == "r0002"

    def test_load_snapshot(self, tmp_path):
        save_record(
            tmp_path, _record(), {"a/main.tf": "x", "nested/b.tf": "y"}
        )
        snap = load_snapshot(tmp_path, "demo", "r0001")
        assert snap == {"a/main.tf": "x", "nested/b.tf": "y"}
        assert load_snapshot(tmp_path, "demo", "r9999") is None

    def test_snapshot_keys_are_normalized_to_posix(self, tmp_path):
        # defensive: backslash keys are normalized on write, so history
        # JSON and snapshot dicts are byte-identical on every OS
        save_record(
            tmp_path, _record(), {"a\\main.tf": "x", "nested/b.tf": "y"}
        )
        snap = load_snapshot(tmp_path, "demo", "r0001")
        assert sorted(snap) == ["a/main.tf", "nested/b.tf"]

    def test_record_files_tuple_is_posix(self, tmp_path):
        # plan-only deploy — no subprocess; the recorded files tuple must
        # hold normalized posix keys on every OS
        record = execute_deploy(
            project="demo", target="compose",
            files={"demo\\Chart.yaml": "x", "demo/values.yaml": "y"},
            state_root=tmp_path, apply=False,
        )
        assert record.files == ("demo/Chart.yaml", "demo/values.yaml")

    def test_find_record_exact_and_prefix(self, tmp_path):
        save_record(tmp_path, _record("r0001"), {})
        save_record(
            tmp_path, _record("r0010", timestamp="2026-09-02T12:00:00+00:00"),
            {},
        )
        assert find_record(tmp_path, "demo", "r0001").revision == "r0001"
        assert find_record(tmp_path, "demo", "r001").revision == "r0010"
        assert find_record(tmp_path, "demo", "r00") is None  # ambiguous
        assert find_record(tmp_path, "demo", "zzz") is None

    def test_last_good_revision(self, tmp_path):
        save_record(tmp_path, _record("r0001"), {})
        save_record(
            tmp_path,
            _record("r0002", status=FAILED,
                    timestamp="2026-09-02T11:00:00+00:00"),
            {},
        )
        good = last_good_revision(tmp_path, "demo")
        assert good is not None and good.revision == "r0001"
        assert last_good_revision(tmp_path, "nope") is None

    def test_restored_counts_as_good(self, tmp_path):
        save_record(tmp_path, _record("r0003", status=RESTORED), {})
        good = last_good_revision(tmp_path, "demo")
        assert good is not None and good.revision == "r0003"


# --------------------------------------------------------------------------- #
# execute_deploy (engine flows, subprocess fully mocked)
# --------------------------------------------------------------------------- #


def _run_with(responses: List[Any]):
    """Patch engine subprocess.run with queued responses (or callables)."""

    def fake_run(cmd, **kwargs):
        answer = responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            return answer(cmd)
        return answer

    return mock.patch(
        "infra.deploy.engine.subprocess.run",
        mock.MagicMock(side_effect=fake_run),
    )


class TestExecuteDeploy:
    def test_plan_only_no_subprocess(self, tmp_path):
        with _run_with([]) as run_mock:
            record = execute_deploy(
                project="demo", target="compose", files=FILES_COMPOSE,
                state_root=tmp_path, apply=False,
            )
        assert record.status == PLANNED
        assert record.steps == ()
        run_mock.assert_not_called()
        assert (tmp_path / "demo" / "history" / "r0001.json").exists()
        # snapshot persisted even for plans
        assert load_snapshot(tmp_path, "demo", "r0001") == FILES_COMPOSE

    def test_compose_success(self, tmp_path):
        with _run_with([_proc(0), _proc(0, stdout="running")]):
            record = execute_deploy(
                project="demo", target="compose", files=FILES_COMPOSE,
                state_root=tmp_path,
            )
        assert record.status == SUCCESS
        assert "rollout verified" in record.message
        assert [s.label.split()[0] for s in record.steps] == ["docker"] * 2

    def test_compose_dead_container_no_previous_fails(self, tmp_path):
        with _run_with([_proc(0), _proc(0, stdout='{"State":"exited"}')]):
            record = execute_deploy(
                project="demo", target="compose", files=FILES_COMPOSE,
                state_root=tmp_path,
            )
        # compose has no tool-native undo and there is no good snapshot
        assert record.status == FAILED
        assert "could not complete" in record.message

    def test_compose_dead_container_rolls_back_to_snapshot(self, tmp_path):
        save_record(tmp_path, _record("r0001"), FILES_COMPOSE)
        # apply ok → ps reports exited → re-apply of r0001 snapshot ok
        with _run_with([_proc(0), _proc(0, stdout='{"State":"exited"}'), _proc(0)]):
            record = execute_deploy(
                project="demo", target="compose", files=FILES_COMPOSE,
                state_root=tmp_path,
            )
        assert record.status == ROLLED_BACK
        assert "restored previous state" in record.message
        rollback_step = record.steps[-1]
        assert "snapshots/r0001" in rollback_step.label.replace("\\", "/")

    def test_compose_rollout_command_failure_no_previous_fails(self, tmp_path):
        # apply ok → `docker compose ps` itself fails (rc != 0)
        with _run_with([_proc(0), _proc(1, stderr="daemon gone")]):
            record = execute_deploy(
                project="demo", target="compose", files=FILES_COMPOSE,
                state_root=tmp_path,
            )
        assert record.status == FAILED
        assert "could not complete" in record.message

    def test_kubernetes_previous_without_snapshot_uses_undo(self, tmp_path):
        import shutil

        save_record(tmp_path, _record("r0001", target="k8s"), FILES_K8S)
        shutil.rmtree(snapshot_dir(tmp_path, "demo", "r0001"))
        # apply ok → rollout status fails → no usable snapshot → native undo
        with _run_with([_proc(0), _proc(1, stderr="deadline exceeded"),
                        _proc(0)]):
            record = execute_deploy(
                project="demo", target="k8s", files=FILES_K8S,
                service_names=["api"], state_root=tmp_path,
            )
        assert record.status == ROLLED_BACK
        assert "rollout undo" in record.steps[-1].label

    def test_kubernetes_apply_failure_no_auto_rollback(self, tmp_path):
        with _run_with([_proc(1, stderr="boom")]):
            record = execute_deploy(
                project="demo", target="k8s", files=FILES_K8S,
                service_names=["api"], state_root=tmp_path,
                auto_rollback=False,
            )
        assert record.status == FAILED
        assert "auto-rollback disabled" in record.message
        assert len(record.steps) == 1  # rollout never attempted

    def test_kubernetes_rollout_failure_runs_undo(self, tmp_path):
        calls: List[List[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            if cmd[:2] == ["kubectl", "apply"]:
                return _proc(0)
            if "deployment/api" in cmd and "status" in cmd:
                return _proc(1, stderr="deadline exceeded")
            return _proc(0)

        with mock.patch("infra.deploy.engine.subprocess.run", fake_run):
            record = execute_deploy(
                project="demo", target="kubernetes", files=FILES_K8S,
                service_names=["api", "web"], state_root=tmp_path,
            )
        assert record.status == ROLLED_BACK
        undo_calls = [c for c in calls if "undo" in c]
        assert len(undo_calls) == 2  # one per service

    def test_kubernetes_apply_timeout(self, tmp_path):
        with _run_with(
            [subprocess.TimeoutExpired(cmd="kubectl", timeout=150)]
        ):
            record = execute_deploy(
                project="demo", target="kubernetes", files=FILES_K8S,
                state_root=tmp_path, auto_rollback=False,
            )
        assert record.status == FAILED
        assert "rc=None" in record.message

    def test_helm_rollout_status_failure_undo_native(self, tmp_path):
        with _run_with([_proc(0), _proc(1), _proc(0)]):
            record = execute_deploy(
                project="demo", target="helm", files=FILES_HELM,
                state_root=tmp_path,
            )
        assert record.status == ROLLED_BACK
        assert record.steps[-1].label == "helm rollback demo"

    def test_helm_previous_snapshot_reapply(self, tmp_path):
        save_record(tmp_path, _record("r0001", target="helm"), FILES_HELM)
        with _run_with([_proc(0), _proc(1, stderr="bad"), _proc(0)]):
            record = execute_deploy(
                project="demo", target="helm", files=FILES_HELM,
                state_root=tmp_path,
            )
        assert record.status == ROLLED_BACK
        assert "snapshots/r0001" in record.steps[-1].label.replace("\\", "/")

    def test_terraform_success_without_rollout(self, tmp_path):
        with _run_with([_proc(0)]) as run_mock:
            record = execute_deploy(
                project="demo", target="terraform", files=FILES_TF,
                state_root=tmp_path,
            )
        assert record.status == SUCCESS
        assert run_mock.call_count == 1  # apply only

    def test_terraform_failure_undo_unavailable(self, tmp_path):
        with _run_with([_proc(2)]):
            record = execute_deploy(
                project="demo", target="tf", files=FILES_TF,
                state_root=tmp_path,
            )
        assert record.status == FAILED
        assert "could not complete" in record.message

    def test_rollback_apply_also_fails(self, tmp_path):
        save_record(tmp_path, _record("r0001"), FILES_COMPOSE)
        with _run_with([_proc(1), _proc(1)]):
            record = execute_deploy(
                project="demo", target="compose", files=FILES_COMPOSE,
                state_root=tmp_path,
            )
        assert record.status == FAILED
        assert "could not complete" in record.message

    def test_revisions_increment(self, tmp_path):
        with _run_with([_proc(0), _proc(0), _proc(0), _proc(0)]):
            first = execute_deploy(
                project="demo", target="compose", files=FILES_COMPOSE,
                state_root=tmp_path,
            )
            second = execute_deploy(
                project="demo", target="compose", files=FILES_COMPOSE,
                state_root=tmp_path,
            )
        assert (first.revision, second.revision) == ("r0001", "r0002")

    def test_record_fields(self, tmp_path):
        with _run_with([_proc(0)]):
            record = execute_deploy(
                project="demo", target="terraform", files=FILES_TF,
                service_names=["api"], environment="prod",
                state_root=tmp_path, timeout=5,
            )
        assert record.environment == "prod"
        assert record.service_names == ("api",)
        assert record.files == ("main.tf",)
        assert record.duration_s >= 0.0
        assert record.tool == "terraform"
        on_disk = json.loads(
            (tmp_path / "demo" / "history" / "r0001.json").read_text(
                encoding="utf-8"
            )
        )
        assert on_disk["status"] == SUCCESS


# --------------------------------------------------------------------------- #
# execute_rollback (engine)
# --------------------------------------------------------------------------- #


class TestExecuteRollback:
    def test_restore_success(self, tmp_path):
        original = _record(
            "r0001", project="demo", target="compose",
        )
        original = DeployRecord(
            **{**original.to_dict(), "environment": "prod",
               "service_names": ["api"], "steps": []}
        )
        save_record(tmp_path, original, FILES_COMPOSE)
        with _run_with([_proc(0)]):
            restored = execute_rollback(
                state_root=tmp_path, project="demo", target="compose",
                revision="r0001",
            )
        assert restored.status == RESTORED
        assert restored.revision == "r0002"
        assert "restored snapshot r0001" in restored.message
        assert restored.environment == "prod"
        assert restored.service_names == ("api",)
        assert load_snapshot(tmp_path, "demo", "r0002") == FILES_COMPOSE

    def test_unknown_revision_raises(self, tmp_path):
        with pytest.raises(LookupError, match="not found"):
            execute_rollback(
                state_root=tmp_path, project="demo", target="compose",
                revision="r9999",
            )

    def test_missing_snapshot_raises(self, tmp_path):
        save_record(tmp_path, _record("r0001"), FILES_COMPOSE)
        import shutil

        shutil.rmtree(snapshot_dir(tmp_path, "demo", "r0001"))
        with pytest.raises(LookupError, match="snapshot"):
            execute_rollback(
                state_root=tmp_path, project="demo", target="compose",
                revision="r0001",
            )

    def test_restore_apply_failure(self, tmp_path):
        save_record(tmp_path, _record("r0001", target="k8s"), FILES_K8S)
        with _run_with([_proc(1)]):
            record = execute_rollback(
                state_root=tmp_path, project="demo", target="kubernetes",
                revision="r0001",
            )
        assert record.status == FAILED
        assert "rollback apply failed" in record.message

    def test_restore_is_recorded_in_history(self, tmp_path):
        save_record(tmp_path, _record("r0001"), FILES_COMPOSE)
        with _run_with([_proc(0)]):
            execute_rollback(
                state_root=tmp_path, project="demo", target="compose",
                revision="r0001",
            )
        statuses = [r.status for r in list_history(tmp_path, "demo")]
        assert statuses == [SUCCESS, RESTORED]


# --------------------------------------------------------------------------- #
# CLI: infra deploy
# --------------------------------------------------------------------------- #


@pytest.fixture()
def tool_present(monkeypatch):
    monkeypatch.setattr("infra.cli.deploy_cmd.have_tool", lambda b: True)


def _invoke(*args: str):
    return runner.invoke(app, list(args))


class TestCliDeploy:
    def test_help_robust(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "120")
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.setenv("FORCE_COLOR", "0")
        result = _invoke("deploy", "--help")
        assert result.exit_code == 0
        flat = _flat(result.output)
        for option in ("--target", "--apply", "--timeout",
                       "--auto-rollback", "--force"):
            assert re.search(re.escape(option), flat), option

    def test_dry_run_default(self, tmp_path):
        f = _write(tmp_path)
        result = _invoke("deploy", str(f), "--target", "kubernetes")
        assert result.exit_code == 0, result.output
        out = _flat(result.output)
        assert "Deployment plan" in out
        assert "service api" in out and "database db" in out
        assert "estimated monthly cost:" in out
        assert "$ kubectl apply -f" in result.output
        # PLAN recorded in .infra-state next to the file
        record = json.loads(
            (tmp_path / ".infra-state" / "demo" / "history" / "r0001.json")
            .read_text(encoding="utf-8")
        )
        assert record["status"] == PLANNED

    def test_plan_shows_security_findings(self, tmp_path):
        f = tmp_path / "risky.infra"
        f.write_text(
            DEMO.replace('image: "registry.example.com/api:2.1"',
                         'image: "api:latest"'),
            encoding="utf-8",
        )
        result = _invoke("deploy", str(f), "--target", "compose")
        assert result.exit_code == 0
        assert "SEC003" in _flat(result.output)

    def test_missing_file(self, tmp_path):
        result = _invoke("deploy", str(tmp_path / "nope.infra"))
        assert result.exit_code == 1
        assert "not found" in _flat(result.output)

    def test_bad_target(self, tmp_path):
        f = _write(tmp_path)
        result = _invoke("deploy", str(f), "--target", "mesos")
        assert result.exit_code == 1
        assert "Unsupported target" in _flat(result.output)

    def test_parse_error(self, tmp_path):
        f = _write(tmp_path, source="service {\n")
        result = _invoke("deploy", str(f))
        assert result.exit_code == 1
        assert "Cannot parse" in _flat(result.output)

    def test_semantic_error_blocks(self, tmp_path):
        f = tmp_path / "bad.infra"
        f.write_text(
            'service a {\n  image: "x:1"\n  env { DB_PASSWORD: "p" }\n}\n',
            encoding="utf-8",
        )
        result = _invoke("deploy", str(f))
        assert result.exit_code == 1
        assert "SEC001" in _flat(result.output)

    def test_apply_missing_tool(self, tmp_path, monkeypatch):
        monkeypatch.setattr("infra.cli.deploy_cmd.have_tool", lambda b: False)
        f = _write(tmp_path)
        result = _invoke("deploy", str(f), "--apply")
        assert result.exit_code == 1
        assert "not found" in _flat(result.output)

    def test_compile_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "infra.cli.deploy_cmd.get_backend",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        f = _write(tmp_path)
        result = _invoke("deploy", str(f))
        assert result.exit_code == 1
        assert "Compilation failed" in _flat(result.output)

    def test_apply_success_compose(self, tmp_path, tool_present):
        f = _write(tmp_path)
        with _run_with([_proc(0), _proc(0, stdout="running")]):
            result = _invoke("deploy", str(f), "-t", "compose", "--apply")
        assert result.exit_code == 0, result.output
        assert "success" in _flat(result.output)
        history = list_history(tmp_path / ".infra-state", "demo")
        assert history[-1].status == SUCCESS

    def test_apply_via_force_alias(self, tmp_path, tool_present):
        f = _write(tmp_path)
        with _run_with([_proc(0), _proc(0, stdout="running")]):
            result = _invoke("deploy", str(f), "-t", "compose", "--force")
        assert result.exit_code == 0

    def test_apply_failure_exit_1(self, tmp_path, tool_present):
        f = _write(tmp_path)
        with _run_with([_proc(1, stderr="boom")]):
            result = _invoke(
                "deploy", str(f), "-t", "kubernetes",
                "--apply", "--no-auto-rollback",
            )
        assert result.exit_code == 1
        assert "failed" in _flat(result.output)

    def test_environment_recorded(self, tmp_path):
        f = _write(tmp_path)
        result = _invoke("deploy", str(f), "-e", "prod")
        assert result.exit_code == 0
        record = json.loads(
            (tmp_path / ".infra-state" / "demo" / "history" / "r0001.json")
            .read_text(encoding="utf-8")
        )
        assert record["environment"] == "prod"


# --------------------------------------------------------------------------- #
# CLI: infra rollback
# --------------------------------------------------------------------------- #


class TestCliRollback:
    def test_help_robust(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "120")
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.setenv("FORCE_COLOR", "0")
        result = _invoke("rollback", "--help")
        assert result.exit_code == 0
        flat = _flat(result.output)
        assert re.search(r"--to-revision", flat)
        assert re.search(r"--timeout", flat)

    def test_empty_history(self, tmp_path):
        f = _write(tmp_path)
        result = _invoke("rollback", str(f))
        assert result.exit_code == 0
        assert "No deploy history" in _flat(result.output)

    def test_history_table(self, tmp_path):
        f = _write(tmp_path)
        state = tmp_path / ".infra-state"
        save_record(state, _record("r0001", project="demo"), FILES_COMPOSE)
        save_record(
            state,
            _record("r0002", project="demo", status=ROLLED_BACK,
                    timestamp="2026-09-02T11:00:00+00:00"),
            FILES_COMPOSE,
        )
        result = _invoke("rollback", str(f))
        assert result.exit_code == 0
        flat = _flat(result.output)
        assert "r0001" in flat and "r0002" in flat
        assert "[rolled-back]" in flat  # not swallowed by Rich markup
        assert "--to-revision" in flat

    def test_missing_file(self, tmp_path):
        result = _invoke("rollback", str(tmp_path / "nope.infra"))
        assert result.exit_code == 1

    def test_bad_target(self, tmp_path):
        f = _write(tmp_path)
        result = _invoke("rollback", str(f), "--target", "mesos")
        assert result.exit_code == 1

    def test_to_revision_unknown(self, tmp_path, tool_present):
        f = _write(tmp_path)
        result = _invoke(
            "rollback", str(f), "-t", "compose", "--to-revision", "r9999"
        )
        assert result.exit_code == 1
        assert "not found" in _flat(result.output)

    def test_to_revision_missing_tool(self, tmp_path, monkeypatch):
        monkeypatch.setattr("infra.cli.deploy_cmd.have_tool", lambda b: False)
        f = _write(tmp_path)
        result = _invoke(
            "rollback", str(f), "-t", "compose", "--to-revision", "r0001"
        )
        assert result.exit_code == 1
        assert "not found" in _flat(result.output)

    def test_to_revision_success(self, tmp_path, tool_present):
        f = _write(tmp_path)
        save_record(
            tmp_path / ".infra-state", _record("r0001", project="demo"),
            FILES_COMPOSE,
        )
        with _run_with([_proc(0)]):
            result = _invoke(
                "rollback", str(f), "-t", "compose", "--to-revision", "r0001"
            )
        assert result.exit_code == 0, result.output
        assert "restored" in _flat(result.output)
        history = list_history(tmp_path / ".infra-state", "demo")
        assert history[-1].status == RESTORED
