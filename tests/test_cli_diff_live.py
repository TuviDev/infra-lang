"""Contract tests for `infra diff --live` (live plan & preview, v0.4.3).

Follows the pattern of tests/test_cli_drift.py: all kubectl / docker
responses are monkeypatched (no real cluster or daemon is touched); the
probes themselves are read-only by design.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import infra.analyzer.drift as drift_mod
from infra.cli.main import app

runner = CliRunner()

SPEC = (
    'service api { image: "nginx:1.25" port 8080 replicas: 3 '
    'env { LOG_LEVEL: "info" } }'
)

#: Base spec + a "prod" environment overlay that bumps replicas to 5.
OVERLAY_SPEC = (
    'service api { image: "nginx:1.25" port 8080 replicas: 3 '
    'env { LOG_LEVEL: "info" } } '
    'environment "prod" { service api { replicas: 5 } }'
)


def write_spec(tmp_path: Path, content: str = SPEC, name: str = "app.infra") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def k8s_payload(
    name: str = "api",
    replicas: int = 3,
    image: str = "nginx:1.25",
    ports=(8080,),
    env=(("LOG_LEVEL", "info"),),
) -> str:
    return json.dumps(
        {
            "items": [
                {
                    "kind": "Deployment",
                    "metadata": {"name": name},
                    "spec": {
                        "replicas": replicas,
                        "template": {
                            "spec": {
                                "containers": [
                                    {
                                        "image": image,
                                        "ports": [{"containerPort": p} for p in ports],
                                        "env": [
                                            {"name": k, "value": v} for k, v in env
                                        ],
                                    }
                                ]
                            }
                        },
                    },
                },
                {"kind": "Service", "metadata": {"name": name}},
            ]
        }
    )


@pytest.fixture
def tools_on_path(monkeypatch):
    monkeypatch.setattr(drift_mod.shutil, "which", lambda name: f"/usr/bin/{name}")


@pytest.fixture
def no_tools(monkeypatch):
    monkeypatch.setattr(drift_mod.shutil, "which", lambda name: None)


def patch_kubectl(monkeypatch, stdout: str, returncode: int = 0) -> None:
    def fake_run(cmd, **kwargs):
        assert cmd[0] == "kubectl"
        # read-only contract: a plan never applies, deletes or scales
        forbidden = {"apply", "delete", "scale", "patch", "create", "replace"}
        assert not forbidden.intersection(cmd)
        assert "get" in cmd
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(drift_mod.subprocess, "run", fake_run)


def patch_docker(monkeypatch, ps_stdout: str, inspect_stdout: str = "[]") -> None:
    def fake_run(cmd, **kwargs):
        assert cmd[0] == "docker"
        assert "ps" in cmd or "inspect" in cmd  # read-only contract
        out = ps_stdout if "ps" in cmd else inspect_stdout
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(drift_mod.subprocess, "run", fake_run)


class TestDiffLivePlanK8s:
    def test_plan_replica_change_exit_1(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, k8s_payload(replicas=2))
        result = runner.invoke(app, ["diff", str(src), "--live"])
        assert result.exit_code == 1
        assert '~ service "api":' in result.stdout
        assert "replicas: 2 -> 3" in result.stdout
        assert "Plan:" in result.stdout

    def test_plan_image_change_quoted(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, k8s_payload(image="nginx:1.24"))
        result = runner.invoke(app, ["diff", str(src), "--live"])
        assert result.exit_code == 1
        assert 'image: "nginx:1.24" -> "nginx:1.25"' in result.stdout

    def test_plan_multiple_changes_grouped(self, tmp_path, monkeypatch, tools_on_path):
        """The task's reference output: several fields under one service."""
        src = write_spec(tmp_path)
        patch_kubectl(
            monkeypatch,
            k8s_payload(replicas=2, image="nginx:1.24"),
        )
        result = runner.invoke(app, ["diff", str(src), "--live"])
        assert result.exit_code == 1
        stdout = result.stdout
        assert stdout.count('~ service "api":') == 1  # grouped, not repeated
        assert "replicas: 2 -> 3" in stdout
        assert 'image: "nginx:1.24" -> "nginx:1.25"' in stdout

    def test_plan_env_change(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, k8s_payload(env=(("LOG_LEVEL", "debug"),)))
        result = runner.invoke(app, ["diff", str(src), "--live"])
        assert result.exit_code == 1
        assert 'env:LOG_LEVEL: "debug" -> "info"' in result.stdout

    def test_plan_no_changes_exit_0(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, k8s_payload())
        result = runner.invoke(app, ["diff", str(src), "--live"])
        assert result.exit_code == 0
        assert "No changes" in result.stdout
        assert '= service "api" (unchanged)' in result.stdout

    def test_plan_missing_service_is_create(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, json.dumps({"items": []}))
        result = runner.invoke(app, ["diff", str(src), "--live"])
        assert result.exit_code == 1
        assert '+ service "api"' in result.stdout
        assert "will be created" in result.stdout
        assert "1 to create" in result.stdout

    def test_plan_hint_to_apply(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, k8s_payload(replicas=1))
        result = runner.invoke(app, ["diff", str(src), "--live"])
        assert "infra up" in result.stdout

    def test_plan_json_output(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, k8s_payload(replicas=1))
        result = runner.invoke(app, ["diff", str(src), "--live", "--format", "json"])
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert data["has_drift"] is True
        assert data["target"] == "k8s"
        assert data["source"] == str(src)
        assert data["namespace"] == "default"
        assert data["drift"][0]["parameter"] == "replicas"
        assert data["drift"][0]["expected"] == "3"
        assert data["drift"][0]["live"] == "1"

    def test_plan_json_no_changes_exit_0(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, k8s_payload())
        result = runner.invoke(app, ["diff", str(src), "--live", "-f", "json"])
        assert result.exit_code == 0
        assert json.loads(result.stdout)["has_drift"] is False

    def test_plan_kubernetes_alias(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, k8s_payload())
        result = runner.invoke(app, ["diff", str(src), "--live", "-t", "kubernetes"])
        assert result.exit_code == 0
        assert "k8s" in result.stdout

    def test_plan_namespace_custom(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout=k8s_payload(), stderr="")

        monkeypatch.setattr(drift_mod.subprocess, "run", fake_run)
        result = runner.invoke(
            app, ["diff", str(src), "--live", "--namespace", "staging"]
        )
        assert result.exit_code == 0
        assert "-n" in seen["cmd"]
        assert seen["cmd"][seen["cmd"].index("-n") + 1] == "staging"
        assert "namespace=staging" in result.stdout

    def test_plan_namespace_short_flag(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout=k8s_payload(), stderr="")

        monkeypatch.setattr(drift_mod.subprocess, "run", fake_run)
        result = runner.invoke(app, ["diff", str(src), "--live", "-n", "prod"])
        assert result.exit_code == 0
        assert "prod" in seen["cmd"]


