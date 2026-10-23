"""Does the derivation say anything true about the demo rules?

Not a unit test suite - a reading. It prints what `derive()` produces for every
rule file it can find, so a human can see whether the structure it hands the
model is worth writing about. `--check` turns the readings into assertions,
which is what the verification bar runs.

The derivation no longer writes English - the model does that - so what is
printed here is the skeleton: which steps the rule performs, the lines each
one occupies, and the numbers it decides on. Those are the things a machine
description is checked against, so they are the things worth looking at.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.engine.explain import derive  # noqa: E402


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
        for step in outline.steps:
            print(f"  {step.n}. [{step.lines[0]:>3}-{step.lines[1]:>3}]")
        print(f"limits     : {', '.join(l['constant'] for l in outline.limits) or '(none)'}")
        print(f"numbers    : {outline.numbers}")

        if args.check:
            problems = []
            if not outline.steps:
                problems.append("no steps derived")
            if not outline.tables:
                problems.append("no tables found")
            for step in outline.steps:
                if step.lines[0] < 1 or step.lines[1] > len(source.splitlines()):
                    problems.append(f"step {step.n} points outside the file")
            if problems:
                failures += 1
                print(f"  FAIL  {'; '.join(problems)}")

    print(f"\n{failures} file(s) with problems")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
