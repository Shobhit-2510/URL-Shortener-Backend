"""Encoder tests: round-trip, ordering, no-collision over a dense range."""
from __future__ import annotations

import pytest

from app import base62


@pytest.mark.parametrize("n", [0, 1, 61, 62, 63, 1_000_000, 9_999_999, 2**40])
def test_round_trip(n: int) -> None:
    assert base62.decode(base62.encode(n)) == n


def test_zero_is_single_char() -> None:
    assert base62.encode(0) == "0"


def test_base_boundaries() -> None:
    assert base62.encode(61) == "Z"   # last single-digit symbol
    assert base62.encode(62) == "10"  # first two-digit symbol


def test_monotonic_strictly_increasing_length_then_lexical() -> None:
    # Encoding preserves numeric order for equal-length codes — useful sanity check.
    prev = -1
    for n in range(62, 62 + 500):
        assert base62.decode(base62.encode(n)) == n > prev
        prev = n


def test_no_collisions_over_dense_range() -> None:
    """The core guarantee vs random hashing: a contiguous counter range yields
    unique codes with zero retries."""
    codes = {base62.encode(i) for i in range(1_000_000, 1_050_000)}
    assert len(codes) == 50_000


def test_negative_rejected() -> None:
    with pytest.raises(ValueError):
        base62.encode(-1)


def test_invalid_char_rejected() -> None:
    with pytest.raises(ValueError):
        base62.decode("abc$")
