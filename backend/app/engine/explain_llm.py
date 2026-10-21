"""
The words over the derivation.

`explain.py` can already say what a rule does, in sentences a head of audit can
read. They are true, and they are flat: "Read ipos_sales, work out hour." What
they cannot do is say *why* the control exists in the language the reader uses,
because that needs judgement rather than inspection.

So a model writes the prose, and the derivation keeps it honest:

    derive(source)          what is true, and where in the file it lives
    write(outline, source)  the same steps, said well
    check(outline, prose)   refuse anything the derivation does not support

The model is given the steps as a numbered skeleton and asked to fill in the
words. It cannot add a step, drop one, or state a number that is not in the
code - `explain.check()` refuses the whole answer if it tries. A model that
fails twice is not displayed at all; the derivation's own wording is.

WHY IT IS WORTH THE TROUBLE
---------------------------
An auditor is answerable for a control's design, not its syntax. A description
they can check against the code beside it - step by step, line by line - is
evidence. A paragraph that merely reads well is a liability, which is exactly
what a bare "summarise this code" prompt produces.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.engine.catalog import Catalog
from app.engine.explain import Gate, Outline, check, passed
from app.engine.rule_generator import complete, is_configured
from app.settings import settings

log = logging.getLogger(__name__)

# Bumped whenever the prompt changes in a way that would change the words. The
# stored explanation carries it, so a page can say which instruction produced
# the sentence it is showing.
PROMPT_VERSION = "explain-1"

SYSTEM = """
You describe audit controls to a head of audit who does not read code and will
not learn to.

You are given:
  - the Python source of one control, which is the only truth there is;
  - a numbered skeleton of the steps that source performs, with the lines each
    one occupies, derived from the source itself.

Rewrite the skeleton in plain English. Rules, in order of importance:

1. One sentence per step, in the same order, keyed by the same number. Never
   add a step, merge two, drop one, or renumber. Each step already carries an
   accurate, dull description of itself: rewrite *that* step, and never move
   an idea from one step to another.
2. State the condition that raises an exception before anything else, as
   `breach_condition`. It is the first thing a reader needs. Write it as one
   sentence beginning exactly "The control raises an exception when ", then
   the condition itself - not a description of the code, and not the quiet
   path. "The control raises an exception when the last hour's takings are
   more than twice the day's hourly average" is the shape.
3. Every number you write must appear in the source. Do not round it, do not
   convert it to a percentage or a multiple, do not invent one.
4. No code. No variable names, table names, column names, function names,
   library names, or file names. If a step reads `ipos_sales`, say what that
   data is - the day's sales lines - not what it is called.
5. Describe what the step is FOR, not what it is written as. "Keep only the
   rows where the invoice is not void" is acceptable but dull; "ignore sales
   that were cancelled, because a cancelled sale was not settled by anybody"
   is the job.
6. One idea per sentence, 20 words at most, lower-secondary reading age.
7. Never claim a control covers something the skeleton does not show. If a step
   is marked as not describable, say exactly that in plain words - that the
   check does something here that this description cannot account for. Do not
   guess.
8. Write in British English. No marketing language, no "this rule is designed
   to", no restating the step number.

Answer with JSON only:

{
  "breach_condition": "One sentence: what makes this control raise an exception.",
  "steps": [
    {"n": 1, "text": "One sentence for step 1."}
  ]
}
""".strip()

USER = """
CONTROL
  code:        {code}
  name:        {name}
  severity:    {severity}
  the day it runs against is one data date, and it looks back {lookback} day(s)

THE STEPS THE SOURCE PERFORMS
{steps}

THE SOURCE
```python
{source}
```

Rewrite the steps as instructed. Answer with JSON only.
""".strip()


def build_user(outline: Outline, source: str, meta: dict[str, Any]) -> str:
    # Each step carries the derivation's own sentence as well as its line range.
    # A skeleton of line numbers alone invites the model to invent a plausible
    # sequence and attach it to whatever numbers are left - which passes a
    # coverage check while pointing a reader at code that says something else.
    steps = "\n".join(
        f"  {s.n}. lines {s.lines[0]}-{s.lines[1]} — {s.text}"
        # The breach check is a different kind of step and the model is told so:
        # describing it as "finish successfully when ..." is accurate and
        # useless, because it describes the quiet path rather than the finding.
        + ("   [THIS IS THE BREACH CHECK: say what raises an exception]" if s.kind == "test" else "")
        + ("" if s.explainable else "   [not describable: say so]")
        for s in outline.steps
    )
    return USER.format(
        code=meta.get("code", ""),
        name=meta.get("name", ""),
        severity=meta.get("severity", ""),
        lookback=meta.get("lookback_days", 1),
        steps=steps or "  (none derived)",
        source=source.strip(),
    )


def _parse(answer: str) -> dict[str, Any] | None:
    """The JSON in a model's answer, however it wrapped it."""
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


