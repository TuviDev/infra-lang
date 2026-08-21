"""Tests for `infra doctor --check-drift` on-disk drift detection."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from infra.analyzer.drift import detect_drift
from infra.cli.main import app
from infra.parser import parse_file

runner = CliRunner()

SERVICE = 'service api { image: "nginx:1.25" port 80 replicas: 2 }'


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def compile_to(infra_file: Path, out_dir: Path, target: str = "kubernetes") -> None:
    """Compile an .infra file to out_dir using the real backend."""
    program = parse_file(infra_file)
    from infra.backends import get_backend

    backend = get_backend(target)
    for name, content in backend.compile(program).files.items():
        dest = out_dir / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")


class TestDetectDrift:
    def test_no_drift(self, tmp_path):
        src = write(tmp_path / "app.infra", SERVICE)
        out = tmp_path / "out"
        compile_to(src, out)
        result = detect_drift(src, out)
        assert result.clean
        assert result.has_drift is False
        assert result.modified_files == []
        assert result.missing_files == []

    def test_detects_modified_line(self, tmp_path):
        src = write(tmp_path / "app.infra", SERVICE)
        out = tmp_path / "out"
        compile_to(src, out)
        # edit one line in the generated YAML
        target = out / "infra.yaml"
        content = target.read_text(encoding="utf-8").replace("replicas: 2", "replicas: 9")
        target.write_text(content, encoding="utf-8")
        result = detect_drift(src, out)
        assert result.has_drift
        assert len(result.modified_files) == 1
        name, diff = result.modified_files[0]
        assert name == "infra.yaml"
        assert "replicas: 9" in diff

    def test_detects_extra_whitespace(self, tmp_path):
        src = write(tmp_path / "app.infra", SERVICE)
        out = tmp_path / "out"
        compile_to(src, out)
        target = out / "infra.yaml"
        target.write_text(target.read_text(encoding="utf-8") + "  \n", encoding="utf-8")
        result = detect_drift(src, out)
        assert result.has_drift

    def test_missing_file(self, tmp_path):
        src = write(tmp_path / "app.infra", SERVICE)
        out = tmp_path / "out"
        compile_to(src, out)
        (out / "infra.yaml").unlink()
        result = detect_drift(src, out)
        assert result.has_drift
        assert result.missing_files == ["infra.yaml"]

    def test_empty_out_dir_all_missing(self, tmp_path):
        src = write(tmp_path / "app.infra", SERVICE)
        out = tmp_path / "empty-out"
        out.mkdir()
        result = detect_drift(src, out)
        assert result.has_drift
        assert result.missing_files  # at least one expected file missing

    def test_multi_file_target_terraform(self, tmp_path):
        src = write(tmp_path / "app.infra", SERVICE)
        out = tmp_path / "out"
        compile_to(src, out, target="terraform")
        # terraform emits many .tf files
        result = detect_drift(src, out, target="terraform")
        assert result.clean

    def test_missing_one_terraform_file(self, tmp_path):
        src = write(tmp_path / "app.infra", SERVICE)
        out = tmp_path / "out"
        compile_to(src, out, target="terraform")
        (out / "main.tf").unlink()
        result = detect_drift(src, out, target="terraform")
        assert result.has_drift
        assert "main.tf" in result.missing_files


class TestDriftErrors:
    def test_parse_error_propagates(self, tmp_path):
        src = write(tmp_path / "bad.infra", 'service api { image: }')
        out = tmp_path / "out"
        from infra.errors.exceptions import InfraParseError

        with pytest.raises(InfraParseError):
            detect_drift(src, out)

    def test_unknown_backend_raises(self, tmp_path):
        src = write(tmp_path / "app.infra", SERVICE)
        from infra.errors.exceptions import InfraCompileError

        with pytest.raises(InfraCompileError):
            detect_drift(src, tmp_path / "out", target="nonexistent")

    def test_nonexistent_source_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            detect_drift(tmp_path / "missing.infra", tmp_path / "out")


class TestCLIDrift:
    def test_cli_no_drift_exit_zero(self, tmp_path):
        src = write(tmp_path / "app.infra", SERVICE)
        out = tmp_path / "out"
        compile_to(src, out)
        result = runner.invoke(
            app, ["doctor", "--check-drift", str(src), "--out-dir", str(out)]
        )
        assert result.exit_code == 0
        assert "No drift detected" in result.stdout

    def test_cli_drift_exit_one(self, tmp_path):
        src = write(tmp_path / "app.infra", SERVICE)
        out = tmp_path / "out"
        compile_to(src, out)
        target = out / "infra.yaml"
        target.write_text(
            target.read_text(encoding="utf-8").replace("replicas: 2", "replicas: 4"),
            encoding="utf-8",
        )
        result = runner.invoke(
            app, ["doctor", "--check-drift", str(src), "--out-dir", str(out)]
        )
        assert result.exit_code == 1
        assert "Files differ" in result.stdout

    def test_cli_missing_output_exit_one(self, tmp_path):
        src = write(tmp_path / "app.infra", SERVICE)
        result = runner.invoke(
            app,
            ["doctor", "--check-drift", str(src), "--out-dir", str(tmp_path / "nope")],
        )
        assert result.exit_code == 1
        assert "Missing generated files" in result.stdout

    def test_cli_target_compose(self, tmp_path):
        src = write(tmp_path / "app.infra", SERVICE)
        out = tmp_path / "out"
        compile_to(src, out, target="compose")
        result = runner.invoke(
            app,
            [
                "doctor",
                "--check-drift",
                str(src),
                "--out-dir",
                str(out),
                "--target",
                "compose",
            ],
        )
        assert result.exit_code == 0

    def test_cli_bad_source_exit_one(self, tmp_path):
        src = write(tmp_path / "bad.infra", "not valid !!!")
        result = runner.invoke(
            app, ["doctor", "--check-drift", str(src), "--out-dir", str(tmp_path / "out")]
        )
        assert result.exit_code == 1
        assert "failed" in result.stdout.lower() or "error" in result.stdout.lower()

    def test_doctor_without_flags_still_diagnoses(self, tmp_path):
        # Backward compatibility: no --check-drift => env diagnostic
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "Infra Lang v" in result.stdout
        assert "Python" in result.stdout
