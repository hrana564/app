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
    if not entity:
        return stmt
    join = _JOIN[model]
    if join is not None:
        target, onclause = join
        stmt = stmt.join(target, onclause)
    return stmt.where(Rule.entity == entity.upper())