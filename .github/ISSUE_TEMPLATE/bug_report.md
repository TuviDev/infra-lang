---
name: Bug report
about: Something doesn't work correctly
labels: bug
---

> Tip: provide the **minimal** `.infra` file that reproduces the problem.
> Do not paste secrets or access tokens — redact anything sensitive.

## Environment
- infra-lang version: (run `infra --version`)
- Python version: (run `python --version`)
- OS: (e.g. macOS 14, Ubuntu 22.04, Windows 11)
- Target backend: (kubernetes / compose / terraform / github)
- How installed: (pip / pip 'infra-lang[lsp]' / from source / VS Code extension)

## Category (pick one)
- [ ] Parser
- [ ] Compiler / backend
- [ ] Linter (SEC/REL)
- [ ] LSP / VS Code
- [ ] CLI
- [ ] Documentation
- [ ] Other

## Input (.infra file) — minimal repro
```infra
(paste the smallest .infra that reproduces the issue)
```

## Command used
(e.g. `infra validate app.infra`, `infra compile app.infra --target kubernetes`,
`infra lsp`, ...)

## Expected output
What did you expect to happen?

## Actual output
What actually happened? (paste the full output / error message)

## Reproducibility
- [ ] Always happens
- [ ] Intermittent (how often?)

## Steps to reproduce
1.
2.
3.
