"""
What a rule does, in plain English.

The head of audit opens a control and is shown sixty lines of Python. This
module answers "what does it actually do" from the file the runner will
execute, and from nothing else.

WHY IT IS DERIVED RATHER THAN WRITTEN
-------------------------------------
A summary is a claim about the code. Written once, it goes out of date the next
time the code changes - and nothing anywhere disagrees out loud. So the
structure (the steps, the lines they came from, the thresholds, what a finding
holds) is derived on every read. There is no copy to fall out of step.

The worded summary laid over the top is optional (`explain_llm.py`) and is only
shown once it has passed the gates here. A model may re-word a step; it may not
add one, drop one, or state a number that is not in the code - `check()` refuses
the prose otherwise. That is the whole design: the derivation supplies the
facts, the model supplies the sentence, and the gates hold the two together.

WHAT IT KNOWS
-------------
Source text only. No file, no database, no catalogue, no network - the same
discipline as `declared_tables()` in validation.py, and for the same reason:
the answer comes out the same whether the API, the smoke test or a laptop with
no database asks for it.

WHEN IT DOES NOT KNOW
---------------------
It says so. A step it cannot describe becomes "does something here" with its
line range and is flagged `explainable=False`. A confident wrong description is
worse than an honest gap: the gap is the one a reviewer investigates.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

# Calls that turn a frame into a Python value, or take a total of one that is
# about to be reported. Naming them produces sentences like "convert the rows
# into a list", which is noise in a description of a control.
PLUMBING = {
    "to_dicts", "to_list", "to_dict", "item", "sum", "mean", "max", "min",
    "len", "height", "cast", "alias", "fill_null", "drop_nulls", "unique",
    "count", "std", "median", "abs", "round", "collect", "lazy", "explode",
    "is_empty", "n_unique", "first", "last", "sort", "rename",
}

# Wrappers around a chain: `float(base["x"].sum())` is about the chain inside.
WRAPPERS = {"float", "int", "str", "round", "sum", "len", "list", "dict", "bool"}

# Words that describe plumbing rather than a control. The readability gate
# refuses them - they are what made the page unreadable in the first place.
JARGON = (
    "dataframe", "data frame", "boolean", "schema", "json", "api",
    "polars", "python", "regex", "tuple", "dict", "lambda", "null value",
    "sql", "function", "method", "loop over", "iterate",
)

COMPARISONS = {
    ast.Eq: "is",
    ast.NotEq: "is not",
    ast.Lt: "is less than",
    ast.LtE: "is at most",
    ast.Gt: "is more than",
    ast.GtE: "is at least",
    ast.In: "is one of",
    ast.NotIn: "is none of",
}

# The complement of each comparison, so "flag when NOT (ratio <= limit)" can be
# said as "ratio is more than limit" - which is what a reader needs, and what a
# negation stays true to.
FLIPPED = {
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE,
    ast.LtE: ast.Gt,
    ast.Gt: ast.LtE,
    ast.GtE: ast.Lt,
}

# Number words, so "more than twice" and "more than 2.0" are the same claim to
# the gates. Without this a summary could say "three times" about a limit of
# 2.0 and pass every check that only looked for digits.
NUMBER_WORDS = {
    "twice": 2.0, "thrice": 3.0, "half": 0.5, "double": 2.0,
    "three": 3.0, "four": 4.0, "five": 5.0,
    "six": 6.0, "seven": 7.0, "eight": 8.0, "nine": 9.0, "ten": 10.0,
}


# ---------------------------------------------------------------------------
# The derived shape
# ---------------------------------------------------------------------------
@dataclass
class Step:
    """One thing the rule does, and the lines it does it on."""

    n: int
    kind: str
    text: str
    lines: tuple[int, int]
    explainable: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "kind": self.kind,
            "text": self.text,
            "lines": list(self.lines),
            "explainable": self.explainable,
        }


@dataclass
class Outline:
    """Everything that is true about a rule by inspection of its source."""

    title: str = ""
    purpose: str = ""
    tables: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    limits: list[dict[str, Any]] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    cap: int | None = None
    breach: str | None = None
    numbers: list[float] = field(default_factory=list)
    names: list[str] = field(default_factory=list)

    @property
    def cannot_explain(self) -> list[dict[str, Any]]:
        return [
            {"n": s.n, "reason": "this step could not be described automatically"}
            for s in self.steps
            if not s.explainable
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "purpose": self.purpose,
            "tables": self.tables,
            "steps": [s.as_dict() for s in self.steps],
            "limits": self.limits,
            "outputs": self.outputs,
            "cap": self.cap,
            "breach": self.breach,
            "cannot_explain": self.cannot_explain,
        }


# ---------------------------------------------------------------------------
# Reading a rule
# ---------------------------------------------------------------------------
def source_hash(source: str) -> str:
    """The identity of the text a summary was written about.

    Stored beside every generated summary, so a description that has outlived
    its code is recognised and withheld. A stale sentence about a control is
    worse than no sentence.
    """
    return hashlib.sha256((source or "").encode("utf-8")).hexdigest()


def humanise(identifier: str) -> str:
    """`is_void` -> `is void`, `employmentStatus` -> `employment status`.

    Column names are the one technical word a reader still meets, and an
    underscore is what makes them read as code rather than English.
    """
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(identifier))
    return text.replace("_", " ").replace("-", " ").strip().lower()


def _module_constants(tree: ast.Module) -> dict[str, Any]:
    """Module-level constants whose value is a literal.

    The numbers a control is tuned with, and the only numbers a summary may
    state. Parsed, never executed: a rule is text until the runner imports it.
    """
    found: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError, SyntaxError):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                found[target.id] = value
    return found


def _numeric_aliases(tree: ast.Module, constants: dict[str, Any]) -> dict[str, str]:
    """Local names that hold a tunable number, each mapped to the constant it copies.

    `ratio_limit = LAST_HOUR_RATIO_LIMIT` at the top of run() is the usual
    shape, and a scan that only looked at module constants would miss every
    threshold in the estate - which is exactly what a first draft of this did.
    """
    aliases: dict[str, str] = {
        name: name for name, value in constants.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Name):
                continue
            origin = aliases.get(node.value.id)
            if origin is None:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id not in aliases:
                    aliases[target.id] = origin
                    changed = True
    return aliases


def _docstring(node: ast.AST) -> str:
    text = ast.get_docstring(node) or ""
    return text.strip()


def _first_line(text: str) -> str:
    """The opening sentence of a docstring, as a usable clause."""
    paragraph = text.strip().split("\n\n")[0].strip()
    first = re.split(r"(?<=[.!?])\s+", paragraph.replace("\n", " "))[0].strip()
    return first


def _purpose(text: str) -> str:
    """The rule's own opening sentence, without the code in front of it.

    Every demo rule opens "LAD-CA-03  -  Last hour of trading value spike".
    The code is already the page title, so repeating it says nothing twice.
    """
    first = _first_line(text)
    return re.sub(r"^[A-Z]{2,6}(-[A-Z0-9]{1,6})+\s*[-\u2013\u2014]\s*", "", first).strip()


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        return getattr(node.func, "attr", None) or getattr(node.func, "id", None) or ""
    return ""


def _unwrap(node: ast.AST) -> ast.AST:
    """Strip the wrappers a chain is dressed in: float(...), x["c"], x[:200]."""
    cursor = node
    for _ in range(8):
        if (
            isinstance(cursor, ast.Call)
            and isinstance(cursor.func, ast.Name)
            and cursor.func.id in WRAPPERS
            and cursor.args
        ):
            cursor = cursor.args[0]
        elif isinstance(cursor, ast.Subscript):
            cursor = cursor.value
        else:
            break
    return cursor


def _chain(node: ast.AST) -> tuple[ast.AST, list[ast.Call]]:
    """Split `a.b(x).c(y)` into the thing it starts from and the calls on it."""
    calls: list[ast.Call] = []
    cursor = node
    while isinstance(cursor, ast.Call) and isinstance(cursor.func, ast.Attribute):
        # ctx.table("x") is the base of a chain, not a call on top of one.
        if _call_name(cursor) in {"table", "count_rows"}:
            break
        calls.append(cursor)
        cursor = cursor.func.value
    return cursor, list(reversed(calls))


def _span(node: ast.AST) -> tuple[int, int]:
    first = min(
        (getattr(n, "lineno", 10**6) for n in ast.walk(node)),
        default=getattr(node, "lineno", 1),
    )
    last = max(
        (getattr(n, "end_lineno", 0) or getattr(n, "lineno", 1) for n in ast.walk(node)),
        default=getattr(node, "lineno", 1),
    )
    return first, last


# ---------------------------------------------------------------------------
# Predicates -> English
# ---------------------------------------------------------------------------
def _literal_phrase(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value
        if isinstance(node.value, bool):
            return "yes" if node.value else "no"
        if node.value is None:
            return "nothing"
        if isinstance(node.value, (int, float)):
            return f"{node.value:g}"
    if isinstance(node, (ast.List, ast.Tuple)):
        parts = [_literal_phrase(e) for e in node.elts]
        if parts and all(p is not None for p in parts):
            return " or ".join(parts)  # type: ignore[arg-type]
    return None


def _column(call: ast.Call) -> str:
    if call.args:
        literal = _literal_phrase(call.args[0])
        if literal:
            return humanise(literal)
    return "value"


def _column_of(node: ast.AST) -> str:
    """The column an expression is about, found wherever it sits in a chain.

    `pl.col("item_group").is_in([...])` has two calls on top of the column, so
    reading the outermost one and calling it "value" loses the only word the
    reader needs.
    """
    for inner in ast.walk(node):
        if isinstance(inner, ast.Call) and _call_name(inner) == "col":
            return _column(inner)
    return "value"


def _aliases_of(node: ast.AST) -> list[str]:
    """Names an expression gives its results: `.alias("closing_ts")`."""
    return [
        humanise(_literal_phrase(inner.args[0]) or "")
        for inner in ast.walk(node)
        if isinstance(inner, ast.Call) and _call_name(inner) == "alias" and inner.args
    ]


def _list(items: list[str]) -> str:
    """`a, b and c` - the way a person lists things."""
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f" and {items[-1]}"


def _value_phrase(node: ast.AST, constants: dict[str, Any]) -> str:
    """A right-hand side, said the way a person would say it."""
    if isinstance(node, ast.Compare):
        return _predicate(node, constants) or "a condition"

    literal = _literal_phrase(node)
    if literal is not None:
        return literal

    if isinstance(node, ast.Name):
        if node.id in constants:
            value = constants[node.id]
            if isinstance(value, (int, float, str)):
                return f"{value:g}" if isinstance(value, (int, float)) else str(value)
        return humanise(node.id)

    if isinstance(node, ast.Call) and _call_name(node) == "col":
        return _column(node)

    if isinstance(node, ast.Attribute):
        return humanise(node.attr)

    if isinstance(node, ast.Subscript):
        return humanise(_literal_phrase(node.slice) or "value")

    if isinstance(node, ast.Call):
        base, methods = _chain(node)
        names = [_call_name(m) for m in methods]
        if _call_name(base) == "col" or isinstance(base, ast.Name) and base.id == "pl":
            column = _column(base) if _call_name(base) == "col" else "value"
            if "mean" in names:
                return f"the average {column}"
            if "sum" in names:
                return f"the total {column}"
            if "hour" in names:
                return f"the hour of {column}"
            if "len" in names or "count" in names:
                return "the number of rows"
        if _call_name(node) == "len":
            return "the number of rows"
        return humanise(_call_name(node) or "value")

    if isinstance(node, ast.BinOp):
        left = _value_phrase(node.left, constants)
        right = _value_phrase(node.right, constants)
        op = {
            ast.Div: "divided by", ast.Mult: "times",
            ast.Sub: "less", ast.Add: "plus",
        }.get(type(node.op), "combined with")
        return f"{left} {op} {right}"

    return "a value"


def _is_place(node: ast.AST, constants: dict[str, Any]) -> bool:
    """Something a row carries - a column, or a name holding one.

    Not a literal and not a module constant. "the status is the same as the
    exited" is what a value mistaken for a place sounds like.
    """
    if isinstance(node, ast.Name):
        return node.id not in constants
    if isinstance(node, (ast.Subscript, ast.Attribute)):
        return True
    return isinstance(node, ast.Call) and _call_name(node) == "col"


def _predicate(node: ast.AST, constants: dict[str, Any]) -> str | None:
    """Translate a filter or test, or None when it is beyond this pass.

    None is a real answer: the caller turns it into an honest "narrows the rows
    here" rather than a sentence invented to fill the space.
    """
    # `and`/`or` in a filter, and polars' `&`/`|`, which parse as arithmetic.
    if isinstance(node, (ast.BoolOp, ast.BinOp)):
        if isinstance(node, ast.BoolOp):
            parts = [_predicate(v, constants) for v in node.values]
            joiner = " and " if isinstance(node.op, ast.And) else " or "
        elif isinstance(node.op, (ast.BitAnd, ast.BitOr)):
            parts = [_predicate(node.left, constants), _predicate(node.right, constants)]
            joiner = " and " if isinstance(node.op, ast.BitAnd) else " or "
        else:
            return None
        if not all(parts):
            return None
        return joiner.join(parts)  # type: ignore[arg-type]

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.Invert)):
        inner = _predicate(node.operand, constants)
        return _negate(inner) if inner else None

    # A bare call used as a flag: pl.col("x").is_in([...]), pl.col("x").is_null()
    if isinstance(node, ast.Call):
        name = _call_name(node)
        if name in {"is_in", "is_not_in"}:
            column = _column_of(node)
            values = _values_phrase(node.args[0], constants) if node.args else None
            if values is None:
                return None
            verb = "is one of" if name == "is_in" else "is none of"
            return f"the {column} {verb} {values}"
        if name in {"is_null", "is_not_null"}:
            column = _column_of(node)
            verb = "is blank" if name == "is_null" else "is filled in"
            return f"the {column} {verb}"

    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        left, right = node.left, node.comparators[0]

        for side, other in ((left, right), (right, left)):
            if isinstance(side, ast.Call) and _call_name(side) in {"is_in", "is_not_in"}:
                column = _column_of(side)
                values = _values_phrase(side.args[0], constants) if side.args else None
                if values is None:
                    return None
                verb = "is one of" if _call_name(side) == "is_in" else "is none of"
                return f"the {column} {verb} {values}"

        verb = COMPARISONS.get(type(node.ops[0]))
        if verb:
            subject = _phrase_subject(left, constants)
            value = _value_phrase(right, constants)

            # A flag column compared to yes/no: `is_void == False` reads as "the
            # row is void", which is what the column name already says.
            flag = subject[4:] if subject.startswith("the ") else subject
            if flag.startswith("is ") and value in {"yes", "no"}:
                return f"the row {flag}" if value == "yes" else f"the row is not {flag[3:]}"

            if subject == value:
                # `pl.col("staff_id") == top["staff_id"]` - the right side is a
                # value already picked out of an earlier step, and naming the
                # column twice says nothing. Say what it means instead.
                return f"the {subject} is the one already found"

            # Two columns compared for equality: `vendor_iban == employee_iban`.
            # "the vendor iban is employee iban" is not a sentence, and plain
            # "is" reads as a definition rather than a match.
            if isinstance(node.ops[0], ast.Eq) and _is_place(left, constants) \
                    and _is_place(right, constants):
                other = value if value.startswith(("the ", "a ", "an ")) else f"the {value}"
                return f"the {subject} is the same as {other}"
            return f"the {subject} {verb} {value}"

    # A bare column used as a flag: pl.col("is_void"), which polars treats as
    # "where this is true" but a reader needs said out loud.
    if isinstance(node, ast.Call) and _call_name(node) == "col":
        return f"the row {_column(node)}" if _column(node).startswith("is ") else f"the {_column(node)} is set"
    if isinstance(node, ast.Attribute):
        return f"the {humanise(node.attr)} is set"

    return None


def _values_phrase(node: ast.AST, constants: dict[str, Any]) -> str | None:
    """The right-hand side of `.is_in(...)`, including a constant that holds it."""
    if isinstance(node, ast.Name) and node.id in constants:
        node = ast.Constant(value=constants[node.id])
    return _literal_phrase(node)


def _negate(phrase: str) -> str:
    """Turn a predicate on its head, keeping it readable.

    `the row is void` -> `the row is not void`, not "it is not true that the
    row is void". A filter is read as a condition, and a condition with a
    negation wrapped around it is the thing that makes people give up.
    """
    swaps = (
        (" is one of ", " is none of "),
        (" is none of ", " is one of "),
        (" is not ", " is "),
        (" is ", " is not "),
    )
    for old, new in swaps:
        if old in phrase:
            return phrase.replace(old, new, 1)
    return f"it is not the case that {phrase}"


def _phrase_subject(node: ast.AST, constants: dict[str, Any]) -> str:
    """The left side of a comparison, without a doubled "the"."""
    phrase = _value_phrase(node, constants)
    if phrase.startswith("the "):
        return phrase
    if phrase.startswith(("a ", "an ")):
        return phrase
    return phrase


def _flip_comparison(node: ast.AST) -> ast.AST | None:
    """`ratio <= limit` -> `ratio > limit`, keeping the reader's point of view."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return node.operand
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        flipped = FLIPPED.get(type(node.ops[0]))
        if flipped is None:
            return None
        out = ast.Compare(
            left=node.left, ops=[flipped()], comparators=list(node.comparators)
        )
        return ast.fix_missing_locations(out)
    return None


