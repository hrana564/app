from __future__ import annotations

import logging
import shutil
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import date
from itertools import islice
from pathlib import Path
from typing import Any

import csv

from app.engine.ctx import invoke_rule
from app.engine.loader import load_rule_at
from app.engine.snapshot import capture
from app.engine.validation import compared_literals, referenced_tables, scan_source
from app.schemas import RuleResult
from app.settings import settings

log = logging.getLogger("abm.trial")

# How a trial ended. The first two are the rule's own doing; the second two
# are the estate's.
STATUS_OK = "ok"
STATUS_CODE_ERROR = "code_error"
STATUS_DATA_MISSING = "data_missing"
STATUS_NOT_ALLOWED = "not_allowed"
STATUS_BANNED = "banned"

# The statuses an agent can act on. Everything else is a stop.
REPAIRABLE = {STATUS_CODE_ERROR, STATUS_NOT_ALLOWED, STATUS_BANNED}

# How many rows of a staged table are read to check a literal against the data.
# The check is a sanity signal, not a proof, so it does not need every row.
LITERAL_SCAN_ROWS = 5000


@dataclass
class TrialResult:
    status: str
    ok: bool = False
    outcome: str | None = None  # pass | fail - what the control FOUND
    summary: str = ""
    rows: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    traceback: str | None = None
    duration_ms: int = 0
    referenced: list[str] = field(default_factory=list)
    name: str | None = None
    description: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def repairable(self) -> bool:
        return self.status in REPAIRABLE

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ok": self.ok,
            "outcome": self.outcome,
            "summary": self.summary,
            "rows": self.rows,
            "metrics": self.metrics,
            "error": self.error,
            "traceback": self.traceback,
            "duration_ms": self.duration_ms,
            "referenced": self.referenced,
            "name": self.name,
            "description": self.description,
            "warnings": self.warnings,
        }


def _scratch_dir() -> Path:
    base = Path(settings.trials_dir)
    token = uuid.uuid4().hex[:12]
    path = base / token
    path.mkdir(parents=True, exist_ok=True)
    return path


def _run_within(module, *, data_date, lookback_days, registered, snapshot, timeout):
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="trial")
    future = executor.submit(
        invoke_rule,
        module,
        data_date=data_date,
        lookback_days=lookback_days,
        registered=registered,
        snapshot=snapshot,
    )
    try:
        return future.result(timeout=timeout)
    finally:
        executor.shutdown(wait=False)


def _staged_values(snap, tables: list[str]) -> tuple[dict[str, set[str]], set[str]]:
    values: dict[str, set[str]] = {}
    columns: set[str] = set()
    for table in tables:
        for path in snap.paths_for(table):
            try:
                with path.open(newline="", encoding="utf-8") as fh:
                    reader = csv.reader(fh)
                    try:
                        header = next(reader)
                    except StopIteration:
                        continue
                    columns.update(header)
                    for row in islice(reader, LITERAL_SCAN_ROWS):
                        for index, name in enumerate(header):
                            if index < len(row) and row[index]:
                                values.setdefault(name, set()).add(row[index].strip())
            except (OSError, UnicodeDecodeError):
                continue
    return values, columns


def _literal_warnings(source: str, snap, tables: list[str]) -> list[str]:
    pairs = compared_literals(source)
    if not pairs:
        return []

    values, columns = _staged_values(snap, tables)
    warnings: list[str] = []
    for column, literal in pairs:
        if column not in columns or literal in values.get(column, set()):
            continue
        warnings.append(
            f"The rule tests {column} for {literal!r}, which does not occur in "
            f"the data for this date. If that is the intended value, the "
            f"control finds nothing - check the spelling against the values "
            f"listed for {column}."
        )
    return warnings


