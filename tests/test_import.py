"""Tests for `infra import` — reverse-compiling Kubernetes YAML to Infra."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from infra.cli.main import app
from infra.importer import import_kubernetes

runner = CliRunner()


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def import_text(tmp_path: Path, content: str, name: str = "m.yaml") -> str:
    return import_kubernetes(write(tmp_path / name, content))


DEPLOYMENT = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
spec:
  replicas: 3
  selector:
    matchLabels:
      app: api
  template:
    metadata:
      labels:
        app: api
    spec:
      containers:
        - name: api
          image: nginx:1.25
          ports:
            - containerPort: 8080
"""


class TestDeploymentToService:
    def test_name_image_replicas(self, tmp_path):
        out = import_text(tmp_path, DEPLOYMENT)
        assert "service api {" in out
        assert 'image: "nginx:1.25"' in out
        assert "replicas: 3" in out

    def test_env_vars(self, tmp_path):
        yaml = DEPLOYMENT.replace(
            "image: nginx:1.25",
            'image: nginx:1.25\n          env:\n            - name: NODE_ENV\n'
            '              value: production',
        )
        out = import_text(tmp_path, yaml)
        assert "env {" in out
        assert 'NODE_ENV: "production"' in out

    def test_resource_limits(self, tmp_path):
        yaml = DEPLOYMENT.replace(
            "image: nginx:1.25",
            'image: nginx:1.25\n          resources:\n            requests:\n'
            '              cpu: 100m\n              memory: 256Mi\n'
            '            limits:\n              cpu: 500m\n              memory: 512Mi',
        )
        out = import_text(tmp_path, yaml)
        assert "resources {" in out
        assert "cpu: 100m" in out
        assert "memory: 512Mi" in out

    def test_health_probe(self, tmp_path):
        yaml = DEPLOYMENT.replace(
            "image: nginx:1.25",
            'image: nginx:1.25\n          readinessProbe:\n            httpGet:\n'
            '              path: /health\n              port: 8080\n'
            '            periodSeconds: 10',
        )
        out = import_text(tmp_path, yaml)
        assert "probes {" in out or "health http" in out
        assert "/health" in out

    def test_env_from_secret(self, tmp_path):
        yaml = DEPLOYMENT.replace(
            "image: nginx:1.25",
            'image: nginx:1.25\n          env:\n            - name: DB_PASS\n'
            '              valueFrom:\n                secretKeyRef:\n'
            '                  name: db-secret\n                  key: password',
        )
        out = import_text(tmp_path, yaml)
        assert 'from secret "db-secret".password' in out


class TestServiceMatching:
    def test_service_ports(self, tmp_path):
        yaml = """\
apiVersion: v1
kind: Service
metadata:
  name: api
spec:
  selector:
    app: api
  ports:
    - port: 80
      targetPort: 8080
      protocol: TCP
"""
        out = import_text(tmp_path, yaml)
        assert "service api {" in out
        assert "port 80" in out

    def test_deployment_service_merged(self, tmp_path):
        yaml = DEPLOYMENT + """\
---
apiVersion: v1
kind: Service
metadata:
  name: api
spec:
  selector:
    app: api
  ports:
    - port: 80
      targetPort: 8080
      protocol: TCP
"""
        out = import_text(tmp_path, yaml)
        # one service block that contains both the image and the service port
        assert "service api {" in out
        assert 'image: "nginx:1.25"' in out
        assert "port 80" in out

    def test_orphan_service_exposes_ports(self, tmp_path):
        yaml = """\
apiVersion: v1
kind: Service
metadata:
  name: orphan
spec:
  selector:
    app: nowhere
  ports:
    - port: 9090
      protocol: TCP
"""
        out = import_text(tmp_path, yaml)
        assert "service orphan {" in out
        assert "port 9090" in out


