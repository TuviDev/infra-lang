"""Tests for ``infra.analyzer.ui_generator`` — standalone HTML dashboard."""

from __future__ import annotations

from infra.analyzer.cost import CostEstimate, CostItem, estimate_cost
from infra.analyzer.drift import (
    STATUS_MISSING,
    DriftItem,
    DriftReport,
)
from infra.analyzer.ui_generator import (
    InfrastructureSpec,
    _canvas_size,
    _collect_dag,
    _dag_svg,
    _DagNode,
    _layout,
    generate_ui_html,
)
from infra.parser import ast_nodes as n
from infra.parser import parse

SAMPLE = """
service frontend {
    image: "nginx:1.25"
    port: 8080
    depends_on: [api]
}
service api {
    image: "myapp:1.0"
    port: 9000
    replicas: 2
    depends_on: [db, redis]
}
database db {
    type: "postgres"
    version: "16"
}
cache redis {
    type: "redis"
}
queue events {
    type: "rabbitmq"
}
network vpc_main {
    cidr: "10.0.0.0/16"
}
secret_store "vault" {
    provider: "vault"
}
network_policy "app_sec" {
    target: "api"
    allow_ingress: ["frontend"]
}
environment prod {
    provider: "aws"
    region: "eu-west-1"
    namespace: "prod"
}
environment "staging" {
    service api {
        replicas: 5
    }
}
"""


def render(src: str = SAMPLE, **kwargs) -> str:
    program = parse(src)
    return generate_ui_html(program, estimate_cost(program), **kwargs)


class TestSpecAlias:
    def test_infrastructure_spec_is_program(self):
        assert InfrastructureSpec is n.Program

    def test_generate_is_callable(self):
        assert callable(generate_ui_html)


class TestDagSection:
    def test_service_and_workload_nodes_present(self):
        html_out = render()
        assert 'node-service" data-name="frontend"' in html_out
        assert 'node-service" data-name="api"' in html_out
        assert 'node-database" data-name="db"' in html_out
        assert 'node-cache" data-name="redis"' in html_out
        assert 'node-queue" data-name="events"' in html_out

    def test_depends_on_edges_present(self):
        html_out = render()
        assert 'data-from="frontend" data-to="api"' in html_out
        assert 'data-from="api" data-to="db"' in html_out
        assert 'data-from="api" data-to="redis"' in html_out

    def test_shared_lane_nodes_present(self):
        html_out = render()
        assert 'node-network" data-name="vpc_main"' in html_out
        assert 'node-secret_store" data-name="vault"' in html_out
        assert 'node-network_policy" data-name="app_sec"' in html_out

    def test_ghost_node_for_undeclared_dependency(self):
        html_out = render('service api { image: "x" depends_on: [db] }')
        assert 'node-external" data-name="db"' in html_out
        assert 'data-from="api" data-to="db"' in html_out

    def test_empty_program_renders_empty_note(self):
        html_out = render("")
        assert "No workloads declared in this file." in html_out
        assert (
            "<svg" not in html_out.split("Architecture DAG")[1].split("</section>")[0]
        )

    def test_layout_layers_dependencies_to_the_right(self):
        program = parse(
            'service a { image: "x" depends_on: [b] }\n'
            'service b { image: "y" depends_on: [c] }\n'
            'database c { type: "postgres" }'
        )
        nodes, edges = _collect_dag(program)
        _layout(nodes, edges)
        by_name = {nd.name: nd for nd in nodes}
        assert by_name["a"].x < by_name["b"].x < by_name["c"].x
        assert edges == [("a", "b"), ("b", "c")]

    def test_layout_handles_dependency_cycles(self):
        program = parse(
            'service a { image: "x" depends_on: [b] }\n'
            'service b { image: "y" depends_on: [a] }'
        )
        nodes, _edges = _collect_dag(program)
        _layout(nodes, _edges)
        assert {nd.name for nd in nodes} == {"a", "b"}

    def test_collect_skips_non_workload_statements(self):
        nodes, edges = _collect_dag(parse(""))
        assert nodes == [] and edges == []

    def test_canvas_size_has_nonempty_default(self):
        width, height = _canvas_size([], [])
        assert width > 0 and height > 0

    def test_dag_svg_skips_unknown_edge_endpoints(self):
        svg = _dag_svg(
            [_DagNode("a", "service", "service")],
            [("a", "phantom-missing-node")],
        )
        assert 'data-from="a"' not in svg
        assert "node-service" in svg


class TestFinOpsSection:
    def test_table_rows_and_total(self):
        html_out = render()
        assert "finops-table" in html_out
        assert "<td>api</td>" in html_out
        assert "<td>db</td>" in html_out
        assert "Estimated monthly total:" in html_out

    def test_share_bar_chart(self):
        html_out = render()
        assert 'bar-row" data-resource="api"' in html_out
        assert 'bar-row" data-resource="db"' in html_out
        assert "USD (" in html_out  # "<amount> USD (<share>%)"

    def test_empty_estimate_renders_note(self):
        html_out = generate_ui_html(parse('service api { image: "x" }'), CostEstimate())
        assert "No billable resources in this file." in html_out

    def test_zero_total_estimate_no_division_error(self):
        estimate = CostEstimate(items=[CostItem(name="free", kind="service")])
        html_out = generate_ui_html(parse('service free { image: "x" }'), estimate)
        assert ">free</td>" in html_out
        assert "0.0%" in html_out


