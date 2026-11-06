"""Global header, directory, column records and payloads, sections, settings, CLI.

The framing buffers are constructed here. Sample tests skip without fixtures.
"""

from __future__ import annotations

import itertools
import math
import struct
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

from fpc_encoder import encode
from opj import (
    SPECIAL_64,
    SPECIAL_65,
    Column,
    ColumnType,
    CurveJoinProblem,
    Special,
    Undecoded,
    parse,
    read,
)
from opj.__main__ import main

_FILE_HEAD = b"CPYUA 4.3330 172\n" + bytes((0x3E, 0x01, 0x6C, 0xC0, 0x11, 0x01, 0x02))
_END = b"\xce\xfa"
_KIND_05 = 0x05
_KIND_04 = 0x04


def _leb128(value: int) -> bytes:
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _zero_tokens(count: int) -> bytes:
    """Zero-run tokens for ``count`` zeros, in pieces of at least 3."""
    out = bytearray()
    while count > 66:
        piece = min(66, count - 3)
        out.append(0x80 | (piece - 3))
        count -= piece
    if count:
        out.append(0x80 | (count - 3))
    return bytes(out)


def _record(name: bytes = b"Book1_A", size: int = 147, name_at: int = 0x58) -> bytes:
    """A framed coded record expanding to ``size``, ending in a descriptor's tag."""
    coded = (
        _zero_tokens(name_at)
        + bytes((len(name),))
        + name
        + _zero_tokens(size - name_at - len(name) - 20)
        + _zero_tokens(20)
    )
    return bytes((0x80, len(coded))) + coded


def _descriptor(length: int, separator: int = 0x0A) -> bytes:
    """A descriptor from its ``18``; the record's last token precedes it."""
    return (
        b"\x18" + struct.pack("<QQ", 24, length) + bytes(range(8)) + bytes((separator,))
    )


def _column(payload: bytes, name: bytes = b"Book1_A") -> bytes:
    return _record(name) + _descriptor(len(payload)) + payload


def _payload(kind: int, a: int, *segments: bytes) -> bytes:
    return bytes((kind,)) + _leb128(a) + b"".join(segments) + _END


def _segment(n: int, flag: int, *tokens: bytes) -> bytes:
    return b"\xff\xff" + _leb128(n) + bytes((flag,)) + b"".join(tokens)


def _literal_token(count: int) -> bytes:
    return _leb128(((count - 1) << 1) | 1)


def _run_token(count: int) -> bytes:
    return _leb128(count << 1)


def _block(values: list[float]) -> bytes:
    """A literal token and its FPC block."""
    return _literal_token(len(values)) + b"\x0c" + encode(values)


def _doubles(values: list[float]) -> bytes:
    return _payload(_KIND_05, len(values), _segment(len(values), 0, _block(values)))


def _lstring(text: bytes) -> bytes:
    return _leb128(len(text)) + text


def _only_column(payload: bytes, after: bytes = b"") -> Column:
    columns = parse(_FILE_HEAD + _column(payload) + after).columns
    assert len(columns) == 1
    return columns[0]


def _assert_undecoded(column: Column, reason: Undecoded) -> None:
    assert column.undecoded is reason
    assert column.values == ()


# --- descriptor validation --------------------------------------------------


def test_valid_descriptor_locates_payload() -> None:
    payload = _doubles([1.0, 2.0, 3.0])
    record = _record()
    [column] = parse(_FILE_HEAD + record + _descriptor(len(payload)) + payload).columns
    assert column.offset == len(_FILE_HEAD) + len(record)
    assert column.payload_offset == column.offset + 26
    assert column.payload_length == len(payload)


def test_descriptor_rejects_wrong_separator() -> None:
    payload = _doubles([1.0])
    data = _FILE_HEAD + _record() + _descriptor(len(payload), 0x0B) + payload
    assert parse(data).columns == []


def test_descriptor_rejects_length_past_end_of_file() -> None:
    payload = _doubles([1.0])
    data = _FILE_HEAD + _record() + _descriptor(len(payload) + 1) + payload
    assert parse(data).columns == []


def test_descriptor_rejects_payload_without_end_marker() -> None:
    payload = _doubles([1.0])[:-1] + b"\x00"
    assert parse(_FILE_HEAD + _column(payload)).columns == []


def test_descriptor_rejects_length_that_misses_end_marker() -> None:
    payload = _doubles([1.0])
    data = _FILE_HEAD + _record() + _descriptor(len(payload) - 1) + payload
    assert parse(data).columns == []


def test_descriptor_rejects_too_short_for_end_marker() -> None:
    assert parse(_FILE_HEAD + _record() + _descriptor(1) + b"\xfa").columns == []


def test_descriptor_rejects_truncated_descriptor() -> None:
    assert parse(_FILE_HEAD + _record() + _descriptor(4)[:20]).columns == []


# --- column records ---------------------------------------------------------


def test_record_is_expanded_and_exposed() -> None:
    column = _only_column(_doubles([1.0]))
    assert column.record_offset == len(_FILE_HEAD)
    assert column.record is not None
    assert len(column.record) == 147
    assert column.record[0x58:0x5F] == b"Book1_A"


def test_name_from_147_byte_record_at_0x58() -> None:
    assert _only_column(_doubles([1.0])).name == "Book1_A"


def _nested_frames(total: int, name_in_rest_at: int = 84) -> bytes:
    """An enclosing frame whose 4-byte literal run holds a coincidental ``80 <m>``.

    The enclosing frame expands to ``total`` bytes and the nested one to 4 fewer.
    """
    name = b"Book1_N"
    rest = (
        _zero_tokens(name_in_rest_at)
        + bytes((len(name),))
        + name
        + _zero_tokens(total - 4 - name_in_rest_at - len(name) - 20)
        + _zero_tokens(20)
    )
    coded = b"\x04\x00\x00\x80" + bytes((len(rest),)) + rest
    return bytes((0x80, len(coded))) + coded


def test_nested_coincidental_frame_is_skipped_for_the_enclosing_147() -> None:
    payload = _doubles([1.0])
    data = _FILE_HEAD + _nested_frames(147) + _descriptor(len(payload)) + payload
    [column] = parse(data).columns
    assert column.record_offset == len(_FILE_HEAD)
    assert column.record is not None
    assert len(column.record) == 147
    assert column.name == "Book1_N"


