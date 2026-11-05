"""Decoder for FPC-compressed double-precision values.

The compression is the one published in M. Burtscher and P. Ratanaworabhan,
"FPC: A High-Speed Compressor for Double-Precision Floating-Point Data," IEEE
Trans. Computers 58(1), 2009. The predictors and hashes are as published. The
layout is the interleaved one in ``docs/engineering-spec.md`` §2.6: one byte
holds two 4-bit codes, low nibble first, and is followed directly by those two
values' residuals.
"""

from __future__ import annotations

import struct

#: Residual byte count for each value of a code's low 3 bits. There is no 4.
RESIDUAL_LENGTHS = (0, 1, 2, 3, 5, 6, 7, 8)
#: In a code, this bit selects the DFCM prediction; clear selects FCM.
DFCM_BIT = 0x8

_MASK64 = (1 << 64) - 1


class FpcError(ValueError):
    """Raised when a block's residuals run past the end of the input."""


def decode(
    data: bytes, count: int, table_bits: int, start: int = 0
) -> tuple[list[float], int]:
    """Decode ``count`` doubles from ``data[start:]``.

    Returns the values and the offset of the first byte not consumed. For an odd
    ``count`` the final code byte's high nibble belongs to no value, and no
    residual is read for it.
    """
    mask = (1 << table_bits) - 1
    fcm = [0] * (mask + 1)
    dfcm = [0] * (mask + 1)
    fcm_hash = 0
    dfcm_hash = 0
    last = 0
    bits: list[int] = []
    pos = start
    end = len(data)
    code_byte = 0

    for i in range(count):
        if i & 1:
            code = code_byte >> 4
        else:
            if pos >= end:
                raise FpcError(f"no code byte for value {i} at offset {pos}")
            code_byte = data[pos]
            pos += 1
            code = code_byte & 0xF

        length = RESIDUAL_LENGTHS[code & 0x7]
        if pos + length > end:
            raise FpcError(f"residual of value {i} overruns at offset {pos}")
        residual = int.from_bytes(data[pos : pos + length], "little")
        pos += length

        if code & DFCM_BIT:
            prediction = (dfcm[dfcm_hash] + last) & _MASK64
        else:
            prediction = fcm[fcm_hash]
        value = prediction ^ residual
        bits.append(value)

        fcm[fcm_hash] = value
        fcm_hash = ((fcm_hash << 6) ^ (value >> 48)) & mask
        delta = (value - last) & _MASK64
        dfcm[dfcm_hash] = delta
        dfcm_hash = ((dfcm_hash << 2) ^ (delta >> 40)) & mask
        last = value

    values = struct.unpack(f"<{count}d", struct.pack(f"<{count}Q", *bits))
    return list(values), pos
