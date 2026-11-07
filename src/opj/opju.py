"""Reader for Origin ``.opju`` project files.

Implements the format described in ``docs/engineering-spec.md``. From body+7 the
stream is run-length coded (:func:`expand`). Column records and the settings
document are read through that layer; what looks like a tag, a length and a value
in the raw bytes is usually a zero run followed by a literal run. Column payloads
and string sections sit in the file uncoded. The rest of the stream is not walked
end to end: this reader finds its structures by their framing.

Everything here is derived from the functional specification and the observable
bytes of publicly published sample files.
"""

from __future__ import annotations

import bisect
import math
import re
import struct
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any

from . import fpc, lz4

# --- format constants (see docs/engineering-spec.md) -----------------------

MAGIC_FAMILY_PREFIX = b"CPY"
#: The Unicode .opju header magic token.
MAGIC_OPJU = b"CPYUA"
#: Length of the per-document root header that precedes the record stream.
ROOT_HEADER_LEN = 7
#: Bytes [2:6] of the root header are constant across documents.
ROOT_HEADER_CONST = bytes((0x6C, 0xC0, 0x11, 0x01))
#: Run-length tokens: below 0x80 literal, below 0xC0 zero run, else byte run.
ZERO_RUN_MIN = 0x80
BYTE_RUN_MIN = 0xC0
RUN_LENGTH_MASK = 0x3F
ZERO_RUN_BASE = 3
BYTE_RUN_BASE = 3
#: ASCII marker that ends the opening records; the asset-directory offset follows.
PREVIEW_MARKER = b"PrvwOPJU"
#: Offset of the version value within the global header.
GLOBAL_HEADER_VERSION_OFFSET = 0x1B
#: Start of a column descriptor, with the record's last token before it.
COLUMN_DESCRIPTOR = re.compile(rb"[\x80-\x9f]\x18\x18\x00{7}")
#: Bytes from the descriptor's ``18`` to its payload.
COLUMN_DESCRIPTOR_LEN = 26
#: The first byte of a column record's frame; its length byte follows.
COLUMN_RECORD_FRAME = 0x80
#: A column record's expanded size, and where its NUL-padded name sits.
COLUMN_RECORD_SIZE = 147
COLUMN_NAME_OFFSET = 0x58
#: Fields of the expanded column record: u32 rows, and the stored value size.
COLUMN_TOTAL_ROWS_OFFSET = 0x19
COLUMN_FIRST_ROW_OFFSET = 0x1D
COLUMN_LAST_ROW_OFFSET = 0x21
COLUMN_VALUE_SIZE_OFFSET = 0x3D
#: A curve record's expanded size, and its size at format version 4.33xx.
CURVE_RECORD_SIZE = 519
CURVE_RECORD_SIZE_4_33 = 515
#: Where a curve record holds its type byte and NUL-terminated short name.
CURVE_TYPE_OFFSET = 0x11
CURVE_NAME_OFFSET = 0x12
#: The most filler bytes seen between a curve record and its label.
LABEL_FILLER_MAX = 3
#: The label's line separator: long name, units, comment.
LABEL_LINE_SEPARATOR = b"\r\n"
#: The last two bytes of a column payload.
COLUMN_PAYLOAD_END = b"\xce\xfa"
#: Payload kinds.
KIND_STRING = 0x05
KIND_INTEGER = 0x04
#: The flag that makes a segment's values strings or integers, by kind.
FLAG_NON_DOUBLE = 1
#: FPC table size in bits, the only value established for column blocks.
COLUMN_FPC_TABLE_BITS = 12
#: A string section's framing, then its decoded header and header size.
STRING_SECTION = re.compile(rb"#_MSER_STRINGS_PSET\x05\x01")
STRING_HEADER_MAGIC = b"\x77\x11\x11\x11"
STRING_HEADER = struct.Struct("<4sHHIIII")
#: The settings document's root, reached by a zero run and a literal run.
SETTINGS_ROOT = b"<OriginStorage>"


class OpjError(Exception):
    """Raised when a buffer is not a recognizable Origin project file."""


