.PHONY: install dev test lint fmt build clean coverage watch

# Install production dependencies
install:
	pip install -e .

# Install development dependencies
dev:
	pip install -e ".[dev,watch,e2e]"

# Run the full test suite with coverage
test:
	pytest

# Run coverage report as HTML
coverage:
	pytest --cov=src/infra --cov-report=html --cov-report=term
	@echo "HTML report in htmlcov/index.html"

# Lint with ruff
lint:
	ruff check src tests

# Format with black
fmt:
	black src tests
	ruff check --fix src tests

# Build wheel and sdist
build:
	python -m build

# Clean build artifacts
clean:
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true

# Watch tests
watch:
	pytest-watch
