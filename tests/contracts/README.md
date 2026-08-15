# Contract Tests

These are the public syntax contracts. They guarantee that every code example
in the public documentation parses and validates correctly.

`tests/test_contracts.py` extracts every ````infra``` block from the public
docs at runtime and parses each one. If any block fails to parse, the docs
are inconsistent with the parser and the test fails — **do not weaken the
test; fix the docs or the parser.**

## Layout

```
tests/contracts/
├── README.md
├── from_readme.infra      (blocks extracted from README.md)
├── from_tutorial.infra    (blocks extracted from docs/tutorial.md)
├── from_examples/         (copies of the examples/ files)
└── from_spec.infra        (blocks extracted from docs/language_spec.md)
```

These files are conveniences / documentation of the contracts. The actual
enforcement is done live by `tests/test_contracts.py` reading the docs
directly, so the contract can never silently drift out of sync.
