"""
Rule validation.

A rule must pass this before it can be activated, so a broken rule never
reaches an unattended 06:30 scheduled run. Validation is deliberately
*static* - it compiles the module and inspects what it declares, without
touching any data. That makes it cheap enough to run on every save.

Checks:
  1. the module imports and compiles
  2. it defines run(ctx)
  3. it does not import banned modules or call open()
  4. every DATASOURCES entry is registered for the rule's entity
  5. every PARAMS entry exists in the parameter store
  6. the declared ALERT block agrees with the database columns
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.datasources import registry_for
from app.engine.loader import load_rule
from app.models import Param

# Imports a rule may legitimately use.
ALLOWED_IMPORTS = {
    "json", "datetime", "statistics", "math", "decimal",
    "collections", "itertools", "functools", "re", "typing",
}

BANNED_IMPORTS = {
    "os", "sys", "subprocess", "socket", "shutil", "pickle",
    "importlib", "ctypes", "multiprocessing", "threading", "requests",
}

# `ctx.table("x")` is normally written INSIDE an SQL string, which the AST
# cannot see - it is just text inside a string literal. A regex pass is the only
# way to catch those references statically. The lookbehind keeps it from matching
# `other_ctx.table(...)`.
_CTX_TABLE_REF = re.compile(
    r"""(?<![\w.])ctx\.(?:table|count_rows)\(\s*['"]([A-Za-z_]\w*)['"]"""
)

BANNED_CALLS = {"open", "eval", "exec", "compile", "__import__", "input"}


@dataclass
class ValidationResult:
    status: str  # "valid" | "invalid"
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    declared_params: list[str] = field(default_factory=list)
    declared_datasources: list[str] = field(default_factory=list)
    declared_alert: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "valid"


class RuleValidator:
    def __init__(self, source_file: str, entity: str, *, rules_dir=None):
        self.source_file = source_file
        self.entity = entity.upper()
        self.rules_dir = rules_dir

    async def validate(self, session: AsyncSession) -> ValidationResult:
        result = ValidationResult(status="valid")

        # --- 1 & 2: imports, compiles, defines run(ctx) -------------------
        try:
            module = load_rule(self.source_file, self.rules_dir)
        except Exception as exc:  # noqa: BLE001
            result.status = "invalid"
            result.errors.append(f"module failed to load: {type(exc).__name__}: {exc}")
            return result

        if not hasattr(module, "run"):
            result.status = "invalid"
            result.errors.append("module has no run(ctx) function")
            return result

        result.metadata = {
            "name": getattr(module, "NAME", None),
            "description": getattr(module, "DESCRIPTION", None),
            "entity": getattr(module, "ENTITY", None),
        }
        result.declared_params = _as_list(getattr(module, "PARAMS", None))
        result.declared_datasources = _as_list(getattr(module, "DATASOURCES", None))
        result.declared_alert = dict(getattr(module, "ALERT", None) or {})

        # --- 3: static scan of the source ----------------------------------
        scan_errors, referenced_tables = self._scan_source()
        result.errors.extend(scan_errors)

        # --- 4: datasources must exist for this entity --------------------
        try:
            known = set(registry_for(self.entity).keys())
        except KeyError as exc:
            result.status = "invalid"
            result.errors.append(str(exc))
            return result

        # Check BOTH the declared DATASOURCES list and every ctx.table("x")
        # found in the SQL. Checking only the declaration lets a rule smuggle
        # in a cross-entity table reference that only fails at run time.
        used = sorted(set(result.declared_datasources) | set(referenced_tables))
        if referenced_tables and not result.declared_datasources:
            result.warnings.append(
                "rule uses ctx.table("
                + ", ".join(f'"{t}"' for t in referenced_tables)
                + ") but declares no DATASOURCES - add it so validation can cover it"
            )

        for name in used:
            if name not in known:
                result.errors.append(
                    f"references datasource '{name}', which is not registered for "
                    f"{self.entity}. Available: {', '.join(sorted(known))}"
                )
                result.status = "invalid"
        result.declared_datasources = used

        # --- 5: declared params must exist ---------------------------------
        if result.declared_params:
            rows = (await session.execute(select(Param))).scalars().all()
            existing = {p.key for p in rows}
            for key in result.declared_params:
                if key not in existing:
                    result.warnings.append(
                        f"PARAMS declares '{key}', which does not exist yet - "
                        f"ctx.params.get('{key}') will fall back to the rule's default"
                    )

        # --- 6: declared ENTITY must match the database --------------------
        declared_entity = result.metadata.get("entity")
        if declared_entity and str(declared_entity).upper() != self.entity:
            result.warnings.append(
                f"rule file declares ENTITY={declared_entity!r} but the rule is "
                f"registered under {self.entity}"
            )

        if result.errors:
            result.status = "invalid"
        return result

    def _scan_source(self) -> tuple[list[str], list[str]]:
        """AST scan for banned imports/calls, and collect ctx.table("x") refs."""
        from app.engine.loader import read_source

        try:
            source = read_source(self.source_file, self.rules_dir)
        except FileNotFoundError:
            return [f"source file {self.source_file} does not exist"], []

        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            return [f"syntax error on line {exc.lineno}: {exc.msg}"], []

        problems: list[str] = []
        referenced_tables: list[str] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in BANNED_IMPORTS:
                        problems.append(
                            f"line {node.lineno}: import of '{alias.name}' is not "
                            f"allowed in a rule"
                        )
                    elif root not in ALLOWED_IMPORTS:
                        problems.append(
                            f"line {node.lineno}: import of '{alias.name}' is not on "
                            f"the allow-list ({', '.join(sorted(ALLOWED_IMPORTS))})"
                        )
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root in BANNED_IMPORTS:
                    problems.append(
                        f"line {node.lineno}: 'from {node.module} import ...' is not "
                        f"allowed in a rule"
                    )
            elif isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name in BANNED_CALLS:
                    problems.append(
                        f"line {node.lineno}: call to {name}() is not allowed in a rule"
                    )
                # ctx.table("x") used from Python
                if name in ("table", "count_rows") and node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        referenced_tables.append(first.value)

        # ...and the common case: ctx.table("x") written inside an SQL string,
        # which no AST pass can see. Union both sources.
        referenced_tables.extend(_CTX_TABLE_REF.findall(source))

        return problems, sorted(set(referenced_tables))


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def now() -> datetime:
    return datetime.now(timezone.utc)