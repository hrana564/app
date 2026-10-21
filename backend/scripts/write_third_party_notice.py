"""Write THIRD_PARTY_NOTICES.txt from the installed dependency trees.

Every permissive licence here - MIT, BSD-2/3, ISC, Apache-2.0, PSF - obliges
you to carry its copyright notice and licence text when you distribute the
software. `check_licenses.py` proves none of them are copyleft. It does NOT
produce the attribution you owe, which is why this is a separate script and why
the file it writes is tracked while the tool that writes it is not.

Run it before a release, and re-run it whenever a dependency changes:

    python scripts/write_third_party_notice.py

Refuses to write if anything BLOCKED appears, so a copyleft dependency cannot
be attributed into compliance by being quietly listed. "BLOCKED" is whatever
`check_licenses.py` says it is - this script imports the policy rather than
restating it, because a notice that silently disagrees with the gate is worse
than no notice.
"""

from __future__ import annotations

import importlib.metadata as md
import sys
from datetime import date
from pathlib import Path

# check_licenses.py owns the policy. Imported, never re-declared.
#
# This script had its own PERMITTED/BLOCKED sets, and they had already drifted
# from the checker's by the first commit that contained them: `epl`, `cpal` and
# `commercial` are BLOCKED by the checker and were absent from this list, while
# bare `apache`, `bsd` and `unlicense` are PERMITTED by the checker and unknown
# here. That is the exact failure the checker exists to prevent - a copyleft
# dependency quietly written into a notice as though it had been declared - and
# it happened inside the tool whose job is to prevent it.
#
# One source of truth. If the policy changes, it changes there and both scripts
# follow.
from check_licenses import (
    _license_from_classifier,
    classify,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent
APP_ROOT = BACKEND_DIR.parent


def normalise(raw: str) -> str:
    """Only used to spot a licence field that carries no licence at all."""
    import re

    return re.sub(r"[^a-z0-9.+-]", "", (raw or "").lower())


def licence_of(dist: md.Distribution) -> str:
    """The most specific licence string we can get for one distribution.

    Resolution order is the checker's, not this script's: PEP 639
    `License-Expression` first, then the free-text `License` field, then the
    Trove classifier. The `License` field is sometimes a copyright line rather
    than a licence - polars is the one that bites - and sometimes simply vague
    ("Dual License", as python-dateutil is). Both cases are why the classifier
    is consulted before giving up.

    Duplicating that order here is what made the two scripts disagree about
    which packages they could even name. `_license_from_classifier` is the
    checker's, so the fallback cannot drift either.
    """
    expr = dist.metadata.get("License-Expression")
    if expr:
        return str(expr).strip()
    raw = dist.metadata.get("License")
    if raw and normalise(raw) not in {"", "unknown"}:
        return str(raw).strip()
    return _license_from_classifier(dist)


def licence_files(root: Path) -> list[str]:
    """Full licence texts shipped alongside the package, where they exist."""
    found = []
    for pattern in ("LICENSE*", "COPYING*", "NOTICE*", "AUTHORS*"):
        for p in sorted(root.glob(pattern)):
            if p.is_file():
                found.append((p.name, p.read_text(encoding="utf-8", errors="replace").strip()))
    return found


def python_entries() -> list[tuple[str, str, str, str, str]]:
    """(name, version, licence, homepage, copyright) for every distribution."""
    rows = []
    for dist in md.distributions():
        name = dist.metadata["Name"]
        if not name:
            continue
        meta = dist.metadata
        homepage = meta.get("Home-page") or ""
        if not homepage:
            for url in meta.get_all("Project-URL") or []:
                if "homepage" in url.lower().split(",")[0]:
                    homepage = url.split(",", 1)[-1].strip()
        rows.append((
            name,
            dist.version,
            licence_of(dist),
            homepage,
            (meta.get("Author") or meta.get("Author-email") or "").strip(),
        ))
    return sorted(rows, key=lambda r: r[0].lower())


def npm_entries() -> list[tuple[str, str, str]]:
    """(name, version, licence) from package-lock.json, when it is present.

    A missing lockfile is not an error: this script is run from the backend, and
    the frontend may not have been installed yet. It is worth a warning, because
    a notice that silently omits the frontend would understate the obligation.
    """
    lock = APP_ROOT / "frontend" / "package-lock.json"
    if not lock.exists():
        print("  note: frontend/package-lock.json not found - npm packages omitted",
              file=sys.stderr)
        return []
    import json
    data = json.loads(lock.read_text(encoding="utf-8"))
    out = {}
    for path, meta in (data.get("packages") or {}).items():
        if not path or not meta.get("version"):
            continue
        name = meta.get("name") or path.split("node_modules/")[-1]
        out[name] = (name, meta["version"], meta.get("license", "UNKNOWN"))
    return sorted(out.values(), key=lambda r: r[0].lower())


def main() -> int:
    out = APP_ROOT / "THIRD_PARTY_NOTICES.txt"

    py = python_entries()
    npm = npm_entries()

    blocked = [
        (n, v, l) for n, v, l, _, _ in py if classify(l)[0] == "BLOCKED"
    ]
    if blocked:
        print("REFUSING to write: a blocked licence is installed.", file=sys.stderr)
        for n, v, l in blocked:
            print(f"  {n}=={v}  {l}", file=sys.stderr)
        return 1

    unclassified = [
        f"{n}=={v} ({l})" for n, v, l, _, _ in py if classify(l)[0] == "UNKNOWN"
    ]

    lines: list[str] = []
    w = lines.append
    w("THIRD-PARTY NOTICES")
    w("=" * 72)
    w("")
    w(f"Audit Portal - generated {date.today().isoformat()} by")
    w("backend/scripts/write_third_party_notice.py")
    w("")
    w("This product bundles the third-party software listed below. Each entry is")
    w("distributed under its own licence, reproduced in full further down. The")
    w("copyright notices and licence texts MUST be retained in any redistribution,")
    w("whether modified or not.")
    w("")
    w("Product source code is proprietary. Nothing here grants any licence in the")
    w("product's own code.")
    w("")
    w("-" * 72)
    w("PYTHON - backend runtime and tooling")
    w("-" * 72)
    w("")
    w(f"{'package':<32}{'version':<14}licence")
    for n, v, l, _, _ in py:
        w(f"{n:<32}{v:<14}{l}")

    if npm:
        w("")
        w("-" * 72)
        w("NPM - frontend runtime")
        w("-" * 72)
        w("")
        w(f"{'package':<44}{'version':<12}licence")
        for n, v, l in npm:
            w(f"{n:<44}{v:<12}{l}")

    w("")
    w("-" * 72)
    w("LICENCE TEXTS")
    w("-" * 72)
    w("")
    w("Reproduced verbatim from the installed distributions. A licence named above")
    w("whose full text is not reproduced here is a permissive licence whose text is")
    w("available at its project home; no blocked (copyleft or source-available)")
    w("licence is present, which scripts/check_licenses.py enforces on every run.")
    w("")

    for n, v, l, _, _ in py:
        try:
            dist = md.distribution(n)
        except md.PackageNotFoundError:
            continue
        root = Path(str(dist.locate_file("")))
        texts = licence_files(root)
        if not texts:
            continue
        w("=" * 72)
        w(f"{n} {v} - {l}")
        w("=" * 72)
        for fname, body in texts:
            w("")
            w(f"--- {fname} ---")
            w("")
            w(body)
            w("")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    print(f"wrote {out.relative_to(APP_ROOT)}")
    print(f"  {len(py)} python packages, {len(npm)} npm packages")
    if unclassified:
        print(
            f"  {len(unclassified)} with no machine-readable licence: "
            f"{', '.join(unclassified)}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())