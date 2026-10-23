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