class TestStatefulSet:
    def test_postgres_to_database(self, tmp_path):
        yaml = """\
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
spec:
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      containers:
        - name: postgres
          image: postgres:15.4
          ports:
            - containerPort: 5432
"""
        out = import_text(tmp_path, yaml)
        assert "database postgres {" in out
        assert "type: postgres" in out

    def test_redis_to_cache(self, tmp_path):
        yaml = """\
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: session
spec:
  selector:
    matchLabels:
      app: session
  template:
    metadata:
      labels:
        app: session
    spec:
      containers:
        - name: redis
          image: redis:7
"""
        out = import_text(tmp_path, yaml)
        assert "cache session {" in out
        assert "type: redis" in out

    def test_statefulset_service_with_volumes(self, tmp_path):
        yaml = """\
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: worker
spec:
  selector:
    matchLabels:
      app: worker
  template:
    metadata:
      labels:
        app: worker
    spec:
      containers:
        - name: worker
          image: myworker:1.0
          volumeMounts:
            - name: scratch
              mountPath: /scratch
      volumes:
        - name: scratch
          emptyDir: {}
"""
        out = import_text(tmp_path, yaml)
        assert "service worker {" in out
        assert "volumes [" in out
        assert 'name: "scratch"' in out


class TestSimpleResources:
    def test_secret_block(self, tmp_path):
        yaml = """\
apiVersion: v1
kind: Secret
metadata:
  name: db-creds
type: Opaque
stringData:
  password: s3cr3t
"""
        out = import_text(tmp_path, yaml)
        assert "secret db-creds {" in out
        assert 'password: "s3cr3t"' in out

    def test_configmap_block(self, tmp_path):
        yaml = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: app-config
data:
  LOG_LEVEL: info
"""
        out = import_text(tmp_path, yaml)
        assert "config app-config {" in out
        assert 'LOG_LEVEL: "info"' in out


class TestIngress:
    def test_ingress_attached_to_service(self, tmp_path):
        yaml = """\
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: frontend-ing
spec:
  rules:
    - host: app.example.com
      http:
        paths:
          - path: /
            backend:
              service:
                name: frontend
                port:
                  number: 3000
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: frontend
spec:
  selector:
    matchLabels:
      app: frontend
  template:
    metadata:
      labels:
        app: frontend
    spec:
      containers:
        - name: frontend
          image: frontend:2.0
          ports:
            - containerPort: 3000
"""
        out = import_text(tmp_path, yaml)
        assert "ingress {" in out
        assert 'host: "app.example.com"' in out


class TestMultiDocAndRobustness:
    def test_multidoc_three_resources(self, tmp_path):
        yaml = DEPLOYMENT + """\
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: cfg
data:
  A: "1"
---
apiVersion: v1
kind: Secret
metadata:
  name: sec
type: Opaque
stringData:
  B: x
"""
        out = import_text(tmp_path, yaml)
        assert "service api {" in out
        assert "config cfg {" in out
        assert "secret sec {" in out

    def test_empty_yaml_no_crash(self, tmp_path):
        out = import_text(tmp_path, "")
        assert out  # a header comment is produced, no crash

    def test_invalid_yaml_raises(self, tmp_path):
        p = write(tmp_path / "bad.yaml", "a: b: : :\n  - [unclosed\n")
        with pytest.raises(Exception):
            import_kubernetes(p)

    def test_unknown_kind_ignored(self, tmp_path):
        yaml = """\
apiVersion: v1
kind: Role
metadata:
  name: some-role
rules: []
"""
        out = import_text(tmp_path, yaml)
        assert "some-role" not in out

    def test_name_with_dots_sanitized(self, tmp_path):
        yaml = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my.app.service
spec:
  selector:
    matchLabels:
      app: x
  template:
    metadata:
      labels:
        app: x
    spec:
      containers:
        - name: c
          image: nginx:1
"""
        out = import_text(tmp_path, yaml)
        assert "service my-app-service {" in out

    def test_import_output_is_valid_infra(self, tmp_path):
        """Imported output must itself parse cleanly (round-trip baseline)."""
        from infra.parser import parse

        multi = DEPLOYMENT + "\n---\n" + DEPLOYMENT.replace("api", "api2")
        out = import_text(tmp_path, multi)
        program = parse(out)
        assert program is not None


