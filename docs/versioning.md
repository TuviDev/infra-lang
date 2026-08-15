# Versioning Policy

## Semantic Versioning

Infra Lang follows [Semantic Versioning 2.0.0](https://semver.org/).

```
MAJOR.MINOR.PATCH
```

- **MAJOR**: breaking changes to `.infra` syntax
- **MINOR**: new features, backward compatible
- **PATCH**: bug fixes, backward compatible

## What counts as a breaking change

- Removing a keyword or block type
- Changing required fields to produce errors
- Changing output format in an incompatible way
- Changing CLI flags

## What is NOT a breaking change

- Adding new optional fields
- Adding new linter rules (warnings only)
- Adding new backends
- Improving error messages
- Adding new stdlib functions

## Version 0.x (current)

During 0.x, MINOR version may include breaking changes with a deprecation
notice.

## Deprecation process

1. Feature marked deprecated in CHANGELOG
2. Warning emitted for 2 minor releases
3. Removed in the next minor release

## What is the public contract

The following are considered the public API / contract of Infra Lang:

- The `.infra` language: top-level blocks and their fields, the grammar, the
  meaning of validated constructs (see `language_spec.md`).
- The compiled output for the supported backends (Kubernetes/Compose/Terraform/
  GitHub Actions), as documented in `support_matrix.md`.
- The CLI command names and their stable flags.
- Linter error/warning codes (SEC/REL) and their severities.

## What users can expect

- **Patch** (0.1.x): bug fixes only; no new syntax, no behavior changes that
  could break existing `.infra` files or output.
- **Minor** (0.x): new features and new optional fields; existing valid files
  keep working. During 0.x, breaking changes are allowed only with a
  deprecation notice (see below).
- **Major** (1.0+): may include breaking changes to syntax or output.

## What may change faster

- LSP behavior and editor integration (still stabilizing).
- Terraform output details (structural; module layout may change).
- Experimental / non-stable features listed in `language_decisions.md`.

## Current deprecations

None in v0.1.0