def _normalise(prose: dict[str, Any], outline: Outline) -> dict[str, Any]:
    """Merge the model's words onto the derived structure.

    The line ranges are never taken from the model. They come from the
    derivation, so a step cannot point at the wrong part of the file even if
    the model hallucinates a line number.
    """
    spans = {s.n: list(s.lines) for s in outline.steps}
    steps = []
    for step in prose.get("steps") or []:
        try:
            n = int(step.get("n", 0))
        except (TypeError, ValueError):
            continue
        if n not in spans:
            continue
        steps.append(
            {
                "n": n,
                "text": str(step.get("text") or "").strip(),
                "lines": spans[n],
            }
        )
    return {
        "breach_condition": str(prose.get("breach_condition") or "").strip(),
        "steps": steps,
    }


def write(
    outline: Outline,
    source: str,
    meta: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[Gate], str]:
    """Ask the model for the words, and refuse them if the gates disagree.

    One retry, quoting the failures back at it. A second failure is final: the
    page falls back to the derived wording rather than showing prose that
    states something the code does not. Two attempts is a budget, not a
    negotiation - a model given a third or fourth try tends to satisfy the
    checker rather than the reader.
    """
    if not is_configured():
        return None, [], ""

    user = build_user(outline, source, meta)
    failures: list[str] = []
    gates: list[Gate] = []

    for attempt in (1, 2):
        prompt = user
        if failures:
            prompt = (
                f"{user}\n\nYour previous answer was rejected by these checks:\n"
                + "\n".join(f"  - {f}" for f in failures)
                + "\nFix exactly these and answer again with JSON only."
            )
        try:
            answer = complete(SYSTEM, prompt)
        except Exception as exc:  # a provider that is down must not break the page
            log.warning("explanation generation failed: %s", exc)
            return None, [], ""

        parsed = _parse(answer)
        if parsed is None:
            failures = ["the answer was not JSON"]
            continue

        prose = _normalise(parsed, outline)
        gates = check(outline, prose, len(source.splitlines()))
        if passed(gates):
            return prose, gates, settings.llm_model
        failures = [f"{g.name}: {g.detail}" for g in gates if not g.ok]

    return None, gates, settings.llm_model


def probe(outline: Outline, source: str, prose: dict[str, Any]) -> dict[str, Any] | None:
    """A question whose answer must agree with the code.

    "Does this control raise an exception when the value is ABOVE the limit or
    BELOW it?" is answerable from the source alone, and a description that
    states the opposite of the answer is not merely poorly worded - it is a
    description of a different control. One question, asked of the same model,
    is a cheap check that the words and the code are about the same thing.

    Recorded, not enforced. A probe that fails is worth showing to a reviewer;
    a probe that blocks the page would make an unreliable second model the
    gatekeeper of the first.
    """
    if not is_configured() or not outline.breach:
        return None

    question = (
        "Read this Python control and answer one question about it.\n\n"
        "```python\n" + source.strip() + "\n```\n\n"
        f"One description of it says: \"{prose.get('breach_condition', '')}\"\n\n"
        "Does that description match what the code does? Answer with JSON only:\n"
        '{"matches": true|false, "why": "one sentence"}'
    )
    try:
        answer = complete(
            "You check whether a written description of a control matches the "
            "code. You answer only with JSON.",
            question,
        )
    except Exception as exc:
        log.warning("explanation probe failed: %s", exc)
        return None

    parsed = _parse(answer) or {}
    return {
        "question": "does the stated condition match the code?",
        "matches": bool(parsed.get("matches")),
        "why": str(parsed.get("why") or "")[:500],
        "model": settings.llm_model,
    }
