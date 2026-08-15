"""Reliability lint rule tests."""

from __future__ import annotations

from infra import parse, validate


def v(source: str):
    return validate(parse(source))


class TestREL001ThunderingHerd:
    def test_triggers_at_5_replicas(self):
        assert any(w.code == "REL001" for w in v('service a { image:"nginx:1.0" replicas:5 }').warnings)

    def test_triggers_at_10_replicas(self):
        assert any(w.code == "REL001" for w in v('service a { image:"nginx:1.0" replicas:10 }').warnings)

    def test_no_trigger_at_4_replicas(self):
        assert not any(w.code == "REL001" for w in v('service a { image:"nginx:1.0" replicas:4 }').warnings)

    def test_no_trigger_with_startup_probe(self):
        src = 'service a { image:"nginx:1.0" replicas:5 probes { startup http("/ready") } }'
        assert not any(w.code == "REL001" for w in v(src).warnings)

    def test_hint_present(self):
        r = v('service a { image:"nginx:1.0" replicas:5 }')
        w = next(w for w in r.warnings if w.code == "REL001")
        assert w.hint and len(w.hint) > 5

    def test_is_warning_not_error(self):
        r = v('service a { image:"nginx:1.0" replicas:5 }')
        assert any(w.code == "REL001" for w in r.warnings)
        assert r.is_valid


class TestREL002EvenReplicas:
    def test_triggers_even_replicas_ha(self):
        assert any(w.code == "REL002" for w in v('database db { type:postgres replicas:2 ha:true }').warnings)

    def test_no_trigger_odd_replicas(self):
        assert not any(w.code == "REL002" for w in v('database db { type:postgres replicas:3 ha:true }').warnings)

    def test_no_trigger_without_ha(self):
        assert not any(w.code == "REL002" for w in v('database db { type:postgres replicas:2 }').warnings)

    def test_hint_suggests_odd(self):
        r = v('database db { type:postgres replicas:2 ha:true }')
        w = next(w for w in r.warnings if w.code == "REL002")
        assert "3" in w.hint


class TestREL003MemoryLimit:
    def test_triggers_no_resources(self):
        assert any(w.code == "REL003" for w in v('service a { image:"nginx:1.0" }').warnings)

    def test_no_trigger_with_memory_limit(self):
        src = 'service a { image:"nginx:1.0" resources { limits { memory: 256Mi } } }'
        assert not any(w.code == "REL003" for w in v(src).warnings)


class TestREL004HealthChecks:
    def test_triggers_no_health(self):
        assert any(w.code == "REL004" for w in v('service a { image:"nginx:1.0" }').warnings)

    def test_no_trigger_with_health(self):
        assert not any(w.code == "REL004" for w in v('service a { image:"nginx:1.0" health http("/") }').warnings)


class TestREL006Backup:
    def test_triggers_no_backup(self):
        assert any(w.code == "REL006" for w in v('database db { type:postgres }').warnings)

    def test_no_trigger_with_backup(self):
        src = 'database db { type:postgres backup { enabled: true schedule: "0 2 * * *" } }'
        assert not any(w.code == "REL006" for w in v(src).warnings)


class TestREL007SingleReplica:
    def test_triggers_single_depended_on(self):
        src = 'service proxy { image:"haproxy:2" replicas:1 }\nservice api { image:"myapp:1.0" depends: ["proxy"] }'
        assert any(w.code == "REL007" for w in v(src).warnings)

    def test_no_trigger_not_depended(self):
        assert not any(w.code == "REL007" for w in v('service proxy { image:"haproxy:2" replicas:1 }').warnings)


class TestREL009GracefulShutdown:
    def test_triggers_no_prestop(self):
        assert any(w.code == "REL009" for w in v('service a { image:"nginx:1.0" replicas:2 }').warnings)

    def test_no_trigger_with_prestop(self):
        src = 'service a { image:"nginx:1.0" replicas:2 lifecycle { preStop { exec: ["sleep","5"] } } }'
        assert not any(w.code == "REL009" for w in v(src).warnings)


class TestMultipleRulesAccumulate:
    def test_many_warnings(self):
        src = 'service api { image:"nginx:latest" replicas:6 }\ndatabase db { type:postgres replicas:2 ha:true }'
        r = v(src)
        codes = [w.code for w in r.warnings]
        assert "REL001" in codes and "REL002" in codes and "REL003" in codes and "REL004" in codes

    def test_warnings_dont_fail_valid(self):
        r = v('service api { image:"nginx:latest" replicas:6 }')
        assert r.is_valid and r.has_warnings

    def test_all_rel_have_hints(self):
        r = v('service api { image:"nginx:1.0" replicas:6 }\ndatabase db { type:postgres replicas:2 ha:true }')
        for w in r.warnings:
            if w.code and w.code.startswith("REL"):
                assert w.hint, f"{w.code} missing hint"
