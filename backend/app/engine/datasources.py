from __future__ import annotations

from app.settings import settings

ENTITIES = {e.code: e.name for e in settings.client_entities}

SYSTEMS_BY_ENTITY = {e.code: list(e.systems) for e in settings.client_entities}


def default_entity() -> str:
    return sorted(ENTITIES)[0]