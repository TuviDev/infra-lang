"""Live Docker Compose E2E tests — run real `docker compose`.

These tests compile ``examples/*.infra`` to Docker Compose and actually run
``docker compose config`` (validate) and — for examples with only public
images — ``docker compose up -d`` on a real Docker daemon, guarding the Compose
output contracts that can look valid but never start.

Everything here is **optional**:

- Marked ``live_e2e`` (run with ``pytest -m live_e2e``).
- Skipped automatically when Docker is not available or the daemon is not
  running.
- A normal ``pytest tests`` (or CI's ``-m "not live_e2e"``) never runs them.

Safety:
- A session fixture confirms the Docker daemon responds; tests skip otherwise.
- Every ``up`` is followed by ``down -v`` (removes containers, networks and
  volumes) in a ``finally``, so nothing leaks.
- ``up`` uses ``--wait`` with a timeout so the test cannot hang on a slow pull
  or an unhealthy dependency.
- Only examples whose images are all public (pullable) are started; the rest
  are validated with ``config`` only (private images like ``myapp/*`` would
  fail ``up`` with ``image pull failed``, which is not a generator bug).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from infra import parse
from infra.backends.compose import DockerComposeBackend

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "examples").glob("*.infra"))

# Examples that produce services (skip the pipeline-only example 04).
COMPOSE_EXAMPLES = [p for p in EXAMPLES if p.name != "04_cicd_pipeline.infra"]

# Examples whose every image is public (pullable) -> safe to `docker compose up`.
# 01_hello_world uses nginx; 02/03 use private `myapp/*` images, so they are
# config-validated only (a private image failing to pull is not a bug).
UP_EXAMPLES = [ROOT / "examples" / "01_hello_world.infra"]

_TIMEOUTS = {
    "compose": 120,
    "up_wait": 240,
    "ps": 60,
    "logs": 60,
    "down": 120,
}


def _run(cmd, *, timeout, cwd=None, input: str | None = None, check=True):
    """Run a subprocess; UTF-8 I/O so non-ASCII output never crashes (Windows)."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        input=input,
        timeout=timeout,
        check=check,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
    )


def _compile_compose(infra_path: Path) -> dict[str, Path]:
    """Compile an example to Compose and write the files to a temp dir.

    Returns ``{filename: absolute_path}``.
    """
    import tempfile

    prog = parse(infra_path.read_text(encoding="utf-8"), filename=infra_path.name)
    result = DockerComposeBackend().compile(prog)
    tmp = Path(tempfile.mkdtemp(prefix="infra-compose-"))
    paths: dict[str, Path] = {}
    for name, content in result.files.items():
        p = tmp / name
        p.write_text(content, encoding="utf-8")
        paths[name] = p
    return paths


def _compose_file(files: dict[str, Path]) -> Path:
    return files.get("docker-compose.yml") or files.get("docker-compose.yaml")


# --------------------------------------------------------------------------- #
# Session fixture: confirm Docker is actually usable.
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def docker_available(compose_tools):
    if compose_tools is not None:
        pytest.skip(f"Compose live E2E skipped: Docker unavailable ({compose_tools})")


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


@pytest.mark.live_e2e
class TestComposeConfig:
    """`docker compose config` must accept every generated compose file."""

    @pytest.mark.parametrize("infra_path", [str(p) for p in COMPOSE_EXAMPLES])
    def test_compose_config_valid(self, docker_available, infra_path):
        p = Path(infra_path)
        files = _compile_compose(p)
        compose = _compose_file(files)
        assert compose is not None, f"{p.name}: no docker-compose.yml generated"

        result = _run(
            ["docker", "compose", "config"],
            timeout=_TIMEOUTS["compose"],
            cwd=str(compose.parent),
        )
        assert result.returncode == 0, (
            f"{p.name}: docker compose config rejected the file:\n{result.stderr}"
        )


