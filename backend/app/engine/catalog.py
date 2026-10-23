from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    entity: str
    system: str = ""
    description: str = ""
    grain: str = ""
    columns: Mapping[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.columns is None:
            object.__setattr__(self, "columns", {})


class Catalog(Protocol):
    def names_for(self, entity: str) -> frozenset[str]:
        pass
    def entries_for(self, entity: str) -> Sequence[CatalogEntry]:
        pass

class DatabaseCatalog:
    def __init__(self, entries: Iterable[CatalogEntry]):
        self._by_entity: dict[str, list[CatalogEntry]] = {}
        for entry in entries:
            self._by_entity.setdefault(entry.entity.upper(), []).append(entry)
        for rows in self._by_entity.values():
            rows.sort(key=lambda e: e.name)

    def names_for(self, entity: str) -> frozenset[str]:
        return frozenset(e.name for e in self._by_entity.get(entity.upper(), ()))

    def entries_for(self, entity: str) -> Sequence[CatalogEntry]:
        return tuple(self._by_entity.get(entity.upper(), ()))


class SnapshotCatalog:
    def __init__(self, entity: str, tables: Iterable[str]):
        self._entity = entity.upper()
        self._names = frozenset(tables)

    def names_for(self, entity: str) -> frozenset[str]:
        # A snapshot belongs to one entity. Asking about another is a bug in the
        # caller, and answering with everything would hide it.
        if entity.upper() != self._entity:
            return frozenset()
        return self._names

    def entries_for(self, entity: str) -> Sequence[CatalogEntry]:
        if entity.upper() != self._entity:
            return ()
        return tuple(
            CatalogEntry(name=name, entity=self._entity) for name in sorted(self._names)
        )


async def database_catalog(session: Any) -> DatabaseCatalog:
    from sqlalchemy import select

    from app.models import Datasource

    rows = (
        await session.execute(
            select(
                Datasource.name,
                Datasource.entity,
                Datasource.system,
                Datasource.description,
                Datasource.grain,
                Datasource.columns,
            )
        )
    ).all()

    return DatabaseCatalog(
        CatalogEntry(
            name=name,
            entity=entity,
            system=system or "",
            description=description or "",
            grain=grain or "",
            columns=dict(columns or {}),
        )
        for name, entity, system, description, grain, columns in rows
    )