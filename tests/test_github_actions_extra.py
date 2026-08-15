"""GitHub Actions output branches (triggers, matrix, artifacts, steps)."""

from __future__ import annotations

import yaml

from infra import parse, validate
from infra.backends.github import GitHubActionsBackend
from infra.backends.kubernetes import KubernetesBackend


def gh_content(src: str) -> str:
    files = GitHubActionsBackend().compile(parse(src)).files
    return "\n".join(files.values())


class TestGitHubExtraPaths:
    def test_schedule_trigger_cron(self):
        content = gh_content(
            'pipeline p { trigger { schedule: "0 2 * * *" } '
            'stages { t: { steps { s: { run: "x" } } } } }'
        )
        assert "schedule" in content

    def test_artifacts_upload_download(self):
        content = gh_content(
            'pipeline p { artifacts { upload: ["dist/"] download: ["dist/"] } '
            'stages { t: { steps { s: { run: "x" } } } } }'
        )
        assert "upload-artifact" in content

    def test_matrix_downloaded(self):
        content = gh_content(
            'pipeline p { stages { t: { runsOn: "ubuntu" '
            'matrix { python: ["3.10", "3.11"] } steps { s: { run: "x" } } } } }'
        )
        assert "matrix" in content

    def test_stage_condition(self):
        content = gh_content(
            'pipeline p { stages { t: { runsOn: "ubuntu", if: "always()", '
            'steps { s: { run: "x" } } } } }'
        )
        assert "if: always()" in content

    def test_step_continue_on_error(self):
        content = gh_content(
            'pipeline p { stages { t: { steps { s: { run: "x", continueOnError: true } } } } }'
        )
        assert "continue-on-error" in content

    def test_step_timeout(self):
        content = gh_content(
            'pipeline p { stages { t: { steps { s: { run: "x", timeout: 30s } } } } }'
        )
        assert "timeout-minutes" in content

    def test_step_env(self):
        content = gh_content(
            'pipeline p { stages { t: { steps { s: { run: "x", env: { K: "v" } } } } } }'
        )
        assert "K: v" in content

    def test_uses_with_args(self):
        content = gh_content(
            'pipeline p { stages { t: { steps { s: { uses: "checkout", '
            'with: { ref: "main" } } } } } }'
        )
        assert "actions/checkout" in content

    def test_needs_multiple(self):
        content = gh_content(
            'pipeline p { stages { '
            'a: { steps { s: { run: "1" } } } '
            'b: { steps { s: { run: "2" } } } '
            'c: { needs: ["a", "b"] steps { s: { run: "3" } } } } }'
        )
        assert "a" in content and "b" in content

    def test_tags_trigger(self):
        content = gh_content(
            'pipeline p { trigger { tags: ["v*"] } stages { t: { steps { s: { run: "x" } } } } }'
        )
        assert "tags" in content

    def test_events_trigger(self):
        content = gh_content(
            'pipeline p { trigger { events: ["push"] } stages { t: { steps { s: { run: "x" } } } } }'
        )
        assert "push" in content


class TestTransformerExtraPaths:
    def test_match_expr_multiple_arms(self):
        prog = parse('let m = match s { 1 -> "a" 2 -> "b" 3 -> "c" _ -> "d" }')
        from infra.parser.ast_nodes import MatchExpr

        assert isinstance(prog.statements[-1].value, MatchExpr)

    def test_nested_if_expr(self):
        from infra.parser.ast_nodes import IfExpr

        v = parse("let x = if a then (if b then c else d) else e").statements[-1].value
        assert isinstance(v, IfExpr)
        assert isinstance(v.then_branch, IfExpr)

    def test_call_kwargs(self):
        from infra.parser.ast_nodes import Call

        v = parse("let x = foo(a, b, key = c)").statements[-1].value
        assert isinstance(v, Call)
        assert len(v.kwargs) == 1

    def test_attribute_chain(self):
        from infra.parser.ast_nodes import Attribute

        v = parse("let x = a.b.c").statements[-1].value
        assert isinstance(v, Attribute)
        assert isinstance(v.obj, Attribute)

    def test_percentage_literal(self):
        from infra.parser.ast_nodes import Percentage

        v = parse("let x = 25%").statements[-1].value
        assert isinstance(v, Percentage)
        assert v.value == 25.0

    def test_storage_lifecycle_full(self):
        prog = parse('storage s { type: s3 lifecycle { retention: 7d prefix: "x" transition: "GLACIER" expiration: 30d } }')
        from infra.parser.ast_nodes import StorageDef, StorageLifecycle

        s = [x for x in prog.statements if isinstance(x, StorageDef)][0]
        assert isinstance(s.lifecycle, StorageLifecycle)
        assert s.lifecycle.retention is not None

    def test_queue_topics_and_users(self):
        prog = parse('queue q { type: kafka topics { t: { partitions: 3 } } users { u: "p" } }')
        from infra.parser.ast_nodes import QueueDef

        q = [x for x in prog.statements if isinstance(x, QueueDef)][0]
        assert len(q.topics) == 1 and len(q.users) == 1

    def test_cluster_iam_block(self):
        prog = parse('cluster c { provider: aws iam { role { actions: ["eks:DescribeCluster"] } } }')
        from infra.parser.ast_nodes import ClusterDef

        c = [x for x in prog.statements if isinstance(x, ClusterDef)][0]
        assert c.iam is not None and c.iam.role is not None

    def test_selinux_block(self):
        prog = parse('service s { image: "x" security { selinux { level: "s0", role: "r", type: "t" } } }')
        from infra.parser.ast_nodes import ServiceDef

        svc = [x for x in prog.statements if isinstance(x, ServiceDef)][0]
        assert svc.security is not None and svc.security.selinux is not None

    def test_strategy_canary_steps(self):
        prog = parse('service s { image: "x" strategy { type: "canary", canary: { weight: 10, steps: 5 } } }')
        from infra.parser.ast_nodes import ServiceDef

        svc = [x for x in prog.statements if isinstance(x, ServiceDef)][0]
        assert svc.strategy is not None

    def test_grpc_health(self):
        prog = parse('service s { image: "x" health grpc(50051) }')
        from infra.parser.ast_nodes import ServiceDef

        svc = [x for x in prog.statements if isinstance(x, ServiceDef)][0]
        assert svc.health is not None and svc.health.kind == "grpc"

    def test_tcp_health(self):
        prog = parse('service s { image: "x" health tcp(3306) }')
        from infra.parser.ast_nodes import ServiceDef

        svc = [x for x in prog.statements if isinstance(x, ServiceDef)][0]
        assert svc.health.kind == "tcp"
