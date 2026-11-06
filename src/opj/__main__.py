"""CLI: ``python -m opj <file.opju>`` — print the recovered structure."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ElementTree

from .opju import AssetDirectory, Column, OpjError, Value, read

#: How many leading values to print per column.
SHOWN_VALUES = 5
#: Longest string shown for one value.
SHOWN_TEXT = 24


def _directory_summary(directory: AssetDirectory | None) -> str:
    if directory is None:
        return "absent"
    if directory.unsupported is not None:
        return f"@{directory.offset:#08x} unsupported: {directory.unsupported}"
    return f"@{directory.offset:#08x} {len(directory.entries)} entries of 16 bytes"


def _settings_summary(document: bytes | None) -> str:
    # Only the shape: this document's values can hold paths and usernames.
    if document is None:
        return "absent"
    children = len(ElementTree.fromstring(document))
    return f"{len(document)} bytes, {children} direct children"


def _show(value: Value) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, str | bytes):
        return repr(value[:SHOWN_TEXT])
    return str(value)


def _column_line(column: Column) -> str:
    name = column.name if column.name is not None else "?"
    where = f"  @{column.payload_offset:#08x} {name}"
    if column.column_type is not None:
        where += f" type={column.column_type.name}"
    if column.long_name is not None:
        where += f" long={column.long_name!r}"
    if column.units is not None:
        where += f" units={column.units!r}"
    if column.undecoded is not None:
        return f"{where} undecoded: {column.undecoded}"
    shown = ", ".join(_show(v) for v in column.values[:SHOWN_VALUES])
    more = ", ..." if len(column.values) > SHOWN_VALUES else ""
    line = f"{where} rows={len(column.values)} values: {shown}{more}"
    if column.resolution_problem is not None:
        line += f" (unresolved: {column.resolution_problem})"
    if column.record_problem is not None:
        line += f" (record: {column.record_problem})"
    if column.curve_problem is not None:
        line += f" (no type: {column.curve_problem})"
    return line


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m opj <file.opju>", file=sys.stderr)
        return 2
    try:
        doc = read(args[0])
    except (OpjError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    h = doc.header
    print(f"magic           {h.magic}")
    print(f"format version  {h.format_version}")
    print(f"build           {h.build}")
    g = doc.global_header
    if g is None:
        print("version         absent")
    else:
        print(f"version         {g.version:g} (build {g.build}, {g.version_value:.6f})")
    print(f"asset directory {_directory_summary(doc.asset_directory)}")
    print(f"root header     {doc.root_header.hex(' ')}")
    print(f"settings        {_settings_summary(doc.settings_xml)}")
    print(f"string sections {len(doc.string_sections)}")
    print(f"text candidates {len(doc.names)} (literal-run scan)")
    print(f"columns ({len(doc.columns)}):")
    for column in doc.columns:
        print(_column_line(column))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
