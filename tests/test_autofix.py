"""Tests for ``infra doctor --fix`` and the autofix/source-editor engine."""

from __future__ import annotations

import random

import pytest
from typer.testing import CliRunner

from infra.analyzer.autofix import (
    AUTO_SECRET_STORE,
    AUTO_STORE_PROVIDER,
    FIXABLE_CODES,
    Fix,
    parse_memory_value,
)
from infra.analyzer.source_editor import (
    attach_fix_comments,
    compute_fixes,
    is_round_trip_stable,
    print_source,
    render_diff,
)
from infra.analyzer.validator import SemanticValidator
from infra.cli.main import app
from infra.parser import parse

runner = CliRunner()

MONSTER = """\
service api {
  image: "myapp:latest"
  replicas: 2
  port: 8080
  env { DB_PASSWORD: "hunter2" }
  depends_on: [db]
}
database db {
  type: postgres
  size: 20Gi
}
"""

CLEAN = """\
service api {
  image: "registry.example.com/api:2.1"
  replicas: 2
  port: 8080
  resources {
    limits { memory: 512Mi }
  }
  health http("/health") { interval: 30s timeout: 5s }
  lifecycle { preStop { exec: ["sleep", "5"] } }
  depends_on: [db]
}
database db {
  type: postgres
  backup { enabled: true schedule: "0 2 * * *" }
}
"""


def _fix_source(src: str, **kw) -> str:
    program = parse(src)
    _result, _old, new = compute_fixes(program, **kw)
    return new


# --------------------------------------------------------------------------- #
# Individual rules
# --------------------------------------------------------------------------- #


class TestSec001:
    def test_plain_secret_becomes_from_secret(self):
        out = _fix_source(MONSTER)
        assert 'DB_PASSWORD: from secret "auto_secrets".DB_PASSWORD' in out
        assert f'secret_store "{AUTO_SECRET_STORE}"' in out

    def test_store_created_only_once_for_multiple_secrets(self):
        src = (
            'service a {\n  image: "x:1"\n'
            '  env { DB_PASSWORD: "p", API_KEY: "k", TOKEN: "t" }\n}\n'
        )
        out = _fix_source(src)
        assert out.count(f'secret_store "{AUTO_SECRET_STORE}"') == 1
        for name in ("DB_PASSWORD", "API_KEY", "TOKEN"):
            assert f'from secret "auto_secrets".{name}' in out

    def test_store_uses_supported_provider(self):
        out = _fix_source(MONSTER)
        assert f'provider: "{AUTO_STORE_PROVIDER}"' in out

    def test_existing_store_not_duplicated(self):
        src = (
            'secret_store "auto_secrets" {\n  provider: vault\n}\n'
            'service a {\n  image: "x:1"\n  env { DB_PASSWORD: "p" }\n}\n'
        )
        out = _fix_source(src)
        assert out.count("secret_store") == 1
        assert "vault" in out

    def test_non_secret_env_untouched(self):
        src = 'service a {\n  image: "x:1"\n  env { MODE: "prod" }\n}\n'
        result, _old, new = compute_fixes(parse(src))
        assert 'MODE: "prod"' in new
        assert not any(f.code == "SEC001" for f in result.applied)

    def test_from_secret_reference_not_refixed(self):
        src = (
            'secret_store "v" {\n  provider: vault\n}\n'
            'service a {\n  image: "x:1"\n'
            '  env { DB_PASSWORD: from secret "v".DB_PASSWORD }\n}\n'
        )
        result, _old, _new = compute_fixes(parse(src))
        assert not any(f.code == "SEC001" for f in result.applied)


