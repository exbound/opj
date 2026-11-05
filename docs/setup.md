# Setup

Toolchain prerequisites and the local development loop.

## Prerequisites

- Python 3.11+
- [`just`](https://github.com/casey/just) for the task recipes

## First-time setup

```sh
just setup
```

Creates a virtualenv in `.venv/` and installs the package editable with its dev
dependencies (`pytest`, `ruff`, `mypy`).

## Development loop

```sh
just fmt      # format with ruff
just check    # ruff format --check + ruff lint + mypy (strict)
just test     # pytest
```

Run `just check && just test` before every commit; CI runs the same.

## Test fixtures

Sample-dependent tests need publicly published Origin files, referenced by
provenance and **not** committed (see `tests/fixtures/README.md`). Point the
tests at a local directory holding them:

```sh
OPJ_FIXTURES=/path/to/samples just test
```

or drop the files in `tests/fixtures/_local/`. Without fixtures present, the
sample-dependent tests skip and the synthetic tests still run.

`tests/test_corpus.py` reproduces the specification's corpus figures. It skips
unless `OPJ_CORPUS` names a flat directory holding the 98 files listed in
`docs/engineering-spec.md` §3:

```sh
OPJ_CORPUS=/path/to/corpus just test
```
