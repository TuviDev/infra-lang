# Installation commands

## Basic
```bash
pip install infra-lang
```

## With LSP support (recommended for VS Code)
```bash
pip install 'infra-lang[lsp]'
```

## Verify
```bash
infra --version
infra --help
```

## Quick smoke test
```bash
echo 'service hello { image: "nginx:1.25" port: 80 }' > /tmp/hello.infra
infra validate /tmp/hello.infra
infra compile /tmp/hello.infra --target kubernetes --dry-run
```
