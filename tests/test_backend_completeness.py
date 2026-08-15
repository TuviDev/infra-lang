"""Completeness: every backend output path and helper is exercised."""

from __future__ import annotations

import yaml

from infra import parse
from infra.backends.base import (
    CompileContext,
    CompileResult,
    evaluate_duration,
    evaluate_expression,
    evaluate_resource,
)
from infra.backends.compose import DockerComposeBackend
from infra.backends.github import GitHubActionsBackend
from infra.backends.kubernetes import KubernetesBackend
from infra.backends.terraform import TerraformBackend
from infra.parser import ast_nodes as n


def docs_of(files) -> list:
    return [d for d in yaml.safe_load_all("\n".join(files.values())) if d is not None]


class TestKubernetesOutputs:
    def test_cache(self):
        files = KubernetesBackend().compile(parse('cache c { type: redis maxmemory: 128Mi }')).files
        kinds = [d["kind"] for d in docs_of(files)]
        assert "Deployment" in kinds and "Service" in kinds

    def test_queue(self):
        files = KubernetesBackend().compile(parse('queue q { type: rabbitmq }')).files
        kinds = [d["kind"] for d in docs_of(files)]
        assert "StatefulSet" in kinds

    def test_network(self):
        files = KubernetesBackend().compile(parse('network n { policy { r: { from: "10.0.0.0/8" ports: [80] } } }')).files
        kinds = [d["kind"] for d in docs_of(files)]
        assert "NetworkPolicy" in kinds

    def test_storage_pvc(self):
        files = KubernetesBackend().compile(parse('storage s { type: pvc size: 10Gi }')).files
        kinds = [d["kind"] for d in docs_of(files)]
        assert "PersistentVolumeClaim" in kinds

    def test_storage_s3_secret(self):
        files = KubernetesBackend().compile(parse('storage s { type: s3 bucket: "b" }')).files
        kinds = [d["kind"] for d in docs_of(files)]
        assert "Secret" in kinds

    def test_environment_namespace(self):
        files = KubernetesBackend().compile(parse('environment dev { namespace: "ns" }')).files
        kinds = [d["kind"] for d in docs_of(files)]
        assert "Namespace" in kinds

    def test_split_mode(self):
        files = KubernetesBackend(split=True).compile(parse('service a { image: "x" }\nservice b { image: "y" }')).files
        assert len(files) >= 2

    def test_expose_loadbalancer(self):
        content = "\n".join(KubernetesBackend().compile(parse('service a { image: "x" port 80 expose: true }')).files.values())
        assert "LoadBalancer" in content

    def test_ingress_ratelimit_cors(self):
        content = "\n".join(KubernetesBackend().compile(
            parse('service a { image: "x" port 80 ingress { host: "h.com" rate_limit { rps: 10 } cors { origins: ["*"] } } }')
        ).files.values())
        assert "Ingress" in content


class TestComposeOutputs:
    def test_secret_to_env(self):
        files = DockerComposeBackend().compile(parse('secret s { a: "v" }')).files
        assert any(".env" in f for f in files)

    def test_config_to_env(self):
        files = DockerComposeBackend().compile(parse('config c { a: "1" }')).files
        assert "docker-compose.yml" in files

    def test_network_bridge(self):
        content = DockerComposeBackend().compile(parse('network n { cidr: "10.0.0.0/16" }')).files["docker-compose.yml"]
        assert "bridge" in content

    def test_minio(self):
        content = DockerComposeBackend().compile(parse('storage m { type: minio }')).files["docker-compose.yml"]
        assert "minio" in content

    def test_mysql_db(self):
        content = DockerComposeBackend().compile(parse('database db { type: mysql }')).files["docker-compose.yml"]
        assert "mysql" in content

    def test_mongodb_db(self):
        content = DockerComposeBackend().compile(parse('database db { type: mongodb }')).files["docker-compose.yml"]
        assert "mongo" in content


class TestTerraformOutputs:
    def test_aws_vpc_network(self):
        content = "\n".join(TerraformBackend().compile(
            parse('cluster c { provider: aws }\nnetwork n { cidr: "10.0.0.0/16" subnets { a: { cidr: "1.1.1.1" } } }')
        ).files.values())
        assert "aws_vpc" in content and "aws_subnet" in content

    def test_sqs_queue(self):
        content = "\n".join(TerraformBackend().compile(
            parse('cluster c { provider: aws }\nqueue q { type: kafka topics { t: { partitions: 1 } } }')
        ).files.values())
        assert "aws_sqs_queue" in content


class TestGitHubOutputs:
    def test_dependabot(self):
        files = GitHubActionsBackend().compile(parse('pipeline p { stages { t: { steps { s: { run: "x" } } } } }')).files
        assert "dependabot.yml" in files

    def test_predefined_actions(self):
        src = 'pipeline p { stages { t: { runsOn: "ubuntu" steps { a: { uses: "setup-python 3.11" } b: { uses: "setup-node 20" } c: { uses: "setup-go 1.21" } d: { uses: "setup-java 17" } } } } }'
        content = "\n".join(GitHubActionsBackend().compile(parse(src)).files.values())
        for action in ["setup-python", "setup-node", "setup-go", "setup-java"]:
            assert action in content

    def test_manual_trigger(self):
        src = 'pipeline p { trigger { manual: true } stages { t: { steps { s: { run: "x" } } } } }'
        content = "\n".join(GitHubActionsBackend().compile(parse(src)).files.values())
        assert "workflow_dispatch" in content

    def test_concurrency(self):
        src = 'pipeline p { concurrency { group: "g" cancelInProgress: true } stages { t: { steps { s: { run: "x" } } } } }'
        content = "\n".join(GitHubActionsBackend().compile(parse(src)).files.values())
        assert "cancel-in-progress" in content


class TestBackendBaseHelpers:
    def test_compile_result(self):
        assert CompileResult(files={}).is_empty
        assert not CompileResult(files={"a": "b"}).is_empty

    def test_evaluate_duration(self):
        assert evaluate_duration(n.Duration(30, "s")) == "30s"
        assert evaluate_duration(n.Duration(1.5, "s")) == "1.5s"

    def test_evaluate_resource(self):
        assert evaluate_resource(n.ResourceValue(128, "Mi"), "kubernetes") == "128Mi"
        assert evaluate_resource(n.ResourceValue(1, "Gi"), "docker") == str(1024**3)

    def test_compile_context_from_program(self):
        program = n.Program(statements=(n.VariableDecl(name="V", value=n.Literal(1)),))
        ctx = CompileContext.from_program(program, symbol_table=None)
        assert "V" in ctx.variables

    def test_evaluate_template(self):
        ctx = CompileContext(program=n.Program(), symbol_table=None)
        ctx.variables["T"] = n.Literal("v1")
        ts = n.TemplateString(parts=("img:", ("expr", "T")))
        assert evaluate_expression(ts, ctx) == "img:v1"

    def test_snake_to_camel(self):
        from infra.backends.base import BaseYAMLBackend

        assert BaseYAMLBackend()._snake_to_camel("read_only") == "readOnly"

    def test_clean_none(self):
        from infra.backends.base import BaseYAMLBackend

        b = BaseYAMLBackend()
        assert b._clean_none({"a": None, "b": 1}) == {"b": 1}
        assert b._clean_none({"x": []}) == {}
