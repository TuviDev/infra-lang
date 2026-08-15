---
name: Parser bug
about: Valid syntax that fails to parse
labels: bug, parser
---

> This template is for **valid** Infra syntax that the parser rejects, or for
> an error message that is unclear. If you are not sure the syntax is valid,
> please use the general "Bug report" template instead.

## Input that fails
```infra
(paste the smallest .infra block that fails to parse)
```

## Error message
(paste the full error output, including `error[PARSE]`)

## Expected behavior
- [ ] This should parse successfully
- [ ] The error message should be clearer (explain why)

## infralang version & environment
- version: (run `infra --version`)
- OS / Python:

## Steps to reproduce
1.
2.
