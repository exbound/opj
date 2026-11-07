"""FPC decoder tests: hand-built blocks and round trips through a test encoder."""

from __future__ import annotations

import math
import random

import pytest

from fpc_encoder import encode, from_bits, to_bits
from opj.fpc import FpcError, decode

_BITS = 0x4009_21FB_5444_2D18  # the pattern of an arbitrary eight-byte value
_FULL_FCM = 0x7  # code: FCM, 8-byte residual
_ZERO_DFCM = 0x8  # code: DFCM, no residual
_ZERO_FCM = 0x0  # code: FCM, no residual


def _bits(values: list[float]) -> list[int]:
    return [to_bits(v) for v in values]


def _roundtrip(values: list[float]) -> None:
    block = encode(values)
    decoded, end = decode(block, len(values), 12)
    assert _bits(decoded) == _bits(values)
    assert end == len(block)


# --- hand-built blocks: the layout, independent of the encoder --------------


@pytest.mark.parametrize(
    "index, length",
    [(0, 0), (1, 1), (2, 2), (3, 3), (4, 5), (5, 6), (6, 7), (7, 8)],
)
def test_residual_length_table(index: int, length: int) -> None:
    residual = bytes(range(0x11, 0x11 + length))
    block = bytes((index,)) + residual
    values, end = decode(block, 1, 12)
    assert to_bits(values[0]) == int.from_bytes(residual, "little")
    assert end == len(block)


def test_high_bit_selects_dfcm_prediction() -> None:
    # After one value the DFCM prediction is that value; the FCM entry is empty.
    first = _BITS.to_bytes(8, "little")
    dfcm_block = bytes((_FULL_FCM | (_ZERO_DFCM << 4),)) + first
    assert _bits(decode(dfcm_block, 2, 12)[0]) == [_BITS, _BITS]


def test_clear_high_bit_selects_fcm_prediction() -> None:
    first = _BITS.to_bytes(8, "little")
    fcm_block = bytes((_FULL_FCM | (_ZERO_FCM << 4),)) + first
    assert _bits(decode(fcm_block, 2, 12)[0]) == [_BITS, 0]


def test_low_nibble_codes_the_first_value() -> None:
    # Low nibble: 1-byte residual for the first value. High nibble: 2 bytes.
    block = bytes((0x21, 0xAA, 0xBB, 0xCC))
    values, end = decode(block, 2, 12)
    assert end == 4
    assert to_bits(values[0]) == 0xAA
    # The first value's high bits leave the FCM hash at 0, so it is the prediction.
    assert to_bits(values[1]) == 0xCCBB ^ 0xAA


def test_codes_and_residuals_are_interleaved() -> None:
    # Four values take two code bytes, each directly before its own residuals.
    # With small patterns the FCM hash stays 0: each value predicts the next.
    block = bytes((0x11, 0x01, 0x02, 0x11, 0x04, 0x08))
    values, end = decode(block, 4, 12)
    assert end == len(block)
    assert _bits(values) == [0x01, 0x01 ^ 0x02, 0x01 ^ 0x02 ^ 0x04, 0x0F]


def test_odd_count_reads_no_residual_for_the_unused_nibble() -> None:
    # The high nibble claims 8 bytes, but it belongs to no value.
    block = bytes((_ZERO_FCM | (_FULL_FCM << 4),)) + b"tail"
    values, end = decode(block, 1, 12)
    assert _bits(values) == [0]
    assert end == 1


def test_zero_count_consumes_nothing() -> None:
    assert decode(b"\x77", 0, 12) == ([], 0)


def test_start_offset() -> None:
    block = encode([1.5, 2.5])
    values, end = decode(b"xx" + block, 2, 12, start=2)
    assert values == [1.5, 2.5]
    assert end == 2 + len(block)


@pytest.mark.parametrize(
    "block",
    [
        pytest.param(b"", id="no-code-byte"),
        pytest.param(b"\x07\x01\x02", id="short-first-residual"),
        pytest.param(b"\x70", id="short-second-residual"),
    ],
)
def test_overrun_raises(block: bytes) -> None:
    with pytest.raises(FpcError):
        decode(block, 2, 12)


# --- round trips ------------------------------------------------------------


@pytest.mark.parametrize("count", [1, 2, 3, 7, 8, 101, 1000])
def test_roundtrip_random_doubles(count: int) -> None:
    rng = random.Random(count)
    _roundtrip([from_bits(rng.getrandbits(64)) for _ in range(count)])


def test_roundtrip_repeats_reach_zero_residual() -> None:
    values = [3.25] * 64
    block = encode(values)
    # Repeats predict exactly, so nearly every value costs only its code nibble.
    assert len(block) < 8 * 3 + 64
    _roundtrip(values)


def test_roundtrip_all_zero_is_only_codes() -> None:
    assert encode([0.0] * 10) == bytes(5)
    _roundtrip([0.0] * 10)


def test_roundtrip_linear_ramp_uses_dfcm() -> None:
    _roundtrip([1000.0 + i * 0.5 for i in range(500)])


@pytest.mark.parametrize("shift", range(0, 64, 8))
def test_roundtrip_each_residual_length(shift: int) -> None:
    # Single-value blocks: the FCM prediction is empty, so the residual is the
    # pattern and its significant byte count sets the length (and 4 becomes 5).
    pattern = 0xA5 << shift
    block = encode([from_bits(pattern)])
    significant = shift // 8 + 1
    assert len(block) == 1 + (5 if significant == 4 else significant)
    _roundtrip([from_bits(pattern)])


def test_roundtrip_specials() -> None:
    _roundtrip([0.0, -0.0, math.inf, -math.inf, 5e-324, 1.7976931348623157e308])


def test_roundtrip_preserves_nan_pattern() -> None:
    _roundtrip([math.nan, 1.0, math.nan])


@pytest.mark.parametrize("table_bits", [1, 4, 12, 16])
def test_roundtrip_table_bits_parameter(table_bits: int) -> None:
    rng = random.Random(table_bits)
    values = [rng.choice([1.0, 2.0, 2.5, rng.random()]) for _ in range(301)]
    decoded, end = decode(encode(values, table_bits), len(values), table_bits)
    assert _bits(decoded) == _bits(values)
    assert end == len(encode(values, table_bits))
