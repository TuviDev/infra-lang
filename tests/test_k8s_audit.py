"""Deep audit of the Kubernetes backend output.

Each generated resource is checked for its concrete fields, exact values and
apiVersion. Fixing these is a contract guarantee for users.
"""

from __future__ import annotations

import base64

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


class TestQueueServicePortNames:
    """Regression: the queue (RabbitMQ) Service is a separate code path from
    ``_compile_service`` and must also emit unique port names."""

    def _queue_service(self):
        docs = compile_docs("queue events { type: rabbitmq }")
        return get_kind(docs, "Service")

    def test_queue_multi_port_has_unique_names(self):
        svc = self._queue_service()
        ports = svc["spec"]["ports"]
        assert len(ports) == 2
        names = [p.get("name") for p in ports]
        assert all(names), f"queue Service ports need names: {ports}"
        assert len(set(names)) == len(names), f"queue port names must be unique: {names}"

    def test_queue_port_names_reflect_rabbitmq_ports(self):
        svc = self._queue_service()
        names = {p.get("name") for p in svc["spec"]["ports"]}
        assert "tcp-5672" in names
        assert "tcp-15672" in names

    def test_queue_ports_have_correct_numbers(self):
        svc = self._queue_service()
        nums = {p.get("port") for p in svc["spec"]["ports"]}
        assert nums == {5672, 15672}


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

    def test_secret_has_managed_by_label(self):
        # Regression: standalone Secrets lacked the managed-by label while
        # every other resource had it (inconsistent labels across resources).
        s = get_kind(compile_docs('secret db { key: from env "KEY" }'), "Secret")
        assert s["metadata"]["labels"]["app.kubernetes.io/managed-by"] == "infra-lang"

    def test_configmap_has_managed_by_label(self):
        s = get_kind(compile_docs('config app { LOG_LEVEL: "info" }'), "ConfigMap")
        assert s["metadata"]["labels"]["app.kubernetes.io/managed-by"] == "infra-lang"


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


class TestProbesAndSecurity:
    def test_readiness_probe_http(self):
        c = _container(compile_docs('service api { image: "x" health http("/ready") }'))
        assert c["readinessProbe"]["httpGet"]["path"] == "/ready"

    def test_liveness_probe_http_path(self):
        c = _container(compile_docs('service api { image: "x" health http("/live") }'))
        assert c["livenessProbe"]["httpGet"]["path"] == "/live"

    def test_security_context_privileged_false_absent(self):
        c = _container(compile_docs('service api { image: "x" security { user: 1000 } }'))
        # no privileged by default
        assert "privileged" not in str(c.get("securityContext") or {})

    def test_security_context_user_and_group(self):
        c = _container(compile_docs('service api { image: "x" security { user: 1000, group: 2000 } }'))
        sc = c["securityContext"]
        assert sc["runAsUser"] == 1000
        assert sc["runAsGroup"] == 2000

    def test_volume_mount_in_container(self):
        c = _container(compile_docs('service api { image: "x" volumes { data: { mountPath: "/var/data" } } }'))
        mounts = c.get("volumeMounts", [])
        assert any(m.get("mountPath") == "/var/data" for m in mounts)


