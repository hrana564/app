from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, SessionDep, require
from app.models import User
from app.permissions import KNOWN_ROLES, ROLE_ADMIN, USERS_READ, USERS_WRITE
from app.schemas import PasswordChange, UserCreate, UserOut, UserUpdate
from app.security import hash_password, verify_password

router = APIRouter(prefix="/api/users", tags=["users"])

UserReader = Annotated[User, Depends(require(USERS_READ))]
UserWriter = Annotated[User, Depends(require(USERS_WRITE))]

# A username is an identifier, not a display name. It is used in file paths
# nowhere and in URLs only as a lookup, but it is quoted into the ledger, so
# it is held to the shape of a name rather than a sentence.
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _validate_username(username: str) -> str:
    name = username.strip()
    if not USERNAME_RE.match(name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "A username may hold letters, digits, dot, underscore and "
                "hyphen, and must be 1-64 characters"
            ),
        )
    return name


def _validate_role(role: str) -> str:
    name = (role or "").strip().lower()
    if name not in KNOWN_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unknown role {role!r}. Known roles: {', '.join(KNOWN_ROLES)}"
            ),
        )
    return name


async def _active_admins(session: SessionDep, *, excluding: int | None = None) -> int:
    stmt = select(func.count()).select_from(User).where(
        User.role == ROLE_ADMIN, User.is_active.is_(True)
    )
    if excluding is not None:
        stmt = stmt.where(User.id != excluding)
    return int((await session.execute(stmt)).scalar_one())


async def _require_not_last_admin(
    session: SessionDep, target: User, *, action: str
) -> None:
    if target.role != ROLE_ADMIN or not target.is_active:
        return
    if await _active_admins(session, excluding=target.id) == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{target.username} is the last active administrator and cannot "
                f"be {action}. Promote somebody else first."
            ),
        )


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------
@router.get("", response_model=list[UserOut])
async def list_users(session: SessionDep, _admin: UserReader) -> list[UserOut]:
    """Every account, in a stable order.

    The list is not filtered by client: an account is a person, not a record
    of one client's estate.
    """
    rows = (
        await session.execute(select(User).order_by(User.username))
    ).scalars().all()
    return [UserOut.model_validate(u) for u in rows]


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------
@router.post("", response_model=UserOut, status_code=201)
async def create_user(
    payload: UserCreate, session: SessionDep, _admin: UserWriter
) -> UserOut:
    """Create an account with a role.

    The role is validated against the matrix, so a user cannot be created
    with a role that grants nothing and then be mistaken for one that grants
    everything. A username is unique: it is the name the ledger records.
    """
    username = _validate_username(payload.username)
    role = _validate_role(payload.role)

    existing = (
        await session.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username {username!r} is already taken",
        )

    user = User(
        username=username,
        full_name=payload.full_name.strip(),
        password_hash=hash_password(payload.password),
        role=role,
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return UserOut.model_validate(user)


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------
@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int, payload: UserUpdate, session: SessionDep, admin: UserWriter
) -> UserOut:
    """Change a role, a name, an active flag, or reset a password.

    Two of those are refused against yourself. Changing your own role or
    deactivating your own account is a way to lock yourself out of the portal
    - or to promote yourself, if the endpoint's gate were ever widened - and
    neither is a thing an administrator needs to do to themselves. The
    password endpoint below exists for that.
    """
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    if target.id == admin.id and (
        payload.role is not None
        or payload.is_active is False
        or (payload.password is not None)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "You cannot change your own role, deactivate yourself, or "
                "reset your own password here. Use "
                "PATCH /api/users/me/password for your own password."
            ),
        )

    if payload.role is not None:
        new_role = _validate_role(payload.role)
        if new_role != target.role:
            # Demoting the only administrator is the same lockout, arrived
            # at from the other side.
            if target.role == ROLE_ADMIN and target.is_active:
                await _require_not_last_admin(session, target, action="demoted")
            target.role = new_role

    if payload.is_active is not None and payload.is_active != target.is_active:
        if payload.is_active is False and target.role == ROLE_ADMIN:
            await _require_not_last_admin(
                session, target, action="deactivated"
            )
        target.is_active = payload.is_active

    if payload.full_name is not None:
        target.full_name = payload.full_name.strip()

    if payload.password is not None:
        target.password_hash = hash_password(payload.password)

    await session.commit()
    await session.refresh(target)
    return UserOut.model_validate(target)


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: int, session: SessionDep, admin: UserWriter
) -> None:
    """Remove an account.

    You may not delete the account you are signed in as - it would end your
    session mid-request and leave your own actions attributed to nobody who
    can still be asked about them. You may not delete the last active
    administrator.

    Deactivating (`PATCH` with `is_active: false`) is the softer path and is
    what most of the time is wanted: it locks the account out on its next
    request without removing the name from the ledger.
    """
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    if target.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You cannot delete the account you are signed in as",
        )
    await _require_not_last_admin(session, target, action="deleted")

    await session.delete(target)
    await session.commit()


# ---------------------------------------------------------------------------
# your own password
# ---------------------------------------------------------------------------
@router.patch("/me/password", response_model=UserOut)
async def change_own_password(
    payload: PasswordChange, session: SessionDep, current: CurrentUser
) -> UserOut:
    """Change your own password.

    The current password is required, so a session left open on a shared
    machine cannot quietly take the account over. Nobody else can set it: the
    administrator's route is a reset, which does not need the old password
    precisely because it is not the user's own credential.
    """
    if not verify_password(payload.current_password, current.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your current password is not correct",
        )
    current.password_hash = hash_password(payload.new_password)
    await session.commit()
    await session.refresh(current)
    return UserOut.model_validate(current)
