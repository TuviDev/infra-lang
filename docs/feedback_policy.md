# Feedback & Telemetry Policy

## Status

- **Opt-in** — feedback is never sent unless the user enables it.
- **Default: OFF** — nothing is sent out of the box.

## Configuration

Feedback is controlled by a local config file. Precedence (highest wins):

1. Environment variables: `INFRA_FEEDBACK=1` (enable) / `INFRA_FEEDBACK_OFF=1`
   (disable, forces off).
2. Project config: `<project>/.infra-config.yaml` →
   `feedback: { enabled: true }`.
3. User config: `~/.config/infra/config.yaml` →
   `feedback: { enabled: true }`.
4. Default: off.

## Checking / toggling status

```bash
infra feedback        # show current status
infra feedback --on   # enable (writes project config)
infra feedback --off  # disable
```

## What is sent (when enabled)

- Product name and version (`infra-lang`, `0.1.0`).
- Error type (e.g. `InfraParseError`, `ValueError`).
- The operation that failed (e.g. `validate`, `compile`).
- A **fingerprint**: a stable, non-identifying hash of the error class.
- A **sanitized** message: file paths are replaced with `<path>`, and numbers
  are collapsed to `<n>`.

## What is NEVER sent

- Source code (`.infra` content) or excerpts of it.
- File paths.
- User name, hostname, environment variables, tokens, or any PII.
- Any config values.

## Failure isolation

A collector outage, timeout, bad response, or malformed config **never**
breaks the CLI, compilation, validation, or the LSP server. All feedback
failures are swallowed internally.

## Current operational note

The collector endpoint is not yet configured; enabling feedback is currently a
no-op until a collector URL is wired in. The fingerprinting and sanitization
logic is implemented and tested.