class TestResourcesAndIngress:
    def test_resources_limits_mapped(self):
        c = _container(compile_docs(
            'service api { image: "x" resources { '
            'requests { cpu: 100m, memory: 128Mi } limits { cpu: 1000m, memory: 512Mi } } }'
        ))
        res = c["resources"]
        assert res["requests"]["cpu"] == "100m"
        assert res["limits"]["memory"] == "512Mi"

    def test_ingress_port_uses_service_port(self):
        ing = get_kind(compile_docs(
            'service api { image: "x" port 8080 ingress { host: "api.example.com" } }'
        ), "Ingress")
        backend = ing["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]["port"]
        assert backend["number"] == 8080

    def test_ingress_tls_secret_name(self):
        ing = get_kind(compile_docs(
            'service api { image: "x" port 80 ingress { host: "h.com" tls: true } }'
        ), "Ingress")
        assert ing["spec"]["tls"][0]["hosts"] == ["h.com"]
        assert "tls" in ing["spec"]["tls"][0]["secretName"]

    def test_annotations_propagated(self):
        dep = get_kind(compile_docs(
            'service api { image: "x" annotations: { team: "platform" } }'
        ), "Deployment")
        ann = dep["metadata"].get("annotations") or {}
        assert ann.get("team") == "platform"

    def test_labels_propagated(self):
        dep = get_kind(compile_docs(
            'service api { image: "x" labels: { tier: "web" } }'
        ), "Deployment")
        assert dep["metadata"]["labels"]["tier"] == "web"

    def test_container_has_command(self):
        c = _container(compile_docs('service api { image: "x" command: ["run"] }'))
        assert c["command"] == ["run"]


class TestAutoscaleAndDisruption:
    def test_hpa_memory_metric(self):
        hpa = get_kind(compile_docs(
            'service api { image: "x" resources { limits { memory: 512Mi } } '
            'autoscale { min: 2, max: 5, target_memory: 70 } }'
        ), "HorizontalPodAutoscaler")
        metrics = hpa["spec"]["metrics"]
        assert any(m.get("type") == "Resource" for m in metrics)

    def test_disruption_max_unavailable(self):
        pdb = get_kind(compile_docs(
            'service api { image: "x" replicas: 3 disruption { max_unavailable: 1 } }'
        ), "PodDisruptionBudget")
        assert pdb["spec"]["maxUnavailable"] == 1


class TestProbeThresholds:
    def _probe_container(self, health_src):
        src = f'service api {{ image: "nginx:1.25" {health_src} }}'
        return _container(compile_docs(src))

    def test_http_probe_full_timings(self):
        c = self._probe_container(
            'health http("/health") '
            "{ initialDelay: 5s interval: 10s timeout: 3s retries: 4 }"
        )
        probe = c["livenessProbe"]
        assert probe["httpGet"]["path"] == "/health"
        assert probe["httpGet"]["port"] == 80
        assert probe["initialDelaySeconds"] == 5
        assert probe["periodSeconds"] == 10
        assert probe["timeoutSeconds"] == 3
        assert probe["failureThreshold"] == 4

    def test_tcp_probe_with_port(self):
        c = self._probe_container("health tcp(80) { port: 8080 }")
        assert c["livenessProbe"]["tcpSocket"] == {"port": 8080}

    def test_grpc_probe_with_port(self):
        c = self._probe_container("health grpc(80) { port: 50051 }")
        assert c["livenessProbe"]["grpc"] == {"port": 50051}

    def test_zero_duration_omitted(self):
        # only set fields are emitted; unset timings must not appear
        c = self._probe_container('health http("/") { interval: 30s }')
        probe = c["livenessProbe"]
        assert probe.get("initialDelaySeconds") is None
        assert probe.get("timeoutSeconds") is None
        assert probe["periodSeconds"] == 30


class TestMultiPortAndSecrets:
    def test_multi_port_service_names(self):
        docs = compile_docs('service api { image: "nginx:1.25" port 80 port 443 }')
        svc = get_kind(docs, "Service")
        names = [p["name"] for p in svc["spec"]["ports"]]
        assert "tcp-80" in names
        assert "tcp-443" in names

    def test_empty_secret_data_not_crash(self):
        # a secret with entries emits base64 data; empty is tolerated
        docs = compile_docs("secret s { }\nservice api { image: \"nginx:1.25\" }")
        assert docs  # no crash

    def test_secret_data_base64(self):
        src = 'secret s { k: "v" }\nservice api { image: "nginx:1.25" }'
        docs = compile_docs(src)
        import base64

        secret = get_kind(docs, "Secret")
        assert "k" in secret["data"]
        decoded = base64.b64decode(secret["data"]["k"]).decode()
        assert decoded == "v"


class TestScheduleRBAC:
    def test_cronjob_and_rbac_emitted(self):
        docs = compile_docs(
            'service api { image: "nginx:1.25" '
            'schedule { "0 2 * * *": { replicas: 1 } } }'
        )
        kinds = {d["kind"] for d in docs}
        assert "CronJob" in kinds
        assert "ServiceAccount" in kinds
        assert "ClusterRole" in kinds
        assert "ClusterRoleBinding" in kinds


class TestStorageContracts:
    def test_s3_storage_secret(self):
        docs = compile_docs(
            'storage s { type: s3 bucket: "mybkt" region: "eu-west-1" }'
        )
        sec = get_kind(docs, "Secret")
        assert sec is not None
        assert sec["metadata"]["name"] == "s-credentials"
        assert sec["stringData"]["bucket"] == "mybkt"
        assert sec["stringData"]["region"] == "eu-west-1"

    def test_s3_bucket_defaults_to_name(self):
        docs = compile_docs('storage s { type: s3 }')
        sec = get_kind(docs, "Secret")
        assert sec["stringData"]["bucket"] == "s"

    def test_pvc_storage_with_size(self):
        docs = compile_docs('storage s { type: gcs size: 50Gi }')
        pvc = get_kind(docs, "PersistentVolumeClaim")
        assert pvc["spec"]["resources"]["requests"]["storage"] == "50Gi"

    def test_pvc_default_size(self):
        docs = compile_docs('storage s { type: gcs }')
        pvc = get_kind(docs, "PersistentVolumeClaim")
        assert pvc["spec"]["resources"]["requests"]["storage"] == "10Gi"

    def test_pvc_access_mode(self):
        docs = compile_docs('storage s { type: gcs accessMode: ReadWriteMany }')
        pvc = get_kind(docs, "PersistentVolumeClaim")
        assert pvc["spec"]["accessModes"] == ["ReadWriteMany"]


class TestNetworkPolicyContracts:
    def test_network_policy_with_from_and_ports(self):
        docs = compile_docs(
            'network n { policy { allow: { from: "10.0.0.0/8" ports: [80,443] } } }'
        )
        np = get_kind(docs, "NetworkPolicy")
        assert np is not None
        ingress = np["spec"]["ingress"]
        assert ingress[0]["spec"]["from"] == [{"ipBlock": {"cidr": "10.0.0.0/8"}}]
        assert ingress[0]["spec"]["ports"] == [{"port": 80}, {"port": 443}]

    def test_network_policy_empty_policy(self):
        # empty policy: _clean_none drops the empty spec, NP still emitted
        docs = compile_docs('network n { policy { } }')
        np = get_kind(docs, "NetworkPolicy")
        assert np is not None
        assert np["metadata"]["name"] == "n"
        assert np.get("spec") is None or np["spec"].get("ingress") == []


class TestSingleResourceCompile:
    """The single-resource compile_service/compile_database entry points."""

    def test_compile_service_single(self):
        from infra.parser import ast_nodes as n

        prog = parse('service api { image: "nginx:1" port 80 }')
        svc = [s for s in prog.statements if isinstance(s, n.ServiceDef)][0]
        out = KubernetesBackend().compile_service(svc)
        assert "Deployment" in out
        assert "apiVersion: apps/v1" in out

    def test_compile_database_single(self):
        from infra.parser import ast_nodes as n

        prog = parse("database db { type: postgres }")
        db = [s for s in prog.statements if isinstance(s, n.DatabaseDef)][0]
        out = KubernetesBackend().compile_database(db)
        assert "StatefulSet" in out


class TestImageAndEnvResolution:
    def test_identifier_image_resolved(self):
        docs = compile_docs(
            'const APP_IMG = "nginx:1"\nservice api { image: APP_IMG }'
        )
        c = _container(docs)
        assert c["image"] == "nginx:1"

    def test_env_from_config(self):
        docs = compile_docs(
            'config c { A: "1" }\nservice api { image: "x" env { X: from config "c".A } }'
        )
        c = _container(docs)
        assert c["env"][0]["valueFrom"]["configMapKeyRef"] == {
            "name": "c",
            "key": "A",
        }

    def test_env_from_field(self):
        docs = compile_docs(
            'service api { image: "x" env { POD: from field "metadata.name" } }'
        )
        c = _container(docs)
        assert c["env"][0]["valueFrom"]["fieldRef"]["fieldPath"] == "metadata.name"


class TestSecretSources:
    def test_secret_from_vault(self):
        docs = compile_docs('secret s { k: from vault "v" }')
        sec = get_kind(docs, "Secret")
        assert base64.b64decode(sec["data"]["k"]).decode() == "from-vault:v"

    def test_secret_from_env(self):
        docs = compile_docs('secret s { k: from env "K" }')
        sec = get_kind(docs, "Secret")
        assert base64.b64decode(sec["data"]["k"]).decode() == "from-env:K"

    def test_secret_from_file(self):
        docs = compile_docs('secret s { k: from file "f.txt" }')
        sec = get_kind(docs, "Secret")
        assert sec["data"]["k"] == ""


class TestDatabaseUsersSecret:
    def test_database_users_emits_secret(self):
        docs = compile_docs(
            'database db { type: postgres users { u1: { password: "p" } } }'
        )
        sec = get_kind(docs, "Secret")
        assert sec is not None
        assert sec["metadata"]["name"] == "db-credentials"


class TestLifecycleHooks:
    def test_pre_stop_post_start(self):
        docs = compile_docs(
            'service api { image: "x" '
            'lifecycle { preStop { exec: ["sleep", "5"] } '
            'postStart { exec: ["echo", "hi"] } } }'
        )
        c = _container(docs)
        lc = c["lifecycle"]
        assert lc["preStop"]["exec"]["command"] == ["sleep", "5"]
        assert lc["postStart"]["exec"]["command"] == ["echo", "hi"]


class TestBuildAndArgs:
    def test_build_service_image_and_args(self):
        docs = compile_docs(
            'service api { build { context: "." } args: ["--x"] }'
        )
        c = _container(docs)
        assert c["image"] == "built-from-dockerfile"
        assert c["args"] == ["--x"]

    def test_percentage_value(self):
        docs = compile_docs(
            'service api { image: "x" disruption { min_available: 50% } }'
        )
        assert any(d.get("kind") == "PodDisruptionBudget" for d in docs)


class TestK8sErrorPaths:
    def test_service_no_image_no_build_raises(self):
        from infra.parser import ast_nodes as n
        from infra.backends.kubernetes import KubernetesBackend
        from infra.errors.exceptions import InfraCompileError

        svc = n.ServiceDef(name="x")
        try:
            KubernetesBackend().compile_service(svc)
            assert False, "expected InfraCompileError"
        except InfraCompileError:
            pass

    def test_env_value_non_literal_resolved(self):
        docs = compile_docs(
            'let APP_PORT = "8080"\nservice api { image: "x" env { P: APP_PORT } }'
        )
        c = _container(docs)
        assert c["env"][0]["value"] == "8080"


class TestEnvFromEnvField:
    def test_env_from_env(self):
        docs = compile_docs(
            'service api { image: "x" env { POD: from env "POD_NAME" } }'
        )
        c = _container(docs)
        assert c["env"][0]["valueFrom"]["fieldRef"]["fieldPath"] == "POD_NAME"

    def test_secret_from_file(self):
        docs = compile_docs('secret s { k: from file "f.txt" }')
        sec = get_kind(docs, "Secret")
        assert "k" in sec["data"]
