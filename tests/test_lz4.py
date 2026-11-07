"""LZ4 block decoder tests, from hand-built blocks."""

from __future__ import annotations

import pytest

from opj.lz4 import Lz4Error, decode_block


def _literal_block(text: bytes) -> bytes:
    """A block of one sequence: only literals."""
    if len(text) < 15:
        return bytes((len(text) << 4,)) + text
    remaining = len(text) - 15
    extension = b"\xff" * (remaining // 255) + bytes((remaining % 255,))
    return b"\xf0" + extension + text


def test_empty_block() -> None:
    assert decode_block(b"\x00", 0) == b""


@pytest.mark.parametrize("size", [1, 14, 15, 16, 269, 270, 271, 600])
def test_literal_length_extension_boundaries(size: int) -> None:
    text = bytes(i % 251 for i in range(size))
    assert decode_block(_literal_block(text), size) == text


def test_match_copies_from_output() -> None:
    # Five literals, then a 4-byte match at distance 5, then a final sequence.
    block = b"\x50abcde\x05\x00" + b"\x00"
    assert decode_block(block, 9) == b"abcdeabcd"


def test_match_may_overlap_its_own_output() -> None:
    # A match-length nibble of 2 means 6 bytes: a repeating two-byte window.
    block = b"\x22ab\x02\x00" + b"\x00"
    assert decode_block(block, 8) == b"abababab"


def test_match_length_extension() -> None:
    block = b"\x1fa\x01\x00\x02" + b"\x00"
    assert decode_block(block, 1 + 15 + 2 + 4) == b"a" * 22


def test_two_sequences_then_literals() -> None:
    block = b"\x20ab\x02\x00" + b"\x10c\x01\x00" + b"\x10d"
    assert decode_block(block, 12) == b"abababcccccd"


@pytest.mark.parametrize(
    "block, size",
    [
        pytest.param(b"", 0, id="empty-input"),
        pytest.param(b"\x30ab", 3, id="literals-past-block"),
        pytest.param(b"\xf0", 15, id="extension-past-block"),
        pytest.param(b"\x10a\x01", 5, id="offset-past-block"),
        pytest.param(b"\x10a\x00\x00\x00", 5, id="zero-offset"),
        pytest.param(b"\x10a\x02\x00\x00", 5, id="offset-outside-output"),
        pytest.param(b"\x10a\x01\x00", 5, id="no-final-literal-sequence"),
        pytest.param(b"\x20ab", 1, id="literals-exceed-size"),
        pytest.param(b"\x10a\x01\x00\x00", 2, id="match-exceeds-size"),
        pytest.param(b"\x10a", 2, id="short-of-size"),
    ],
)
def test_rejects_malformed(block: bytes, size: int) -> None:
    with pytest.raises(Lz4Error):
        decode_block(block, size)