class RunLengthError(OpjError):
    """Raised when a run-length token's operands run past the input."""


@dataclass(frozen=True)
class Header:
    """The parsed header line and the offset where the body begins."""

    magic: str
    format_version: str
    build: int
    body_offset: int  # offset of the first body byte (after the header newline)


@dataclass(frozen=True)
class Record:
    """A literal run found by scanning, with the token byte before its count."""

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


@dataclass(frozen=True)
class Special:
    """A compact-double code with no value bytes, whose meaning is not established.

    It is deliberately not a number: compare ``code`` with :data:`SPECIAL_64` and
    :data:`SPECIAL_65`.
    """

    code: int

    def __str__(self) -> str:
        return f"<special {self.code:02x}>"


SPECIAL_64 = Special(0x64)
SPECIAL_65 = Special(0x65)
_SPECIALS = {s.code: s for s in (SPECIAL_64, SPECIAL_65)}

#: One row: a double, a string (``bytes`` when it is not valid UTF-8), a special
#: code, ``None`` for integer 0 ("none"), or an ``int`` that could not be resolved.
Value = float | str | bytes | Special | int | None


class Undecoded(StrEnum):
    """Why a payload failed to parse."""

    KIND = "payload kind is neither 05 nor 04"
    TRUNCATED = "payload ends inside a segment, token or value"
    SELECTOR = "segment starts with neither FF FF nor selector 0 or 1"
    OVERFILL = "token holds more rows than its segment has left"
    ROW_COUNT = "segments do not hold exactly the payload's row count"
    COMPACT_CODE = "compact-double code has no established width"
    TABLE_BITS = "FPC table size is not the established 12 bits"
    OVERRUN = "FPC block overruns the payload"


@dataclass(frozen=True)
class Segment:
    """One payload segment. ``selector`` is true for the single-value form."""

    rows: int
    flag: int
    selector: bool


@dataclass(frozen=True)
class StringSection:
    """A string section: integer columns refer to its strings by offset."""

    offset: int
    usize: int
    #: The decoded header's fields, whose meaning is not fully established.
    count: int = 0
    byte_count: int = 0
    rows: int = 0
    #: Each string by its offset; ``bytes`` when it is not valid UTF-8.
    strings: dict[int, str | bytes] = field(default_factory=dict)
    #: Why ``strings`` is empty when the section could not be decoded.
    undecoded: str | None = None


class ColumnType(Enum):
    """A curve record's type byte. Other bytes keep their value as an unknown."""

    Y = 0
    Y_ERROR = 2
    X = 3
    LABEL = 4
    Z = 5
    X_ERROR = 6

    @classmethod
    def _missing_(cls, value: object) -> ColumnType | None:
        if not isinstance(value, int):
            return None
        member = object.__new__(cls)
        member._name_ = f"UNKNOWN_{value}"
        member._value_ = value
        return cls._value2member_map_.setdefault(value, member)  # type: ignore[return-value]

    @property
    def known(self) -> bool:
        return self.name in type(self).__members__


class CurveJoinProblem(StrEnum):
    """Why a column has no type or label."""

    NO_COLUMN_NAME = "column has no name of the form <book>_<col>"
    NO_RECORD = "no curve record of this name in the column's book"
    FEWER_RECORDS = "the book has fewer records of this name than the sheet index"


@dataclass(frozen=True)
class CurveRecord:
    """A curve record: a column's short name, its type, and any label after it."""

    offset: int
    size: int
    name: str | bytes
    column_type: ColumnType
    #: Raw label markup and any stray control bytes are kept as found.
    long_name: str | bytes | None = None
    units: str | bytes | None = None
    comment: str | bytes | None = None
    label_offset: int | None = None


