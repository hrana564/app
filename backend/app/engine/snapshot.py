"""
Per-run execution folder.

A rule run answers a question about a specific day, using a specific version of
the code, reading specific bytes. None of those three stay still: the CSVs keep
landing, the rule keeps getting edited. So a failure you cannot reproduce is a
failure you cannot fix.

Every run therefore gets a folder that freezes all three:

    mount/Rules_Executions/000123/
        metadata.json    what was staged, with a SHA-256 per file
        rule.py          byte-identical copy of the source that ran
        data/CLIENT-A/2026-10-16/hr_employees.csv
        data/CLIENT-A/2026-10-16/ipos_sales.csv
        result.json      written after the rule returns
        error.txt        written only when it raises

`metadata.json` is the contract. `app.engine.ctx` reads it to build frames, and
nothing else - the reader never globs a directory or guesses a path, so a run
can only ever see the files listed here.

MISSING INPUT IS AN ERROR
-------------------------
A source file that failed to land must not read as "no breach". A rule handed
nothing returns zero rows, the run reports `pass`, and no ticket opens - so the
audit record claims a clean day that was never actually checked. `blocking_problem`
turns that into an explicit error naming the file.

TWO KINDS OF MISSING
--------------------
A day folder that does not exist is normal (a lookback reaching back before the
data starts, or a public holiday). A file missing from a day folder that *does*
exist is a landing failure and blocks the run. They are reported separately
because they mean opposite things.

This module imports no ORM and reads no database. Plain arguments in, a folder
and a `Snapshot` out.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.engine.loader import resolve_rule_path

log = logging.getLogger("abm.snapshot")

MANIFEST_NAME = "metadata.json"
RESULT_NAME = "result.json"
ERROR_NAME = "error.txt"
RULE_COPY_NAME = "rule.py"
_DATA_DIR = "data"


def days_for(anchor: date, lookback_days: int) -> list[date]:
    """The days a run reads, oldest first.

    lookback_days=1 is the anchor day alone; 5 is T-4..T. Never returns an
    empty list, so a misconfigured 0 still audits something rather than nothing.
    """
    span = max(1, int(lookback_days))
    return [anchor - timedelta(days=offset) for offset in range(span - 1, -1, -1)]


def _copy_counting(src: Path, dst: Path) -> tuple[int, int, str]:
    """Copy a file, returning (data_rows, bytes, sha256).

    Read once. The hash and the row count fall out of the same pass that writes
    the copy, so integrity costs nothing beyond the copy itself.
    """
    digest = hashlib.sha256()
    size = 0
    newlines = 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as fh, dst.open("wb") as out:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
            newlines += chunk.count(b"\n")
            size += len(chunk)
            out.write(chunk)
    return max(0, newlines - 1), size, digest.hexdigest()


@dataclass(frozen=True)
class StagedFile:
    table: str
    day: str
    rel: str
    rows: int
    bytes: int
    sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.rel,
            "day": self.day,
            "rows": self.rows,
            "bytes": self.bytes,
            "sha256": self.sha256,
        }


@dataclass
class Snapshot:
    """A frozen run folder on disk."""

    run_id: int
    root: Path
    entity: str
    data_date: date
    lookback_days: int
    rule_code: str
    rule_version: int
    rule_source_file: str
    tables: dict[str, list[StagedFile]] = field(default_factory=dict)
    missing_days: list[str] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)
    rule_sha256: str = ""
    captured_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # -- reading ---------------------------------------------------------
    def paths_for(self, table: str) -> list[Path]:
        """Absolute paths of the files staged for one table."""
        return [self.root / f.rel for f in self.tables.get(table, [])]

    def has_table(self, table: str) -> bool:
        return bool(self.tables.get(table))

    @property
    def anchor_day_present(self) -> bool:
        return str(self.data_date) not in self.missing_days

    def blocking_problem(self) -> str | None:
        """Why this run must not execute, or None if it is safe to run.

        A missing file inside a day that exists means the landing failed. The
        anchor day being absent means there is nothing to audit at all. Either
        way the run must record `error` - never `pass`.
        """
        if not self.anchor_day_present:
            return (
                f"no data folder for the anchor date {self.data_date} "
                f"in {self.entity} under the mount"
            )
        if self.missing_files:
            listed = ", ".join(self.missing_files)
            return (
                f"{len(self.missing_files)} source file(s) failed to land: {listed}"
            )
        return None

    # -- writing ---------------------------------------------------------
    def manifest(self) -> dict[str, Any]:
        """The contract a reader (or a person) reads to understand this run."""
        return {
            "run_id": self.run_id,
            "captured_at": self.captured_at.isoformat(),
            "rule": {
                "code": self.rule_code,
                "version": self.rule_version,
                "source_file": self.rule_source_file,
                "sha256": self.rule_sha256,
            },
            "entity": self.entity,
            "data_date": str(self.data_date),
            "lookback_days": self.lookback_days,
            "days": [str(d) for d in days_for(self.data_date, self.lookback_days)],
            "tables": {
                name: {
                    "files": [f.as_dict() for f in files],
                    "rows": sum(f.rows for f in files),
                }
                for name, files in sorted(self.tables.items())
            },
            "missing_days": self.missing_days,
            "missing_files": self.missing_files,
        }

    def write_manifest(self) -> Path:
        path = self.root / MANIFEST_NAME
        path.write_text(
            json.dumps(self.manifest(), indent=2) + "\n", encoding="utf-8"
        )
        return path

    def write_result(self, payload: dict[str, Any]) -> Path:
        path = self.root / RESULT_NAME
        path.write_text(json.dumps(payload, indent=2, default=str) + "\n",
                        encoding="utf-8")
        return path

    def write_error(self, message: str, traceback_text: str | None = None) -> Path:
        body = message if traceback_text is None else f"{message}\n\n{traceback_text}"
        path = self.root / ERROR_NAME
        path.write_text(body, encoding="utf-8")
        return path

    def reload(self) -> Snapshot:
        """Rebuild a Snapshot from an existing folder, for replay.

        Fails loudly on a folder with no manifest: an incomplete snapshot must
        never be replayed as though it were complete.
        """
        path = self.root / MANIFEST_NAME
        if not path.exists():
            raise FileNotFoundError(
                f"{self.root} has no {MANIFEST_NAME} - the run did not finish "
                f"staging, so it cannot be replayed"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        tables: dict[str, list[StagedFile]] = {}
        for name, block in (data.get("tables") or {}).items():
            tables[name] = [
                StagedFile(
                    table=name,
                    day=f["day"],
                    rel=f["path"],
                    rows=int(f.get("rows", 0)),
                    bytes=int(f.get("bytes", 0)),
                    sha256=f.get("sha256", ""),
                )
                for f in block.get("files", [])
            ]
        return Snapshot(
            run_id=int(data["run_id"]),
            root=self.root,
            entity=data["entity"],
            data_date=date.fromisoformat(data["data_date"]),
            lookback_days=int(data.get("lookback_days", 1)),
            rule_code=(data.get("rule") or {}).get("code", ""),
            rule_version=int((data.get("rule") or {}).get("version", 0)),
            rule_source_file=(data.get("rule") or {}).get("source_file", ""),
            tables=tables,
            missing_days=list(data.get("missing_days") or []),
            missing_files=list(data.get("missing_files") or []),
            rule_sha256=(data.get("rule") or {}).get("sha256", ""),
        )


def capture(
    run_id: int,
    *,
    entity: str,
    sources: list[str],
    anchor: date,
    lookback_days: int,
    source_file: str,
    rule_code: str,
    rule_version: int,
    runs_dir: Path | None = None,
    sourcedata_dir: Path | None = None,
    rules_dir: Path | None = None,
) -> Snapshot:
    """Stage the exact inputs for one run, then publish the folder atomically.

    Everything is written into `<run_id>.staging` and renamed into place only
    once the manifest is on disk. A process that dies mid-copy leaves a staging
    directory and no run folder, so a half-built snapshot can never be mistaken
    for a replayable one.
    """
    from app.settings import settings

    base = Path(runs_dir or settings.runs_dir)
    mount = Path(sourcedata_dir or settings.sourcedata_dir)
    entity = entity.upper()

    final = base / f"{run_id:06d}"
    staging = base / f"{run_id:06d}.staging"
    if final.exists():
        # Run ids come from the database sequence, so this cannot happen
        # without something upstream reusing one. Refuse rather than delete
        # evidence of whatever is already there.
        raise FileExistsError(
            f"run folder {final} already exists; refusing to overwrite it"
        )
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    snap = Snapshot(
        run_id=run_id,
        root=final,
        entity=entity,
        data_date=anchor,
        lookback_days=lookback_days,
        rule_code=rule_code,
        rule_version=rule_version,
        rule_source_file=source_file,
    )

    # -- the rule source, byte for byte ---------------------------------
    rule_path = resolve_rule_path(source_file, rules_dir)
    rule_copy = staging / RULE_COPY_NAME
    shutil.copy2(rule_path, rule_copy)
    digest = hashlib.sha256()
    with rule_copy.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    snap.rule_sha256 = digest.hexdigest()

    # -- one file per source per day ------------------------------------
    for source in sorted(set(sources)):
        for day in days_for(anchor, lookback_days):
            day_dir = mount / entity / day.isoformat()
            if not day_dir.is_dir():
                if str(day) not in snap.missing_days:
                    snap.missing_days.append(str(day))
                continue
            src = day_dir / f"{source}.csv"
            if not src.exists():
                snap.missing_files.append(f"{day}/{source}.csv")
                continue
            rel = f"{_DATA_DIR}/{entity}/{day.isoformat()}/{source}.csv"
            rows, size, sha = _copy_counting(src, staging / rel)
            snap.tables.setdefault(source, []).append(
                StagedFile(
                    table=source,
                    day=day.isoformat(),
                    rel=rel,
                    rows=rows,
                    bytes=size,
                    sha256=sha,
                )
            )

    snap.missing_days.sort()
    snap.missing_files.sort()

    manifest = json.dumps(snap.manifest(), indent=2) + "\n"
    (staging / MANIFEST_NAME).write_text(manifest, encoding="utf-8")

    staging.rename(final)

    staged = sum(len(f) for f in snap.tables.values())
    log.info(
        "run %s staged %d file(s) for %d table(s) into %s%s",
        run_id, staged, len(snap.tables), final,
        f" [BLOCKED: {snap.blocking_problem()}]" if snap.blocking_problem() else "",
    )
    return snap


def open_run(runs_dir: Path, run_id: int) -> Snapshot:
    """Reopen a previously captured run folder for replay."""
    return Snapshot(
        run_id=run_id, root=Path(runs_dir) / f"{run_id:06d}",
        entity="", data_date=date.today(), lookback_days=1,
        rule_code="", rule_version=0, rule_source_file="",
    ).reload()