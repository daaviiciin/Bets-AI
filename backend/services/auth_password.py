"""Hash y verificación de contraseña (PBKDF2; sin deps externas)."""

from __future__ import annotations

import hashlib
import secrets


_ITERATIONS = 120_000


def hash_password(plain: str, *, salt: str | None = None) -> str:
    """Genera ``salt_hex:dk_hex`` (sal ASCII sin ``:``, digest hex)."""
    s = salt or secrets.token_hex(12)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode(), s.encode(), _ITERATIONS)
    return f"{s}:{dk.hex()}"


def verify_password(plain: str, stored: str) -> bool:
    if ":" not in stored:
        return False
    salt, want = stored.split(":", 1)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), _ITERATIONS)
    return dk.hex() == want
