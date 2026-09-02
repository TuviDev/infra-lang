"""Tests for `infra workspace` — manager + CLI (v1.0.0).

Everything is local-file based: no external tools, no network — the suite
runs fully offline.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Optional

import pytest
from typer.testing import CliRunner

from infra.cli.main import app
from infra.workspace.manager import (
    WORKSPACE_FILE,
    WORKSPACE_VERSION,
    ProjectSpec,
    Workspace,
    WorkspaceError,
    check_project,
    check_workspace,
    compile_project,
    find_workspace,
    init_workspace,
    load_program,
    load_workspace,
    project_status,
)

runner = CliRunner()

VALID_INFRA = '''service app {
  image: "nginx:1.27"
  port: 8080
  replicas: 1
}
'''

INFRA_WITH_ENV = '''service app {
  image: "nginx:1.27"
  port: 8080
  replicas: 1
}

environment "prod" {
  service app {
    replicas: 3
  }
}
'''

INVALID_SEMANTIC = '''service broken {
  image: "nginx:1.27"
  port: 8080
  replicas: 0
}
'''

OVERLAY_PROD = '''environment "prod" {
  service app {
    replicas: 5
  }
}
'''

NO_LATEST_POLICY = """version: 1
name: guardrails
rules:
  - id: no-latest
    type: disallow_image_tag
