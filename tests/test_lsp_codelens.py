"""Tests for the CodeLens FinOps feature (v0.9.0).

Covers every block type, badge flag combinations, the ASCII-safe fallback,
option resolution, the server handler (including legacy-pygls tolerance)
and the hover "Insight" expansion.
"""

from __future__ import annotations

import os

import pytest
from lsprotocol.types import (
    CodeLensParams,
    HoverParams,
    Position,
    TextDocumentIdentifier,
)

from infra.lsp import server as mod
from infra.lsp.codelens import (
    LensOptions,
    block_decl_at,
    build_lenses,
    hover_insight_section,
    options_from_initialization,
    resolve_emoji,
)

SOURCE = """\
service api {
  image: "registry.example.com/api:2.0"
  replicas: 3
  port: 8080
  depends_on: [db]
}
database db {
  type: postgres
  size: 20Gi
  backup { enabled: true }
}
cache sessions {
  type: redis
  persistence: true
}
queue events {
  type: kafka
  replicas: 3
  topics { orders: { partitions: 3 } }
}
storage assets {
  type: s3
  size: 50Gi
}
environment prod {
  provider: aws
  region: "eu-central-1"
}
"""


def _titles(source: str = SOURCE, **flags) -> list[str]:
    opts = LensOptions(**flags)
    return [lens.command.title for lens in build_lenses(source, opts)]


# --------------------------------------------------------------------------- #
# Per-block-type labels
# --------------------------------------------------------------------------- #


class TestBlockTypes:
    def test_service_label_emoji(self):
        title = _titles()[0]
        assert title.startswith("💰 $")
        assert "/mo" in title
        assert "⚡ 3 replicas" in title
        assert "📊 Grade:" in title

    def test_service_label_line_points_at_declaration(self):
        lenses = build_lenses(SOURCE, LensOptions())
        assert lenses[0].range.start.line == 0  # 0-based line of `service api`

    def test_database_label(self):
        title = next(t for t in _titles() if "20Gi" in t)
        assert title.startswith("💾 20Gi")
        assert "💰 $103.00/mo" in title
        assert "Backup: enabled" in title

    def test_database_backup_disabled(self):
        src = 'database db {\n  type: postgres\n}\n'
        title = _titles(src)[0]
        assert "Backup: disabled" in title

    def test_database_default_size(self):
        src = 'database db {\n  type: postgres\n  backup { enabled: true }\n}\n'
        title = _titles(src)[0]
        assert title.startswith("💾 100Gi")

    def test_cache_label(self):
        title = next(t for t in _titles() if "redis" in t)
        assert "⚡ redis" in title
        assert "💰 $63.00/mo" in title
        assert "persistence: on" in title

    def test_queue_label(self):
        title = next(t for t in _titles() if "kafka" in t)
        assert "📨 kafka" in title
        assert "3 replicas" in title
        assert "1 topic" in title

    def test_queue_without_topics(self):
        src = 'queue q {\n  type: rabbitmq\n  replicas: 2\n}\n'
        title = _titles(src)[0]
        assert "📨 rabbitmq" in title
        assert "topic" not in title

    def test_storage_label(self):
        title = next(t for t in _titles() if "50Gi" in t)
        assert "📦 50Gi" in title
        assert "💰 $5.00/mo" in title
        assert "s3" in title

    def test_environment_label(self):
        title = next(t for t in _titles() if "Target:" in t)
        assert "🌍 1 service" in title
        assert "/mo total" in title
        assert "🎯 Target: aws" in title

    def test_environment_default_target(self):
        src = 'environment staging {\n  region: "eu-west-1"\n}\n'
        title = _titles(src)[0]
        assert "🎯 Target: kubernetes" in title

    def test_environment_plural_services(self):
        src = (
            'service a {\n  image: "x:1"\n}\n'
            'service b {\n  image: "y:1"\n}\n'
            'environment prod {\n  provider: gcp\n}\n'
        )
        title = next(t for t in _titles(src) if "Target:" in t)
        assert "🌍 2 services" in title