# ---------------------------------------------------------------------------
# Chains -> steps
# ---------------------------------------------------------------------------
def _describe_chain(
    calls: list[ast.Call],
    base: ast.AST,
    constants: dict[str, Any],
    helpers: dict[str, ast.FunctionDef],
    outline: Outline,
) -> tuple[str, str, bool] | None:
    """One assignment, as (kind, sentence, explainable) - or None if it is a copy."""
    clauses: list[str] = []
    kind = "work"
    explainable = True

    base_name = _call_name(base)
    if base_name in {"table", "count_rows"} and isinstance(base, ast.Call) and base.args:
        table = _literal_phrase(base.args[0]) or "a table"
        clauses.append(f"read {table}")
        kind = "read"
    elif isinstance(base, ast.Call) and base_name in helpers:
        # A helper does the reading. Its own docstring is the best one-line
        # description available, and it was written by whoever wrote the rule.
        summary = _first_line(_docstring(helpers[base_name]))
        if summary:
            clauses.append(summary[0].lower() + summary[1:].rstrip("."))
            kind = "read"
        else:
            clauses.append("take a filtered set of rows")
            kind = "read"
    elif isinstance(base, ast.Call) and base_name in PLUMBING | WRAPPERS | {"pl"}:
        # `float(...)`, `pl.len()` - nothing to say, and saying it would be noise.
        return None
    elif not isinstance(base, ast.Name):
        explainable = False

    if not calls and kind == "work":
        return None  # a plain copy of a variable: not a step

    grouped = False
    for call in calls:
        name = _call_name(call)
        if name in PLUMBING:
            continue

        if name == "filter" and call.args:
            phrase = _predicate(call.args[0], constants)
            if phrase:
                clauses.append(f"keep only the rows where {phrase}")
                kind = "filter" if kind == "read" else kind
            else:
                clauses.append("narrow the rows")
                explainable = False
        elif name == "with_columns":
            made = [humanise(k.arg) for k in call.keywords if k.arg]
            for arg in call.args:
                made.extend(_aliases_of(arg))
            if made:
                clauses.append("work out " + _list(made))
            else:
                explainable = False
        elif name == "group_by":
            keys = [
                humanise(_literal_phrase(a) or "a key") for a in call.args
            ] or [humanise(k.arg or "") for k in call.keywords if k.arg]
            clauses.append("group the rows by " + _list(keys))
            grouped = True
        elif name == "agg":
            totals = [humanise(k.arg) for k in call.keywords if k.arg]
            for arg in call.args:
                totals.extend(_aliases_of(arg))
            clauses.append(
                "add up " + _list(totals) if totals else "summarise each group"
            )
            grouped = True
            kind = "aggregate"
        elif name == "sort":
            column = _literal_phrase(call.args[0]) if call.args else None
            descending = any(
                k.arg == "descending" and isinstance(k.value, ast.Constant) and k.value.value
                for k in call.keywords
            )
            if column:
                clauses.append(
                    f"put the largest {humanise(column)} first" if descending
                    else f"sort by {humanise(column)}"
                )
        elif name == "head" and call.args:
            n = _literal_phrase(call.args[0])
            if n and n.isdigit():
                outline.cap = int(n)
                clauses.append(f"keep at most {n} rows")
            else:
                clauses.append("keep only the first few rows")
        elif name == "select":
            columns: list[str] = []
            for arg in call.args:
                literal = _literal_phrase(arg)
                if literal:
                    columns.extend(
                        humanise(c) for c in re.split(r"[,\s]+", literal) if c
                    )
                else:
                    # `cond=pl.col("x")` in a select is a computed column; its
                    # name is still worth saying, and it is not a failure.
                    columns.extend(_aliases_of(arg))
            if columns:
                outline.outputs = columns
                clauses.append("keep the columns a reviewer needs")
            else:
                explainable = False
        elif name == "join":
            # `", ".join(...)` is string assembly, not a data join. A real one
            # always names the rows it matches on.
            keys = {k.arg for k in call.keywords}
            if not keys & {"how", "on", "left_on", "right_on"}:
                continue
            how = next(
                (
                    k.value.value for k in call.keywords
                    if k.arg == "how" and isinstance(k.value, ast.Constant)
                ),
                "inner",
            )
            other = humanise(call.args[0].id) if call.args and isinstance(call.args[0], ast.Name) else "the other table"
            phrases = {
                "cross": f"mix every row with every row of {other}",
                "inner": f"match rows against {other}",
                "left": f"attach matching rows from {other}",
            }
            clauses.append(phrases.get(str(how), f"combine with {other}"))
            kind = "join"
        elif name == "row" and call.args:
            literal = _literal_phrase(call.args[0])
            clauses.append(
                "take the top row" if literal == "0" else "take one row"
            )
        else:
            clauses.append("do something more here")
            explainable = False

    if grouped and kind == "work":
        kind = "aggregate"

    if not clauses:
        return None

    return kind, _sentence(clauses), explainable