@dataclass(frozen=True)
class Column:
    """One column: its coded record, descriptor, decoded payload, and metadata."""

    offset: int  # file offset of the descriptor's ``18``
    payload_offset: int
    payload_length: int
    #: The record's file offset (its ``80 <n>`` frame) and expanded bytes.
    record_offset: int | None = None
    record: bytes | None = None
    name: str | None = None
    #: Why ``name`` is ``None``: no frame expands to 147 bytes, or a bad name.
    record_problem: str | None = None
    kind: int | None = None
    #: The payload's row count; ``values`` holds this many unless undecoded.
    a: int | None = None
    segments: tuple[Segment, ...] = ()
    values: tuple[Value, ...] = ()
    #: Raw integers aligned to rows, ``None`` on other rows; empty if there are none.
    integers: tuple[int | None, ...] = ()
    #: The section integers were resolved through; the rule is a hypothesis.
    string_section_offset: int | None = None
    #: Rows whose integer stayed unresolved in ``values``, and why.
    unresolved_rows: int = 0
    resolution_problem: str | None = None
    undecoded: Undecoded | None = None
    #: Fields of the expanded record; ``None`` without a record.
    total_rows: int | None = None
    first_row: int | None = None
    last_row: int | None = None
    value_size: int | None = None
    #: From the joined curve record. The sheet rule (``<col>@k`` is the k-th record
    #: in the book) is a hypothesis. A joined record may have no label.
    column_type: ColumnType | None = None
    long_name: str | bytes | None = None
    units: str | bytes | None = None
    comment: str | bytes | None = None
    #: The joined record's offset, how many records of this name the book has, and
    #: why all four fields above are ``None`` when the join failed.
    curve_offset: int | None = None
    curves_in_book: int = 0
    curve_problem: CurveJoinProblem | None = None


@dataclass
class Document:
    """The result of parsing an Origin project file."""

    header: Header
    root_header: bytes
    #: Literal runs of text found by scanning: candidates, and they include text
    #: from stored XML, so treat them as inventory evidence and do not print them.
    names: list[Record] = field(default_factory=list)
    #: ``None`` when the preview field or the header's framing is absent.
    global_header: GlobalHeader | None = None
    #: ``None`` when the preview field is absent.
    asset_directory: AssetDirectory | None = None
    string_sections: list[StringSection] = field(default_factory=list)
    #: Column payloads in file order.
    columns: list[Column] = field(default_factory=list)
    #: Curve records past the last column payload, in file order.
    curve_records: list[CurveRecord] = field(default_factory=list)
    #: The settings document's raw UTF-8 bytes. Its values can hold local paths and
    #: usernames from other machines: do not log or copy them.
    settings_xml: bytes | None = None

    @property
    def name_strings(self) -> list[str]:
        return [r.text for r in self.names if r.text is not None]


# --- the run-length layer ---------------------------------------------------


def iter_expanded(
    data: bytes, start: int = 0, end: int | None = None
) -> Iterator[bytes]:
    """Yield the expansion of each token in ``data[start:end]``."""
    pos = start
    stop = len(data) if end is None else end
    while pos < stop:
        token = data[pos]
        pos += 1
        if token < ZERO_RUN_MIN:
            if pos + token > stop:
                raise RunLengthError(f"literal run overruns at offset {pos - 1}")
            yield data[pos : pos + token]
            pos += token
        elif token < BYTE_RUN_MIN:
            yield bytes((token & RUN_LENGTH_MASK) + ZERO_RUN_BASE)
        else:
            if pos >= stop:
                raise RunLengthError(f"byte run has no operand at offset {pos - 1}")
            yield data[pos : pos + 1] * ((token & RUN_LENGTH_MASK) + BYTE_RUN_BASE)
            pos += 1


def expand(data: bytes, start: int = 0, end: int | None = None) -> bytes:
    """Expand run-length tokens into the logical byte stream (spec §2.1)."""
    return b"".join(iter_expanded(data, start, end))


def _ends_in_zero_run(data: bytes, start: int, end: int) -> bool:
    """Whether tokens from ``start`` land exactly on ``end`` on a zero-run token."""
    pos = start
    token = 0
    while pos < end:
        token = data[pos]
        pos += 1
        if token < ZERO_RUN_MIN:
            pos += token
        elif token >= BYTE_RUN_MIN:
            pos += 1
    return pos == end and ZERO_RUN_MIN <= token < BYTE_RUN_MIN


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


