"""`infra learn` — interactive terminal tutor (v0.8.0)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from infra.cli.learn_cmd import (
    LESSONS,
    render_lesson,
    render_lesson_list,
    verify_solution,
)
from infra.cli.main import app

runner = CliRunner()


def _write(tmp_path: Path, content: str) -> Path:
    f = tmp_path / "solution.infra"
    f.write_text(content, encoding="utf-8")
    return f


# --------------------------------------------------------------------------- #
# --list
# --------------------------------------------------------------------------- #


class TestLearnList:
    def test_list_shows_all_five_lessons(self):
        r = runner.invoke(app, ["learn", "--list"])
        assert r.exit_code == 0, r.output
        for lesson in LESSONS:
            assert lesson.title in r.output
            assert lesson.goal in r.output

    def test_list_mentions_verify_usage(self):
        r = runner.invoke(app, ["learn", "--list"])
        assert r.exit_code == 0
        assert "--verify" in r.output
        assert "--lesson" in r.output

    def test_render_list_helper_is_deterministic(self):
        assert render_lesson_list() == render_lesson_list()
        for i in range(1, 6):
            assert f"[{i}]" in render_lesson_list()


# --------------------------------------------------------------------------- #
# --lesson N
# --------------------------------------------------------------------------- #


class TestLearnLesson:
    @pytest.mark.parametrize("number", [1, 2, 3, 4, 5])
    def test_each_lesson_renders_all_sections(self, number: int):
        r = runner.invoke(app, ["learn", "--lesson", str(number)])
        assert r.exit_code == 0, r.output
        assert f"LESSON {number}/5" in r.output
        for section in ("GOAL", "THEORY", "EXAMPLE", "YOUR TASK", "EXPECTED PATTERN"):
            assert section in r.output
        assert f"--verify {number}" in r.output

    @pytest.mark.parametrize("bad", ["0", "6", "99", "-3"])
    def test_out_of_range_lesson_fails(self, bad: str):
        r = runner.invoke(app, ["learn", "--lesson", bad])
        assert r.exit_code == 1
        assert "[FAIL] Unknown lesson" in r.output

    def test_render_helper_matches_cli(self):
        r = runner.invoke(app, ["learn", "--lesson", "3"])
        assert r.output.strip() == render_lesson(LESSONS[2]).strip()


# --------------------------------------------------------------------------- #
# Embedded content stays valid (regression net for the course material)
# --------------------------------------------------------------------------- #


class TestEmbeddedContent:
    def test_examples_and_patterns_parse_and_validate(self):
        from infra.analyzer.validator import SemanticValidator
        from infra.parser import parse

        for lesson in LESSONS:
            for source in (lesson.example, lesson.pattern):
                program = parse(source)
                result = SemanticValidator().validate(program)
                assert result.errors == [], (
                    f"lesson {lesson.number} source has semantic errors: "
                    f"{[e.code for e in result.errors]}"
                )

    def test_patterns_satisfy_their_own_lesson_checks(self):
        from infra.cli.learn_cmd import _CHECKERS
        from infra.parser import parse

        for lesson, check in zip(LESSONS, _CHECKERS):
            assert check(parse(lesson.pattern)) == [], (
                f"lesson {lesson.number} pattern fails its own checks"
            )


# --------------------------------------------------------------------------- #
# --verify N <file> — happy paths
# --------------------------------------------------------------------------- #


class TestLearnVerifyHappy:
    @pytest.mark.parametrize("number", [1, 2, 3, 4, 5])
    def test_pattern_solution_passes(self, tmp_path: Path, number: int):
        lesson = LESSONS[number - 1]
        f = _write(tmp_path, lesson.pattern + "\n")
        r = runner.invoke(app, ["learn", str(f), "--verify", str(number)])
        assert r.exit_code == 0, r.output
        assert "[OK]" in r.output
        assert f"Lesson {number}" in r.output

    def test_verify_solution_helper_returns_true(self, tmp_path: Path):
        f = _write(tmp_path, 'service web { image: "nginx:1.25" port: 80 }\n')
        assert verify_solution(1, f) is True


# --------------------------------------------------------------------------- #
# --verify N <file> — failure paths
# --------------------------------------------------------------------------- #


class TestLearnVerifyFailures:
    def test_missing_image_fails_lesson_1(self, tmp_path: Path):
        f = _write(tmp_path, "service web { port: 80 }\n")
        r = runner.invoke(app, ["learn", str(f), "--verify", "1"])
        assert r.exit_code == 1
        assert "[FAIL]" in r.output
        assert "image:" in r.output

    def test_lesson_1_requires_a_service_block(self, tmp_path: Path):
        f = _write(tmp_path, "database db { type: postgres }\n")
        r = runner.invoke(app, ["learn", str(f), "--verify", "1"])
        assert r.exit_code == 1
        assert "at least one 'service' block" in r.output

    def test_lesson_2_requires_secret_and_database(self, tmp_path: Path):
        f = _write(tmp_path, 'service api { image: "x:1" }\n')
        r = runner.invoke(app, ["learn", str(f), "--verify", "2"])
        assert r.exit_code == 1
        assert "'database' block" in r.output
        assert "'secret' block" in r.output
        assert "from secret" in r.output

    def test_lesson_3_requires_two_services_and_network(self, tmp_path: Path):
        f = _write(tmp_path, 'service api { image: "x:1" }\n')
        r = runner.invoke(app, ["learn", str(f), "--verify", "3"])
        assert r.exit_code == 1
        assert "two 'service' blocks" in r.output
        assert "'network' block" in r.output

    def test_lesson_4_requires_health_autoscale_disruption(self, tmp_path: Path):
        f = _write(tmp_path, 'service api { image: "x:1" }\n')
        r = runner.invoke(app, ["learn", str(f), "--verify", "4"])
        assert r.exit_code == 1
        assert "health check" in r.output
        assert "autoscale" in r.output
        assert "disruption" in r.output

    def test_parse_error_is_reported(self, tmp_path: Path):
        f = _write(tmp_path, "service {\n")
        r = runner.invoke(app, ["learn", str(f), "--verify", "1"])
        assert r.exit_code == 1
        assert "does not parse" in r.output

    def test_semantic_error_fails_verification(self, tmp_path: Path):
        f = _write(
            tmp_path,
            'service web { image: "nginx:1.25" replicas: -3 }\n',
        )
        r = runner.invoke(app, ["learn", str(f), "--verify", "1"])
        assert r.exit_code == 1
        assert "semantic error" in r.output

    def test_verify_without_path_fails(self):
        r = runner.invoke(app, ["learn", "--verify", "1"])
        assert r.exit_code == 1
        assert "--verify needs a solution file" in r.output

    def test_verify_unknown_lesson_fails(self, tmp_path: Path):
        f = _write(tmp_path, 'service web { image: "nginx:1.25" }\n')
        r = runner.invoke(app, ["learn", str(f), "--verify", "42"])
        assert r.exit_code == 1
        assert "[FAIL] Unknown lesson" in r.output

    def test_verify_missing_file_fails(self, tmp_path: Path):
        r = runner.invoke(
            app, ["learn", str(tmp_path / "nope.infra"), "--verify", "1"]
        )
        assert r.exit_code == 1
        assert "File not found" in r.output

    def test_verify_solution_helper_returns_false(self, tmp_path: Path):
        f = _write(tmp_path, 'service api { image: "x:1" }\n')
        assert verify_solution(5, f) is False


# --------------------------------------------------------------------------- #
# Interactive mode (default, no flags)
# --------------------------------------------------------------------------- #


class TestLearnInteractive:
    def test_eof_exits_gracefully(self):
        r = runner.invoke(app, ["learn"], input="")
        assert r.exit_code == 0, r.output
        assert "LESSON 1/5" in r.output
        assert "[OK] See you next time." in r.output

    def test_quit_key_exits_after_first_lesson(self):
        r = runner.invoke(app, ["learn"], input="q\n")
        assert r.exit_code == 0, r.output
        assert "LESSON 1/5" in r.output
        assert "LESSON 2/5" not in r.output
        assert "[OK] See you next time." in r.output

    def test_enter_advances_through_all_lessons(self):
        r = runner.invoke(app, ["learn"], input="\n\n\n\n\n")
        assert r.exit_code == 0, r.output
        for i in range(1, 6):
            assert f"LESSON {i}/5" in r.output
        assert "[OK] All 5 lessons shown." in r.output

    def test_help_lists_learn_command(self):
        r = runner.invoke(app, ["--help"])
        assert r.exit_code == 0
        assert "learn" in r.output
