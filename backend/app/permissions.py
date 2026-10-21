"""What each role may do, expressed once.

An endpoint asks for the capability it needs, not the role it assumes:

    admin:  AdminUser      ->  write: Annotated[User, Depends(require("rules.write"))]

Eleven endpoints asking "may I?" is eleven mappings, and they drift the first
time a third role exists. A role here is a named bundle of capabilities, so
granting one capability to one role is a visible edit in this file rather than
a hunt through the routers.

Two rules make this hold:

- **An unknown role grants nothing.** A typo in the `users.role` column
  produces a user who can read nothing, not one who can do everything. Failing
  closed means a mistake is a locked door rather than an open one.
- **The matrix is the only answer.** If a second module decides what a role
  can do, the two drift invisibly: the server still enforces correctly while
  the screen beside it shows an action the user may not take.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from app.models import User

ROLE_ADMIN = "admin"
ROLE_MANAGER = "manager"
ROLE_AUDITOR = "auditor"
ROLE_VIEWER = "viewer"

# --- capabilities ---------------------------------------------------------
# The vocabulary. An endpoint's dependency names one of these, and the same
# name travels to the frontend in the login response, so a screen can ask the
# same question the server asked.
RULES_READ = "rules.read"
RULES_TEST = "rules.test"
RULES_WRITE = "rules.write"
RULES_ACTIVATE = "rules.activate"
RULES_DELETE = "rules.delete"
RULES_GENERATE = "rules.generate"

# Reading a description is part of reading the estate, which every role may do.
# *Approving* one is a sign-off, and the whole point of the review state is that
# the person who signs it is the person answerable for the control - an auditor,
# not whoever last edited the rule. So it is its own capability.
RULES_EXPLAIN_REVIEW = "rules.explain_review"

SCHEDULES_READ = "schedules.read"
SCHEDULES_WRITE = "schedules.write"

RUNS_READ = "runs.read"
RUNS_RERUN = "runs.rerun"

TICKETS_READ = "tickets.read"
TICKETS_ACKNOWLEDGE = "tickets.acknowledge"

DATASOURCES_READ = "datasources.read"
DATASOURCES_WRITE = "datasources.write"

ASSURANCE_READ = "assurance.read"

USERS_READ = "users.read"
USERS_WRITE = "users.write"

# The order the matrix is READ in. A set has no order, and a table whose rows
# move about when a capability is added is a table nobody can learn - so the
# order is written down here, once, and the set is derived from it rather than
# the other way round.
CAPABILITY_ORDER: tuple[str, ...] = (
    RULES_READ, RULES_TEST, RULES_WRITE, RULES_ACTIVATE, RULES_DELETE,
    RULES_GENERATE, RULES_EXPLAIN_REVIEW,
    SCHEDULES_READ, SCHEDULES_WRITE,
    RUNS_READ, RUNS_RERUN,
    TICKETS_READ, TICKETS_ACKNOWLEDGE,
    DATASOURCES_READ, DATASOURCES_WRITE,
    ASSURANCE_READ,
    USERS_READ, USERS_WRITE,
)

ALL_CAPABILITIES: frozenset[str] = frozenset(CAPABILITY_ORDER)

# --- the matrix ------------------------------------------------------------
# Read everything, change nothing, sign off nothing. The read-only audience:
# a head of audit who wants to see the estate without being able to alter it.
ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    ROLE_ADMIN: ALL_CAPABILITIES,
    # Everything about the ESTATE, and nothing about the PEOPLE. A manager
    # writes the rules, the schedules and the catalogue; a manager cannot add
    # an account or change a role. That is the split worth having when the
    # estate is maintained by somebody who is not the portal administrator.
    ROLE_MANAGER: ALL_CAPABILITIES - {USERS_READ, USERS_WRITE},
    ROLE_AUDITOR: frozenset(
        {
            RULES_READ, RULES_TEST, RULES_EXPLAIN_REVIEW,
            SCHEDULES_READ,
            RUNS_READ, RUNS_RERUN,
            TICKETS_READ, TICKETS_ACKNOWLEDGE,
            DATASOURCES_READ,
            ASSURANCE_READ,
        }
    ),
    ROLE_VIEWER: frozenset(
        {
            RULES_READ, RULES_TEST,
            SCHEDULES_READ,
            RUNS_READ,
            TICKETS_READ,
            DATASOURCES_READ,
            ASSURANCE_READ,
        }
    ),
}

KNOWN_ROLES: tuple[str, ...] = tuple(ROLE_CAPABILITIES)


def capabilities_for(role: str) -> frozenset[str]:
    """The capabilities a role holds.

    An unrecognised role holds none. The alternative - treating an unknown
    role as the most privileged one - would make a typo in a database column
    into a security hole, and a typo that silently grants nothing is a locked
    door somebody notices and fixes.
    """
    return ROLE_CAPABILITIES.get(role, frozenset())


def capabilities_for_user(user: "User") -> frozenset[str]:
    """The capabilities one user holds.

    The role is the only thing that decides it today. When a user is confined
    to particular clients, that restriction is applied here and nowhere else -
    so there is one answer to "what may this user see" rather than one per
    endpoint.
    """
    return capabilities_for(user.role)


def has_capability(user: "User", capability: str) -> bool:
    return capability in capabilities_for_user(user)


def permissions_list(role: str) -> list[str]:
    """The capabilities a role holds, sorted, for the wire.

    Sorted so the list is stable: two calls with the same role produce the
    same bytes, which is what lets a client cache it and compare it.
    """
    return sorted(capabilities_for(role))


# ---------------------------------------------------------------------------
# How the matrix reads
# ---------------------------------------------------------------------------
# The names above are a wire vocabulary. `rules.read` is precise, and it is what
# a dependency names - but it is not what a person reads on a screen, and the
# screen that shows the matrix must show the SERVER's matrix rather than a
# second copy of it written in the frontend.
#
# So the labels live here, next to the vocabulary they describe. A label
# defined in the frontend is a label that drifts: the matrix gains a
# capability, the frontend is not told, and the screen says the capability does
# not exist.
ROLE_LABELS: dict[str, str] = {
    ROLE_ADMIN: "Administrator",
    ROLE_MANAGER: "Manager",
    ROLE_AUDITOR: "Auditor",
    ROLE_VIEWER: "Viewer",
}

ROLE_DESCRIPTIONS: dict[str, str] = {
    ROLE_ADMIN: "Everything, including managing the accounts.",
    ROLE_MANAGER: (
        "Everything about the estate - rules, schedules, catalogue - and "
        "nothing about the people."
    ),
    ROLE_AUDITOR: (
        "Reads the estate, runs controls and signs off findings. Cannot "
        "change the estate."
    ),
    ROLE_VIEWER: (
        "Reads the estate and runs controls. Cannot sign off a finding or "
        "change anything."
    ),
}

CAPABILITY_LABELS: dict[str, str] = {
    RULES_READ: "Read the rules",
    RULES_TEST: "Run a rule against the data",
    RULES_WRITE: "Create and edit rules",
    RULES_ACTIVATE: "Activate, pause and retire a rule",
    RULES_DELETE: "Remove a rule",
    RULES_GENERATE: "Draft a rule with the assistant",
    RULES_EXPLAIN_REVIEW: "Sign off a rule's plain-English description",
    SCHEDULES_READ: "Read the schedules",
    SCHEDULES_WRITE: "Create and edit schedules",
    RUNS_READ: "Read the runs",
    RUNS_RERUN: "Re-run a control",
    TICKETS_READ: "Read the findings",
    TICKETS_ACKNOWLEDGE: "Acknowledge a finding",
    DATASOURCES_READ: "Read the catalogue",
    DATASOURCES_WRITE: "Register and remove data sources",
    ASSURANCE_READ: "Read the assurance overview",
    USERS_READ: "Read the accounts",
    USERS_WRITE: "Manage the accounts",
}

# The group a capability belongs to, by the prefix of its name. The groups are
# the subjects of the portal, and a table of seventeen rows reads better as
# seven short ones.
CAPABILITY_GROUPS: dict[str, str] = {
    "rules": "Rules",
    "schedules": "Schedules",
    "runs": "Runs",
    "tickets": "Findings",
    "datasources": "Data sources",
    "assurance": "Assurance",
    "users": "Accounts",
}


def capability_group(capability: str) -> str:
    """The group a capability belongs to: the part before its first dot."""
    return capability.split(".", 1)[0]


def capability_label(capability: str) -> str:
    """What a capability is called on a screen."""
    return CAPABILITY_LABELS.get(capability, capability)


def group_label(group: str) -> str:
    """What a group of capabilities is called on a screen."""
    return CAPABILITY_GROUPS.get(group, group)


def role_label(role: str) -> str:
    """What a role is called on a screen."""
    return ROLE_LABELS.get(role, role)


def role_description(role: str) -> str:
    """What a role is for, in one sentence."""
    return ROLE_DESCRIPTIONS.get(role, "")