def scan_named_records(
    data: bytes, start: int, min_len: int = 4, max_len: int = 0x7F
) -> list[Record]:
    """Scan for text held in literal runs (spec §2.2), without expanding the stream.

    Advances one byte at a time, accepting a zero-run token followed by a literal
    count and that many printable bytes. Names and comments are found this way,
    and so are coincidental matches and pieces of stored XML; callers validate
    against an expected inventory rather than trusting every hit.
    """
    out: list[Record] = []
    i = start
    n = len(data)
    while i + 1 < n:
        tag = data[i]
        if ZERO_RUN_MIN <= tag < BYTE_RUN_MIN:
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


def _text(raw: bytes) -> str | bytes:
    """Decode a stored string as UTF-8, leaving it as bytes if it is not valid."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw


# --- column payloads --------------------------------------------------------


class _Malformed(Exception):
    def __init__(self, reason: Undecoded) -> None:
        self.reason = reason


def _leb128(stream: bytes, pos: int) -> tuple[int, int]:
    """Read a LEB128 varint at ``pos``: its value and the offset after it."""
    value = 0
    shift = 0
    while pos < len(stream):
        byte = stream[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, pos
    raise _Malformed(Undecoded.TRUNCATED)


def _compact_double(stream: bytes, pos: int) -> tuple[float | Special, int]:
    """Read a code byte and the high bytes of a double whose low bytes are zero."""
    if pos >= len(stream):
        raise _Malformed(Undecoded.TRUNCATED)
    code = stream[pos]
    pos += 1
    special = _SPECIALS.get(code)
    if special is not None:
        return special, pos
    width, remainder = divmod(code - 8, 9)
    if remainder or not 1 <= width <= 8:
        raise _Malformed(Undecoded.COMPACT_CODE)
    if pos + width > len(stream):
        raise _Malformed(Undecoded.TRUNCATED)
    raw = bytes(8 - width) + stream[pos : pos + width]
    return struct.unpack("<d", raw)[0], pos + width


def _string(stream: bytes, pos: int) -> tuple[str | bytes, int]:
    length, pos = _leb128(stream, pos)
    if pos + length > len(stream):
        raise _Malformed(Undecoded.TRUNCATED)
    return _text(stream[pos : pos + length]), pos + length


def _single(stream: bytes, pos: int, kind: int, flag: int) -> tuple[Value, int]:
    """Read one value of the type that the flag and kind select."""
    if flag != FLAG_NON_DOUBLE:
        return _compact_double(stream, pos)
    if kind == KIND_STRING:
        return _string(stream, pos)
    return _leb128(stream, pos)


def _literals(
    stream: bytes, pos: int, kind: int, flag: int, count: int
) -> tuple[list[Value], int]:
    """Read a token's literal values: an FPC block, or values one after another."""
    if flag != FLAG_NON_DOUBLE:
        if pos >= len(stream):
            raise _Malformed(Undecoded.TRUNCATED)
        if stream[pos] != COLUMN_FPC_TABLE_BITS:
            raise _Malformed(Undecoded.TABLE_BITS)
        try:
            doubles, pos = fpc.decode(stream, count, COLUMN_FPC_TABLE_BITS, pos + 1)
        except fpc.FpcError as exc:
            raise _Malformed(Undecoded.OVERRUN) from exc
        return list(doubles), pos
    values: list[Value] = []
    for _ in range(count):
        value, pos = _single(stream, pos, kind, flag)
        values.append(value)
    return values, pos


