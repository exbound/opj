"""Global header, asset directory, column payloads, and the CLI.

The framing buffers are constructed here. Sample tests skip without fixtures.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from fpc_encoder import encode
from opj import Column, Undecoded, parse, read
from opj.__main__ import main

_FILE_HEAD = b"CPYUA 4.3330 172\n" + bytes((0x3E, 0x01, 0x6C, 0xC0, 0x11, 0x01, 0x02))
_END = b"\xce\xfa"
_TRAILER = bytes((0xFF, 0xFF, 0x02, 0x01, 0x04, 0x00))


def _leb128(value: int) -> bytes:
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _descriptor(length: int, tag: int = 0x9D, separator: int = 0x0A) -> bytes:
    return (
        bytes((tag, 0x18))
        + struct.pack("<QQ", 24, length)
        + bytes(range(8))
        + bytes((separator,))
    )


def _column(payload: bytes, **kwargs: int) -> bytes:
    return _descriptor(len(payload), **kwargs) + payload


def _fpc_payload(
    values: list[float],
    a: int | None = None,
    b: int | None = None,
    token_count: int | None = None,
    table_bits: int = 12,
    trailing: bytes = b"",
) -> bytes:
    b = len(values) if b is None else b
    a = b if a is None else a
    token_count = len(values) if token_count is None else token_count
    return (
        b"\x05"
        + _leb128(a)
        + b"\xff\xff"
        + _leb128(b)
        + b"\x00"
        + _leb128(((token_count - 1) << 1) | 1)
        + bytes((table_bits,))
        + encode(values)
        + trailing
        + _END
    )


def _name(text: bytes, tag: int = 0x82) -> bytes:
    return bytes((tag, len(text))) + text


def _only_column(payload: bytes) -> Column:
    columns = parse(_FILE_HEAD + _name(b"Book1_A") + _column(payload)).columns
    assert len(columns) == 1
    return columns[0]


# --- descriptor validation --------------------------------------------------


def test_valid_descriptor_locates_payload() -> None:
    payload = _fpc_payload([1.0, 2.0, 3.0])
    data = _FILE_HEAD + b"\x00\x00" + _column(payload)
    [column] = parse(data).columns
    assert column.offset == len(_FILE_HEAD) + 2
    assert column.payload_offset == column.offset + 27
    assert column.payload_length == len(payload)


@pytest.mark.parametrize("tag", [0x80, 0x9B, 0x9F])
def test_descriptor_accepts_any_value_tag(tag: int) -> None:
    assert len(parse(_FILE_HEAD + _column(_fpc_payload([1.0]), tag=tag)).columns) == 1


@pytest.mark.parametrize("tag", [0x7F, 0xA0])
def test_descriptor_rejects_tag_outside_value_range(tag: int) -> None:
    assert parse(_FILE_HEAD + _column(_fpc_payload([1.0]), tag=tag)).columns == []


def test_descriptor_rejects_wrong_separator() -> None:
    data = _FILE_HEAD + _column(_fpc_payload([1.0]), separator=0x0B)
    assert parse(data).columns == []


def test_descriptor_rejects_length_past_end_of_file() -> None:
    payload = _fpc_payload([1.0])
    data = _FILE_HEAD + _descriptor(len(payload) + 1) + payload
    assert parse(data).columns == []


def test_descriptor_rejects_payload_without_end_marker() -> None:
    payload = _fpc_payload([1.0])[:-1] + b"\x00"
    assert parse(_FILE_HEAD + _column(payload)).columns == []


def test_descriptor_rejects_length_that_misses_end_marker() -> None:
    payload = _fpc_payload([1.0])
    data = _FILE_HEAD + _descriptor(len(payload) - 1) + payload
    assert parse(data).columns == []


def test_descriptor_rejects_too_short_for_end_marker() -> None:
    assert parse(_FILE_HEAD + _descriptor(1) + b"\xfa").columns == []


def test_descriptor_rejects_truncated_descriptor() -> None:
    assert parse(_FILE_HEAD + _descriptor(4)[:20]).columns == []


# --- decoded payloads -------------------------------------------------------


def test_fpc_payload_values_and_exact_consumption() -> None:
    values = [1.5, -2.25, 1e300, 0.0, 0.0]
    column = _only_column(_fpc_payload(values))
    assert column.undecoded is None
    assert column.a == column.b == 5
    assert list(column.values) == values
    assert column.trailing == b""


def test_multibyte_leb128_heads() -> None:
    values = [float(i) for i in range(300)]
    column = _only_column(_fpc_payload(values, a=1000))
    assert (column.a, column.b) == (1000, 300)
    assert list(column.values) == values


def test_trailing_bytes_are_kept_raw() -> None:
    column = _only_column(_fpc_payload([1.0, 2.0], a=4, trailing=_TRAILER))
    assert list(column.values) == [1.0, 2.0]
    assert column.trailing == _TRAILER


def test_head_byte_is_exposed() -> None:
    assert _only_column(_fpc_payload([1.0])).head_byte == 0


# --- undecoded forms: reported, never guessed, never raising ------------------


def _assert_no_values(column: Column, reason: Undecoded) -> None:
    assert column.undecoded is reason
    assert column.values == ()
    assert column.trailing == b""


def test_undecoded_04_payload() -> None:
    column = _only_column(b"\x04\x03\xff\xff\x03\x00\x05\x0c\x00" + _END)
    _assert_no_values(column, Undecoded.PAYLOAD_04)
    assert column.a is None and column.b is None


def test_undecoded_05_lacking_marker() -> None:
    column = _only_column(b"\x05\x03\x00\x00\x03\x00\x05\x0c\x00" + _END)
    _assert_no_values(column, Undecoded.NO_MARKER)
    assert column.a is None and column.b is None


def test_undecoded_unknown_payload_form() -> None:
    _assert_no_values(_only_column(b"\x07\x00" + _END), Undecoded.PAYLOAD_UNKNOWN)


def test_undecoded_payload_of_only_end_marker() -> None:
    _assert_no_values(_only_column(_END), Undecoded.PAYLOAD_UNKNOWN)


def test_undecoded_run_only_stream() -> None:
    # v = 6: three copies of one following value, of unstated width.
    column = _only_column(b"\x05\x03\xff\xff\x03\x00\x06\x2a" + _END)
    _assert_no_values(column, Undecoded.RUN_TOKEN)
    assert (column.a, column.b) == (3, 3)


def test_undecoded_block_with_fewer_values_than_b() -> None:
    column = _only_column(_fpc_payload([1.0, 2.0], b=5))
    _assert_no_values(column, Undecoded.COUNT_MISMATCH)
    assert (column.a, column.b) == (5, 5)


def test_undecoded_other_table_size() -> None:
    column = _only_column(_fpc_payload([1.0], table_bits=16))
    _assert_no_values(column, Undecoded.TABLE_BITS)


def test_undecoded_overrun() -> None:
    # Three values announced, with residuals for only part of one.
    payload = b"\x05\x03\xff\xff\x03\x00\x05\x0c\x07\x01" + _END
    _assert_no_values(_only_column(payload), Undecoded.OVERRUN)


@pytest.mark.parametrize(
    "stream",
    [
        pytest.param(b"\x05\x03\xff\xff", id="no-b"),
        pytest.param(b"\x05\x03\xff\xff\x03", id="no-head-byte"),
        pytest.param(b"\x05\x03\xff\xff\x03\x00", id="no-token"),
        pytest.param(b"\x05\x03\xff\xff\x03\x00\x05", id="no-table-bits"),
        pytest.param(b"\x05\x03\xff\xff\x03\x00\x85", id="cut-token"),
    ],
)
def test_undecoded_truncated_stream(stream: bytes) -> None:
    _assert_no_values(_only_column(stream + _END), Undecoded.MALFORMED_HEAD)


# --- column naming ----------------------------------------------------------


def test_name_is_nearest_preceding_record() -> None:
    data = (
        _FILE_HEAD
        + _name(b"Book1_A", tag=0x97)
        + bytes((0x8F, 0x02, 0xC8, 0x10))
        + _column(_fpc_payload([1.0]))
        + _name(b"Book1_B")
        + _name(b"Book1_C")
        + _column(_fpc_payload([2.0]))
    )
    assert [c.name for c in parse(data).columns] == ["Book1_A", "Book1_C"]


def test_name_is_not_taken_from_before_the_previous_payload() -> None:
    data = (
        _FILE_HEAD
        + _name(b"Book1_A")
        + _column(_fpc_payload([1.0]))
        + _column(_fpc_payload([2.0]))
    )
    assert [c.name for c in parse(data).columns] == ["Book1_A", None]


def test_name_is_not_taken_from_inside_the_previous_payload() -> None:
    payload = _fpc_payload([1.0], trailing=_name(b"Inside_A"))
    data = _FILE_HEAD + _column(payload) + _column(_fpc_payload([2.0]))
    assert parse(data).columns[1].name is None


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


# --- CLI --------------------------------------------------------------------


def test_cli_prints_build_version_directory_and_columns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = _fpc_payload([float(i) for i in range(1, 8)])
    columns = (
        _name(b"Book1_A")
        + _column(payload)
        + _name(b"Book1_B")
        + _column(b"\x04\x00" + _END)
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
    path = tmp_path / "synthetic.opju"
    path.write_bytes(data)

    assert main([str(path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "build           172" in lines
    assert "version         9.6 (build 172, 9.600172)" in lines
    assert any(ln.endswith("1 entries of 16 bytes") for ln in lines)
    assert any("Book1_A rows=7 values: 1, 2, 3, 4, 5, ..." in ln for ln in lines)
    assert any(
        f"Book1_B rows=? undecoded: {Undecoded.PAYLOAD_04}" in ln for ln in lines
    )


def test_cli_reports_absent_global_header(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "synthetic.opju"
    path.write_bytes(_FILE_HEAD)
    assert main([str(path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "version         absent" in lines
    assert "asset directory absent" in lines


# --- real samples (skip without fixtures) -----------------------------------

# IUPAC group of elements Z = 1 to 54.
IUPAC_GROUPS = [
    1, 18,
    1, 2, 13, 14, 15, 16, 17, 18,
    1, 2, 13, 14, 15, 16, 17, 18,
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18,
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18,
]  # fmt: skip


def test_periodictable_first_column_is_iupac_group(periodic_table: Path) -> None:
    first = read(periodic_table).columns[0]
    assert first.name == "Book1_A"
    assert first.undecoded is None
    assert len(IUPAC_GROUPS) == 54
    assert list(first.values[:54]) == IUPAC_GROUPS


def test_xpsarea_first_column_is_a_fixed_step_scan(xps_area: Path) -> None:
    first = read(xps_area).columns[0]
    assert first.undecoded is None
    assert first.values[0] == pytest.approx(740.03)
    assert first.values[1] == pytest.approx(739.98)
    steps = [a - b for a, b in zip(first.values, first.values[1:], strict=False)]
    assert len(first.values) == first.b
    assert steps == pytest.approx([0.05] * len(steps), abs=1e-9)


def test_sampledata_global_header(sample_data: Path) -> None:
    doc = read(sample_data)
    assert doc.global_header is not None
    assert (doc.global_header.version, doc.global_header.build) == (9.6, 172)
    assert doc.header.build == 172


def test_sampledata_columns_are_named(sample_data: Path) -> None:
    columns = read(sample_data).columns
    assert [c.name for c in columns[:3]] == ["Book1_A", "Book1_B", "Book1_C"]
