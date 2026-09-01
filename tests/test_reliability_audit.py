"""Deep audit of reliability rules: trigger, no-trigger, hint."""

from __future__ import annotations

from infra import parse, validate


def v(source):
    return validate(parse(source))


def warn_codes(source):
    return {w.code for w in v(source).warnings}


def get_warning(source, code):
    return next((w for w in v(source).warnings if w.code == code), None)


class TestRel001ThunderingHerd:
    def test_triggers_at_5_replicas_no_probe(self):
        assert "REL001" in warn_codes('service s { image: "x:1" replicas: 5 }')

    def test_not_trigger_at_4_replicas(self):
        assert "REL001" not in warn_codes('service s { image: "x:1" replicas: 4 }')

    def test_not_trigger_with_startup_probe(self):
        source = 'service s { image: "x:1" replicas: 5 probes { startup http("/") } }'
        assert "REL001" not in warn_codes(source)

    def test_hint_mentions_probe(self):
        w = get_warning('service s { image: "x:1" replicas: 5 }', "REL001")
        assert w is not None and w.hint
        assert "probe" in w.hint.lower()


class TestRel002EvenReplicasHA:
    def test_triggers_even_replicas_ha_true(self):
        assert "REL002" in warn_codes(
            "database db { type: postgres ha: true replicas: 2 }"
        )

    def test_not_trigger_odd_replicas(self):
        assert "REL002" not in warn_codes(
            "database db { type: postgres ha: true replicas: 3 }"
        )

    def test_not_trigger_without_ha(self):
        assert "REL002" not in warn_codes("database db { type: postgres replicas: 2 }")

    def test_hint_suggests_odd_number(self):
        w = get_warning("database db { type: postgres ha: true replicas: 2 }", "REL002")
        assert w is not None and w.hint
        assert "3" in w.hint


class TestRel003NoMemoryLimit:
    def test_triggers_no_resources(self):
        assert "REL003" in warn_codes('service s { image: "x:1" replicas: 5 }')

    def test_not_trigger_with_memory_limit(self):
        src = (
            'service s { image: "x:1" replicas: 5 resources { limits { memory: 256Mi } '
            '} }'
        )
        assert "REL003" not in warn_codes(src)

    def test_hint_present(self):
        w = get_warning('service s { image: "x:1" replicas: 5 }', "REL003")
        assert w is not None and w.hint


class TestRel004NoHealthChecks:
    def test_triggers_no_health(self):
        assert "REL004" in warn_codes('service s { image: "x:1" replicas: 5 }')

    def test_not_trigger_with_health(self):
        assert "REL004" not in warn_codes(
            'service s { image: "x:1" replicas: 5 health http("/") }'
        )


class TestRel006NoBackup:
    def test_triggers_no_backup(self):
        assert "REL006" in warn_codes("database db { type: postgres }")

    def test_not_trigger_with_backup(self):
        src = "database db { type: postgres backup { enabled: true } }"
        assert "REL006" not in warn_codes(src)


class TestRel008RedisNoPersistence:
    def test_triggers_no_persistence_when_depended_on(self):
        src = 'cache c { type: redis }\nservice s { image: "x:1" depends: [c] }'
        assert "REL008" in warn_codes(src)

    def test_not_trigger_with_persistence(self):
        src = (
            'cache c { type: redis persistence: true }\nservice s { image: "x:1" '
            'depends: [c] }'
        )
        assert "REL008" not in warn_codes(src)

    def test_not_trigger_when_not_depended_on(self):
        assert "REL008" not in warn_codes("cache c { type: redis }")


class TestRel012AutoscaleFixedReplicas:
    def test_triggers_autoscale_and_replicas(self):
        src = 'service s { image: "x:1" replicas: 5 autoscale { min: 2 max: 10 } }'
        assert "REL012" in warn_codes(src)

    def test_not_trigger_autoscale_no_replicas(self):
        src = 'service s { image: "x:1" autoscale { min: 2 max: 10 } }'
        assert "REL012" not in warn_codes(src)


class TestRel013DatabaseNoResources:
    def test_triggers_database_no_resources(self):
        assert "REL013" in warn_codes("database db { type: postgres }")


class TestRel014KafkaSingleReplica:
    def test_triggers_kafka_single_replica(self):
        src = "queue q { type: kafka replicas: 1 }"
        assert "REL014" in warn_codes(src)

    def test_not_trigger_kafka_multiple_replicas(self):
        src = "queue q { type: kafka replicas: 3 }"
        assert "REL014" not in warn_codes(src)


class TestAllRelFindingsHaveHints:
    def test_every_rel_warning_has_hint(self):
        sources = [
            'service s { image: "x:1" replicas: 5 }',
            "database db { type: postgres ha: true replicas: 2 }",
            "database db { type: postgres }",
            "cache c { type: redis }",
            'service s { image: "x:1" replicas: 5 autoscale { min: 2 max: 10 } }',
            "queue q { type: kafka replicas: 1 }",
        ]
        for source in sources:
            for w in v(source).warnings:
                if w.code and w.code.startswith("REL"):
                    assert w.hint is not None, f"{w.code} missing hint: {w.message}"