def test_nothing_is_accepted_when_no_frame_reaches_147() -> None:
    payload = _doubles([1.0])
    data = _FILE_HEAD + _nested_frames(140) + _descriptor(len(payload)) + payload
    [column] = parse(data).columns
    assert column.record_offset is None
    assert column.record is None
    assert column.name is None
    assert column.record_problem is not None
    assert column.values == (1.0,)


def test_other_record_size_reports_rather_than_guesses() -> None:
    payload = _doubles([1.0])
    data = _FILE_HEAD + _record(size=160) + _descriptor(len(payload)) + payload
    [column] = parse(data).columns
    assert column.name is None
    assert column.record is None
    assert column.record_problem is not None
    assert column.undecoded is None


def test_no_frame_reports_rather_than_guesses() -> None:
    payload = _doubles([1.0])
    data = _FILE_HEAD + _zero_tokens(20) + _descriptor(len(payload)) + payload
    [column] = parse(data).columns
    assert column.record is None
    assert column.name is None
    assert column.record_problem is not None
    assert column.values == (1.0,)


def test_frame_must_land_on_a_zero_run_token() -> None:
    # The frame's tokens are a literal run that swallows the descriptor's tag.
    payload = _doubles([1.0])
    data = (
        _FILE_HEAD
        + bytes((0x80, 0x03, 0x02, 0x01))
        + _zero_tokens(20)
        + _descriptor(len(payload))
        + payload
    )
    [column] = parse(data).columns
    assert column.record is None


def test_nearest_of_two_147_byte_frames_wins() -> None:
    payload = _doubles([1.0])
    # A 3-byte literal run holds the nested frame and its first token, a 3-byte
    # zero run, so both frames expand to the same size over the same rest.
    name = b"Book1_N"
    rest = (
        _zero_tokens(85)
        + bytes((len(name),))
        + name
        + _zero_tokens(147 - 88 - len(name) - 20)
        + _zero_tokens(20)
    )
    inner = b"\x80" + rest
    coded = bytes((0x03, 0x80, len(inner))) + inner
    data = _FILE_HEAD + bytes((0x80, len(coded))) + coded
    data += _descriptor(len(payload)) + payload
    [column] = parse(data).columns
    assert column.record_offset == len(_FILE_HEAD) + 3
    assert column.name == "Book1_N"


def test_non_utf8_name_is_reported() -> None:
    column = parse(_FILE_HEAD + _column(_doubles([1.0]), name=b"\xff\xfe")).columns[0]
    assert column.name is None
    assert column.record_problem is not None


def test_columns_take_their_own_names() -> None:
    data = (
        _FILE_HEAD
        + _column(_doubles([1.0]), b"Book1_A")
        + _column(_doubles([2.0]), b"Book1_B")
    )
    assert [c.name for c in parse(data).columns] == ["Book1_A", "Book1_B"]


# --- payload grammar: doubles -----------------------------------------------


def test_fpc_literal_values() -> None:
    values = [1.5, -2.25, 1e300, 0.0, 0.0]
    column = _only_column(_doubles(values))
    assert column.undecoded is None
    assert column.a == 5
    assert column.kind == _KIND_05
    assert list(column.values) == values


def test_multibyte_leb128_heads() -> None:
    values = [float(i) for i in range(300)]
    column = _only_column(_doubles(values))
    assert column.a == 300
    assert list(column.values) == values


def test_literal_count_is_half_v_plus_one() -> None:
    # v = 5 holds 3 literal values; the next token completes the segment.
    payload = _payload(
        _KIND_05, 4, _segment(4, 0, b"\x05\x0c" + encode([1.0, 2.0, 3.0]), b"\x01\x0c")
    )
    payload = payload[:-2] + encode([4.0]) + _END
    assert _only_column(payload).values == (1.0, 2.0, 3.0, 4.0)


def test_run_count_is_half_v() -> None:
    payload = _payload(_KIND_05, 3, _segment(3, 0, b"\x06", b"\x11\x40"))
    assert _only_column(payload).values == (2.0, 2.0, 2.0)


@pytest.mark.parametrize(
    "encoded, value",
    [
        pytest.param(b"\x11\x40", 2.0, id="w1"),
        pytest.param(b"\x1a\xf0\x3f", 1.0, id="w2"),
        pytest.param(b"\x23\x20\x9f\x40", 1992.0, id="w3"),
        pytest.param(b"\x2c\x00\x20\x9f\x40", 1992.0, id="w4"),
        pytest.param(b"\x35\x00\x00\x20\x9f\x40", 1992.0, id="w5"),
        pytest.param(b"\x3e\x00\x00\x00\x20\x9f\x40", 1992.0, id="w6"),
        pytest.param(b"\x47" + struct.pack("<d", 1.5)[1:], 1.5, id="w7"),
        pytest.param(b"\x50" + struct.pack("<d", math.pi), math.pi, id="w8"),
    ],
)
def test_compact_double_width(encoded: bytes, value: float) -> None:
    payload = _payload(_KIND_05, 2, _segment(2, 0, b"\x04", encoded))
    assert _only_column(payload).values == (value, value)


@pytest.mark.parametrize("code", [0x08, 0x09, 0x10, 0x59, 0x63, 0x66, 0xFF])
def test_compact_double_code_without_width_is_rejected(code: int) -> None:
    payload = _payload(_KIND_05, 1, _segment(1, 0, b"\x02", bytes((code,)) + bytes(9)))
    _assert_undecoded(_only_column(payload), Undecoded.COMPACT_CODE)


@pytest.mark.parametrize(
    "code, marker", [(0x64, SPECIAL_64), (0x65, SPECIAL_65)], ids=["64", "65"]
)
def test_special_codes_stay_distinct_markers(code: int, marker: Special) -> None:
    payload = _payload(_KIND_05, 2, _segment(2, 0, b"\x04", bytes((code,))))
    values = _only_column(payload).values
    assert values == (marker, marker)
    assert isinstance(values[0], Special)
    assert values[0].code == code
    assert not isinstance(values[0], float)
    assert values[0] != 0.0


def test_specials_are_not_equal_to_each_other() -> None:
    assert SPECIAL_64 != SPECIAL_65


def test_special_consumes_no_value_bytes() -> None:
    payload = _payload(_KIND_05, 2, _segment(2, 0, b"\x02\x64", b"\x02\x65"))
    assert _only_column(payload).values == (SPECIAL_64, SPECIAL_65)


def test_selector_0_is_one_compact_double() -> None:
    payload = _payload(_KIND_05, 1, b"\x00\x00\x11\x40")
    column = _only_column(payload)
    assert column.values == (2.0,)
    assert [(s.rows, s.flag, s.selector) for s in column.segments] == [(1, 0, True)]