def _sentence(clauses: list[str]) -> str:
    """Join clauses into sentences a reader can hold in their head.

    One long imperative chain ("read the sales, work out the hour, keep the
    cash rows, group them by staff, add up the value, keep the leaders") is
    accurate and unreadable. Splitting at a clause boundary costs nothing and
    keeps every sentence inside the length the readability gate allows - which
    matters because the derived wording is the fallback the gates are measured
    against.
    """
    sentences: list[list[str]] = []
    current: list[str] = []
    words = 0
    for clause in clauses:
        length = len(clause.split())
        if current and words + length > 16:
            sentences.append(current)
            current, words = [], 0
        current.append(clause)
        words += length
    if current:
        sentences.append(current)

    out: list[str] = []
    for parts in sentences:
        text = ", ".join(parts)
        out.append(text[:1].upper() + text[1:] + ".")
    return " ".join(out)


def _describe_loop(node: ast.For, constants: dict[str, Any]) -> tuple[str, bool]:
    """A Python loop. Coarse on purpose - but it names what it compares."""
    compared: set[str] = set()
    for compare in (n for n in ast.walk(node) if isinstance(n, ast.Compare)):
        for side in [compare.left, *compare.comparators]:
            if isinstance(side, ast.Call) and _call_name(side) == "col":
                compared.add(_column(side))
            elif isinstance(side, ast.Subscript):
                key = _literal_phrase(side.slice)
                if key:
                    compared.add(humanise(key))

    if compared:
        return (
            "Go through the combined rows one at a time, comparing "
            + " with ".join(sorted(compared))
            + ".",
            True,
        )
    return "Go through the rows one at a time, deciding what to keep.", False


