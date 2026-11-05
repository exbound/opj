"""CLI: ``python -m opj <file.opju>`` — print the recovered structure."""

from __future__ import annotations

import sys

from .opju import AssetDirectory, Column, OpjError, read

#: How many leading values to print per column.
SHOWN_VALUES = 5


def _directory_summary(directory: AssetDirectory | None) -> str:
    if directory is None:
        return "absent"
    if directory.unsupported is not None:
        return f"@{directory.offset:#08x} unsupported: {directory.unsupported}"
    return f"@{directory.offset:#08x} {len(directory.entries)} entries of 16 bytes"


def _column_line(column: Column) -> str:
    name = column.name if column.name is not None else "?"
    where = f"  @{column.payload_offset:#08x} {name}"
    if column.undecoded is not None:
        rows = column.b if column.b is not None else "?"
        return f"{where} rows={rows} undecoded: {column.undecoded}"
    shown = ", ".join(f"{v:g}" for v in column.values[:SHOWN_VALUES])
    more = ", ..." if len(column.values) > SHOWN_VALUES else ""
    return f"{where} rows={len(column.values)} values: {shown}{more}"


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
    print(f"header records  {len(doc.header_records)}")
    for r in doc.header_records:
        shown = r.text if r.text is not None else r.payload.hex(" ")
        print(f"  @{r.offset:#08x} tag={r.tag:02x} len={r.length:<3d} {shown}")
    print(f"records         {len(doc.records)} (resync walk)")
    print(f"recovered names ({len(doc.names)}):")
    for r in doc.names:
        print(f"  @{r.offset:#08x} tag={r.tag:02x} {r.text}")
    print(f"columns ({len(doc.columns)}):")
    for column in doc.columns:
        print(_column_line(column))
    if doc.gaps:
        print("undecoded regions:")
        for off, note in doc.gaps:
            print(f"  @{off:#08x} {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
