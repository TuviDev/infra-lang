"""Comprehensive edge-case hardening tests (v1.0.1, FAZA 4).

Targets the failure classes called out by the quality block:

* deploy      — corrupt JSON in ``.infra-state/``, unwritable history dir,
                concurrent deployments racing auto-rollback, unknown targets
* workspace   — cyclic/cross-referencing environment overlays, empty policy
                inheritance, deeply nested monorepo paths
* compliance  — empty programs, extreme SOC2+CIS violation mixes, unknown
                standards
* explain/sbom — Polish/unicode names, very long identifiers, empty
                definitions

Everything runs fully offline: deploy tool calls are mocked at
``infra.deploy.engine.subprocess.run`` and all paths are ``pathlib``-based
so the suite stays OS-agnostic (FILAR 2).
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

import pytest

from infra.compliance.mappings import (
    STANDARDS,
    UnknownStandardError,
    controls_for,
)
from infra.compliance.scanner import scan_file, scan_program
from infra.deploy import engine
from infra.deploy.engine import (
    FAILED,
    RESTORED,
    ROLLED_BACK,
    SUCCESS,
    DeployRecord,
    DeployTargetError,
    RevisionNotFoundError,
    execute_deploy,
    execute_rollback,
    last_good_revision,
    list_history,
    load_snapshot,
    save_record,
)
from infra.errors.exceptions import InfraError, InfraLexError
from infra.explain import collect_explain_data
from infra.explain.renderer import render_explain
from infra.parser import parse
from infra.sbom.generator import collect_components, render_sbom
from infra.workspace.manager import (
    WORKSPACE_FILE,
    WorkspaceError,
    check_workspace,
    compile_project,
    find_workspace,
    load_program,
    load_workspace,
)

FILES_COMPOSE = {"docker-compose.yml": "services: {}\n"}


def _proc(rc: int = 0, stdout: str = "", stderr: str = "") -> Any:
    return subprocess.CompletedProcess(
        args=["x"], returncode=rc, stdout=stdout, stderr=stderr
    )


def _ok_run(cmd: Any, **kwargs: Any) -> Any:
    return _proc(0, "ok")


def _unhealthy_rollout_run(cmd: List[str], **kwargs: Any) -> Any:
    """Apply steps succeed; the compose ``ps`` probe reports a dead service."""
    if cmd[:2] == ["docker", "compose"] and "ps" in cmd:
        return _proc(0, '{"State":"exited"}')
    return _proc(0, "ok")


def _write_history(state_root: Path, project: str, revision: str, content: str) -> Path:
    directory = engine.history_dir(state_root, project)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{revision}.json"
    path.write_text(content, encoding="utf-8")
    return path


VALID_SERVICES = 'service app {\n  image: "nginx:1.27"\n  port: 80\n  replicas: 1\n}\n'


# --------------------------------------------------------------------------- #
# deploy: corrupt JSON inside .infra-state/
# --------------------------------------------------------------------------- #


class TestCorruptDeployState:
    def test_history_skips_valid_json_with_wrong_shape(self, tmp_path):
        _write_history(tmp_path, "demo", "r0001", "{}")
        _write_history(tmp_path, "demo", "r0002", "[1, 2]")
        _write_history(tmp_path, "demo", "r0003", '"just a string"')
        _write_history(tmp_path, "demo", "r0004", "null")
        with mock.patch("infra.deploy.engine.subprocess.run", _ok_run):
            record = execute_deploy(
                project="demo",
                target="compose",
                files=FILES_COMPOSE,
                state_root=tmp_path,
            )
        assert record.revision == "r0005"  # corrupt files still count
        history = list_history(tmp_path, "demo")
        assert [r.revision for r in history] == ["r0005"]

    def test_history_skips_records_with_uncoercible_fields(self, tmp_path):
        payload = {
            "revision": "r0001",
            "timestamp": "2026-09-02T10:00:00+00:00",
            "project": "demo",
            "target": "compose",
            "tool": "docker",
            "status": "success",
            "duration_s": "not-a-number",
            "compile_hash": "abc",
        }
        _write_history(tmp_path, "demo", "r0001", json.dumps(payload))
        assert list_history(tmp_path, "demo") == []

    def test_malformed_json_is_skipped(self, tmp_path):
        _write_history(tmp_path, "demo", "r0001", "{not json]")
        _write_history(tmp_path, "demo", "r0002", "")
        assert list_history(tmp_path, "demo") == []

    def test_rollback_finds_valid_revision_among_corrupt_files(self, tmp_path):
        with mock.patch("infra.deploy.engine.subprocess.run", _ok_run):
            base = execute_deploy(
                project="web",
                target="compose",
                files=FILES_COMPOSE,
                state_root=tmp_path,
            )
        assert base.revision == "r0001" and base.status == SUCCESS
        _write_history(tmp_path, "web", "r0002", "{}")
        _write_history(tmp_path, "web", "r0003", "]garbage[")
        with mock.patch("infra.deploy.engine.subprocess.run", _ok_run):
            restored = execute_rollback(
                state_root=tmp_path,
                project="web",
                target="compose",
                revision="r0001",
            )
        assert restored.status == RESTORED
        assert restored.revision == "r0004"  # corrupt files still counted
        assert "r0001" in restored.message
        assert load_snapshot(tmp_path, "web", "r0004") == FILES_COMPOSE

    def test_last_good_revision_ignores_corrupt_tail(self, tmp_path):
        with mock.patch("infra.deploy.engine.subprocess.run", _ok_run):
            execute_deploy(
                project="web",
                target="compose",
                files=FILES_COMPOSE,
                state_root=tmp_path,
            )
        _write_history(tmp_path, "web", "r0002", "[[[")
        good = last_good_revision(tmp_path, "web")
        assert good is not None and good.revision == "r0001"


# --------------------------------------------------------------------------- #
# deploy: unwritable state directory (simulated OS-agnostically)
# --------------------------------------------------------------------------- #


class TestRollbackAndFailureEdges:
    def test_missing_snapshot_raises_revision_not_found(self, tmp_path):
        with mock.patch("infra.deploy.engine.subprocess.run", _ok_run):
            base = execute_deploy(
                project="web",
                target="compose",
                files=FILES_COMPOSE,
                state_root=tmp_path,
            )
        # simulate a lost snapshot directory (manual cleanup of .infra-state)
        snap = engine.snapshot_dir(tmp_path, "web", base.revision)
        for child in snap.rglob("*"):
            if child.is_file():
                child.unlink()
        snap.rmdir()
        with pytest.raises(RevisionNotFoundError, match="snapshot"):
            execute_rollback(
                state_root=tmp_path,
                project="web",
                target="compose",
                revision="r0001",
            )

    def test_failed_rollout_without_auto_rollback_stays_failed(self, tmp_path):
        with mock.patch("infra.deploy.engine.subprocess.run", _unhealthy_rollout_run):
            record = execute_deploy(
                project="web",
                target="compose",
                files=FILES_COMPOSE,
                state_root=tmp_path,
                auto_rollback=False,
            )
        assert record.status == FAILED
        assert "auto-rollback disabled" in record.message
        # no rollback steps were appended after the failed rollout
        assert list_history(tmp_path, "web")[0].status == FAILED

    def test_failed_deploy_without_previous_state_cannot_roll_back(self, tmp_path):
        with mock.patch(
            "infra.deploy.engine.subprocess.run",
            lambda cmd, **kw: _proc(1, stderr="boom"),
        ):
            record = execute_deploy(
                project="web",
                target="compose",
                files=FILES_COMPOSE,
                state_root=tmp_path,
            )
        assert record.status == FAILED
        assert "could not complete" in record.message
        assert last_good_revision(tmp_path, "web") is None

    def test_windows_style_keys_normalize_to_posix(self, tmp_path):
        record = execute_deploy(
            project="demo",
            target="compose",
            files={"sub\\dir\\main.tf": "resource {}\n"},
            state_root=tmp_path,
            apply=False,
        )
        assert record.files == ("sub/dir/main.tf",)
        snapshot = load_snapshot(tmp_path, "demo", record.revision)
        assert snapshot == {"sub/dir/main.tf": "resource {}\n"}


# --------------------------------------------------------------------------- #
# deploy: unwritable state directory (simulated OS-agnostically)
# --------------------------------------------------------------------------- #


class TestUnwritableStateDir:
    def test_record_write_denied_surfaces_oserror(self, tmp_path, monkeypatch):
        def _deny(self: Path, *args: Any, **kwargs: Any) -> int:
            raise PermissionError("read-only filesystem")

        monkeypatch.setattr(Path, "write_text", _deny)
        with pytest.raises(OSError):
            execute_deploy(
                project="demo",
                target="compose",
                files=FILES_COMPOSE,
                state_root=tmp_path,
                apply=False,
            )

    def test_state_dir_creation_denied_surfaces_oserror(self, tmp_path, monkeypatch):
        def _deny(self: Path, *args: Any, **kwargs: Any) -> None:
            raise PermissionError("mkdir denied")

        monkeypatch.setattr(Path, "mkdir", _deny)
        with pytest.raises(OSError):
            save_record(
                tmp_path,
                DeployRecord(
                    revision="r0001",
                    timestamp="t",
                    project="demo",
                    target="compose",
                    tool="docker",
                    status=SUCCESS,
                    duration_s=0.0,
                    compile_hash="abc",
                ),
                FILES_COMPOSE,
            )


# --------------------------------------------------------------------------- #
# deploy: concurrent deployments with auto-rollback
# --------------------------------------------------------------------------- #


class TestConcurrentDeploys:
    def _baseline(self, state_root: Path, project: str) -> None:
        with mock.patch("infra.deploy.engine.subprocess.run", _ok_run):
            record = execute_deploy(
                project=project,
                target="compose",
                files=FILES_COMPOSE,
                state_root=state_root,
            )
        assert record.status == SUCCESS

    def test_same_project_race_never_crashes_and_state_parses(self, tmp_path):
        """4 threads race one project; revisions may collide but every file
        left on disk must decode and every thread must finish cleanly."""
        self._baseline(tmp_path, "web")
        barrier = threading.Barrier(4)
        errors: List[Exception] = []
        records: List[DeployRecord] = []

        def _worker(i: int) -> None:
            try:
                barrier.wait(timeout=10)
                records.append(
                    execute_deploy(
                        project="web",
                        target="compose",
                        files={"docker-compose.yml": f"version: {i}\n"},
                        state_root=tmp_path,
                    )
                )
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        # mock.patch is NOT thread-safe — patch once on the main thread so
        # every worker shares the single patched module attribute (no leak).
        with mock.patch("infra.deploy.engine.subprocess.run",
                        _unhealthy_rollout_run):
            threads = [threading.Thread(target=_worker, args=(i,)) for i in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)

        assert errors == []
        assert len(records) == 4
        assert all(r.status == ROLLED_BACK for r in records)
        # whatever survives the write race must decode into a valid record
        files = list(engine.history_dir(tmp_path, "web").glob("*.json"))
        assert len(files) >= 1
        for path in files:
            DeployRecord.from_dict(json.loads(path.read_text("utf-8")))
        assert len(list_history(tmp_path, "web")) == len(files)
        # the baseline success is still the re-appliable anchor
        good = last_good_revision(tmp_path, "web")
        assert good is not None and good.revision == "r0001"

    def test_isolated_projects_race_deterministic_rollback(self, tmp_path):
        """One project per thread: no on-disk collision is possible, so each
        project must end with exactly [SUCCESS, ROLLED_BACK]."""
        projects = [f"svc-{i}" for i in range(4)]
        for project in projects:
            self._baseline(tmp_path, project)
        barrier = threading.Barrier(len(projects))
        errors: List[Exception] = []

        def _worker(project: str) -> None:
            try:
                barrier.wait(timeout=10)
                with mock.patch(
                    "infra.deploy.engine.subprocess.run",
                    _unhealthy_rollout_run,
                ):
                    record = execute_deploy(
                        project=project,
                        target="compose",
                        files=FILES_COMPOSE,
                        state_root=tmp_path,
                    )
                assert record.status == ROLLED_BACK
                assert "auto-rollback restored" in record.message
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(p,)) for p in projects]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)

        assert errors == []
        for project in projects:
            history = list_history(tmp_path, project)
            assert [r.status for r in history] == [SUCCESS, ROLLED_BACK]
            assert [r.revision for r in history] == ["r0001", "r0002"]


# --------------------------------------------------------------------------- #
# deploy: unknown targets / unified error family
# --------------------------------------------------------------------------- #


class TestDeployUnknownTargets:
    def test_execute_deploy_unknown_target_raises_unified_error(self, tmp_path):
        with pytest.raises(DeployTargetError) as ei:
            execute_deploy(
                project="demo",
                target="lambda",
                files=FILES_COMPOSE,
                state_root=tmp_path,
            )
        assert isinstance(ei.value, InfraError)
        assert isinstance(ei.value, ValueError)  # backwards compatibility
        assert "lambda" in str(ei.value)

    def test_execute_rollback_unknown_target_raises_before_state_access(self, tmp_path):
        with pytest.raises(DeployTargetError):
            execute_rollback(
                state_root=tmp_path,
                project="demo",
                target="nomad",
                revision="r0001",
            )
        assert not engine.project_dir(tmp_path, "demo").exists()

    def test_missing_revision_raises_lookuperror_family(self, tmp_path):
        with pytest.raises(RevisionNotFoundError) as ei:
            execute_rollback(
                state_root=tmp_path,
                project="ghost",
                target="compose",
                revision="r9999",
            )
        assert isinstance(ei.value, InfraError)
        assert isinstance(ei.value, LookupError)

    def test_alias_target_records_canonical_name(self, tmp_path):
        record = execute_deploy(
            project="demo",
            target="k8s",
            files={"a/b.yaml": "x: 1\n"},
            state_root=tmp_path,
            apply=False,
        )
        assert record.target == "kubernetes"
        assert record.files == ("a/b.yaml",)  # normalized, sorted posix keys


# --------------------------------------------------------------------------- #
# workspace: overlay cycles, policy inheritance, deep nesting
# --------------------------------------------------------------------------- #

_SELF_OVERLAY = 'environment "loop" {\n  service app { replicas: 2 }\n}\n'
_UNRELATED_OVERLAY = 'environment "unrelated" {\n  service app { replicas: 9 }\n}\n'


def _make_workspace(root: Path, *, manifest: str, files: Dict[str, str]) -> Path:
    (root / WORKSPACE_FILE).write_text(manifest, encoding="utf-8")
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root / WORKSPACE_FILE


class TestWorkspaceManifestShapes:
    def test_find_workspace_in_empty_dir_points_to_init(self, tmp_path):
        with pytest.raises(WorkspaceError, match="workspace init"):
            find_workspace(tmp_path)

    def test_empty_projects_mapping_raises(self, tmp_path):
        manifest = _make_workspace(
            tmp_path,
            manifest='version: "1.0"\nprojects: {}\n',
            files={},
        )
        with pytest.raises(WorkspaceError, match="non-empty"):
            load_workspace(manifest)

    def test_environments_must_map_names_to_files(self, tmp_path):
        manifest = _make_workspace(
            tmp_path,
            manifest=(
                'version: "1.0"\nprojects:\n  app:\n    path: app.infra\n'
                "environments:\n  - prod\n"
            ),
            files={"app.infra": VALID_SERVICES},
        )
        with pytest.raises(WorkspaceError, match="must map"):
            load_workspace(manifest)


class TestWorkspaceOverlayCycles:
    def test_self_referencing_overlay_applies_normally(self, tmp_path):
        manifest = _make_workspace(
            tmp_path,
            manifest=(
                'version: "1.0"\n'
                "projects:\n  app:\n    path: app.infra\n"
                "environments:\n  loop: loops/self.infra\n"
            ),
            files={"app.infra": VALID_SERVICES, "loops/self.infra": _SELF_OVERLAY},
        )
        ws = load_workspace(manifest)
        program = load_program(ws, ws.project("app"), "loop")
        svc = next(s for s in program.statements if type(s).__name__ == "ServiceDef")
        assert svc.replicas == 2

    def test_cross_cycle_missing_block_raises_workspace_error(self, tmp_path):
        """environment "cross" points at a file defining a *different* block —
        the cycle is not followed recursively; resolution is by name only."""
        manifest = _make_workspace(
            tmp_path,
            manifest=(
                'version: "1.0"\n'
                "projects:\n  app:\n    path: app.infra\n"
                "environments:\n  cross: loops/other.infra\n"
            ),
            files={
                "app.infra": VALID_SERVICES,
                "loops/other.infra": _UNRELATED_OVERLAY,
            },
        )
        ws = load_workspace(manifest)
        with pytest.raises(WorkspaceError, match="defines no"):
            load_program(ws, ws.project("app"), "cross")

    def test_missing_overlay_file_raises_workspace_error(self, tmp_path):
        manifest = _make_workspace(
            tmp_path,
            manifest=(
                'version: "1.0"\n'
                "projects:\n  app:\n    path: app.infra\n"
                "environments:\n  ghost: loops/missing.infra\n"
            ),
            files={"app.infra": VALID_SERVICES},
        )
        ws = load_workspace(manifest)
        with pytest.raises(WorkspaceError, match="overlay file not found"):
            load_program(ws, ws.project("app"), "ghost")

    def test_unknown_environment_name_raises_workspace_error(self, tmp_path):
        manifest = _make_workspace(
            tmp_path,
            manifest=('version: "1.0"\nprojects:\n  app:\n    path: app.infra\n'),
            files={"app.infra": VALID_SERVICES},
        )
        ws = load_workspace(manifest)
        with pytest.raises(WorkspaceError):
            load_program(ws, ws.project("app"), "does-not-exist")


class TestWorkspacePolicyInheritance:
    _LATEST = 'service app {\n  image: "nginx:latest"\n  port: 80\n}\n'

    def test_manifest_without_policies_key_applies_none(self, tmp_path):
        manifest = _make_workspace(
            tmp_path,
            manifest=('version: "1.0"\nprojects:\n  web:\n    path: web.infra\n'),
            files={"web.infra": self._LATEST},
        )
        ws = load_workspace(manifest)
        assert ws.policies == ()
        report = check_workspace(ws)[0]
        assert report.ok is True
        assert report.violations == ()

    def test_manifest_with_empty_policies_list_applies_none(self, tmp_path):
        manifest = _make_workspace(
            tmp_path,
            manifest=(
                'version: "1.0"\nprojects:\n  web:\n    path: web.infra\npolicies: []\n'
            ),
            files={"web.infra": self._LATEST},
        )
        ws = load_workspace(manifest)
        report = check_workspace(ws)[0]
        assert report.ok is True and report.violations == ()


class TestWorkspaceDeepNesting:
    _DEEP = "teams/core/services/api/v2/app.infra"

    def test_deeply_nested_monorepo_resolves_checks_and_compiles(self, tmp_path):
        manifest = _make_workspace(
            tmp_path,
            manifest=(f'version: "1.0"\nprojects:\n  api:\n    path: {self._DEEP}\n'),
            files={self._DEEP: VALID_SERVICES},
        )
        assert find_workspace(tmp_path) == manifest
        ws = load_workspace(manifest)
        spec = ws.project("api")
        assert ws.project_file(spec).is_file()
        report = check_workspace(ws)[0]
        assert (report.ok, report.name, report.errors) == (True, "api", ())
        compiled = compile_project(ws, spec)
        assert isinstance(compiled, dict) and compiled

    def test_deeply_nested_missing_file_reports_not_found(self, tmp_path):
        manifest = _make_workspace(
            tmp_path,
            manifest=(f'version: "1.0"\nprojects:\n  api:\n    path: {self._DEEP}\n'),
            files={},
        )
        ws = load_workspace(manifest)
        with pytest.raises(WorkspaceError, match="file not found"):
            load_program(ws, ws.project("api"))


# --------------------------------------------------------------------------- #
# compliance: empty programs, extreme mixes, unknown standards
# --------------------------------------------------------------------------- #

_NASTY = """\
service open1 {
  image: "nginx:latest"
  port: 80
  expose: true
}
service open2 {
  image: "redis:latest"
  port: 6379
  expose: true
}
service open3 {
  image: "api:1.0"
  port: 8080
}
"""


class TestComplianceEdgeCases:
    @pytest.mark.parametrize("standard", STANDARDS)
    def test_empty_program_passes_every_control(self, standard):
        report = scan_program(parse(""), "empty.infra", standard)
        assert report.total == len(controls_for(standard))
        assert report.failed == 0
        assert report.score == 100.0

    def test_comment_only_file_scores_100(self, tmp_path):
        path = tmp_path / "notes.infra"
        path.write_text("# nothing here\n# but comments\n", encoding="utf-8")
        report = scan_file(path, "all")
        assert (report.total, report.failed, report.score) == (10, 0, 100.0)

    def test_extreme_mixed_soc2_cis_violations_are_linear(self, tmp_path):
        path = tmp_path / "nasty.infra"
        path.write_text(_NASTY, encoding="utf-8")
        rep_all = scan_file(path, "all")
        rep_soc2 = scan_file(path, "soc2")
        rep_cis = scan_file(path, "cis")
        # "all" is exactly the union of both standards
        assert rep_all.total == rep_soc2.total + rep_cis.total
        assert rep_all.failed == rep_soc2.failed + rep_cis.failed
        assert 0.0 < rep_all.score < 100.0
        # the extreme mix fails a majority of controls
        assert rep_all.failed > rep_all.passed
        violations = [v for r in rep_all.results for v in r.violations]
        assert violations  # at least one concrete breach
        assert all(v.recommendation for v in violations)
        # the report must serialize (JSON output path of the CLI)
        json.dumps(rep_all.to_dict())

    def test_control_violation_coherence_invariant(self, tmp_path):
        """Every failed control carries ≥1 violation; every passed one none."""
        path = tmp_path / "mixed.infra"
        path.write_text(_NASTY, encoding="utf-8")
        report = scan_file(path, "all")
        for result in report.results:
            assert result.passed == (not result.violations)
        assert report.passed + report.failed == report.total

    @pytest.mark.parametrize("bogus", ["hipaa", "pci", "", "SOC2"])
    def test_unknown_standard_raises_unified_error(self, tmp_path, bogus):
        with pytest.raises(UnknownStandardError) as ei:
            controls_for(bogus)
        assert isinstance(ei.value, InfraError)
        assert isinstance(ei.value, ValueError)
        path = tmp_path / "app.infra"
        path.write_text(VALID_SERVICES, encoding="utf-8")
        with pytest.raises(UnknownStandardError):
            scan_file(path, bogus)
        with pytest.raises(UnknownStandardError):
            scan_program(parse(VALID_SERVICES), "app.infra", bogus)


# --------------------------------------------------------------------------- #
# explain & sbom: unicode names, long identifiers, empty definitions
# --------------------------------------------------------------------------- #

_LONG_NAME = "svc_" + "x" * 500
_UNICODE_SOURCE = (
    'service api {\n  image: "nginx:1.27"\n  port: 80\n  replicas: 1\n}\n'
    'environment "zażółć-środowisko" {\n  service api { replicas: 3 }\n}\n'
)


class TestExplainSbomEdgeCases:
    def test_unicode_environment_names_render_in_all_formats(self):
        program = parse(_UNICODE_SOURCE)
        data = collect_explain_data(program, source=_UNICODE_SOURCE, project="demo")
        for fmt in ("markdown", "text", "json"):
            for audience in ("human", "ai"):
                out = render_explain(
                    data, output_format=fmt, audience=audience, now=None
                )
                assert isinstance(out, str) and out
                if fmt == "json":
                    json.loads(out)

    def test_diacritics_in_service_names_raise_friendly_lex_error(self):
        source = 'service zażółć {\n  image: "nginx:1.27"\n}\n'
        with pytest.raises(InfraLexError) as ei:
            parse(source)
        assert "ż" in str(ei.value)

    def test_very_long_identifier_roundtrips_explain_and_sbom(self):
        source = (
            f"service {_LONG_NAME} {{\n"
            '  image: "nginx:1.27"\n  port: 80\n  replicas: 1\n}\n'
        )
        program = parse(source)
        data = collect_explain_data(program, source=source, project="demo")
        assert _LONG_NAME in render_explain(data, output_format="markdown")
        components = collect_components(program)
        assert components
        assert any(_LONG_NAME in s for c in components for s in c.sources)
        text = render_sbom(
            components,
            "text",
            project="demo",
            source_name="app.infra",
            checksum="0" * 64,
            timestamp="2026-09-02T10:00:00+00:00",
        )
        assert _LONG_NAME in text

    def test_empty_definitions_render_empty_sbom_in_all_formats(self):
        components = collect_components(parse(""))
        assert components == []
        for fmt in ("text", "markdown", "spdx-json", "cyclonedx-json"):
            out = render_sbom(
                components,
                fmt,
                project="demo",
                source_name="empty.infra",
                checksum="0" * 64,
                timestamp="2026-09-02T10:00:00+00:00",
            )
            assert isinstance(out, str) and out
            if fmt.endswith("-json"):
                json.loads(out)

    def test_empty_service_definition_best_effort_insight(self):
        source = "service empty {\n}\n"
        program = parse(source)
        data = collect_explain_data(program, source=source, project="demo")
        assert data.counts["services"] == 1
        assert data.validation_errors  # missing image/port is surfaced
        for fmt in ("markdown", "text", "json"):
            assert render_explain(data, output_format=fmt, now=None)
        # a service with neither image nor build contributes no component
        assert collect_components(program) == []

    def test_variables_only_program_explains_cleanly(self):
        source = "let x = 1\nconst y = 2\n"
        program = parse(source)
        data = collect_explain_data(program, source=source, project="vars")
        assert data.counts["services"] == 0
        assert data.arch_type
        for fmt in ("markdown", "text", "json"):
            assert render_explain(data, output_format=fmt, now=None)

    def test_unknown_explain_section_raises_unified_error(self):
        from infra.explain.renderer import InvalidSectionsError, parse_sections

        with pytest.raises(InvalidSectionsError) as ei:
            parse_sections("overview,bogus")
        assert isinstance(ei.value, InfraError)
        assert isinstance(ei.value, ValueError)

    @pytest.mark.parametrize("bogus", ["pdf", "yaml", "SPDX"])
    def test_unknown_sbom_format_raises_unified_error(self, bogus):
        from infra.sbom.generator import UnknownSbomFormatError

        with pytest.raises(UnknownSbomFormatError) as ei:
            render_sbom(
                [],
                bogus,
                project="demo",
                source_name="app.infra",
                checksum="0" * 64,
                timestamp="2026-09-02T10:00:00+00:00",
            )
        assert isinstance(ei.value, InfraError)
        assert isinstance(ei.value, ValueError)
