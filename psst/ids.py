"""Permanent ids: a prefix plus Crockford base32 (no i, l, o, or u, so ids read unambiguously)."""

import hashlib
import secrets

ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
LENGTHS = {"pl": 10, "fa": 10, "so": 10, "tg": 8, "run": 10}


def _encode(data: bytes, length: int) -> str:
    number = int.from_bytes(data, "big")
    chars = []
    for _ in range(length):
        number, remainder = divmod(number, 32)
        chars.append(ALPHABET[remainder])
    return "".join(chars)


def new(prefix: str) -> str:
    """A random id, for anything created from now on."""
    return f"{prefix}_{_encode(secrets.token_bytes(16), LENGTHS[prefix])}"


def derived(prefix: str, key: str) -> str:
    """The same id every time for the same key, so the legacy import can run again without new ids."""
    digest = hashlib.sha256(f"psst:{prefix}:{key}".encode()).digest()
    return f"{prefix}_{_encode(digest, LENGTHS[prefix])}"
