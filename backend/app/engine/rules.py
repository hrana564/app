"""
Rule codes and version filenames.

A rule's `code` is its permanent identifier: it keys the version rows, the
schedules, and the source filename. Because schedules reference the code
directly (no foreign key), a renamed code would silently orphan them - so the
code is validated to a filename-safe, uppercase-only shape and is never edited
after creation.

THE CODE NAMES THE CLIENT
-------------------------
`LAD-CA-03` says whose control it is, and the ticket number derived from it
says the same - a reference an auditor reads out of an email should say where
it came from without opening the record.

Which client a rule belongs to is the rule's ROW's business, so the prefix is
applied when the rule is created and never written into the file: a file that
carried the prefix would be wrong the moment it was registered for a
different client.
"""

from __future__ import annotations

import re

from app.engine.datasources import ENTITIES

# Uppercase segments joined by hyphens: CA-03, LAD-CA-22.
CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*$")

MAX_CODE_LENGTH = 64


class CodeError(ValueError):
    pass


def validate_code(code: str) -> str:
    """Return the normalised code, or raise CodeError with a usable message."""
    if code is None:
        raise CodeError("code is required")
    normalised = str(code).strip().upper().replace("_", "-")
    if not normalised:
        raise CodeError("code is required")
    if len(normalised) > MAX_CODE_LENGTH:
        raise CodeError(f"code must be {MAX_CODE_LENGTH} characters or fewer")
    if not CODE_PATTERN.match(normalised):
        raise CodeError(
            f"code {code!r} is not valid. Use uppercase letters and digits "
            f"separated by single hyphens, e.g. CA-03. "
            f"(letters, digits and hyphens only - no spaces, slashes or "
            f"underscores.)"
        )
    return normalised


def versioned_filename(code: str, version: int) -> str:
    """`LAD-CA-03` + 2 -> `LAD-CA-03_v2.py`"""
    if version < 1:
        raise CodeError(f"version must be 1 or greater, got {version}")
    return f"{code}_v{version}.py"


def scoped_code(code: str, entity: str) -> str:
    """`CA-03` for the client `LAD` -> `LAD-CA-03`.

    The prefix is the client's code, and this is the only place a code and a
    client meet - so a rule filed under one client can never carry another
    client's prefix.

    A code that already starts with a client code has it REPLACED rather than
    stacked, so typing `ABM-CA-03` while LAD is selected gives `LAD-CA-03`
    and not `LAD-ABM-CA-03`. The prefix always follows the row.
    """
    client = str(entity or "").strip().upper()
    if not client:
        return code
    body = code
    for known in ENTITIES:
        prefix = str(known).upper()
        if body.startswith(prefix + "-"):
            body = body[len(prefix) + 1:]
            break
    return f"{client}-{body}" if body else client