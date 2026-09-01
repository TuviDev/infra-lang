"""In-memory Web API (`infra.web_api`) — WASM/playground surface (v0.6.0)."""

from __future__ import annotations

import ast
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from infra import web_api
from infra.analyzer.environments import EnvironmentNotFoundError
from infra.errors.exceptions import InfraError

SRC = """\
service web {
  image: "nginx:1.25"
  replicas: 1
  port 8080:80
  env { LOG_LEVEL: "debug" }
}
environment "prod" {
  service web {
    replicas: 5
  }
}
"""

DUP = 'service a { image: "x:1" }\nservice a { image: "y:2" }\n'
BROKEN = "service web {\n"


# --------------------------------------------------------------------------- #
# compile_to_target
# --------------------------------------------------------------------------- #


class TestCompileToTarget:
    def test_success_kubernetes(self):
        r = web_api.compile_to_target(SRC, "kubernetes")
        assert r["success"] is True
        assert r["errors"] == []
        assert any(name.endswith(".yaml") for name in r["files"])
        assert any("Deployment" in body for body in r["files"].values())

    def test_success_compose_and_terraform(self):
        for target in ("compose", "terraform"):
            r = web_api.compile_to_target(SRC, target)
            assert r["success"], target
            assert r["files"]

    def test_default_target_is_kubernetes(self):
        r = web_api.compile_to_target(SRC)
        assert r["success"]
        assert any("Deployment" in body for body in r["files"].values())

    def test_environment_overlay_applied(self):
        r = web_api.compile_to_target(SRC, "kubernetes", env_name="prod")
        assert r["success"]
        assert any("replicas: 5" in body for body in r["files"].values())

    def test_unknown_environment_is_data_error(self):
        r = web_api.compile_to_target(SRC, "kubernetes", env_name="nope")
        assert r["success"] is False
        assert r["files"] == {}
        assert any("not defined" in e for e in r["errors"])

    def test_semantic_error_is_data_error(self):
        r = web_api.compile_to_target(DUP, "kubernetes")
        assert r["success"] is False
        assert r["files"] == {}
        assert r["errors"][0].startswith("error[E002]")

    def test_parse_error_is_data_error(self):
        r = web_api.compile_to_target(BROKEN, "kubernetes")
        assert r["success"] is False
        assert r["errors"]

    def test_unknown_target_is_data_error(self):
        r = web_api.compile_to_target(SRC, "cobol-mainframe")
        assert r["success"] is False
        assert any("cobol-mainframe" in e for e in r["errors"])

    def test_never_raises_and_never_exits(self, monkeypatch):
        # Even an empty-message exception becomes a data error (fail-safe).
        def _boom(name):
            raise RuntimeError

        monkeypatch.setattr(web_api, "get_backend", _boom)
        r = web_api.compile_to_target(SRC, "kubernetes")
        assert r["success"] is False
        assert r["errors"] == ["RuntimeError"]


# --------------------------------------------------------------------------- #
# generate_ui_report
# --------------------------------------------------------------------------- #


class TestGenerateUiReport:
    def test_dashboard_html(self):
        html = web_api.generate_ui_report(SRC)
        assert html.startswith("<!DOCTYPE html>")
        assert "Infra Lang Dashboard" in html
        assert 'download="infra-dag.svg"' in html
        assert 'data-active-env="base"' in html

    def test_environment_selected(self):
        html = web_api.generate_ui_report(SRC, env_name="prod")
        assert 'data-active-env="prod"' in html

    def test_compare_renders_side_by_side(self):
        html = web_api.generate_ui_report(SRC, env_name="base", compare_env="prod")
        assert 'class="cmp-panel"' in html
        assert "replicas" in html

    def test_compare_defaults_left_side_to_base(self):
        html = web_api.generate_ui_report(SRC, compare_env="prod")
        assert "<b>base</b> vs" in html

    def test_unknown_env_raises(self):
        with pytest.raises(EnvironmentNotFoundError):
            web_api.generate_ui_report(SRC, env_name="nope")

    def test_unknown_compare_env_raises(self):
        with pytest.raises(EnvironmentNotFoundError):
            web_api.generate_ui_report(SRC, compare_env="nope")

    def test_parse_error_raises(self):
        with pytest.raises(InfraError):
            web_api.generate_ui_report(BROKEN)


# --------------------------------------------------------------------------- #
# export_dag_svg
# --------------------------------------------------------------------------- #


class TestExportDagSvg:
    def test_standalone_svg_document(self):
        svg = web_api.export_dag_svg(SRC)
        assert svg.startswith('<?xml version="1.0" encoding="UTF-8"?>')
        ET.fromstring(svg)
        assert 'data-name="web"' in svg

    def test_environment_overlay_accepted(self):
        svg = web_api.export_dag_svg(SRC, env_name="prod")
        ET.fromstring(svg)

    def test_unknown_env_raises(self):
        with pytest.raises(EnvironmentNotFoundError):
            web_api.export_dag_svg(SRC, env_name="nope")

    def test_parse_error_raises(self):
        with pytest.raises(InfraError):
            web_api.export_dag_svg(BROKEN)


