"""Dependencia FastAPI: usuario autenticado vía Bearer JWT."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.models import AppUser
from backend.services.jwt_tokens import decode_access_token

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> AppUser:
    if creds is None or not creds.credentials:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Hace falta cabecera Authorization: Bearer <token>",
        )
    try:
        payload = decode_access_token(creds.credentials)
    except Exception:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o caducado",
        ) from None
    try:
        uid = int(payload.get("sub", "0"))
    except (TypeError, ValueError):
        uid = 0
    user = db.scalar(select(AppUser).where(AppUser.id == uid))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Usuario no encontrado")
    return user
