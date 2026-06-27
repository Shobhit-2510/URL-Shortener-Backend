"""Base62 codec for the short codes.

Design decision (see README "Code generation"): codes are a base62 encoding of a
**monotonic counter** (the database identity column), not a random hash.

Why counter + base62 instead of random hash (e.g. md5(url)[:7])?
  * No collision-retry loop. A random 7-char code has a birthday-bounded
    collision probability that grows with table size; each collision means an
    extra DB round-trip to detect and a retry. A counter is collision-free by
    construction — the uniqueness is guaranteed by the identity column.
  * Shortest possible codes for a given number of URLs (dense, gap-free space).
  * O(1), branch-free encode — no DB read before insert.

Tradeoffs we accept (and mitigate):
  * Sequential codes are *enumerable*: code N+1 follows code N, so a scraper can
    walk the keyspace. Mitigations: an offset so we never expose tiny ids, and
    the option to run ids through a reversible scramble (Feistel/Hashids) before
    encoding. We do NOT rely on code secrecy for authorization.
  * A single global counter is a write coordination point. With Postgres this is
    a cheap sequence; at extreme scale you'd hand out *ranges* of ids to each app
    instance (Flickr "ticket server" pattern) — noted as future work in the README.
"""
from __future__ import annotations

# Default alphabet kept in sync with config.Settings.code_alphabet.
ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
BASE = len(ALPHABET)


def encode(number: int, alphabet: str = ALPHABET) -> str:
    """Encode a non-negative integer to a base62 string."""
    if number < 0:
        raise ValueError("base62.encode requires a non-negative integer")
    base = len(alphabet)
    if number == 0:
        return alphabet[0]
    chars: list[str] = []
    while number:
        number, rem = divmod(number, base)
        chars.append(alphabet[rem])
    return "".join(reversed(chars))


def decode(code: str, alphabet: str = ALPHABET) -> int:
    """Decode a base62 string back to an integer (inverse of `encode`)."""
    base = len(alphabet)
    index = {ch: i for i, ch in enumerate(alphabet)}
    number = 0
    for ch in code:
        try:
            number = number * base + index[ch]
        except KeyError as exc:  # pragma: no cover - defensive
            raise ValueError(f"invalid base62 character: {ch!r}") from exc
    return number
