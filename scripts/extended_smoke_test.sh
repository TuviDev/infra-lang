#!/bin/bash
set -e

echo "=== Extended Smoke Test ==="
echo "Testing all backend × feature combinations"

PASS=0
FAIL=0

check() {
    local desc="$1"
    shift
    if "$@" > /dev/null 2>&1; then
        echo "  PASS: $desc"
        PASS=$((PASS+1))
    else
        echo "  FAIL: $desc"
        FAIL=$((FAIL+1))
    fi
}

# Basic compilation
for backend in kubernetes compose; do
    for example in examples/0*.infra; do
        check "compile $backend $(basename "$example")" \
          infra compile "$example" \
          --target "$backend" --dry-run
    done
done

# Validation
check "validate examples" \
  infra validate examples/01_hello_world.infra

check "validate catches SEC001" \
  bash -c 'echo "service s { image: \"nginx:1.25\" env { PASSWORD: \"bad\" } }" | \
    infra validate /dev/stdin; test $? -eq 1'

# Graph
for fmt in ascii dot mermaid; do
    check "graph --format $fmt" \
      infra graph examples/02_web_app.infra \
      --format "$fmt"
done

# Docs
check "docs markdown" \
  infra docs examples/02_web_app.infra

# Diff
check "diff identical" \
  infra diff examples/01_hello_world.infra \
  examples/01_hello_world.infra

# Fmt
check "fmt check" \
  infra fmt examples/01_hello_world.infra --check

# Init
check "init basic" \
  bash -c 'cd /tmp && infra init smoke-test-$$ --yes && \
    rm -rf smoke-test-$$'

echo ""
echo "Results: $PASS passed, $FAIL failed"
test "$FAIL" -eq 0
