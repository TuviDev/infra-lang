# CI Integration — `infra ci-comment` & the Infra Lang Check Action

`infra ci-comment` turns a changed `.infra` file into a pull-request-ready
report: resource changes, **monthly cost delta** and **security (SEC\*) /
reliability (REL\*) findings** — plus configurable pass/fail gates.

## CLI usage

```bash
# Markdown comment for the current file (absolute cost, no diff)
infra ci-comment infra/app.infra

# Diff against the base branch version of the same file
git show origin/main:infra/app.infra > /tmp/base.infra
infra ci-comment infra/app.infra --base /tmp/base.infra

# Gates: monthly budget + security
infra ci-comment infra/app.infra \
    --base /tmp/base.infra \
    --max-monthly-cost 500 \
    --fail-on-security

# Machine-readable output
infra ci-comment infra/app.infra --format json
```

**Exit codes** — `0`: report generated, all gates passed · `1`: gate failed
(or parse error) · `2`: usage error (Typer).

Rendering formats: `github-comment` (default, Markdown with an HTML marker so
bots update the same comment), `json`, `text` (plain ASCII for logs/email).

## GitHub Action (composite)

The repository ships a ready-to-use composite action at
`.github/actions/infra-check/`. Example workflow for a project that uses
infra-lang:

```yaml
name: infra-check
on:
  pull_request:

permissions:
  pull-requests: write   # let the action post/update the comment
  contents: read

jobs:
  infra:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0   # needed for the base-ref diff
      - uses: TuviDev/infra-lang/.github/actions/infra-check@v0.7.1
        with:
          files: "infra/**/*.infra"
          base-ref: origin/main
          max-monthly-cost: "500"
          fail-on-security: "true"
```

Inputs: `files` (glob(s), default `**/*.infra`), `base-ref`, `max-monthly-cost`,
`fail-on-security`, `version` (infra-lang from PyPI, default latest),
`github-token`. Output: `gate-passed`.

The step fails when a gate fails — but the PR comment is still posted
(`if: always()`), so the author sees *why*.

## Local run without GitHub

```bash
infra ci-comment app.infra --base base.infra > comment.md
gh pr comment 123 --body-file comment.md
```

## Related

- `infra policy-check` — declarative, repo-wide rules (budgets, no secrets in
  env, forbidden image tags); see `infra policy-check --help`.
- `infra alert` — Slack/Teams/Discord notifications for drift, cost overruns
  and security violations.
