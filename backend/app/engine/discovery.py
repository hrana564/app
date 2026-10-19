"""
What the ETL actually landed.

Reads the CSV landing zone and reports it. That is the whole job. This module
touches no database and imports no model, so it can be pointed at a folder and
exercised without the app running.

    mount/SourceData/LAD/2026-10-19/
        hr_employees.csv
        ipos_sales.csv
        ...

The ETL belongs to another team and knows nothing about this app. The only
thing shared is the folder convention above, and that is deliberately small:
entity, date, filename. Everything an admin needs to decide whether to register
a table is derived from the file itself - the header gives the columns, and the
first rows give the types.

TWO KINDS OF ABSENCE
--------------------
A day folder that does not exist is normal: a public holiday, or a date before
the feed started. A file missing from a day folder that DOES exist is a landing
failure, and it is reported separately because it means the opposite thing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import polars as pl

from app.settings import settings

# How many rows to read to infer a column's type. Enough to see a bool or a
# date; far too few to matter on a file the ETL just wrote.
_TYPE_SAMPLE_ROWS = 200


@dataclass(frozen=True)
class DiscoveredFile:
    """One CSV that landed, described entirely by reading the file."""

    name: str
    entity: str
    data_date: str
    columns: dict[str, str]
    row_count: int
    size_bytes: int
    modified_at: datetime

    @property
    def column_count(self) -> int:
        return len(self.columns)


def entity_root(entity: str, sourcedata_dir: Path | None = None) -> Path:
    base = sourcedata_dir or settings.sourcedata_dir
    return base / entity.upper()


def available_dates(entity: str, sourcedata_dir: Path | None = None) -> list[str]:
    """Days that have a folder at all, oldest first. Unparseable names are ignored."""
    root = entity_root(entity, sourcedata_dir)
    if not root.exists():
        return []
    found: list[str] = []
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        try:
            date.fromisoformat(folder.name)
        except ValueError:
            continue
        found.append(folder.name)
    return sorted(found)


def latest_date(entity: str, sourcedata_dir: Path | None = None) -> str | None:
    dates = available_dates(entity, sourcedata_dir)
    return dates[-1] if dates else None


def describe_csv(path: Path, entity: str, data_date: str) -> DiscoveredFile | None:
    """Read one CSV and report what is in it. None if it cannot be read at all."""
    try:
        sample = pl.read_csv(path, n_rows=_TYPE_SAMPLE_ROWS)
        # Counting by scanning rather than reading: the whole point is to report
        # the real row count without pulling a full file into memory.
        row_count = int(
            pl.scan_csv(path).select(pl.len().alias("n")).collect()["n"][0]
        )
        stat = path.stat()
    except Exception:  # noqa: BLE001 - a malformed file is a finding, not a crash
        return None

    return DiscoveredFile(
        name=path.stem,
        entity=entity.upper(),
        data_date=data_date,
        columns={name: str(dtype) for name, dtype in sample.schema.items()},
        row_count=row_count,
        size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime),
    )


def discover(
    entity: str,
    data_date: str,
    sourcedata_dir: Path | None = None,
) -> list[DiscoveredFile]:
    """Every readable CSV in one day's folder, by name."""
    folder = entity_root(entity, sourcedata_dir) / data_date
    if not folder.is_dir():
        return []
    described = [
        describe_csv(csv_path, entity, data_date)
        for csv_path in sorted(folder.glob("*.csv"))
    ]
    return [d for d in described if d is not None]