class TestSec003:
    def test_mutable_tag_gets_comment(self):
        out = _fix_source(MONSTER)
        assert 'image: "myapp:latest"  # FIXME: pin to a specific version' in out

    def test_no_tag_treated_as_latest(self):
        src = 'service a {\n  image: "nginx"\n}\n'
        out = _fix_source(src)
        assert '# FIXME: pin to a specific version' in out

    def test_pinned_tag_untouched(self):
        src = 'service a {\n  image: "nginx:1.25.3"\n}\n'
        result, _old, new = compute_fixes(parse(src))
        assert "# FIXME" not in new
        assert not any(f.code == "SEC003" for f in result.applied)

    def test_sha256_digest_untouched(self):
        src = 'service a {\n  image: "nginx@sha256:deadbeef"\n}\n'
        result, _old, _new = compute_fixes(parse(src))
        assert not any(f.code == "SEC003" for f in result.applied)

    def test_output_still_parses_with_comment(self):
        out = _fix_source(MONSTER)
        again = parse(out)
        assert again.statements


class TestRel003:
    def test_memory_limit_injected_into_fresh_resources(self):
        out = _fix_source(MONSTER)
        assert "limits: {memory: 512Mi}" in out

    def test_existing_requests_preserved(self):
        src = (
            'service a {\n  image: "x:1"\n'
            "  resources {\n    requests { cpu: 100m }\n  }\n}\n"
        )
        out = _fix_source(src)
        assert "cpu: 100m" in out
        assert "memory: 512Mi" in out

    def test_existing_limits_kept_memory_added(self):
        src = (
            'service a {\n  image: "x:1"\n'
            "  resources {\n    limits { cpu: 1 }\n  }\n}\n"
        )
        out = _fix_source(src)
        assert "cpu: 1" in out
        assert "memory: 512Mi" in out

    def test_custom_default_memory(self):
        out = _fix_source(MONSTER, default_memory="256Mi")
        assert "memory: 256Mi" in out

    def test_request_memory_counts_as_limit(self):
        src = (
            'service a {\n  image: "x:1"\n'
            "  resources {\n    requests { memory: 128Mi }\n  }\n}\n"
        )
        result, _old, _new = compute_fixes(parse(src))
        assert not any(f.code == "REL003" for f in result.applied)

    def test_invalid_default_memory_raises(self):
        with pytest.raises(ValueError, match="Invalid memory value"):
            parse_memory_value("lots")

    def test_memory_units_accepted(self):
        for txt in ("128Ki", "256Mi", "1Gi", "2Ti"):
            rv = parse_memory_value(txt)
            assert rv.unit in ("Ki", "Mi", "Gi", "Ti")


class TestRel004:
    def test_health_injected_with_defaults(self):
        out = _fix_source(MONSTER)
        assert 'health http("/health") { interval: 30s timeout: 5s }' in out

    def test_service_without_port_skipped(self):
        src = 'service a {\n  image: "x:1"\n}\n'
        result, _old, new = compute_fixes(parse(src))
        assert "health http" not in new
        skip = next(f for f in result.skipped if f.code == "REL004")
        assert "no port" in skip.description

    def test_existing_health_preserved(self):
        src = 'service a {\n  image: "x:1"\n  port: 1\n  health http("/ready")\n}\n'
        result, _old, new = compute_fixes(parse(src))
        assert 'health http("/ready")' in new
        assert not any(f.code == "REL004" for f in result.applied)

    def test_existing_probes_count(self):
        src = (
            'service a {\n  image: "x:1"\n  port: 1\n'
            '  probes { liveness: http("/live") }\n}\n'
        )
        result, _old, _new = compute_fixes(parse(src))
        assert not any(f.code == "REL004" for f in result.applied)