def run_trial(
    source: str,
    *,
    entity: str,
    tables: list[str],
    data_date: date,
    lookback_days: int = 1,
    allowed: list[str] | None = None,
    timeout: int | None = None,
) -> TrialResult:
    import time

    started = time.perf_counter()
    timeout = timeout or settings.trial_timeout_seconds

    def elapsed_ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    referenced = referenced_tables(source)

    def fail(status: str, error: str, tb: str | None = None) -> TrialResult:
        return TrialResult(
            status=status,
            ok=False,
            error=error,
            traceback=tb,
            duration_ms=elapsed_ms(),
            referenced=referenced,
        )

    # --- the gate, before anything is executed --------------------------
    problems = scan_source(source)
    if problems:
        return fail(
            STATUS_BANNED,
            "The rule is not allowed to run: " + "; ".join(problems),
        )

    if "def run(" not in source:
        return fail(
            STATUS_BANNED,
            "The rule does not define run(ctx), so there is nothing to execute.",
        )

    if allowed is not None:
        outside = [t for t in referenced if t not in allowed]
        if outside:
            return fail(
                STATUS_NOT_ALLOWED,
                (
                    f"The rule reads {', '.join(outside)}, which was not selected. "
                    f"Use only: {', '.join(sorted(allowed)) or '(none)'}."
                ),
            )

    if not referenced:
        return fail(
            STATUS_CODE_ERROR,
            "The rule never calls ctx.table(), so it reads nothing and can find "
            "nothing. Name the tables it uses.",
        )

    scratch = _scratch_dir()
    try:
        # --- stage, with the production staging code --------------------
        (scratch / "candidate.py").write_text(source, encoding="utf-8")
        try:
            snap = capture(
                0,
                entity=entity,
                sources=referenced,
                anchor=data_date,
                lookback_days=lookback_days,
                source_file="candidate.py",
                rule_code="TRIAL",
                rule_version=0,
                runs_dir=scratch,
                rules_dir=scratch,
            )
        except Exception as exc:  # noqa: BLE001
            return fail(
                STATUS_DATA_MISSING,
                f"Could not stage the inputs: {type(exc).__name__}: {exc}",
            )

        problem = snap.blocking_problem()
        if problem:
            return fail(
                STATUS_DATA_MISSING,
                (
                    f"The data this rule needs is not there ({problem}). The ETL "
                    f"has to land it first - no change to the rule will fix this."
                ),
            )

        # --- run the copy that was frozen into the folder ----------------
        try:
            module = load_rule_at(snap.root / "rule.py")
        except Exception as exc:  # noqa: BLE001
            return fail(
                STATUS_CODE_ERROR,
                f"The rule could not be loaded: {type(exc).__name__}: {exc}",
                traceback.format_exc()[-4000:],
            )

        try:
            raw, logs = _run_within(
                module,
                data_date=data_date,
                lookback_days=lookback_days,
                registered=sorted(set(referenced)),
                snapshot=snap,
                timeout=timeout,
            )
        except FutureTimeout:
            return fail(
                STATUS_CODE_ERROR,
                f"The rule did not finish within {timeout}s and was abandoned. "
                f"It is probably looping.",
            )
        except Exception as exc:  # noqa: BLE001
            return fail(
                STATUS_CODE_ERROR,
                f"{type(exc).__name__}: {exc}",
                traceback.format_exc()[-4000:],
            )

        try:
            result = RuleResult.model_validate(raw)
        except Exception as exc:  # noqa: BLE001
            return fail(
                STATUS_CODE_ERROR,
                f"The rule did not return a valid result: {exc}",
            )

        if result.status != "success":
            return TrialResult(
                status=STATUS_CODE_ERROR,
                ok=False,
                error=(
                    result.error_message
                    or "The rule reported that it could not run, although the "
                    "data it was given is present."
                ),
                duration_ms=elapsed_ms(),
                referenced=referenced,
            )

        rows = result.rows[: settings.max_rows_in_ticket]
        summary = result.summary
        if logs:
            summary = (summary + " | logs: " + "; ".join(logs)).strip()

        return TrialResult(
            status=STATUS_OK,
            ok=True,
            outcome="fail" if rows else "pass",
            summary=summary,
            rows=rows,
            metrics=result.metrics,
            duration_ms=elapsed_ms(),
            referenced=referenced,
            name=getattr(module, "NAME", None),
            description=getattr(module, "DESCRIPTION", None),
            warnings=_literal_warnings(source, snap, sorted(set(referenced))),
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
