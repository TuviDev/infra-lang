"""Deep audit of the Kubernetes backend output.

Each generated resource is checked for its concrete fields, exact values and
apiVersion. Fixing these is a contract guarantee for users.
"""

from __future__ import annotations

import yaml

from infra import parse
from infra.backends.kubernetes import KubernetesBackend


def compile_docs(source):
    result = KubernetesBackend().compile(parse(source))
    content = "\n".join(result.files.values())
    return [d for d in yaml.safe_load_all(content) if d is not None]


def get_kind(docs, kind):
    return next((d for d in docs if d.get("kind") == kind), None)


def _container(docs):
    dep = get_kind(docs, "Deployment")
    return dep["spec"]["template"]["spec"]["containers"][0]


class TestDeploymentOutput:
    def test_apiVersion_is_apps_v1(self):
        dep = get_kind(compile_docs('service api { image: "nginx:1.25" }'), "Deployment")
        assert dep["apiVersion"] == "apps/v1"

    def test_metadata_name_matches_service_name(self):
        dep = get_kind(compile_docs('service my-api { image: "nginx:1.25" }'), "Deployment")
        assert dep["metadata"]["name"] == "my-api"

    def test_managed_by_label_correct_value(self):
        dep = get_kind(compile_docs('service api { image: "nginx:1.25" }'), "Deployment")
        assert dep["metadata"]["labels"]["app.kubernetes.io/managed-by"] == "infra-lang"

    def test_replicas_is_integer(self):
        dep = get_kind(compile_docs('service api { image: "nginx:1.25" replicas: 3 }'), "Deployment")
        assert isinstance(dep["spec"]["replicas"], int)
        assert dep["spec"]["replicas"] == 3

    def test_image_exact_match(self):
        image = "myregistry.io/myapp:v1.2.3-hotfix"
        dep = get_kind(compile_docs(f'service api {{ image: "{image}" }}'), "Deployment")
        assert dep["spec"]["template"]["spec"]["containers"][0]["image"] == image

    def test_selector_matches_template_labels(self):
        dep = get_kind(compile_docs('service api { image: "nginx:1.25" }'), "Deployment")
        selector = dep["spec"]["selector"]["matchLabels"]
        template_labels = dep["spec"]["template"]["metadata"]["labels"]
        for k, v in selector.items():
            assert template_labels.get(k) == v

    def test_resource_requests_format(self):
        source = """
        service api {
            image: "nginx:1.25"
            resources {
                requests { cpu: 200m, memory: 256Mi }
                limits   { cpu: 1000m, memory: 512Mi }
            }
        }
        """
        resources = _container(compile_docs(source)).get("resources", {})
        assert resources["requests"]["cpu"] == "200m"
        assert resources["requests"]["memory"] == "256Mi"
        assert resources["limits"]["cpu"] == "1000m"
        assert resources["limits"]["memory"] == "512Mi"

    def test_liveness_probe_http_path(self):
        source = 'service api { image: "nginx:1.25" health: http("/health") }'
        probe = _container(compile_docs(source)).get("livenessProbe", {})
        assert probe.get("httpGet", {}).get("path") == "/health"

    def test_env_var_literal_value(self):
        source = 'service api { image: "nginx:1.25" env { LOG_LEVEL: "debug" } }'
        env = _container(compile_docs(source)).get("env", [])
        log = next((e for e in env if e.get("name") == "LOG_LEVEL"), None)
        assert log is not None
        assert log.get("value") == "debug"

    def test_env_var_from_secret(self):
        source = 'service api { image: "nginx:1.25" env { DB_URL: from secret "db-creds".url } }'
        env = _container(compile_docs(source)).get("env", [])
        db = next((e for e in env if e.get("name") == "DB_URL"), None)
        assert db is not None
        assert "valueFrom" in db or "secretKeyRef" in str(db)

    def test_security_context_user(self):
        source = 'service api { image: "nginx:1.25" security { user: 1000 } }'
        ctx = _container(compile_docs(source)).get("securityContext", {})
        assert ctx.get("runAsUser") == 1000

    def test_strategy_rolling_update(self):
        dep = get_kind(compile_docs('service api { image: "nginx:1.25" strategy: rolling }'), "Deployment")
        assert dep["spec"]["strategy"]["type"] == "RollingUpdate"

    def test_strategy_recreate(self):
        dep = get_kind(compile_docs('service api { image: "nginx:1.25" strategy: recreate }'), "Deployment")
        assert dep["spec"]["strategy"]["type"] == "Recreate"


