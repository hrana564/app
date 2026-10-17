"""Shared FastAPI dependencies: authentication and role gating."""

from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import User
from app.schemas import UserOut
from app.security import ROLE_ADMIN, decode_access_token

bearer = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    session: SessionDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
) -> User:
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(creds.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = await session.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(current: CurrentUser) -> User:
    """Route dependency: admin only. Depends on get_current_user, so the token
    is decoded exactly once."""
    if current.role != ROLE_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires the admin role",
        )
    return current


AdminUser = Annotated[User, Depends(require_admin)]


def list_filters(params: dict[str, list[str] | None] | dict) -> dict:
    """Flatten FastAPI query params, dropping None and list wrappers."""
    out: dict = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, list):
            if not value:
                continue
            value = value[0]
        out[key] = value
    return out


async def user_by_username(session: AsyncSession, username: str) -> User | None:
    return (
        await session.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()


__all__ = [
    "SessionDep",
    "CurrentUser",
    "AdminUser",
    "get_current_user",
    "require_admin",
    "list_filters",
    "user_by_username",
    "UserOut",
]