class TestDriftSection:
    def test_no_report_renders_none_state(self):
        html_out = render()
        assert 'data-state="none"' in html_out
        assert "Live drift was not collected" in html_out

    def test_clean_report_renders_in_sync(self):
        report = DriftReport(target="docker", in_sync=["api", "db"])
        html_out = render(drift_report=report)
        assert 'data-state="clean"' in html_out
        assert "IN-SYNC" in html_out
        assert "api, db" in html_out

    def test_clean_report_without_in_sync_list(self):
        report = DriftReport(target="docker")
        html_out = render(drift_report=report)
        assert "Verified in sync: none" in html_out

    def test_drifted_report_highlights_fields(self):
        report = DriftReport(
            target="docker",
            items=[
                DriftItem(
                    resource="api",
                    parameter="replicas",
                    expected="2",
                    live="1",
                ),
                DriftItem(
                    resource="db",
                    parameter="image",
                    expected="postgres:16",
                    live="<unknown>",
                    status=STATUS_MISSING,
                ),
            ],
        )
        html_out = render(drift_report=report)
        assert 'data-state="drifted"' in html_out
        assert "DRIFTED" in html_out
        assert 'data-resource="api"' in html_out
        assert '<td class="drift-exp">2</td>' in html_out
        assert '<td class="drift-live">1</td>' in html_out
        assert "MISSING" in html_out
        # the "<unknown>" value must be escaped, never raw HTML
        assert "<unknown>" not in html_out
        assert "&lt;unknown&gt;" in html_out

    def test_error_report_renders_error_state(self):
        report = DriftReport(target="docker", error="docker daemon unreachable")
        html_out = render(drift_report=report)
        assert 'data-state="error"' in html_out
        assert "docker daemon unreachable" in html_out

    def test_missing_cli_tool_badge(self):
        report = DriftReport(
            target="k8s",
            error="kubectl is not available or the cluster is unreachable",
        )
        html_out = render(drift_report=report)
        assert 'class="badge badge-err">CLI TOOL MISSING' in html_out
        assert "Live drift probe (k8s)" in html_out

    def test_timeout_badge(self):
        report = DriftReport(
            target="compose",
            error="`docker inspect` timed out after 30.0s",
        )
        html_out = render(drift_report=report)
        assert 'class="badge badge-err">PROBE TIMEOUT' in html_out

    def test_unreachable_badge(self):
        report = DriftReport(target="k8s", error="api server 10.0.0.1:6443 unreachable")
        html_out = render(drift_report=report)
        assert 'class="badge badge-err">CLUSTER UNREACHABLE' in html_out

    def test_generic_error_badge(self):
        report = DriftReport(target="k8s", error="unexpected probe output")
        html_out = render(drift_report=report)
        assert 'class="badge badge-err">PROBE ERROR' in html_out


class TestProbeErrorBadge:
    def test_classification_matrix(self):
        from infra.analyzer.ui_generator import _probe_error_badge

        assert _probe_error_badge("x timed out after 1s") == "PROBE TIMEOUT"
        assert _probe_error_badge("kubectl is not available") == ("CLI TOOL MISSING")
        assert _probe_error_badge("cluster unreachable") == ("CLUSTER UNREACHABLE")
        assert _probe_error_badge("something else") == "PROBE ERROR"

    def test_timeout_wins_over_unavailable(self):
        from infra.analyzer.ui_generator import _probe_error_badge

        # partial probes can report a timeout alongside a tool problem
        msg = "tool not available; probe timed out"
        assert _probe_error_badge(msg) == "PROBE TIMEOUT"


class TestEnvironmentSelector:
    def test_options_for_both_environment_forms(self):
        html_out = render()
        assert '<option value="prod"' in html_out
        assert '<option value="staging"' in html_out
        assert '<option value="base"' in html_out

    def test_active_env_is_preselected_and_shown(self):
        html_out = render(env_name="prod")
        assert '<option value="prod" selected>' in html_out
        assert 'data-active-env="prod"' in html_out
        assert "provider: aws, region: eu-west-1, namespace: prod" in html_out

    def test_overlay_env_shows_override_count(self):
        html_out = render()
        assert "1 service override" in html_out

    def test_no_environments_renders_note(self):
        html_out = render('service api { image: "x" }')
        assert "No environments declared." in html_out


class TestStandaloneOutput:
    def test_no_external_resource_references(self):
        html_out = render()
        assert 'src="http' not in html_out
        assert 'href="http' not in html_out
        assert "url(http" not in html_out
        assert "<script src" not in html_out
        assert "<link" not in html_out

    def test_is_complete_html_document(self):
        html_out = render()
        assert html_out.startswith("<!DOCTYPE html>")
        assert "</html>" in html_out
        assert '<meta charset="utf-8">' in html_out

    def test_inline_style_and_script_present(self):
        html_out = render()
        assert "<style>" in html_out and "<script>" in html_out

    def test_version_present_in_header(self):
        from infra.version import __version__

        assert f"infra-lang v{__version__}" in render()

    def test_html_escaping_of_names(self):
        # A crafted name cannot inject markup into the report.
        program = n.Program(
            statements=(n.ServiceDef(name="evil<img src=x onerror=alert(1)>"),)
        )
        html_out = generate_ui_html(program, CostEstimate())
        assert "<img src=x onerror=alert(1)>" not in html_out
        assert "evil&lt;img" in html_out
