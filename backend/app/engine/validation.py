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
def _datasources_assignment(tree: ast.Module) -> ast.Assign | None:
    """The module-level `DATASOURCES = ...` statement, if there is one."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(t, ast.Name) and t.id == "DATASOURCES" for t in node.targets):
            return node
    return None


def _declared_list(tree: ast.Module) -> list[str] | None:
    """The declaration's names, or None when there is no list to read."""
    node = _datasources_assignment(tree)
    if node is None:
        return None
    try:
        return _as_list(ast.literal_eval(node.value))
    except (ValueError, TypeError, SyntaxError):
        return None


def _declared_constant(tree: ast.Module) -> list[str]:
    return _declared_list(tree) or []


def declared_names(source: str) -> list[str]:
    try:
        return _declared_constant(ast.parse(source))
    except SyntaxError:
        return []


def referenced_tables(source: str, tree: ast.Module | None = None) -> list[str]:
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
    return sorted(set(declared_names(source)) | set(referenced_tables(source)))


# ---------------------------------------------------------------------------
# The binding - what a source declares, what it reads, and the difference
# ---------------------------------------------------------------------------
def declaration_text(names: list[str]) -> str:
    """The DATASOURCES line, written the way a person would write it."""
    unique = sorted(set(names))
    if not unique:
        return "DATASOURCES = []"
    if len(unique) <= 3:
        return "DATASOURCES = [" + ", ".join(f'"{n}"' for n in unique) + "]"
    return "DATASOURCES = [\n" + "".join(f'    "{n}",\n' for n in unique) + "]"


def binding(source: str) -> dict[str, Any]:
    """What a source declares, what it reads, and where the two disagree.

    One answer, from the AST. The browser used to work this out with a regex,
    which could disagree with the validator about the same file - and the
    validator is the one that decides whether a rule may be activated.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        tree = None

    declared = _declared_list(tree) if tree is not None else None
    referenced = referenced_tables(source, tree)
    names = declared or []
    unreadable = declared is None

    return {
        "declared": names,
        "referenced": referenced,
        "unreadable": unreadable,
        "undeclared": referenced if unreadable else [t for t in referenced if t not in names],
        "unused": [] if unreadable else [t for t in names if t not in referenced],
        "declaration": "" if unreadable else declaration_text(names),
    }


def rewrite_datasources(source: str, names: list[str]) -> str:
    """The source with its DATASOURCES declaration set to `names`.

    The assignment is found with the AST and replaced where it stands, so a
    declaration written across several lines, or with a comment inside it, is
    replaced whole rather than half-matched.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    text = declaration_text(names)
    lines = source.splitlines(keepends=True)
    node = _datasources_assignment(tree)

    if node is not None:
        start = node.lineno - 1
        end = node.end_lineno or node.lineno
        return "".join(lines[:start]) + text + "\n" + "".join(lines[end:])

    # No declaration at all. Put one where Python expects it: after the
    # imports and the module docstring, before anything that uses it.
    anchor = 0
    for stmt in tree.body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            anchor = max(anchor, stmt.end_lineno or stmt.lineno)
        elif (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        ):
            anchor = max(anchor, stmt.end_lineno or stmt.lineno)

    return "".join(lines[:anchor]) + text + "\n\n" + "".join(lines[anchor:])


def _module_strings(tree: ast.Module) -> dict[str, str]:
    found: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                found[target.id] = value.value
    return found


def _column_of(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    if getattr(node.func, "attr", None) != "col" or not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _string_of(node: ast.AST, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def compared_literals(source: str) -> list[tuple[str, str]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    constants = _module_strings(tree)

    negated = {
        id(node.operand)
        for node in ast.walk(tree)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Invert)
    }

    found: set[tuple[str, str]] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            for op, left, right in zip(node.ops, operands, operands[1:]):
                if not isinstance(op, ast.Eq):
                    continue
                column = _column_of(left) or _column_of(right)
                if column is None:
                    continue
                literal = _string_of(right, constants)
                if literal is None:
                    literal = _string_of(left, constants)
                if literal is not None:
                    found.add((column, literal))
        elif isinstance(node, ast.Call):
            if getattr(node.func, "attr", None) != "is_in" or not node.args:
                continue
            if id(node) in negated:
                continue
            column = _column_of(node.func.value)
            if column is None:
                continue
            argument = node.args[0]
            elements = (
                argument.elts
                if isinstance(argument, (ast.List, ast.Tuple, ast.Set))
                else []
            )
            for element in elements:
                literal = _string_of(element, constants)
                if literal is not None:
                    found.add((column, literal))

    return sorted(found)


def scan_source(source: str) -> list[str]:
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
        # One derivation, shared with the write path, so what is validated and
        # what is recorded can never disagree - and one read of the file, since
        # the declaration, the static scan and the tables it reads are three
        # answers about one piece of text.
        try:
            text: str | None = read_source(self.source_file, self.rules_dir)
        except FileNotFoundError:
            text = None
        result.declared_datasources = declared_tables(text or "")

        # --- 3: static scan of the source ----------------------------------
        scan_errors, read_by_rule = self._scan_source(text)
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
        if read_by_rule and not declared_names(text or ""):
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

    def _scan_source(self, source: str | None) -> tuple[list[str], list[str]]:
        if source is None:
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