"""A small FPC encoder for tests, written from the published paper.

Uses the interleaved layout of docs/engineering-spec.md §2.6. An odd count leaves
the final code byte's high nibble unused, with no residual for it.
"""

from __future__ import annotations

import struct

_MASK64 = (1 << 64) - 1
_LENGTH_INDEX = {0: 0, 1: 1, 2: 2, 3: 3, 5: 4, 6: 5, 7: 6, 8: 7}


def to_bits(value: float) -> int:
    return int(struct.unpack("<Q", struct.pack("<d", value))[0])


def from_bits(bits: int) -> float:
    return float(struct.unpack("<d", struct.pack("<Q", bits))[0])


def _residual_length(residual: int) -> int:
    length = (residual.bit_length() + 7) // 8
    return 5 if length == 4 else length


def encode(values: list[float], table_bits: int = 12) -> bytes:
    mask = (1 << table_bits) - 1
    fcm = [0] * (mask + 1)
    dfcm = [0] * (mask + 1)
    fcm_hash = 0
    dfcm_hash = 0
    last = 0
    pairs: list[tuple[int, bytes]] = []

    for value in values:
        bits = to_bits(value)
        fcm_residual = bits ^ fcm[fcm_hash]
        dfcm_residual = bits ^ ((dfcm[dfcm_hash] + last) & _MASK64)
        use_dfcm = dfcm_residual < fcm_residual
        residual = dfcm_residual if use_dfcm else fcm_residual
        length = _residual_length(residual)
        code = _LENGTH_INDEX[length] | (0x8 if use_dfcm else 0)
        pairs.append((code, residual.to_bytes(length, "little")))

        fcm[fcm_hash] = bits
        fcm_hash = ((fcm_hash << 6) ^ (bits >> 48)) & mask
        delta = (bits - last) & _MASK64
        dfcm[dfcm_hash] = delta
        dfcm_hash = ((dfcm_hash << 2) ^ (delta >> 40)) & mask
        last = bits

    out = bytearray()
    for i in range(0, len(pairs), 2):
        code_low, residual_low = pairs[i]
        code_high, residual_high = pairs[i + 1] if i + 1 < len(pairs) else (0, b"")
        out.append(code_low | (code_high << 4))
        out += residual_low + residual_high
    return bytes(out)