def _segments(
    stream: bytes, pos: int, kind: int, a: int
) -> tuple[list[Segment], list[Value]]:
    """Read segments to the end of ``stream``, holding exactly ``a`` rows."""
    segments: list[Segment] = []
    rows: list[Value] = []
    while pos < len(stream):
        mark = stream[pos : pos + 2]
        if mark in (b"\x00\x00", b"\x01\x00"):
            flag = mark[0]
            value, pos = _single(stream, pos + 2, kind, flag)
            rows.append(value)
            segments.append(Segment(1, flag, True))
        elif mark == b"\xff\xff":
            n, pos = _leb128(stream, pos + 2)
            if pos >= len(stream):
                raise _Malformed(Undecoded.TRUNCATED)
            flag = stream[pos]
            pos += 1
            if len(rows) + n > a:
                raise _Malformed(Undecoded.ROW_COUNT)
            filled = 0
            while filled < n:
                v, pos = _leb128(stream, pos)
                count = (v >> 1) + 1 if v & 1 else v >> 1
                if filled + count > n:
                    raise _Malformed(Undecoded.OVERFILL)
                if v & 1:
                    literal, pos = _literals(stream, pos, kind, flag, count)
                    rows += literal
                else:
                    value, pos = _single(stream, pos, kind, flag)
                    rows += [value] * count
                filled += count
            segments.append(Segment(n, flag, False))
        elif len(mark) < 2:
            raise _Malformed(Undecoded.TRUNCATED)
        else:
            raise _Malformed(Undecoded.SELECTOR)
    if len(rows) != a:
        raise _Malformed(Undecoded.ROW_COUNT)
    return segments, rows


def _resolve(
    rows: list[Value], sections: list[StringSection], payload_end: int
) -> tuple[list[Value], int | None, int, str | None]:
    """Resolve integer rows through the first string section after the payload.

    Returns the rows, the section's offset, the count of rows left as integers,
    and why they were. Integer 0 means none and becomes ``None``.
    """
    integer_rows = sum(1 for row in rows if isinstance(row, int))
    index = bisect.bisect_left([s.offset for s in sections], payload_end)
    if index == len(sections):
        return rows, None, integer_rows, "no string section after the payload"
    section = sections[index]
    if section.undecoded is not None:
        return rows, section.offset, integer_rows, section.undecoded

    resolved: list[Value] = []
    misses = 0
    for row in rows:
        if not isinstance(row, int):
            resolved.append(row)
        elif row == 0:
            resolved.append(None)
        elif row - 1 in section.strings:
            resolved.append(section.strings[row - 1])
        else:
            misses += 1
            resolved.append(row)
    problem = f"{misses} integers are not on a string start" if misses else None
    return resolved, section.offset, misses, problem


def _decode_payload(
    payload: bytes,
    payload_end: int,
    sections: list[StringSection],
) -> dict[str, Any]:
    """Decode a payload, including its end marker, into ``Column`` fields."""
    stream = payload[: -len(COLUMN_PAYLOAD_END)]
    kind = stream[0] if stream else None
    if kind not in (KIND_STRING, KIND_INTEGER):
        return {"undecoded": Undecoded.KIND}
    a = None
    try:
        a, pos = _leb128(stream, 1)
        segments, rows = _segments(stream, pos, kind, a)
    except _Malformed as exc:
        return {"kind": kind, "a": a, "undecoded": exc.reason}

    fields: dict[str, Any] = {"kind": kind, "a": a, "segments": tuple(segments)}
    if any(isinstance(row, int) for row in rows):
        fields["integers"] = tuple(r if isinstance(r, int) else None for r in rows)
        rows, offset, unresolved, problem = _resolve(rows, sections, payload_end)
        fields["string_section_offset"] = offset
        fields["unresolved_rows"] = unresolved
        fields["resolution_problem"] = problem
    fields["values"] = tuple(rows)
    return fields


# --- column records ---------------------------------------------------------


def _record_fields(data: bytes, descriptor: int, start: int) -> dict[str, Any]:
    """Find a column's coded record: the nearest frame that expands to 147 bytes.

    ``80 <n>`` can also occur by coincidence inside the coded bytes, so a nearer
    frame of another size is skipped rather than accepted.
    """
    for n in range(1, 256):
        frame = descriptor - n - 2
        if frame < start:
            break
        if data[frame] != COLUMN_RECORD_FRAME or data[frame + 1] != n:
            continue
        if not _ends_in_zero_run(data, frame + 2, descriptor):
            continue
        record = expand(data, frame + 2, descriptor)
        if len(record) != COLUMN_RECORD_SIZE:
            continue
        return {
            "record_offset": frame,
            "record": record,
            "total_rows": _u32(record, COLUMN_TOTAL_ROWS_OFFSET),
            "first_row": _u32(record, COLUMN_FIRST_ROW_OFFSET),
            "last_row": _u32(record, COLUMN_LAST_ROW_OFFSET),
            "value_size": record[COLUMN_VALUE_SIZE_OFFSET],
            **_record_name(record),
        }
    return {"record_problem": f"no frame expands to {COLUMN_RECORD_SIZE} bytes"}


