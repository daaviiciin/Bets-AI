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


#: Hash de una contraseña aleatoria que nadie conoce. Se verifica contra él cuando el usuario
#: no existe, para que el login tarde lo mismo y no delate qué usuarios hay (C-5).
_HASH_FICTICIO = hash_password(secrets.token_urlsafe(16))

#: Longitud mínima al fijar una contraseña nueva (``backend.tools.set_password``).
LONGITUD_MINIMA_PASSWORD = 10


def verificar_password_ficticia(plain: str) -> bool:
    """Gasta el mismo tiempo que ``verify_password`` y devuelve siempre ``False``."""
    verify_password(plain, _HASH_FICTICIO)
    return False


def verify_password(plain: str, stored: str) -> bool:
    if ":" not in stored:
        return False
    salt, want = stored.split(":", 1)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), _ITERATIONS)
    return dk.hex() == want
