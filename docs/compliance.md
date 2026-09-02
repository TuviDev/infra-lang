# Compliance: SOC 2 & CIS Audits (since 1.0.0)

`infra compliance` turns the existing static analyzers into an
**audit-readable compliance report** — each control of the selected standard
is listed as passed or violated, with the norm ID, the triggering SEC*/REL*
error codes, file locations and fix recommendations, plus an overall
Compliance Score.

## Quickstart

```bash
infra compliance app.infra                        # all standards, text report
infra compliance app.infra --standard soc2        # SOC 2 only
infra compliance app.infra -s cis -f markdown -o audit.md
infra compliance app.infra -f json | jq .score    # machine-readable
```

Exit code: `0` when every control passes, `1` when anything is violated —
drop it straight into CI.

## Control mappings

### SOC 2 (Trust Services Criteria)

| Control | Theme | Triggered by |
|---|---|---|
| CC6.1 | Logical access: secrets & privileges | `SEC001`, `SEC004` |
| CC6.3 | Immutable build artifacts | `SEC003` |
| CC7.1 | Configuration baseline & encryption | `SEC003`, `SEC006` |
| CC7.2 | Incident detection / health monitoring | `REL004` |
| A1.1  | Availability & resource limits | `REL001`, `REL002`, `REL003` |

### CIS Kubernetes Benchmark v1.8

| Control | Theme | Triggered by |
|---|---|---|
| 5.1.1 | No containers as root | `SEC005` |
| 5.2.1 | Minimize privileged containers | `SEC004` |
| 5.2.4 | Read-only root filesystem | direct check: `security { read_only_root_filesystem: true }` |
| 5.2.5 | No privilege escalation | `SEC004` (privileged mode implies escalation) |
| 5.7.3 | NetworkPolicy for public services | direct check: exposed services need `network_policy { … }` |

Controls 5.2.4 and 5.7.3 have no SEC*/REL* code yet, so they are evaluated
directly against your service definitions.

## Compliance Score

```
Compliance score: 80.0% (8/10 controls passed)
```

`score = passed / total * 100` over the evaluated standard's controls.

## Report formats

- **text** (default) — `[PASS]`/`[FAIL]` per control, violations with
  `code`, location and `fix:` line; readable in any CI log.
- **markdown** — header with score, control table and a `## Violations`
  section; ready for wikis and PR attachments.
- **json** — `{file, standard, score, controls_*, results[]}` with nested
  violations; stable shape for dashboards.

## Example (text)

```
[FAIL] CC7.2 Incident detection — health monitoring of services (1 violation)
    - [REL004] Service 'api' has no health checks. @ app.infra:1:1
      fix: Add health http("/health")
```

## What compliance is NOT

This is a static pre-flight signal, not a certification: it maps your DSL
definitions to well-known control themes so an auditor can review evidence
alongside the report. Runtime posture still needs cluster-level scanners.