# --------------------------------------------------------------------------- #
# Badge flag combinations
# --------------------------------------------------------------------------- #


class TestFlagCombinations:
    def test_show_cost_off_removes_cost_badge(self):
        titles = _titles(show_cost=False)
        svc = titles[0]
        assert svc.startswith("⚡")
        assert "$" not in svc
        # database lens drops the money part but keeps size+backup
        db = next(t for t in titles if "💾" in t)
        assert "$" not in db
        assert "Backup:" in db

    def test_show_security_off_removes_warning_badge(self):
        svc = _titles(show_security=False)[0]
        assert "warning" not in svc
        assert "🔒" not in svc
        assert "📊 Grade:" in svc  # reliability stays

    def test_show_reliability_off_removes_grade_and_backup(self):
        titles = _titles(show_reliability=False)
        svc = titles[0]
        assert "Grade:" not in svc
        db = next(t for t in titles if "💾" in t)
        assert "Backup:" not in db

    def test_all_badges_off_leaves_replicas_only(self):
        svc = _titles(
            show_cost=False, show_security=False, show_reliability=False
        )[0]
        assert svc == "⚡ 3 replicas"

    def test_disabled_returns_empty(self):
        assert build_lenses(SOURCE, LensOptions(enabled=False)) == []

    def test_parse_error_returns_empty(self):
        assert build_lenses("service {{{\n", LensOptions()) == []


# --------------------------------------------------------------------------- #
# ASCII-safe fallback
# --------------------------------------------------------------------------- #


class TestAsciiFallback:
    def test_service_ascii_labels(self):
        title = _titles(emoji=False)[0]
        assert title.startswith("[$] $")
        assert "[R] 3 replicas" in title
        assert "[!]" in title
        assert "[G:" in title and "Grade:" in title

    def test_database_ascii_labels(self):
        title = next(t for t in _titles(emoji=False) if "[DB]" in t)
        assert title.startswith("[DB] 20Gi")
        assert "[$]" in title

    def test_environment_ascii_labels(self):
        title = next(t for t in _titles(emoji=False) if "[ENV]" in t)
        assert "[ENV] 1 service" in title
        assert "[T] Target: aws" in title

    def test_queue_cache_storage_ascii(self):
        titles = _titles(emoji=False)
        assert any("[Q] kafka" in t for t in titles)
        assert any("[C] redis" in t for t in titles)
        assert any("[ST] 50Gi" in t for t in titles)

    def test_no_emoji_left_in_ascii_mode(self):
        for title in _titles(emoji=False):
            assert all(ord(ch) < 128 for ch in title)

    def test_snapshot_service_full_set(self):
        assert _titles()[0] == (
            "💰 $96.00/mo · ⚡ 3 replicas · 🔒 0 warnings · 📊 Grade: D"
        )

    def test_snapshot_service_ascii(self):
        assert _titles(emoji=False)[0] == (
            "[$] $96.00/mo | [R] 3 replicas | [!] 0 warnings | [G:D] Grade: D"
        )


# --------------------------------------------------------------------------- #
# Option resolution
# --------------------------------------------------------------------------- #


