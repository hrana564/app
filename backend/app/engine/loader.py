"""Load a rule .py file and expose its run(ctx) callable."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

RULES_DIR = Path(__file__).resolve().parent.parent.parent / "rules"


def resolve_rule_path(source_file: str, rules_dir: Path | None = None) -> Path:
    base = rules_dir or RULES_DIR
    name = Path(source_file).name
    path = (base / name).resolve()
    if not str(path).startswith(str(base.resolve())):
        raise ValueError(f"Rule path escapes rules directory: {source_file}")
    if not path.exists():
        raise FileNotFoundError(f"Rule file not found: {path}")
    return path


def load_rule(source_file: str, rules_dir: Path | None = None):
    """Import the module fresh each call so edits take effect without a restart."""
    path = resolve_rule_path(source_file, rules_dir)
    module_name = f"_rules.{path.stem}"

    # Drop any cached module so a re-run picks up edited source.
    for key in [k for k in sys.modules if k == module_name or k.startswith(module_name + ".")]:
        del sys.modules[key]

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load rule module from {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    if not hasattr(module, "run"):
        raise AttributeError(
            f"Rule {path.name} has no run(ctx) function. "
            "Every rule must define: def run(ctx) -> dict"
        )
    return module


def read_source(source_file: str, rules_dir: Path | None = None) -> str:
    return resolve_rule_path(source_file, rules_dir).read_text(encoding="utf-8")


def write_source(source_file: str, code: str, rules_dir: Path | None = None) -> Path:
    """Persist a rule to disk. Filenames are derived from a DB id, never user text."""
    base = rules_dir or RULES_DIR
    base.mkdir(parents=True, exist_ok=True)
    name = Path(source_file).name
    if not name.endswith(".py") or not name.startswith("rule_"):
        raise ValueError("Rule filenames must look like rule_<id>.py")
    path = (base / name).resolve()
    if not str(path).startswith(str(base.resolve())):
        raise ValueError("Refusing to write outside the rules directory")
    path.write_text(code, encoding="utf-8")
    return path


def rule_path_for(rule_id: int, rules_dir: Path | None = None) -> str:
    return f"rule_{rule_id}.py"


def describe_module(module: Any) -> dict[str, Any]:
    """Optional metadata a rule file may declare."""
    return {
        "name": getattr(module, "NAME", None),
        "description": getattr(module, "DESCRIPTION", None),
        "datasources": list(getattr(module, "DATASOURCES", []) or []),
    }