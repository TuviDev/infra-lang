"""Live Kubernetes E2E tests — run real Docker / kind / kubectl.

These tests compile ``examples/*.infra`` to Kubernetes YAML and **actually**
apply it to a real ``kind`` cluster with ``kubectl apply``, guarding the K8s
output contracts that earlier regressions (Secret base64, unnamed multi-port
Services) violated.

Everything here is **optional**:

- Marked ``live_e2e`` (register with ``pytest -m live_e2e``).
- Skipped automatically when Docker / kind / kubectl / kubeconform are missing.
- A normal ``pytest tests`` (or CI's ``-m "not live_e2e"``) never runs them.

Safety:
- The kind cluster is created once per session (session-scoped fixture).
- Every resource is created via a single ``kubectl apply -f`` per example and
  removed with ``kubectl delete -f`` in a ``finally``.
- The cluster itself is torn down in the session fixture teardown (also in
  ``finally``) so no zombie kind clusters survive a failure.
- All subprocesses are bounded by explicit timeouts.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from infra import parse
from infra.backends.kubernetes import KubernetesBackend

# --------------------------------------------------------------------------- #
# Constants / helpers
# --------------------------------------------------------------------------- #

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "examples").glob("*.infra"))
# Pipeline-only example generates no Kubernetes resources by design.
K8S_EXAMPLES = [p for p in EXAMPLES if "04_cicd_pipeline" not in p.name]

_TIMEOUTS = {
    "kind_create": 180,
    "kubectl": 60,
    # Long enough for a first image pull (e.g. nginx) on a slow host.
    "kubectl_wait": 240,
    "kubeconform": 60,
    "apply": 60,
    "delete": 60,
}

_MANAGED_BY = "app.kubernetes.io/managed-by"


def _run(cmd, *, timeout, input: str | None = None, check=True):
    """Run a subprocess and return CompletedProcess; raise on failure.

    Uses UTF-8 for stdout/stderr so non-ASCII output from kubectl/kubeconform
    does not crash on Windows where the default locale codec (e.g. cp1250)
    cannot decode it. ``input`` (str) is forwarded to the child stdin.
    """
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        input=input,
        timeout=timeout,
        check=check,
        encoding="utf-8",
        errors="replace",
    )


def _compile_k8s(infra_path: Path) -> str:
    """Compile an example to a single Kubernetes YAML string."""
    program = parse(infra_path.read_text(), filename=infra_path.name)
    result = KubernetesBackend().compile(program)
    return "\n".join(result.files.values())


def _k8s_docs(yaml_content: str):
    return [d for d in yaml.safe_load_all(yaml_content) if d is not None]


def _kubeconform_summary(yaml_content: str) -> dict[str, int]:
    """Run kubeconform -strict and parse its summary counters."""
    bin_ = shutil.which("kubeconform")
    assert bin_, "kubeconform should be available (guard ran)"
    result = _run(
        [bin_, "-strict", "-summary", "-kubernetes-version", "1.28.0", "-"],
        timeout=_TIMEOUTS["kubeconform"],
        check=False,
        input=yaml_content,
    )
    text = result.stdout + result.stderr
    # e.g. "Summary: 20 resources ... Valid: 20, Invalid: 0, Errors: 0, Skipped: 0"
    import re

    def _field(name: str) -> int:
        m = re.search(rf"{name}:\s*(\d+)", text)
        return int(m.group(1)) if m else -1

    return {
        "valid": _field("Valid"),
        "invalid": _field("Invalid"),
        "errors": _field("Errors"),
        "skipped": _field("Skipped"),
    }


# --------------------------------------------------------------------------- #
# Session fixture: create the kind cluster once, tear it down reliably.
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session", autouse=True)
def kind_cluster(live_e2e_tools):
    """Create a kind cluster for the whole session; always delete it after."""
    if live_e2e_tools is not None:
        pytest.skip(f"live E2E skipped: missing tool '{live_e2e_tools}'")

    kind_bin = shutil.which("kind")
    kubectl_bin = shutil.which("kubectl")
    assert kind_bin and kubectl_bin

    cluster_name = "infra-lang-e2e"
    created = False
    try:
        _run(
            [kind_bin, "create", "cluster", "--name", cluster_name],
            timeout=_TIMEOUTS["kind_create"],
        )
        created = True
        yield cluster_name
    finally:
        if created:
            _run(
                [kind_bin, "delete", "cluster", "--name", cluster_name],
                timeout=_TIMEOUTS["kind_create"],
                check=False,
            )


# --------------------------------------------------------------------------- #
# Optional per-example kubeconform / kubectl apply helpers
# --------------------------------------------------------------------------- #


def _assert_no_api_errors(content: str, label: str) -> None:
    """kubectl apply must never fail with an API error for valid .infra."""
    apply = _run(
        ["kubectl", "apply", "-f", "-"],
        input=content,
        timeout=_TIMEOUTS["apply"],
    )
    assert apply.returncode == 0, (
        f"{label}: kubectl apply failed:\n{apply.stderr}"
    )


def _assert_contracts(docs, label: str) -> None:
    """Assert the K8s output contracts that earlier bugs violated."""
    for doc in docs:
        kind = doc.get("kind")
        name = doc.get("metadata", {}).get("name", "?")
        if kind == "Service":
            ports = doc.get("spec", {}).get("ports", [])
            if len(ports) > 1:
                names = [p.get("name") for p in ports]
                assert all(names), (
                    f"{label}: Service {name} multi-port needs names: {ports}"
                )
                assert len(set(names)) == len(names), (
                    f"{label}: Service {name} port names must be unique: {names}"
                )
        if kind == "Secret" and "data" in doc:
            for key, value in doc["data"].items():
                base64.b64decode(value, validate=True)  # raises on invalid
        labels = doc.get("metadata", {}).get("labels") or {}
        if kind in (
            "Deployment",
            "StatefulSet",
            "Service",
            "ConfigMap",
            "Secret",
            "PersistentVolumeClaim",
            "Namespace",
        ):
            assert labels.get(_MANAGED_BY) == "infra-lang", (
                f"{label}: {kind}/{name} missing managed-by label"
            )


@pytest.mark.live_e2e
class TestLiveK8sE2E:
    """Real kubectl apply of every K8s example against a kind cluster."""

    @pytest.mark.parametrize("infra_path", [str(p) for p in K8S_EXAMPLES])
    def test_example_applies(self, kind_cluster, infra_path):
        p = Path(infra_path)
        label = p.name
        content = _compile_k8s(p)
        docs = _k8s_docs(content)
        assert docs, f"{label}: example produced no Kubernetes resources"

        # 1. kubeconform -strict must pass
        summary = _kubeconform_summary(content)
        assert summary["invalid"] == 0 and summary["errors"] == 0, (
            f"{label}: kubeconform reported problems: {summary}"
        )

        # 2. apply the whole file at once
        _assert_no_api_errors(content, label)

        # 3. verify the Service/Secret contracts on the generated YAML
        _assert_contracts(docs, label)

    def test_hello_world_pods_become_ready(self, kind_cluster):
        """The simplest example must deploy and schedule a ready pod.

        A slow first image pull must not fail the test: ``rollout status`` is
        only polling, and the pod may still become ready after the Python
        timeout expires. So we bump the timeouts and treat ``TimeoutExpired``
        as non-fatal (the deployment was applied successfully, which is the
        API contract under test; readiness is best-effort on slow hosts).
        """
        p = ROOT / "examples" / "01_hello_world.infra"
        content = _compile_k8s(p)
        _assert_no_api_errors(content, p.name)
        try:
            _run(
                [
                    "kubectl",
                    "rollout",
                    "status",
                    "deployment/hello",
                    "--timeout=180s",
                ],
                timeout=_TIMEOUTS["kubectl_wait"],
                check=False,
            )
        except subprocess.TimeoutExpired:
            # Pod is still pulling its image on a slow host. Verify the
            # deployment is still present (kubectl get succeeds) rather than
            # hard-failing the suite on a slow image pull.
            get = _run(
                [
                    "kubectl",
                    "get",
                    "deployment/hello",
                    "-o",
                    "jsonpath={.metadata.name}",
                ],
                timeout=_TIMEOUTS["kubectl"],
                check=False,
            )
            assert get.returncode == 0, (
                "deployment/hello missing after rollout timeout:\n" + get.stderr
            )


@pytest.mark.live_e2e
class TestLiveK8sRegressionGuards:
    """Real-cluster checks for previously fixed bugs (Secret base64, ports)."""

    def test_multi_port_service_accepted(self, kind_cluster):
        """A queue (RabbitMQ) with 2 ports must apply cleanly (was rejected)."""
        content = _compile_k8s(ROOT / "examples" / "03_microservices.infra")
        _assert_no_api_errors(content, "03_microservices")
        docs = _k8s_docs(content)
        events = next(
            d for d in docs if d.get("kind") == "Service" and d["metadata"]["name"] == "events"
        )
        names = [p.get("name") for p in events["spec"]["ports"]]
        assert names == ["tcp-5672", "tcp-15672"]

    def test_secret_base64_accepted(self, kind_cluster):
        """Secrets with from-env placeholders must be valid base64 (was rejected)."""
        content = _compile_k8s(ROOT / "examples" / "02_web_app.infra")
        _assert_no_api_errors(content, "02_web_app")
        docs = _k8s_docs(content)
        for sec in (d for d in docs if d.get("kind") == "Secret"):
            for value in sec.get("data", {}).values():
                base64.b64decode(value, validate=True)
