"""The role matrix, as a screen reads it.

The matrix in `app/permissions.py` decides what each role may do. This router
serves it - labels, groups and all - so the screen that shows it is showing
the server's answer rather than a second copy of it written in the frontend.

WHY IT IS NOT CAPABILITY-GATED
------------------------------
Every read of the ESTATE is capability-gated, and this is not a read of the
estate: it is a read of the permission MODEL. Which role may manage accounts
is documented in the README, so serving it to a signed-in account leaks
nothing. And it has to be readable by everyone, because the question it
answers - "what can the auditor do that I cannot?" - is asked by the people
who hold the least.

It does require a session. An unauthenticated reader gets a 401, because a
model of the portal's own permissions is still the portal's business.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.permissions import (
    CAPABILITY_ORDER,
    KNOWN_ROLES,
    ROLE_CAPABILITIES,
    capability_group,
    capability_label,
    group_label,
    role_description,
    role_label,
)
from app.schemas import CapabilityOut, RoleMatrixOut, RoleOut

router = APIRouter(prefix="/api/roles", tags=["roles"])


@router.get("", response_model=RoleMatrixOut)
async def role_matrix(_current: CurrentUser) -> RoleMatrixOut:
    """Every role, every capability, and which crosses where.

    The roles come out in the order of `KNOWN_ROLES` - the permission ladder,
    most to least - so the table reads from the top down rather than
    alphabetically.
    """
    return RoleMatrixOut(
        capabilities=[
            CapabilityOut(
                name=name,
                label=capability_label(name),
                group=capability_group(name),
                group_label=group_label(capability_group(name)),
            )
            for name in CAPABILITY_ORDER
        ],
        roles=[
            RoleOut(
                name=role,
                label=role_label(role),
                description=role_description(role),
                capabilities=sorted(ROLE_CAPABILITIES[role]),
            )
            for role in KNOWN_ROLES
        ],
    )
