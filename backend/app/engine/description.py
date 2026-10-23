from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.engine.explain import Gate, check, derive, passed, source_hash
from app.engine.llm import complete, is_configured
from app.models import Rule, RuleExplanation
from app.schemas import ExplanationDraft, ExplanationStepDraft

log = logging.getLogger(__name__)

# Bumped whenever the prompt changes in a way that would change the words. The
# stored explanation carries it, so a page can say which instruction produced
# the sentence it is showing.
PROMPT_VERSION = "explain-2"

# How long the whole description may take, both attempts together. Separate
# from the deployment's model timeout because this call sits inside a request
# somebody is already waiting on: a hung model must not hold it open. It is a
# budget for the whole thing, not for each attempt - two attempts that each
# took the limit would double it.
BUDGET_SECONDS = 60.0

SYSTEM = """
You describe audit controls to a head of audit who does not read code and will
not learn to.

You are given:
  - a numbered list of the steps the control performs, and the lines of the
    file each step occupies;
  - the source of the control, with its line numbers on the left;
  - the numeric constants the control is tuned with.

Write plain English for each step. Rules, in order of importance:

1. One sentence per step, in the same order, keyed by the same number. Never
   add a step, merge two, drop one, or renumber. Each step's line range tells
   you which lines of the source it is - read them, and write about that.
2. State the condition that raises an exception before anything else, as
   `breach_condition`. It is the first thing a reader needs. Write it as one
   sentence beginning exactly "The control raises an exception when ", then
   the condition itself - not a description of the code, and not the quiet
   path. "The control raises an exception when the last hour's takings are
   more than twice the day's hourly average" is the shape.
3. Every number you write must be one of the constants you were given. Do not
   round it, do not convert it to a percentage or a multiple, do not invent one.
4. No code. No variable names, table names, column names, function names, or
   file names. If a step reads `ipos_sales`, say what that data is - the day's
   sales lines - not what it is called.
5. Describe what the step is FOR, not what it is written as. "Keep only the
   rows where the invoice is not void" is acceptable but dull; "ignore sales
   that were cancelled, because a cancelled sale was not settled by anybody"
   is the job.
6. One idea per sentence, 20 words at most, lower-secondary reading age.
7. Never claim a control covers something the steps do not show. If a step
   does something you cannot account for, say exactly that in plain words -
   that the check does something here that this description cannot explain.
   Do not guess.
8. Write in British English. No marketing language, no "this rule is designed
   to", no restating the step number.

Answer with JSON only:

{
  "breach_condition": "One sentence: what makes this control raise an exception.",
  "steps": [
    {"n": 1, "text": "One sentence for step 1."}
  ],
  "outputs": ["the columns a reviewer needs"]
}
""".strip()

USER = """
CONTROL
  code:        {code}
  name:        {name}
  severity:    {severity}
  the day it runs against is one data date, and it looks back {lookback} day(s)

THE CONSTANTS THIS CONTROL IS TUNED WITH
{limits}

THE STEPS IT PERFORMS
{steps}

THE SOURCE
```
{source}
```

Rewrite the steps as instructed. Answer with JSON only.
""".strip()


@dataclass
class Meta:
    code: str = ""
    name: str = ""
    severity: str = ""
    lookback_days: int = 1


@dataclass
class Judged:
    prose: dict[str, Any]
    gates: list[Gate]
    known: list[int]
    given: list[int]

    @property
    def missing(self) -> list[int]:
        return [n for n in self.known if n not in self.given]

    @property
    def unknown(self) -> list[int]:
        return [n for n in self.given if n not in self.known]


