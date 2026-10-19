"""Which client entity a query is about, derived in exactly one place.

Every screen in the portal is scoped to one client, and only `rules_master`
actually knows which client a thing belongs to. A run and a ticket each point at
a rule and inherit its entity from there.

Writing that join in the runs endpoint, the tickets endpoint and the dashboard
separately is how a dashboard ends up counting LAD while the ticket inbox beside
it shows ABM - the two disagree silently and every number still looks plausible.
So the join lives here, once.

    stmt = scoped(stmt, Run, entity)

An absent or empty entity means "every client", which is what the scheduler and
the replay tool want. Callers pass the result to BOTH the page query and its
count query; narrowing one and not the other is how `total` starts lying about
what the page holds.
"""

from __future__ import annotations

from typing import Any

from app.models import Rule, Run, Ticket

# What each table joins to in order to reach `rules_master.entity`. Only Rule
# owns the column; runs and tickets inherit it through the rule they came from.
# A model that is not listed here is one no screen scopes by client.
_JOIN: dict[type, tuple[Any, Any] | None] = {
    Rule: None,
    Run: (Rule, Run.rule_id == Rule.id),
    Ticket: (Rule, Ticket.rule_id == Rule.id),
}


def scoped(stmt: Any, model: type, entity: str | None) -> Any:
    """Narrow `stmt` to one client entity.

    Returns the statement untouched when there is no entity, so callers never
    have to branch on it themselves.
    """
    if not entity:
        return stmt
    join = _JOIN[model]
    if join is not None:
        target, onclause = join
        stmt = stmt.join(target, onclause)
    return stmt.where(Rule.entity == entity.upper())