def _return_kind(node: ast.Return, lines: list[str]) -> tuple[str, bool]:
    """What happens at the end: a report, or a refusal to report."""
    segment = "\n".join(lines[node.lineno - 1: (node.end_lineno or node.lineno)])
    if '"failed"' in segment or "'failed'" in segment:
        return (
            "If that cannot be done, stop and say the data was not available, "
            "rather than reporting a clean day.",
            True,
        )
    if re.search(r'"rows":\s*\[\]', segment) or re.search(r"'rows':\s*\[\]", segment):
        return "Report the day as clean.", True
    return "Report what was found.", True


# ---------------------------------------------------------------------------
# derive()
# ---------------------------------------------------------------------------
def derive(source: str) -> Outline:
    """Everything that is true about this rule, from its text alone."""
    outline = Outline()
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return outline

    constants = _module_constants(tree)
    aliases = _numeric_aliases(tree, constants)
    numeric = set(aliases)
    helpers = {
        n.name: n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name != "run"
    }

    # A local name holding a tunable number is said as the number it holds
    # (`ratio_limit` -> "2"): the value is what a reader needs, and the
    # identifier is what made the page unreadable in the first place.
    values = dict(constants)
    values.update({name: constants[origin] for name, origin in aliases.items()})

    # The tables come from the one derivation that already answers this
    # question, so the page and the catalogue cannot disagree about what the
    # rule reads. Imported here rather than at module level because validation
    # imports nothing from this file - one direction only.
    from app.engine.validation import declared_tables

    outline.tables = declared_tables(source)
    outline.title = str(constants.get("NAME") or "")
    outline.purpose = (
        _purpose(_docstring(tree)) or str(constants.get("DESCRIPTION") or "")
    )
    outline.names = sorted(
        {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        | set(constants)
        | set(helpers)
    )

    lines = (source or "").splitlines()
    run = next(
        (
            n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "run"
        ),
        None,
    )
    if run is None:
        return outline

    decision = _find_decision(run, numeric)
    counter = 0

    def add(kind: str, text: str, node: ast.AST, explainable: bool = True) -> None:
        nonlocal counter
        counter += 1
        outline.steps.append(Step(counter, kind, text, _span(node), explainable))

    for statement in run.body:
        if isinstance(statement, ast.Assign):
            target = statement.targets[0]
            base, calls = _chain(_unwrap(statement.value))

            # `rows = rows[:200]` is a cap, not a copy.
            if isinstance(statement.value, ast.Subscript) and isinstance(
                statement.value.slice, ast.Slice
            ):
                upper = statement.value.slice.upper
                if isinstance(upper, ast.Constant) and isinstance(upper.value, int):
                    outline.cap = max(outline.cap or 0, upper.value)
                    add("report", f"Keep at most {upper.value} rows.", statement)
                continue

            described = _describe_chain(calls, base, values, helpers, outline)
            if described:
                kind, sentence, explainable = described
                add(kind, sentence, statement, explainable)
            elif isinstance(target, ast.Name) and isinstance(statement.value, (ast.List, ast.Dict)):
                continue  # `rows = []` - the accumulator, not a step
        elif isinstance(statement, ast.AugAssign):
            continue
        elif isinstance(statement, ast.If):
            if statement is decision and decision is not None:
                phrase = _decision_phrase(statement, values)
                if phrase:
                    outline.breach = _breach_sentence(phrase)
                    add("test", f"Flag the day when {phrase}.", statement)
                    continue
            add("branch", _branch_text(statement, lines), statement)
        elif isinstance(statement, ast.For):
            text, explainable = _describe_loop(statement, values)
            add("compare", text, statement, explainable)
        elif isinstance(statement, ast.Return):
            text, explainable = _return_kind(statement, lines)
            add("report", text, statement, explainable)

    # A decision nested inside a loop (`if score < minimum: continue`) is still
    # the condition that makes a finding, so it is stated even though it was not
    # one of run()'s own statements.
    if outline.breach is None and decision is not None:
        outline.breach = _breach_sentence(_decision_phrase(decision, values))
    if outline.breach is None:
        outline.breach = _breach_sentence(_threshold_filter(run, values))
    if outline.breach is None:
        outline.breach = _breach_sentence(_guard_phrase(run, values))

    _collect_limits(tree, constants, numeric, outline)
    _collect_numbers(run, aliases, outline)
    return outline


def _branch_text(statement: ast.If, lines: list[str]) -> str:
    segment = "\n".join(lines[statement.lineno - 1: (statement.end_lineno or statement.lineno)])
    if '"failed"' in segment or "'failed'" in segment:
        return "Stop if the data is not there, rather than reporting a clean day."
    if re.search(r'"rows":\s*\[\]', segment) or re.search(r"'rows':\s*\[\]", segment):
        return "Stop and report the day as clean when there is nothing to check."
    return "Stop here when there is nothing left to check."


def _find_decision(run: ast.FunctionDef, numeric: set[str]) -> ast.If | None:
    """The `if` that decides whether the control raises anything.

    Deliberately narrow: a comparison whose one side is a name holding a
    tunable number. That is what a threshold looks like, and a threshold is the
    one thing a head of audit wants stated before anything else.
    """
    for node in ast.walk(run):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        for side in [node.test.left, *node.test.comparators]:
            if any(
                isinstance(n, ast.Name) and n.id in numeric for n in ast.walk(side)
            ):
                return node
    return None


def _decision_phrase(statement: ast.If, values: dict[str, Any]) -> str | None:
    """The condition that makes a finding, said positively.

    `if ratio <= limit: return clean` means a finding happens when the ratio is
    *above* the limit, so the sentence is the complement. Saying the code's own
    inequality would describe the quiet path and leave the breach unexplained.
    """
    node = statement.test
    inverted = _flip_comparison(node)
    if inverted is not None:
        phrase = _predicate(inverted, values)
        if phrase:
            return phrase
    return _predicate(node, values)


def _threshold_filter(run: ast.FunctionDef, values: dict[str, Any]) -> str | None:
    """The row filter that keeps the breaches, when no `if` states the verdict.

    A rule that returns whatever survives `.filter(share > 50)` has said what a
    breach is - it said it in the chain rather than in a sentence.
    """
    for node in ast.walk(run):
        if not isinstance(node, ast.Call) or _call_name(node) != "filter":
            continue
        if not node.args:
            continue

        thresholds = [
            name.id for name in ast.walk(node.args[0])
            if isinstance(name, ast.Name)
            and isinstance(values.get(name.id), (int, float))
        ]
        if not thresholds:
            continue

        # A filter often carries several conditions - "not void AND the exit
        # date is known AND days since exit is positive". Only the parts that
        # mention a limit are the breach condition; the rest is housekeeping.
        subject = node.args[0]
        if isinstance(subject, (ast.BoolOp, ast.BinOp)):
            parts = (
                list(subject.values) if isinstance(subject, ast.BoolOp)
                else [subject.left, subject.right]
            )
            operands = [
                part for part in parts
                if any(
                    isinstance(name, ast.Name) and name.id in thresholds
                    for name in ast.walk(part)
                )
            ]
            if operands:
                phrase = _combine(operands, " or " if _is_or(subject) else " and ", values)
                if phrase:
                    return phrase

        phrase = _predicate(subject, values)
        if phrase:
            return phrase
    return None


def _guard_phrase(run: ast.FunctionDef, values: dict[str, Any]) -> str | None:
    """The condition stated by the guard in front of the accumulator.

    A control that is an exact match has no tunable number to hang the verdict
    on. `if the keys differ: continue` is the whole of it, and the complement of
    that guard is the sentence - so the guard is read the same way a threshold
    is, with `_decision_phrase` doing the turning.

    Tried last: a number the author chose says what a breach is more directly
    than a guard that skips.
    """
    for loop in (n for n in ast.walk(run) if isinstance(n, (ast.For, ast.While))):
        appends = any(
            isinstance(c, ast.Call) and _call_name(c) == "append"
            for c in ast.walk(loop)
        )
        if not appends:
            continue
        for statement in loop.body:
            if not isinstance(statement, ast.If) or not statement.body:
                continue
            if not isinstance(statement.body[-1], (ast.Continue, ast.Return)):
                continue
            phrase = _decision_phrase(statement, values)
            if phrase:
                return phrase
    return None


def _is_or(node: ast.AST) -> bool:
    op = getattr(node, "op", None)
    return isinstance(op, (ast.Or, ast.BitOr))


def _combine(parts: list[ast.AST], joiner: str, values: dict[str, Any]) -> str | None:
    phrases = [_predicate(part, values) for part in parts]
    if not all(phrases):
        return None
    return joiner.join(phrases)  # type: ignore[arg-type]


def _breach_sentence(phrase: str | None) -> str | None:
    if not phrase:
        return None
    text = phrase.strip().rstrip(".")
    return f"The day is flagged when {text}."


def _collect_limits(
    tree: ast.Module, constants: dict[str, Any], numeric: set[str], outline: Outline
) -> None:
    """The tunable numbers, with the name each is declared under.

    The label is the human number; the constant is the caption beside it. An
    auditor reading "2.0" does not need the identifier, and the person who
    changes it does.
    """
    used = {
        node.id for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id in constants
    }
    for name in sorted(used):
        value = constants.get(name)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        outline.limits.append({"label": f"{value:g}", "constant": name, "value": value})


def _collect_numbers(run: ast.FunctionDef, aliases: dict[str, str], outline: Outline) -> None:
    """Every number the prose may legitimately state.

    Comparison literals and row caps, plus the constants behind the named
    thresholds - the numbers that *decide* something. Collecting every literal
    in the file would let a summary claim any number it liked, which is the
    failure this list exists to catch.
    """
    found: set[float] = set()
    constants = {l["constant"]: l["value"] for l in outline.limits}
    if outline.cap:
        found.add(float(outline.cap))
    for node in ast.walk(run):
        if isinstance(node, ast.Compare):
            for side in [node.left, *node.comparators]:
                if isinstance(side, ast.Constant) and isinstance(side.value, (int, float)):
                    found.add(float(side.value))
                elif isinstance(side, ast.Name):
                    origin = aliases.get(side.id)
                    if origin in constants:
                        found.add(float(constants[origin]))
        elif isinstance(node, ast.Call) and _call_name(node) == "head" and node.args:
            literal = _literal_phrase(node.args[0])
            if literal and literal.isdigit():
                found.add(float(literal))
    outline.numbers = sorted(found)


# ---------------------------------------------------------------------------
# Gates - the difference between a summary and a plausible summary
# ---------------------------------------------------------------------------
@dataclass
class Gate:
    name: str
    ok: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


def _numerals(text: str) -> set[float]:
    found = {float(m) for m in re.findall(r"\d+(?:\.\d+)?", text or "")}
    lowered = (text or "").lower()
    for word, value in NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", lowered):
            found.add(value)
    return found


def _prose_text(prose: dict[str, Any]) -> str:
    return " ".join(
        [str(prose.get("breach_condition") or "")]
        + [str(s.get("text") or "") for s in prose.get("steps") or []]
    )


def check_numbers(outline: Outline, prose: dict[str, Any]) -> Gate:
    """Every number in the words must be a number in the code.

    The failure this catches is the one an auditor would never forgive: a
    summary saying "more than three times the average" about a limit of 2.0.
    It reads perfectly, and it is wrong.
    """
    # Step numbers are ordinals, not claims, so they are not checked as values.
    allowed = set(outline.numbers)
    stray = sorted(n for n in _numerals(_prose_text(prose)) if n not in allowed)
    return Gate(
        "numbers",
        not stray,
        "numbers that are not in the code: " + ", ".join(f"{n:g}" for n in stray)
        if stray else "",
    )


def check_identifiers(outline: Outline, prose: dict[str, Any]) -> Gate:
    """Any name the summary puts in backticks has to exist in the file."""
    quoted = set(re.findall(r"`([^`]+)`", _prose_text(prose)))
    stray = sorted(q for q in quoted if q not in set(outline.names))
    return Gate(
        "identifiers",
        not stray,
        "names that are not in the file: " + ", ".join(stray) if stray else "",
    )


def check_coverage(outline: Outline, prose: dict[str, Any]) -> Gate:
    """One sentence per derived step. No additions, no omissions.

    This is the lever that keeps a model from writing a seventh step about
    something the rule does not do: the steps come from the derivation, and the
    model only fills in words.
    """
    derived = {s.n for s in outline.steps}
    given = {int(s.get("n", 0)) for s in prose.get("steps") or []}
    detail = []
    missing = sorted(derived - given)
    extra = sorted(given - derived)
    if missing:
        detail.append("not described: " + ", ".join(str(n) for n in missing))
    if extra:
        detail.append("described but not in the code: " + ", ".join(str(n) for n in extra))
    return Gate("coverage", not detail, "; ".join(detail))


def check_anchors(outline: Outline, prose: dict[str, Any], line_count: int) -> Gate:
    """Every line range points into the file being described."""
    bad = []
    for step in prose.get("steps") or []:
        span = step.get("lines") or []
        if len(span) != 2 or not all(isinstance(v, int) for v in span):
            bad.append(f"step {step.get('n')}: no line range")
            continue
        first, last = span
        if not 1 <= first <= last <= line_count:
            bad.append(f"step {step.get('n')}: lines {first}-{last} of {line_count}")
    return Gate("anchors", not bad, "; ".join(bad))


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def _syllables(word: str) -> int:
    word = re.sub(r"[^a-z]", "", word.lower())
    if not word:
        return 0
    if len(word) <= 3:
        return 1
    count = len(re.findall(r"[aeiouy]+", word))
    if word.endswith("e") and not word.endswith(("le", "ee")):
        count -= 1
    return max(1, count)


def reading_grade(text: str) -> float:
    """Flesch-Kincaid grade. Lower secondary is the bar, so 9 is the ceiling."""
    sentences = _sentences(text) or [text]
    words = re.findall(r"[A-Za-z']+", text)
    if not words:
        return 0.0
    syllables = sum(_syllables(w) for w in words)
    return round(
        0.39 * (len(words) / len(sentences))
        + 11.8 * (syllables / len(words))
        - 15.59,
        1,
    )


def check_readability(prose: dict[str, Any]) -> Gate:
    """Short sentences, ordinary words, and none of the words that started this.

    Not taste: WCAG 3.1.5 asks for a version written to lower-secondary reading
    age, and this is that version's contract.
    """
    text = _prose_text(prose)
    problems: list[str] = []
    grade = reading_grade(text)
    if grade > 9:
        problems.append(f"reading grade {grade}")
    long_sentences = [s for s in _sentences(text) if len(re.findall(r"[A-Za-z']+", s)) > 20]
    if long_sentences:
        problems.append(f"{len(long_sentences)} sentences over 20 words")
    lowered = text.lower()
    jargon = [word for word in JARGON if word in lowered]
    if jargon:
        problems.append("jargon: " + ", ".join(jargon))
    return Gate("readability", not problems, "; ".join(problems))


def check(outline: Outline, prose: dict[str, Any], line_count: int) -> list[Gate]:
    """Every gate, in one call. All must pass before the prose is shown."""
    return [
        check_coverage(outline, prose),
        check_numbers(outline, prose),
        check_identifiers(outline, prose),
        check_anchors(outline, prose, line_count),
        check_readability(prose),
    ]


def passed(gates: list[Gate]) -> bool:
    return all(g.ok for g in gates)


def derived_prose(outline: Outline) -> dict[str, Any]:
    """The summary this module can make on its own.

    It is what the page shows when no model is configured, when the model
    fails, or when its words fail the gates. Plain, and true.
    """
    return {
        "breach_condition": outline.breach or outline.purpose or "",
        "steps": [
            {"n": s.n, "text": s.text, "lines": list(s.lines)}
            for s in outline.steps
        ],
        "limits": outline.limits,
        "cannot_explain": outline.cannot_explain,
        "outputs": outline.outputs,
        "cap": outline.cap,
    }
