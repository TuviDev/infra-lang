"""Shared fixtures for the Infra Lang test suite.

These fixtures reduce duplication across behavioral and unit tests: they turn
repeated "parse -> find node / compile -> load YAML / assert error code"
boilerplate into one-liners, and keep the tests readable as documentation.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def parse_service():
    """Return the first :class:`ServiceDef` from ``source``."""

    def _parse(source):
        from infra import parse
        from infra.parser.ast_nodes import ServiceDef

        prog = parse(source)
        return next(s for s in prog.statements if isinstance(s, ServiceDef))

    return _parse


@pytest.fixture
def k8s_docs():
    """Compile ``source`` with the Kubernetes backend and return its YAML docs."""

    def _docs(source):
        import yaml

        from infra import parse
        from infra.backends.kubernetes import KubernetesBackend

        result = KubernetesBackend().compile(parse(source))
        content = "\n".join(result.files.values())
        return [d for d in yaml.safe_load_all(content) if d is not None]

    return _docs


@pytest.fixture
def assert_error():
    """Assert that validating ``source`` yields a semantic error with ``code``."""

    def _assert(source, code):
        from infra import parse, validate

        result = validate(parse(source))
        found = [e for e in result.errors if e.code == code]
        assert found, f"Expected {code}, got: {[e.code for e in result.errors]}"
        return found[0]

    return _assert


@pytest.fixture
def assert_warning():
    """Assert that validating ``source`` yields a warning with ``code``."""

    def _assert(source, code):
        from infra import parse, validate

        result = validate(parse(source))
        found = [w for w in result.warnings if w.code == code]
        assert found, f"Expected {code}, got: {[w.code for w in result.warnings]}"
        return found[0]

    return _assert


@pytest.fixture
def infra_file(tmp_path):
    """Write ``content`` to a temporary ``.infra`` file (default name t.infra)."""

    def _create(content, name="t.infra"):
        f = tmp_path / name
        f.write_text(content)
        return f

    return _create


# --------------------------------------------------------------------------- #
# Live E2E tool availability (shared with tests/test_live_e2e.py)
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def live_e2e_tools() -> str | None:
    """Return the name of the first missing live-E2E tool, or None if ready.

    Cached for the whole session. Tests should ``pytest.skip`` when this
    returns a tool name.
    """
    from tests.tools import require_tools

    return require_tools(("docker", "kind", "kubectl", "kubeconform"))


@pytest.fixture(scope="session")
def compose_tools() -> str | None:
    """Return a skip reason if Compose live E2E cannot run, else None.

    Docker Compose live E2E only needs the Docker daemon (not kind/kubectl),
    so it is gated on Docker alone — plus the daemon must run **linux**
    containers. GitHub's windows runners expose a running daemon switched to
    Windows containers (`docker info` succeeds, OSType == "windows"); linux
    images like nginx cannot start there, so the suite must skip, not fail.
    """
    from tests.tools import docker_daemon_os, require_tools

    missing = require_tools(("docker",))
    if missing is not None:
        return missing
    os_type = docker_daemon_os()
    if os_type != "linux":
        return f"docker daemon runs {os_type or 'unknown'} containers, need linux"
    return None