@pytest.mark.parametrize("flag", [0, 3])
def test_flags_other_than_1_are_doubles(flag: int) -> None:
    payload = _payload(_KIND_05, 2, _segment(2, flag, b"\x04", b"\x11\x40"))
    assert _only_column(payload).values == (2.0, 2.0)


def test_kind_04_with_flag_0_is_doubles() -> None:
    payload = _payload(_KIND_04, 2, _segment(2, 0, _block([1.5, 2.5])))
    column = _only_column(payload)
    assert column.values == (1.5, 2.5)
    assert column.integers == ()


def test_fpc_tables_reset_for_each_block() -> None:
    # The encoder starts fresh for each block, so a decoder must as well.
    first = [1.5, 2.5, 1.5, 2.5, 7.25, 1.5]
    second = [1.5, 2.5, 9.75, 1.5]
    payload = _payload(_KIND_05, 10, _segment(10, 0, _block(first), _block(second)))
    assert _only_column(payload).values == tuple(first + second)


def test_fpc_tables_reset_across_segments() -> None:
    values = [3.5, 4.5, 3.5, 4.5]
    payload = _payload(
        _KIND_05, 8, _segment(4, 0, _block(values)), _segment(4, 0, _block(values))
    )
    assert _only_column(payload).values == tuple(values + values)


# --- payload grammar: strings and rows in order -------------------------------


def test_strings_literal_run_and_empty() -> None:
    payload = _payload(
        _KIND_05,
        5,
        _segment(
            5,
            1,
            _literal_token(2) + _lstring(b"one") + _lstring(b"two"),
            _run_token(2) + _lstring(b"rep"),
            _run_token(1) + b"\x00",
        ),
    )
    assert _only_column(payload).values == ("one", "two", "rep", "rep", "")


def test_selector_1_is_one_string_in_kind_05() -> None:
    payload = _payload(_KIND_05, 1, b"\x01\x00" + _lstring(b"text"))
    assert _only_column(payload).values == ("text",)


def test_non_utf8_string_stays_bytes() -> None:
    payload = _payload(_KIND_05, 1, _segment(1, 1, b"\x02", _lstring(b"caf\xe9")))
    assert _only_column(payload).values == (b"caf\xe9",)


def test_utf8_string_is_decoded() -> None:
    text = "café".encode()
    payload = _payload(_KIND_05, 1, _segment(1, 1, b"\x02", _lstring(text)))
    assert _only_column(payload).values == ("café",)


def test_segments_of_mixed_types_keep_row_order() -> None:
    payload = _payload(
        _KIND_05,
        4,
        _segment(2, 0, _block([1.5, 2.5])),
        b"\x01\x00" + _lstring(b"gap"),
        b"\x00\x00\x11\x40",
    )
    column = _only_column(payload)
    assert column.values == (1.5, 2.5, "gap", 2.0)
    assert [(s.rows, s.flag, s.selector) for s in column.segments] == [
        (2, 0, False),
        (1, 1, True),
        (1, 0, True),
    ]


def test_empty_segment_list_is_zero_rows() -> None:
    column = _only_column(_payload(_KIND_05, 0))
    assert column.undecoded is None
    assert column.values == ()


# --- payload grammar: rejection ---------------------------------------------


def test_token_overfilling_its_segment_is_rejected_not_truncated() -> None:
    payload = _payload(_KIND_05, 5, _segment(2, 0, b"\x06", b"\x11\x40"))
    _assert_undecoded(_only_column(payload), Undecoded.OVERFILL)


def test_literal_token_overfilling_its_segment_is_rejected() -> None:
    payload = _payload(_KIND_05, 5, _segment(2, 0, _block([1.0, 2.0, 3.0])))
    _assert_undecoded(_only_column(payload), Undecoded.OVERFILL)


def test_segment_overfilling_the_payload_is_rejected() -> None:
    payload = _payload(_KIND_05, 1, _segment(2, 0, b"\x04", b"\x11\x40"))
    _assert_undecoded(_only_column(payload), Undecoded.ROW_COUNT)


def test_selector_overfilling_the_payload_is_rejected() -> None:
    payload = _payload(_KIND_05, 1, b"\x00\x00\x11\x40", b"\x00\x00\x11\x40")
    _assert_undecoded(_only_column(payload), Undecoded.ROW_COUNT)


def test_fewer_rows_than_a_is_rejected() -> None:
    payload = _payload(_KIND_05, 3, _segment(2, 0, b"\x04", b"\x11\x40"))
    _assert_undecoded(_only_column(payload), Undecoded.ROW_COUNT)


def test_unknown_kind_is_rejected() -> None:
    _assert_undecoded(_only_column(b"\x07\x00" + _END), Undecoded.KIND)


def test_payload_of_only_end_marker_is_rejected() -> None:
    _assert_undecoded(_only_column(_END), Undecoded.KIND)


def test_unknown_selector_is_rejected() -> None:
    payload = _payload(_KIND_05, 1, b"\x02\x00\x11\x40")
    _assert_undecoded(_only_column(payload), Undecoded.SELECTOR)


def test_other_table_size_is_rejected() -> None:
    payload = _payload(_KIND_05, 1, _segment(1, 0, b"\x01\x10" + encode([1.0])))
    _assert_undecoded(_only_column(payload), Undecoded.TABLE_BITS)


def test_fpc_overrun_is_rejected() -> None:
    payload = _payload(_KIND_05, 3, _segment(3, 0, b"\x05\x0c\x07\x01"))
    _assert_undecoded(_only_column(payload), Undecoded.OVERRUN)


@pytest.mark.parametrize(
    "stream",
    [
        pytest.param(b"\x05", id="no-a"),
        pytest.param(b"\x05\x81", id="cut-a"),
        pytest.param(b"\x05\x01\xff", id="half-marker"),
        pytest.param(b"\x05\x01\xff\xff", id="no-n"),
        pytest.param(b"\x05\x01\xff\xff\x01", id="no-flag"),
        pytest.param(b"\x05\x01\xff\xff\x01\x00", id="no-token"),
        pytest.param(b"\x05\x01\xff\xff\x01\x00\x01", id="no-table-bits"),
        pytest.param(b"\x05\x01\xff\xff\x01\x00\x02", id="no-run-value"),
        pytest.param(b"\x05\x01\xff\xff\x01\x00\x02\x1a\xf0", id="short-compact"),
        pytest.param(b"\x05\x01\xff\xff\x01\x01\x02\x05ab", id="short-string"),
        pytest.param(b"\x05\x01\x00\x00", id="no-selector-value"),
    ],
)
def test_truncated_payload_is_rejected(stream: bytes) -> None:
    _assert_undecoded(_only_column(stream + _END), Undecoded.TRUNCATED)