# --------------------------------------------------------------------------- #
# get_ast_json
# --------------------------------------------------------------------------- #


class TestGetAstJson:
    def test_returns_json_safe_dict(self):
        data = web_api.get_ast_json(SRC)
        assert isinstance(data, dict)
        assert json.loads(json.dumps(data)) == data  # round-trip serializable

    def test_statements_and_names(self):
        data = web_api.get_ast_json(SRC)
        names = [s.get("name") for s in data["statements"] if "name" in s]
        assert "web" in names

    def test_environments_serialized(self):
        data = web_api.get_ast_json(SRC)
        assert data["environments"]
        assert data["environments"][0]["name"] == "prod"

    def test_parse_error_raises(self):
        with pytest.raises(InfraError):
            web_api.get_ast_json(BROKEN)


# --------------------------------------------------------------------------- #
# list_examples
# --------------------------------------------------------------------------- #


class TestListExamples:
    def test_named_examples_present(self):
        examples = web_api.list_examples()
        assert set(examples) == {"hello_world", "web_app", "microservices"}

    def test_examples_parse_and_compile(self):
        for name, source in web_api.list_examples().items():
            r = web_api.compile_to_target(source, "kubernetes")
            assert r["success"], f"{name}: {r['errors']}"

    def test_result_is_defensive_copy(self):
        a = web_api.list_examples()
        a["hello_world"] = "tampered"
        assert web_api.list_examples()["hello_world"] != "tampered"


# --------------------------------------------------------------------------- #
# WASM guarantee: no system / process / disk references in the module
# --------------------------------------------------------------------------- #


_FORBIDDEN_MODULES = {"sys", "os", "subprocess", "webbrowser", "typer"}


class TestWasmGuarantees:
    def test_no_forbidden_module_imports(self):
        tree = ast.parse(Path(web_api.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in _FORBIDDEN_MODULES, alias.name
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                assert root not in _FORBIDDEN_MODULES or root == "infra", node.module

    def test_no_process_or_browser_calls(self):
        tree = ast.parse(Path(web_api.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr not in ("system", "exit", "startfile"), node.attr
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                assert node.id not in ("webbrowser", "subprocess", "os", "sys"), node.id

    def test_no_file_io_calls(self):
        source = Path(web_api.__file__).read_text(encoding="utf-8")
        for needle in ("open(", "read_text", "write_text", "Path("):
            assert needle not in source, needle


# --------------------------------------------------------------------------- #
# generate_explain_report (v0.9.0)
# --------------------------------------------------------------------------- #


class TestGenerateExplainReport:
    def test_success_default_markdown(self):
        result = web_api.generate_explain_report(SRC)
        assert result["success"] is True
        assert result["format"] == "markdown"
        assert result["errors"] == []
        assert "# Architecture Insight" in result["report"]

    def test_text_format(self):
        result = web_api.generate_explain_report(SRC, format="text")
        assert result["success"] is True
        assert "ARCHITECTURE INSIGHT" in result["report"]
        assert "# Architecture" not in result["report"]

    def test_json_format_parseable(self):
        result = web_api.generate_explain_report(SRC, format="json")
        assert result["success"] is True
        doc = json.loads(result["report"])
        assert isinstance(doc, dict)

    def test_ai_audience_has_meta_and_summary(self):
        result = web_api.generate_explain_report(SRC, format="json",
                                                 audience="ai")
        assert result["success"] is True
        doc = json.loads(result["report"])
        assert set(doc["_meta"]) >= {"language", "generator_version",
                                     "checksum"}
        assert 3 <= len(doc["_summary"]) <= 5

    def test_unknown_format_returns_error_dict(self):
        result = web_api.generate_explain_report(SRC, format="yaml")
        assert result["success"] is False
        assert result["report"] == ""
        assert "yaml" in result["errors"][0]

    def test_unknown_audience_returns_error_dict(self):
        result = web_api.generate_explain_report(SRC, audience="robot")
        assert result["success"] is False
        assert "robot" in result["errors"][0]

    def test_parse_error_never_raises(self):
        result = web_api.generate_explain_report(BROKEN)
        assert result["success"] is False
        assert result["errors"]

    def test_environment_overlay_applied(self):
        base = web_api.generate_explain_report(SRC, env_name="prod")
        assert base["success"] is True
        assert "replicas: 5" in base["report"] or "5" in base["report"]

    def test_deterministic_repeated_calls(self):
        first = web_api.generate_explain_report(SRC)
        second = web_api.generate_explain_report(SRC)
        assert first == second
