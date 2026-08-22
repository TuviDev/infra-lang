"""Contract tests for `infra doctor --check-drift --live` (live drift, v0.4.2).

All kubectl / docker responses are monkeypatched (no real cluster or daemon
is touched); the probes themselves are read-only by design.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import infra.analyzer.drift as drift_mod
from infra.analyzer.drift import (
    DriftItem,
    DriftReport,
    _parse_compose_ps,
    detect_live_drift,
)
from infra.cli.main import app

runner = CliRunner()

SPEC = (
    'service api { image: "nginx:1.25" port 8080 replicas: 3 '
    'env { LOG_LEVEL: "info" } }'
)


def write_spec(tmp_path: Path, content: str = SPEC) -> Path:
    p = tmp_path / "app.infra"
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
                                        "ports": [
                                            {"containerPort": p} for p in ports
                                        ],
                                        "env": [
                                            {"name": k, "value": v}
                                            for k, v in env
                                        ],
                                    }
                                ]
                            }
                        },
                    },
                },
                # A Service object must be ignored by the comparison.
                {"kind": "Service", "metadata": {"name": name}},
            ]
        }
    )


@pytest.fixture
def tools_on_path(monkeypatch):
    monkeypatch.setattr(
        drift_mod.shutil, "which", lambda name: f"/usr/bin/{name}"
    )


@pytest.fixture
def no_tools(monkeypatch):
    monkeypatch.setattr(drift_mod.shutil, "which", lambda name: None)


def patch_kubectl(monkeypatch, stdout: str, returncode: int = 0) -> None:
    def fake_run(cmd, **kwargs):
        assert cmd[0] == "kubectl"
        assert "get" in cmd  # read-only contract: never apply/delete/scale
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(drift_mod.subprocess, "run", fake_run)


def patch_docker(monkeypatch, ps_stdout: str, inspect_stdout: str = "[]") -> None:
    def fake_run(cmd, **kwargs):
        assert cmd[0] == "docker"
        assert "ps" in cmd or "inspect" in cmd  # read-only contract
        out = ps_stdout if "ps" in cmd else inspect_stdout
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(drift_mod.subprocess, "run", fake_run)


class TestDetectLiveDriftK8s:
    def test_in_sync(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, k8s_payload())
        report = detect_live_drift(src, target="k8s")
        assert report.clean
        assert not report.has_drift
        assert report.in_sync == ["api"]
        assert report.error is None

    def test_replica_drift(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, k8s_payload(replicas=1))
        report = detect_live_drift(src, target="k8s")
        assert report.has_drift
        item = report.items[0]
        assert item.parameter == "replicas"
        assert item.expected == "3"
        assert item.live == "1"
        assert item.status == "MODIFIED"

    def test_drift_line_format(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, k8s_payload(replicas=1))
        report = detect_live_drift(src, target="k8s")
        assert (
            "[DRIFT] api: replicas expected 3, live 1 (MODIFIED)"
            in report.render_lines()
        )

    def test_image_drift(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, k8s_payload(image="nginx:1.24"))
        report = detect_live_drift(src, target="k8s")
        params = [i.parameter for i in report.items]
        assert params == ["image"]
        assert report.items[0].expected == "nginx:1.25"
        assert report.items[0].live == "nginx:1.24"

    def test_port_drift(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, k8s_payload(ports=(9090,)))
        report = detect_live_drift(src, target="k8s")
        assert [i.parameter for i in report.items] == ["ports"]
        assert report.items[0].expected == "8080"
        assert report.items[0].live == "9090"

    def test_env_value_drift(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, k8s_payload(env=(("LOG_LEVEL", "debug"),)))
        report = detect_live_drift(src, target="k8s")
        assert [i.parameter for i in report.items] == ["env:LOG_LEVEL"]
        assert report.items[0].live == "debug"

    def test_env_missing_live(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, k8s_payload(env=()))
        report = detect_live_drift(src, target="k8s")
        assert report.items[0].parameter == "env:LOG_LEVEL"
        assert report.items[0].live == "unset"
        assert report.items[0].status == "MISSING"

    def test_deployment_missing(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, json.dumps({"items": []}))
        report = detect_live_drift(src, target="k8s")
        assert report.has_drift
        assert report.items[0].status == "MISSING"
        assert report.items[0].live == "absent"

    def test_multiple_drifts_reported(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(
            monkeypatch,
            k8s_payload(replicas=1, image="nginx:1.20", env=(("LOG_LEVEL", "x"),)),
        )
        report = detect_live_drift(src, target="k8s")
        assert {i.parameter for i in report.items} == {
            "replicas",
            "image",
            "env:LOG_LEVEL",
        }

    def test_kubectl_missing(self, tmp_path, no_tools):
        src = write_spec(tmp_path)
        report = detect_live_drift(src, target="k8s")
        assert report.error is not None
        assert "kubectl" in report.error

    def test_kubectl_failure(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, "", returncode=1)
        report = detect_live_drift(src, target="k8s")
        assert report.error is not None

    def test_kubectl_bad_json(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, "not json {")
        report = detect_live_drift(src, target="k8s")
        assert report.error is not None

    def test_kubernetes_alias(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, k8s_payload())
        report = detect_live_drift(src, target="kubernetes")
        assert report.target == "k8s"
        assert report.clean

    def test_namespace_passed_to_kubectl(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(
                cmd, 0, stdout=k8s_payload(), stderr=""
            )

        monkeypatch.setattr(drift_mod.subprocess, "run", fake_run)
        detect_live_drift(src, target="k8s", namespace="staging")
        assert "-n" in seen["cmd"]
        assert seen["cmd"][seen["cmd"].index("-n") + 1] == "staging"

    def test_unknown_target(self, tmp_path):
        src = write_spec(tmp_path)
        report = detect_live_drift(src, target="bogus")
        assert report.error is not None
        assert "bogus" in report.error


class TestDetectLiveDriftCompose:
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

    def test_compose_replica_drift(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_docker(monkeypatch, self._ps_row(), self._inspect())
        report = detect_live_drift(src, target="compose")
        assert report.target == "compose"
        assert [i.parameter for i in report.items] == ["replicas"]
        assert report.items[0].live == "1"

    def test_compose_in_sync(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path, SPEC.replace("replicas: 3", "replicas: 1"))
        patch_docker(monkeypatch, self._ps_row(), self._inspect())
        report = detect_live_drift(src, target="compose")
        assert report.clean
        assert report.in_sync == ["api"]

    def test_compose_image_drift(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path, SPEC.replace("replicas: 3", "replicas: 1"))
        patch_docker(
            monkeypatch,
            self._ps_row(image="nginx:1.24"),
            self._inspect(image="nginx:1.24"),
        )
        report = detect_live_drift(src, target="compose")
        assert [i.parameter for i in report.items] == ["image"]

    def test_compose_env_drift(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path, SPEC.replace("replicas: 3", "replicas: 1"))
        patch_docker(
            monkeypatch,
            self._ps_row(),
            self._inspect(env=("LOG_LEVEL=debug", "PATH=/usr/bin")),
        )
        report = detect_live_drift(src, target="compose")
        assert [i.parameter for i in report.items] == ["env:LOG_LEVEL"]
        assert report.items[0].live == "debug"

    def test_compose_service_missing(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_docker(monkeypatch, "", "[]")
        report = detect_live_drift(src, target="compose")
        assert report.items[0].status == "MISSING"

    def test_compose_scaled_replicas(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        rows = "\n".join(
            self._ps_row(cid=f"c{i}") for i in range(3)
        )  # NDJSON: 3 containers
        patch_docker(monkeypatch, rows, self._inspect())
        report = detect_live_drift(src, target="compose")
        assert report.clean  # 3 declared == 3 running

    def test_docker_missing(self, tmp_path, no_tools):
        src = write_spec(tmp_path)
        report = detect_live_drift(src, target="compose")
        assert report.error is not None
        assert "docker" in report.error

    def test_docker_alias(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path, SPEC.replace("replicas: 3", "replicas: 1"))
        patch_docker(monkeypatch, self._ps_row(), self._inspect())
        report = detect_live_drift(src, target="docker")
        assert report.target == "compose"


class TestLiveStateEdgeCases:
    def test_kubectl_oserror(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)

        def raise_oserror(cmd, **kwargs):
            raise OSError("boom")

        monkeypatch.setattr(drift_mod.subprocess, "run", raise_oserror)
        report = detect_live_drift(src, target="k8s")
        assert report.error is not None

    def test_kubectl_timeout(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)

        def raise_timeout(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 30)

        monkeypatch.setattr(drift_mod.subprocess, "run", raise_timeout)
        report = detect_live_drift(src, target="k8s")
        assert report.error is not None

    def test_k8s_malformed_items_skipped(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        payload = json.dumps(
            {
                "items": [
                    "not-a-dict",
                    {"kind": "Deployment", "metadata": {}},  # no name
                    {"kind": "Deployment", "metadata": {"name": "api"}, "spec": {}},
                ]
            }
        )
        patch_kubectl(monkeypatch, payload)
        report = detect_live_drift(src, target="k8s")
        # deployment exists but has no containers -> only comparable fields
        # are skipped; nothing crashes
        assert report.error is None

    def test_k8s_ports_without_container_port(
        self, tmp_path, monkeypatch, tools_on_path
    ):
        src = write_spec(tmp_path)
        payload = json.dumps(
            {
                "items": [
                    {
                        "kind": "Deployment",
                        "metadata": {"name": "api"},
                        "spec": {
                            "replicas": 3,
                            "template": {
                                "spec": {
                                    "containers": [
                                        {
                                            "image": "nginx:1.25",
                                            "ports": [{"name": "http"}],
                                            "env": [
                                                {
                                                    "name": "LOG_LEVEL",
                                                    "value": "info",
                                                },
                                                {
                                                    "name": "SECRET",
                                                    "valueFrom": {},
                                                },
                                            ],
                                        }
                                    ]
                                }
                            },
                        },
                    }
                ]
            }
        )
        patch_kubectl(monkeypatch, payload)
        report = detect_live_drift(src, target="k8s")
        # live port list is empty -> ports drift against expected 8080
        assert [i.parameter for i in report.items] == ["ports"]

    def test_spec_env_reference_not_compared(
        self, tmp_path, monkeypatch, tools_on_path
    ):
        """`from secret` env entries resolve cluster-side and are skipped."""
        spec = (
            'service api { image: "nginx:1.25" port 8080 replicas: 3 '
            'env { KEY: from secret "creds.key" } }'
        )
        src = write_spec(tmp_path, spec)
        patch_kubectl(monkeypatch, k8s_payload(env=()))
        report = detect_live_drift(src, target="k8s")
        assert report.clean

    def test_compose_ps_failure(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="err")

        monkeypatch.setattr(drift_mod.subprocess, "run", fake_run)
        report = detect_live_drift(src, target="compose")
        assert report.error is not None

    def test_compose_row_without_service_name(
        self, tmp_path, monkeypatch, tools_on_path
    ):
        src = write_spec(tmp_path)
        patch_docker(monkeypatch, json.dumps({"Image": "nginx:1.25"}), "[]")
        report = detect_live_drift(src, target="compose")
        # row is skipped -> service counts as missing
        assert report.items[0].status == "MISSING"

    def test_compose_row_without_container_id(
        self, tmp_path, monkeypatch, tools_on_path
    ):
        src = write_spec(tmp_path, SPEC.replace("replicas: 3", "replicas: 1"))
        # no ID -> docker inspect is never called; image from ps row only
        patch_docker(
            monkeypatch, json.dumps({"Service": "api", "Image": "nginx:1.25"})
        )
        report = detect_live_drift(src, target="compose")
        assert not any(i.parameter == "image" for i in report.items)

    def test_compose_inspect_bad_json(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path, SPEC.replace("replicas: 3", "replicas: 1"))
        patch_docker(
            monkeypatch,
            json.dumps({"Service": "api", "Image": "nginx:1.25", "ID": "abc"}),
            "not json {",
        )
        report = detect_live_drift(src, target="compose")
        # inspect failed -> env/ports unknown, not reported as drift
        assert not any(
            i.parameter.startswith("env:") or i.parameter == "ports"
            for i in report.items
        )

    def test_compose_inspect_empty_list(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path, SPEC.replace("replicas: 3", "replicas: 1"))
        patch_docker(
            monkeypatch,
            json.dumps({"Service": "api", "Image": "nginx:1.25", "ID": "abc"}),
            "[]",
        )
        report = detect_live_drift(src, target="compose")
        assert report.error is None

    def test_compose_image_from_inspect_only(
        self, tmp_path, monkeypatch, tools_on_path
    ):
        """When `ps` has no Image, the image comes from docker inspect."""
        src = write_spec(tmp_path, SPEC.replace("replicas: 3", "replicas: 1"))
        inspect_out = json.dumps(
            [
                {
                    "Config": {
                        "Image": "nginx:1.24",
                        "Env": ["LOG_LEVEL=info", "NOEQUALS"],
                        "ExposedPorts": {"8080/tcp": {}, "bogus/tcp": {}},
                    }
                }
            ]
        )
        patch_docker(
            monkeypatch, json.dumps({"Service": "api", "ID": "abc"}), inspect_out
        )
        report = detect_live_drift(src, target="compose")
        assert any(
            i.parameter == "image" and i.live == "nginx:1.24"
            for i in report.items
        )


class TestParseComposePs:
    def test_ndjson(self):
        text = '{"Service": "a"}\n{"Service": "b"}\n'
        assert [r["Service"] for r in _parse_compose_ps(text)] == ["a", "b"]

    def test_json_array(self):
        text = '[{"Service": "a"}, {"Service": "b"}]'
        assert len(_parse_compose_ps(text)) == 2

    def test_empty(self):
        assert _parse_compose_ps("") == []
        assert _parse_compose_ps("   \n  ") == []

    def test_malformed_lines_skipped(self):
        text = '{"Service": "a"}\nnot-json\n{"Service": "b"}'
        assert len(_parse_compose_ps(text)) == 2

    def test_malformed_array(self):
        assert _parse_compose_ps("[ broken") == []


class TestDriftReportSerialization:
    def test_to_dict_shape(self):
        report = DriftReport(
            target="k8s",
            items=[
                DriftItem(
                    resource="api", parameter="replicas", expected="3", live="1"
                )
            ],
            in_sync=["worker"],
        )
        data = report.to_dict()
        assert data["target"] == "k8s"
        assert data["has_drift"] is True
        assert data["in_sync"] == ["worker"]
        assert data["drift"][0] == {
            "resource": "api",
            "parameter": "replicas",
            "expected": "3",
            "live": "1",
            "status": "MODIFIED",
        }
        assert data["error"] is None
        # must round-trip through json for CI gates
        assert json.loads(json.dumps(data)) == data

    def test_clean_report(self):
        report = DriftReport(target="k8s", in_sync=["api"])
        assert report.clean
        assert not report.has_drift
        assert report.render_lines() == []


class TestDoctorLiveDriftCLI:
    def test_cli_drift_table_and_exit_1(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, k8s_payload(replicas=1))
        result = runner.invoke(
            app, ["doctor", "--check-drift", str(src), "--live", "-t", "k8s"]
        )
        assert result.exit_code == 1
        assert "replicas" in result.stdout
        assert "Drifted" in result.stdout
        assert "[DRIFT] api: replicas expected 3, live 1 (MODIFIED)" in result.stdout

    def test_cli_in_sync_exit_0(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, k8s_payload())
        result = runner.invoke(
            app, ["doctor", "--check-drift", str(src), "--live"]
        )
        assert result.exit_code == 0
        assert "In-Sync" in result.stdout
        assert "No live drift detected" in result.stdout

    def test_cli_json_output(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, k8s_payload(replicas=1))
        result = runner.invoke(
            app, ["doctor", "--check-drift", str(src), "--live", "--json"]
        )
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert data["has_drift"] is True
        assert data["drift"][0]["parameter"] == "replicas"

    def test_cli_json_in_sync_exit_0(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        patch_kubectl(monkeypatch, k8s_payload())
        result = runner.invoke(
            app, ["doctor", "--check-drift", str(src), "--live", "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["has_drift"] is False
        assert data["in_sync"] == ["api"]

    def test_cli_namespace_flag(self, tmp_path, monkeypatch, tools_on_path):
        src = write_spec(tmp_path)
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(
                cmd, 0, stdout=k8s_payload(), stderr=""
            )

        monkeypatch.setattr(drift_mod.subprocess, "run", fake_run)
        result = runner.invoke(
            app,
            ["doctor", "--check-drift", str(src), "--live", "-n", "prod"],
        )
        assert result.exit_code == 0
        assert "prod" in seen["cmd"]

    def test_cli_tool_missing_exit_1(self, tmp_path, no_tools):
        src = write_spec(tmp_path)
        result = runner.invoke(
            app, ["doctor", "--check-drift", str(src), "--live"]
        )
        assert result.exit_code == 1
        assert "kubectl" in result.stdout

    def test_cli_missing_source_file(self, tmp_path):
        result = runner.invoke(
            app,
            ["doctor", "--check-drift", str(tmp_path / "nope.infra"), "--live"],
        )
        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_cli_parse_error_json(self, tmp_path, tools_on_path):
        bad = tmp_path / "bad.infra"
        bad.write_text("service {{{", encoding="utf-8")
        result = runner.invoke(
            app, ["doctor", "--check-drift", str(bad), "--live", "--json"]
        )
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert data["error"]

    def test_cli_without_live_still_on_disk(self, tmp_path):
        """Backward compatibility: --check-drift without --live = on-disk check."""
        src = write_spec(tmp_path)
        result = runner.invoke(
            app,
            [
                "doctor",
                "--check-drift",
                str(src),
                "--out-dir",
                str(tmp_path / "missing-out"),
            ],
        )
        assert result.exit_code == 1
        assert "Missing generated files" in result.stdout