class TestCLI:
    def test_import_to_stdout(self, tmp_path):
        p = write(tmp_path / "d.yaml", DEPLOYMENT)
        result = runner.invoke(app, ["import", str(p)])
        assert result.exit_code == 0
        assert "service api {" in result.stdout

    def test_import_output_file(self, tmp_path):
        src = write(tmp_path / "d.yaml", DEPLOYMENT)
        out = tmp_path / "out.infra"
        result = runner.invoke(app, ["import", str(src), "--output", str(out)])
        assert result.exit_code == 0
        assert out.exists()
        assert "service api {" in out.read_text(encoding="utf-8")

    def test_import_directory(self, tmp_path):
        d = tmp_path / "manifests"
        write(d / "a.yaml", DEPLOYMENT)
        write(
            d / "b.yaml",
            """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: b
data:
  X: y
""",
        )
        result = runner.invoke(app, ["import", str(d)])
        assert result.exit_code == 0
        assert "service api {" in result.stdout
        assert "config b {" in result.stdout

    def test_import_invalid_yaml_cli_error(self, tmp_path):
        p = write(tmp_path / "bad.yaml", "a: b: :\n  - [x\n")
        result = runner.invoke(app, ["import", str(p)])
        assert result.exit_code != 0
        output = (result.stdout or "") + (result.stderr or "")
        assert "error" in output.lower()

    def test_import_command_registered(self):
        result = runner.invoke(app, ["--help"])
        assert "import" in result.stdout


class TestEdgeBranches:
    def test_env_from_configmap(self, tmp_path):
        yaml = DEPLOYMENT.replace(
            "image: nginx:1.25",
            'image: nginx:1.25\n          env:\n            - name: LOG_LEVEL\n'
            '              valueFrom:\n                configMapKeyRef:\n'
            '                  name: app-cfg\n                  key: LEVEL',
        )
        out = import_text(tmp_path, yaml)
        assert 'from config "app-cfg".LEVEL' in out

    def test_env_from_field(self, tmp_path):
        yaml = DEPLOYMENT.replace(
            "image: nginx:1.25",
            'image: nginx:1.25\n          env:\n            - name: POD_NAME\n'
            '              valueFrom:\n                fieldRef:\n'
            '                  fieldPath: metadata.name',
        )
        out = import_text(tmp_path, yaml)
        assert 'from field "metadata.name"' in out

    def test_udp_port_emits_protocol(self, tmp_path):
        yaml = """\
apiVersion: v1
kind: Service
metadata:
  name: dns
spec:
  selector:
    app: dns
  ports:
    - port: 53
      protocol: UDP
"""
        out = import_text(tmp_path, yaml)
        assert "port 53 { protocol: \"UDP\" }" in out

    def test_tcp_and_grpc_probes(self, tmp_path):
        yaml = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: grpc
spec:
  selector:
    matchLabels:
      app: grpc
  template:
    metadata:
      labels:
        app: grpc
    spec:
      containers:
        - name: grpc
          image: grpcsrv:1
          livenessProbe:
            tcpSocket:
              port: 50051
          readinessProbe:
            grpc:
              port: 50051