# --- string sections and integer columns ------------------------------------


def _literal_lz4(text: bytes) -> bytes:
    if len(text) < 15:
        return bytes((len(text) << 4,)) + text
    remaining = len(text) - 15
    return b"\xf0" + b"\xff" * (remaining // 255) + bytes((remaining % 255,)) + text


def _section(strings: list[bytes], magic: bytes = b"\x77\x11\x11\x11") -> bytes:
    body = b"".join(s + b"\x00" for s in strings)
    decoded = magic + struct.pack("<HHIIII", 2, 1, 4, 11, len(body), 22) + body
    return _section_of(_literal_lz4(decoded), len(decoded))


def _section_of(block: bytes, usize: int) -> bytes:
    return (
        b"#_MSER_STRINGS_PSET\x05\x01"
        + _leb128(len(block) + 4)
        + b"\x00"
        + struct.pack("<I", usize)
        + block
    )


def _integers(values: list[int]) -> bytes:
    tokens = _literal_token(len(values)) + b"".join(_leb128(v) for v in values)
    return _payload(_KIND_04, len(values), _segment(len(values), 1, tokens))


def test_section_header_and_strings() -> None:
    data = _FILE_HEAD + _section([b"Albania", b"Austria"]) + b"tail"
    [section] = parse(data).string_sections
    assert section.undecoded is None
    assert section.offset == len(_FILE_HEAD)
    assert (section.count, section.rows) == (11, 22)
    assert section.byte_count == len(b"Albania\x00Austria\x00")
    assert section.strings == {0: "Albania", 8: "Austria"}


def test_section_with_empty_string_keeps_its_offset() -> None:
    [section] = parse(_FILE_HEAD + _section([b"a", b"", b"b"])).string_sections
    assert section.strings == {0: "a", 2: "", 3: "b"}


def test_section_non_utf8_string_stays_bytes() -> None:
    [section] = parse(_FILE_HEAD + _section([b"caf\xe9"])).string_sections
    assert section.strings == {0: b"caf\xe9"}


def test_section_lz4_failure_is_recorded() -> None:
    [section] = parse(_FILE_HEAD + _section_of(b"\x30ab", 3)).string_sections
    assert section.undecoded is not None
    assert section.strings == {}


def test_section_size_mismatch_is_recorded() -> None:
    [section] = parse(_FILE_HEAD + _section_of(_literal_lz4(b"abc"), 9)).string_sections
    assert section.undecoded is not None


def test_section_block_length_comes_from_csize() -> None:
    # Bytes after the block are not part of it.
    body = _section([b"Albania"])
    [section] = parse(_FILE_HEAD + body + b"\x00" * 4).string_sections
    assert section.undecoded is None


def test_section_header_form_is_checked() -> None:
    [section] = parse(_FILE_HEAD + _section([b"a"], magic=b"XXXX")).string_sections
    assert section.undecoded is not None


def test_section_too_short_for_header_is_recorded() -> None:
    [section] = parse(_FILE_HEAD + _section_of(_literal_lz4(b"abc"), 3)).string_sections
    assert section.undecoded is not None


@pytest.mark.parametrize(
    "framing",
    [
        pytest.param(b"\x81", id="cut-size"),
        pytest.param(b"\x09\x01\x00\x00\x00\x00", id="separator-not-zero"),
        pytest.param(b"\x7f\x00\x00\x00\x00\x00", id="size-past-end"),
    ],
)
def test_section_framing_failure_is_recorded(framing: bytes) -> None:
    data = _FILE_HEAD + b"#_MSER_STRINGS_PSET\x05\x01" + framing
    [section] = parse(data).string_sections
    assert section.undecoded is not None


def test_integer_minus_one_is_the_string_offset() -> None:
    column = _only_column(_integers([1, 9]), _section([b"Albania", b"Austria"]))
    assert column.values == ("Albania", "Austria")
    assert column.resolution_problem is None
    assert column.unresolved_rows == 0


def test_integer_raw_values_are_kept_aligned_to_rows() -> None:
    payload = _payload(
        _KIND_04,
        3,
        _segment(2, 0, _block([1.5, 2.5])),
        b"\x01\x00" + _leb128(1),
    )
    column = _only_column(payload, _section([b"Albania"]))
    assert column.values == (1.5, 2.5, "Albania")
    assert column.integers == (None, None, 1)


def test_integer_run_and_multibyte_values() -> None:
    strings = [b"x" * 200, b"far"]
    payload = _payload(_KIND_04, 3, _segment(3, 1, _run_token(3) + _leb128(202)))
    assert _only_column(payload, _section(strings)).values == ("far", "far", "far")


def test_integer_zero_means_none() -> None:
    column = _only_column(_integers([0, 1]), _section([b"Albania"]))
    assert column.values == (None, "Albania")
    assert column.integers == (0, 1)
    assert column.resolution_problem is None


def test_integer_uses_first_section_after_the_payload() -> None:
    before = _section([b"before"])
    payload = _integers([1])
    data = (
        _FILE_HEAD
        + before
        + _column(payload)
        + _section([b"first"])
        + _section([b"second"])
    )
    column = parse(data).columns[0]
    assert column.values == ("first",)
    assert column.string_section_offset == len(_FILE_HEAD) + len(before) + len(
        _column(payload)
    )


def test_integer_without_a_following_section_records_failure() -> None:
    column = parse(_FILE_HEAD + _section([b"a"]) + _column(_integers([1, 2]))).columns[
        0
    ]
    assert column.values == (1, 2)
    assert column.integers == (1, 2)
    assert column.unresolved_rows == 2
    assert column.resolution_problem is not None


def test_integer_off_a_string_start_records_failure() -> None:
    column = _only_column(_integers([1, 3]), _section([b"Albania"]))
    assert column.values == ("Albania", 3)
    assert column.integers == (1, 3)
    assert column.unresolved_rows == 1
    assert column.resolution_problem is not None


def test_integer_with_undecoded_section_records_failure() -> None:
    column = _only_column(_integers([1]), _section_of(b"\x30ab", 3))
    assert column.values == (1,)
    assert column.unresolved_rows == 1
    assert column.resolution_problem is not None


def test_string_column_is_not_resolved() -> None:
    payload = _payload(_KIND_05, 1, _segment(1, 1, b"\x02", _lstring(b"a")))
    column = _only_column(payload)
    assert column.string_section_offset is None
    assert column.integers == ()


# --- settings document ------------------------------------------------------

_SETTINGS = b"<OriginStorage><ToolA><v>x</v></ToolA><ToolB/></OriginStorage>"


def _coded_settings(document: bytes = _SETTINGS) -> bytes:
    """A zero-run token, literal runs of at most 127 bytes, then a NUL."""
    out = bytearray(b"\x82")
    for at in range(0, len(document), 127):
        chunk = document[at : at + 127]
        out += bytes((len(chunk),)) + chunk
    return bytes(out) + b"\x80"


def test_settings_is_raw_bytes_up_to_the_nul() -> None:
    doc = parse(_FILE_HEAD + _coded_settings() + b"\x05after")
    assert doc.settings_xml == _SETTINGS


def test_settings_continues_across_literal_runs() -> None:
    inner = b"".join(b"<T%d/>" % i for i in range(60))
    document = b"<OriginStorage>" + inner + b"</OriginStorage>"
    assert len(document) > 127
    assert parse(_FILE_HEAD + _coded_settings(document)).settings_xml == document


def test_settings_expands_byte_runs() -> None:
    coded = (
        b"\x82\x0f<OriginStorage>"
        + bytes((0xC0, 0x20))
        + b"\x10</OriginStorage>"
        + b"\x80"
    )
    expected = b"<OriginStorage>   </OriginStorage>"
    assert parse(_FILE_HEAD + coded).settings_xml == expected


def test_settings_absent() -> None:
    assert parse(_FILE_HEAD).settings_xml is None


def test_settings_skips_root_text_not_after_a_zero_run() -> None:
    data = _FILE_HEAD + b"\x01\x0f<OriginStorage>" + _coded_settings()
    assert parse(data).settings_xml == _SETTINGS


def test_settings_skips_a_malformed_candidate() -> None:
    bad = _coded_settings(b"<OriginStorage><unclosed>")
    assert parse(_FILE_HEAD + bad + _coded_settings()).settings_xml == _SETTINGS


def test_settings_without_terminating_nul_is_absent() -> None:
    assert parse(_FILE_HEAD + _coded_settings()[:-1]).settings_xml is None


def test_settings_with_truncated_run_is_absent() -> None:
    coded = _coded_settings()[:-1] + b"\x05ab"
    assert parse(_FILE_HEAD + coded).settings_xml is None


# --- global header ----------------------------------------------------------


def _preview(directory_offset: int) -> bytes:
    return b"PrvwOPJU" + struct.pack("<Q", directory_offset)


def _global_header(value: float, size: int = 0x40) -> bytes:
    body = bytearray(size)
    struct.pack_into("<d", body, 0x1B, value)
    return struct.pack("<I", size) + b"\n" + bytes(body) + b"\n\x00\x00\x00\x00\n"


@pytest.mark.parametrize(
    "value, version, build",
    [
        (9.600172, 9.6, 172),
        (9.800193, 9.8, 193),
        (10.050100, 10.05, 100),
        (10.300137, 10.3, 137),
        (9.900017, 9.9, 17),
        (10.000023, 10.0, 23),
    ],
)
def test_global_header_splits_version_and_build(
    value: float, version: float, build: int
) -> None:
    head = _FILE_HEAD + _preview(0)
    header = parse(head + _global_header(value)).global_header
    assert header is not None
    assert header.version_value == value
    assert header.version == version
    assert header.build == build
    assert header.offset == len(head) + 5
    assert header.size == 0x40


def test_global_header_absent_without_preview_field() -> None:
    assert parse(_FILE_HEAD).global_header is None


def test_global_header_rejects_missing_terminator() -> None:
    data = _FILE_HEAD + _preview(0) + _global_header(9.6)[:-1] + b"\xff"
    assert parse(data).global_header is None


def test_global_header_rejects_size_too_small_for_version() -> None:
    # Framing holds, but the 8-byte value at 0x1B would not fit in 0x22 bytes.
    short = struct.pack("<I", 0x22) + b"\n" + bytes(0x22) + b"\n\x00\x00\x00\x00\n"
    assert parse(_FILE_HEAD + _preview(0) + short).global_header is None


def test_global_header_rejects_missing_size_separator() -> None:
    body = _global_header(9.6)
    data = _FILE_HEAD + _preview(0) + body[:4] + b"\x00" + body[5:]
    assert parse(data).global_header is None


def test_global_header_rejects_non_finite_value() -> None:
    data = _FILE_HEAD + _preview(0) + _global_header(float("inf"))
    assert parse(data).global_header is None


# --- asset directory --------------------------------------------------------


def _with_directory(table_bytes: int, count: int, table: bytes, zero: int = 0) -> bytes:
    offset = len(_FILE_HEAD) + 16
    head = struct.pack("<QII", count, zero, table_bytes)
    return _FILE_HEAD + _preview(offset) + head + table


def test_directory_16_byte_entries() -> None:
    table = struct.pack("<QQQQ", 8, 4, 12, 4)
    directory = parse(_with_directory(32, 2, table)).asset_directory
    assert directory is not None
    assert directory.unsupported is None
    assert directory.offset == len(_FILE_HEAD) + 16
    assert [(e.offset, e.length) for e in directory.entries] == [(8, 4), (12, 4)]


def test_directory_36_byte_entries_are_unsupported() -> None:
    directory = parse(_with_directory(72, 2, bytes(72))).asset_directory
    assert directory is not None
    assert directory.unsupported is not None
    assert directory.entries == ()
    assert (directory.count, directory.table_bytes) == (2, 72)


def test_directory_of_neither_shape_is_unsupported() -> None:
    directory = parse(_with_directory(88, 3, bytes(88))).asset_directory
    assert directory is not None
    assert directory.unsupported is not None
    assert directory.entries == ()


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(_with_directory(0, 0, b""), id="no-entries"),
        pytest.param(_with_directory(16, 1, bytes(16), zero=1), id="reserved-nonzero"),
        pytest.param(_with_directory(16, 1, b""), id="table-past-end"),
        pytest.param(
            _with_directory(16, 1, struct.pack("<QQ", 8, 1 << 40)), id="entry-past-end"
        ),
        pytest.param(_FILE_HEAD + _preview(1 << 40), id="offset-past-end"),
    ],
)
def test_directory_unusable_is_unsupported(data: bytes) -> None:
    directory = parse(data).asset_directory
    assert directory is not None
    assert directory.unsupported is not None
    assert directory.entries == ()


