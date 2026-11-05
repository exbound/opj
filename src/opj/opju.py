"""Reader for Origin ``.opju`` project files.

Implements the format described in ``docs/engineering-spec.md``. The body is a
tag/length/value byte stream read by a forward cursor; text and short scalar
records use ``[tag 0x80-0x9F][length byte][payload]`` framing, interleaved with
opaque regions that this reader records as gaps. The global header, the
16-byte asset directory, and FPC-compressed column data are decoded; forms the
specification does not establish are reported as undecoded, never guessed.

Everything here is derived from the functional specification and the observable
bytes of publicly published sample files.
"""

from __future__ import annotations

import math
import re
import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple

from . import fpc

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
#: ASCII marker that ends the opening records; the asset-directory offset follows.
PREVIEW_MARKER = b"PrvwOPJU"
#: Offset of the version value within the global header.
GLOBAL_HEADER_VERSION_OFFSET = 0x1B
#: Start of a column descriptor: any value tag, length 24, then u64 24.
COLUMN_DESCRIPTOR = re.compile(rb"[\x80-\x9f]\x18\x18\x00{7}")
#: Bytes from the start of a column descriptor to its payload.
COLUMN_DESCRIPTOR_LEN = 27
#: The last two bytes of a column payload.
COLUMN_PAYLOAD_END = b"\xce\xfa"
#: FPC table size in bits, the only value established for column blocks.
COLUMN_FPC_TABLE_BITS = 12


class OpjError(Exception):
    """Raised when a buffer is not a recognizable Origin project file."""


@dataclass(frozen=True)
class Header:
    """The parsed header line and the offset where the body begins."""

    magic: str
    format_version: str
    build: int
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


@dataclass(frozen=True)
class GlobalHeader:
    """The fixed-layout global header's established fields."""

    offset: int  # file offset of the header's first byte (after its size prefix)
    size: int
    #: The stored value: version plus build scaled by 10^-6, e.g. 9.600172.
    version_value: float
    version: float
    build: int


@dataclass(frozen=True)
class AssetEntry:
    """One 16-byte directory entry: an absolute file offset and a length."""

    offset: int
    length: int


@dataclass(frozen=True)
class AssetDirectory:
    """The asset directory. Only the 16-byte entry shape is decoded."""

    offset: int
    count: int
    table_bytes: int
    entries: tuple[AssetEntry, ...] = ()
    #: Why ``entries`` is empty when the directory is not the 16-byte shape.
    unsupported: str | None = None


class Undecoded(StrEnum):
    """Why a column's values were not decoded."""

    PAYLOAD_04 = "04 payload form is not established"
    PAYLOAD_UNKNOWN = "payload form is neither 05 nor 04"
    NO_MARKER = "05 payload lacks FF FF"
    MALFORMED_HEAD = "payload head or token stream is truncated"
    RUN_TOKEN = "stream starts with a run, whose value width is not established"
    COUNT_MISMATCH = "FPC block holds a different number of values than b"
    TABLE_BITS = "FPC table size is not the established 12 bits"
    OVERRUN = "FPC block overruns the token stream"


