"""Does the derivation say anything true about the demo rules?

Not a unit test suite - a reading. It prints what `derive()` produces for every
rule file it can find, so a human can see whether the sentences are worth
showing to a head of audit. `--check` turns the readings into assertions, which
is what the verification bar runs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.engine.explain import check, derive, reading_grade  # noqa: E402


def rules() -> list[Path]:
    directory = BACKEND / "rules"
    return sorted(p for p in directory.glob("*.py") if not p.name.startswith("_"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    failures = 0
    for path in rules():
        source = path.read_text(encoding="utf-8")
        outline = derive(source)

        print(f"\n=== {path.name} ===")
        print(f"title      : {outline.title or '(none)'}")
        print(f"tables     : {', '.join(outline.tables) or '(none)'}")
        print(f"breach     : {outline.breach or '(not derived)'}")
        for step in outline.steps:
            flag = "" if step.explainable else "   [could not describe]"
            print(f"  {step.n}. [{step.lines[0]:>3}-{step.lines[1]:>3}] {step.text}{flag}")
        print(f"limits     : {', '.join(l['constant'] for l in outline.limits) or '(none)'}")
        print(f"cap        : {outline.cap}")
        print(f"numbers    : {outline.numbers}")
        print(f"grade      : {reading_grade(outline.breach or '')}")

        if args.check:
            problems = []
            if not outline.steps:
                problems.append("no steps derived")
            if not outline.tables:
                problems.append("no tables found")
            if not outline.breach:
                problems.append("no breach condition derived")
            for step in outline.steps:
                if step.lines[0] < 1 or step.lines[1] > len(source.splitlines()):
                    problems.append(f"step {step.n} points outside the file")
            # The derived wording must itself survive the gates it will apply
            # to a model's wording. If it cannot, it is not a fallback.
            prose = {
                "breach_condition": outline.breach or "",
                "steps": [
                    {"n": s.n, "text": s.text, "lines": list(s.lines)}
                    for s in outline.steps
                ],
            }
            gates = check(outline, prose, len(source.splitlines()))
            for gate in gates:
                if not gate.ok and gate.name != "readability":
                    problems.append(f"{gate.name}: {gate.detail}")

            if problems:
                failures += 1
                print(f"  FAIL  {'; '.join(problems)}")

    print(f"\n{failures} file(s) with problems")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
