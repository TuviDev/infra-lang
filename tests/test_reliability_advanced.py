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


class TestReliabilityEdgeCases:
    """Mutation-driven: false-positive avoidance and guard branches."""

    def _codes(self, source):
        from infra import parse, validate
        result = validate(parse(source))
        return [w.code for w in result.warnings]

    def test_rel001_no_trigger_with_startup_probe(self):
        src = ('service api { image: "x" replicas: 8 '
               'probes { startup { http: "/ready" } } }')
        assert "REL001" not in self._codes(src)

    def test_rel001_no_trigger_few_replicas(self):
        src = 'service api { image: "x" replicas: 3 }'
        assert "REL001" not in self._codes(src)

    def test_rel001_triggers_high_replicas_no_probe(self):
        src = 'service api { image: "x" replicas: 8 }'
        assert "REL001" in self._codes(src)

    def test_rel002_no_trigger_odd_ha_replicas(self):
        src = "database db { type: postgres ha: true replicas: 3 }"
        assert "REL002" not in self._codes(src)

    def test_rel002_triggers_even_ha_replicas(self):
        src = "database db { type: postgres ha: true replicas: 2 }"
        assert "REL002" in self._codes(src)

    def test_rel003_no_trigger_with_memory_limit(self):
        src = ('service api { image: "x" replicas: 5 resources { '
               'limits { memory: 512Mi } } }')
        assert "REL003" not in self._codes(src)

    def test_rel003_triggers_no_memory_limit(self):
        src = ('service api { image: "x" replicas: 5 resources { '
               'requests { cpu: 100m } } }')
        assert "REL003" in self._codes(src)

    def test_rel011_no_trigger_autoscale_with_limits(self):
        src = ('service api { image: "x" autoscale { min: 2, max: 5 } '
               'resources { limits { memory: 512Mi } } }')
        assert "REL011" not in self._codes(src)

    def test_rel011_triggers_autoscale_without_limits(self):
        src = 'service api { image: "x" autoscale { min: 2, max: 5 } }'
        assert "REL011" in self._codes(src)

    def test_rel012_no_trigger_autoscale_without_replicas(self):
        src = 'service api { image: "x" autoscale { min: 2, max: 5 } }'
        assert "REL012" not in self._codes(src)

    def test_rel012_triggers_autoscale_with_replicas(self):
        src = 'service api { image: "x" replicas: 4 autoscale { min: 2, max: 5 } }'
        assert "REL012" in self._codes(src)

    def test_rel008_no_trigger_persistent_cache(self):
        src = "cache c { type: redis persistence: true }"
        assert "REL008" not in self._codes(src)

    def test_rel014_no_trigger_kafka_multi_replica(self):
        src = "queue q { type: kafka replicas: 3 }"
        assert "REL014" not in self._codes(src)


class TestReliabilityMoreEdge:
    def _codes(self, source):
        from infra import parse, validate
        result = validate(parse(source))
        return [w.code for w in result.warnings]

    def test_rel002_non_int_replicas_treated_as_1(self):
        # non-int replicas default to 1 -> odd -> no REL002
        src = "database db { type: postgres ha: true }"
        assert "REL002" not in self._codes(src)

    def test_rel005_deep_dependency_chain(self):
        # A -> B -> C -> D (depth >=4) triggers REL005
        src = (
            'service a { image: "x" depends: [b] }\n'
            'service b { image: "x" depends: [c] }\n'
            'service c { image: "x" depends: [d] }\n'
            'service d { image: "x" depends: [e] }\n'
            'service e { image: "x" }\n'
        )
        assert "REL005" in self._codes(src)

    def test_rel005_no_trigger_shallow(self):
        src = 'service a { image: "x" depends: [b] }\nservice b { image: "x" }'
        assert "REL005" not in self._codes(src)

    def test_rel009_no_trigger_single_replica(self):
        src = 'service api { image: "x" }'
        assert "REL009" not in self._codes(src)

    def test_rel011_no_trigger_with_cpu_limit(self):
        src = ('service api { image: "x" autoscale { min: 2, max: 5 } '
               'resources { limits { cpu: 500m } } }')
        assert "REL011" not in self._codes(src)

    def test_rel014_triggers_kafka_single_replica(self):
        src = "queue q { type: kafka }"
        assert "REL014" in self._codes(src)
