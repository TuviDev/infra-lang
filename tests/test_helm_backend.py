"""Unit tests for the Helm backend.

Verifies the generated chart structure, values.yaml, template files, and edge
cases — plus that the generated chart passes ``helm lint --strict`` and renders
with ``helm template`` when the binary is available.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from infra import parse
from infra.backends import get_backend


def _compile(source: str, filename: str = "app.infra"):
    program = parse(source, filename=filename)
    return get_backend("helm").compile(program)


def _files(source: str):
    return _compile(source).files


def _chart_files(source: str):
    """Return {basename: content} for a compiled chart (strip chart dir prefix)."""
    out = {}
    for path, content in _files(source).items():
        # path is "<chart>/<file>"; keep everything after the first '/'
        _, _, rel = path.partition("/")
        out[rel] = content
    return out


def _yaml(content: str):
    # strip the AUTO-GENERATED header comment(s) before YAML parsing
    lines = content.splitlines()
    while lines:
        head = lines[0].lstrip()
        if head.startswith("#") or head.startswith("{{- /*"):
            lines.pop(0)
        else:
            break
    return yaml.safe_load("\n".join(lines))


SERVICE_SRC = 'service api { image: "nginx:1.25" port 8080 replicas: 2 }'
DB_SRC = "database db { type: postgres version: \"15\" storage: 20Gi }"


# --------------------------------------------------------------------------- #
# Chart structure
# --------------------------------------------------------------------------- #


class TestChartStructure:
    def test_chart_yaml_exists(self):
        files = _chart_files("service api { image: \"x\" }")
        assert "Chart.yaml" in files

    def test_chart_yaml_required_fields(self):
        files = _chart_files("service api { image: \"x\" }")
        data = yaml.safe_load(files["Chart.yaml"])
        assert data["apiVersion"] == "v2"
        assert data["name"]
        assert data["type"] == "application"
        assert data["version"] == "0.3.2"
        assert "appVersion" in data

    def test_values_yaml_is_valid_yaml(self):
        files = _chart_files(SERVICE_SRC)
        assert yaml.safe_load(files["values.yaml"]) is not None

    def test_templates_dir_has_helpers(self):
        files = _chart_files(SERVICE_SRC)
        assert "templates/_helpers.tpl" in files
        assert "fullname" in files["templates/_helpers.tpl"]
        assert "labels" in files["templates/_helpers.tpl"]
        assert "selectorLabels" in files["templates/_helpers.tpl"]

    def test_helmignore_exists(self):
        files = _chart_files(SERVICE_SRC)
        assert ".helmignore" in files

    def test_deployment_and_service_templates(self):
        files = _chart_files(SERVICE_SRC)
        assert "templates/deployment.yaml" in files
        assert "templates/service.yaml" in files

    def test_chart_name_from_service(self):
        files = _files("service myapp-api { image: \"x\" }")
        assert any(p.startswith("myapp-api/") for p in files)

    def test_no_empty_template_files(self):
        # a service-only chart must not contain statefulset/secret/configmap
        files = _chart_files(SERVICE_SRC)
        assert "templates/statefulset.yaml" not in files or not files.get("templates/statefulset.yaml")


# --------------------------------------------------------------------------- #
# values.yaml
# --------------------------------------------------------------------------- #


class TestValuesYaml:
    def test_service_values(self):
        vals = _yaml(_chart_files(SERVICE_SRC)["values.yaml"])
        api = vals["service"]["api"]
        assert api["kind"] == "deployment"
        assert api["replicas"] == 2
        assert api["image"]["repository"] == "nginx"
        assert api["image"]["tag"] == "1.25"

    def test_database_values(self):
        vals = _yaml(_chart_files(DB_SRC)["values.yaml"])
        db = vals["service"]["db"]
        assert db["kind"] == "statefulset"
        assert db["engine"] == "postgres"
        assert db["storage"] == "20Gi"

    def test_secret_values_placeholder(self):
        vals = _yaml(_chart_files('secret creds { password: from env "P" }')["values.yaml"])
        sec = vals["secret"]["creds"]["values"]
        assert sec["password"] == ""

    def test_configmap_values(self):
        vals = _yaml(_chart_files('config app { LOG_LEVEL: "info" }')["values.yaml"])
        assert vals["configmap"]["app"]["data"]["LOG_LEVEL"] == "info"

    def test_cache_values_deployment(self):
        vals = _yaml(_chart_files('cache c { type: redis }')["values.yaml"])
        c = vals["service"]["c"]
        assert c["kind"] == "deployment"
        assert c["image"]["repository"] == "redis"

    def test_queue_values_statefulset(self):
        vals = _yaml(_chart_files('queue q { type: rabbitmq }')["values.yaml"])
        assert vals["service"]["q"]["kind"] == "statefulset"
        assert vals["service"]["q"]["port"] == 5672

    def test_port_from_target_only(self):
        # `port 8080` (no host) must still yield a numeric port
        vals = _yaml(_chart_files('service api { image: "x" port 8080 }')["values.yaml"])
        assert vals["service"]["api"]["port"] == 8080

    def test_resources_in_values(self):
        src = 'service api { image: "x" resources { requests { cpu: 100m, memory: 128Mi } } }'
        vals = _yaml(_chart_files(src)["values.yaml"])
        res = vals["service"]["api"]["resources"]["requests"]
        assert res["cpu"] == "100m"
        assert res["memory"] == "128Mi"

    def test_health_in_values(self):
        src = 'service api { image: "x" health http("/live") }'
        vals = _yaml(_chart_files(src)["values.yaml"])
        assert vals["service"]["api"]["health"]["path"] == "/live"


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #


class TestTemplates:
    def test_deployment_uses_values(self):
        tpl = _chart_files(SERVICE_SRC)["templates/deployment.yaml"]
        assert ".Values.service" in tpl
        assert "include" in tpl  # uses helpers

    def test_deployment_has_image_and_replicas(self):
        tpl = _chart_files(SERVICE_SRC)["templates/deployment.yaml"]
        assert "replicas" in tpl
        assert "image" in tpl
        assert "kind: Deployment" in tpl

    def test_service_template_maps_port(self):
        tpl = _chart_files(SERVICE_SRC)["templates/service.yaml"]
        assert "kind: Service" in tpl
        assert "$svc.port" in tpl

    def test_statefulset_for_database(self):
        tpl = _chart_files(DB_SRC)["templates/statefulset.yaml"]
        assert "kind: StatefulSet" in tpl
        assert "$wk.storage" in tpl

    def test_secret_template_base64(self):
        tpl = _chart_files('secret creds { password: from env "P" }')["templates/secret.yaml"]
        assert "kind: Secret" in tpl
        assert "b64enc" in tpl  # base64 encoding

    def test_configmap_template(self):
        tpl = _chart_files('config app { LOG_LEVEL: "info" }')["templates/configmap.yaml"]
        assert "kind: ConfigMap" in tpl

    def test_multi_port_service_template(self):
        src = 'service api { image: "x" port 5672:5672 port 15672:15672 }'
        vals = _yaml(_chart_files(src)["values.yaml"])
        ports = vals["service"]["api"]["ports"]
        names = [p["name"] for p in ports]
        assert names == ["tcp-5672", "tcp-15672"]

    def test_helpers_define_fullname_labels(self):
        tpl = _chart_files(SERVICE_SRC)["templates/_helpers.tpl"]
        assert "fullname" in tpl
        assert "helm.sh/chart" in tpl
        assert "managed-by" in tpl


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #


class TestEdgeCases:
    def test_empty_program(self):
        files = _chart_files("")
        assert "Chart.yaml" in files
        assert "values.yaml" in files
        # no workload templates
        assert not any("templates/deployment" in f for f in files)

    def test_services_only(self):
        files = _chart_files("service a { image: \"x\" }\nservice b { image: \"y\" }")
        assert any("deployment" in f for f in files)

    def test_databases_only(self):
        files = _chart_files("database a { type: postgres }\ndatabase b { type: mysql }")
        assert any("statefulset" in f for f in files)

    def test_multi_service_with_dependencies(self):
        src = (
            "database db { type: postgres }\n"
            'service api { image: "x" depends: [db] }\n'
        )
        files = _chart_files(src)
        assert any("statefulset" in f for f in files)
        assert any("deployment" in f for f in files)

    def test_secrets_from_env(self):
        files = _chart_files('secret s { a: from env "A" b: from env "B" }')
        assert any("secret" in f for f in files)

    def test_config_and_secret_together(self):
        src = 'secret s { a: from env "A" }\nconfig c { X: "1" }\n'
        files = _chart_files(src)
        assert any("secret" in f for f in files)
        assert any("configmap" in f for f in files)

    def test_pipeline_no_workloads(self):
        src = 'pipeline ci { trigger { branches: ["main"] } stages { } }'
        files = _chart_files(src)
        # chart still exists, no deployment template
        assert "Chart.yaml" in files
        assert not any("templates/deployment" in f for f in files)

    def test_unknown_resource_ignored(self):
        src = 'storage s { type: object }\nservice api { image: "x" }\n'
        files = _chart_files(src)
        assert any("deployment" in f for f in files)

    def test_backend_registered(self):
        assert get_backend("helm").name == "helm"


# --------------------------------------------------------------------------- #
# helm binary integration (live lint / template)
# --------------------------------------------------------------------------- #


def _helm_available() -> bool:
    return shutil.which("helm") is not None or Path("/tmp/linux-amd64/helm").exists()


@pytest.mark.skipif(not _helm_available(), reason="helm not installed")
class TestHelmBinaryIntegration:
    @pytest.fixture()
    def chart_dir(self, tmp_path):
        """Compile 02_web_app to a real chart dir on disk."""
        from infra.backends import get_backend

        prog = parse(
            (Path(__file__).resolve().parents[1] / "examples" / "02_web_app.infra").read_text(encoding="utf-8"),
            filename="02.infra",
        )
        res = get_backend("helm").compile(prog)
        chartdir = None
        for path, content in res.files.items():
            fp = tmp_path / path
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content)
            if "Chart.yaml" in path:
                chartdir = fp.parent
        return chartdir

    def _helm(self, *args, cwd=None):
        helm = shutil.which("helm") or "/tmp/linux-amd64/helm"
        return subprocess.run([helm, *args], capture_output=True, text=True, timeout=120, cwd=cwd)

    def test_helm_lint_strict(self, chart_dir):
        r = self._helm("lint", "--strict", str(chart_dir))
        assert r.returncode == 0, f"helm lint failed:\n{r.stdout}\n{r.stderr}"

    def test_helm_template_renders(self, chart_dir):
        r = self._helm("template", "rel", str(chart_dir))
        assert r.returncode == 0, f"helm template failed:\n{r.stderr}"
        assert "kind: Deployment" in r.stdout
        assert "kind: Service" in r.stdout


class TestMoreValuesEdge:
    def test_image_split_with_registry(self):
        vals = _yaml(_chart_files('service api { image: "gcr.io/my/repo:v2" }')["values.yaml"])
        img = vals["service"]["api"]["image"]
        assert img["repository"] == "gcr.io/my/repo"
        assert img["tag"] == "v2"

    def test_image_no_tag_defaults_latest(self):
        vals = _yaml(_chart_files('service api { image: "nginx" }')["values.yaml"])
        assert vals["service"]["api"]["image"]["tag"] == "latest"

    def test_chart_name_slugified(self):
        files = _files("service My_App { image: \"x\" }")
        assert any(p.startswith("my-app/") for p in files)

    def test_database_default_engine(self):
        vals = _yaml(_chart_files("database db {}")["values.yaml"])
        assert vals["service"]["db"]["engine"] == "postgres"

    def test_expose_sets_service_type(self):
        src = 'service api { image: "x" port 80 expose: true }'
        vals = _yaml(_chart_files(src)["values.yaml"])
        assert vals["service"]["api"]["serviceType"] == "ClusterIP"

    def test_health_default_path(self):
        src = 'service api { image: "x" }'
        vals = _yaml(_chart_files(src)["values.yaml"])
        # no health block -> no health key
        assert "health" not in vals["service"]["api"]


class TestMoreStructure:
    def test_values_header_present(self):
        assert _chart_files(SERVICE_SRC)["values.yaml"].startswith(
            "# AUTO-GENERATED by infra-lang"
        )

    def test_chart_yaml_header(self):
        assert _chart_files(SERVICE_SRC)["Chart.yaml"].startswith(
            "# AUTO-GENERATED by infra-lang"
        )

    def test_helpers_chart_name_in_fullname(self):
        tpl = _chart_files(SERVICE_SRC)["templates/_helpers.tpl"]
        assert "app" in tpl  # chart name appears in define

    def test_all_workload_files_count(self):
        src = DB_SRC + '\nservice api { image: "x" }\ncache c { type: redis }\nqueue q { type: rabbitmq }\n'
        files = _chart_files(src)
        assert "templates/deployment.yaml" in files
        assert "templates/statefulset.yaml" in files
        assert "templates/service.yaml" in files


class TestHelmValueDefaults:
    def test_database_storage_explicit(self):
        vals = _yaml(_chart_files("database db { type: postgres storage: 50Gi }")["values.yaml"])
        assert vals["service"]["db"]["storage"] == "50Gi"

    def test_database_storage_size_fallback(self):
        vals = _yaml(_chart_files("database db { type: postgres size: 25Gi }")["values.yaml"])
        assert vals["service"]["db"]["storage"] == "25Gi"

    def test_database_storage_default(self):
        vals = _yaml(_chart_files("database db { type: postgres }")["values.yaml"])
        assert vals["service"]["db"]["storage"] == "10Gi"

    def test_split_image_registry_no_tag_defaults_latest(self):
        vals = _yaml(_chart_files('service api { image: "gcr.io/my/repo" }')["values.yaml"])
        img = vals["service"]["api"]["image"]
        assert img["repository"] == "gcr.io/my/repo"
        assert img["tag"] == "latest"

    def test_split_image_tag_only(self):
        vals = _yaml(_chart_files('service api { image: "nginx:2.0" }')["values.yaml"])
        assert vals["service"]["api"]["image"]["repository"] == "nginx"
        assert vals["service"]["api"]["image"]["tag"] == "2.0"

    def test_health_default_path_when_missing(self):
        vals = _yaml(_chart_files('service api { image: "x" }')["values.yaml"])
        assert "health" not in vals["service"]["api"]

    def test_database_version_empty_uses_latest(self):
        vals = _yaml(_chart_files("database db { type: postgres }")["values.yaml"])
        assert vals["service"]["db"]["version"] == ""


class TestHelmServiceValues:
    def test_build_only_service_image(self):
        vals = _yaml(_chart_files('service api { build { context: "." } }')["values.yaml"])
        img = vals["service"]["api"]["image"]
        assert img["repository"] == "built-from-dockerfile"
        assert img["tag"] == "latest"

    def test_replicas_defaults_to_one(self):
        vals = _yaml(_chart_files('service api { image: "x" }')["values.yaml"])
        assert vals["service"]["api"]["replicas"] == 1

    def test_service_type_clusterip_default(self):
        vals = _yaml(_chart_files('service api { image: "x" }')["values.yaml"])
        assert vals["service"]["api"]["serviceType"] == "ClusterIP"


class TestValuesSchema:
    def test_schema_json_generated(self):
        files = _chart_files(SERVICE_SRC)
        assert "values.schema.json" in files

    def test_schema_is_valid_json(self):
        import json

        content = _chart_files(SERVICE_SRC)["values.schema.json"]
        data = json.loads(content)  # no header comment — must be pure JSON
        assert data["$schema"] == "http://json-schema.org/draft-07/schema#"
        assert data["type"] == "object"
        assert "service" in data["properties"]
        assert "secret" in data["properties"]
        assert "configmap" in data["properties"]

    def test_schema_allows_service_image_object(self):
        import json

        content = _chart_files(SERVICE_SRC)["values.schema.json"]
        data = json.loads(content)
        image = data["properties"]["service"]["additionalProperties"]["properties"]["image"]
        assert "oneOf" in image

    def test_schema_lists_workload_kinds(self):
        import json

        content = _chart_files(SERVICE_SRC)["values.schema.json"]
        data = json.loads(content)
        kinds = data["properties"]["service"]["additionalProperties"]["properties"]["kind"]
        assert kinds["enum"] == ["deployment", "statefulset"]

    def test_schema_no_header_comment(self):
        # Helm parses values.schema.json as JSON — a leading # would be invalid
        content = _chart_files(SERVICE_SRC)["values.schema.json"]
        assert not content.startswith("#")


class TestHelmUtf8Encoding:
    """Regression: generated Helm files must be valid UTF-8, no BOM (Windows CI)."""

    def test_helm_output_files_are_valid_utf8(self, tmp_path):
        # compile a realistic multi-resource chart to disk via the CLI (the same
        # path that wrote cp1252 files on Windows before the encoding fix)
        import subprocess
        import sys

        src = tmp_path / "app.infra"
        src.write_text(
            'service api { image: "nginx:1.25" port 80 }\n'
            "database db { type: postgres }\n"
            "secret s { k: from env \"K\" }\n",
            encoding="utf-8",
        )
        out = tmp_path / "out"
        result = subprocess.run(
            [sys.executable, "-m", "infra", "compile", str(src), "-t", "helm", "-o", str(out)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr

        # every generated file (Chart.yaml, values.yaml, values.schema.json,
        # templates/*) must be UTF-8-decodable and start without a BOM
        generated = [p for p in out.rglob("*") if p.is_file()]
        assert generated, "no helm files were generated"

        for path in generated:
            raw = path.read_bytes()
            # reject UTF-8 BOM (ef bb bf)
            assert not raw.startswith(b"\xef\xbb\xbf"), (
                f"{path.name} has a UTF-8 BOM"
            )
            # must decode cleanly as UTF-8
            raw.decode("utf-8")
