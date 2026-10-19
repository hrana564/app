"""
What a rule is allowed to read.

This module is the ONLY contract between the datasource catalogue and everything
downstream of it. A consumer asks a catalogue for the names an entity may read;
it never asks a database, a folder, or a registry file.

    ETL team ──writes CSVs──> mount/SourceData/<ENTITY>/<date>/
                                     │
                             app.engine.discovery      (filesystem only)
                                     │  admin registers what we consume
                                     ▼
                              the datasources table
                                     │
                                     ▼
                            app.engine.catalog  ◄── this file
                                     │
        ┌──────────────┬─────────────┼─────────────┬───────────────┐
      rules         runs          replay      rule_generator     UI
   (validate)   (ctx.table)   (frozen set)    (LLM prompt)   (admin)
                                    │
                        no database, no live catalogue

TWO IMPLEMENTATIONS, ONE INTERFACE
---------------------------------
DatabaseCatalog is the live answer: what an admin has registered and not
retired. SnapshotCatalog is what a single frozen run was given, read back out of
its own manifest.

Replay uses the second deliberately. AGENTS.md requires a replay to work with no
database and no network, and more importantly a replay should reproduce what THAT
run saw - not what the catalogue looks like today. If an admin retires a table
next week, replaying a run from last week must still work, and must still see
the table that run actually read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class CatalogEntry:
    """One registered table. The name is the contract; rules reference it."""

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
    """The whole surface a consumer gets. Kept this small on purpose."""

    def names_for(self, entity: str) -> frozenset[str]:
        """Table names this entity may read right now."""

    def entries_for(self, entity: str) -> Sequence[CatalogEntry]:
        """The same thing, with metadata. Used for prompts and the UI."""


class DatabaseCatalog:
    """The live catalogue: every table an admin has registered.

    No filtering. A table is either in the catalogue or it has been removed, and
    removal is refused while a rule reads it - so there is no third state for
    this class to interpret.
    """

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

    def all_entries(self) -> Sequence[CatalogEntry]:
        return tuple(e for rows in self._by_entity.values() for e in rows)


class SnapshotCatalog:
    """What one frozen run was given, read from its manifest.

    Built from table names alone. A run's manifest is the record of what it
    read, so it is the only honest answer to "could this rule read anything" -
    and it needs neither the database nor the live catalogue.
    """

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
    """Read the live catalogue. The one place that knows the table exists.

    The model import is local so this module stays importable without the ORM -
    SnapshotCatalog and CatalogEntry are used by replay, which runs with no
    database at all.
    """
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