# ---------------------------------------------------------------------------
# Writing the words
# ---------------------------------------------------------------------------
def _parse(answer: str) -> dict[str, Any] | None:
    text = (answer or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


def _build_user(outline, source: str, meta: Meta) -> str:
    numbered = "\n".join(
        f"{i:>4} | {line}" for i, line in enumerate((source or "").splitlines(), 1)
    )
    steps = "\n".join(
        f"  {s.n}. lines {s.lines[0]}-{s.lines[1]}" for s in outline.steps
    ) or "  (none derived)"
    limits = "\n".join(
        f"  {limit['constant']} = {limit['value']:g}" for limit in outline.limits
    ) or "  (none declared)"
    return USER.format(
        code=meta.code,
        name=meta.name,
        severity=meta.severity,
        lookback=meta.lookback_days,
        limits=limits,
        steps=steps,
        source=numbered,
    )


def write_words(
    source: str,
    meta: Meta,
    *,
    llm: Any = None,
) -> tuple[ExplanationDraft | None, list[Gate]]:
    if llm is None and not is_configured():
        return None, []

    outline = derive(source)
    if not outline.steps:
        return None, []

    user = _build_user(outline, source, meta)
    failures: list[str] = []
    gates: list[Gate] = []
    deadline = time.monotonic() + BUDGET_SECONDS

    for attempt in (1, 2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # The budget is spent. A second attempt that cannot finish is worse
            # than no second attempt, because it holds the request open to find
            # out that it has no time.
            log.warning("explanation generation ran out of time before the retry")
            break
        prompt = user
        if failures:
            prompt = (
                f"{user}\n\nYour previous answer was rejected by these checks:\n"
                + "\n".join(f"  - {f}" for f in failures)
                + "\nFix exactly these and answer again with JSON only."
            )
        try:
            answer = (
                llm(SYSTEM, prompt) if llm is not None
                else complete(SYSTEM, prompt, timeout=remaining)
            )
        except Exception as exc:  # a provider that is down must not break the rule
            log.warning("explanation generation failed: %s", exc)
            return None, []

        parsed = _parse(answer)
        if parsed is None:
            failures = ["the answer was not JSON"]
            continue

        words = _as_draft(parsed)
        judged = accept(source, words)
        gates = judged.gates
        if passed(gates):
            return words, gates
        failures = [
            f"{g.name}: {g.detail}" for g in gates if not g.ok and g.blocking
        ]

    return None, gates


def _as_draft(parsed: dict[str, Any]) -> ExplanationDraft:
    steps: list[ExplanationStepDraft] = []
    for step in parsed.get("steps") or []:
        try:
            n = int(step.get("n", 0))
        except (TypeError, ValueError):
            continue
        steps.append(
            ExplanationStepDraft(n=n, text=str(step.get("text") or "").strip())
        )
    return ExplanationDraft(
        breach_condition=str(parsed.get("breach_condition") or "").strip(),
        steps=steps,
        outputs=[
            str(o).strip() for o in parsed.get("outputs") or [] if str(o).strip()
        ],
        origin="ai",
        model="",
        prompt_version=PROMPT_VERSION,
    )


# ---------------------------------------------------------------------------
# Judging the words
# ---------------------------------------------------------------------------
def accept(source: str, words: ExplanationDraft) -> Judged:
    outline = derive(source)
    spans = {s.n: list(s.lines) for s in outline.steps}

    given = [s.n for s in words.steps]
    steps: list[dict[str, Any]] = []
    for step in words.steps:
        if step.n not in spans:
            continue
        steps.append({"n": step.n, "text": step.text, "lines": spans[step.n]})

    prose = {
        "breach_condition": words.breach_condition,
        "steps": steps,
        "outputs": list(words.outputs or []),
    }
    gates = check(outline, prose, len(source.splitlines()))
    return Judged(
        prose=prose,
        gates=gates,
        known=[s.n for s in outline.steps],
        given=given,
    )


# ---------------------------------------------------------------------------
# Keeping the words
# ---------------------------------------------------------------------------
async def _upsert(
    session, rule: Rule, values: dict[str, Any], *, commit: bool = True
) -> RuleExplanation:
    row = (
        await session.execute(
            select(RuleExplanation).where(RuleExplanation.rule_id == rule.id)
        )
    ).scalar_one_or_none()
    if row is None:
        row = RuleExplanation(rule_id=rule.id, rule_code=rule.code, version=rule.version)
        session.add(row)
    for key, value in values.items():
        setattr(row, key, value)
    if commit:
        await session.commit()
        await session.refresh(row)
    else:
        await session.flush()
    return row


async def store(
    session,
    rule: Rule,
    source: str,
    words: ExplanationDraft,
    *,
    commit: bool = True,
) -> RuleExplanation | None:
    judged = accept(source, words)
    if not passed(judged.gates):
        return None

    values = {
        "rule_code": rule.code,
        "version": rule.version,
        "source_sha256": source_hash(source),
        "breach_condition": judged.prose["breach_condition"],
        "steps": judged.prose["steps"],
        "outputs": list(words.outputs or []),
        "origin": words.origin,
        "model": words.model,
        "prompt_version": words.prompt_version,
        "generated_by": words.generated_by,
        "gates": [g.as_dict() for g in judged.gates],
        # A description that has just been written, or just copied from
        # another version, has not been signed off - whatever the one it
        # replaces had.
        "reviewed_by": None,
        "reviewed_at": None,
    }
    return await _upsert(session, rule, values, commit=commit)


async def copy_forward(
    session, rule: Rule, source: str
) -> RuleExplanation | None:
    digest = source_hash(source)
    existing = (
        await session.execute(
            select(RuleExplanation)
            .where(RuleExplanation.source_sha256 == digest)
            .where(RuleExplanation.rule_id != rule.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is None:
        return None

    words = ExplanationDraft(
        breach_condition=existing.breach_condition,
        steps=[
            ExplanationStepDraft(n=int(s.get("n", 0)), text=str(s.get("text") or ""))
            for s in (existing.steps or [])
        ],
        outputs=list(existing.outputs or []),
        origin=existing.origin,
        generated_by=existing.generated_by,
        model=existing.model,
        prompt_version=existing.prompt_version,
    )
    return await store(session, rule, source, words, commit=False)
