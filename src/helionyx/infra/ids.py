"""Short, sortable, prefixed identifiers (for example ``scn_01J9X4K2QH``)."""

from __future__ import annotations

import secrets
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32


def _b32(n: int, width: int) -> str:
    out = []
    for _ in range(width):
        out.append(_ALPHABET[n & 31])
        n >>= 5
    return "".join(reversed(out))


def new_id(prefix: str) -> str:
    ms = int(time.time() * 1000)
    return f"{prefix}_{_b32(ms, 9)}{_b32(secrets.randbits(25), 5)}"