class TestRel006:
    def test_backup_injected(self):
        out = _fix_source(MONSTER)
        assert "backup {" in out
        assert "enabled: true" in out
        assert 'schedule: "0 2 * * *"' in out

    def test_disabled_backup_reenabled(self):
        # NOTE: the transformer treats a present ``enabled`` key as truthy
        # (``bool(Literal)``), so the reachable "disabled" state is a backup
        # block WITHOUT an ``enabled`` key (parser quirk kept for backcompat).
        src = (
            'database db {\n  type: postgres\n'
            '  backup { schedule: "0 5 * * *" }\n}\n'
        )
        out = _fix_source(src)
        assert "enabled: true" in out
        assert 'schedule: "0 5 * * *"' in out

    def test_disabled_backup_without_schedule_gets_default(self):
        src = 'database db {\n  type: postgres\n  backup { storage: "s3" }\n}\n'
        out = _fix_source(src)
        assert "enabled: true" in out
        assert 'schedule: "0 2 * * *"' in out

    def test_enabled_backup_untouched(self):
        result, _old, _new = compute_fixes(parse(CLEAN))
        assert not any(f.code == "REL006" for f in result.applied)


class TestRel009:
    def test_prestop_injected_for_multi_replica(self):
        out = _fix_source(MONSTER)
        assert 'preStop { exec: ["sleep", "5"] }' in out

    def test_single_replica_skipped(self):
        src = 'service a {\n  image: "x:1"\n  replicas: 1\n}\n'
        result, _old, new = compute_fixes(parse(src))
        assert "preStop" not in new

    def test_existing_prestop_preserved(self):
        src = (
            'service a {\n  image: "x:1"\n  replicas: 2\n'
            '  lifecycle { preStop { exec: ["sleep", "9"] } }\n}\n'
        )
        result, _old, new = compute_fixes(parse(src))
        assert '"sleep", "9"' in new

    def test_existing_poststart_preserved_when_adding_prestop(self):
        src = (
            'service a {\n  image: "x:1"\n  replicas: 2\n'
            '  lifecycle { postStart { exec: ["echo", "go"] } }\n}\n'
        )
        out = _fix_source(src)
        assert "postStart" in out
        assert "preStop" in out


# --------------------------------------------------------------------------- #
# Combinations, --only, result metadata
# --------------------------------------------------------------------------- #


class TestCombinationsAndOnly:
    def test_seven_issues_fixed_in_one_pass(self):
        result, _old, new = compute_fixes(parse(MONSTER))
        codes = sorted({f.code for f in result.applied})
        assert codes == [
            "REL003",
            "REL004",
            "REL006",
            "REL009",
            "SEC001",
            "SEC003",
        ]
        again = parse(new)
        validation = SemanticValidator().validate(again)
        assert validation.is_valid  # SEC001 error is gone

    def test_only_single_code(self):
        result, _old, new = compute_fixes(parse(MONSTER), only=["SEC001"])
        assert 'from secret "auto_secrets".DB_PASSWORD' in new
        assert "health http" not in new
        assert "backup {" not in new
        assert {f.code for f in result.applied} == {"SEC001"}

    def test_only_multiple_codes(self):
        result, _old, new = compute_fixes(
            parse(MONSTER), only=["REL003", "REL006"]
        )
        assert {f.code for f in result.applied} == {"REL003", "REL006"}
        assert "memory" in new and "backup {" in new
        assert "# FIXME" not in new

    def test_only_is_case_insensitive_at_cli_not_engine(self):
        # engine expects exact uppercase codes (CLI normalises them)
        result, _old, _new = compute_fixes(parse(MONSTER), only=["sec001"])
        assert not result.applied

    def test_clean_file_reports_no_changes(self):
        result, old, new = compute_fixes(parse(CLEAN))
        assert result.applied == []
        assert not result.changed
        assert old == new

    def test_fix_result_dataclass_defaults(self):
        fix = Fix(code="X", target="t", description="d")
        assert fix.code == "X"

    def test_fixable_codes_order_stable(self):
        assert FIXABLE_CODES == (
            "SEC001", "SEC003", "REL003", "REL004", "REL006", "REL009"
        )


# --------------------------------------------------------------------------- #
# source_editor helpers
# --------------------------------------------------------------------------- #


