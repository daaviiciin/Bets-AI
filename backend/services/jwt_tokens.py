"""Tokens JWT muy simples para sesión MVP (mejor OAuth2 servidor más adelante)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt

from backend.app.config import get_settings


def create_access_token(*, subject_user_id: int, username: str) -> tuple[str, int]:
    """Devuelve (token_jwt, exp_unix)."""
    st = get_settings()
    ttl_days = st.jwt_access_ttl_days
    now = datetime.now(timezone.utc)
    exp = now + timedelta(days=ttl_days)
    payload = {
        "sub": str(subject_user_id),
        "username": username,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    encoded = jwt.encode(payload, st.jwt_secret, algorithm="HS256")
    return encoded, int(exp.timestamp())


def decode_access_token(token: str) -> dict:
    """Lanza jwt exceptions si firma/exp inválidos."""
    payload = jwt.decode(
        token,
        get_settings().jwt_secret,
        algorithms=["HS256"],
    )
    return payload
