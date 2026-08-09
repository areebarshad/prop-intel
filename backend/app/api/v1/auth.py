"""Authentication endpoints: register, login, me."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user, rate_limited_ip
from app.db.session import get_db
from app.models.user import User
from app.schemas import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _prehash(plain: str) -> bytes:
    # SHA-256 digest keeps bcrypt input to 32 bytes, safely below its 72-byte limit.
    return hashlib.sha256(plain.encode()).digest()


def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(_prehash(plain), bcrypt.gensalt()).decode()


def _verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(_prehash(plain), hashed.encode())


def _issue_token(user_id: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "iat": now.timestamp(),
        "exp": (now.timestamp() + settings.security.access_token_ttl_minutes * 60),
    }
    return jwt.encode(
        payload,
        settings.security.secret_key.get_secret_value(),
        algorithm=settings.security.algorithm,
    )


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limited_ip),
) -> TokenResponse:
    existing = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email already registered")

    user = User(
        email=body.email,
        hashed_password=_hash_password(body.password),
        full_name=body.full_name,
        company=body.company,
        tier="free",
        is_active=True,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="email already registered"
        ) from e

    token = _issue_token(str(user.id))
    return TokenResponse(access_token=token, token_type="bearer", user_id=str(user.id))


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limited_ip),
) -> TokenResponse:
    user = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if user is None or not _verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account inactive")

    user.last_login_at = datetime.now(UTC)
    token = _issue_token(str(user.id))
    return TokenResponse(access_token=token, token_type="bearer", user_id=str(user.id))


@router.post("/refresh", response_model=TokenResponse)
async def refresh(current_user: User = Depends(get_current_user)) -> TokenResponse:
    """Issue a fresh JWT from a still-valid one. Call before expiry to stay logged in."""
    token = _issue_token(str(current_user.id))
    return TokenResponse(access_token=token, token_type="bearer", user_id=str(current_user.id))


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
