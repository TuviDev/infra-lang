# Architecture Insight Reports (`infra explain`)

`infra explain` answers "what did I just write?" for any `.infra` file. It
combines the existing static analyzers (cost, security, reliability,
validator) into one deterministic report — **no AI/ML runtime, no network,
no LLM calls**. Every sentence comes from a fixed template, so identical
input always produces identical output.

## Usage

```bash
infra explain app.infra                              # markdown, all sections
infra explain app.infra --format text                # terminal plain text
infra explain app.infra --format json --for ai       # machine consumption
infra explain app.infra --sections overview,cost
infra explain app.infra -o report.md                 # write to file
infra explain app.infra -e prod --var region=eu      # overlays & var overrides
```

| Option | Default | Meaning |
|--------|---------|---------|
| `--format` | `markdown` | `text` / `json` / `markdown` |
| `--for` | `human` | `human` (prose) / `ai` (compact JSON) |
| `--sections` | `all` | comma-separated subset of the sections below |
| `-o, --output` | stdout | write the report to a file |
| `-e, --environment` | base | environment overlay to analyze |
| `--var` | — | `key=value` variable override (repeatable) |

## The seven sections

1. **Overview** — resource counts, detected technologies, architecture type
   heuristic (monolithic / microservices / event-driven / CI/CD-first) and
   the top-3 monthly cost drivers.
2. **Services** — one row per service: image, replicas, ports, monthly cost
   and an **A–F reliability grade** computed from the REL\* findings.
3. **Dependencies** — the `depends` graph plus **single points of failure**
   (blocks with ≤1 replica and ≥2 dependents).
4. **Cost Breakdown** — per-category totals (compute / storage / network /
   managed) with per-resource detail.
5. **Security Warnings** — every SEC\* finding with its severity.
6. **Reliability Report** — every REL\* finding, labelled by impact
   (high/medium/low).
7. **What-If** — deterministic scenarios: zone-failure blast radius and the
   cost delta of doubling every replica count.

## AI-optimized output (`--for ai`)

With `--for ai` the report is a single compact JSON document:

```json
{
  "_meta": {
    "language": "infra",
    "generator_version": "0.9.0",
    "checksum": "…",
    "timestamp": "…"
  },
  "_summary": ["3 services, 1 database, …", "…"],
  "sections": { "overview": {…}, "services": […], … }
}
```

- `_summary` contains 3–5 template-generated sentences — safe to feed to an
  LLM as grounding context without any probabilistic generation on our side.
- `timestamp` is derived from the input file's **mtime** (not the wall
  clock), so an unchanged file yields a byte-identical report.

## Web Playground

The [playground](https://TuviDev.github.io/infra-lang/) has a
**🧠 Insight Report** tab that renders the same markdown entirely in the
browser (Pyodide / WebAssembly). Programmatically:

```python
from infra import web_api

result = web_api.generate_explain_report(source, format="markdown")
# {"success": True, "format": "markdown", "report": "# Architecture …", "errors": []}
```

`generate_explain_report` never raises: parse/validation problems come back
as `{"success": False, "errors": [...]}`.

## Editor CodeLens badges

Inside VS Code the language server projects the same numbers inline — one
badge per `service` / `database` / `cache` / `queue` / `storage` /
`environment` block:

```
💰 $47.20/mo · ⚡ 3 replicas · 🔒 2 warnings · 📊 Grade: A
💾 20Gi · 💰 $89.00/mo · 🔒 Backup: enabled
🌍 5 services · 💰 $421.50/mo total · 🎯 Target: kubernetes
```

Settings (all default `true`, plus `infra.codelens.showEmoji` = `auto`):

```jsonc
{
  "infra.codelens.enabled": true,
  "infra.codelens.showCost": true,
  "infra.codelens.showSecurity": true,
  "infra.codelens.showReliability": true
}
```

Set `showEmoji` to `false` for ASCII badges (`[$] 47.20/mo | [R] 3 replicas
| [!] 2 warnings | [G:A]`). Hover cards include a "💡 Insight" section with
the same per-block summary.