def test_directory_absent_without_preview_field() -> None:
    assert parse(_FILE_HEAD).asset_directory is None


# --- record fields ----------------------------------------------------------


def _record_with_fields(
    name: bytes, total: int, first: int, last: int, value_size: int
) -> bytes:
    body = bytearray(147 - 20)
    struct.pack_into("<III", body, 0x19, total, first, last)
    body[0x3D] = value_size
    body[0x58 : 0x58 + len(name)] = name
    coded = b"\x7f" + bytes(body) + _zero_tokens(20)
    return bytes((0x80, len(coded))) + coded


def test_record_fields_are_read_from_the_expanded_record() -> None:
    payload = _doubles([1.0, 2.0])
    record = _record_with_fields(b"Book1_A", 2, 5, 9, 24)
    [column] = parse(_FILE_HEAD + record + _descriptor(len(payload)) + payload).columns
    assert column.total_rows == 2
    assert column.first_row == 5
    assert column.last_row == 9
    assert column.value_size == 24
    assert column.name == "Book1_A"


def test_record_fields_are_none_without_a_record() -> None:
    payload = _doubles([1.0])
    data = _FILE_HEAD + _zero_tokens(20) + _descriptor(len(payload)) + payload
    [column] = parse(data).columns
    fields = (column.total_rows, column.first_row, column.last_row, column.value_size)
    assert fields == (None, None, None, None)


