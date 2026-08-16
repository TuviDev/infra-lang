"""Live Helm E2E — really runs `helm lint --strict` and `helm template` on
generated charts for every example.

Marked ``live_e2e`` and skipped when the helm binary is missing. Requires only
the helm CLI (no cluster needed): ``helm lint`` validates the chart and
``helm template`` renders it to Kubernetes YAML without installing.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from infra import parse
from infra.backends import get_backend

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "examples").glob("*.infra"))

_HELM = shutil.which("helm") or "/tmp/linux-amd64/helm"


def _helm_available() -> bool:
    return shutil.which("helm") is not None or Path("/tmp/linux-amd64/helm").exists()


@pytest.fixture(scope="module")
def helm_available():
    if not _helm_available():
        pytest.skip("helm not installed; live Helm E2E skipped")


def _write_chart(tmpdir: str, files: dict[str, str]) -> Path:
    chartdir = None
    for path, content in files.items():
        fp = Path(tmpdir) / path
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        if "Chart.yaml" in path:
            chartdir = fp.parent
    assert chartdir is not None
    return chartdir


def _run(*args, cwd=None):
    return subprocess.run(
        [_HELM, *args], capture_output=True, text=True, timeout=120, cwd=cwd
    )


@pytest.mark.live_e2e
class TestLiveHelmE2E:
    @pytest.mark.parametrize("infra_path", [str(p) for p in EXAMPLES])
    def test_helm_lint_strict(self, helm_available, infra_path, tmp_path):
        p = Path(infra_path)
        prog = parse(p.read_text(encoding="utf-8"), filename=p.name)
        files = get_backend("helm").compile(prog).files
        chartdir = _write_chart(str(tmp_path), files)
        r = _run("lint", "--strict", str(chartdir))
        assert r.returncode == 0, (
            f"{p.name}: helm lint --strict failed:\n{r.stdout}\n{r.stderr}"
        )

    @pytest.mark.parametrize("infra_path", [str(p) for p in EXAMPLES])
    def test_helm_template_renders(self, helm_available, infra_path, tmp_path):
        p = Path(infra_path)
        prog = parse(p.read_text(encoding="utf-8"), filename=p.name)
        files = get_backend("helm").compile(prog).files
        chartdir = _write_chart(str(tmp_path), files)
        r = _run("template", "rel", str(chartdir))
        assert r.returncode == 0, (
            f"{p.name}: helm template failed:\n{r.stderr}"
        )

    def test_chart_render_has_expected_kinds(self, helm_available, tmp_path):
        p = ROOT / "examples" / "02_web_app.infra"
        prog = parse(p.read_text(encoding="utf-8"), filename=p.name)
        files = get_backend("helm").compile(prog).files
        chartdir = _write_chart(str(tmp_path), files)
        r = _run("template", "rel", str(chartdir))
        assert "kind: Deployment" in r.stdout
        assert "kind: Service" in r.stdout
        assert "kind: StatefulSet" in r.stdout