"""
        out = import_text(tmp_path, yaml)
        assert "probes {" in out
        assert "liveness tcp(50051)" in out
        assert "readiness grpc(50051)" in out

    def test_exec_probe(self, tmp_path):
        yaml = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: job
spec:
  selector:
    matchLabels:
      app: job
  template:
    metadata:
      labels:
        app: job
    spec:
      containers:
        - name: job
          image: job:1
          livenessProbe:
            exec:
              command:
                - cat
                - /tmp/ok
"""
        out = import_text(tmp_path, yaml)
        assert 'exec(["cat", "/tmp/ok"])' in out

    def test_statefulset_mysql_database(self, tmp_path):
        yaml = """\
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: shop-db
spec:
  selector:
    matchLabels:
      app: shop-db
  template:
    metadata:
      labels:
        app: shop-db
    spec:
      containers:
        - name: mysql
          image: mysql:8
"""
        out = import_text(tmp_path, yaml)
        assert "database shop-db {" in out
        assert "type: mysql" in out

    def test_statefulset_mongo_database(self, tmp_path):
        yaml = """\
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: docs
spec:
  selector:
    matchLabels:
      app: docs
  template:
    metadata:
      labels:
        app: docs
    spec:
      containers:
        - name: mongo
          image: mongo:6
"""
        out = import_text(tmp_path, yaml)
        assert "database docs {" in out
        assert "type: mongo" in out

    def test_volume_with_claim(self, tmp_path):
        yaml = """\
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: data
spec:
  selector:
    matchLabels:
      app: data
  template:
    metadata:
      labels:
        app: data
    spec:
      containers:
        - name: data
          image: notadb:1
          volumeMounts:
            - name: data
              mountPath: /data
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: data-pvc
"""
        out = import_text(tmp_path, yaml)
        assert "volumes [" in out
        assert 'claim: "data-pvc"' in out

    def test_empty_secret_and_config_blocks(self, tmp_path):
        yaml = """\
apiVersion: v1
kind: Secret
metadata:
  name: empty-sec
type: Opaque
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: empty-cfg
"""
        out = import_text(tmp_path, yaml)
        assert "secret empty-sec {" in out
        assert "config empty-cfg {" in out

    def test_service_with_no_ports_comment(self, tmp_path):
        yaml = """\
apiVersion: v1
kind: Service
metadata:
  name: bare
spec:
  selector:
    app: x
"""
        out = import_text(tmp_path, yaml)
        assert "service bare {" in out

    def test_directory_with_no_yaml_errors(self, tmp_path):
        d = tmp_path / "empty-dir"
        d.mkdir()
        with pytest.raises(Exception):
            import_kubernetes(d)

    def test_unreadable_file_errors(self, tmp_path):
        p = tmp_path / "dir-as-file.yaml"
        p.mkdir()  # a directory pretending to be a file
        with pytest.raises(Exception):
            import_kubernetes(p)

    def test_sanitize_edge_cases(self, tmp_path):
        yaml = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: 2fa
spec:
  selector:
    matchLabels:
      app: x
  template:
    metadata:
      labels:
        app: x
    spec:
      containers:
        - name: c
          image: nginx:1
"""
        out = import_text(tmp_path, yaml)
        assert "service res-2fa {" in out


class TestMoreBranches:
    def test_secret_data_base64_uses_from_env(self, tmp_path):
        yaml = """\
apiVersion: v1
kind: Secret
metadata:
  name: bin
type: Opaque
data:
  token: MTIzNDU2
"""
        out = import_text(tmp_path, yaml)
        assert "secret bin {" in out
        assert 'from env "TOKEN"' in out

    def test_no_supported_resources(self, tmp_path):
        yaml = """\
apiVersion: v1
kind: NetworkPolicy
metadata:
  name: np
spec:
  podSelector: {}
"""
        out = import_text(tmp_path, yaml)
        assert "no supported Kubernetes resources" in out

    def test_ingress_name_fallback_match(self, tmp_path):
        # Ingress with no explicit backend service; matches by its own name.
        yaml = """\
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: frontend
spec:
  rules:
    - host: fe.example.com
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: frontend
spec:
  selector:
    matchLabels:
      app: frontend
  template:
    metadata:
      labels:
        app: frontend
    spec:
      containers:
        - name: c
          image: fe:1
