"""Login y estado de sesión (único endpoint público junto con el health check)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.deps.auth_user import get_current_user
from backend.app.deps.rate_limit import limite_login
from backend.app.schemas.responses import AuthUserBriefOut, TokenResponseOut
from backend.models import AppUser
from backend.services.auth_password import verificar_password_ficticia, verify_password
from backend.services.jwt_tokens import create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login", response_model=TokenResponseOut, dependencies=[Depends(limite_login)])
def login(
    body: LoginBody,
    db: Session = Depends(get_db),
) -> TokenResponseOut:
    user = db.scalar(select(AppUser).where(AppUser.username == body.username.strip()))
    # Si el usuario no existe se verifica contra un hash ficticio: misma duración (C-5).
    if user is None:
        valido = verificar_password_ficticia(body.password)
    else:
        valido = verify_password(body.password, user.password_hash)
    if not valido or user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Usuario o contraseña incorrectos",
        )
    token, exp = create_access_token(subject_user_id=user.id, username=user.username)
    return TokenResponseOut(access_token=token, expires_at=exp)


@router.get("/me", response_model=AuthUserBriefOut)
def me(user: AppUser = Depends(get_current_user)) -> AuthUserBriefOut:
    return AuthUserBriefOut(id=user.id, username=user.username)
