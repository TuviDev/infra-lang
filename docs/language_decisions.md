# Language Design Decisions

This document records the design decisions of the Infra language. It is the
source of truth for *why* things look the way they do, and it governs what
counts as a change for future versions.

## Naming convention: snake_case for new fields

All fields *invented by Infra* use `snake_case`:

```
max_skew, min_available, target_cpu, allow_from, deny_from, spread_by
```

Fields that are *not* invented by Infra, but mirror a native Kubernetes field
name, keep the Kubernetes camelCase spelling for familiarity:

```
envFrom, mountPath, hostPath, startPeriod, initialDelay, readOnly,
postStart, preStop, accessMode, runsOn, continueOnError, restoreKeys,
cancelInProgress, serviceAccount
```

> Rationale: for constructs that map 1:1 to a Kubernetes field we reuse the
> well-known name so users don't have to translate; for anything Infra
> introduces we use snake_case to keep the core language consistent.

## Colon after block keywords: optional

A block keyword may be written with or without a colon. Both forms are
equivalent:

```
service api { ... }         # OK
service: api { ... }        # also OK
schedule { ... }            # OK
schedule: { ... }           # also OK
```

The colon is always optional after a block keyword.

## String quoting: double quotes preferred

```
image: "nginx:1.25"     # preferred
image: 'nginx:1.25'     # also valid
```

## Duration units: s/min/h/d/w

Use the unambiguous units. In a **time** context `m` is not accepted for
minutes (it collides with milli-cores in resource contexts); write `5min`.

```
timeout: 5min
retention: 30d
```

## Resource units: m/Mi/Gi/Ti/cores/MB/GB/TB

In a **resource** context, `m` means milli-cores (CPU).

```
cpu: 500m        # 500 milli-cores
memory: 128Mi
```

`m` is never "minutes" in a resource context.

## Template strings: backtick

```
image: `nginx:{TAG}`     # template — interpolates {TAG}
image: "nginx:1.25"      # literal string
```

## Wildcard in lists: "*" string

The wildcard inside a list is the string `"*"` (quoted), not a bare `*`:

```
deny_from: ["*"]         # correct
deny_from: [*]           # parser error
```

## Comment style: hash

```
# This is a comment      # preferred
/* block comment */      # also valid
```

## List of known stable features (v0.1.0)

The following features are final and part of the v0.1.0 contract. They will
not change in a backwards-incompatible way within v0.1.x:

- Structures: `service`, `database`, `cache`, `queue`, `storage`, `network`,
  `secret`, `config`, `pipeline`, `environment`, `cluster`
- `schedule`, `autoscale`, `disruption`, `network_policy`, `topology`,
  `affinity`
- `environment.extends`, `environment.quotas`
- Import system (`import` / `from ... import`)
- Template-string interpolation
- `const` / `let` declarations and `--var` injection
- The 4 backends (Kubernetes, Compose, Terraform, GitHub Actions)

## List of experimental features (may change)

These exist but their exact shape may change in a future minor version:

- `cluster` IAM blocks (`serviceAccount`, `role`) — output is structural only
- Terraform output details (module layout, provider wiring)
- `storage` `lifecycle` transition details
- Deeper `pipeline` features (matrix/parallel semantics)

## Deprecated (removed in next minor)

None in v0.1.0.