class TestServiceOutput:
    def test_service_apiVersion_v1(self):
        svc = get_kind(compile_docs('service api { image: "nginx:1.25" port: 80 }'), "Service")
        assert svc["apiVersion"] == "v1"

    def test_service_selector_matches_deployment(self):
        docs = compile_docs('service api { image: "nginx:1.25" port: 80 }')
        dep_labels = get_kind(docs, "Deployment")["spec"]["selector"]["matchLabels"]
        svc_selector = get_kind(docs, "Service")["spec"]["selector"]
        for k, v in dep_labels.items():
            assert svc_selector.get(k) == v

    def test_port_number_in_service(self):
        svc = get_kind(compile_docs('service api { image: "nginx:1.25" port: 8080 }'), "Service")
        ports = svc["spec"]["ports"]
        assert any(p.get("port") == 8080 or p.get("targetPort") == 8080 for p in ports)


class TestServicePortNames:
    """Regression: multi-port Services must have unique port names (kubectl)."""

    def test_single_port_needs_no_name(self):
        # A Service exposing a single port is valid without a name; it must not
        # crash and may leave the name absent.
        svc = get_kind(compile_docs('service api { image: "nginx:1.25" port: 8080 }'), "Service")
        ports = svc["spec"]["ports"]
        assert len(ports) == 1

    def test_multi_port_has_unique_names(self):
        src = 'service events { image: "rabbitmq:3.12" port 5672:5672 port 15672:15672 }'
        svc = get_kind(compile_docs(src), "Service")
        ports = svc["spec"]["ports"]
        assert len(ports) == 2
        names = [p.get("name") for p in ports]
        assert all(names), f"every multi-port Service port needs a name: {ports}"
        assert len(set(names)) == len(names), f"port names must be unique: {names}"

    def test_multi_port_names_reflect_protocol_and_port(self):
        src = 'service events { image: "rabbitmq:3.12" port 5672:5672 port 15672:15672 }'
        svc = get_kind(compile_docs(src), "Service")
        names = {p.get("name") for p in svc["spec"]["ports"]}
        assert "tcp-5672" in names
        assert "tcp-15672" in names

    def test_colliding_ports_get_unique_suffixed_names(self):
        # Two ports on the same number would otherwise share a base name; the
        # index suffix must disambiguate them.
        src = 'service api { image: "x" port 80:80 port 80:8080 }'
        svc = get_kind(compile_docs(src), "Service")
        names = [p.get("name") for p in svc["spec"]["ports"]]
        assert all(names)
        assert len(set(names)) == len(names), f"colliding ports need unique names: {names}"


class TestSecretBase64:
    """Regression: every value in a Secret's data: must be valid base64."""

    def test_data_values_are_valid_base64(self):
        src = 'secret creds { api_key: from env "API_KEY" token: "plain" }'
        sec = get_kind(compile_docs(src), "Secret")
        import base64

        for key, value in sec["data"].items():
            base64.b64decode(value, validate=True)  # raises on invalid base64

    def test_plain_value_decodes_back(self):
        src = 'secret creds { password: "supersecret" }'
        sec = get_kind(compile_docs(src), "Secret")
        import base64

        assert base64.b64decode(sec["data"]["password"]).decode() == "supersecret"

    def test_env_placeholder_round_trips(self):
        src = 'secret creds { api_key: from env "API_KEY" }'
        sec = get_kind(compile_docs(src), "Secret")
        import base64

        assert base64.b64decode(sec["data"]["api_key"]).decode() == "from-env:API_KEY"


class TestStatefulSetOutput:
    def test_statefulset_for_database(self):
        sts = get_kind(compile_docs("database db { type: postgres }"), "StatefulSet")
        assert sts is not None
        assert sts["apiVersion"] == "apps/v1"

    def test_postgres_image_in_statefulset(self):
        sts = get_kind(compile_docs('database db { type: postgres version: "15" }'), "StatefulSet")
        containers = sts["spec"]["template"]["spec"]["containers"]
        assert any("postgres" in c.get("image", "") for c in containers)

    def test_mysql_image_in_statefulset(self):
        sts = get_kind(compile_docs("database db { type: mysql }"), "StatefulSet")
        containers = sts["spec"]["template"]["spec"]["containers"]
        assert any("mysql" in c.get("image", "") for c in containers)