@dataclass(frozen=True)
class Column:
    """One column payload located by a validated descriptor."""

    offset: int  # file offset of the descriptor
    payload_offset: int
    payload_length: int
    #: The nearest preceding name record since the previous payload; best-effort.
    name: str | None
    #: Head fields of a 05 payload that has FF FF, else ``None``.
    a: int | None = None
    b: int | None = None
    head_byte: int | None = None
    values: tuple[float, ...] = ()
    #: Bytes between the end of an FPC block and the payload's end marker.
    trailing: bytes = b""
    undecoded: Undecoded | None = None


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
    #: ``None`` when the preview field or the header's framing is absent.
    global_header: GlobalHeader | None = None
    #: ``None`` when the preview field is absent.
    asset_directory: AssetDirectory | None = None
    #: Column payloads in file order, decoded or marked undecoded.
    columns: list[Column] = field(default_factory=list)

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
    magic, version, build_token = parts[0], parts[1], parts[2]
    try:
        build = int(build_token)
    except ValueError as exc:
        raise OpjError(f"header build not an integer: {build_token!r}") from exc
    return Header(
        magic=magic,
        format_version=version,
        build=build,
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
        gaps.append((marker, "preview field (PrvwOPJU)"))
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


def parse_global_header(data: bytes) -> GlobalHeader | None:
    """Parse the global header after the preview field, if its framing holds."""
    marker = data.find(PREVIEW_MARKER)
    if marker < 0:
        return None
    start = marker + len(PREVIEW_MARKER) + 8
    if start + 5 > len(data) or data[start + 4] != 0x0A:
        return None
    size = int.from_bytes(data[start : start + 4], "little")
    offset = start + 5
    if size < GLOBAL_HEADER_VERSION_OFFSET + 8:
        return None
    # Closing newline, then the zero-size terminator.
    if data[offset + size : offset + size + 6] != b"\n\x00\x00\x00\x00\n":
        return None
    value = struct.unpack_from("<d", data, offset + GLOBAL_HEADER_VERSION_OFFSET)[0]
    if not math.isfinite(value):
        return None
    scaled = round(value * 1e6)
    return GlobalHeader(
        offset=offset,
        size=size,
        version_value=value,
        version=(scaled // 10_000) / 100,
        build=scaled % 10_000,
    )


def parse_asset_directory(data: bytes) -> AssetDirectory | None:
    """Parse the directory the preview field points to.

    Exposes 16-byte entries. Every other shape is reported through
    ``unsupported``, with no entries.
    """
    marker = data.find(PREVIEW_MARKER)
    field_end = marker + len(PREVIEW_MARKER) + 8
    if marker < 0 or field_end > len(data):
        return None
    offset = int.from_bytes(data[field_end - 8 : field_end], "little")
    if offset + 16 > len(data):
        return AssetDirectory(offset, 0, 0, unsupported="offset is past end of file")
    count, zero, table_bytes = struct.unpack_from("<QII", data, offset)

    def unsupported(reason: str) -> AssetDirectory:
        return AssetDirectory(offset, count, table_bytes, unsupported=reason)

    table_end = offset + 16 + table_bytes
    if zero != 0:
        return unsupported("reserved field is not zero")
    if count == 0:
        return unsupported("no entries")
    if table_bytes != 16 * count:
        return unsupported(f"{table_bytes} table bytes for {count} entries")
    if table_end > len(data):
        return unsupported("table runs past end of file")
    entries = tuple(
        AssetEntry(*pair)
        for pair in struct.iter_unpack("<QQ", data[offset + 16 : table_end])
    )
    if any(e.offset + e.length > len(data) for e in entries):
        return unsupported("an entry runs past end of file")
    return AssetDirectory(offset, count, table_bytes, entries)


def _leb128(data: bytes, pos: int) -> tuple[int, int] | None:
    """Read a LEB128 varint at ``pos``: its value and the offset after it."""
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


class _Payload(NamedTuple):
    a: int | None = None
    b: int | None = None
    head_byte: int | None = None
    values: tuple[float, ...] = ()
    trailing: bytes = b""
    undecoded: Undecoded | None = None


def _decode_payload(payload: bytes) -> _Payload:
    """Decode a column payload, including its two-byte end marker."""
    if payload[0] == 0x04:
        return _Payload(undecoded=Undecoded.PAYLOAD_04)
    if payload[0] != 0x05:
        return _Payload(undecoded=Undecoded.PAYLOAD_UNKNOWN)
    stream = payload[: -len(COLUMN_PAYLOAD_END)]

    head = _leb128(stream, 1)
    if head is None or stream[head[1] : head[1] + 2] != b"\xff\xff":
        return _Payload(undecoded=Undecoded.NO_MARKER)
    a, pos = head
    head = _leb128(stream, pos + 2)
    if head is None or head[1] >= len(stream):
        return _Payload(undecoded=Undecoded.MALFORMED_HEAD)
    b, pos = head
    parsed = _Payload(a, b, stream[pos])

    token = _leb128(stream, pos + 1)
    if token is None:
        return parsed._replace(undecoded=Undecoded.MALFORMED_HEAD)
    v, pos = token
    if not v & 1:
        return parsed._replace(undecoded=Undecoded.RUN_TOKEN)
    if (v >> 1) + 1 != b:
        return parsed._replace(undecoded=Undecoded.COUNT_MISMATCH)
    if pos >= len(stream):
        return parsed._replace(undecoded=Undecoded.MALFORMED_HEAD)
    if stream[pos] != COLUMN_FPC_TABLE_BITS:
        return parsed._replace(undecoded=Undecoded.TABLE_BITS)
    try:
        values, end = fpc.decode(stream, b, COLUMN_FPC_TABLE_BITS, pos + 1)
    except fpc.FpcError:
        return parsed._replace(undecoded=Undecoded.OVERRUN)
    return parsed._replace(values=tuple(values), trailing=stream[end:])


def _nearest_name(data: bytes, start: int, end: int) -> str | None:
    """The last text record in ``data[start:end]``, if any."""
    if start >= end:
        return None
    records = scan_named_records(data[start:end], 0)
    return records[-1].text if records else None


def parse_columns(data: bytes, start: int = 0) -> list[Column]:
    """Locate column payloads by descriptor, rejecting coincidental matches.

    A match counts only if its separator byte is in place, its stated length
    fits in the file, and that length ends on the payload end marker.
    """
    columns: list[Column] = []
    previous_end = start
    for match in COLUMN_DESCRIPTOR.finditer(data, start):
        offset = match.start()
        payload_offset = offset + COLUMN_DESCRIPTOR_LEN
        if payload_offset > len(data) or data[payload_offset - 1] != 0x0A:
            continue
        length = int.from_bytes(data[offset + 10 : offset + 18], "little")
        payload_end = payload_offset + length
        if payload_end > len(data) or length < len(COLUMN_PAYLOAD_END):
            continue
        if (
            data[payload_end - len(COLUMN_PAYLOAD_END) : payload_end]
            != COLUMN_PAYLOAD_END
        ):
            continue
        columns.append(
            Column(
                offset=offset,
                payload_offset=payload_offset,
                payload_length=length,
                name=_nearest_name(data, previous_end, offset),
                **_decode_payload(data[payload_offset:payload_end])._asdict(),
            )
        )
        previous_end = payload_end
    return columns


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
    doc.global_header = parse_global_header(data)
    doc.asset_directory = parse_asset_directory(data)
    doc.columns = parse_columns(data, stream_start)
    return doc


def read(path: str | Path) -> Document:
    """Read and parse an ``.opju`` file from disk."""
    return parse(Path(path).read_bytes())
