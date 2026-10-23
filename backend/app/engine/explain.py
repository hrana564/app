from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

# A number written in the source, with or without a decimal point.
_NUMERAL = re.compile(r"\d+(?:\.\d+)?")

# Number words, so "more than twice" is checked against 2.0 like "more than
# 2" is. Only the ones that state a quantity rather than count something:
# "one" and "two" are far too common in prose to be evidence of a claim.
NUMBER_WORDS = {
    "twice": 2.0, "thrice": 3.0, "half": 0.5, "double": 2.0,
    "three": 3.0, "four": 4.0, "five": 5.0,
    "six": 6.0, "seven": 7.0, "eight": 8.0, "nine": 9.0, "ten": 10.0,
}

# Words that describe plumbing rather than a control. The readability gate
# refuses them - they are what made the page unreadable in the first place.
JARGON = (
    "dataframe", "data frame", "boolean", "schema", "json", "api",
    "polars", "python", "regex", "tuple", "dict", "lambda", "null value",
    "sql", "function", "method", "loop over", "iterate",
)


# ---------------------------------------------------------------------------
# The derived shape
# ---------------------------------------------------------------------------
@dataclass
class Step:
    n: int
    lines: tuple[int, int]


@dataclass
class Outline:
    title: str = ""
    purpose: str = ""
    tables: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    limits: list[dict[str, Any]] = field(default_factory=list)
    numbers: list[float] = field(default_factory=list)
    names: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Reading a rule
# ---------------------------------------------------------------------------
def source_hash(source: str) -> str:
    return hashlib.sha256((source or "").encode("utf-8")).hexdigest()


def _run_function(tree: ast.Module) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    return next(
        (
            n
            for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "run"
        ),
        None,
    )


def _constant(tree: ast.Module, name: str) -> str:
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
    return ""


def _contains_call(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Call) for n in ast.walk(node))


def _is_step(statement: ast.stmt) -> bool:
    if isinstance(statement, (ast.AugAssign, ast.Pass, ast.Expr)):
        return False
    if isinstance(statement, ast.Assign):
        value = statement.value
        if isinstance(value, (ast.List, ast.Dict, ast.Tuple, ast.Set)):
            return False
        if not _contains_call(value):
            return False
    return True


def _steps(tree: ast.Module) -> list[Step]:
    run = _run_function(tree)
    if run is None:
        return []
    steps: list[Step] = []
    for statement in run.body:
        if not _is_step(statement):
            continue
        start = getattr(statement, "lineno", 0)
        end = getattr(statement, "end_lineno", None) or start
        steps.append(Step(n=len(steps) + 1, lines=(start, end)))
    return steps


def _limits(tree: ast.Module) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not (
            isinstance(value, ast.Constant)
            and isinstance(value.value, (int, float))
            and not isinstance(value.value, bool)
        ):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                found.append({"constant": target.id, "value": float(value.value)})
    return found


def _numbers(tree: ast.Module, limits: list[dict[str, Any]]) -> set[float]:
    found = {float(limit["value"]) for limit in limits}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for operand in [node.left, *node.comparators]:
            if (
                isinstance(operand, ast.Constant)
                and isinstance(operand.value, (int, float))
                and not isinstance(operand.value, bool)
            ):
                found.add(float(operand.value))
    return found


def _names(tree: ast.Module) -> list[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.add(node.name)
    return sorted(found)


def derive(source: str) -> Outline:
    outline = Outline()
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return outline

    # The tables come from the one derivation that already answers this
    # question, so the page and the catalogue cannot disagree about what the
    # rule reads. Imported here rather than at module level because
    # validation imports nothing from this file - one direction only.
    from app.engine.validation import declared_tables

    outline.tables = declared_tables(source or "")
    outline.title = _constant(tree, "NAME")
    outline.purpose = _constant(tree, "DESCRIPTION")
    outline.limits = _limits(tree)
    outline.numbers = sorted(_numbers(tree, outline.limits))
    outline.names = _names(tree)
    outline.steps = _steps(tree)
    return outline


# ---------------------------------------------------------------------------
# Gates - the difference between a summary and a plausible summary
# ---------------------------------------------------------------------------
@dataclass
class Gate:
    name: str
    ok: bool
    detail: str = ""
    blocking: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "blocking": self.blocking,
        }


def _numerals(text: str) -> set[float]:
    found = {float(m) for m in _NUMERAL.findall(text or "")}
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


def check_coverage(outline: Outline, prose: dict[str, Any]) -> Gate:
    derived = {s.n for s in outline.steps}
    given = {int(s.get("n", 0)) for s in prose.get("steps") or []}
    detail = []
    missing = sorted(derived - given)
    extra = sorted(given - derived)
    if missing:
        detail.append("not described: " + ", ".join(f"{n:g}" for n in missing))
    if extra:
        detail.append("described but not in the code: " + ", ".join(f"{n:g}" for n in extra))
    return Gate("coverage", not detail, "; ".join(detail))


def check_numbers(outline: Outline, prose: dict[str, Any]) -> Gate:
    allowed = set(outline.numbers)
    stray = sorted(n for n in _numerals(_prose_text(prose)) if n not in allowed)
    return Gate(
        "numbers",
        not stray,
        "numbers that are not in the code: " + ", ".join(f"{n:g}" for n in stray)
        if stray else "",
    )


def check_identifiers(outline: Outline, prose: dict[str, Any]) -> Gate:
    quoted = set(re.findall(r"`([^`]+)`", _prose_text(prose)))
    stray = sorted(q for q in quoted if q not in set(outline.names))
    return Gate(
        "identifiers",
        not stray,
        "names that are not in the file: " + ", ".join(stray) if stray else "",
    )


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
    text = _prose_text(prose)
    problems: list[str] = []
    grade = reading_grade(text)
    if grade > 9:
        problems.append(f"reading grade {grade}")
    long_sentences = [
        s for s in _sentences(text) if len(re.findall(r"[A-Za-z']+", s)) > 20
    ]
    if long_sentences:
        problems.append(
            "sentences over 20 words: "
            + "; ".join(f'"{s}"' for s in long_sentences[:3])
        )
    lowered = text.lower()
    jargon = [word for word in JARGON if word in lowered]
    if jargon:
        problems.append("jargon: " + ", ".join(jargon))
    return Gate(
        "readability", not problems, "; ".join(problems), blocking=False
    )


def check(outline: Outline, prose: dict[str, Any], line_count: int) -> list[Gate]:
    return [
        check_coverage(outline, prose),
        check_numbers(outline, prose),
        check_identifiers(outline, prose),
        check_readability(prose),
    ]


def passed(gates: list[Gate]) -> bool:
    return all(g.ok for g in gates if g.blocking)
