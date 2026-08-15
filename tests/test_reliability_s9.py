"""Tests for REL012/REL013/REL014 reliability rules."""

from __future__ import annotations

from infra import parse, validate


def v(source):
    return validate(parse(source))


def codes(source):
    r = v(source)
    return [w.code for w in r.warnings]


class TestREL012AutoscaleWithReplicas:
    def test_triggers(self):
        assert "REL012" in codes('service api { image: "x" autoscale { min: 2, max: 10 } replicas: 3 }')

    def test_no_trigger_without_replicas(self):
        assert "REL012" not in codes('service api { image: "x" autoscale { min: 2 } }')

    def test_no_trigger_without_autoscale(self):
        assert "REL012" not in codes('service api { image: "x" replicas: 3 }')

    def test_hint_present(self):
        r = v('service api { image: "x" autoscale { min: 2 } replicas: 5 }')
        w = next((w for w in r.warnings if w.code == "REL012"), None)
        if w:
            assert w.hint is not None
            assert "replicas" in w.hint.lower()

    def test_message_mentions_ignored(self):
        r = v('service api { image: "x" autoscale { min: 2 } replicas: 3 }')
        w = next((w for w in r.warnings if w.code == "REL012"), None)
        if w:
            assert "ignored" in w.message.lower()


class TestREL013DatabaseNoResources:
    def test_triggers(self):
        assert "REL013" in codes("database db { type: postgres }")

    def test_no_trigger_with_storage(self):
        assert "REL013" not in codes("database db { type: postgres storage: 20Gi }")

    def test_no_trigger_with_size(self):
        assert "REL013" not in codes("database db { type: postgres size: 10Gi }")

    def test_hint_present(self):
        r = v("database db { type: postgres }")
        w = next((w for w in r.warnings if w.code == "REL013"), None)
        if w:
            assert w.hint is not None

    def test_message_mentions_resources(self):
        r = v("database db { type: postgres }")
        w = next((w for w in r.warnings if w.code == "REL013"), None)
        if w:
            assert "resource" in w.message.lower()


class TestREL014KafkaSingleReplica:
    def test_triggers(self):
        assert "REL014" in codes("queue q { type: kafka }")

    def test_no_trigger_with_replicas(self):
        assert "REL014" not in codes("queue q { type: kafka replicas: 3 }")

    def test_no_trigger_not_kafka(self):
        assert "REL014" not in codes("queue q { type: rabbitmq }")

    def test_hint_present(self):
        r = v("queue q { type: kafka }")
        w = next((w for w in r.warnings if w.code == "REL014"), None)
        if w:
            assert w.hint is not None
            assert "3" in w.hint

    def test_message_mentions_fault_tolerance(self):
        r = v("queue q { type: kafka }")
        w = next((w for w in r.warnings if w.code == "REL014"), None)
        if w:
            assert "fault tolerance" in w.message.lower()


class TestRulesAreWarnings:
    def test_all_warnings_do_not_invalidate(self):
        src = ('service api { image: "x" autoscale { min: 2 } replicas: 3 }\n'
               "database db { type: postgres }\n"
               "queue q { type: kafka }")
        r = v(src)
        assert r.is_valid
        for code in ["REL012", "REL013", "REL014"]:
            assert code in codes(src)
