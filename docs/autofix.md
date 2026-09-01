# Auto-Fix (`infra doctor --fix`)

`infra doctor --fix` rewrites your `.infra` file **in place**, applying
deterministic fixes for the most common security and reliability findings.
It is a source-to-source transformation built on the same validated parser
and printer the compiler uses — **round-trip safe**: the parts of the file
it does not touch print back byte-stable.

## Usage

```bash
infra doctor app.infra --fix               # apply all fixes (+ app.infra.bak)
infra doctor app.infra --dry-run           # colored unified diff, writes nothing
infra doctor app.infra --fix --only SEC001,REL003
infra doctor app.infra --fix --no-backup   # skip the .bak file (use git instead)
infra doctor app.infra --dry-run --only REL006
```

| Flag | Meaning |
|------|---------|
| `--fix` | rewrite the file in place (creates `file.infra.bak` first) |
| `--dry-run` | print the unified diff (`+` green / `-` red), change nothing |
| `--only CODES` | restrict to a comma-separated subset of fixable codes |
| `--no-backup` | skip the backup file when applying fixes |
| `--default-memory 1Gi` | memory limit injected by the REL003 rule (default `512Mi`) |

Exit codes: `0` on success (including "nothing to fix"), `1` for a missing
file, a parse error, an invalid `--only` code or an invalid
`--default-memory` value.

## The six rules

| Code | Trigger | Applied fix |
|------|---------|-------------|
| **SEC001** | hardcoded secret-looking env value (e.g. `DB_PASSWORD: "hunter2"`) | value replaced by `from secret "auto_secrets".DB_PASSWORD`; a `secret_store "auto_secrets"` block is prepended once per file |
| **SEC003** | mutable image tag (`:latest`, `:edge`, …) | an inline comment is appended: `# FIXME: pin to a specific version, e.g., :1.25.3 …` — the rule **never guesses a version** |
| **REL003** | no memory limit/request on a service | `resources { limits { memory: 512Mi } }` injected (existing `cpu`/`requests` keys are preserved) |
| **REL004** | no health check *and* no probes | `health http("/health") { interval: 30s timeout: 5s }` injected — services **without a port are skipped** (no endpoint to pick) and reported |
| **REL006** | database without an enabled backup | `backup { enabled: true schedule: "0 2 * * *" retention: 7d }` injected; an existing but disabled backup is re-enabled with its schedule preserved |
| **REL009** | `replicas: 2+` without a graceful shutdown hook | `lifecycle { preStop { exec: ["sleep", "5"] } }` injected (an existing `postStart` hook is preserved) |

Rules are keyed to the same codes `infra validate` reports, so a typical
session is:

```bash
infra validate app.infra            # 1 error, 4 warnings
infra doctor app.infra --fix        # fixed 5 finding(s) in app.infra
infra validate app.infra            # clean
```

## Safety properties

- **Idempotent** — running `--fix` twice produces no further changes
  (the SEC003 comment is only appended when the line does not have it yet).
- **Backup-first** — the original file is always copied to
  `<name>.infra.bak` unless you pass `--no-backup`.
- **Syntax-verified** — the rewritten source is re-printed from the parsed
  AST, so a fix can never produce an unparseable file.
- **Deterministic** — no clocks, no randomness, no network: same input,
  same output.

## CI recipe

Use `--dry-run` in pull-request checks to suggest fixes without writing:

```yaml
- name: Suggest infra auto-fixes
  run: infra doctor app.infra --dry-run || true
```