class TestOptionResolution:
    def test_resolve_emoji_true_false(self):
        assert resolve_emoji("true", {}) is True
        assert resolve_emoji("FALSE", {}) is False

    def test_resolve_emoji_auto_locale(self):
        assert resolve_emoji("auto", {"LANG": "en_US.UTF-8"}) is True
        bare = {"LANG": "C", "LC_ALL": "", "LC_CTYPE": ""}
        assert resolve_emoji("auto", bare) is False
        assert resolve_emoji("auto", {"LC_ALL": "pl_PL.UTF8"}) is True
        assert resolve_emoji("weird", {"LANG": "POSIX"}) is False

    def test_options_from_none(self):
        opts = options_from_initialization(None, {})
        assert opts == LensOptions(emoji=False)  # empty locale -> ASCII

    def test_options_from_empty_dict(self):
        opts = options_from_initialization({}, {})
        assert opts.enabled is True
        assert opts.show_cost is True

    def test_options_from_full_dict(self):
        opts = options_from_initialization(
            {
                "infra.codelens.enabled": False,
                "infra.codelens.showCost": False,
                "infra.codelens.showSecurity": False,
                "infra.codelens.showReliability": False,
                "infra.codelens.emoji": "false",
            },
            {},
        )
        assert opts.enabled is False
        assert opts.show_cost is False
        assert opts.emoji is False

    def test_options_ignore_wrong_types(self):
        opts = options_from_initialization(
            {"infra.codelens.enabled": "yes", "infra.codelens.emoji": 42},
            {},
        )
        assert opts.enabled is True  # falls back to default
        assert opts.emoji is False  # numeric -> auto -> empty locale

    def test_options_non_dict_initialization(self):
        assert options_from_initialization("bogus", {}).enabled is True

    def test_real_locale_detection_uses_environ(self):
        opts = options_from_initialization(None)  # default os.environ
        assert isinstance(opts.emoji, bool)


# --------------------------------------------------------------------------- #
# block_decl_at helper
# --------------------------------------------------------------------------- #


class TestBlockDeclAt:
    @pytest.mark.parametrize(
        "line,expected",
        [
            ("service api {", ("service", "api")),
            ('database "db" {', ("database", "db")),
            ("cache sessions {", ("cache", "sessions")),
            ("queue events {", ("queue", "events")),
            ("storage assets {", ("storage", "assets")),
            ("  service worker {", ("service", "worker")),
        ],
    )
    def test_declarations(self, line, expected):
        assert block_decl_at(line) == expected

    @pytest.mark.parametrize(
        "line",
        ["image: nginx", "  replicas: 3", "}", "# service api {", "ingress {", ""],
    )
    def test_non_declarations(self, line):
        assert block_decl_at(line) is None


# --------------------------------------------------------------------------- #
# Hover Insight expansion
# --------------------------------------------------------------------------- #


class TestHoverInsight:
    def test_service_card_full(self):
        card = hover_insight_section(SOURCE, "service", "api")
        assert "### 💡 Insight" in card
        assert "**service** `api`" in card
        assert "**Cost:** $96.00/mo" in card
        assert "compute" in card and "RAM" in card
        assert "REL003" in card  # no memory limit
        assert "**Depends on:** db" in card
        assert "Suggested optimizations:" in card

    def test_dependents_listing(self):
        card = hover_insight_section(SOURCE, "database", "db")
        assert "**database** `db`" in card
        assert "storage" in card  # 20Gi storage part of the cost detail

    def test_service_without_findings(self):
        src = (
            'service ok {\n  image: "reg.local/x:1.0"\n  replicas: 2\n'
            "  port: 8080\n  health http(\"/h\")\n"
            "  resources {\n    limits { memory: 512Mi }\n  }\n"
            "  security {\n    user: 1000\n  }\n"
            "  lifecycle {\n    preStop {\n"
            "      exec: [\"sleep\", \"1\"]\n"
            "    }\n  }\n}\n"
        )
        card = hover_insight_section(src, "service", "ok")
        assert card is not None
        assert "**Warnings:** none" in card

    def test_unmetered_block_cost_line(self):
        src = 'queue events {\n  type: kafka\n  replicas: 3\n}\n'
        card = hover_insight_section(src, "queue", "events")
        assert card is not None
        assert "not metered" in card

    def test_unknown_block_returns_none(self):
        assert hover_insight_section(SOURCE, "service", "ghost") is None

    def test_parse_error_returns_none(self):
        assert hover_insight_section("service {{{\n", "service", "x") is None

    def test_wrong_kind_returns_none(self):
        assert hover_insight_section(SOURCE, "database", "api") is None

    def test_message_first_line_only(self):
        src = 'service a {\n  image: "x:latest"\n}\n'
        card = hover_insight_section(src, "service", "a")
        assert card is not None
        for ln in card.splitlines():
            if ln.startswith("- `SEC"):
                assert len(ln) < 140

    def test_storage_card_lists_storage_cost(self):
        src = 'storage assets {\n  type: s3\n  size: 50Gi\n}\n'
        card = hover_insight_section(src, "storage", "assets")
        assert card is not None
        assert "storage 50.00 GB" in card
        assert "**Warnings:** none" in card

    def test_findings_without_hints_skip_suggestions(self, monkeypatch):
        from infra.analyzer.reliability import ReliabilityFinding

        monkeypatch.setattr(
            "infra.lsp.codelens.SecurityChecker.check", lambda _self, _p: []
        )
        monkeypatch.setattr(
            "infra.lsp.codelens.ReliabilityChecker.check",
            lambda _self, _p: [
                ReliabilityFinding(code="REL999", message="mystery", hint=None)
            ],
        )
        src = 'service a {\n  image: "x:1"\n}\n'
        card = hover_insight_section(src, "service", "a")
        assert card is not None
        assert "`REL999`" in card
        assert "Suggested optimizations:" not in card


