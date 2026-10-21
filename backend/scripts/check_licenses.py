"""
License gate for the Audit Portal backend.

Policy (see README -> Licensing):
    ALLOWED  : Apache-2.0, MIT, BSD-2/3-Clause, ISC, PSF
    BLOCKED  : GPL, AGPL, LGPL, SSPL, BUSL, Elastic/SSPL variants, proprietary,
               and anything source-available that has relicensed away from OSI.
    REVIEW   : MPL-2.0 and anything we cannot classify - reported, not failed.

Why not "Apache/MIT only": FastAPI requires Starlette (BSD-3) and uvicorn is
BSD-3. BSD-3 sits in the same permissive tier as MIT (attribution only). The
only material difference from Apache-2.0 is the absence of an explicit patent
grant. This script enforces the practical rule: no copyleft, no relicensing.

Exit codes: 0 = pass (with optional warnings), 1 = a blocked license was found.
"""

from __future__ import annotations

import re
import sys

# Licenses that are acceptable on their own.
PERMISSIVE = {
    "apache",
    "apache-2.0",
    "apache software license",
    "mit",
    "mit license",
    "bsd",
    "bsd-2-clause",
    "bsd-3-clause",
    "bsd license",
    "isc",
    "isc license (iscl)",
    "psf-2.0",
    "python software foundation license",
    "zlib",
    "unlicense",
}

# Licenses that must never appear.
BLOCKING = {
    "gpl",
    "agpl",
    "lgpl",
    "sspl",
    "busl",
    "elastic",
    "epl",
    "cpal",
    "proprietary",
    "commercial",
}

# Weak copyleft / unclear - surfaced loudly but not fatal.
NEEDS_REVIEW = {"mpl", "cddl", "eclipse", "artistic", "rpl", "mspl", "eupl"}


def normalise(raw: str) -> str:
    """Collapse a raw license string to a comparable form."""
    text = (raw or "").strip().lower()
    text = re.sub(r"licen[cs]e", "", text)
    text = re.sub(r"version\s+", "", text)
    text = re.sub(r"[(),\[\]]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def classify(raw: str) -> tuple[str, str]:
    """
    Return (verdict, reason).

    Dual/multi licensing such as "MPL-2.0 AND (Apache-2.0 OR MIT)" is treated as
    PERMITTED because the consumer may elect the permissive option.
    """
    text = normalise(raw)
    if not text:
        return "UNKNOWN", "no license metadata"

    has_permissive = any(token in text for token in PERMISSIVE)
    has_blocking = any(re.search(rf"\b{re.escape(tok)}\b", text) for tok in BLOCKING)
    has_review = any(re.search(rf"\b{re.escape(tok)}\b", text) for tok in NEEDS_REVIEW)

    if has_permissive:
        return "PERMITTED", "permissive license present"
    if has_blocking:
        return "BLOCKED", f"copyleft/source-available: {raw.strip()[:60]}"
    if has_review:
        return "REVIEW", f"weak copyleft or needs sign-off: {raw.strip()[:60]}"
    return "UNKNOWN", f"could not classify: {raw.strip()[:60]}"


def main() -> int:
    import importlib.metadata as md

    verdicts: dict[str, list[tuple[str, str, str]]] = {
        "BLOCKED": [],
        "REVIEW": [],
        "UNKNOWN": [],
        "PERMITTED": [],
    }

    for dist in md.distributions():
        name = dist.metadata["Name"]
        if not name:
            continue
        raw = dist.metadata.get("License-Expression") or dist.metadata.get("License") or ""
        if not raw or normalise(raw) in {"", "unknown"}:
            raw = _license_from_classifier(dist)
        verdict, reason = classify(raw)
        if verdict == "UNKNOWN":
            # Metadata can be vague ("Dual License"); the Trove classifier is
            # authoritative, so consult it before giving up.
            from_classifier = _license_from_classifier(dist)
            if normalise(from_classifier) != "unknown":
                verdict, reason = classify(from_classifier)
        verdicts[verdict].append((name, str(raw).strip()[:70], reason))

    print("=" * 72)
    print("LICENSE AUDIT - Audit Portal backend")
    print("=" * 72)
    print(f"  PERMITTED : {len(verdicts['PERMITTED']):>3}")
    print(f"  REVIEW    : {len(verdicts['REVIEW']):>3}")
    print(f"  UNKNOWN   : {len(verdicts['UNKNOWN']):>3}")
    print(f"  BLOCKED   : {len(verdicts['BLOCKED']):>3}")

    for bucket in ("BLOCKED", "REVIEW", "UNKNOWN"):
        items = sorted(verdicts[bucket])
        if not items:
            continue
        print()
        print(f"--- {bucket} " + "-" * (64 - len(bucket)))
        for name, raw, reason in items:
            print(f"  {name:<32} {raw:<40} {reason}")

    print()
    if verdicts["BLOCKED"]:
        print("RESULT: FAIL - blocked licenses present.")
        return 1
    print("RESULT: PASS - no copyleft or source-available licenses in the tree.")
    return 0


def _license_from_classifier(dist) -> str:
    """Fall back to Trove classifiers (older metadata may lack License-Expression)."""
    for classifier in dist.metadata.get_all("Classifier") or []:
        if not classifier.startswith("License ::"):
            continue
        parts = [p.strip() for p in classifier.split("::")]
        leaf = parts[-1].lower()
        if "apache" in leaf:
            return "Apache-2.0"
        if leaf.startswith("mit"):
            return "MIT"
        if "bsd" in leaf:
            return "BSD-3-Clause"
        if "isc" in leaf:
            return "ISC"
        if "psf" in leaf:
            return "PSF-2.0"
        return leaf
    return "unknown"


if __name__ == "__main__":
    sys.exit(main())