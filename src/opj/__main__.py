"""CLI: ``python -m opj <file.opju>`` — print the recovered structure."""

from __future__ import annotations

import sys

from .opju import OpjError, read


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
    print(f"object count    {h.object_count}")
    print(f"root header     {doc.root_header.hex(' ')}")
    print(f"header records  {len(doc.header_records)}")
    for r in doc.header_records:
        shown = r.text if r.text is not None else r.payload.hex(" ")
        print(f"  @{r.offset:#08x} tag={r.tag:02x} len={r.length:<3d} {shown}")
    print(f"records         {len(doc.records)} (resync walk)")
    print(f"recovered names ({len(doc.names)}):")
    for r in doc.names:
        print(f"  @{r.offset:#08x} tag={r.tag:02x} {r.text}")
    if doc.gaps:
        print("undecoded regions:")
        for off, note in doc.gaps:
            print(f"  @{off:#08x} {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
