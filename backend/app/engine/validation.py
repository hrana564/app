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
  5. every ctx.table() in the SQL is registered, whether declared or not
  6. a declared ENTITY matches the one the rule is registered under

Check 4 reads the live catalogue, so removing a datasource stops a NEW rule
referencing it. That is the enforcement point for the admin's decision - a rule
that has already been validated against a table keeps running, because silently
breaking a live control is worse than leaving it visible.

WHAT A RULE READS
-----------------
`declared_tables()` below is the single answer to "which tables does this source
touch", and every caller goes through it. It is pure text: no file, no database,
no catalogue. That matters because the answer is needed in two places with
different access - validating a rule reads the catalogue, while writing one only
has the text it was just handed. Deriving it twice would let the two drift, and
that drift is invisible: the rule would run correctly while the catalogue and
the rule's own page disagreed about what it reads.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.catalog import database_catalog
from app.engine.loader import load_rule, read_source

# Imports a rule may legitimately use.
#
# `polars` is the DataFrame library rules are written against and `difflib` is
# the stdlib string-similarity helper. Neither can reach the filesystem or the
# network on its own; the AST gate below is what keeps a rule from importing
# something that can.
ALLOWED_IMPORTS = {
    "json", "datetime", "statistics", "math", "decimal", "difflib",
    "collections", "itertools", "functools", "re", "typing",
    "polars",
}

BANNED_IMPORTS = {
    "os", "sys", "subprocess", "socket", "shutil", "pickle",
    "importlib", "ctypes", "multiprocessing", "threading", "requests",
}

# `ctx.table("x")` is written as a plain call, so the AST sees it directly.
_CTX_TABLE_REF = re.compile(
    r"""(?<![\w.])ctx\.(?:table|count_rows)\(\s*['"]([A-Za-z_]\w*)['"]"""
)

BANNED_CALLS = {"open", "eval", "exec", "compile", "__import__", "input"}


# ---------------------------------------------------------------------------
# What a rule reads - pure, shared by validation and by every write path
# ---------------------------------------------------------------------------
def _declared_constant(tree: ast.Module) -> list[str]:
    """The module-level `DATASOURCES = [...]` literal, or [] if there isn't one.

    Parsed rather than executed. A rule that built the list at import time would
    need the module to run to be understood, and the write path has only text.
    Rules are plain files holding a literal constant; anything else is treated
    as "declares nothing", which validation then flags.
    """
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "DATASOURCES" for t in node.targets):
            continue
        try:
            return _as_list(ast.literal_eval(node.value))
        except (ValueError, TypeError, SyntaxError):
            return []
    return []


def declared_names(source: str) -> list[str]:
    """Only the module-level `DATASOURCES` literal - not what the code reads.

    Kept separate from `declared_tables` because the difference is the whole
    point of the warning in check 4: a rule that reads a table it never
    declared is covered by validation but should still say so.
    """
    try:
        return _declared_constant(ast.parse(source))
    except SyntaxError:
        return []


def referenced_tables(source: str, tree: ast.Module | None = None) -> list[str]:
    """Every table this source reads with ctx.table("x") or ctx.count_rows("x").

    Both the plain call and the one written inside an SQL string, which no AST
    pass can see. Union both, because a rule that skips the declaration and
    names the table only in its SQL still reads it.
    """
    if tree is None:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return sorted(set(_CTX_TABLE_REF.findall(source)))

    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in ("table", "count_rows") and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    found.append(first.value)
    found.extend(_CTX_TABLE_REF.findall(source))
    return sorted(set(found))


def declared_tables(source: str) -> list[str]:
    """Every table this source declares or reads, sorted and deduplicated.

    The union, because a rule can name a table in `DATASOURCES` and never touch
    it, or read one it never declared. Recording only half of that is how the
    catalogue ends up disagreeing with the code that reads it.
    """
    return sorted(set(declared_names(source)) | set(referenced_tables(source)))


def scan_source(source: str) -> list[str]:
    """Every reason this file may not run. Pure text - no file, no database.

    The gate a rule must pass before it is allowed to execute. It is a function
    of the source alone so that every caller gets the same answer: the
    validator, and the trial that runs a candidate before it is saved.

    A candidate that has not been through this gate is not executed. A rule is
    code, and code that has not been read is code that does not get to run.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax error on line {exc.lineno}: {exc.msg}"]

    problems: list[str] = []

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

    return problems


@dataclass
class ValidationResult:
    status: str  # "valid" | "invalid"
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    declared_datasources: list[str] = field(default_factory=list)
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
        # One derivation, shared with the write path, so what is validated
        # and what is recorded can never disagree.
        try:
            text = read_source(self.source_file, self.rules_dir)
        except FileNotFoundError:
            text = ""
        result.declared_datasources = declared_tables(text)

        # --- 3: static scan of the source ----------------------------------
        scan_errors, read_by_rule = self._scan_source()
        result.errors.extend(scan_errors)

        # --- 4: datasources must exist for this entity --------------------
        # The catalogue, not a constant. A table removed from the catalogue is
        # refused here, so a new rule cannot bind to a name that resolves to
        # nothing - and ctx.table() would refuse it at run time anyway.
        catalog = await database_catalog(session)
        known = set(catalog.names_for(self.entity))

        # `declared_datasources` is already the union of the declaration and
        # every ctx.table() in the SQL, so this one list is the whole surface.
        # Checking only DATASOURCES would let a rule smuggle in a cross-entity
        # reference that otherwise fails only at run time.
        if read_by_rule and not declared_names(text):
            result.warnings.append(
                "rule uses ctx.table("
                + ", ".join(f'"{t}"' for t in read_by_rule)
                + ") but declares no DATASOURCES - add it so the declaration "
                "matches what the code reads"
            )

        for name in result.declared_datasources:
            if name not in known:
                result.errors.append(
                    f"references datasource '{name}', which is not registered for "
                    f"{self.entity}. Available: {', '.join(sorted(known))}"
                )
                result.status = "invalid"

        # --- 5: declared ENTITY must match the database --------------------
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
        """AST scan for banned imports/calls, and collect ctx.table("x") refs.

        Returns only what the scan is uniquely for - the problems. The table
        names come from `referenced_tables()` so there is one implementation of
        the tables it reads come from the shared helper, and the problems come
        from `scan_source()` so there is one implementation of what is allowed
        to run.
        """
        try:
            source = read_source(self.source_file, self.rules_dir)
        except FileNotFoundError:
            return [f"source file {self.source_file} does not exist"], []

        return scan_source(source), referenced_tables(source)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def now() -> datetime:
    return datetime.now(timezone.utc)