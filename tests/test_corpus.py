"""Reproduce the specification's corpus counts.

Set ``OPJ_CORPUS`` to a flat directory holding the 98 public files named in
docs/engineering-spec.md §3. Every expected value here is a figure the
specification states, unless its comment says otherwise.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ElementTree
from collections import Counter
from pathlib import Path

import pytest

from opj import (
    SPECIAL_64,
    SPECIAL_65,
    AssetDirectory,
    Column,
    CurveJoinProblem,
    Document,
    RunLengthError,
    Special,
    StringSection,
    Value,
    parse,
)
from opj.opju import SETTINGS_ROOT, STRING_HEADER, iter_expanded

_DESCRIPTOR_HEAD = re.compile(rb"[\x80-\x9f]\x18\x18\x00{7}")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


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


def _count_values(column: Column, counts: Counter[str]) -> None:
    for value, integer in zip(
        column.values, column.integers or [None] * len(column.values), strict=True
    ):
        if integer is not None:
            counts["rows_integer"] += 1
        elif value == SPECIAL_64:
            counts["rows_special_64"] += 1
        elif value == SPECIAL_65:
            counts["rows_special_65"] += 1
        elif isinstance(value, float):
            counts["rows_double"] += 1
        elif isinstance(value, str | bytes):
            counts["rows_string"] += 1
        else:
            counts["rows_other"] += 1


def _is_filled(value: Value) -> bool:
    """Filled excludes empty rows and the special codes."""
    if value is None or isinstance(value, Special):
        return False
    return not isinstance(value, str | bytes) or len(value) > 0


def _count_record_fields(column: Column, counts: Counter[str]) -> None:
    counts["total_rows_is_row_count"] += column.total_rows == len(column.values)
    counts["value_size_10"] += column.value_size == 10
    filled = [i for i, value in enumerate(column.values) if _is_filled(value)]
    if filled:
        counts["columns_with_data"] += 1
        counts["first_row_is_first_filled"] += column.first_row == filled[0]
        counts["last_row_is_last_filled"] += column.last_row == filled[-1] + 1


def _count_join(column: Column, counts: Counter[str]) -> None:
    counts["columns_reaching_a_record"] += column.curves_in_book > 0
    counts["reached_record_unique_in_book"] += column.curves_in_book == 1
    counts["fewer_records_than_sheet"] += (
        column.curve_problem is CurveJoinProblem.FEWER_RECORDS
    )
    failed = column.curve_problem is CurveJoinProblem.NO_RECORD
    counts["no_record"] += failed
    counts["no_record_at_3"] += failed and (column.name or "").endswith("@3")
    if column.column_type is not None:
        counts["typed"] += 1
        counts[f"type_{column.column_type.name}"] += 1


def _count_column(data: bytes, column: Column, counts: Counter[str]) -> None:
    counts["ce_fa_ended"] += 1
    counts["undecoded"] += column.undecoded is not None
    counts["rows_equal_a"] += (
        column.undecoded is None and len(column.values) == column.a
    )
    counts["payload_05"] += column.kind == 0x05
    counts["payload_04"] += column.kind == 0x04

    size = None if column.record is None else len(column.record)
    counts["record_147"] += size == 147
    # The name is what sits at the stated offset, and the reader took it from there.
    if column.record is not None and column.name is not None:
        name = column.name.encode()
        counts["name_at_0x58"] += size == 147 and column.record[0x58:].startswith(name)
    frames = sum(
        1
        for n in range(1, 256)
        if column.offset - n - 2 >= 0
        and data[column.offset - n - 2] == 0x80
        and data[column.offset - n - 1] == n
    )
    counts["columns_with_several_frames"] += frames > 1
    _count_record_fields(column, counts)
    _count_join(column, counts)

    for segment in column.segments:
        if segment.selector:
            counts[f"segments_selector_{segment.flag}"] += 1
        else:
            counts["segments_ff_ff"] += 1
            counts["segments_flag_0_kind_05"] += (
                segment.flag == 0 and column.kind == 0x05
            )
            counts["segments_flag_0_kind_04"] += (
                segment.flag == 0 and column.kind == 0x04
            )
            counts["segments_flag_3"] += segment.flag == 3

    _count_values(column, counts)

    if column.integers:
        has_run_segment = any(s.flag == 1 and not s.selector for s in column.segments)
        counts["integer_columns"] += 1
        counts["integer_columns_on_string_starts"] += column.resolution_problem is None
        counts["integer_columns_in_run_segments"] += has_run_segment
        counts["integer_columns_only_in_selector_segments"] += not has_run_segment


def _count_section(section: StringSection, counts: Counter[str]) -> None:
    counts["string_sections"] += 1
    counts["lz4_exact"] += section.undecoded is None
    counts["section_count_is_strings"] += section.count == len(section.strings)
    string_bytes = section.usize - STRING_HEADER.size
    counts["section_bytes_is_string_bytes"] += section.byte_count == string_bytes


def _expanded_to_nul(data: bytes, start: int) -> bytes | None:
    out = bytearray()
    try:
        for chunk in iter_expanded(data, start):
            nul = chunk.find(0)
            if nul >= 0:
                return bytes(out + chunk[:nul])
            out += chunk
    except RunLengthError:
        pass
    return None


def _well_formed_documents(data: bytes) -> list[bytes]:
    """Every document of the settings form. Callers keep only shapes, not values.

    The root can sit inside a literal run that follows a zero-run token, so each
    literal token that could hold it is tried.
    """
    documents = []
    at = data.find(SETTINGS_ROOT, 2)
    while at >= 0:
        for before in range(128):
            token_at = at - 1 - before
            if token_at < 1:
                break
            token = data[token_at]
            if not before < token < 0x80 or not 0x80 <= data[token_at - 1] < 0xC0:
                continue
            expanded = _expanded_to_nul(data, token_at)
            if expanded is None or not expanded.startswith(SETTINGS_ROOT, before):
                continue
            try:
                ElementTree.fromstring(expanded[before:])
            except ElementTree.ParseError:
                continue
            documents.append(expanded[before:])
            break
        at = data.find(SETTINGS_ROOT, at + 1)
    return documents


def _count_settings(
    data: bytes, doc: Document, counts: Counter[str], tools: set[str]
) -> None:
    if doc.settings_xml is None:
        return
    root = ElementTree.fromstring(doc.settings_xml)
    counts["settings_well_formed"] += 1
    counts["settings_origin_storage_root"] += root.tag == "OriginStorage"
    tools.update(child.tag for child in root)

    first, *_ = _well_formed_documents(data)
    counts["settings_is_first_document"] += first == doc.settings_xml


def _measure(corpus: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    format_versions = []
    versions = []
    tools: set[str] = set()
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
        sizes = {curve.size for curve in doc.curve_records}
        counts["curve_size_519_files"] += sizes == {519}
        counts["curve_size_515_files"] += sizes == {515}
        counts["curve_size_515_at_4_33"] += sizes == {
            515
        } and doc.header.format_version.startswith("4.33")
        for section in doc.string_sections:
            _count_section(section, counts)
        for column in doc.columns:
            _count_column(data, column, counts)
        _count_settings(data, doc, counts, tools)
    counts["settings_tool_names"] = len(tools)
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
        # §2.5
        ("candidates", 1039),
        ("ce_fa_ended", 903),
        ("record_147", 903),
        ("name_at_0x58", 903),
        ("columns_with_several_frames", 6),
        ("total_rows_is_row_count", 903),
        ("columns_with_data", 891),
        ("first_row_is_first_filled", 890),
        ("last_row_is_last_filled", 866),
        ("value_size_10", 882),
        # §2.6 grammar
        ("undecoded", 0),
        ("rows_equal_a", 903),
        ("payload_05", 855),
        ("payload_04", 48),
        ("segments_ff_ff", 1323),
        ("segments_selector_0", 110),
        ("segments_selector_1", 48),
        ("segments_flag_0_kind_05", 790),
        ("segments_flag_0_kind_04", 1),
        ("segments_flag_3", 1),
        # §2.6 rows
        ("rows_string", 18414),
        ("rows_integer", 4602),
        ("rows_double", 357062),
        ("rows_special_64", 1366),
        ("rows_special_65", 526),
        ("rows_other", 0),
        # §2.7
        ("string_sections", 48),
        ("lz4_exact", 48),
        ("integer_columns", 48),
        ("integer_columns_on_string_starts", 48),
        ("integer_columns_in_run_segments", 46),
        ("integer_columns_only_in_selector_segments", 2),
        ("section_count_is_strings", 4),
        ("section_bytes_is_string_bytes", 0),
        # §2.8
        ("settings_well_formed", 98),
        ("settings_origin_storage_root", 98),
        ("settings_tool_names", 225),
        ("settings_is_first_document", 98),
        # §2.9. The file counts are not in the specification: they pin which sizes
        # it states to which files.
        ("curve_size_519_files", 97),
        ("curve_size_515_files", 1),
        ("curve_size_515_at_4_33", 1),
        ("columns_reaching_a_record", 885),
        ("reached_record_unique_in_book", 618),
        ("fewer_records_than_sheet", 78),
        ("no_record", 18),
        ("no_record_at_3", 12),
        ("typed", 807),
        ("type_Y", 632),
        ("type_X", 139),
        ("type_Z", 16),
        ("type_Y_ERROR", 12),
        ("type_UNKNOWN_1", 8),
    ],
)
def test_spec_figure(measured: Counter[str], figure: str, expected: int) -> None:
    assert measured[figure] == expected
