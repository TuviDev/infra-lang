"""DAG export to SVG (`infra graph --format svg` / `-o out.svg`) — v0.5.5."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from typer.testing import CliRunner

from infra.analyzer.ui_generator import generate_dag_svg
from infra.cli.main import app
from infra.parser import parse

runner = CliRunner()

SRC = """\
service api {
  image: "nginx:1.0"
  ingress { host: "h" }
  depends: [db]
}
service worker {
  image: "x:1"
  depends: [db]
}
database db {
  type: postgres
  storage: 10Gi
}
cache session {
  type: redis
}
queue jobs {
  type: rabbitmq
}
environment "prod" {
  service api {
    replicas: 3
  }
}
"""


def _write(tmp_path: Path, content: str = SRC) -> Path:
    f = tmp_path / "main.infra"
    f.write_text(content, encoding="utf-8")
    return f


# --------------------------------------------------------------------------- #
# Generator (unit)
# --------------------------------------------------------------------------- #


class TestGenerateDagSvg:
    def test_document_shape(self):
        svg = generate_dag_svg(parse(SRC))
        assert svg.startswith('<?xml version="1.0" encoding="UTF-8"?>')
        assert svg.rstrip().endswith("</svg>")
        assert "Architecture DAG" in svg

    def test_well_formed_xml(self):
        ET.fromstring(generate_dag_svg(parse(SRC)))

    def test_nodes_and_edges_annotated(self):
        svg = generate_dag_svg(parse(SRC))
        assert 'data-name="api"' in svg
        assert 'data-name="db"' in svg
        assert 'data-from="api" data-to="db"' in svg

    def test_empty_program_has_empty_state(self):
        svg = generate_dag_svg(parse("# no workloads at all\n"))
        ET.fromstring(svg)
        assert "No workloads declared." in svg

    def test_svg_is_self_contained(self):
        svg = generate_dag_svg(parse(SRC))
        # same DAG as the dashboard must render without any network access
        # (xmlns is a namespace identifier, not a fetched resource)
        assert "<script" not in svg
        assert "href=" not in svg
        assert "@import" not in svg


# --------------------------------------------------------------------------- #
# CLI — `infra graph`
# --------------------------------------------------------------------------- #


class TestGraphSvgCLI:
    def test_output_file_created_by_format(self, tmp_path):
        f = _write(tmp_path)
        out = tmp_path / "dag.out"
        r = runner.invoke(
            app, ["graph", str(f), "--format", "svg", "-o", str(out)]
        )
        assert r.exit_code == 0, r.output
        assert "[OK] Graph written to" in r.output
        body = out.read_text(encoding="utf-8")
        assert body.startswith('<?xml version="1.0" encoding="UTF-8"?>')
        ET.fromstring(body)

    def test_svg_format_inferred_from_suffix(self, tmp_path):
        f = _write(tmp_path)
        out = tmp_path / "dag.svg"
        r = runner.invoke(app, ["graph", str(f), "-o", str(out)])
        assert r.exit_code == 0, r.output
        body = out.read_text(encoding="utf-8")
        assert "<svg" in body and 'data-name="api"' in body

    def test_stdout_when_no_output(self, tmp_path):
        f = _write(tmp_path)
        r = runner.invoke(app, ["graph", str(f), "--format", "svg"])
        assert r.exit_code == 0, r.output
        assert r.output.lstrip().startswith('<?xml version="1.0"')
        ET.fromstring(r.output)

    def test_env_flag_accepted_for_svg(self, tmp_path):
        f = _write(tmp_path)
        r = runner.invoke(
            app, ["graph", str(f), "--format", "svg", "-e", "prod"]
        )
        assert r.exit_code == 0, r.output
        ET.fromstring(r.output)

    def test_env_flag_accepted_for_ascii(self, tmp_path):
        f = _write(tmp_path)
        r = runner.invoke(app, ["graph", str(f), "-e", "prod"])
        assert r.exit_code == 0, r.output
        assert "api" in r.output

    def test_unknown_env_exit_1(self, tmp_path):
        f = _write(tmp_path)
        out = tmp_path / "dag.svg"
        r = runner.invoke(
            app, ["graph", str(f), "-e", "nope", "-o", str(out)]
        )
        assert r.exit_code == 1
        assert "Environment 'nope' is not defined" in r.output
        assert not out.exists()

    def test_multiple_files_rejected_for_svg(self, tmp_path):
        f = _write(tmp_path)
        g = tmp_path / "other.infra"
        g.write_text(SRC, encoding="utf-8")
        r = runner.invoke(
            app, ["graph", str(f), str(g), "--format", "svg"]
        )
        assert r.exit_code == 1
        assert "exactly one" in r.output

    def test_broken_file_exit_1(self, tmp_path):
        f = _write(tmp_path, "service api {\n")
        r = runner.invoke(app, ["graph", str(f), "--format", "svg"])
        assert r.exit_code == 1


class TestGraphCollectEdges:
    def test_same_file_twice_dedupes_nodes(self, tmp_path):
        f = _write(tmp_path)
        r = runner.invoke(app, ["graph", str(f), str(f)])
        assert r.exit_code == 0, r.output
        assert r.output.count("[service: api] ◄──") == 1

    def test_empty_file_reports_no_infrastructure(self, tmp_path):
        f = _write(tmp_path, "# no workloads at all\n")
        r = runner.invoke(app, ["graph", str(f)])
        assert r.exit_code == 0, r.output
        assert "(no infrastructure)" in r.output

    def test_dependency_on_undeclared_service_renders_bare_label(self, tmp_path):
        f = _write(
            tmp_path, 'service solo { image: "i:1" depends: [ghost] }\n'
        )
        r = runner.invoke(app, ["graph", str(f)])
        assert r.exit_code == 0, r.output
        assert "[service: solo] ──► [ghost]" in r.output


class TestDashboardDownloadLink:
    def test_dashboard_embeds_download_svg(self):
        from infra.analyzer.cost import estimate_cost
        from infra.analyzer.ui_generator import generate_ui_html

        spec = parse(SRC)
        html = generate_ui_html(spec, estimate_cost(spec))
        assert 'download="infra-dag.svg"' in html
        assert "data:image/svg+xml" in html
        assert "Download SVG" in html

    def test_download_payload_is_the_standalone_svg(self):
        from urllib.parse import unquote

        from infra.analyzer.ui_generator import _dag_download_link

        spec = parse(SRC)
        link = _dag_download_link(spec)
        payload = link.split("data:image/svg+xml;charset=utf-8,", 1)[1]
        # the data URI ends at the first '"' (quote() escapes '"' as %22)
        svg = unquote(payload.split('"', 1)[0])
        ET.fromstring(svg)
        assert 'data-name="api"' in svg
