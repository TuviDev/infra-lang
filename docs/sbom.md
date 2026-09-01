# SBOM Generation (`infra sbom`)

`infra sbom` builds a **Software Bill of Materials** from the container
images your `.infra` file already declares. It works fully offline and is
deterministic — the same file always yields the same SBOM, which makes the
output safe to commit, sign and diff.

## Usage

```bash
infra sbom app.infra                              # markdown table + badges
infra sbom app.infra --format text                # plain text
infra sbom app.infra --format spdx-json           # SPDX 2.3 JSON
infra sbom app.infra --format cyclonedx-json      # CycloneDX 1.5 JSON
infra sbom app.infra -o sbom.spdx.json            # write to file
infra sbom app.infra --include-transitive         # + best-effort base images
infra sbom app.infra --registry-check             # + availability column
```

## Where components come from

| Block | SBOM entry |
|-------|-----------|
| `service` with `image:` | the image reference itself |
| `service` with `build:` only | skipped — there is no registry artifact to list |
| `database` | `​<type>:<version>` (e.g. `postgres:16`; `latest` when no version) |
| `cache` | `​<type>:<version>` (e.g. `redis:7`) |
| `queue` | `​<type>:<version>` (e.g. `rabbitmq:3.13`) |
| `storage` | a managed-service pseudo-component (`managed/s3`) — always ZERO risk |

Identical images used by several blocks are merged into one component with
all sources listed (`service web, service edge`).

## Tag-mutability risk scoring

| Class | Meaning | Examples |
|-------|---------|----------|
| 🔴 `[!] HIGH` | mutable tag — content can change under your feet | `:latest`, `:edge`, `:nightly`, `:dev`, no tag |
| 🟡 `[~] LOW` | pinned tag | `:1.25.3`, `:16`, `:bookworm` |
| 🟢 `[OK] ZERO` | pinned by digest | `app@sha256:…` |

## Formats

### SPDX 2.3 (`--format spdx-json`)

A spec-shaped JSON document: `SPDXRef-DOCUMENT` with `DESCRIBES`
relationships to one package per component, purl external references,
deterministic `documentNamespace` (derived from the source checksum) and a
`tag-risk` comment per package. Transitive base images appear as
`DEPENDS_ON` relationships.

### CycloneDX 1.5 (`--format cyclonedx-json`)

`container` components (storage blocks are `platform`) with `purl`
bom-refs, `infra:source` / `infra:tag-risk` / `infra:transitive`
properties, and a `dependencies` section for base images. The
`serialNumber` is a deterministic name-based (v5) UUID.

### Markdown / text

A reviewable table with risk badges — ideal for PRs and release notes.

## Transitive base images (`--include-transitive`)

Best-effort expansion using the bundled `base_images.json` (~50 mappings,
e.g. `nginx → alpine:3.20`, `postgres → debian:bookworm`). It is a curated
approximation, not a registry lookup — real base layers depend on how the
image was built, so treat transitive entries as "likely bases" during
triage. Unknown images simply get no transitive entry.

## Registry availability (`--registry-check`)

Adds a best-effort liveness probe per image (Docker Hub tag API for
`docker.io`, the standard `/v2/<name>/manifests/<ref>` endpoint elsewhere)
and a `Registry check` column in markdown/text output. Statuses: `found`,
`missing`, `unknown`. Digest-pinned images on Docker Hub are always
`unknown` (the public tag API cannot resolve digests). CI tests mock the
network entirely; the probe never fails the command.

## CI recipe

```yaml
- name: Generate SBOM
  run: |
    infra sbom deploy/app.infra --format spdx-json -o sbom.spdx.json
    infra sbom deploy/app.infra --format cyclonedx-json -o sbom.cdx.json
- uses: actions/upload-artifact@v4
  with:
    name: sbom
    path: sbom.*.json
```
