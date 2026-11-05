"""Reproduce the specification's corpus figures.

Set ``OPJ_CORPUS`` to a flat directory holding the 98 public files named in
docs/engineering-spec.md §3. Every expected value here is the figure the
specification states.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from pathlib import Path

import pytest

from opj import AssetDirectory, Column, Undecoded, parse

_DESCRIPTOR_HEAD = re.compile(rb"[\x80-\x9f]\x18\x18\x00{7}")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _leb128(data: bytes, pos: int) -> tuple[int, int] | None:
    value = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, pos
    return None


def _run_stream_shape(stream: bytes, width: int) -> tuple[int, int] | None:
    """Sum and count of a stream read as runs of ``width``-byte values, if it is."""
    pos = 0
    total = 0
    runs = 0
    while pos < len(stream):
        token = _leb128(stream, pos)
        if token is None or token[0] & 1:
            return None
        total += token[0] >> 1
        pos = token[1] + width
        runs += 1
    return (total, runs) if pos == len(stream) else None


def _token_stream(data: bytes, column: Column) -> bytes:
    payload = data[
        column.payload_offset : column.payload_offset + column.payload_length - 2
    ]
    first = _leb128(payload, 1)
    assert first is not None
    second = _leb128(payload, first[1] + 2)
    assert second is not None
    return payload[second[1] + 1 :]


def _count_run_streams(data: bytes, column: Column, counts: Counter[str]) -> None:
    stream = _token_stream(data, column)
    for width in (1, 2, 4, 8):
        shape = _run_stream_shape(stream, width)
        if shape is not None and shape[0] == column.b:
            counts["runs_sum_to_b"] += 1
            if width == 1 and shape[1] == 1:
                counts["runs_single_of_1_byte"] += 1
            return
    counts["runs_other"] += 1


def _count_directory(
    data: bytes, directory: AssetDirectory | None, counts: Counter[str]
) -> None:
    assert directory is not None
    if directory.table_bytes == 16 * directory.count:
        counts["directory_16"] += 1
        starts = set()
        for entry in directory.entries:
            starts.add(entry.offset)
            counts["directory_entries"] += 1
            end = entry.offset + entry.length
            complete = (
                data[entry.offset : entry.offset + 8] == _PNG_SIGNATURE
                and data[end - 8 : end - 4] == b"IEND"
            )
            counts["directory_entries_complete_png"] += complete
        at = data.find(_PNG_SIGNATURE)
        while at >= 0:
            counts["png_signatures"] += 1
            counts["png_signatures_at_entry"] += at in starts
            at = data.find(_PNG_SIGNATURE, at + 1)
    elif directory.table_bytes == 36 * directory.count:
        counts["directory_36"] += 1
    else:
        counts["directory_neither"] += 1


def _count_column(data: bytes, column: Column, counts: Counter[str]) -> None:
    counts["ce_fa_ended"] += 1
    form = data[column.payload_offset]
    counts["payload_05"] += form == 0x05
    counts["payload_04"] += form == 0x04
    counts["payload_05_no_marker"] += column.undecoded is Undecoded.NO_MARKER
    if column.a is None or column.b is None:
        return

    counts["heads"] += 1
    counts["a_eq_b"] += column.a == column.b
    counts["a_gt_b"] += column.a > column.b
    counts["a_lt_b"] += column.a < column.b
    counts["head_byte_00"] += column.head_byte == 0
    if column.undecoded is Undecoded.RUN_TOKEN:
        counts["even_first"] += 1
        _count_run_streams(data, column, counts)
        return

    counts["odd_first"] += 1
    if column.undecoded is Undecoded.COUNT_MISMATCH:
        counts["block_count_differs"] += 1
        return
    counts["block_count_is_b"] += 1
    counts["table_bits_12"] += column.undecoded is not Undecoded.TABLE_BITS
    if column.undecoded is not None:
        return

    counts["fpc_decoded"] += 1
    trailing = column.trailing
    counts["fpc_exact"] += not trailing
    counts["fpc_trailing"] += bool(trailing)
    if trailing:
        assert column.a is not None and column.b is not None
        empty = column.a - column.b
        form = b"\xff\xff" + _enc(empty) + b"\x01" + _enc(2 * empty) + b"\x00"
        is_empty_rows = trailing == form
        counts["trailer_empty_rows"] += is_empty_rows
        counts["trailer_empty_rows_6_bytes"] += is_empty_rows and len(trailing) == 6
        counts["trailer_empty_rows_8_bytes"] += is_empty_rows and len(trailing) == 8
        counts["trailer_other"] += not is_empty_rows


def _enc(value: int) -> bytes:
    out = bytearray()
    while value > 0x7F:
        out.append(value & 0x7F | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _measure(corpus: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    format_versions = []
    versions = []
    for path in sorted(corpus.iterdir()):
        data = path.read_bytes()
        doc = parse(data)
        counts["files"] += 1
        format_versions.append(doc.header.format_version)
        counts["candidates"] += sum(1 for _ in _DESCRIPTOR_HEAD.finditer(data))
        if doc.global_header is not None:
            counts["global_header_and_terminator"] += 1
            counts["build_matches"] += doc.header.build == doc.global_header.build
            versions.append(doc.global_header.version)
        _count_directory(data, doc.asset_directory, counts)
        for column in doc.columns:
            _count_column(data, column, counts)
    counts["format_version_min"] = round(float(min(format_versions)) * 10_000)
    counts["format_version_max"] = round(float(max(format_versions)) * 10_000)
    counts["version_min"] = round(min(versions) * 100)
    counts["version_max"] = round(max(versions) * 100)
    return counts


@pytest.fixture(scope="module")
def measured() -> Counter[str]:
    location = os.environ.get("OPJ_CORPUS")
    if not location:
        pytest.skip("corpus not available (set OPJ_CORPUS)")
    return _measure(Path(location))


@pytest.mark.parametrize(
    "figure, expected",
    [
        # §1, §3
        ("files", 98),
        ("build_matches", 98),
        ("global_header_and_terminator", 98),
        ("format_version_min", 43330),
        ("format_version_max", 43774),
        ("version_min", 960),
        ("version_max", 1030),
        # §2.4
        ("directory_16", 51),
        ("directory_36", 42),
        ("directory_neither", 5),
        ("directory_entries_complete_png", 131),
        ("directory_entries", 131),
        ("png_signatures_at_entry", 131),
        ("png_signatures", 135),
        # §2.6 descriptors and payload forms
        ("candidates", 1039),
        ("ce_fa_ended", 903),
        ("payload_05", 855),
        ("payload_04", 48),
        ("payload_05_no_marker", 39),
        ("heads", 816),
        ("a_eq_b", 379),
        ("a_gt_b", 437),
        ("a_lt_b", 0),
        ("head_byte_00", 786),
        # §2.6 token stream
        ("odd_first", 795),
        ("block_count_is_b", 705),
        ("block_count_differs", 90),
        ("even_first", 21),
        ("runs_sum_to_b", 14),
        ("runs_single_of_1_byte", 12),
        ("runs_other", 7),
        # §2.6 FPC blocks
        ("table_bits_12", 705),
        ("fpc_decoded", 705),
        ("fpc_exact", 294),
        ("fpc_trailing", 411),
        ("trailer_empty_rows", 391),
        ("trailer_empty_rows_6_bytes", 384),
        ("trailer_empty_rows_8_bytes", 7),
        ("trailer_other", 20),
    ],
)
def test_spec_figure(measured: Counter[str], figure: str, expected: int) -> None:
    assert measured[figure] == expected
