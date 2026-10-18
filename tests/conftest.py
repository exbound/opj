"""Test fixtures: locate publicly published Origin samples by provenance.

Samples are not committed (see tests/fixtures/README.md). Point the env var
``OPJ_FIXTURES`` at a directory holding them, or drop them in
``tests/fixtures/_local/``. Sample-dependent tests skip when a file is absent.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_SEARCH_DIRS = [
    Path(os.environ["OPJ_FIXTURES"]) if os.environ.get("OPJ_FIXTURES") else None,
    Path(__file__).parent / "fixtures" / "_local",
]


def _find(name: str) -> Path | None:
    for d in _SEARCH_DIRS:
        if d is None:
            continue
        p = d / name
        if p.is_file():
            return p
    return None


def require(name: str) -> Path:
    p = _find(name)
    if p is None:
        pytest.skip(f"fixture {name!r} not available (set OPJ_FIXTURES)")
    return p


@pytest.fixture
def sample_data() -> Path:
    return require("SampleData.opju")


@pytest.fixture
def fit_summation() -> Path:
    return require("FitSummation.opju")
