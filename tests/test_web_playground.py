"""Web Playground structure & template regression tests (v0.8.0).

The playground is client-side JS; these tests assert its key contracts
textually (no browser needed) and validate that every architecture
template embedded in ``web/app.js`` still parses and validates with the
real Python compiler.
"""

from __future__ import annotations

import re
from pathlib import Path

import infra

WEB = Path(__file__).resolve().parent.parent / "web"
APP_JS = WEB / "app.js"
INDEX_HTML = WEB / "index.html"

_TEMPLATE_IDS = [
    "01_web_app",
    "02_microservices",
    "03_cloud_native",
    "04_scheduled_pipeline",
]

_TEMPLATE_RE = re.compile(
    r'id: "(\d{2}_[a-z_]+)",\s*\n\s*label: "[^"]*",\s*\n\s*source: `(.*?)`',
    re.S,
)


def _app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _index() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


class TestArchitectureTemplates:
    def test_all_four_templates_present(self):
        ids = [tid for tid, _src in _TEMPLATE_RE.findall(_app_js())]
        assert ids == _TEMPLATE_IDS

    def test_templates_use_extractable_backtick_sources(self):
        # the extraction convention documented in app.js must hold
        sources = _TEMPLATE_RE.findall(_app_js())
        assert len(sources) == len(_TEMPLATE_IDS)
        for _tid, source in sources:
            assert "`" not in source
            assert "${" not in source

    def test_templates_cover_expected_constructs(self):
        sources = dict(_TEMPLATE_RE.findall(_app_js()))
        assert "nginx" in sources["01_web_app"]
        assert "redis" in sources["01_web_app"]
        assert "postgres" in sources["01_web_app"]
        assert "rabbitmq" in sources["02_microservices"]
        assert "autoscale" in sources["03_cloud_native"]
        assert "network_policy" in sources["03_cloud_native"]
        assert "secret_store" in sources["03_cloud_native"]
        assert "schedule" in sources["04_scheduled_pipeline"]
        assert "pipeline" in sources["04_scheduled_pipeline"]

    def test_templates_parse_and_validate(self):
        from infra.analyzer.validator import SemanticValidator
        from infra.parser import parse

        for tid, source in _TEMPLATE_RE.findall(_app_js()):
            program = parse(source)
            result = SemanticValidator().validate(program)
            assert result.errors == [], (
                f"template {tid} has semantic errors: "
                f"{[e.code for e in result.errors]}"
            )


class TestSelectorAndTabs:
    def test_template_optgroup_and_engine_group(self):
        app = _app_js()
        assert "Architecture templates" in app
        assert "Engine examples (from compiler)" in app
        assert "optgroup" in app

    def test_dag_graph_tab_wired_to_export_dag_svg(self):
        index = _index()
        assert 'data-tab="dag"' in index
        assert "DAG Graph" in index
        assert 'id="out-dag"' in index
        app = _app_js()
        assert "renderDag" in app
        assert "export_dag_svg" in app

    def test_output_pane_ids_stay_in_sync(self):
        index = _index()
        for tab in ("compose", "kubernetes", "terraform", "dag", "dashboard"):
            assert f'"{tab}"' in index or f'"{tab},' not in index  # tab listed
            assert f'id="out-{tab}"' in index, f"missing output pane {tab}"


class TestBundleExport:
    def test_bundle_button_in_html(self):
        assert 'id="bundle-btn"' in _index()
        assert "Download All Manifests (.zip)" in _index()

    def test_jszip_cdn_loaded(self):
        assert "jszip" in _index().lower()

    def test_bundle_targets_all_backends(self):
        app = _app_js()
        assert 'BUNDLE_TARGETS = ["compose", "kubernetes", "terraform", "helm"]' in app
        assert "downloadAllManifests" in app
        assert "infra-manifests.zip" in app


class TestVersionPinning:
    def test_wheel_name_matches_installed_version(self):
        expected = f'WHEEL_NAME = "infra_lang-{infra.__version__}-py3-none-any.whl"'
        assert expected in _app_js()

    def test_playground_still_uses_web_api_entry_points(self):
        app = _app_js()
        for call in (
            "compile_to_target",
            "export_dag_svg",
            "generate_ui_report",
            "list_examples",
        ):
            assert call in app
