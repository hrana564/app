"""Tunable parameters - thresholds auditors adjust without touching code."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.models import Param
from app.schemas import ParamOut, ParamUpsert

router = APIRouter(prefix="/api/params", tags=["params"])


@router.get("", response_model=list[ParamOut])
async def list_params(session: SessionDep, _user: CurrentUser) -> list[ParamOut]:
    rows = (await session.execute(select(Param).order_by(Param.key))).scalars().all()
    return [ParamOut.model_validate(p) for p in rows]


@router.get("/as-dict")
async def params_as_dict(session: SessionDep, _user: CurrentUser) -> dict:
    """Exactly what rules see as ctx.params - handy when writing a rule."""
    rows = (await session.execute(select(Param))).scalars().all()
    return {
        p.key: (p.value_numeric if p.value_numeric is not None else p.value_text)
        for p in rows
    }


@router.put("", response_model=ParamOut)
async def upsert_param(
    payload: ParamUpsert, session: SessionDep, admin: AdminUser
) -> ParamOut:
    param = (
        await session.execute(select(Param).where(Param.key == payload.key))
    ).scalar_one_or_none()

    if param is None:
        param = Param(key=payload.key, updated_by=admin.username)
        session.add(param)

    param.label = payload.label or payload.key
    param.value_numeric = payload.value_numeric
    param.value_text = payload.value_text
    param.description = payload.description
    param.updated_by = admin.username

    await session.commit()
    await session.refresh(param)
    return ParamOut.model_validate(param)


@router.delete("/{key}", status_code=204)
async def delete_param(key: str, session: SessionDep, admin: AdminUser) -> None:
    param = (
        await session.execute(select(Param).where(Param.key == key))
    ).scalar_one_or_none()
    if param is None:
        return
    await session.delete(param)
    await session.commit()