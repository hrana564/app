"""Load a rule .py file and expose its run(ctx) callable."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

RULES_DIR = Path(__file__).resolve().parent.parent.parent / "rules"


def resolve_rule_path(source_file: str, rules_dir: Path | None = None) -> Path:
    base = (rules_dir or RULES_DIR).resolve()
    # Take only the filename: the DB stores a name, never a path, so a crafted
    # value like "../../secrets.py" cannot escape the rules directory.
    name = Path(str(source_file)).name
    path = (base / name).resolve()
    if path.parent != base:
        raise ValueError(f"Rule path escapes rules directory: {source_file}")
    if not path.exists():
        raise FileNotFoundError(f"Rule file not found: {name}")
    return path


def _import_from(path: Path, module_name: str):
    """Import a module from a path, discarding any cached copy first."""
    for key in [k for k in sys.modules if k == module_name or k.startswith(module_name + ".")]:
        del sys.modules[key]

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load rule module from {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _require_run(module, path: Path):
    if not hasattr(module, "run"):
        raise AttributeError(
            f"Rule {path.name} has no run(ctx) function. "
            "Every rule must define: def run(ctx) -> dict"
        )
    return module


def load_rule(source_file: str, rules_dir: Path | None = None):
    """Import the module fresh each call so edits take effect without a restart."""
    path = resolve_rule_path(source_file, rules_dir)
    module = _import_from(path, f"_rules.{path.stem}")
    return _require_run(module, path)


def load_rule_at(path: Path):
    """Import a rule from an explicit path.

    Replay uses this to run the copy frozen into a run folder rather than
    whatever version happens to sit in rules/ today - which is the only way a
    replayed run reproduces the original.
    """
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Rule file not found: {path}")
    module = _import_from(path, f"_replay.{path.stem}")
    return _require_run(module, path)


def read_source(source_file: str, rules_dir: Path | None = None) -> str:
    return resolve_rule_path(source_file, rules_dir).read_text(encoding="utf-8")


def write_source(source_file: str, code: str, rules_dir: Path | None = None) -> Path:
    """Persist a rule to disk. Filenames look like LAD-CA-03_v2.py."""
    base = (rules_dir or RULES_DIR).resolve()
    base.mkdir(parents=True, exist_ok=True)
    name = Path(str(source_file)).name
    if not name.endswith(".py") or "_v" not in name:
        raise ValueError(
            f"Rule filenames must look like <CODE>_v<version>.py, got {name!r}"
        )
    path = (base / name).resolve()
    if path.parent != base:
        raise ValueError("Refusing to write outside the rules directory")
    path.write_text(code, encoding="utf-8")
    return path