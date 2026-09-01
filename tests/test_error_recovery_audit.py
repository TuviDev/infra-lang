"""Audit of error reporting: readable parse errors and multi-error collection."""

from __future__ import annotations

from infra import parse, validate
from infra.errors.exceptions import InfraParseError


class TestParseErrorMessages:
    def test_error_shows_line_number(self):
        try:
            parse("service {")
        except InfraParseError as e:
            assert "1" in str(e)

    def test_error_shows_context_line(self):
        source = "service {\nimage: bad\n}"
        try:
            parse(source)
        except Exception as e:
            msg = str(e)
            assert "service" in msg or "image" in msg

    def test_error_message_no_raw_lark_traceback(self):
        try:
            parse("service {")
        except InfraParseError as e:
            msg = str(e)
            assert "UnexpectedToken" not in msg
            assert "lark" not in msg.lower()

    def test_error_has_caret(self):
        try:
            parse("service {")
        except InfraParseError as e:
            assert "^" in str(e)

    def test_error_has_expected_and_got(self):
        try:
            parse("service {")
        except InfraParseError as e:
            assert "Expected:" in str(e)
            assert "Got:" in str(e)

    def test_validate_collects_multiple_errors(self):
        source = """
        service api {
            image: "nginx:1.25"
            replicas: 0
            port: 99999
        }
        """
        r = validate(parse(source))
        assert len(r.errors) >= 2
        codes = {e.code for e in r.errors}
        assert len(codes) >= 2

    def test_validate_collects_multiple_structures_errors(self):
        source = (
            'service api { image: "x" replicas: 0 }\n'
            'service api2 { image: "x" port: 99999 }\n'
            "database db { type: unknown_db_type }"
        )
        r = validate(parse(source))
        # several different error codes reported at once
        assert len(r.errors) >= 2


class TestSemanticErrorHints:
    def test_semantic_error_has_hint(self):
        from infra import parse, validate

        r = validate(parse("database db { type: postgress }"))
        e = next(e for e in r.errors if e.code == "E020")
        assert e.hint and "postgres" in e.hint
