"""
The rule agent: look at the data, write a rule, run it, and fix it until it works.

    ask tools ─► write ─► run_rule ─► (fix ─► run_rule)* ─► submit ─► gate
       ▲                                                               │
       └───────────────────────────────────────────────────────────────┘

The model is not handed a snapshot of the data and asked to imagine a rule. It
is given tools that read the real bytes and it looks. Everything it learns about
the estate, it learns by asking - `distinct_values` settles a value,
`join_overlap` settles a key, `column_stats` settles a threshold and a date
format. It then writes the rule and runs it with `run_rule` until the run is
clean.

WHY TOOLS
---------
A prompt can carry only what we thought to put in it. A tool answers what the
model actually needs. That is the difference between us guessing which value
list matters and the model asking for the one it is about to compare against.

THE GATE
--------
`run_rule` is the truth: the code executed against the bytes the estate holds,
never the model's opinion of its own code. The submitted source is run once more
as the gate, so a rule is handed back only if it runs - whatever the model
believed on the way.

THE LOOP IS SYNCHRONOUS
-----------------------
The model call and the trial are both blocking, so the whole thing runs in a
worker thread. Pretending otherwise would buy nothing but a way to lose the
traceback.

WHAT IS NOT HERE
----------------
No database, no ledger, no rule row. A generation writes nothing outside its own
scratch space. A rule that has not been reviewed by a human does not belong in
the catalogue, and a trial that raised a ticket would put a finding in the
ledger that nobody asked for.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from app.engine import rule_generator as gen
from app.engine.agent_tools import TOOL_SCHEMAS, Toolbox
from app.engine.catalog import Catalog
from app.engine.trial import TrialResult, run_trial

log = logging.getLogger("abm.agent")

# The model turn, as a function: given the conversation and the tools, return
# the next assistant message. Injected so the loop can be exercised without a
# live endpoint.
ModelStep = Callable[[list[dict[str, Any]], list[dict[str, Any]]], "gen.ModelReply"]


@dataclass
class GenerationResult:
    """What the caller gets back."""

    ok: bool
    source: str | None
    name: str | None
    description: str | None
    datasources_used: list[str]
    trial: TrialResult | None
    attempts: list[dict[str, Any]]
    error: str | None
    fatal: str | None
    # What the agent looked at, in order. The review screen shows it, so a
    # person sees why the agent believes the rule is right - not just that it
    # ran.
    trace: list[dict[str, Any]] = field(default_factory=list)


def _summarise(name: str, result: dict[str, Any]) -> str:
    """One readable line for the trace. Never the whole payload."""
    # A tool that failed returns {"error": ...} and nothing else. `run_rule`
    # returns an "error" key too, holding None on success - so the check is a
    # real error on a tool that has no verdict, not the key's presence.
    if "ok" not in result and result.get("error"):
        return f"error: {result['error']}"

    if name == "list_tables":
        return f"{len(result.get('tables', []))} table(s)"
    if name == "describe_table":
        columns = result.get("columns") or {}
        return f"{result.get('rows')} row(s), {len(columns)} column(s)"
    if name == "sample_rows":
        return f"{len(result.get('rows', []))} row(s)"
    if name == "distinct_values":
        shown = ", ".join(
            str(value.get("value")) for value in (result.get("values") or [])[:8]
        )
        more = ", ..." if result.get("truncated") else ""
        return f"{result.get('distinct')} value(s): {shown}{more}"
    if name == "column_stats":
        return (
            f"{result.get('type')}, {result.get('min')} .. {result.get('max')}"
            f", {result.get('nulls')} null(s)"
        )
    if name == "join_overlap":
        return (
            f"{result.get('matched_keys')} of {result.get('left_distinct_keys')} "
            f"key(s) matched"
        )
    if name == "filter_count":
        return f"{result.get('matched')} of {result.get('total')} row(s)"
    if name == "validate_rule":
        problems = result.get("problems") or []
        return "no problems" if not problems else f"{len(problems)} problem(s)"
    if name.startswith("run_rule"):
        found = result.get("row_count", 0)
        if result.get("ok"):
            return (
                f"ran: {result.get('status')}, found {found} row(s)"
                if found
                else f"ran: {result.get('status')}, found nothing"
            )
        return f"ran: {result.get('status')} - {result.get('error')}"
    return ""


def _final_run_summary(trial: TrialResult) -> dict[str, Any]:
    return {
        "ok": trial.ok,
        "status": trial.status,
        "row_count": len(trial.rows),
        "error": trial.error,
    }


def run_generation(
    *,
    catalog: Catalog,
    prompt: str,
    entity: str,
    data_date: date,
    tables: list[str],
    lookback_days: int = 1,
    allowed: list[str] | None = None,
    llm: ModelStep | None = None,
) -> GenerationResult:
    """Let the agent look at the data, write a rule, and prove it runs."""
    call = llm or gen.chat
    selection = list(tables)
    permitted = list(allowed) if allowed is not None else list(tables)

    toolbox = Toolbox(
        entity=entity,
        data_date=data_date,
        lookback_days=lookback_days,
        tables=selection,
        catalog=catalog,
        allowed=permitted,
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": gen.SYSTEM_PROMPT + "\n\n" + gen.AGENT_METHOD},
        {
            "role": "user",
            "content": gen.build_prompt(
                catalog=catalog,
                natural_language=prompt,
                entity=entity,
                data_date=data_date,
                tables=selection,
                lookback_days=lookback_days,
            ),
        },
    ]

    trace: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    source = ""

    def result(**overrides: Any) -> GenerationResult:
        base = {
            "ok": False,
            "source": None,
            "name": None,
            "description": None,
            "datasources_used": [],
            "trial": None,
            "attempts": attempts,
            "error": None,
            "fatal": None,
            "trace": trace,
        }
        base.update(overrides)
        return GenerationResult(**base)

    try:
        previous: tuple[Any, ...] | None = None
        while True:
            reply = call(messages, TOOL_SCHEMAS)

            assistant: dict[str, Any] = {
                "role": "assistant",
                "content": reply.content or "",
            }
            if reply.tool_calls:
                assistant["tool_calls"] = [c.as_message() for c in reply.tool_calls]
            messages.append(assistant)

            if not reply.tool_calls:
                source = gen.strip_fences(reply.content or "")
                break

            signature = tuple(
                (c.name, json.dumps(c.arguments, sort_keys=True))
                for c in reply.tool_calls
            )
            if signature == previous:
                # The same look, twice. Nothing new is coming; stop rather than
                # ask for it forever. This is a stuck-detector, not a budget.
                source = gen.strip_fences(reply.content or "")
                break
            previous = signature

            for tool_call in reply.tool_calls:
                output = toolbox.call(tool_call.name, tool_call.arguments)
                trace.append(
                    {
                        "tool": tool_call.name,
                        "args": tool_call.arguments,
                        "summary": _summarise(tool_call.name, output),
                    }
                )
                if tool_call.name == "run_rule":
                    attempts.append(
                        {
                            "n": len(attempts) + 1,
                            "ok": bool(output.get("ok")),
                            "error": output.get("error"),
                        }
                    )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(output, default=str),
                    }
                )
    except Exception as exc:  # noqa: BLE001
        fatal = f"The model could not be reached: {type(exc).__name__}: {exc}"
        return result(error=fatal, fatal=fatal)

    if not source.strip():
        fatal = "The model finished without writing a rule."
        return result(error=fatal, fatal=fatal)

    # The gate: the rule runs, or it is not a rule. Run once more on the source
    # that was submitted, whatever the model did while it was thinking.
    trial = run_trial(
        source,
        entity=entity,
        tables=selection,
        data_date=data_date,
        lookback_days=lookback_days,
        allowed=permitted,
    )
    attempts.append(
        {"n": len(attempts) + 1, "ok": trial.ok, "error": None if trial.ok else trial.error}
    )
    trace.append(
        {
            "tool": "run_rule",
            "args": {"final": True},
            "summary": _summarise("run_rule", _final_run_summary(trial)),
        }
    )

    return result(
        ok=trial.ok,
        source=source,
        name=trial.name,
        description=trial.description,
        datasources_used=list(trial.referenced),
        trial=trial,
        error=None if trial.ok else trial.error,
    )
