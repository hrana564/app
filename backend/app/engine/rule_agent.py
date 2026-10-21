"""
The rule agent: generate, run it, and fix it until it works.

    generate --> verify --+--> ok -------> END
       ^                  |
       |                  +--> repairable -> repair --+
       |                                              |
       +----------------------------------------------+

`verify` is where the truth comes from. It is not a second opinion from the
model about its own code - it is the code being executed against the bytes the
estate actually holds. A model asked "is this right?" will say yes; a rule
asked to read a column that does not exist cannot.

WHY A GRAPH
-----------
The loop has a shape: an attempt is made, the attempt is checked against
something outside the model, and the answer decides what happens next. Written
as a straight `while` it is four branches and one shared dict of state that
every branch has to know about. As a graph the state is declared once, the
edges say what may follow what, and the attempt history falls out of the state
rather than being assembled by hand.

The graph is synchronous, so the whole thing runs in a worker thread. The
model call and the trial are both blocking, and pretending otherwise would buy
nothing but a way to lose the traceback.

WHAT IS NOT HERE
----------------
No database, no ledger, no rule row. A generation attempt writes nothing
outside its own scratch folder, which is deleted afterwards. A rule that has
not been reviewed by a human does not belong in the catalogue, and a trial
that raised a ticket would put a finding in the ledger that nobody asked for.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, TypedDict

from app.engine import rule_generator as gen
from app.engine.catalog import Catalog
from app.engine.trial import TrialResult, run_trial
from app.settings import settings

log = logging.getLogger("abm.agent")

# langchain-core imports pydantic.v1 for its own internal models, and warns
# that pydantic v1 is not compatible with Python 3.14. It is: we use pydantic
# v2 everywhere, and this warning is about a code path we never touch. It
# would otherwise print on every generation, in the log of a product being
# sold, saying something that is not true of this code.
warnings.filterwarnings(
    "ignore",
    message=".*Pydantic V1 functionality isn't compatible.*",
    category=UserWarning,
)

# The model call, as a function. Injected so the graph can be exercised
# without a live endpoint.
Llm = Callable[[str, str], str]


class GenState(TypedDict, total=False):
    """Everything the graph carries between nodes."""

    prompt: str
    entity: str
    data_date: date
    lookback_days: int
    tables: list[str]
    allowed: list[str]
    source: str
    attempt: int
    attempts: list[dict[str, Any]]
    failure: str
    traceback: str | None
    fatal: str | None
    done: bool
    trial: dict[str, Any] | None


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


def _record(state: GenState, attempt: int, ok: bool, error: str | None,
            traceback: str | None = None) -> list[dict[str, Any]]:
    """One entry per attempt, recorded when that attempt concludes.

    An attempt concludes at the trial - that is where its outcome is known.
    The generate and repair nodes do not add records of their own, because
    "the model answered" is not an outcome; "the rule ran" is.
    """
    history = list(state.get("attempts") or [])
    history.append(
        {
            "n": attempt,
            "ok": ok,
            "error": error,
            "traceback": traceback,
        }
    )
    return history


def build_graph(llm: Llm, catalog: Catalog):
    """Compile the graph around one model-call function.

    The catalogue is closed over rather than carried in the state: it is
    constant for the whole run, and a graph channel is for things that change
    between nodes.
    """

    def generate(state: GenState) -> dict[str, Any]:
        attempt = 1
        try:
            raw = llm(
                gen.SYSTEM_PROMPT,
                gen.build_prompt(
                    catalog=catalog,
                    natural_language=state["prompt"],
                    entity=state["entity"],
                    data_date=state["data_date"],
                    tables=state["tables"],
                    lookback_days=state["lookback_days"],
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "attempt": attempt,
                "fatal": f"The model could not be reached: {type(exc).__name__}: {exc}",
                "attempts": _record(state, attempt, False, str(exc)),
            }

        source = gen.strip_fences(raw)
        return {
            "source": source,
            "attempt": attempt,
        }

    def verify(state: GenState) -> dict[str, Any]:
        attempt = state.get("attempt", 1)
        source = state.get("source") or ""

        trial = run_trial(
            source,
            entity=state["entity"],
            tables=state["tables"],
            data_date=state["data_date"],
            lookback_days=state["lookback_days"],
            allowed=state["allowed"],
        )

        if trial.ok:
            return {
                "trial": trial.as_dict(),
                "done": True,
                "attempts": _record(state, attempt, True, None),
            }

        history = _record(state, attempt, False, trial.error, trial.traceback)

        if not trial.repairable:
            # The data is not there. No rewrite fixes that, and an agent that
            # believes otherwise will rewrite working code until it has
            # nothing left to rewrite.
            return {
                "attempts": history,
                "fatal": trial.error,
            }

        if attempt >= settings.llm_max_attempts:
            return {
                "attempts": history,
                "fatal": (
                    f"Gave up after {attempt} attempt(s). Last failure: {trial.error}"
                ),
            }

        return {
            "attempts": history,
            "failure": trial.error or "The rule did not run.",
            "traceback": trial.traceback,
        }

    def repair(state: GenState) -> dict[str, Any]:
        attempt = state.get("attempt", 1) + 1
        try:
            raw = llm(
                gen.SYSTEM_PROMPT,
                gen.build_repair_prompt(
                    catalog=catalog,
                    source=state.get("source") or "",
                    failure=state.get("failure") or "The rule did not run.",
                    traceback_text=state.get("traceback"),
                    entity=state["entity"],
                    data_date=state["data_date"],
                    tables=state["tables"],
                    lookback_days=state["lookback_days"],
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "attempt": attempt,
                "fatal": f"The model could not be reached: {type(exc).__name__}: {exc}",
                "attempts": _record(state, attempt, False, str(exc)),
            }

        return {
            "source": gen.strip_fences(raw),
            "attempt": attempt,
        }

    def route_after_generate(state: GenState) -> str:
        # A model that could not be reached ends the run. There is nothing to
        # verify: the source is empty, and "the rule does not define run(ctx)"
        # would be a complaint about the model's absence, not about the rule.
        return "end" if state.get("fatal") else "verify"

    def route(state: GenState) -> str:
        if state.get("done") or state.get("fatal"):
            return "end"
        return "repair"

    from langgraph.graph import END, StateGraph

    graph = StateGraph(GenState)
    graph.add_node("generate", generate)
    graph.add_node("verify", verify)
    graph.add_node("repair", repair)
    graph.set_entry_point("generate")
    graph.add_conditional_edges(
        "generate", route_after_generate, {"verify": "verify", "end": END}
    )
    graph.add_conditional_edges(
        "verify", route, {"repair": "repair", "end": END}
    )
    graph.add_conditional_edges(
        "repair", route_after_generate, {"verify": "verify", "end": END}
    )
    return graph.compile()


def run_generation(
    *,
    catalog: Catalog,
    prompt: str,
    entity: str,
    data_date: date,
    tables: list[str],
    lookback_days: int = 1,
    allowed: list[str] | None = None,
    llm: Llm | None = None,
) -> GenerationResult:
    """Generate a rule, prove it runs, and repair it until it does."""
    call = llm or gen.complete

    state: GenState = {
        "prompt": prompt,
        "entity": entity,
        "data_date": data_date,
        "lookback_days": lookback_days,
        "tables": list(tables),
        "allowed": list(allowed) if allowed is not None else list(tables),
        "source": "",
        "attempt": 0,
        "attempts": [],
        "done": False,
    }

    graph = build_graph(call, catalog)
    final = graph.invoke(state)

    attempts = list(final.get("attempts") or [])
    trial_raw = final.get("trial")
    fatal = final.get("fatal")
    source = final.get("source") or ""

    trial = None
    if trial_raw:
        trial = TrialResult(
            status=trial_raw["status"],
            ok=trial_raw["ok"],
            outcome=trial_raw["outcome"],
            summary=trial_raw["summary"],
            rows=trial_raw["rows"],
            metrics=trial_raw["metrics"],
            error=trial_raw["error"],
            traceback=trial_raw["traceback"],
            duration_ms=trial_raw["duration_ms"],
            referenced=trial_raw["referenced"],
            name=trial_raw["name"],
            description=trial_raw["description"],
        )

    ok = bool(trial and trial.ok)
    return GenerationResult(
        ok=ok,
        source=source if ok else (source or None),
        name=trial.name if trial else None,
        description=trial.description if trial else None,
        datasources_used=list(trial.referenced) if trial else [],
        trial=trial,
        attempts=attempts,
        error=None if ok else (fatal or (trial.error if trial else "No result")),
        fatal=fatal,
    )