"""

LATEST_INFRA = '''service app {
  image: "nginx:latest"
  port: 8080
}
'''


def _manifest(
    *,
    projects: str,
    policies: str = "policies: []\n",
    environments: str = "environments: {}\n",
    version: str = 'version: "1.0"\n',
) -> str:
    return version + projects + policies + environments


def _write_workspace(
    tmp_path: Path,
    *,
    projects: Optional[str] = None,
    policies: str = "policies: []\n",
    environments: str = "environments: {}\n",
    files: Optional[Dict[str, str]] = None,
    version: str = 'version: "1.0"\n',
) -> Path:
    if projects is None:
        projects = (
            "projects:\n"
            "  app:\n"
            "    path: app.infra\n"
            "    target: kubernetes\n"
        )
    manifest = tmp_path / WORKSPACE_FILE
    manifest.write_text(
        _manifest(
            projects=projects,
            policies=policies,
            environments=environments,
            version=version,
        ),
        encoding="utf-8",
    )
    merged = {"app.infra": VALID_INFRA}
    merged.update(files or {})
    for rel, content in merged.items():
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    return manifest


def _service(program, name="app"):
    from infra.parser import ast_nodes as n

    for stmt in program.statements:
        if isinstance(stmt, n.ServiceDef) and stmt.name == name:
            return stmt
    raise AssertionError(f"service {name!r} not found in program")


def _flat(text: str) -> str:
    clean = re.sub(r"\x1b\[[0-9;]*[mGKH]", "", text)
    return re.sub(r"\s+", " ", clean)


# --------------------------------------------------------------------------- #
# Manifest loading
# --------------------------------------------------------------------------- #


class TestLoadWorkspace:
    def test_happy_path(self, tmp_path):
        manifest = _write_workspace(tmp_path)
        ws = load_workspace(manifest)
        assert ws.version == WORKSPACE_VERSION
        assert ws.root == tmp_path
        assert [p.name for p in ws.projects] == ["app"]
        spec = ws.projects[0]
        assert spec == ProjectSpec(name="app", path="app.infra",
                                   target="kubernetes")

    def test_missing_file(self, tmp_path):
        with pytest.raises(WorkspaceError, match="not found"):
            load_workspace(tmp_path / WORKSPACE_FILE)

    def test_bad_yaml(self, tmp_path):
        manifest = tmp_path / WORKSPACE_FILE
        manifest.write_text("projects: [unclosed\n", encoding="utf-8")
        with pytest.raises(WorkspaceError, match="cannot parse YAML"):
            load_workspace(manifest)

    def test_top_level_not_mapping(self, tmp_path):
        manifest = tmp_path / WORKSPACE_FILE
        manifest.write_text("- just\n- a\n- list\n", encoding="utf-8")
        with pytest.raises(WorkspaceError, match="expected a mapping"):
            load_workspace(manifest)

    def test_wrong_version(self, tmp_path):
        manifest = _write_workspace(tmp_path, version='version: "9.9"\n')
        with pytest.raises(WorkspaceError, match="unsupported version"):
            load_workspace(manifest)

    def test_numeric_version_accepted(self, tmp_path):
        manifest = _write_workspace(tmp_path, version="version: 1.0\n")
        ws = load_workspace(manifest)
        assert ws.version == "1.0"

    def test_missing_version(self, tmp_path):
        manifest = _write_workspace(tmp_path, version="")
        with pytest.raises(WorkspaceError, match="unsupported version"):
            load_workspace(manifest)

    def test_projects_not_mapping(self, tmp_path):
        manifest = _write_workspace(
            tmp_path, projects="projects: []\n"
        )
        with pytest.raises(WorkspaceError, match="non-empty mapping"):
            load_workspace(manifest)

    def test_projects_empty(self, tmp_path):
        manifest = _write_workspace(
            tmp_path, projects="projects: {}\n"
        )
        with pytest.raises(WorkspaceError, match="non-empty mapping"):
            load_workspace(manifest)

    def test_project_entry_not_mapping(self, tmp_path):
        manifest = _write_workspace(
            tmp_path, projects='projects:\n  app: "oops"\n'
        )
        with pytest.raises(WorkspaceError, match="must be a mapping"):
            load_workspace(manifest)

    def test_project_without_path(self, tmp_path):
        manifest = _write_workspace(
            tmp_path,
            projects="projects:\n  app:\n    target: compose\n",
        )
        with pytest.raises(WorkspaceError, match="'path' must be"):
            load_workspace(manifest)

    def test_project_empty_path(self, tmp_path):
        manifest = _write_workspace(
            tmp_path,
            projects='projects:\n  app:\n    path: ""\n',
        )
        with pytest.raises(WorkspaceError, match="'path' must be"):
            load_workspace(manifest)

    def test_project_unknown_target(self, tmp_path):
        manifest = _write_workspace(
            tmp_path,
            projects=(
                "projects:\n  app:\n    path: app.infra\n"
                "    target: ansible\n"
            ),
        )
        with pytest.raises(WorkspaceError, match="unknown target"):
            load_workspace(manifest)

    def test_project_non_string_name(self, tmp_path):
        manifest = _write_workspace(
            tmp_path,
            projects="projects:\n  42:\n    path: app.infra\n",
        )
        with pytest.raises(WorkspaceError, match="must be strings"):
            load_workspace(manifest)

    def test_default_target_when_omitted(self, tmp_path):
        manifest = _write_workspace(
            tmp_path, projects="projects:\n  app:\n    path: app.infra\n"
        )
        ws = load_workspace(manifest)
        assert ws.projects[0].target == "kubernetes"

    def test_policies_not_a_list(self, tmp_path):
        manifest = _write_workspace(
            tmp_path, policies='policies: "oops"\n'
        )
        with pytest.raises(WorkspaceError, match="'policies' must be"):
            load_workspace(manifest)

    def test_policies_non_string_entries(self, tmp_path):
        manifest = _write_workspace(
            tmp_path, policies="policies:\n  - 42\n"
        )
        with pytest.raises(WorkspaceError, match="'policies' must be"):
            load_workspace(manifest)

    def test_environments_not_a_mapping(self, tmp_path):
        manifest = _write_workspace(
            tmp_path, environments="environments:\n  - prod\n"
        )
        with pytest.raises(WorkspaceError, match="'environments' must map"):
            load_workspace(manifest)

    def test_environments_non_string_value(self, tmp_path):
        manifest = _write_workspace(
            tmp_path, environments="environments:\n  prod: 42\n"
        )
        with pytest.raises(WorkspaceError, match="'environments' must map"):
            load_workspace(manifest)

    def test_policies_and_environments(self, tmp_path):
        manifest = _write_workspace(
            tmp_path,
            policies="policies:\n  - infra-policy.yaml\n",
            environments="environments:\n  prod: overlays/prod.infra\n",
        )
        ws = load_workspace(manifest)
        assert ws.policies == ("infra-policy.yaml",)
        assert ws.environment_map == {"prod": "overlays/prod.infra"}
        assert ws.policy_files() == [tmp_path / "infra-policy.yaml"]


class TestWorkspaceMethods:
    def test_project_lookup(self, tmp_path):
        ws = load_workspace(_write_workspace(tmp_path))
        assert ws.project("app").path == "app.infra"

    def test_project_unknown(self, tmp_path):
        ws = load_workspace(_write_workspace(tmp_path))
        with pytest.raises(WorkspaceError, match="Unknown project 'nope'"):
            ws.project("nope")

    def test_project_file(self, tmp_path):
        ws = load_workspace(_write_workspace(tmp_path))
        assert ws.project_file(ws.projects[0]) == tmp_path / "app.infra"


class TestFindWorkspace:
    def test_explicit_directory(self, tmp_path):
        _write_workspace(tmp_path)
        assert find_workspace(tmp_path) == tmp_path / WORKSPACE_FILE

    def test_cwd_default(self, tmp_path, monkeypatch):
        _write_workspace(tmp_path)
        monkeypatch.chdir(tmp_path)
        assert find_workspace() == Path.cwd() / WORKSPACE_FILE

    def test_missing(self, tmp_path):
        with pytest.raises(WorkspaceError, match="workspace init"):
            find_workspace(tmp_path)


# --------------------------------------------------------------------------- #
# Program loading & overlays
# --------------------------------------------------------------------------- #


class TestLoadProgram:
    def _ws(self, tmp_path: Path, **kwargs) -> Workspace:
        return load_workspace(_write_workspace(tmp_path, **kwargs))

    def test_basic_parse(self, tmp_path):
        ws = self._ws(tmp_path)
        program = load_program(ws, ws.projects[0])
        assert _service(program).name == "app"

    def test_missing_project_file(self, tmp_path):
        manifest = tmp_path / WORKSPACE_FILE
        manifest.write_text(
            _manifest(
                projects="projects:\n  app:\n    path: gone.infra\n"
            ),
            encoding="utf-8",
        )
        ws = load_workspace(manifest)
        with pytest.raises(WorkspaceError, match="file not found"):
            load_program(ws, ws.projects[0])

    def test_unparseable_file(self, tmp_path):
        ws = self._ws(tmp_path, files={"app.infra": "service {{{"})
        with pytest.raises(WorkspaceError, match="cannot parse"):
            load_program(ws, ws.projects[0])

    def test_file_local_environment(self, tmp_path):
        ws = self._ws(tmp_path, files={"app.infra": INFRA_WITH_ENV})
        program = load_program(ws, ws.projects[0], "prod")
        assert _service(program).replicas == 3

    def test_workspace_overlay_applied(self, tmp_path):
        ws = self._ws(
            tmp_path,
            environments="environments:\n  prod: overlays/prod.infra\n",
            files={
                "app.infra": VALID_INFRA,
                "overlays/prod.infra": OVERLAY_PROD,
            },
        )
        program = load_program(ws, ws.projects[0], "prod")
        assert _service(program).replicas == 5

    def test_workspace_overlay_wins_over_file_block(self, tmp_path):
        ws = self._ws(
            tmp_path,
            environments="environments:\n  prod: overlays/prod.infra\n",
            files={
                "app.infra": INFRA_WITH_ENV,  # file-local prod → 3
                "overlays/prod.infra": OVERLAY_PROD,  # workspace prod → 5
            },
        )
        program = load_program(ws, ws.projects[0], "prod")
        assert _service(program).replicas == 5

    def test_workspace_overlay_missing_file(self, tmp_path):
        ws = self._ws(
            tmp_path,
            environments="environments:\n  prod: overlays/prod.infra\n",
        )
        with pytest.raises(WorkspaceError, match="overlay file not found"):
            load_program(ws, ws.projects[0], "prod")

    def test_workspace_overlay_unparseable(self, tmp_path):
        ws = self._ws(
            tmp_path,
            environments="environments:\n  prod: overlays/prod.infra\n",
            files={"overlays/prod.infra": "service {{{"},
        )
        with pytest.raises(WorkspaceError, match="cannot parse"):
            load_program(ws, ws.projects[0], "prod")

    def test_workspace_overlay_without_matching_block(self, tmp_path):
        ws = self._ws(
            tmp_path,
            environments="environments:\n  prod: overlays/prod.infra\n",
            files={"overlays/prod.infra": VALID_INFRA},
        )
        with pytest.raises(WorkspaceError, match="defines no environment"):
            load_program(ws, ws.projects[0], "prod")

    def test_unknown_environment_everywhere(self, tmp_path):
        ws = self._ws(tmp_path)
        with pytest.raises(WorkspaceError, match="not defined"):
            load_program(ws, ws.projects[0], "staging")


# --------------------------------------------------------------------------- #
# Checking
# --------------------------------------------------------------------------- #


class TestCheck:
    def test_check_project_ok(self, tmp_path):
        ws = load_workspace(_write_workspace(tmp_path))
        report = check_project(ws, ws.projects[0])
        assert report.ok
        assert report.errors == ()
        assert report.violations == ()

    def test_check_project_load_failure(self, tmp_path):
        manifest = tmp_path / WORKSPACE_FILE
        manifest.write_text(
            _manifest(projects="projects:\n  app:\n    path: gone.infra\n"),
            encoding="utf-8",
        )
        ws = load_workspace(manifest)
        report = check_project(ws, ws.projects[0])
        assert not report.ok
        assert "file not found" in report.errors[0]

    def test_check_project_semantic_errors(self, tmp_path):
        ws = load_workspace(
            _write_workspace(tmp_path, files={"app.infra": INVALID_SEMANTIC})
        )
        report = check_project(ws, ws.projects[0])
        assert not report.ok
        assert any("error[" in e for e in report.errors)

    def test_policy_violation_fails_project(self, tmp_path):
        ws = load_workspace(
            _write_workspace(
                tmp_path,
                policies="policies:\n  - infra-policy.yaml\n",
                files={
                    "app.infra": LATEST_INFRA,
                    "infra-policy.yaml": NO_LATEST_POLICY,
                },
            )
        )
        report = check_project(ws, ws.projects[0])
        assert not report.ok
        assert report.violations[0].code == "POL004"

    def test_broken_policy_file_is_error(self, tmp_path):
        ws = load_workspace(
            _write_workspace(
                tmp_path,
                policies="policies:\n  - infra-policy.yaml\n",
                files={"infra-policy.yaml": "rules: nope\n"},
            )
        )
        report = check_project(ws, ws.projects[0])
        assert not report.ok
        assert any("infra-policy.yaml" in e for e in report.errors)

    def test_check_workspace_all_projects(self, tmp_path):
        ws = load_workspace(
            _write_workspace(
                tmp_path,
                projects=(
                    "projects:\n"
                    "  a:\n    path: a.infra\n"
                    "  b:\n    path: b.infra\n"
                ),
                files={"a.infra": VALID_INFRA, "b.infra": INVALID_SEMANTIC},
            )
        )
        reports = check_workspace(ws)
        assert [r.ok for r in reports] == [True, False]
        assert [r.name for r in reports] == ["a", "b"]

    def test_check_with_environment_overlay(self, tmp_path):
        ws = load_workspace(
            _write_workspace(
                tmp_path,
                environments="environments:\n  prod: overlays/prod.infra\n",
                files={
                    "app.infra": VALID_INFRA,
                    "overlays/prod.infra": OVERLAY_PROD,
                },
            )
        )
        report = check_workspace(ws, "prod")[0]
        assert report.ok

    def test_check_with_unknown_environment_fails(self, tmp_path):
        ws = load_workspace(_write_workspace(tmp_path))
        report = check_workspace(ws, "staging")[0]
        assert not report.ok
        assert "not defined" in report.errors[0]


# --------------------------------------------------------------------------- #
# Compilation & status
# --------------------------------------------------------------------------- #


class TestCompileProject:
    def test_compile_success(self, tmp_path):
        ws = load_workspace(_write_workspace(tmp_path))
        files = compile_project(ws, ws.projects[0])
        assert "infra.yaml" in files

    def test_compile_compose_target(self, tmp_path):
        ws = load_workspace(
            _write_workspace(
                tmp_path,
                projects="projects:\n  app:\n    path: app.infra\n"
                "    target: compose\n",
            )
        )
        files = compile_project(ws, ws.projects[0])
        assert "docker-compose.yml" in files

    def test_compile_semantic_error(self, tmp_path):
        ws = load_workspace(
            _write_workspace(tmp_path, files={"app.infra": INVALID_SEMANTIC})
        )
        with pytest.raises(WorkspaceError, match="semantic error"):
            compile_project(ws, ws.projects[0])

    def test_compile_load_failure(self, tmp_path):
        ws = load_workspace(
            _write_workspace(tmp_path, files={"app.infra": "service {{{"})
        )
        with pytest.raises(WorkspaceError, match="cannot parse"):
            compile_project(ws, ws.projects[0])

    def test_compile_backend_failure_wrapped(self, tmp_path, monkeypatch):
        ws = load_workspace(_write_workspace(tmp_path))

        def boom(*args, **kwargs):
            raise RuntimeError("kaput")

        monkeypatch.setattr(
            "infra.backends.kubernetes.KubernetesBackend.compile", boom
        )
        with pytest.raises(WorkspaceError, match="compilation failed"):
            compile_project(ws, ws.projects[0])

    def test_compile_with_overlay(self, tmp_path):
        ws = load_workspace(
            _write_workspace(
                tmp_path,
                environments="environments:\n  prod: overlays/prod.infra\n",
                files={
                    "app.infra": VALID_INFRA,
                    "overlays/prod.infra": OVERLAY_PROD,
                },
            )
        )
        files = compile_project(ws, ws.projects[0], "prod")
        assert "replicas: 5" in files["infra.yaml"]


class TestProjectStatus:
    def test_valid(self, tmp_path):
        ws = load_workspace(_write_workspace(tmp_path))
        assert project_status(ws, ws.projects[0]) == "valid"

    def test_missing(self, tmp_path):
        manifest = tmp_path / WORKSPACE_FILE
        manifest.write_text(
            _manifest(projects="projects:\n  app:\n    path: gone.infra\n"),
            encoding="utf-8",
        )
        ws = load_workspace(manifest)
        assert project_status(ws, ws.projects[0]) == "missing"

    def test_parse_error(self, tmp_path):
        ws = load_workspace(
            _write_workspace(tmp_path, files={"app.infra": "service {{{"})
        )
        assert project_status(ws, ws.projects[0]) == "parse-error"

    def test_invalid(self, tmp_path):
        ws = load_workspace(
            _write_workspace(tmp_path, files={"app.infra": INVALID_SEMANTIC})
        )
        assert project_status(ws, ws.projects[0]) == "invalid"


# --------------------------------------------------------------------------- #
# init_workspace templates
# --------------------------------------------------------------------------- #


class TestInitWorkspace:
    def test_basic_template(self, tmp_path):
        written = init_workspace(tmp_path, "basic")
        names = {p.relative_to(tmp_path).as_posix() for p in written}
        assert names == {WORKSPACE_FILE, "app.infra"}
        ws = load_workspace(tmp_path / WORKSPACE_FILE)
        reports = check_workspace(ws)
        assert all(r.ok for r in reports)

    def test_micro_template(self, tmp_path):
        written = init_workspace(tmp_path, "micro")
        names = {p.relative_to(tmp_path).as_posix() for p in written}
        assert names == {
            WORKSPACE_FILE,
            "services/api.infra",
            "services/web.infra",
            "services/worker.infra",
            "infra-policy.yaml",
        }
        ws = load_workspace(tmp_path / WORKSPACE_FILE)
        assert len(ws.projects) == 3
        assert all(r.ok for r in check_workspace(ws))

    def test_full_template(self, tmp_path):
        init_workspace(tmp_path, "full")
        ws = load_workspace(tmp_path / WORKSPACE_FILE)
        assert ws.environment_map == {"prod": "overlays/prod.infra"}
        assert all(r.ok for r in check_workspace(ws, "prod"))
        # every template file is valid UTF-8 text with no BOM
        raw = (tmp_path / "overlays/prod.infra").read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")

    def test_unknown_template(self, tmp_path):
        with pytest.raises(WorkspaceError, match="Unknown template"):
            init_workspace(tmp_path, "huge")

    def test_existing_manifest_refused(self, tmp_path):
        init_workspace(tmp_path, "basic")
        with pytest.raises(WorkspaceError, match="already exists"):
            init_workspace(tmp_path, "micro")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


class TestCliInit:
    def test_init_default(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["workspace", "init"])
        assert result.exit_code == 0, result.output
        assert "[OK]" in result.output
        assert (tmp_path / WORKSPACE_FILE).is_file()
        assert (tmp_path / "app.infra").is_file()

    def test_init_template_option(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app, ["workspace", "init", "--template", "micro"]
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "services/api.infra").is_file()

    def test_init_existing_manifest(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["workspace", "init"])
        result = runner.invoke(app, ["workspace", "init"])
        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_init_bad_template(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app, ["workspace", "init", "--template", "huge"]
        )
        assert result.exit_code == 1
        assert "Unknown template" in result.output


class TestCliList:
    def test_list_table(self, tmp_path, monkeypatch):
        _write_workspace(
            tmp_path,
            projects=(
                "projects:\n"
                "  a:\n    path: a.infra\n"
                "  b:\n    path: b.infra\n"
            ),
            files={"a.infra": VALID_INFRA, "b.infra": INVALID_SEMANTIC},
        )
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["workspace", "list"])
        assert result.exit_code == 0, result.output
        flat = _flat(result.output)
        assert "a" in flat and "b" in flat
        assert "[OK] valid" in flat
        assert "[FAIL] invalid" in flat

    def test_list_missing_manifest(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["workspace", "list"])
        assert result.exit_code == 1
        assert "workspace init" in _flat(result.output)

    def test_list_broken_manifest(self, tmp_path, monkeypatch):
        _write_workspace(tmp_path, version='version: "9.9"\n')
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["workspace", "list"])
        assert result.exit_code == 1
        assert "unsupported version" in _flat(result.output)


class TestCliCheck:
    def test_check_all_pass(self, tmp_path, monkeypatch):
        _write_workspace(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["workspace", "check"])
        assert result.exit_code == 0, result.output
        flat = _flat(result.output)
        assert "[PASS] app" in flat
        assert "All 1 project(s) passed" in flat

    def test_check_failure_exit_1(self, tmp_path, monkeypatch):
        _write_workspace(
            tmp_path, files={"app.infra": INVALID_SEMANTIC}
        )
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["workspace", "check"])
        assert result.exit_code == 1
        flat = _flat(result.output)
        assert "[FAIL] app" in flat
        assert "failed" in flat

    def test_check_policy_violation(self, tmp_path, monkeypatch):
        _write_workspace(
            tmp_path,
            policies="policies:\n  - infra-policy.yaml\n",
            files={
                "app.infra": LATEST_INFRA,
                "infra-policy.yaml": NO_LATEST_POLICY,
            },
        )
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["workspace", "check"])
        assert result.exit_code == 1
        flat = _flat(result.output)
        assert "[POL004]" in flat
        assert "policy violation" in flat

    def test_check_with_environment(self, tmp_path, monkeypatch):
        _write_workspace(
            tmp_path,
            environments="environments:\n  prod: overlays/prod.infra\n",
            files={
                "app.infra": VALID_INFRA,
                "overlays/prod.infra": OVERLAY_PROD,
            },
        )
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app, ["workspace", "check", "--environment", "prod"]
        )
        assert result.exit_code == 0, result.output

    def test_check_with_unknown_environment(self, tmp_path, monkeypatch):
        _write_workspace(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app, ["workspace", "check", "-e", "staging"]
        )
        assert result.exit_code == 1
        assert "not defined" in _flat(result.output)


class TestCliCompile:
    def test_compile_all(self, tmp_path, monkeypatch):
        _write_workspace(
            tmp_path,
            projects=(
                "projects:\n"
                "  a:\n    path: a.infra\n    target: kubernetes\n"
                "  b:\n    path: b.infra\n    target: compose\n"
            ),
            files={"a.infra": VALID_INFRA, "b.infra": VALID_INFRA},
        )
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app, ["workspace", "compile", "-o", "out"]
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "out/a/infra.yaml").is_file()
        assert (tmp_path / "out/b/docker-compose.yml").is_file()
        assert "[OK] a" in result.output

    def test_compile_single_project(self, tmp_path, monkeypatch):
        _write_workspace(
            tmp_path,
            projects=(
                "projects:\n"
                "  a:\n    path: a.infra\n"
                "  b:\n    path: b.infra\n"
            ),
            files={"a.infra": VALID_INFRA, "b.infra": VALID_INFRA},
        )
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app, ["workspace", "compile", "--project", "a"]
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "workspace-out/a").is_dir()
        assert not (tmp_path / "workspace-out/b").exists()

    def test_compile_unknown_project(self, tmp_path, monkeypatch):
        _write_workspace(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app, ["workspace", "compile", "--project", "nope"]
        )
        assert result.exit_code == 1
        assert "Unknown project" in _flat(result.output)

    def test_compile_failure_exit_1(self, tmp_path, monkeypatch):
        _write_workspace(
            tmp_path, files={"app.infra": INVALID_SEMANTIC}
        )
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["workspace", "compile"])
        assert result.exit_code == 1
        flat = _flat(result.output)
        assert "[FAIL]" in flat
        assert "failed to compile" in flat


class TestCliHelp:
    def test_group_help(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "120")
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.setenv("FORCE_COLOR", "0")
        result = runner.invoke(app, ["workspace", "--help"])
        assert result.exit_code == 0, result.output
        flat = _flat(result.output)
        for word in ("init", "list", "check", "compile"):
            assert word in flat

    def test_check_help(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "120")
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.setenv("FORCE_COLOR", "0")
        result = runner.invoke(app, ["workspace", "check", "--help"])
        assert result.exit_code == 0, result.output
        assert "--environment" in _flat(result.output)