def _u32(record: bytes, offset: int) -> int:
    return int.from_bytes(record[offset : offset + 4], "little")


def _record_name(record: bytes) -> dict[str, Any]:
    name = _text(record[COLUMN_NAME_OFFSET:].split(b"\x00", 1)[0])
    if not name or isinstance(name, bytes):
        return {"record_problem": "no UTF-8 name at the name offset"}
    return {"name": name}


def parse_columns(
    data: bytes, start: int = 0, sections: list[StringSection] | None = None
) -> list[Column]:
    """Locate columns by descriptor, rejecting coincidental matches.

    A match counts only if its separator byte is in place, its stated length
    fits in the file, and that length ends on the payload end marker.
    """
    columns: list[Column] = []
    for match in COLUMN_DESCRIPTOR.finditer(data, start):
        offset = match.start() + 1
        payload_offset = offset + COLUMN_DESCRIPTOR_LEN
        if payload_offset > len(data) or data[payload_offset - 1] != 0x0A:
            continue
        length = int.from_bytes(data[offset + 9 : offset + 17], "little")
        payload_end = payload_offset + length
        if payload_end > len(data) or length < len(COLUMN_PAYLOAD_END):
            continue
        if data[payload_end - len(COLUMN_PAYLOAD_END) : payload_end] != (
            COLUMN_PAYLOAD_END
        ):
            continue
        columns.append(
            Column(
                offset=offset,
                payload_offset=payload_offset,
                payload_length=length,
                **_record_fields(data, offset, start),
                **_decode_payload(
                    data[payload_offset:payload_end], payload_end, sections or []
                ),
            )
        )
    return columns


# --- curve records and the join to columns -----------------------------------


def _label_at(data: bytes, pos: int) -> bytes | None:
    """The label text if ``pos`` holds ``<s> <s-1> <s-1 bytes ending in NUL>``."""
    if pos + 1 >= len(data):
        return None
    size = data[pos]
    if size < 2 or data[pos + 1] != size - 1 or pos + size >= len(data):
        return None
    if data[pos + size] != 0:
        return None
    return data[pos + 2 : pos + size]


def _find_label(data: bytes, pos: int, frames: set[int]) -> tuple[int, bytes] | None:
    """Pass a few filler bytes after a frame until a label, a frame, or anything else.

    The label is tried first at each step, because its ``<s>`` can equal a filler
    byte.
    """
    limit = pos + LABEL_FILLER_MAX
    while pos not in frames and pos < len(data) and pos <= limit:
        label = _label_at(data, pos)
        if label is not None:
            return pos, label
        if data[pos : pos + 2] == b"\x01\x00":
            pos += 2
        elif data[pos] in (0x00, 0x0A):
            pos += 1
        else:
            return None
    return None


def _curve_frames(data: bytes, start: int, size: int) -> list[tuple[int, int, bytes]]:
    """Find ``80 <n>`` frames expanding to ``size``: offset, end, expanded bytes."""
    found: list[tuple[int, int, bytes]] = []
    pos = data.find(COLUMN_RECORD_FRAME, start)
    while 0 <= pos < len(data) - 1:
        end = pos + 2 + data[pos + 1]
        if end <= len(data) and _ends_in_zero_run(data, pos + 2, end):
            record = expand(data, pos + 2, end)
            if len(record) == size:
                found.append((pos, end, record))
                pos = data.find(COLUMN_RECORD_FRAME, end)
                continue
        pos = data.find(COLUMN_RECORD_FRAME, pos + 1)
    return found


