from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl


class RuleContext:
    def __init__(
        self,
        *,
        data_date: date,
        lookback_days: int = 1,
        registered: list[str] | None = None,
        snapshot: Any = None,
    ):
        self.date = data_date
        # How far back from `date` this rule reads: 1 = that day, 5 = T-5 to T.
        self.lookback_days = lookback_days
        self._registered = registered or []
        self._logs: list[str] = []
        self._snapshot = snapshot
        self._cache: dict[str, pl.DataFrame] = {}

    # -- data access ----------------------------------------------------
    def table(self, name: str) -> pl.DataFrame:
        if name in self._cache:
            return self._cache[name]

        if self._registered and name not in self._registered:
            raise KeyError(
                f"Unknown datasource '{name}'. "
                f"Registered: {', '.join(sorted(self._registered)) or '(none)'}"
            )

        paths = self._paths_for(name)
        if not paths:
            available = self._available() or "(none)"
            raise LookupError(
                f"No files were staged for '{name}'. This run staged: "
                f"{', '.join(sorted(available))}. A rule must only read tables "
                f"it declared - check DATASOURCES and lookback_days."
            )

        frame = (
            pl.read_csv(paths[0]) if len(paths) == 1
            else pl.read_csv(paths, infer_schema_length=None)
        )
        self._cache[name] = frame
        return frame

    def _paths_for(self, name: str) -> list[str]:
        if self._snapshot is None:
            return []
        return [str(p) for p in self._snapshot.paths_for(name)]

    def _available(self) -> list[str]:
        if self._snapshot is None:
            return []
        return list(self._snapshot.tables.keys())

    def count_rows(self, name: str) -> int:
        return self.table(name).height

    # -- diagnostics ----------------------------------------------------
    def log(self, message: str) -> None:
        self._logs.append(str(message))

    @property
    def logs(self) -> list[str]:
        return list(self._logs)


def invoke_rule(
    module: Any,
    *,
    data_date: date,
    lookback_days: int,
    registered: list[str] | None = None,
    snapshot: Any = None,
) -> tuple[dict[str, Any], list[str]]:
    ctx = RuleContext(
        data_date=data_date,
        lookback_days=lookback_days,
        registered=registered,
        snapshot=snapshot,
    )
    return module.run(ctx), ctx.logs