# --- curve records, labels and the join ---------------------------------------

_HEAD_519 = b"CPYUA 4.3721 180\n" + bytes((0x3E, 0x01, 0x6C, 0xC0, 0x11, 0x01, 0x02))
_X = 3


def _curve(name: bytes = b"A", type_byte: int = _X, size: int = 519) -> bytes:
    coded = (
        _zero_tokens(0x11)
        + bytes((1 + len(name), type_byte))
        + name
        + _zero_tokens(size - 0x12 - len(name))
    )
    return bytes((0x80, len(coded))) + coded


def _label(text: bytes) -> bytes:
    size = len(text) + 2
    return bytes((size, size - 1)) + text + b"\x00"


def _labelled_column(after_curve: bytes, name: bytes = b"Book1_A") -> Column:
    data = (
        _HEAD_519
        + _column(_doubles([1.0]), name)
        + b"Book1\x00"
        + _curve()
        + after_curve
    )
    return parse(data).columns[0]


@pytest.mark.parametrize(
    "byte, member",
    [
        (3, ColumnType.X),
        (0, ColumnType.Y),
        (5, ColumnType.Z),
        (6, ColumnType.X_ERROR),
        (2, ColumnType.Y_ERROR),
        (4, ColumnType.LABEL),
    ],
)
def test_type_byte_is_read_at_0x11(byte: int, member: ColumnType) -> None:
    data = _HEAD_519 + _column(_doubles([1.0])) + b"Book1\x00" + _curve(type_byte=byte)
    assert parse(data).columns[0].column_type is member
    assert member.known


@pytest.mark.parametrize("byte", [1, 9, 255])
def test_unknown_type_keeps_the_raw_byte(byte: int) -> None:
    data = _HEAD_519 + _column(_doubles([1.0])) + b"Book1\x00" + _curve(type_byte=byte)
    column_type = parse(data).columns[0].column_type
    assert column_type is not None
    assert column_type.value == byte
    assert not column_type.known
    assert column_type is ColumnType(byte)


def test_label_lines_are_long_name_units_comment() -> None:
    column = _labelled_column(b"\x00" + _label(b"BE\r\neV\r\nscan"))
    assert (column.long_name, column.units, column.comment) == ("BE", "eV", "scan")
    assert column.column_type is ColumnType.X
    assert column.curve_problem is None


def test_label_with_only_a_long_name() -> None:
    column = _labelled_column(b"\x00" + _label(b"Raw"))
    assert (column.long_name, column.units, column.comment) == ("Raw", None, None)


def test_label_with_empty_long_name_keeps_it_empty() -> None:
    column = _labelled_column(b"\x00" + _label(b"\r\nkeV"))
    assert (column.long_name, column.units) == ("", "keV")


def test_comment_keeps_further_line_separators() -> None:
    column = _labelled_column(b"\x00" + _label(b"a\r\nb\r\nc\r\nd"))
    assert column.comment == "c\r\nd"


@pytest.mark.parametrize(
    "filler",
    [
        pytest.param(b"", id="none"),
        pytest.param(b"\x00", id="00"),
        pytest.param(b"\x01\x00", id="01-00"),
        pytest.param(b"\x0a", id="0a"),
        pytest.param(b"\x01\x00\x0a", id="01-00-0a"),
    ],
)
def test_each_filler_before_a_label(filler: bytes) -> None:
    assert _labelled_column(filler + _label(b"Raw")).long_name == "Raw"


def test_label_size_equal_to_a_filler_byte_is_not_taken_as_filler() -> None:
    # <s> of 0A, the same byte as the filler, with an 8-byte text.
    label = _label(b"12345678")
    assert label[0] == 0x0A
    assert _labelled_column(b"\x01\x00\x0a" + label).long_name == "12345678"


def test_label_second_byte_must_be_size_minus_one() -> None:
    column = _labelled_column(b"\x00\x05\x05abc\x00")
    assert column.long_name is None
    assert column.column_type is ColumnType.X


def test_label_must_end_in_nul() -> None:
    assert _labelled_column(b"\x00\x05\x04abcd\x00").long_name is None


def test_no_label_before_the_next_frame() -> None:
    column = _labelled_column(_curve(b"B") + _label(b"belongs to B"))
    assert column.long_name is None
    assert column.column_type is ColumnType.X
    assert column.curve_problem is None


def test_no_label_before_other_bytes() -> None:
    assert _labelled_column(b"\x00\xc7\x41").long_name is None


def test_no_label_past_a_few_filler_bytes() -> None:
    assert _labelled_column(b"\x00" * 3 + _label(b"Raw")).long_name == "Raw"
    assert _labelled_column(b"\x00" * 4 + _label(b"Raw")).long_name is None


def test_label_markup_is_returned_raw() -> None:
    assert _labelled_column(_label(rb"2\g(q)")).long_name == r"2\g(q)"


