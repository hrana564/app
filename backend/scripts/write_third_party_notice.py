"""Write THIRD_PARTY_NOTICES.txt from the installed dependency trees.

Every permissive licence here - MIT, BSD-2/3, ISC, Apache-2.0, PSF - obliges
you to carry its copyright notice and licence text when you distribute the
software. `check_licenses.py` proves none of them are copyleft. It does NOT
produce the attribution you owe, which is why this is a separate script and why
the file it writes is tracked while the tool that writes it is not.

Run it before a release, and re-run it whenever a dependency changes:

    python scripts/write_third_party_notice.py

WHAT IT WRITES

  1. A table of every Python and npm package with its version, licence and
     copyright holder.
  2. The full text of every distinct licence, exactly once.
  3. Verbatim reproductions of any package-specific notice files the
     distributions ship (a NOTICE, or a non-standard LICENSE), deduplicated.
     AUTHORS files are name lists with no licence content and are not repeated.

WHERE THE TEXTS COME FROM

  * The canonical SPDX text of each licence, from the `licenses/` directory
    beside this app (see licenses/README.md). This is what makes the notice
    complete even though most wheels ship no licence file at all.
  * Anything a distribution actually ships, reproduced verbatim, so a
    non-standard licence is never silently replaced by the canonical one.

  A licence present in the tree with no canonical text and no shipped text is a
  hard failure: the script refuses to write a notice that understates the
  obligation.

WHY IT USED TO SHIP AN EMPTY TEXT SECTION

  The first version globbed LICENSE* at `dist.locate_file("")`, which is the
  site-packages *root*. Licences do not live there - they live inside each
  `*.dist-info/` directory (and, since PEP 639, in `*.dist-info/licenses/`).
  The glob matched nothing, so the notice listed the packages and reproduced
  none of the texts. Discovery now walks the distribution's RECORD (the exact
  installed file list) and its metadata directory.

Refuses to write if anything BLOCKED appears, so a copyleft dependency cannot
be attributed into compliance by being quietly listed. "BLOCKED" is whatever
`check_licenses.py` says it is - this script imports the policy rather than
restating it, because a notice that silently disagrees with the gate is worse
than no notice.
"""

from __future__ import annotations

import importlib.metadata as md
import json
import re
import sys
from datetime import date
from pathlib import Path

