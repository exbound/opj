# opj — standard command interface (Python toolchain).

venv := ".venv"
py := venv / "bin" / "python"

# List available recipes
default:
    @just --list

# Create the virtualenv and install the package with dev dependencies
setup:
    python3 -m venv {{venv}}
    {{py}} -m pip install --upgrade pip
    {{py}} -m pip install -e ".[dev]"

# Format sources in place
fmt:
    {{py}} -m ruff format src tests

# Lint
lint:
    {{py}} -m ruff check src tests

# Format-check + lint + typecheck — the CI equivalent
check:
    {{py}} -m ruff format --check src tests
    {{py}} -m ruff check src tests
    {{py}} -m mypy

# Run the test suite
test:
    {{py}} -m pytest -q
