"""The licence policy for the whole product. One source of truth.

Both the gate (`check_licenses.py`) and the attribution writer
(`write_third_party_notice.py`) import from here, so the verdict a release is
held to and the notice it ships cannot disagree.

WHAT CHANGED FROM THE OLD classify()

The old classifier matched licence strings by substring. That is wrong in three
ways, all of which were reachable:

  * It read `Disclaimer of warranty` as ISC and `permit` as MIT, because `isc`
    and `mit` sit inside those words. A proprietary string could pass.
  * It checked the permissive set *before* the copyleft set, so `MIT AND GPL-3.0`
    came back PERMITTED. `AND` is not `OR`.
  * It only knew the abbreviations, so a dependency whose metadata said
    `GNU General Public License v3.0`, `GPLv3` or `Affero General Public
    License` fell through to UNKNOWN - which the old gate treated as non-fatal.

This version tokenises the string, maps known names (abbreviation *and* spelled
out) to SPDX ids, parses `AND`/`OR`/parentheses with correct precedence, and
evaluates the expression against the policy:

  * BLOCKED  - a copyleft or source-available obligation *binds*. `A OR B` binds
               only if both sides do (you may elect the other option); `A AND B`
               binds if either side does.
  * PERMITTED- every binding option is in the allowed set.
  * REVIEW   - nothing binds copyleft, but a weak-copyleft term (MPL, EPL, ...)
               applies and needs a documented sign-off.
  * UNKNOWN  - no licence could be identified, or an unrecognised term sits
               alongside the ones that could. The gate fails on UNKNOWN: an
               unclassifiable licence is exactly the case a release must not
               wave through.

Categories are sets of SPDX ids, so adding a licence is a one-line change and
there is no second place for the policy to drift to.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# The policy
# ---------------------------------------------------------------------------

# Permissive: attribution only, no copyleft. Safe to bundle.
ALLOWED = frozenset(
    {
        "MIT",
        "MIT-0",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "ISC",
        "PSF-2.0",
        "0BSD",
        "Zlib",
        "Unlicense",
        "CC0-1.0",
        "WTFPL",
    }
)

# Weak copyleft, or a content licence used on a code dependency: per-file
# obligations only, but they need a human to sign off. Non-fatal, but loud.
REVIEW = frozenset(
    {
        "MPL-2.0",
        "EPL-1.0",
        "EPL-2.0",
        "CDDL-1.0",
        "CDDL-1.1",
        "CPL-1.0",
        "Artistic-2.0",
        "MS-PL",
        "MS-RL",
        "NPL-1.1",
        "ZPL-2.1",
        "EUPL-1.2",
        "CC-BY-4.0",
    }
)

# Strong copyleft, source-available, or proprietary. Never bundle.
BLOCKED = frozenset(
    {
        "GPL",  # the family, however the string spells it
        "LGPL",
        "AGPL",
        "SSPL-1.0",
        "BUSL-1.1",
        "Elastic-2.0",
        "CPAL-1.0",
        "OSL-3.0",
        "RPL-1.5",
        "Proprietary",
        "Commercial",
        "UNLICENSED",
    }
)

# ---------------------------------------------------------------------------
# Recognising a licence in free text
# ---------------------------------------------------------------------------

# Ordered: the scanner tries these in turn at each position and takes the first
# match, so a specific name must come before a generic one (lesser-GPL before
# GPL, `apache-2.0` before `apache`). The `(?![\w-])` guard stops `mit` matching
# the start of `mitosis`.
_PATTERNS: list[tuple[str, str]] = [
    # --- strong copyleft and source-available, spelled out ---
    (r"gnu\s+(?:lesser|library)\s+general\s+public\s+license", "LGPL"),
    (r"gnu\s+affero\s+general\s+public\s+license", "AGPL"),
    (r"affero\s+general\s+public\s+license", "AGPL"),
    (r"gnu\s+general\s+public\s+license", "GPL"),
    (r"server\s+side\s+public\s+license", "SSPL-1.0"),
    (r"business\s+source\s+license", "BUSL-1.1"),
    (r"elastic\s+license", "Elastic-2.0"),
    # --- weak copyleft and content licences, spelled out ---
    (r"mozilla\s+public\s+license", "MPL-2.0"),
    (r"eclipse\s+public\s+license", "EPL-2.0"),
    (r"common\s+development\s+and\s+distribution\s+license", "CDDL-1.0"),
    (r"creative\s+commons\s+attribution", "CC-BY-4.0"),
    # --- permissive, spelled out ---
    (r"apache\s+software\s+license", "Apache-2.0"),
    (r"apache\s+license", "Apache-2.0"),
    (r"python\s+software\s+foundation\s+license", "PSF-2.0"),
    (r"modified\s+bsd\s+license", "BSD-3-Clause"),
    (r"bsd\s+license", "BSD-3-Clause"),
    (r"mit\s+license", "MIT"),
    (r"isc\s+license", "ISC"),
    # --- SPDX ids, family forms before their generic token ---
    (r"agpl\s*-?\s*v?\s*\d(?:\.\d+)?(?:-or-later|-only)?(?![\w-])", "AGPL"),
    (r"lgpl\s*-?\s*v?\s*\d(?:\.\d+)?(?:-or-later|-only)?(?![\w-])", "LGPL"),
    (r"gpl\s*-?\s*v?\s*\d(?:\.\d+)?(?:-or-later|-only)?(?![\w-])", "GPL"),
    (r"sspl-?1\.0", "SSPL-1.0"),
    (r"busl-?1\.1", "BUSL-1.1"),
    (r"elastic-?2\.0", "Elastic-2.0"),
    (r"bsd-?3-?clause", "BSD-3-Clause"),
    (r"bsd-?2-?clause", "BSD-2-Clause"),
    (r"apache-?2\.0", "Apache-2.0"),
    (r"mpl-?2\.0", "MPL-2.0"),
    (r"epl-?[12]\.0", "EPL-2.0"),
    (r"cddl-?1\.[01]", "CDDL-1.0"),
    (r"cpl-?1\.0", "CPL-1.0"),
    (r"artistic-?2\.0", "Artistic-2.0"),
    (r"osl-?3\.0", "OSL-3.0"),
    (r"rpl-?1\.5", "RPL-1.5"),
    (r"npl-?1\.1", "NPL-1.1"),
    (r"zpl-?2\.1", "ZPL-2.1"),
    (r"eupl-?1\.\d", "EUPL-1.2"),
    (r"ms-pl(?![\w-])", "MS-PL"),
    (r"ms-rl(?![\w-])", "MS-RL"),
    (r"cc-by-?\d(?:\.\d)?", "CC-BY-4.0"),
    (r"cc0-?1\.0", "CC0-1.0"),
    (r"psf-?2\.0", "PSF-2.0"),
    (r"0bsd", "0BSD"),
    (r"mit-?0(?![\w-])", "MIT-0"),
    (r"wtfpl", "WTFPL"),
    (r"unlicense(?![\w-])", "Unlicense"),
    (r"zlib(?![\w-])", "Zlib"),
    (r"proprietary(?![\w-])", "Proprietary"),
    (r"commercial(?![\w-])", "Commercial"),
    (r"unlicensed(?![\w-])", "UNLICENSED"),
    # --- bare abbreviations (last, and guarded so they cannot match mid-word) ---
    (r"agpl(?![\w-])", "AGPL"),
    (r"lgpl(?![\w-])", "LGPL"),
    (r"gpl(?![\w-])", "GPL"),
    (r"sspl(?![\w-])", "SSPL-1.0"),
    (r"busl(?![\w-])", "BUSL-1.1"),
    (r"elastic(?![\w-])", "Elastic-2.0"),
    (r"apache(?![\w-])", "Apache-2.0"),
    (r"mpl(?![\w-])", "MPL-2.0"),
    (r"epl(?![\w-])", "EPL-2.0"),
    (r"cddl(?![\w-])", "CDDL-1.0"),
    (r"bsd(?![\w-])", "BSD-3-Clause"),
    (r"mit(?![\w-])", "MIT"),
    (r"isc(?![\w-])", "ISC"),
    (r"iscl(?![\w-])", "ISC"),
    (r"psf(?![\w-])", "PSF-2.0"),
]

_COMPILED = [(re.compile(pattern, re.IGNORECASE), sid) for pattern, sid in _PATTERNS]

# Descriptive words that are never a licence id. A leftover word not in here is
# "suspicious" - it may be an id we do not know - and downgrades the verdict to
# UNKNOWN rather than guessing.
_GLUE = frozenset(
    """
    and or with the a an of for to in on at by is are be as
    license licence licensed version dual v ver
    public general software foundation open source initiative clause style
    exception only later
    copyright c year author authors email all rights reserved
    see file https http www org com net
    """.split()
)

_CATEGORISED = ALLOWED | REVIEW | BLOCKED


class _ParseError(Exception):
    pass


def _is_suspicious(word: str) -> bool:
    """A leftover token that could be a licence id we do not recognise."""
    low = word.lower()
    if low in _GLUE or re.fullmatch(r"[\d.]+", low):
        return False
    if re.fullmatch(r"(?:19|20)\d\d", low):  # a year
        return False
    if re.search(r"\d", low) and re.search(r"[a-z]", low):
        return True  # e.g. epl-2.0, zpl-2.1
    if word.isupper() and 2 <= len(word) <= 6:
        return True  # e.g. GPL, CDDL, OSL
    return False


def _tokenize(text: str) -> list[tuple[str, str]]:
    """Split into ('id', spdx), ('and', ''), ('or', ''), ('(', ''), (')', ''), ('junk', word)."""
    text = re.sub(r"[,;/|]+", " ", text)
    tokens: list[tuple[str, str]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "(":
            tokens.append(("(", ""))
            i += 1
            continue
        if ch == ")":
            tokens.append((")", ""))
            i += 1
            continue

        matched = False
        for pattern, sid in _COMPILED:
            m = pattern.match(text, i)
            if m:
                tokens.append(("id", sid))
                i = m.end()
                matched = True
                break
        if matched:
            continue

        m = re.match(r"(and|or)(?![\w-])", text[i:], re.IGNORECASE)
        if m:
            tokens.append((m.group(1).lower(), ""))
            i += m.end()
            continue

        m = re.match(r"[A-Za-z0-9._+-]+", text[i:])
        if m:
            tokens.append(("junk", m.group(0)))
            i += m.end()
            continue

        i += 1
    return tokens


def _parse(tokens: list[tuple[str, str]]):
    """Recursive-descent parse of the id tokens, AND binding tighter than OR."""
    seq = [(kind, val) for kind, val in tokens if kind in {"id", "and", "or", "(", ")"}]
    pos = 0

    def peek() -> str | None:
        return seq[pos][0] if pos < len(seq) else None

    def parse_or():
        nonlocal pos
        node = parse_and()
        while peek() == "or":
            pos += 1
            node = ("or", node, parse_and())
        return node

    def parse_and():
        nonlocal pos
        node = parse_atom()
        while peek() in ("and", "id", "("):
            if peek() == "and":
                pos += 1
            node = ("and", node, parse_atom())
        return node

    def parse_atom():
        nonlocal pos
        kind = peek()
        if kind == "(":
            pos += 1
            node = parse_or()
            if peek() != ")":
                raise _ParseError("unbalanced parenthesis")
            pos += 1
            return node
        if kind == "id":
            node = ("id", seq[pos][1])
            pos += 1
            return node
        raise _ParseError(f"unexpected token {kind!r}")

    if not seq:
        return None
    node = parse_or()
    if pos != len(seq):
        raise _ParseError("trailing tokens")
    return node


def _blocked(node) -> bool:
    if node[0] == "id":
        return node[1] in BLOCKED
    op, left, right = node
    if op == "or":
        return _blocked(left) and _blocked(right)
    return _blocked(left) or _blocked(right)


def _permissive(node) -> bool:
    if node[0] == "id":
        return node[1] in ALLOWED
    op, left, right = node
    if op == "or":
        return _permissive(left) or _permissive(right)
    return _permissive(left) and _permissive(right)


def _review(node) -> bool:
    if node[0] == "id":
        return node[1] in REVIEW
    op, left, right = node
    if op == "or":
        return _review(left) and _review(right)
    return _review(left) or _review(right)


def classify(raw: str) -> tuple[str, str]:
    """Return (verdict, reason) for one licence string."""
    text = (raw or "").strip()
    if not text:
        return "UNKNOWN", "no license metadata"

    tokens = _tokenize(text)
    ids = {value for kind, value in tokens if kind == "id"}
    if not ids:
        return "UNKNOWN", f"could not classify: {text[:60]!r}"

    try:
        node = _parse(tokens)
    except _ParseError as exc:
        return "UNKNOWN", f"unparseable expression ({exc}): {text[:60]!r}"
    if node is None:
        return "UNKNOWN", f"could not classify: {text[:60]!r}"

    if _blocked(node):
        return "BLOCKED", f"copyleft/source-available: {text[:60]!r}"

    if any(kind == "junk" and _is_suspicious(value) for kind, value in tokens):
        return "UNKNOWN", f"unrecognised term alongside {', '.join(sorted(ids))}: {text[:60]!r}"

    if _permissive(node) and not _review(node):
        return "PERMITTED", "permissive license present"
    if _review(node):
        return "REVIEW", f"weak copyleft or needs sign-off: {text[:60]!r}"
    return "UNKNOWN", f"no binding permissive option: {text[:60]!r}"


def ids_of(raw: str) -> set[str]:
    """Every SPDX id named in a licence string, ignoring AND/OR structure.

    Used for attribution, where naming every licence a package mentions is the
    safe superset: a conjunctive term binds, and a disjunctive one may be
    elected.
    """
    return {value for kind, value in _tokenize(raw or "") if kind == "id"}


# ---------------------------------------------------------------------------
# Reading a licence off an installed Python distribution
# ---------------------------------------------------------------------------


def classifier_licences(dist) -> list[str]:
    """The licence name(s) from the Trove classifiers, original case."""
    leaves = []
    for classifier in dist.metadata.get_all("Classifier") or []:
        if classifier.startswith("License ::"):
            leaves.append(classifier.split("::")[-1].strip())
    return leaves


def classifier_license(dist) -> str:
    """The first licence classifier, or "" - the display fallback."""
    leaves = classifier_licences(dist)
    return leaves[0] if leaves else ""


def licence_string(dist) -> str:
    """The most specific licence string for a distribution.

    PEP 639 `License-Expression` first, then the free-text `License` field, then
    the Trove classifier. The `License` field is sometimes a copyright line
    (polars) or simply vague ("Dual License", as python-dateutil is), which is
    why the classifier is consulted too.
    """
    expr = dist.metadata.get("License-Expression")
    if expr:
        return str(expr).strip()
    raw = dist.metadata.get("License")
    if raw and re.sub(r"[^a-z0-9.+-]", "", str(raw).lower()) not in {"", "unknown"}:
        return str(raw).strip()
    return classifier_license(dist)


def _all_licence_strings(dist) -> list[str]:
    """Every licence string a distribution offers, most specific first."""
    strings = [licence_string(dist)]
    strings.extend(classifier_licences(dist))
    return [s for s in strings if s]


def classify_distribution(dist) -> tuple[str, str]:
    """Classify a distribution, falling back to its classifiers.

    The `License` field can be unusable on its own, so the first string that
    resolves to a real verdict wins. A BLOCKED verdict is never overridden.
    """
    for raw in _all_licence_strings(dist):
        verdict, reason = classify(raw)
        if verdict != "UNKNOWN":
            return verdict, reason
    return "UNKNOWN", f"could not classify: {licence_string(dist)[:60]!r}"


def ids_of_distribution(dist) -> set[str]:
    """Every SPDX id any of the distribution's licence strings names."""
    ids: set[str] = set()
    for raw in _all_licence_strings(dist):
        ids |= ids_of(raw)
    return ids