def test_label_control_bytes_stay_as_found() -> None:
    column = _labelled_column(_label(b"a\x01b\x7f\rc\nd"))
    assert column.long_name == "a\x01b\x7f\rc\nd"
    assert column.units is None


def test_label_non_utf8_stays_bytes() -> None:
    assert _labelled_column(_label(b"caf\xe9")).long_name == b"caf\xe9"


def test_curve_size_follows_the_format_version() -> None:
    columns = _column(_doubles([1.0]))
    at_519 = b"Book1\x00" + _curve(size=519)
    at_515 = b"Book1\x00" + _curve(size=515)
    assert parse(_HEAD_519 + columns + at_519).columns[0].column_type is ColumnType.X
    assert parse(_HEAD_519 + columns + at_515).columns[0].column_type is None
    assert parse(_FILE_HEAD + columns + at_515).columns[0].column_type is ColumnType.X
    assert parse(_FILE_HEAD + columns + at_519).columns[0].column_type is None


def _assert_join_failed(column: Column, problem: CurveJoinProblem) -> None:
    assert column.curve_problem is problem
    assert column.column_type is None
    assert column.long_name is None
    assert column.units is None
    assert column.comment is None
    assert column.curve_offset is None


def test_join_book_literal_followed_by_a_zero_run_token() -> None:
    data = _HEAD_519 + _column(_doubles([1.0])) + b"Book1" + _curve()
    assert parse(data).columns[0].column_type is ColumnType.X


def test_join_book_literal_followed_by_anything_else_is_not_an_occurrence() -> None:
    data = _HEAD_519 + _column(_doubles([1.0])) + b"Book1]" + _curve()
    _assert_join_failed(parse(data).columns[0], CurveJoinProblem.NO_RECORD)


def test_join_ignores_book_literals_before_the_last_payload() -> None:
    data = _HEAD_519 + b"Book1\x00" + _curve() + _column(_doubles([1.0])) + _curve()
    _assert_join_failed(parse(data).columns[0], CurveJoinProblem.NO_RECORD)


def test_join_uses_the_columns_own_book() -> None:
    data = (
        _HEAD_519
        + _column(_doubles([1.0]), b"Book1_A")
        + _column(_doubles([2.0]), b"Book2_A")
        + b"Book1\x00"
        + _curve(type_byte=3)
        + _label(b"first book")
        + b"Book2\x00"
        + _curve(type_byte=0)
        + _label(b"second book")
    )
    first, second = parse(data).columns
    assert (first.column_type, first.long_name) == (ColumnType.X, "first book")
    assert (second.column_type, second.long_name) == (ColumnType.Y, "second book")
    assert (first.curves_in_book, second.curves_in_book) == (1, 1)


def test_join_order_of_books_in_the_stream_is_followed() -> None:
    data = (
        _HEAD_519
        + _column(_doubles([1.0]), b"Book1_A")
        + _column(_doubles([2.0]), b"Book2_A")
        + b"Book2\x00"
        + _curve(type_byte=0)
        + b"Book1\x00"
        + _curve(type_byte=3)
    )
    first, second = parse(data).columns
    assert (first.column_type, second.column_type) == (ColumnType.X, ColumnType.Y)


def test_join_prefix_of_a_longer_book_name_is_not_an_occurrence() -> None:
    data = (
        _HEAD_519
        + _column(_doubles([1.0]), b"Book1_A")
        + b"Book1\x00"
        + _curve(type_byte=3)
        + b"Book11\x00"
        + _curve(type_byte=0)
    )
    column = parse(data).columns[0]
    assert column.curves_in_book == 2
    assert column.column_type is ColumnType.X


def _two_sheets(column_name: bytes) -> Column:
    data = (
        _HEAD_519
        + _column(_doubles([1.0]), column_name)
        + b"Book1\x00"
        + _curve(type_byte=3)
        + _label(b"sheet one")
        + _curve(type_byte=0)
        + _label(b"sheet two")
    )
    return parse(data).columns[0]


def test_join_plain_name_is_the_first_record_in_the_book() -> None:
    column = _two_sheets(b"Book1_A")
    assert (column.long_name, column.curves_in_book) == ("sheet one", 2)


def test_join_at_k_is_the_kth_record_in_the_book() -> None:
    column = _two_sheets(b"Book1_A@2")
    assert column.long_name == "sheet two"
    assert column.column_type is ColumnType.Y
    assert column.curves_in_book == 2


def test_join_fails_when_the_book_has_fewer_records_than_the_sheet() -> None:
    column = _two_sheets(b"Book1_A@3")
    _assert_join_failed(column, CurveJoinProblem.FEWER_RECORDS)
    assert column.curves_in_book == 2


def test_join_fails_when_no_record_has_the_name() -> None:
    column = _two_sheets(b"Book1_Z")
    _assert_join_failed(column, CurveJoinProblem.NO_RECORD)
    assert column.curves_in_book == 0


def test_join_fails_for_a_name_without_a_book() -> None:
    column = _labelled_column(_label(b"Raw"), name=b"NoSeparator")
    _assert_join_failed(column, CurveJoinProblem.NO_COLUMN_NAME)


def test_join_fails_for_a_column_without_a_name() -> None:
    payload = _doubles([1.0])
    data = (
        _HEAD_519
        + _zero_tokens(20)
        + _descriptor(len(payload))
        + payload
        + b"Book1\x00"
        + _curve()
    )
    _assert_join_failed(parse(data).columns[0], CurveJoinProblem.NO_COLUMN_NAME)


@pytest.mark.parametrize("name", [b"Book1_A@x", b"Book1_A@0", b"Book1_A@"])
def test_join_at_without_a_sheet_number_is_part_of_the_name(name: bytes) -> None:
    _assert_join_failed(_two_sheets(name), CurveJoinProblem.NO_RECORD)


# --- CLI --------------------------------------------------------------------


def _cli_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], data: bytes
) -> list[str]:
    path = tmp_path / "synthetic.opju"
    path.write_bytes(data)
    assert main([str(path)]) == 0
    return capsys.readouterr().out.splitlines()


