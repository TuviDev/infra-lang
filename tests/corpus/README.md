# Corpus

A representative set of `.infra` files used to verify behavior across a range
of inputs. `tests/test_corpus.py` runs these.

```
corpus/
├── minimal/     must parse and validate
├── realistic/   must parse, validate and compile to Kubernetes
├── edge_cases/  must not crash (parse or InfraParseError)
└── invalid/     must fail with specific error codes
```

Every file starts with two comment lines:

- `# Purpose: <what this file exercises>`
- `# Expected: <parse/validate pass, or a specific error code>`