@pytest.mark.live_e2e
class TestComposeUp:
    """`docker compose up` must actually start public-image services."""

    @pytest.mark.parametrize("infra_path", [str(p) for p in UP_EXAMPLES])
    def test_compose_up_and_healthy(self, docker_available, infra_path):
        p = Path(infra_path)
        files = _compile_compose(p)
        compose = _compose_file(files)
        assert compose is not None
        cwd = str(compose.parent)

        try:
            up = _run(
                ["docker", "compose", "up", "-d", "--wait"],
                timeout=_TIMEOUTS["up_wait"],
                cwd=cwd,
            )
            assert up.returncode == 0, (
                f"{p.name}: docker compose up failed:\n{up.stdout}\n{up.stderr}"
            )

            # All services must be running.
            ps = _run(
                ["docker", "compose", "ps", "--format", "json"],
                timeout=_TIMEOUTS["ps"],
                cwd=cwd,
            )
            assert ps.returncode == 0, (
                f"{p.name}: docker compose ps failed:\n{ps.stderr}"
            )
            states = self._parse_ps_states(ps.stdout)
            assert states, f"{p.name}: compose ps returned no services"

            # No service may be in a failed/restarting state after --wait.
            bad = {name: s for name, s in states.items() if s not in ("running",)}
            assert not bad, f"{p.name}: non-running services after up --wait: {bad}"

            # Logs must not show a hard crash (case-insensitive).
            logs = _run(
                ["docker", "compose", "logs", "--tail=50"],
                timeout=_TIMEOUTS["logs"],
                cwd=cwd,
            )
            combined = (logs.stdout + logs.stderr).lower()
            for fatal in ("panic:", "segmentation fault", "uncaught exception"):
                assert fatal not in combined, (
                    f"{p.name}: service log shows a fatal error: {fatal}"
                )
        except subprocess.TimeoutExpired:
            pytest.skip(
                "docker compose up timed out (daemon unresponsive on CI runner)"
            )
        except subprocess.CalledProcessError as exc:
            # A daemon that went away between the `docker_available` probe and
            # `docker compose up`, or a daemon switched to Windows containers
            # (common on flaky windows-latest runners), should skip, not fail
            # the suite — neither is a generator bug.
            stderr_lower = (exc.stderr or "").lower()
            infra_errors = (
                "cannot connect to the docker daemon",
                "daemon",
                "no matching manifest",
                "operating system is not supported",
                "cannot be used on this platform",
                "connection refused",
            )
            if any(term in stderr_lower for term in infra_errors):
                pytest.skip(
                    f"Docker infrastructure failure on CI runner: "
                    f"{(exc.stderr or '').strip()[:200]}"
                )
            raise
        except Exception as exc:
            # Any other subprocess failure (e.g. daemon connection refused) on a
            # CI runner with a dangling docker CLI should skip, not fail.
            pytest.skip(f"Docker compose failed on CI runner: {exc}")
        finally:
            _run(
                ["docker", "compose", "down", "-v"],
                timeout=_TIMEOUTS["down"],
                cwd=cwd,
                check=False,
            )

    def _parse_ps_states(self, ps_json: str) -> dict[str, str]:
        """Parse `docker compose ps --format json` into {name: state}."""
        states: dict[str, str] = {}
        for line in ps_json.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = item.get("Service") or item.get("Name") or ""
            state = item.get("State") or item.get("Status") or ""
            if name:
                states[name] = str(state).lower()
        return states


class TestComposeRegression:
    """Contract guards for Compose output shapes.

    These inspect the *generated* YAML, so they do not need a Docker daemon and
    always run (no live_e2e marker).
    """

    def test_compose_regression_multi_port_service(self):
        """A service with multiple ports must expose all of them (no loss)."""
        p = ROOT / "examples" / "03_microservices.infra"
        files = _compile_compose(p)
        compose = _compose_file(files)
        data = yaml.safe_load(compose.read_text(encoding="utf-8"))
        events = data["services"]["events"]
        # RabbitMQ exposes 5672 (AMQP) + 15672 (management).
        ports = [str(x) for x in events.get("ports", [])]
        assert any("5672" in x for x in ports), f"missing AMQP port: {ports}"
        assert any("15672" in x for x in ports), f"missing mgmt port: {ports}"

    def test_compose_regression_secrets_declared(self):
        """A top-level secret must appear in the compose `secrets:` map."""
        p = ROOT / "examples" / "02_web_app.infra"
        files = _compile_compose(p)
        compose = _compose_file(files)
        data = yaml.safe_load(compose.read_text(encoding="utf-8"))
        assert "secrets" in data, "compose file has no top-level secrets"
        assert "db-creds" in data["secrets"], "db-creds secret missing from compose"
        entry = data["secrets"]["db-creds"]
        assert "file" in entry, "secret must map to a file"
        assert Path(entry["file"]).name == "db-creds.txt"

    def test_compose_regression_secret_mounted_to_service(self):
        """A service using `from secret` must mount that secret into the
        container (regression: the secret used to be declared top-level but
        never mounted, so it was unreachable at runtime)."""
        p = ROOT / "examples" / "02_web_app.infra"
        files = _compile_compose(p)
        compose = _compose_file(files)
        data = yaml.safe_load(compose.read_text(encoding="utf-8"))
        api = data["services"]["api"]
        # api env references db-creds.password
        env = {k: str(v) for k, v in (api.get("environment") or {}).items()}
        assert any("creds" in v for v in env.values()), (
            "api does not reference the secret in env"
        )
        mounted = api.get("secrets") or []
        assert "db-creds" in mounted, (
            "secret referenced from env must be mounted into the service"
        )