# --------------------------------------------------------------------------- #
# Server handler integration (dev stack) + legacy tolerance
# --------------------------------------------------------------------------- #


def _fake_ls(lines, init_options=None):
    class FakeDoc:
        def __init__(self, doc_lines):
            self.lines = doc_lines

    class FakeWorkspace:
        def __init__(self, doc_lines):
            self._lines = doc_lines

        def get_text_document(self, uri):
            return FakeDoc(self._lines)

    class FakeLS:
        def __init__(self, doc_lines, opts):
            self.workspace = FakeWorkspace(doc_lines)
            self.initialization_options = opts

    return FakeLS(lines, init_options)


URI = "file:///t.infra"


class TestServerHandler:
    def setup_method(self):
        mod._live_lens_settings.clear()

    def test_codelens_returns_lenses(self):
        ls = _fake_ls(SOURCE.splitlines())
        params = CodeLensParams(text_document=TextDocumentIdentifier(uri=URI))
        lenses = mod.code_lens(ls, params)
        assert lenses is not None and len(lenses) == 6

    def test_codelens_disabled_returns_none(self):
        ls = _fake_ls(
            SOURCE.splitlines(), {"infra.codelens.enabled": False}
        )
        params = CodeLensParams(text_document=TextDocumentIdentifier(uri=URI))
        assert mod.code_lens(ls, params) is None

    def test_codelens_respects_badge_flags(self):
        ls = _fake_ls(
            SOURCE.splitlines(),
            {"infra.codelens.showCost": False, "infra.codelens.emoji": "false"},
        )
        params = CodeLensParams(text_document=TextDocumentIdentifier(uri=URI))
        lenses = mod.code_lens(ls, params)
        assert lenses is not None
        svc = lenses[0].command.title
        assert "$" not in svc
        assert svc.startswith("[R]")

    def test_live_settings_override(self):
        from lsprotocol.types import DidChangeConfigurationParams

        ls = _fake_ls(SOURCE.splitlines())
        mod.did_change_configuration(
            ls,
            DidChangeConfigurationParams(
                settings={"infra.codelens.enabled": False}
            ),
        )
        params = CodeLensParams(text_document=TextDocumentIdentifier(uri=URI))
        assert mod.code_lens(ls, params) is None
        mod._live_lens_settings.clear()

    def test_live_settings_non_dict_ignored(self):
        from lsprotocol.types import DidChangeConfigurationParams

        ls = _fake_ls(SOURCE.splitlines())
        mod.did_change_configuration(
            ls, DidChangeConfigurationParams(settings=["not", "a", "dict"])
        )
        params = CodeLensParams(text_document=TextDocumentIdentifier(uri=URI))
        assert mod.code_lens(ls, params) is not None

    def test_lens_options_fallback_no_attrs(self):
        class BareLS:
            pass

        opts = mod._lens_options(BareLS())
        assert isinstance(opts, LensOptions)

    def test_hover_with_insight_on_decl_keyword(self):
        ls = _fake_ls(SOURCE.splitlines())
        params = HoverParams(
            text_document=TextDocumentIdentifier(uri=URI),
            position=Position(line=0, character=3),  # on `service api {` keyword
        )
        hover = mod.hover(ls, params)
        assert hover is not None
        assert "💡 Insight" in hover.contents.value
        assert "**service**" in hover.contents.value
        assert "Service definition block" in hover.contents.value

    def test_hover_with_insight_on_decl_name(self):
        ls = _fake_ls(SOURCE.splitlines())
        params = HoverParams(
            text_document=TextDocumentIdentifier(uri=URI),
            position=Position(line=0, character=9),  # on the `api` name
        )
        hover = mod.hover(ls, params)
        assert hover is not None
        assert "💡 Insight" in hover.contents.value
        assert "**Cost:** $96.00/mo" in hover.contents.value

    def test_hover_plain_docs_still_work(self):
        ls = _fake_ls(["image: nginx:1.25"])
        params = HoverParams(
            text_document=TextDocumentIdentifier(uri=URI),
            position=Position(line=0, character=2),
        )
        hover = mod.hover(ls, params)
        assert hover is not None
        assert "Docker image" in hover.contents.value
        assert "💡 Insight" not in hover.contents.value

    def test_hover_nothing_found(self):
        ls = _fake_ls(["   "])
        params = HoverParams(
            text_document=TextDocumentIdentifier(uri=URI),
            position=Position(line=0, character=1),
        )
        assert mod.hover(ls, params) is None

    def test_hover_position_beyond_lines(self):
        ls = _fake_ls(["service api {"])
        params = HoverParams(
            text_document=TextDocumentIdentifier(uri=URI),
            position=Position(line=99, character=0),
        )
        assert mod.hover(ls, params) is None