def parse_curve_records(
    data: bytes, start: int, format_version: str
) -> list[CurveRecord]:
    """Parse the curve records at or after ``start``, with their labels."""
    size = (
        CURVE_RECORD_SIZE_4_33
        if format_version.startswith("4.33")
        else (CURVE_RECORD_SIZE)
    )
    frames = _curve_frames(data, start, size)
    starts = {offset for offset, _, _ in frames}
    records = []
    for offset, end, expanded in frames:
        name = expanded[CURVE_NAME_OFFSET:].split(b"\x00", 1)[0]
        labels: dict[str, Any] = {}
        found = _find_label(data, end, starts)
        if found is not None:
            labels["label_offset"] = found[0]
            lines = found[1].split(LABEL_LINE_SEPARATOR, 2)
            for key, line in zip(
                ("long_name", "units", "comment"), lines, strict=False
            ):
                labels[key] = _text(line)
        records.append(
            CurveRecord(
                offset,
                size,
                _text(name),
                ColumnType(expanded[CURVE_TYPE_OFFSET]),
                **labels,
            )
        )
    return records


def _split_column_name(name: str) -> tuple[str, str, int] | None:
    """Split ``<book>_<col>[@k]`` into book, short name and sheet index."""
    book, separator, col = name.rpartition("_")
    if not separator or not book or not col:
        return None
    short, at, sheet = col.rpartition("@")
    if at and short and sheet.isdecimal() and int(sheet) >= 1:
        return book, short, int(sheet)
    return book, col, 1


def _book_starts(data: bytes, books: set[str], start: int) -> list[tuple[int, str]]:
    """Where each book's name occurs as a literal: followed by NUL or a zero run."""
    occurrences = []
    for book in books:
        pattern = re.escape(book.encode()) + rb"(?=[\x00\x80-\xbf])"
        occurrences += [
            (match.start(), book) for match in re.finditer(pattern, data[start:])
        ]
    return sorted((offset + start, book) for offset, book in occurrences)


def join_curve_records(
    data: bytes, columns: list[Column], curves: list[CurveRecord], start: int
) -> list[Column]:
    """Give columns their type and label from their book's curve records.

    A curve record belongs to the book whose name occurs last before it, among
    the books that columns name. ``<col>@k`` is the k-th record of ``<col>`` in its
    book, which is a hypothesis. Other columns get a problem, not a guess.
    """
    names = {c.name: _split_column_name(c.name) for c in columns if c.name is not None}
    books = {split[0] for split in names.values() if split is not None}
    occurrences = _book_starts(data, books, start)

    by_book: dict[tuple[str, str | bytes], list[CurveRecord]] = {}
    index = 0
    current = None
    for curve in curves:
        while index < len(occurrences) and occurrences[index][0] < curve.offset:
            current = occurrences[index][1]
            index += 1
        if current is not None:
            by_book.setdefault((current, curve.name), []).append(curve)

    joined = []
    for column in columns:
        split = names.get(column.name) if column.name is not None else None
        if split is None:
            joined.append(
                replace(column, curve_problem=CurveJoinProblem.NO_COLUMN_NAME)
            )
            continue
        book, short, sheet = split
        records = by_book.get((book, short), [])
        if not records:
            joined.append(replace(column, curve_problem=CurveJoinProblem.NO_RECORD))
        elif sheet > len(records):
            joined.append(
                replace(
                    column,
                    curves_in_book=len(records),
                    curve_problem=CurveJoinProblem.FEWER_RECORDS,
                )
            )
        else:
            record = records[sheet - 1]
            joined.append(
                replace(
                    column,
                    curves_in_book=len(records),
                    curve_offset=record.offset,
                    column_type=record.column_type,
                    long_name=record.long_name,
                    units=record.units,
                    comment=record.comment,
                )
            )
    return joined


# --- string sections --------------------------------------------------------


