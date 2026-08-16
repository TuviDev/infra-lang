"""Manual mutation testing for reliability.py.

Applies small mutations (flip conditions / change operators) to
src/infra/analyzer/reliability.py, runs the reliability test suite, and
reports whether each mutant was *killed* (tests failed -> good) or *survived*
(tests passed -> coverage gap).

Usage: python scripts/manual_mutation.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SRC = Path("src/infra/analyzer/reliability.py")
TESTS = [
    "tests/test_reliability.py",
    "tests/test_autoscale_disruption.py",
    "tests/test_reliability_advanced.py",
    "tests/test_network_topology_quota.py",
]

# (description, find, replace) — each should change behaviour in a way tests
# should detect.
MUTATIONS = [
    # flip REL001 replica threshold
    ("REL001 threshold 5->4", "if _replicas(svc) < 5:", "if _replicas(svc) < 4:"),
    # REL002 odd/even flip
    ("REL002 %2 !=0 -> ==0", "if replicas % 2 != 0:", "if replicas % 2 == 0:"),
    # REL003 has_limit -> not has_limit
    ("REL003 invert has_limit", "if has_limit:", "if not has_limit:"),
    # REL004 health check flip
    ("REL004 invert health", "if svc.health is not None or svc.probes is not None:",
     "if svc.health is None or svc.probes is None:"),
    # REL006 backup flip
    ("REL006 backup enabled flip", "if db.backup is not None and db.backup.enabled:",
     "if db.backup is None or not db.backup.enabled:"),
    # REL007 replica ==1 -> !=1
    ("REL007 replicas !=1 -> ==1", "if _replicas(svc) != 1:", "if _replicas(svc) == 1:"),
    # REL009 pre_stop check flip
    ("REL009 pre_stop flip", "if svc.lifecycle is not None and svc.lifecycle.pre_stop is not None:",
     "if svc.lifecycle is None or svc.lifecycle.pre_stop is None:"),
    # REL011 autoscale None flip
    ("REL011 autoscale None flip", "if svc.autoscale is None:", "if svc.autoscale is not None:"),
    # REL012 autoscale None flip
    ("REL012 autoscale None flip", "if svc.autoscale is None:\n            return []\n        if svc.replicas",
     "if svc.autoscale is not None:\n            return []\n        if svc.replicas"),
    # REL013 storage flip
    ("REL013 storage flip", "if db.size is not None or db.storage is not None:",
     "if db.size is None and db.storage is None:"),
    # REL014 kafka type flip
    ("REL014 kafka type flip", 'if queue.type != "kafka":', 'if queue.type == "kafka":'),
]


def run_tests() -> bool:
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
           "--no-cov", "-o", "addopts="] + TESTS
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.returncode == 0


def main() -> int:
    original = SRC.read_text()
    killed = survived = 0
    results = []
    for desc, find, repl in MUTATIONS:
        if find not in original:
            results.append((desc, "NOT-APPLIED", 0.0))
            continue
        mutated = original.replace(find, repl, 1)
        if mutated == original:
            results.append((desc, "NOT-APPLIED", 0.0))
            continue
        SRC.write_text(mutated)
        tests_pass = run_tests()
        SRC.write_text(original)
        if tests_pass:
            survived += 1
            results.append((desc, "SURVIVED", 0.0))
        else:
            killed += 1
            results.append((desc, "KILLED", 100.0))
    SRC.write_text(original)

    print(f"\nMutation testing on {SRC}")
    print("=" * 60)
    for desc, status, _ in results:
        print(f"  {status:10} {desc}")
    total = killed + survived
    score = (killed / total * 100) if total else 0
    print("=" * 60)
    print(f"Killed: {killed}  Survived: {survived}  Not-applied: {len(results) - total}")
    print(f"Mutation score: {score:.1f}%  ({killed}/{total})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
