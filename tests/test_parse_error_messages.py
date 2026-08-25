"""Regression tests for improved *parse* error messages.

(These complement test_error_messages.py, which covers semantic error codes.)
"""

from __future__ import annotations

import pytest

from infra.errors.exceptions import InfraLexError, InfraParseError
from infra.parser import parse


class TestMissingClosingBrace:
    def test_unclosed_service_block(self):
        with pytest.raises(InfraParseError) as ei:
            parse('service api { image: "nginx" replicas: 2')
        assert "Missing closing brace" in ei.value.message
        assert "started at line" in ei.value.message

    def test_unclosed_block_multiline_reports_start_line(self):
        src = 'service api {\n    image: "nginx"\n    replicas: 2\n    env { KEY: "v"'
        with pytest.raises(InfraParseError) as ei:
            parse(src)
        assert "Missing closing brace" in ei.value.message
        assert "line 4" in ei.value.message  # innermost open brace is on line 4


class TestUnknownKeyword:
    def test_misspelled_keyword_suggests(self):
        with pytest.raises(InfraParseError) as ei:
            parse('servic api { image: "nginx" }')
        assert "Unknown keyword 'servic'" in ei.value.message
        assert "service" in ei.value.message

    def test_gibberish_keyword_generic_hint(self):
        with pytest.raises(InfraParseError) as ei:
            parse('foobar api { image: "nginx" }')
        assert "Unknown keyword 'foobar'" in ei.value.message
        assert "Did you mean" in ei.value.message


class TestMissingValue:
    def test_missing_value_after_image(self):
        with pytest.raises(InfraParseError) as ei:
            parse("service api { image: }")
        assert "Expected a value after 'image:'" in ei.value.message
        assert "Example:" in ei.value.message


class TestValidCodeUnaffected:
    def test_valid_service_still_parses(self):
        result = parse('service api { image: "nginx:1.25" port 80 }')
        assert result is not None


class TestMissingColon:
    def test_missing_colon_after_field(self):
        with pytest.raises(InfraParseError) as ei:
            parse('service api { image "nginx" }')
        assert "Expected ':' after field name 'image'" in ei.value.message
        assert "forget the colon" in ei.value.message

    def test_missing_colon_other_field(self):
        with pytest.raises(InfraParseError) as ei:
            parse('service api { image: "nginx" replicas 2 }')
        assert "Expected ':' after field name 'replicas'" in ei.value.message


class TestUnclosedString:
    def test_unterminated_string_hint(self):
        with pytest.raises(InfraLexError) as ei:
            parse('service api { image: "nginx }')
        assert "Unterminated string literal" in ei.value.message
        assert "forget to close" in ei.value.message