# license_policy.py owns the policy. Imported, never re-declared.
#
# The gate and the notice must agree on what a licence string means, so both ask
# the same module. `ids_of` names every licence a string mentions for
# attribution; `classify` gives the release verdict.
from license_policy import (
    classify,
    classify_distribution,
    ids_of,
    ids_of_distribution,
    licence_string,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent
APP_ROOT = BACKEND_DIR.parent

# Canonical licence texts, one file per SPDX id. Sourced from the SPDX
# license-list-data repository; see licenses/README.md.
CANONICAL_DIR = APP_ROOT / "licenses"

# Files a distribution ships that carry licence or attribution text.
NOTICE_NAMES = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS", "COPYRIGHT")

# Packages that carry attribution for material their own licence field does not
# name, keyed by package -> {additional SPDX id: why}. The id is added to what
# the package is listed under, so its text travels with the package; the
# package's own table row keeps the licence it actually declares.
#
# @mui/icons-material declares MIT, but the icon geometry it distributes is
# Google's Material Icons, which are Apache-2.0. Attribution for both is owed.
EXTRA_ATTRIBUTION: dict[str, dict[str, str]] = {
    "@mui/icons-material": {
        "Apache-2.0": "icon artwork derived from Google's Material Icons",
    },
}


def normalise(raw: str) -> str:
    """Only used to spot a licence field that carries no licence at all."""
    return re.sub(r"[^a-z0-9.+-]", "", (raw or "").lower())


def _read_text(path: Path) -> str:
    """Read a file as text with LF endings, whatever the checkout produced.

    A licence text is reproduced verbatim, so it must not depend on the line
    endings Git happened to check out - otherwise the notice differs between a
    Linux and a Windows checkout. Normalising here keeps it identical.
    """
    return (
        path.read_text(encoding="utf-8", errors="replace")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )


def _is_notice_name(name: str) -> bool:
    up = name.upper()
    return any(up.startswith(prefix) for prefix in NOTICE_NAMES)


def notice_files(dist: md.Distribution) -> list[tuple[str, str]]:
    """Every licence/notice file this distribution actually installed.

    Walked from two places because neither is complete on its own:

      * `dist.files` - the RECORD, the exact installed file list. This is the
        authoritative source and finds both `*.dist-info/LICENSE` and the
        PEP 639 `*.dist-info/licenses/*` layout.
      * the metadata directory itself - a fallback for distributions whose
        RECORD is missing or truncated.

    Deduplicated by content, so a licence shipped in two places is printed once.
    """
    candidates: list[Path] = []

    for entry in dist.files or []:
        if _is_notice_name(entry.name):
            path = Path(str(dist.locate_file(entry)))
            if path.is_file():
                candidates.append(path)

    meta_dir = getattr(dist, "_path", None)
    if meta_dir is not None:
        meta_dir = Path(meta_dir)
        if meta_dir.is_dir():
            for path in sorted(meta_dir.rglob("*")):
                if path.is_file() and _is_notice_name(path.name):
                    candidates.append(path)

    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for path in candidates:
        try:
            text = _read_text(path).strip()
        except OSError:
            continue
        if not text:
            continue
        fingerprint = re.sub(r"\s+", " ", text)[:4000]
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        found.append((path.name, text))
    return found


def canonical_text(key: str) -> str | None:
    path = CANONICAL_DIR / f"{key}.txt"
    if not path.is_file():
        return None
    return _read_text(path).strip()


def all_canonical_texts() -> dict[str, str]:
    if not CANONICAL_DIR.is_dir():
        return {}
    return {
        p.stem: _read_text(p).strip()
        for p in sorted(CANONICAL_DIR.glob("*.txt"))
    }


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _is_standard_licence(text: str, canonical_texts) -> bool:
    """True when a shipped file is just the standard text of some licence.

    A file that matches a canonical licence adds nothing the FULL LICENSE TEXTS
    section does not already carry, so the appendix skips it. Matching is done
    on a 300-character probe from the middle of the canonical text, because a
    shipped MIT file differs from the SPDX template only in its copyright line
    and line wrapping - a full-string comparison would never match.
    """
    flat = _flat(text)
    for canonical in canonical_texts:
        cn = _flat(canonical)
        if not cn:
            continue
        if cn in flat:
            return True
        if len(cn) > 400:
            middle = len(cn) // 2
            if cn[middle - 150 : middle + 150] in flat:
                return True
    return False


def keep_notice(fname: str, text: str, canonical_texts) -> bool:
    """Whether a shipped file belongs in the package-specific appendix.

    NOTICE files are always kept: Apache-2.0 section 4(d) requires propagating
    them. AUTHORS files are name lists with no licence content, so they are
    dropped - the COPYRIGHT NOTICES table already carries the attribution.
    Everything else is kept only when it is not a standard licence text.
    """
    up = fname.upper()
    if up.startswith("NOTICE"):
        return True
    if up.startswith("AUTHORS"):
        return False
    return not _is_standard_licence(text, canonical_texts)


def _author_of(dist: md.Distribution) -> str:
    meta = dist.metadata
    author = (meta.get("Author") or "").strip()
    if not author:
        author = (meta.get("Author-email") or "").strip()
    if not author:
        # Some projects (polars) put a bare copyright line in the License field.
        raw = (meta.get("License") or "").strip()
        if raw.lower().startswith("copyright"):
            author = raw.splitlines()[0].strip()
    return author


def licence_label(raw: str, keys: set[str]) -> str:
    """A one-line licence label for the table.

    The package's own wording is kept only when it is a short, single-line
    expression that carries an `AND`/`OR` choice - there the wording is legally
    meaningful (`Apache-2.0 OR BSD-2-Clause` is not `Apache-2.0 AND
    BSD-2-Clause`). Everything else is shown as the resolved SPDX ids, so a
    classifier-derived "BSD License" becomes `BSD-3-Clause` and polars' whole
    MIT text in the `License` field does not land in the table.
    """
    text = (raw or "").strip()
    first = text.splitlines()[0].strip() if text else ""
    single_line = "\n" not in text
    if single_line and first and len(first) <= 80 and re.search(r"\b(?:AND|OR)\b", text):
        return first
    if keys:
        return " / ".join(sorted(keys))
    return first[:80] or "UNKNOWN"


def python_entries() -> list[dict]:
    """One record per installed Python distribution."""
    rows = []
    for dist in md.distributions():
        name = dist.metadata["Name"]
        if not name:
            continue
        licence = licence_string(dist)
        declared = ids_of_distribution(dist)
        verdict, reason = classify_distribution(dist)
        rows.append(
            {
                "name": name,
                "version": dist.version,
                "licence": licence,
                "label": licence_label(licence, declared),
                "copyright": _author_of(dist),
                "keys": declared | set(EXTRA_ATTRIBUTION.get(name, {})),
                "verdict": verdict,
                "reason": reason,
                "notices": notice_files(dist),
            }
        )
    return sorted(rows, key=lambda r: r["name"].lower())


def npm_entries() -> list[dict]:
    """One record per npm package, from package-lock.json.

    A missing lockfile is not an error: this script is run from the backend, and
    the frontend may not have been installed yet. It is worth a warning, because
    a notice that silently omits the frontend would understate the obligation.
    """
    lock = APP_ROOT / "frontend" / "package-lock.json"
    if not lock.exists():
        print(
            "  note: frontend/package-lock.json not found - npm packages omitted",
            file=sys.stderr,
        )
        return []

    data = json.loads(lock.read_text(encoding="utf-8"))
    rows: dict[str, dict] = {}
    for path, meta in (data.get("packages") or {}).items():
        if not path or not meta.get("version"):
            continue
        name = meta.get("name") or path.split("node_modules/")[-1]
        # Keyed by name@version so a package installed at two versions keeps
        # both rows, while the same version reached by two paths is listed once.
        key = f"{name}@{meta['version']}"
        if key in rows:
            continue
        licence = meta.get("license", "")
        declared = ids_of(licence)
        verdict, reason = classify(licence)
        rows[key] = {
            "name": name,
            "version": meta["version"],
            "licence": licence,
            "label": licence_label(licence, declared),
            "copyright": _npm_author(path),
            "keys": declared | set(EXTRA_ATTRIBUTION.get(name, {})),
            "verdict": verdict,
            "reason": reason,
            "notices": [],
        }
    return sorted(rows.values(), key=lambda r: r["name"].lower())


def _npm_author(lock_path: str) -> str:
    """The `author` field from node_modules, when the tree is present."""
    pkg_json = APP_ROOT / "frontend" / lock_path / "package.json"
    if not pkg_json.is_file():
        return ""
    try:
        meta = json.loads(pkg_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    author = meta.get("author")
    if isinstance(author, dict):
        return (author.get("name") or "").strip()
    return (author or "").strip()


def _table(rows: list[dict], name_width: int) -> list[str]:
    lines = [f"{'package':<{name_width}}{'version':<14}licence"]
    for row in rows:
        lines.append(f"{row['name']:<{name_width}}{row['version']:<14}{row['label']}")
    return lines


def main() -> int:
    out = APP_ROOT / "THIRD_PARTY_NOTICES.txt"

    py = python_entries()
    npm = npm_entries()
    rows = py + npm

    # Refuse on anything the policy cannot clear for a release: a copyleft term,
    # or a licence that could not be classified (which would otherwise be listed
    # with no text, understating the obligation).
    unfixable = [r for r in rows if r["verdict"] in {"BLOCKED", "UNKNOWN"}]
    if unfixable:
        print("REFUSING to write: a dependency is blocked or unclassifiable.", file=sys.stderr)
        for row in unfixable:
            print(
                f"  [{row['verdict']}] {row['name']}=={row['version']}  "
                f"{row['reason']}",
                file=sys.stderr,
            )
        return 1

    # Every canonical id the tree needs, and which packages need it.
    needed: dict[str, list[str]] = {}
    for row in rows:
        for key in row["keys"]:
            needed.setdefault(key, []).append(row["name"])

    texts: dict[str, str] = {}
    missing: list[str] = []
    for key in sorted(needed):
        text = canonical_text(key)
        if text is None:
            missing.append(key)
        else:
            texts[key] = text
    if missing:
        print(
            "REFUSING to write: no licence text available for "
            + ", ".join(missing)
            + ".",
            file=sys.stderr,
        )
        print(
            "  Add licenses/<SPDX-ID>.txt (see licenses/README.md), or confirm "
            "the dependency ships its own text.",
            file=sys.stderr,
        )
        return 1

    lines: list[str] = []
    w = lines.append
    w("THIRD-PARTY NOTICES")
    w("=" * 72)
    w("")
    w(f"SquareOne Holdings Agentic Data Analytics - generated {date.today().isoformat()} by")
    w("backend/scripts/write_third_party_notice.py")
    w("")
    w("This product bundles the third-party software listed below. Each package is")
    w("distributed under its own licence. The copyright notices are listed with the")
    w("packages, the full text of every licence is reproduced further down, and any")
    w("notice file a package ships is reproduced verbatim at the end. The copyright")
    w("notices and licence texts MUST be retained in any redistribution, whether")
    w("modified or not.")
    w("")
    w("Product source code is proprietary. Nothing here grants any licence in the")
    w("product's own code.")
    w("")

    w("-" * 72)
    w("PYTHON - backend runtime and tooling")
    w("-" * 72)
    w("")
    lines.extend(_table(py, 34))
    w("")

    if npm:
        w("-" * 72)
        w("NPM - frontend")
        w("-" * 72)
        w("")
        lines.extend(_table(npm, 46))
        w("")

    w("-" * 72)
    w("COPYRIGHT NOTICES")
    w("-" * 72)
    w("")
    for row in py + npm:
        holder = row["copyright"] or "(see package home)"
        w(f"{row['name']} {row['version']} - {holder}")
    w("")

    extra_notes = [
        (row["name"], row["version"], sid, why)
        for row in rows
        for sid, why in EXTRA_ATTRIBUTION.get(row["name"], {}).items()
    ]
    if extra_notes:
        w("-" * 72)
        w("ADDITIONAL ATTRIBUTIONS")
        w("-" * 72)
        w("")
        w("These packages carry material licensed separately from the licence their")
        w("own metadata declares. They are listed under the additional licence(s)")
        w("as well, and its text is reproduced below.")
        w("")
        for name, version, sid, why in extra_notes:
            w(f"  {name} {version} - {sid} ({why})")
        w("")

    w("-" * 72)
    w("FULL LICENSE TEXTS")
    w("-" * 72)
    w("")
    for key in sorted(texts):
        users = sorted(set(needed[key]))
        w(f"==== {key} " + "=" * max(1, 66 - len(key)))
        w(f"applies to: {', '.join(users)}")
        w("")
        w(texts[key])
        w("")

    all_canonical = all_canonical_texts()
    shipped = [
        (row, fname, text)
        for row in py + npm
        for fname, text in row["notices"]
        if keep_notice(fname, text, all_canonical.values())
    ]
    if shipped:
        w("-" * 72)
        w("PACKAGE-SPECIFIC NOTICES (reproduced verbatim)")
        w("-" * 72)
        w("")
        for row, fname, text in shipped:
            w(f"==== {row['name']} {row['version']} - {fname} " + "=" * 8)
            w("")
            w(text)
            w("")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    print(f"wrote {out.relative_to(APP_ROOT)}")
    print(f"  {len(py)} python packages, {len(npm)} npm packages")
    print(f"  {len(texts)} distinct licences, {len(shipped)} package notice files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
