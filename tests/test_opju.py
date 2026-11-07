"""Tests for the header, the run-length layer, and the literal-run text scan."""

from __future__ import annotations

from pathlib import Path

import pytest

from opj import Header, OpjError, RunLengthError, expand, parse, read
from opj.opju import ROOT_HEADER_CONST, parse_header, scan_named_records


def _text_run(token: int, text: bytes) -> bytes:
    return bytes((token, len(text))) + text


def _synthetic() -> bytes:
    """A minimal well-formed .opju-shaped buffer."""
    header = b"CPYUA 1.0 3\n"
    root = bytes((0x3E, 0x01, 0x6C, 0xC0, 0x11, 0x01, 0x02))
    return header + root + _text_run(0x82, b"Book1_A") + bytes((0x85, 0x02, 0xF0, 0x3F))


# --- header ---------------------------------------------------------------


def test_parse_header_fields() -> None:
    h = parse_header(_synthetic())
    assert isinstance(h, Header)
    assert h.magic == "CPYUA"
    assert h.format_version == "1.0"
    assert h.build == 3
    assert h.body_offset == len(b"CPYUA 1.0 3\n")


def test_parse_header_rejects_non_origin() -> None:
    with pytest.raises(OpjError):
        parse_header(b"not an origin file\n")


def test_parse_header_rejects_non_integer_build() -> None:
    with pytest.raises(OpjError, match="build"):
        parse_header(b"CPYUA 1.0 x\n")


def test_parse_header_rejects_truncated() -> None:
    with pytest.raises(OpjError):
        parse_header(b"CPYUA 1.0 3 no newline")


def test_root_header_constant_bytes() -> None:
    doc = parse(_synthetic())
    assert len(doc.root_header) == 7
    assert doc.root_header[2:6] == ROOT_HEADER_CONST


# --- run-length layer -------------------------------------------------------


def test_expand_empty_literal_token() -> None:
    assert expand(b"\x00") == b""


def test_expand_literal_run() -> None:
    assert expand(b"\x03abc") == b"abc"


def test_expand_largest_literal_run() -> None:
    text = bytes(range(127))
    assert expand(b"\x7f" + text) == text


@pytest.mark.parametrize(
    "token, count",
    [(0x80, 3), (0x81, 4), (0x85, 8), (0x9D, 32), (0xBF, 66)],
)
def test_expand_zero_run_uses_base_3(token: int, count: int) -> None:
    assert expand(bytes((token,))) == bytes(count)


@pytest.mark.parametrize(
    "token, count",
    [(0xC0, 3), (0xC1, 4), (0xC5, 8), (0xDD, 32), (0xFF, 66)],
)
def test_expand_byte_run_uses_base_3(token: int, count: int) -> None:
    assert expand(bytes((token, 0x20))) == b"\x20" * count


def test_expand_a_compact_looking_double_is_zeros_then_literal() -> None:
    # What looks like a tag, a length and a value is a run and a literal.
    assert expand(bytes((0x85, 0x02, 0xF0, 0x3F))) == bytes(8) + b"\xf0\x3f"


def test_expand_mixed_tokens_in_order() -> None:
    coded = bytes((0x80, 0x02)) + b"hi" + bytes((0xC0, 0x41, 0x01)) + b"!"
    assert expand(coded) == bytes(3) + b"hi" + b"AAA!"


def test_expand_window() -> None:
    coded = b"xx" + b"\x01a" + b"yy"
    assert expand(coded, 2, 4) == b"a"


@pytest.mark.parametrize(
    "coded",
    [
        pytest.param(b"\x03ab", id="short-literal"),
        pytest.param(b"\xc0", id="byte-run-without-operand"),
    ],
)
def test_expand_rejects_truncated_operands(coded: bytes) -> None:
    with pytest.raises(RunLengthError):
        expand(coded)


def test_expand_literal_cannot_read_past_its_window() -> None:
    with pytest.raises(RunLengthError):
        expand(b"\x03abc", 0, 3)


# --- literal-run text scan --------------------------------------------------


def test_scan_recovers_name() -> None:
    data = _synthetic()
    recs = scan_named_records(data, 0)
    assert "Book1_A" in [r.text for r in recs]


def test_scan_accepts_any_zero_run_token() -> None:
    assert scan_named_records(_text_run(0xBF, b"Book1_B"), 0)[0].text == "Book1_B"


def test_scan_ignores_short_runs() -> None:
    # a 3-char printable run after a zero-run token must not be accepted
    data = bytes((0x82, 0x03)) + b"abc"
    assert scan_named_records(data, 0, min_len=4) == []


def test_scan_rejects_non_printable_run() -> None:
    assert scan_named_records(bytes((0x82, 0x05)) + b"ab\x01cd", 0) == []


# --- real-sample inventory (skips without fixtures) -------------------------


def test_sampledata_inventory(sample_data: Path) -> None:
    doc = read(sample_data)
    assert doc.header.magic == "CPYUA"
    assert doc.header.build > 0
    assert doc.root_header[2:6] == ROOT_HEADER_CONST
    assert {"Book1_A", "Book1_B", "Book1_C"} <= set(doc.name_strings)


def test_fitsummation_inventory(fit_summation: Path) -> None:
    doc = read(fit_summation)
    assert doc.header.magic == "CPYUA"
    assert {"PrRhSn3_A", "PrRhSn3_E"} <= set(doc.name_strings)
