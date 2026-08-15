# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x | Yes |

## Reporting a vulnerability

Please do **not** open a public GitHub issue for security vulnerabilities.

Instead:

1. Use GitHub Security Advisories (Security tab → Report a vulnerability)
2. Or email: security@infra-lang.dev (placeholder)

Include in your report:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (optional)

## Response time

We aim to respond within **7 days** and publish a fix within **30 days**.

## Scope

### In scope
- Parser crashes on malicious input
- Secret detection bypass
- Code injection through template strings
- Privilege escalation in generated K8s YAML

### Out of scope
- Issues in generated YAML that depend on cluster misconfiguration
- Third-party dependencies (report upstream)
