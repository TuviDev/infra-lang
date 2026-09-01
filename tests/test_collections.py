"""Regression tests: transformer must aggregate collections into tuples.

These target the most common transformer bug — returning the first element
instead of the whole collection.
"""

from __future__ import annotations

from infra.parser import Parser

P = Parser()


def first(src: str):
    program = P.parse(src, "c.infra")
    for s in program.statements:
        if getattr(getattr(s, "location", None), "file", "") != "<prelude>":
            return s
    return program.statements[0]


def assert_tuple_len(node, attr, expected):
    value = getattr(node, attr)
    assert isinstance(value, tuple), (
        f"{attr} should be a tuple, got {type(value).__name__}"
    )
    assert len(value) == expected, f"{attr} length {len(value)} != {expected}"


class TestServiceCollections:
    def test_ports_multiple(self):
        svc = first('service a { image: "x" port 80 port 443 }')
        assert_tuple_len(svc, "ports", 2)

    def test_volumes_multiple(self):
        svc = first(
            'service a { image: "x" volumes { v1: { mountPath: "/a" } v2: { mountPath: '
            '"/b" } } }'
        )
        assert_tuple_len(svc, "volumes", 2)

    def test_depends_multiple(self):
        svc = first('service a { image: "x" depends: ["db", "cache", "queue"] }')
        depends = getattr(svc, "depends")
        assert len(depends) == 3


class TestDatabaseCollections:
    def test_users_multiple(self):
        db = first('database d { type: postgres users { a: "1" b: "2" } }')
        assert_tuple_len(db, "users", 2)


class TestQueueCollections:
    def test_topics_multiple(self):
        q = first(
            "queue q { type: kafka topics { t1: { partitions: 1 } t2: { partitions: 2 "
            "} "
            "t3: { partitions: 3 } } }"
        )
        assert_tuple_len(q, "topics", 3)

    def test_users_multiple(self):
        q = first('queue q { type: rabbitmq users { a: "1" b: "2" } }')
        assert_tuple_len(q, "users", 2)


class TestPipelineCollections:
    def test_stages_multiple(self):
        pl = first(
            'pipeline p { stages { a: { steps { s: { run: "1" } } } b: { steps { s: { '
            'run: "2" } } } } }'
        )
        assert_tuple_len(pl, "stages", 2)

    def test_stage_steps_multiple(self):
        pl = first(
            'pipeline p { stages { t: { steps { a: { run: "1" } b: { run: "2" } c: { '
            'run: "3" } } } } }'
        )
        assert_tuple_len(pl.stages[0], "steps", 3)


class TestClusterCollections:
    def test_node_pools_multiple(self):
        c = first("cluster c { provider: aws nodes { w1: { min: 1 } w2: { min: 2 } } }")
        assert_tuple_len(c, "nodes", 2)


class TestNetworkCollections:
    def test_subnets_multiple(self):
        net = first(
            'network n { cidr: "10.0.0.0/16" subnets { a: { cidr: "1" } b: { cidr: "2" '
            '} } }'
        )
        assert_tuple_len(net, "subnets", 2)


class TestTriggerCollections:
    def test_branches(self):
        pl = first(
            'pipeline p { trigger { branches: ["a", "b"] } stages { t: { steps { s: { '
            'run: "x" } } } } }'
        )
        assert pl.trigger.branches == ("a", "b")

    def test_paths(self):
        pl = first(
            'pipeline p { trigger { paths: ["x", "y", "z"] } stages { t: { steps { s: '
            '{ '
            'run: "x" } } } } }'
        )
        assert pl.trigger.paths == ("x", "y", "z")
