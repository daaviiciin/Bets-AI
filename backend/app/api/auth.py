"""Login MVP y estado de sesión."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.deps.auth_user import get_current_user
from backend.app.schemas.responses import AuthUserBriefOut, TokenResponseOut
from backend.models import AppUser
from backend.services.auth_password import verify_password
from backend.services.jwt_tokens import create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login", response_model=TokenResponseOut)
def login(
    body: LoginBody,
    db: Session = Depends(get_db),
) -> TokenResponseOut:
    user = db.scalar(select(AppUser).where(AppUser.username == body.username.strip()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Usuario o contraseña incorrectos",
        )
    token, exp = create_access_token(subject_user_id=user.id, username=user.username)
    return TokenResponseOut(access_token=token, expires_at=exp)


@router.get("/me", response_model=AuthUserBriefOut)
def me(user: AppUser = Depends(get_current_user)) -> AuthUserBriefOut:
    return AuthUserBriefOut(id=user.id, username=user.username)
