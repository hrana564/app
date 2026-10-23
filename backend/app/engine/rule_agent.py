from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from app.engine import rule_generator as gen
from app.engine.agent_tools import TOOL_SCHEMAS, Toolbox
from app.engine.catalog import Catalog
from app.engine.llm import ModelReply, chat, strip_fences
from app.engine.trial import TrialResult, run_trial
from app.schemas import ExplanationDraft

log = logging.getLogger("abm.agent")

# The model turn, as a function: given the conversation and the tools, return
# the next assistant message. Injected so the loop can be exercised without a
# live endpoint.
ModelStep = Callable[[list[dict[str, Any]], list[dict[str, Any]]], ModelReply]


@dataclass
class GenerationResult:
    ok: bool
    source: str | None
    name: str | None
    description: str | None
    datasources_used: list[str]
    trial: TrialResult | None
    attempts: list[dict[str, Any]]
    error: str | None
    fatal: str | None
    # The plain-English description of the rule, written while the source was
    # still the one the model had just written. None when the model could not
    # be asked, or when its words did not survive the checks - a rule without
    # a description is still a rule.
    explanation: ExplanationDraft | None = None
    # What the agent looked at, in order. The review screen shows it, so a
    # person sees why the agent believes the rule is right - not just that it
    # ran.
    trace: list[dict[str, Any]] = field(default_factory=list)


def _summarise(name: str, result: dict[str, Any]) -> str:
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


def _describe(source: str, *, meta, llm: Any = None) -> ExplanationDraft | None:
    try:
        from app.engine import description

        words, gates = description.write_words(source, meta, llm=llm)
        if words is None:
            # Said out loud, because a silent failure here reads as "the
            # model cannot write descriptions" and costs an afternoon.
            refused = "; ".join(f"{g.name}: {g.detail}" for g in gates if not g.ok)
            log.warning(
                "the rule was written but not described%s",
                f": {refused}" if refused else " (the model did not answer with JSON)",
            )
        return words
    except Exception as exc:  # noqa: BLE001
        log.warning("could not describe the rule: %s", exc)
        return None


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
    call = llm or chat
    selection = list(tables)
    permitted = list(allowed) if allowed is not None else list(tables)

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
    return _run(
        call=call,
        messages=messages,
        catalog=catalog,
        entity=entity,
        data_date=data_date,
        lookback_days=lookback_days,
        tables=selection,
        allowed=permitted,
    )


def run_revision(
    *,
    catalog: Catalog,
    request: str,
    current_source: str,
    entity: str,
    data_date: date,
    tables: list[str],
    lookback_days: int = 1,
    allowed: list[str] | None = None,
    llm: ModelStep | None = None,
) -> GenerationResult:
    call = llm or chat
    selection = list(tables)
    permitted = list(allowed) if allowed is not None else list(tables)

    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                gen.SYSTEM_PROMPT + "\n\n" + gen.AGENT_METHOD
                + "\n\n" + gen.REVISION_METHOD
            ),
        },
        {
            "role": "user",
            "content": gen.build_revision_prompt(
                catalog=catalog,
                request=request,
                current_source=current_source,
                entity=entity,
                data_date=data_date,
                tables=selection,
                lookback_days=lookback_days,
            ),
        },
    ]
    return _run(
        call=call,
        messages=messages,
        catalog=catalog,
        entity=entity,
        data_date=data_date,
        lookback_days=lookback_days,
        tables=selection,
        allowed=permitted,
    )


def _run(
    *,
    call: ModelStep,
    messages: list[dict[str, Any]],
    catalog: Catalog,
    entity: str,
    data_date: date,
    lookback_days: int,
    tables: list[str],
    allowed: list[str],
) -> GenerationResult:
    toolbox = Toolbox(
        entity=entity,
        data_date=data_date,
        lookback_days=lookback_days,
        tables=tables,
        catalog=catalog,
        allowed=allowed,
    )

    trace: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []

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
            "explanation": None,
            "trace": trace,
        }
        base.update(overrides)
        return GenerationResult(**base)

    try:
        source = _run_loop(
            call=call, messages=messages, toolbox=toolbox,
            trace=trace, attempts=attempts,
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
        tables=tables,
        data_date=data_date,
        lookback_days=lookback_days,
        allowed=allowed,
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

    # The description of the control, written now while the source is still
    # the one the model has just written. Publishing must not cost a model
    # call, so this happens while the rule is being written rather than when
    # it goes live.
    explanation = None
    if trial.ok:
        def describe(system: str, user: str) -> str:
            reply = call(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                None,
            )
            return (reply.content or "").strip()

        from app.engine import description

        explanation = _describe(
            source,
            meta=description.Meta(
                name=trial.name or "",
                lookback_days=lookback_days,
            ),
            llm=describe,
        )

    return result(
        ok=trial.ok,
        source=source,
        name=trial.name,
        description=trial.description,
        datasources_used=list(trial.referenced),
        trial=trial,
        explanation=explanation,
        error=None if trial.ok else trial.error,
    )


def _run_loop(
    *,
    call: ModelStep,
    messages: list[dict[str, Any]],
    toolbox: Toolbox,
    trace: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
) -> str:
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
            return strip_fences(reply.content or "")

        signature = tuple(
            (c.name, json.dumps(c.arguments, sort_keys=True))
            for c in reply.tool_calls
        )
        if signature == previous:
            return strip_fences(reply.content or "")
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
