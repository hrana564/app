from __future__ import annotations

import re

from app.engine.datasources import ENTITIES

# Uppercase segments joined by hyphens: CA-03, LAD-CA-22.
CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*$")

MAX_CODE_LENGTH = 64


class CodeError(ValueError):
    pass


def validate_code(code: str) -> str:
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
    if version < 1:
        raise CodeError(f"version must be 1 or greater, got {version}")
    return f"{code}_v{version}.py"


def scoped_code(code: str, entity: str) -> str:
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