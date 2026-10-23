from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import require
from app.engine.validation import binding, rewrite_datasources
from app.models import User
from app.permissions import RULES_READ
from app.schemas import SourceBindingIn, SourceBindingOut

router = APIRouter(prefix="/api/source", tags=["source"])

SourceReader = Annotated[User, Depends(require(RULES_READ))]


@router.post("/binding", response_model=SourceBindingOut)
async def source_binding(payload: SourceBindingIn, _user: SourceReader) -> SourceBindingOut:
    """What a rule's source declares and reads, and the source with a new declaration.

    The browser used to work this out with a regex. The server has the AST,
    and the server is what decides whether a rule may be activated - so the
    server is where the answer belongs, and there is only one of it.

    With `names`, the source comes back rewritten. Nothing is saved: the
    caller decides what to do with the text.
    """
    source = payload.source_code
    if payload.names is not None:
        source = rewrite_datasources(source, payload.names)
    return SourceBindingOut(**binding(source), source_code=source)
