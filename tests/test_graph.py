"""Session 10 - Infra graph visualization (Zadanie 4)."""

from __future__ import annotations

from typer.testing import CliRunner

from infra.cli.main import app

runner = CliRunner()

MAIN = (
    'service api { image: "nginx:1.0" ingress { host: "h" } depends: [db] }\n'
    'service worker { image: "x:1" depends: [db] }\n'
    'database db { type: postgres }\n'
    "cache session { type: redis }\n"
)


def _write_main(tmp_path):
    f = tmp_path / "main.infra"
    f.write_text(MAIN)
    return f


def _graph(tmp_path, *args):
    f = _write_main(tmp_path)
    return runner.invoke(app, ["graph", str(f), *args])


class TestGraphBasic:
    def test_generates_nonempty_output(self, tmp_path):
        r = _graph(tmp_path)
        assert r.exit_code == 0, r.output
        assert r.output.strip() != ""

    def test_ascii_contains_service_name(self, tmp_path):
        r = _graph(tmp_path)
        assert "api" in r.output
        assert "worker" in r.output

    def test_ascii_contains_edge(self, tmp_path):
        r = _graph(tmp_path)
        assert "api" in r.output and "db" in r.output

    def test_ascii_type_prefix_and_ingress(self, tmp_path):
        r = _graph(tmp_path)
        assert "[service: api]" in r.output
        assert "[database: db]" in r.output
        assert "INGRESS (h)" in r.output


class TestGraphDOT:
    def test_dot_contains_service_name(self, tmp_path):
        r = _graph(tmp_path, "--format", "dot")
        assert "api" in r.output

    def test_database_has_different_style(self, tmp_path):
        r = _graph(tmp_path, "--format", "dot")
        assert 'shape=box' in r.output  # service
        assert 'shape=cylinder' in r.output  # database
        assert 'label="api\\nservice"' in r.output
        assert 'label="db\\npostgres"' in r.output

    def test_dot_has_edge(self, tmp_path):
        r = _graph(tmp_path, "--format", "dot")
        assert '"api" -> "db"' in r.output

    def test_dot_starts_with_digraph(self, tmp_path):
        r = _graph(tmp_path, "--format", "dot")
        assert r.output.lstrip().startswith("digraph infra")


class TestGraphMermaid:
    def test_mermaid_starts_with_graph(self, tmp_path):
        r = _graph(tmp_path, "--format", "mermaid")
        assert r.output.lstrip().startswith("graph")

    def test_mermaid_contains_node_and_edge(self, tmp_path):
        r = _graph(tmp_path, "--format", "mermaid")
        assert "api" in r.output
        assert "-->" in r.output


class TestGraphOutput:
    def test_output_file_created(self, tmp_path):
        f = _write_main(tmp_path)
        out = tmp_path / "graph.dot"
        r = runner.invoke(
            app, ["graph", str(f), "--format", "dot", "--output", str(out)]
        )
        assert r.exit_code == 0
        assert out.exists()
        assert "digraph" in out.read_text(encoding="utf-8")