"""
        out = import_text(tmp_path, yaml)
        assert "ingress {" in out
        assert 'host: "fe.example.com"' in out

    def test_volume_host_path(self, tmp_path):
        yaml = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: hp
spec:
  selector:
    matchLabels:
      app: hp
  template:
    metadata:
      labels:
        app: hp
    spec:
      containers:
        - name: c
          image: nginx:1
          volumeMounts:
            - name: logs
              mountPath: /var/log
      volumes:
        - name: logs
          hostPath:
            path: /var/log/hp
"""
        out = import_text(tmp_path, yaml)
        assert "volumes [" in out
        assert 'name: "logs"' in out

    def test_sanitize_leading_dash(self, tmp_path):
        from infra.importer.k8s import sanitize_name

        assert sanitize_name("-foo").startswith("res")
        assert sanitize_name("") == "resource"
        assert sanitize_name("123abc") == "res-123abc"

    def test_statefulset_db_multi_replica(self, tmp_path):
        yaml = """\
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: pg
spec:
  replicas: 3
  selector:
    matchLabels:
      app: pg
  template:
    metadata:
      labels:
        app: pg
    spec:
      containers:
        - name: pg
          image: postgres:15
"""
        out = import_text(tmp_path, yaml)
        assert "database pg {" in out
        assert "replicas: 3" in out


class TestImporterHelpers:
    def test_import_kubernetes_from_docs(self, tmp_path):
        from infra.importer import import_kubernetes_from_docs

        docs = [{"kind": "Deployment", "metadata": {"name": "api"}, "spec": {
            "replicas": 2,
            "selector": {"matchLabels": {"app": "api"}},
            "template": {"metadata": {"labels": {"app": "api"}},
                         "spec": {"containers": [{"name": "api", "image": "nginx:1"}]}},
        }}]
        out = import_kubernetes_from_docs(docs)
        assert "service api {" in out
        assert "replicas: 2" in out

    def test_import_kubernetes_file_helper(self, tmp_path):
        from infra.importer import import_kubernetes_file

        p = write(tmp_path / "single.yaml", DEPLOYMENT)
        out = import_kubernetes_file(p)
        assert "service api {" in out


class TestRoundTrip:
    def test_import_compile_round_trip(self, tmp_path):
        """Import -> compile must preserve key fields (image, replicas, port)."""
        src = write(tmp_path / "app.yaml", DEPLOYMENT + """\
---
apiVersion: v1
kind: Service
metadata:
  name: api
spec:
  selector:
    app: api
  ports:
    - port: 80
      targetPort: 8080
      protocol: TCP
""")
        infra_file = tmp_path / "app.infra"
        result = runner.invoke(app, ["import", str(src), "--output", str(infra_file)])
        assert result.exit_code == 0

        outdir = tmp_path / "out"
        comp = runner.invoke(app, ["compile", str(infra_file), "--output", str(outdir)])
        assert comp.exit_code == 0, comp.stdout

        compiled = (outdir / "infra.yaml").read_text(encoding="utf-8")
        assert "nginx:1.25" in compiled  # image
        assert "replicas: 3" in compiled  # replicas
        assert "80" in compiled  # service port


class TestImportEdgeCases:
    def test_service_with_no_fields(self, tmp_path):
        yaml = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: empty
spec:
  selector:
    matchLabels: {app: empty}
  template:
    metadata:
      labels: {app: empty}
    spec:
      containers:
        - name: empty
"""
        out = import_text(tmp_path, yaml)
        assert "service empty {" in out
        assert "no runnable fields" in out

    def test_statefulset_db_multireplica(self, tmp_path):
        yaml = """\
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: pg
spec:
  replicas: 3
  selector:
    matchLabels: {app: pg}
  template:
    metadata:
      labels: {app: pg}
    spec:
      containers:
        - name: pg
          image: postgres:15
"""
        out = import_text(tmp_path, yaml)
        assert "database pg {" in out
        assert "replicas: 3" in out

    def test_ingress_non_dict_rule_skipped(self, tmp_path):
        # ingress rule that is not a dict -> gracefully skipped
        yaml = """\
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ing
spec:
  rules:
    - bad_rule
"""
        out = import_text(tmp_path, yaml)
        assert out  # no crash
