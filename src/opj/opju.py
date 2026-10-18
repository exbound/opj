"""Reader for Origin ``.opju`` project files.

Implements the format described in ``docs/engineering-spec.md``. The body is a
tag/length/value byte stream read by a forward cursor; text and short scalar
records use ``[tag 0x80-0x9F][length byte][payload]`` framing, interleaved with
opaque embedded-asset regions (preview thumbnail, images) that this reader does
not yet decode and instead records as gaps.

Everything here is derived from the functional specification and the observable
bytes of publicly published sample files.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

# --- format constants (see docs/engineering-spec.md) -----------------------

MAGIC_FAMILY_PREFIX = b"CPY"
#: The Unicode .opju header magic token.
MAGIC_OPJU = b"CPYUA"
#: Length of the per-document root header that precedes the record stream.
ROOT_HEADER_LEN = 7
#: Bytes [2:6] of the root header are constant across documents.
ROOT_HEADER_CONST = bytes((0x6C, 0xC0, 0x11, 0x01))
#: Inclusive tag range for length-prefixed value records (text / short scalar).
TAG_VALUE_MIN = 0x80
TAG_VALUE_MAX = 0x9F
#: ASCII marker that introduces the embedded preview thumbnail.
PREVIEW_MARKER = b"PrvwOPJU"


class OpjError(Exception):
    """Raised when a buffer is not a recognizable Origin project file."""


@dataclass(frozen=True)
class Header:
    """The parsed header line and the offset where the body begins."""

    magic: str
    format_version: str
    object_count: int
    body_offset: int  # offset of the first body byte (after the header newline)


@dataclass(frozen=True)
class Record:
    """One length-prefixed value record from the tag/length/value stream."""

    offset: int
    tag: int
    length: int
    payload: bytes

    @property
    def is_text(self) -> bool:
        """True if the payload is entirely printable, non-NUL bytes."""
        return len(self.payload) > 0 and all(0x20 <= b < 0x7F for b in self.payload)

    @property
    def text(self) -> str | None:
        """The payload decoded as text, or ``None`` if it is not text."""
        if not self.is_text:
            return None
        return self.payload.decode("latin-1")

    def as_u16(self) -> int | None:
        return struct.unpack("<H", self.payload)[0] if self.length == 2 else None

    def as_u32(self) -> int | None:
        return struct.unpack("<I", self.payload)[0] if self.length == 4 else None

    def as_f64(self) -> float | None:
        return struct.unpack("<d", self.payload)[0] if self.length == 8 else None


@dataclass
class Document:
    """The result of parsing an Origin project file."""

    header: Header
    root_header: bytes
    #: Records of the clean document-header TLV (from body+7 to the first gap).
    header_records: list[Record] = field(default_factory=list)
    #: All value records recovered from the body by the resync walk (§2), in
    #: file order, skipping the opaque embedded-asset regions.
    records: list[Record] = field(default_factory=list)
    #: Length-prefixed text records (object names, comments) — the text subset
    #: of ``records``.
    names: list[Record] = field(default_factory=list)
    #: (offset, note) for regions the reader does not yet decode.
    gaps: list[tuple[int, str]] = field(default_factory=list)

    @property
    def name_strings(self) -> list[str]:
        return [r.text for r in self.names if r.text is not None]


# --- parsing ---------------------------------------------------------------


def parse_header(data: bytes) -> Header:
    """Parse the ASCII header line. Raises :class:`OpjError` if absent."""
    if not data.startswith(MAGIC_FAMILY_PREFIX):
        raise OpjError("not an Origin project file: missing CPY* magic")
    nl = data.find(b"\n")
    if nl < 0:
        raise OpjError("truncated: no header-line terminator")
    line = data[:nl].decode("latin-1")
    parts = line.split()
    if len(parts) < 3:
        raise OpjError(f"unexpected header line: {line!r}")
    magic, version, count = parts[0], parts[1], parts[2]
    try:
        object_count = int(count)
    except ValueError as exc:
        raise OpjError(f"header object-count not an integer: {count!r}") from exc
    return Header(
        magic=magic,
        format_version=version,
        object_count=object_count,
        body_offset=nl + 1,
    )


def root_header(data: bytes, header: Header) -> bytes:
    """Return the 7-byte per-document root header that precedes the stream."""
    start = header.body_offset
    end = start + ROOT_HEADER_LEN
    if end > len(data):
        raise OpjError("truncated: body shorter than the root header")
    return data[start:end]


def iter_header_records(data: bytes, start: int) -> Iterator[Record]:
    """Yield value records from ``start`` until the first non-value tag.

    Walks the clean document-header TLV. Stops at the first byte that is not a
    ``0x80-0x9F`` tag (the first opaque / embedded-asset region).
    """
    i = start
    n = len(data)
    while i + 1 < n:
        tag = data[i]
        if not (TAG_VALUE_MIN <= tag <= TAG_VALUE_MAX):
            return
        length = data[i + 1]
        payload = data[i + 2 : i + 2 + length]
        if len(payload) < length:  # truncated
            return
        yield Record(offset=i, tag=tag, length=length, payload=payload)
        i += 2 + length


def _record_at(data: bytes, i: int, max_len: int) -> Record | None:
    """Parse a value record at ``i``, or ``None`` if the framing does not hold."""
    n = len(data)
    if i + 1 >= n:
        return None
    tag = data[i]
    if not (TAG_VALUE_MIN <= tag <= TAG_VALUE_MAX):
        return None
    length = data[i + 1]
    if length > max_len or i + 2 + length > n:
        return None
    return Record(
        offset=i, tag=tag, length=length, payload=data[i + 2 : i + 2 + length]
    )


def iter_records(
    data: bytes, start: int, run: int = 4, max_len: int = 0x60
) -> Iterator[Record]:
    """Walk the body as value records, resyncing across opaque regions.

    Parses ``[tag 0x80-0x9F][length][payload]`` records forward. On a byte that
    is not a value record (an embedded-asset / binary-data region), scans forward
    one byte at a time until ``run`` consecutive records parse cleanly, then
    resumes there. This recovers the document skeleton and the object-name
    inventory despite the binary blobs that interrupt a naive linear walk.

    Caveat: records that fall *inside* a binary data/asset sub-blob may be
    coincidental, not real fields. Only distinctive text (names, comments) is
    reliable; callers validate against an expected inventory rather than trusting
    every record. See docs/engineering-spec.md §2.6.
    """
    n = len(data)
    i = start
    while i + 1 < n:
        rec = _record_at(data, i, max_len)
        if rec is not None:
            yield rec
            i += 2 + rec.length
            continue
        # opaque region: find the next point where `run` records parse cleanly
        j = i + 1
        while j + 1 < n:
            p = j
            ok = 0
            while ok < run:
                r = _record_at(data, p, max_len)
                if r is None:
                    break
                p += 2 + r.length
                ok += 1
            if ok >= run:
                break
            j += 1
        if j + 1 >= n:
            return
        i = j


def _find_gaps(data: bytes, records: list[Record], start: int) -> list[tuple[int, str]]:
    """Report the opaque spans between recovered records, plus the preview."""
    gaps: list[tuple[int, str]] = []
    cursor = start
    for r in records:
        if r.offset > cursor:
            gaps.append((cursor, f"opaque region ({r.offset - cursor} bytes)"))
        cursor = r.offset + 2 + r.length
    if cursor < len(data):
        gaps.append((cursor, f"trailing region ({len(data) - cursor} bytes)"))
    marker = data.find(PREVIEW_MARKER)
    if marker >= 0:
        gaps.append((marker, "preview thumbnail (PrvwOPJU): serialized bitmap"))
    return gaps


def scan_named_records(
    data: bytes, start: int, min_len: int = 4, max_len: int = 0x7F
) -> list[Record]:
    """Globally scan for length-prefixed text records (object names, comments).

    Robust to the opaque embedded-asset regions that interrupt a linear walk:
    advances one byte at a time, accepting a ``[tag][len][len printable bytes]``
    record wherever the framing holds. May include occasional coincidental
    matches; callers validate against an expected inventory rather than trusting
    every hit.
    """
    out: list[Record] = []
    i = start
    n = len(data)
    while i + 1 < n:
        tag = data[i]
        if TAG_VALUE_MIN <= tag <= TAG_VALUE_MAX:
            length = data[i + 1]
            if min_len <= length <= max_len and i + 2 + length <= n:
                payload = data[i + 2 : i + 2 + length]
                if all(0x20 <= b < 0x7F for b in payload):
                    out.append(
                        Record(offset=i, tag=tag, length=length, payload=payload)
                    )
                    i += 2 + length
                    continue
        i += 1
    return out


def parse(data: bytes) -> Document:
    """Parse an in-memory ``.opju`` buffer into a :class:`Document`."""
    header = parse_header(data)
    root = root_header(data, header)
    stream_start = header.body_offset + ROOT_HEADER_LEN

    header_records = list(iter_header_records(data, stream_start))
    records = list(iter_records(data, stream_start))

    doc = Document(
        header=header,
        root_header=root,
        header_records=header_records,
        records=records,
    )
    doc.names = [r for r in records if r.is_text and r.length >= 4]
    doc.gaps = _find_gaps(data, records, stream_start)
    return doc


def read(path: str | Path) -> Document:
    """Read and parse an ``.opju`` file from disk."""
    return parse(Path(path).read_bytes())