class TestHPAOutput:
    def test_hpa_apiVersion_autoscaling_v2(self):
        hpa = get_kind(
            compile_docs('service api { image: "nginx:1.25" autoscale { min: 2 max: 10 } }'),
            "HorizontalPodAutoscaler",
        )
        assert hpa["apiVersion"] == "autoscaling/v2"

    def test_hpa_scale_target_ref(self):
        hpa = get_kind(
            compile_docs('service myapp { image: "nginx:1.25" autoscale { min: 2 max: 10 } }'),
            "HorizontalPodAutoscaler",
        )
        ref = hpa["spec"]["scaleTargetRef"]
        assert ref["kind"] == "Deployment"
        assert ref["name"] == "myapp"
        assert ref["apiVersion"] == "apps/v1"

    def test_hpa_min_max_replicas(self):
        hpa = get_kind(
            compile_docs('service api { image: "nginx:1.25" autoscale { min: 3 max: 15 } }'),
            "HorizontalPodAutoscaler",
        )
        assert hpa["spec"]["minReplicas"] == 3
        assert hpa["spec"]["maxReplicas"] == 15

    def test_hpa_cpu_metric_present(self):
        hpa = get_kind(
            compile_docs('service api { image: "nginx:1.25" autoscale { min: 2 max: 10 target_cpu: 75 } }'),
            "HorizontalPodAutoscaler",
        )
        metrics = hpa["spec"]["metrics"]
        cpu = next((m for m in metrics if m.get("resource", {}).get("name") == "cpu"), None)
        assert cpu is not None
        assert cpu["resource"]["target"]["averageUtilization"] == 75


class TestNetworkPolicyOutput:
    def test_netpol_apiVersion(self):
        source = 'service api { image: "nginx:1.25" network_policy { allow_from: [frontend] deny_from: ["*"] } }'
        np = get_kind(compile_docs(source), "NetworkPolicy")
        assert np is not None
        assert "networking.k8s.io" in np["apiVersion"]

    def test_wildcard_deny_produces_empty_ingress(self):
        source = 'service api { image: "nginx:1.25" network_policy { deny_from: ["*"] } }'
        np = get_kind(compile_docs(source), "NetworkPolicy")
        ingress = np["spec"].get("ingress", [])
        assert ingress == [] or ingress is None


class TestPDBOutput:
    def test_pdb_apiVersion_policy_v1(self):
        source = 'service api { image: "nginx:1.25" disruption { min_available: 1 } }'
        pdb = get_kind(compile_docs(source), "PodDisruptionBudget")
        assert pdb["apiVersion"] == "policy/v1"

    def test_pdb_min_available_value(self):
        source = 'service api { image: "nginx:1.25" disruption { min_available: 2 } }'
        pdb = get_kind(compile_docs(source), "PodDisruptionBudget")
        assert pdb["spec"]["minAvailable"] == 2


class TestSecretOutput:
    def test_secret_apiVersion_v1(self):
        s = get_kind(compile_docs('secret db { key: from env "KEY" }'), "Secret")
        assert s["apiVersion"] == "v1"

    def test_secret_type_opaque(self):
        s = get_kind(compile_docs('secret db { key: from env "KEY" }'), "Secret")
        assert s.get("type") == "Opaque"


class TestResourceQuotaOutput:
    def test_quota_apiVersion_v1(self):
        source = """
        environment prod {
            namespace: "prod"
            quotas { max_cpu: 10cores max_memory: 20Gi max_pods: 100 }
        }
        """
        rq = get_kind(compile_docs(source), "ResourceQuota")
        assert rq is not None
        assert rq["apiVersion"] == "v1"

    def test_quota_hard_fields(self):
        source = """
        environment prod {
            namespace: "prod"
            quotas { max_cpu: 10cores max_memory: 20Gi max_pods: 100 }
        }
        """
        rq = get_kind(compile_docs(source), "ResourceQuota")
        hard = rq["spec"]["hard"]
        assert "requests.cpu" in hard
        assert "limits.memory" in hard
        assert hard["pods"] == "100"
