"""License gate for the whole product - backend Python and frontend npm.

Policy lives in `license_policy.py` and is imported, never restated here. Read
that module for what PERMITTED / REVIEW / UNKNOWN / BLOCKED mean and why the
matching is exact rather than by substring.

    python scripts/check_licenses.py

Exit codes: 0 = pass (REVIEW is reported but allowed), 1 = a blocked licence is
present, or a licence could not be classified at all. UNKNOWN fails on purpose:
an unclassifiable licence is exactly what a release must not wave through.
"""

from __future__ import annotations

import importlib.metadata as md
import json
import sys
from pathlib import Path

# license_policy.py owns the policy. Imported, never re-declared.
from license_policy import classify, classify_distribution, licence_string

BACKEND_DIR = Path(__file__).resolve().parent.parent
APP_ROOT = BACKEND_DIR.parent

VERDICTS = ("BLOCKED", "UNKNOWN", "REVIEW", "PERMITTED")


def _short(raw: str, width: int = 44) -> str:
    """One line, truncated, for the report."""
    first = (raw or "").splitlines()[0].strip() or "(none)"
    return first[:width]


def python_entries() -> list[tuple[str, str, str, str, str, str]]:
    """(ecosystem, name, version, licence, verdict, reason) per distribution."""
    rows = []
    for dist in md.distributions():
        name = dist.metadata["Name"]
        if not name:
            continue
        verdict, reason = classify_distribution(dist)
        rows.append(("python", name, dist.version, _short(licence_string(dist)), verdict, reason))
    return rows


def npm_entries() -> list[tuple[str, str, str, str, str, str]]:
    """The same, for every package in frontend/package-lock.json."""
    lock = APP_ROOT / "frontend" / "package-lock.json"
    if not lock.exists():
        print(
            "  note: frontend/package-lock.json not found - npm packages are NOT gated",
            file=sys.stderr,
        )
        return []

    data = json.loads(lock.read_text(encoding="utf-8"))
    rows = []
    seen: set[tuple[str, str]] = set()
    for path, meta in (data.get("packages") or {}).items():
        if not path or not meta.get("version"):
            continue
        name = meta.get("name") or path.split("node_modules/")[-1]
        key = (name, meta["version"])
        if key in seen:
            continue
        seen.add(key)
        raw = meta.get("license") or ""
        verdict, reason = classify(raw)
        rows.append(("npm", name, meta["version"], _short(raw), verdict, reason))
    return rows


def main() -> int:
    python_rows = python_entries()
    npm_rows = npm_entries()
    rows = python_rows + npm_rows

    buckets: dict[str, list] = {verdict: [] for verdict in VERDICTS}
    for row in rows:
        buckets[row[4]].append(row)

    print("=" * 78)
    print("LICENSE AUDIT - Audit Portal (backend Python + frontend npm)")
    print("=" * 78)
    print(f"  python distributions : {len(python_rows):>4}")
    print(f"  npm packages         : {len(npm_rows):>4}")
    print(f"  total checked        : {len(rows):>4}")
    print()
    print(f"  PERMITTED : {len(buckets['PERMITTED']):>4}")
    print(f"  REVIEW    : {len(buckets['REVIEW']):>4}")
    print(f"  UNKNOWN   : {len(buckets['UNKNOWN']):>4}")
    print(f"  BLOCKED   : {len(buckets['BLOCKED']):>4}")

    for verdict in ("BLOCKED", "UNKNOWN", "REVIEW"):
        items = sorted(buckets[verdict], key=lambda r: (r[0], r[1].lower()))
        if not items:
            continue
        print()
        print(f"--- {verdict} " + "-" * (70 - len(verdict)))
        for eco, name, version, licence, _v, reason in items:
            print(f"  [{eco:<6}] {name:<34} {version:<12} {licence:<24} {reason}")

    print()
    if buckets["BLOCKED"]:
        print("RESULT: FAIL - blocked licences present.")
        return 1
    if buckets["UNKNOWN"]:
        print("RESULT: FAIL - licences that could not be classified (add them to")
        print("        license_policy.py if they are acceptable).")
        return 1

    print("RESULT: PASS - every dependency is permitted or reviewed.")
    if buckets["REVIEW"]:
        print("        The REVIEW entries need a recorded sign-off before release.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