class TestLegacyStackTolerance:
    """The handler must never crash the legacy pygls 1.3.1 runtime."""

    def test_build_lenses_pure_python(self):
        # build_lenses only touches lsprotocol dataclasses, no pygls APIs
        lenses = build_lenses(SOURCE, LensOptions())
        assert all(lens.command for lens in lenses)

    def test_server_module_has_code_lens_feature(self):
        assert hasattr(mod, "code_lens")
        assert callable(mod.code_lens)

    def test_disabled_feature_returns_none_not_error(self):
        ls = _fake_ls(SOURCE.splitlines(), {"infra.codelens.enabled": False})
        params = CodeLensParams(text_document=TextDocumentIdentifier(uri=URI))
        # legacy stack: handler is simply skipped/returns None, no exception
        assert mod.code_lens(ls, params) is None


class TestLensStructure:
    def test_lens_has_command_with_title(self):
        for lens in build_lenses(SOURCE, LensOptions()):
            assert lens.command is not None
            assert lens.command.title
            assert lens.range.start == lens.range.end

    def test_every_declared_block_gets_a_lens(self):
        titles = _titles()
        assert len(titles) == 6  # service, db, cache, queue, storage, env


class TestEmojiEnvOverride:
    def test_monkeypatched_locale_switches_mode(self, monkeypatch):
        for var in ("LC_ALL", "LC_CTYPE", "LANG"):
            monkeypatch.delitem(os.environ, var, raising=False)
        monkeypatch.setitem(os.environ, "LC_ALL", "C")
        opts = options_from_initialization({"infra.codelens.emoji": "auto"})
        assert opts.emoji is False
        opts = options_from_initialization(
            {"infra.codelens.emoji": "auto"},
            {"LC_ALL": "en_US.UTF-8"},
        )
        assert opts.emoji is True
