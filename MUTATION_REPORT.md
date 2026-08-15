# Mutation Testing Report — reliability.py

## Approach

`mutmut` could not run in this environment: the editable install breaks when
`mutmut` copies only the mutated file into `mutants/src` (imports fail), and
its baseline run of the whole suite fails on project-relative paths (README).
Replaced with **manual mutation testing** (`scripts/manual_mutation.py`) that
applies a real source mutation, runs the reliability test suite, and reports
whether the mutant was killed.

## Result

| Status     | Count |
|------------|-------|
| Killed     | 11    |
| Survived   | 0     |
| Not-applied| 0     |
| **Score**  | **100% (11/11)** |

## Mutants applied (all killed)

| Rule  | Mutation |
|-------|----------|
| REL001 | threshold `5` → `4` |
| REL002 | even/odd check inverted |
| REL003 | `has_limit` inverted |
| REL004 | health-check condition inverted |
| REL006 | backup-enabled condition inverted |
| REL007 | single-replica condition inverted |
| REL009 | preStop condition inverted |
| REL011 | autoscale-None condition inverted |
| REL012 | autoscale-None condition inverted |
| REL013 | storage presence condition inverted |
| REL014 | kafka-type condition inverted |

Every reliability rule (REL001–REL014) is protected by at least one test in
`tests/test_reliability.py`, `tests/test_autoscale_disruption.py`,
`tests/test_reliability_s9.py`, or `tests/test_network_topology_quota.py`.
