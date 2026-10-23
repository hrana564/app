from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, SessionDep, user_by_username
from app.permissions import (
    ROLE_ADMIN,
    ROLE_AUDITOR,
    ROLE_MANAGER,
    ROLE_VIEWER,
)
from app.schemas import DemoAccount, LoginRequest, TokenResponse, UserOut
from app.security import create_access_token, verify_password
from app.settings import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

# What each demo account is FOR, in one line. The panel is a map of the
# permission ladder, so the blurbs say what changes when you sign in as each.
#
# Each blurb is one line at the panel's width. A row that wraps makes the
# list ragged, and the eye stops scanning it.
BLURBS: dict[str, str] = {
    ROLE_ADMIN: "manages accounts, rules, schedules and parameters",
    ROLE_MANAGER: "authors rules and schedules — but not accounts",
    ROLE_AUDITOR: "reviews runs and acknowledges tickets",
    ROLE_VIEWER: "reads everything; cannot sign off a finding",
}


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, session: SessionDep) -> TokenResponse:
    user = await user_by_username(session, payload.username.strip())
    # Same message for unknown user and bad password - do not leak which.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User is deactivated"
        )

    token, expires_in = create_access_token(
        user_id=user.id, username=user.username
    )
    return TokenResponse(
        access_token=token, expires_in=expires_in, user=UserOut.model_validate(user)
    )


@router.get("/me", response_model=UserOut)
async def me(current: CurrentUser) -> UserOut:
    return UserOut.model_validate(current)


@router.get("/demo-accounts", response_model=list[DemoAccount])
async def demo_accounts() -> list[DemoAccount]:
    """The accounts the sign-in screen offers, with their passwords.

    Empty unless the deployment has opted in. The order is the permission
    ladder - most to least - because the panel is read as a map of what each
    role can do.
    """
    if not settings.show_demo_accounts:
        return []

    seeded = (
        (settings.admin_username, settings.admin_password, ROLE_ADMIN),
        (settings.manager_username, settings.manager_password, ROLE_MANAGER),
        (settings.auditor_username, settings.auditor_password, ROLE_AUDITOR),
        (settings.viewer_username, settings.viewer_password, ROLE_VIEWER),
    )
    return [
        DemoAccount(
            username=username,
            password=password,
            role=role,
            blurb=BLURBS.get(role, ""),
        )
        for username, password, role in seeded
        if username and password
    ]