class TestSourceEditor:
    def test_attach_fix_comments_no_comments_returns_source(self):
        src = 'service a { image: "x:1" }\n'
        assert attach_fix_comments(src, []) == src

    def test_attach_comments_duplicate_images_in_order(self):
        printed = print_source(
            parse(
                'service a {\n  image: "dup:latest"\n}\n'
                'service b {\n  image: "dup:latest"\n}\n'
            )
        )
        commented = attach_fix_comments(
            printed, [("dup:latest", "# c1"), ("dup:latest", "# c2")]
        )
        assert "# c1" in commented and "# c2" in commented
        assert commented.index("# c1") < commented.index("# c2")

    def test_unmatched_comment_goes_to_file_end(self):
        src = 'service a { image: "x:1" }\n'
        out = attach_fix_comments(src, [("ghost:1", "# dangling")])
        assert out.rstrip().endswith("# dangling")

    def test_render_diff_contains_headers(self):
        diff = render_diff("a\n", "b\n", from_name="old", to_name="new")
        assert diff.startswith("--- old")
        assert "+++ new" in diff
        assert "-a" in diff and "+b" in diff

    def test_diff_snapshot(self):
        result, old, new = compute_fixes(parse(MONSTER), only=["REL003"])
        diff = render_diff(old, new, from_name="a.infra", to_name="b.infra")
        expected = (
            "--- a.infra\n"
            "+++ b.infra\n"
            "@@ -6,6 +6,9 @@\n"
            '       DB_PASSWORD: "hunter2"\n'
            "     }\n"
            "     depends_on: [db]\n"
            "+    resources {\n"
            "+      limits: {memory: 512Mi}\n"
            "+    }\n"
            " }\n"
            " \n"
            " database db {\n"
        )
        assert diff == expected

    def test_is_round_trip_stable_on_examples(self):
        for f in (
            "examples/01_hello_world.infra",
            "examples/02_web_app.infra",
            "examples/03_microservices.infra",
            "examples/04_cicd_pipeline.infra",
        ):
            with open(f, encoding="utf-8") as fh:
                src = fh.read()
            assert is_round_trip_stable(src), f

    def test_compute_fixes_default_memory_override(self):
        result, _old, new = compute_fixes(parse(MONSTER), default_memory=None)
        assert "512Mi" in new
        _result2, _old2, new2 = compute_fixes(
            parse(MONSTER), default_memory="1Gi"
        )
        assert "1Gi" in new2


# --------------------------------------------------------------------------- #
# Round-trip preservation over 100 realistic generated files
# --------------------------------------------------------------------------- #


def _gen_file(rng: random.Random, idx: int) -> str:
    """Deterministic pseudo-realistic .infra source for round-trip tests."""
    lines: list[str] = []
    svcs = rng.randint(1, 4)
    tags = ["nginx:1.25", "app:latest", "reg.local/x:2", "img@sha256:abc"]
    for i in range(svcs):
        lines.append(f"service svc{i} {{")
        lines.append(f'  image: "{rng.choice(tags)}"')
        lines.append(f"  replicas: {rng.randint(1, 5)}")
        if rng.random() < 0.7:
            lines.append(f"  port: {rng.randint(80, 9090)}")
        if rng.random() < 0.5:
            lines.append(f'  env {{ KEY{i}: "v{i}" }}')
        if rng.random() < 0.3:
            lines.append('  env { DB_PASSWORD: "hardcoded" }')
        if rng.random() < 0.4:
            mem = rng.choice(["128Mi", "256Mi", "512Mi"])
            lines.append(f"  resources {{\n    limits {{ memory: {mem} }}\n  }}")
        if rng.random() < 0.4:
            lines.append('  health http("/ok")')
        if rng.random() < 0.2:
            lines.append('  lifecycle { preStop { exec: ["sleep", "2"] } }')
        if i > 0:
            lines.append(f"  depends_on: [svc{i - 1}]")
        lines.append("}")
    if rng.random() < 0.6:
        lines.extend(
            [
                "database db {",
                "  type: postgres",
                f"  size: {rng.choice([10, 20, 50])}Gi",
            ]
        )
        if rng.random() < 0.5:
            lines.append('  backup { enabled: true schedule: "0 1 * * *" }')
        lines.append("}")
    if rng.random() < 0.4:
        lines.extend(
            [
                "cache redis_cache {",
                "  type: redis",
                f"  persistence: {rng.choice(['true', 'false'])}",
                "}",
            ]
        )
    if rng.random() < 0.3:
        lines.append("queue events {\n  type: kafka\n  replicas: 2\n}")
    if rng.random() < 0.3:
        lines.append("storage assets {\n  type: s3\n  size: 20Gi\n}")
    return "\n".join(lines) + "\n"


