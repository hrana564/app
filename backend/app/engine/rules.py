"""
Rule codes and version filenames.

A rule's `code` is its permanent identifier: it keys the version rows, the
schedules, and the source filename. Because schedules reference the code
directly (no foreign key), a renamed code would silently orphan them - so the
code is validated to a filename-safe, uppercase-only shape and is never edited
after creation.
"""

from __future__ import annotations

import re

# Uppercase segments joined by hyphens: LAD-CA-03, ABM-CA-22.
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
            f"separated by single hyphens, e.g. LAD-CA-03. "
            f"(letters, digits and hyphens only - no spaces, slashes or "
            f"underscores.)"
        )
    return normalised


def versioned_filename(code: str, version: int) -> str:
    """`LAD-CA-03` + 2 -> `LAD-CA-03_v2.py`"""
    if version < 1:
        raise CodeError(f"version must be 1 or greater, got {version}")
    return f"{code}_v{version}.py"