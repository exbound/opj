"""Tests for the .opju reader: synthetic framing plus real-sample validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from opj import Header, OpjError, parse, read
from opj.opju import (
    ROOT_HEADER_CONST,
    iter_header_records,
    iter_records,
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


# --- resync walk -----------------------------------------------------------


def _synthetic_with_gap() -> bytes:
    header = b"CPYUA 1.0 0\n"
    root = bytes((0x3E, 0x01, 0x6C, 0xC0, 0x11, 0x01, 0x02))
    pre = _string_record(0x82, b"AaAaAa")
    opaque = bytes((0xF8, 0xFF)) + bytes(30)  # asset-like region, not value tags
    after = b"".join(_string_record(0x82, f"Col{i}__".encode()) for i in range(5))
    return header + root + pre + opaque + after


def test_iter_records_resyncs_across_opaque() -> None:
    data = _synthetic_with_gap()
    h = parse_header(data)
    recs = list(iter_records(data, h.body_offset + 7))
    texts = [r.text for r in recs]
    assert "AaAaAa" in texts  # recovered before the gap
    assert [f"Col{i}__" for i in range(5)] == [
        t for t in texts if t and t.startswith("Col")
    ]


def test_gap_between_records_is_reported() -> None:
    doc = parse(_synthetic_with_gap())
    assert any("opaque" in note for _, note in doc.gaps)


# --- real-sample validation (skips without fixtures) -----------------------


def test_sampledata_inventory(sample_data: Path) -> None:
    doc = read(sample_data)
    assert doc.header.magic == "CPYUA"
    assert doc.header.object_count > 0
    assert doc.root_header[2:6] == ROOT_HEADER_CONST
    names = set(doc.name_strings)
    assert {"Book1_A", "Book1_B", "Book1_C"} <= names


def test_sampledata_new_object_vs_continuation(sample_data: Path) -> None:
    doc = read(sample_data)
    by_name = {r.text: r for r in doc.names}
    # the first column of a book defines a new object (tag != 0x82);
    # subsequent columns continue it (tag 0x82).
    assert by_name["Book1_A"].tag != 0x82
    assert by_name["Book1_B"].tag == 0x82
    assert by_name["Book1_C"].tag == 0x82
    # new-object names precede their continuations in file order.
    assert by_name["Book1_A"].offset < by_name["Book1_B"].offset


def test_fitsummation_inventory(fit_summation: Path) -> None:
    doc = read(fit_summation)
    assert doc.header.magic == "CPYUA"
    names = set(doc.name_strings)
    assert {"PrRhSn3_A", "PrRhSn3_E"} <= names