class TestDiffLivePlanCompose:
    def _ps_row(self, service="api", image="nginx:1.25", cid="abc123") -> str:
        return json.dumps({"Service": service, "Image": image, "ID": cid})

    def _inspect(self, image="nginx:1.25", env=("LOG_LEVEL=info",), ports=(8080,)):
        return json.dumps(
            [
                {
                    "Config": {
                        "Image": image,
                        "Env": list(env),
                        "ExposedPorts": {f"{p}/tcp": {} for p in ports},
                    }
                }
            ]
        )

    def test_plan_compose_replica_change(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_docker(monkeypatch, self._ps_row(), self._inspect())
        result = runner.invoke(app, ["diff", str(src), "--live", "-t", "compose"])
        assert result.exit_code == 1
        assert "replicas: 1 -> 3" in result.stdout

    def test_plan_compose_in_sync_exit_0(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path, SPEC.replace("replicas: 3", "replicas: 1"))
        patch_docker(monkeypatch, self._ps_row(), self._inspect())
        result = runner.invoke(app, ["diff", str(src), "--live", "-t", "compose"])
        assert result.exit_code == 0
        assert "No changes" in result.stdout

    def test_plan_compose_docker_alias(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path, SPEC.replace("replicas: 3", "replicas: 1"))
        patch_docker(monkeypatch, self._ps_row(), self._inspect())
        result = runner.invoke(app, ["diff", str(src), "--live", "-t", "docker"])
        assert result.exit_code == 0
        assert "compose" in result.stdout


class TestDiffLiveEnvOverlay:
    def test_env_overlay_changes_plan(self, tmp_path, monkeypatch, tools_on_path):
        """`-e prod` prices the overlay, so the plan targets 5 replicas."""
        src = write_spec(tmp_path, OVERLAY_SPEC)
        patch_kubectl(monkeypatch, k8s_payload(replicas=3))
        result = runner.invoke(app, ["diff", str(src), "--live", "-e", "prod"])
        assert result.exit_code == 1
        assert "replicas: 3 -> 5" in result.stdout

    def test_without_overlay_in_sync(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path, OVERLAY_SPEC)
        patch_kubectl(monkeypatch, k8s_payload(replicas=3))
        result = runner.invoke(app, ["diff", str(src), "--live"])
        assert result.exit_code == 0

    def test_unknown_environment_exit_1(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path, OVERLAY_SPEC)
        patch_kubectl(monkeypatch, k8s_payload())
        result = runner.invoke(app, ["diff", str(src), "--live", "-e", "nope"])
        assert result.exit_code == 1


class TestDiffLiveErrors:
    def test_unknown_target(self, tmp_path, tools_on_path):
        src = write_spec(tmp_path)
        result = runner.invoke(app, ["diff", str(src), "--live", "-t", "bogus"])
        assert result.exit_code == 1
        assert "bogus" in result.stdout
        assert "k8s, compose" in result.stdout

    def test_kubectl_missing(self, tmp_path, no_tools):
        src = write_spec(tmp_path)
        result = runner.invoke(app, ["diff", str(src), "--live"])
        assert result.exit_code == 1
        assert "kubectl" in result.stdout

    def test_docker_missing(self, tmp_path, no_tools):
        src = write_spec(tmp_path)
        result = runner.invoke(app, ["diff", str(src), "--live", "-t", "compose"])
        assert result.exit_code == 1
        assert "docker" in result.stdout

    def test_source_file_not_found(self, tmp_path):
        result = runner.invoke(app, ["diff", str(tmp_path / "nope.infra"), "--live"])
        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_parse_error_exit_1(self, tmp_path, tools_on_path):
        bad = write_spec(tmp_path, "service {{{", name="bad.infra")
        result = runner.invoke(app, ["diff", str(bad), "--live"])
        assert result.exit_code == 1
        assert "Plan failed" in result.stdout
        assert "Traceback" not in result.stdout

    def test_probe_failure_exit_1(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, "", returncode=1)
        result = runner.invoke(app, ["diff", str(src), "--live"])
        assert result.exit_code == 1
        assert "Live plan failed" in result.stdout


class TestDiffCliUsageContract:
    def test_live_rejects_second_file(self, tmp_path):
        a = write_spec(tmp_path, name="a.infra")
        b = write_spec(tmp_path, name="b.infra")
        result = runner.invoke(app, ["diff", str(a), str(b), "--live"])
        assert result.exit_code == 2
        assert "second file" in result.output

    def test_file_mode_requires_second_file(self, tmp_path):
        a = write_spec(tmp_path)
        result = runner.invoke(app, ["diff", str(a)])
        assert result.exit_code == 2
        assert "--live" in result.output

    def test_legacy_file_vs_file_unchanged(self, tmp_path):
        """Backward compatibility: two-file diff keeps working, exit 0."""
        a = write_spec(tmp_path, name="a.infra")
        b = write_spec(
            tmp_path, SPEC.replace("replicas: 3", "replicas: 5"), name="b.infra"
        )
        result = runner.invoke(app, ["diff", str(a), str(b)])
        assert result.exit_code == 0
        assert "SUMMARY" in result.stdout
        assert "replicas" in result.stdout

    def test_legacy_file_vs_file_json_unchanged(self, tmp_path):
        a = write_spec(tmp_path, name="a.infra")
        b = write_spec(
            tmp_path, SPEC.replace("replicas: 3", "replicas: 5"), name="b.infra"
        )
        result = runner.invoke(app, ["diff", str(a), str(b), "-f", "json"])
        assert result.exit_code == 0
        assert json.loads(result.stdout)["has_changes"] is True


class TestDiffLiveReadOnlyContract:
    def test_k8s_probe_is_read_only(self, tmp_path, monkeypatch, tools_on_path):
        """`diff --live` must never mutate the cluster (kubectl get only)."""
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, k8s_payload())  # asserts get-only inside
        result = runner.invoke(app, ["diff", str(src), "--live"])
        assert result.exit_code == 0

    def test_compose_probe_is_read_only(self, tmp_path, monkeypatch, tools_on_path):
        """`diff --live` must never mutate the daemon (ps/inspect only)."""
        src = write_spec(tmp_path, SPEC.replace("replicas: 3", "replicas: 1"))
        row = json.dumps({"Service": "api", "Image": "nginx:1.25", "ID": "abc"})
        patch_docker(monkeypatch, row, "[]")  # asserts ps/inspect-only inside
        result = runner.invoke(app, ["diff", str(src), "--live", "-t", "compose"])
        assert result.exit_code == 0