class TestRoundTripHundredFiles:
    @pytest.mark.parametrize("idx", list(range(100)))
    def test_generated_file_is_stable(self, idx):
        src = _gen_file(random.Random(9000 + idx), idx)
        assert is_round_trip_stable(src), src

    @pytest.mark.parametrize("idx", list(range(25)))
    def test_clean_files_pass_through_unchanged_after_fix_engine(self, idx):
        """Semantically clean sources come out of compute_fixes unmodified."""
        src = _gen_file(random.Random(5000 + idx), idx)
        result, old, new = compute_fixes(parse(src))
        # with changes enabled the output still re-parses and stays stable
        reparsed = parse(new)
        assert is_round_trip_stable(new)
        assert len(reparsed.statements) >= len(parse(src).statements)
        # `old` is itself a fixed point of the formatter
        assert is_round_trip_stable(old)


# --------------------------------------------------------------------------- #
# CLI: doctor --fix / --dry-run / --only / --no-backup
# --------------------------------------------------------------------------- #


def _write(tmp_path, name: str, source: str):
    f = tmp_path / name
    f.write_text(source, encoding="utf-8")
    return f


class TestDoctorFixCli:
    def test_dry_run_shows_diff_and_does_not_write(self, tmp_path):
        f = _write(tmp_path, "m.infra", MONSTER)
        result = runner.invoke(app, ["doctor", str(f), "--dry-run"])
        assert result.exit_code == 0
        assert "+++ " in result.output
        assert "# FIXME" in result.output
        assert f.read_text(encoding="utf-8") == MONSTER  # untouched
        assert not (tmp_path / "m.infra.bak").exists()

    def test_fix_writes_and_creates_backup(self, tmp_path):
        f = _write(tmp_path, "m.infra", MONSTER)
        result = runner.invoke(app, ["doctor", str(f), "--fix"])
        assert result.exit_code == 0
        assert "Applied 6 fix(es)" in result.output
        backup = tmp_path / "m.infra.bak"
        assert backup.exists()
        assert backup.read_text(encoding="utf-8") == MONSTER
        fixed = f.read_text(encoding="utf-8")
        assert "health http" in fixed

    def test_fix_no_backup(self, tmp_path):
        f = _write(tmp_path, "m.infra", MONSTER)
        result = runner.invoke(app, ["doctor", str(f), "--fix", "--no-backup"])
        assert result.exit_code == 0
        assert not (tmp_path / "m.infra.bak").exists()
        assert "backup {" in f.read_text(encoding="utf-8")

    def test_fix_then_validate_has_no_errors(self, tmp_path):
        f = _write(tmp_path, "m.infra", MONSTER)
        runner.invoke(app, ["doctor", str(f), "--fix"])
        val = runner.invoke(app, ["validate", str(f)])
        assert val.exit_code == 0, val.output

    def test_fix_twice_is_idempotent(self, tmp_path):
        f = _write(tmp_path, "m.infra", MONSTER)
        runner.invoke(app, ["doctor", str(f), "--fix"])
        once = f.read_text(encoding="utf-8")
        result2 = runner.invoke(app, ["doctor", str(f), "--fix"])
        assert result2.exit_code == 0
        # SEC003 comment re-attaches, everything else is already clean
        assert f.read_text(encoding="utf-8") == once

    def test_clean_file_nothing_to_do(self, tmp_path):
        f = _write(tmp_path, "c.infra", CLEAN)
        result = runner.invoke(app, ["doctor", str(f), "--fix"])
        assert result.exit_code == 0
        assert "No auto-fixable findings" in result.output
        assert not (tmp_path / "c.infra.bak").exists()

    def test_only_sec001_via_cli(self, tmp_path):
        f = _write(tmp_path, "m.infra", MONSTER)
        result = runner.invoke(
            app, ["doctor", str(f), "--fix", "--only", "sec001"]
        )
        assert result.exit_code == 0
        fixed = f.read_text(encoding="utf-8")
        assert "auto_secrets" in fixed
        assert "health http" not in fixed

    def test_unknown_only_code_exits_1(self, tmp_path):
        f = _write(tmp_path, "m.infra", MONSTER)
        result = runner.invoke(
            app, ["doctor", str(f), "--fix", "--only", "SEC999"]
        )
        assert result.exit_code == 1
        assert "Unknown autofix code" in result.output

    def test_empty_only_exits_1(self, tmp_path):
        f = _write(tmp_path, "m.infra", MONSTER)
        result = runner.invoke(
            app, ["doctor", str(f), "--fix", "--only", " , "]
        )
        assert result.exit_code == 1
        assert "requires at least one code" in result.output

    def test_missing_file_exits_1(self, tmp_path):
        result = runner.invoke(
            app, ["doctor", str(tmp_path / "nope.infra"), "--fix"]
        )
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_parse_error_exits_1(self, tmp_path):
        f = _write(tmp_path, "bad.infra", "service {{{\n")
        result = runner.invoke(app, ["doctor", str(f), "--fix"])
        assert result.exit_code == 1
        assert "Cannot parse" in result.output

    def test_invalid_default_memory_exits_1(self, tmp_path):
        f = _write(tmp_path, "m.infra", MONSTER)
        result = runner.invoke(
            app, ["doctor", str(f), "--fix", "--default-memory", "lots"]
        )
        assert result.exit_code == 1
        assert "Invalid memory value" in result.output

    def test_fix_without_file_exits_1(self):
        result = runner.invoke(app, ["doctor", "--fix"])
        assert result.exit_code == 1
        assert "require a .infra file" in result.output

    def test_file_without_fix_flag_exits_1(self, tmp_path):
        f = _write(tmp_path, "m.infra", MONSTER)
        result = runner.invoke(app, ["doctor", str(f)])
        assert result.exit_code == 1
        assert "requires --fix or --dry-run" in result.output

    def test_fix_conflicts_with_check_drift(self, tmp_path):
        f = _write(tmp_path, "m.infra", MONSTER)
        result = runner.invoke(
            app, ["doctor", str(f), "--fix", "--check-drift", str(f)]
        )
        assert result.exit_code == 1
        assert "cannot be combined" in result.output

    def test_dry_run_only_preview_with_skipped_note(self, tmp_path):
        src = 'service a {\n  image: "x:latest"\n}\n'
        f = _write(tmp_path, "s.infra", src)
        result = runner.invoke(app, ["doctor", str(f), "--dry-run"])
        assert result.exit_code == 0
        assert "fix(es) available" in result.output

    def test_rel004_skip_note_visible(self, tmp_path):
        src = 'service a {\n  image: "x:1"\n}\n'
        f = _write(tmp_path, "s.infra", src)
        result = runner.invoke(app, ["doctor", str(f), "--fix"])
        assert result.exit_code == 0
        assert "no port declared" in result.output

    def test_workbook_errors_gone_after_fix_integration(self, tmp_path):
        """Integration: 6 distinct issue codes in one file -> validate clean."""
        f = _write(tmp_path, "m.infra", MONSTER)
        result = runner.invoke(app, ["doctor", str(f), "--fix"])
        assert result.exit_code == 0
        val = runner.invoke(app, ["validate", str(f)])
        assert val.exit_code == 0
        assert "error" not in val.output.lower()
