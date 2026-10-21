"""Shared FastAPI dependencies: authentication and capability gating."""

from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import User
from app.permissions import capabilities_for_user
from app.schemas import UserOut
from app.security import decode_access_token

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

    # The row is re-read on every request rather than trusted from the token,
    # so a role changed by an administrator takes effect immediately.
    user = await session.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require(*capabilities: str):
    """Route dependency: the caller must hold every one of `capabilities`.

    The capability names come from `app/permissions.py`, and the same names
    travel to the frontend in the login response - so a screen asks the same
    question the server asked, rather than re-deriving the answer from a role
    string.

    The dependency names a CAPABILITY, never a role. "May this user change a
    rule?" is the question; "is this user an admin?" is an assumption about
    which roles might change a rule, and it is the assumption that stops the
    estate being maintained by somebody who is not an administrator.
    """

    async def _dependency(current: CurrentUser) -> User:
        held = capabilities_for_user(current)
        missing = [c for c in capabilities if c not in held]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This action requires the {', '.join(missing)} "
                    f"capability; your role ({current.role}) does not hold it"
                ),
            )
        return current

    return _dependency


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
    "get_current_user",
    "require",
    "list_filters",
    "user_by_username",
    "UserOut",
]
