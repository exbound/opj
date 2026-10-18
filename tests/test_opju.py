"""Tests for the .opju reader: synthetic framing plus real-sample validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from opj import Header, OpjError, parse, read
from opj.opju import (
    ROOT_HEADER_CONST,
    iter_header_records,
    parse_header,
    scan_named_records,
)


def _string_record(tag: int, text: bytes) -> bytes:
    return bytes((tag, len(text))) + text


def _synthetic() -> bytes:
    """A minimal well-formed .opju-shaped buffer."""
    header = b"CPYUA 1.0 3\n"
    root = bytes((0x3E, 0x01, 0x6C, 0xC0, 0x11, 0x01, 0x02))
    body = b"".join(
        [
            bytes((0x80, 0x01, 0x0C)),  # short scalar
            _string_record(0x82, b"Book1_A"),  # a name
            bytes((0x85, 0x02, 0xF0, 0x3F)),  # 2-byte scalar
            b"\x1c\x00\x00\x00",  # opaque region begins (non-value tag)
        ]
    )
    return header + root + body


# --- header ---------------------------------------------------------------


def test_parse_header_fields() -> None:
    h = parse_header(_synthetic())
    assert isinstance(h, Header)
    assert h.magic == "CPYUA"
    assert h.format_version == "1.0"
    assert h.object_count == 3
    assert h.body_offset == len(b"CPYUA 1.0 3\n")


def test_parse_header_rejects_non_origin() -> None:
    with pytest.raises(OpjError):
        parse_header(b"not an origin file\n")


def test_parse_header_rejects_truncated() -> None:
    with pytest.raises(OpjError):
        parse_header(b"CPYUA 1.0 3 no newline")


# --- root header + stream --------------------------------------------------


def test_root_header_constant_bytes() -> None:
    doc = parse(_synthetic())
    assert len(doc.root_header) == 7
    assert doc.root_header[2:6] == ROOT_HEADER_CONST


def test_header_records_walk_stops_at_opaque() -> None:
    data = _synthetic()
    h = parse_header(data)
    recs = list(iter_header_records(data, h.body_offset + 7))
    # three value records, then the 0x1c opaque byte stops the walk
    assert [r.tag for r in recs] == [0x80, 0x82, 0x85]
    assert recs[1].text == "Book1_A"
    assert recs[2].length == 2


def test_gap_recorded_for_opaque_region() -> None:
    doc = parse(_synthetic())
    assert doc.gaps, "an opaque region should be recorded"


# --- global name scan ------------------------------------------------------


def test_scan_recovers_name() -> None:
    data = _synthetic()
    recs = scan_named_records(data, 0)
    assert "Book1_A" in [r.text for r in recs]


def test_scan_ignores_short_runs() -> None:
    # a 3-char printable run with a valid-looking tag/len must not be accepted
    data = b"CPYUA 1.0 0\n" + bytes((0x3E, 0x01, 0x6C, 0xC0, 0x11, 0x01, 0x02))
    data += bytes((0x82, 0x03)) + b"abc"
    recs = scan_named_records(data, 0, min_len=4)
    assert recs == []


# --- real-sample validation (skips without fixtures) -----------------------


def test_sampledata_inventory(sample_data: Path) -> None:
    doc = read(sample_data)
    assert doc.header.magic == "CPYUA"
    assert doc.header.object_count > 0
    assert doc.root_header[2:6] == ROOT_HEADER_CONST
    names = set(doc.name_strings)
    assert {"Book1_A", "Book1_B", "Book1_C"} <= names


def test_fitsummation_inventory(fit_summation: Path) -> None:
    doc = read(fit_summation)
    assert doc.header.magic == "CPYUA"
    names = set(doc.name_strings)
    assert {"PrRhSn3_A", "PrRhSn3_E"} <= names