def test_cli_prints_build_version_directory_and_columns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    columns = _column(_doubles([float(i) for i in range(1, 8)]), b"Book1_A") + _column(
        _payload(_KIND_05, 3, _segment(3, 0, b"\x06\x64")), b"Book1_B"
    )
    preview_at = len(_FILE_HEAD) + len(columns)
    directory_at = preview_at + 16 + len(_global_header(9.600172))
    data = (
        _FILE_HEAD
        + columns
        + _preview(directory_at)
        + _global_header(9.600172)
        + struct.pack("<QIIQQ", 1, 0, 16, 0, 1)
    )
    lines = _cli_lines(tmp_path, capsys, data)
    assert "build           172" in lines
    assert "version         9.6 (build 172, 9.600172)" in lines
    assert any(ln.endswith("1 entries of 16 bytes") for ln in lines)
    assert any("Book1_A rows=7 values: 1, 2, 3, 4, 5, ..." in ln for ln in lines)
    assert any("Book1_B rows=3 values: <special 64>" in ln for ln in lines)


def test_cli_prints_strings_and_problems(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = _FILE_HEAD + _column(_integers([1, 2]))
    lines = _cli_lines(tmp_path, capsys, data + _section([b"Albania"]))
    assert any("values: 'Albania', 2" in ln and "unresolved" in ln for ln in lines)


def test_cli_prints_type_long_name_and_units(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = (
        _HEAD_519
        + _column(_doubles([1.0]), b"Book1_A")
        + _column(_doubles([2.0]), b"Book1_B")
        + b"Book1\x00"
        + _curve(b"A")
        + _label(b"BE\r\neV")
    )
    lines = _cli_lines(tmp_path, capsys, data)
    assert any("Book1_A type=X long='BE' units='eV' rows=1" in ln for ln in lines)
    assert any(
        "Book1_B" in ln and f"(no type: {CurveJoinProblem.NO_RECORD})" in ln
        for ln in lines
    )


def test_cli_reports_undecoded_column(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lines = _cli_lines(tmp_path, capsys, _FILE_HEAD + _column(b"\x07\x00" + _END))
    assert any(f"undecoded: {Undecoded.KIND}" in ln for ln in lines)


def test_cli_reports_absent_sections(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lines = _cli_lines(tmp_path, capsys, _FILE_HEAD)
    assert "version         absent" in lines
    assert "asset directory absent" in lines
    assert "settings        absent" in lines


def test_cli_prints_only_the_settings_size_and_children(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A value shaped like a path, in a literal run after a zero-run token, which
    # is also what the text scan accepts.
    document = (
        b"<OriginStorage><ToolA>"
        + b"</ToolA><ToolB>canary-value-for-this-test</ToolB></OriginStorage>"
    )
    lines = _cli_lines(tmp_path, capsys, _FILE_HEAD + _coded_settings(document))
    assert f"settings        {len(document)} bytes, 2 direct children" in lines
    out = "\n".join(lines)
    assert "canary-value" not in out
    assert "ToolA" not in out


# --- real samples (skip without fixtures) -----------------------------------

# IUPAC group of elements Z = 1 to 54.
IUPAC_GROUPS = [
    1, 18,
    1, 2, 13, 14, 15, 16, 17, 18,
    1, 2, 13, 14, 15, 16, 17, 18,
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18,
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18,
]  # fmt: skip


def _run_lengths(values: tuple[object, ...]) -> list[tuple[object, int]]:
    return [(value, len(list(run))) for value, run in itertools.groupby(values)]


def test_periodictable_first_column_is_iupac_group(periodic_table: Path) -> None:
    first = read(periodic_table).columns[0]
    assert first.name == "Book1_A"
    assert first.undecoded is None
    assert len(IUPAC_GROUPS) == 54
    assert list(first.values[:54]) == IUPAC_GROUPS


def test_periodictable_second_column_is_periods_through_five(
    periodic_table: Path,
) -> None:
    second = read(periodic_table).columns[1]
    assert second.name == "Book1_B"
    # Periods 1 to 5 hold 2, 8, 8, 18 and 18 elements. The file's order holds
    # that through Z = 54, and period 6 starts with two rows and then changes.
    assert _run_lengths(second.values)[:6] == [
        (1.0, 2),
        (2.0, 8),
        (3.0, 8),
        (4.0, 18),
        (5.0, 18),
        (6.0, 2),
    ]


def test_periodictable_integer_column_is_element_symbols(periodic_table: Path) -> None:
    columns = read(periodic_table).columns
    symbols = next(c for c in columns if c.name == "Book1_D")
    assert symbols.integers[:2] == (1, 3)
    assert symbols.values[:10] == (
        "H",
        "He",
        "Li",
        "Be",
        "B",
        "C",
        "N",
        "O",
        "F",
        "Ne",
    )
    assert symbols.resolution_problem is None


def test_xpsarea_columns_have_type_and_labels(xps_area: Path) -> None:
    columns = {c.name: c for c in read(xps_area).columns}
    energy = columns["Book1_A"]
    assert energy.column_type is ColumnType.X
    assert (energy.long_name, energy.units) == ("BE", "eV")
    raw = columns["Book1_B"]
    assert raw.column_type is ColumnType.Y
    assert raw.long_name == "Raw"


def test_xpsarea_first_column_is_a_fixed_step_scan(xps_area: Path) -> None:
    first = read(xps_area).columns[0]
    assert first.undecoded is None
    assert first.values[0] == pytest.approx(740.03)
    assert first.values[1] == pytest.approx(739.98)
    doubles = [v for v in first.values if isinstance(v, float)]
    assert len(doubles) == first.a
    steps = [a - b for a, b in itertools.pairwise(doubles)]
    assert steps == pytest.approx([0.05] * len(steps), abs=1e-9)


def test_sampledata_first_column_runs_through_the_years(sample_data: Path) -> None:
    first = read(sample_data).columns[0]
    assert first.name == "Book1_A"
    runs = _run_lengths(first.values)
    assert [value for value, _ in runs] == [float(y) for y in range(1992, 2005)]
    assert sum(length for _, length in runs) == first.a


def test_sampledata_global_header(sample_data: Path) -> None:
    doc = read(sample_data)
    assert doc.global_header is not None
    assert (doc.global_header.version, doc.global_header.build) == (9.6, 172)
    assert doc.header.build == 172


def test_sampledata_columns_are_named(sample_data: Path) -> None:
    columns = read(sample_data).columns
    assert [c.name for c in columns[:3]] == ["Book1_A", "Book1_B", "Book1_C"]


@pytest.mark.parametrize(
    "fixture", ["sample_data", "fit_summation", "periodic_table", "xps_area"]
)
def test_settings_document_parses(fixture: str, request: pytest.FixtureRequest) -> None:
    path: Path = request.getfixturevalue(fixture)
    document = read(path).settings_xml
    assert document is not None
    root = ElementTree.fromstring(document)
    assert root.tag == "OriginStorage"
    assert len(root) > 0