def _decode_string_block(block: bytes, usize: int) -> dict[str, Any]:
    try:
        decoded = lz4.decode_block(block, usize)
    except lz4.Lz4Error as exc:
        return {"undecoded": f"LZ4 block: {exc}"}
    if len(decoded) < STRING_HEADER.size:
        return {"undecoded": "decoded section is shorter than its header"}
    magic, version, kind, width, count, byte_count, rows = STRING_HEADER.unpack_from(
        decoded
    )
    if (magic, version, kind, width) != (STRING_HEADER_MAGIC, 2, 1, 4):
        return {"undecoded": "decoded section header is not the established form"}

    strings: dict[int, str | bytes] = {}
    body = decoded[STRING_HEADER.size :]
    offset = 0
    while offset < len(body):
        end = body.find(b"\x00", offset)
        end = len(body) if end < 0 else end
        strings[offset] = _text(body[offset:end])
        offset = end + 1
    return {"count": count, "byte_count": byte_count, "rows": rows, "strings": strings}


def _string_section(data: bytes, offset: int, head_end: int) -> StringSection:
    def failed(reason: str, usize: int = 0) -> StringSection:
        return StringSection(offset, usize, undecoded=reason)

    try:
        csize, pos = _leb128(data, head_end)
    except _Malformed:
        return failed("compressed size is truncated")
    if pos + 5 > len(data) or data[pos] != 0x00:
        return failed("separator byte is not in place")
    usize = int.from_bytes(data[pos + 1 : pos + 5], "little")
    block_start = pos + 5
    # csize is the block's length plus 4, for usize.
    block_end = pos + 1 + csize
    if csize < 4 or block_end > len(data):
        return failed(f"compressed size {csize} does not fit", usize)
    return StringSection(
        offset,
        usize,
        **_decode_string_block(data[block_start:block_end], usize),
    )


def parse_string_sections(data: bytes, start: int = 0) -> list[StringSection]:
    """Decode each string section, in file order. Failures are recorded, not raised."""
    return [
        _string_section(data, match.start(), match.end())
        for match in STRING_SECTION.finditer(data, start)
    ]


# --- settings document ------------------------------------------------------


def _expand_to_nul(data: bytes, start: int) -> bytes | None:
    """Expand from ``start`` up to the first NUL, or ``None`` if there is none."""
    out = bytearray()
    try:
        for chunk in iter_expanded(data, start):
            nul = chunk.find(0)
            if nul >= 0:
                out += chunk[:nul]
                return bytes(out)
            out += chunk
    except RunLengthError:
        return None
    return None


def parse_settings_xml(data: bytes, start: int = 0) -> bytes | None:
    """Return the first well-formed settings document, as raw UTF-8 bytes.

    Its root opens a literal run that follows a zero-run token. Other occurrences
    of the root's text in a file are not at such a position or do not parse.
    """
    at = data.find(SETTINGS_ROOT, start + 2)
    while at >= 0:
        token = data[at - 1]
        if token < ZERO_RUN_MIN and ZERO_RUN_MIN <= data[at - 2] < BYTE_RUN_MIN:
            document = _expand_to_nul(data, at - 1)
            if document is not None:
                try:
                    ElementTree.fromstring(document)
                except ElementTree.ParseError:
                    pass
                else:
                    return document
        at = data.find(SETTINGS_ROOT, at + 1)
    return None


def parse(data: bytes) -> Document:
    """Parse an in-memory ``.opju`` buffer into a :class:`Document`."""
    header = parse_header(data)
    root = root_header(data, header)
    stream_start = header.body_offset + ROOT_HEADER_LEN
    sections = parse_string_sections(data, stream_start)
    columns = parse_columns(data, stream_start, sections)
    past_payloads = stream_start
    if columns:
        past_payloads = columns[-1].payload_offset + columns[-1].payload_length
    curves = parse_curve_records(data, past_payloads, header.format_version)
    columns = join_curve_records(data, columns, curves, past_payloads)
    return Document(
        header=header,
        root_header=root,
        names=scan_named_records(data, stream_start),
        global_header=parse_global_header(data),
        asset_directory=parse_asset_directory(data),
        string_sections=sections,
        columns=columns,
        curve_records=curves,
        settings_xml=parse_settings_xml(data, stream_start),
    )


def read(path: str | Path) -> Document:
    """Read and parse an ``.opju`` file from disk."""
    return parse(Path(path).read_bytes())
