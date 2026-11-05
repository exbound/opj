"""Decoder for the LZ4 block format, as used by string sections (spec §2.7).

A block is a series of sequences. Each starts with a token whose high nibble is
the literal length and low nibble the match length minus 4, either extended by
following bytes when it is 15 (each adds its value; a byte other than 255 ends
it). Literals follow, then a little-endian u16 match offset. The last sequence
stops after its literals.
"""

from __future__ import annotations

MIN_MATCH = 4


class Lz4Error(ValueError):
    """Raised when a block is malformed or does not decode to the stated size."""


def _extended(data: bytes, pos: int, length: int) -> tuple[int, int]:
    """Add the extension bytes of a length that reached 15."""
    while True:
        if pos >= len(data):
            raise Lz4Error("length extension runs past the block")
        byte = data[pos]
        pos += 1
        length += byte
        if byte != 255:
            return length, pos


def decode_block(data: bytes, size: int) -> bytes:
    """Decode a whole block, which must produce exactly ``size`` bytes."""
    out = bytearray()
    pos = 0
    while True:
        if pos >= len(data):
            raise Lz4Error("block ends without a final literal sequence")
        token = data[pos]
        pos += 1

        literals = token >> 4
        if literals == 15:
            literals, pos = _extended(data, pos, literals)
        if pos + literals > len(data):
            raise Lz4Error("literals run past the block")
        if len(out) + literals > size:
            raise Lz4Error(f"block decodes to more than {size} bytes")
        out += data[pos : pos + literals]
        pos += literals
        if pos == len(data):
            break

        if pos + 2 > len(data):
            raise Lz4Error("match offset runs past the block")
        distance = int.from_bytes(data[pos : pos + 2], "little")
        pos += 2
        if distance == 0 or distance > len(out):
            raise Lz4Error(f"match offset {distance} is outside the output")

        match = token & 0xF
        if match == 15:
            match, pos = _extended(data, pos, match)
        match += MIN_MATCH
        if len(out) + match > size:
            raise Lz4Error(f"block decodes to more than {size} bytes")
        # A match may overlap the bytes it is producing, so repeat the window.
        start = len(out) - distance
        window = bytes(out[start : start + match])
        out += (window * (match // len(window) + 1))[:match]

    if len(out) != size:
        raise Lz4Error(f"block decodes to {len(out)} bytes, not {size}")
    return bytes(out)
