"""Smoke test for a candidate rules.py: six synthetic windows, domain-validated output, no exceptions.

    python loop/smoke.py                      # classifier/rules.py in the current directory
    python loop/smoke.py --rules path/to/rules.py

Exit 0 = every case returned a valid GestureState. Exit 1 = at least one case failed; each failure is printed
as one line the patch agent can act on. The guard runs this in a subprocess with a timeout.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from classifier.schema import validate_state  # noqa: E402
from loop.synth import smoke_cases  # noqa: E402


def load(path: Path):
    spec = importlib.util.spec_from_file_location("tenfold_smoke_rules", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def run(path: Path) -> list[str]:
    failures: list[str] = []
    try:
        mod = load(path)
    except Exception as e:
        return [f"import: {path} failed to import | cause: {type(e).__name__}: {e} | fix: make the file importable"]
    classify = getattr(mod, "classify", None)
    if classify is None:
        return ["import: no classify(window) function | cause: it was renamed or removed | fix: keep classify(window)"]
    for name, window in smoke_cases().items():
        try:
            state = classify(window)
        except Exception as e:
            failures.append(f"smoke {name}: classify() raised | cause: {type(e).__name__}: {e} | fix: handle this input and return GestureState.unknown() when the geometry is unusable")
            continue
        for p in validate_state(state):
            failures.append(f"smoke {name}: {p} | cause: out-of-domain output | fix: return values inside the GestureState domain")
        if name in ("both_absent", "one_hand", "degenerate", "nan") and getattr(state, "method", None) != "unknown":
            failures.append(f"smoke {name}: expected method 'unknown', got {state.method!r} | cause: unusable geometry was classified | fix: return GestureState.unknown() when both hands are not present or the geometry is degenerate")
        if name == "five_valid_contact" and getattr(state, "method", None) == "unknown":
            failures.append("smoke five_valid_contact: expected a 6-10 state, got unknown | cause: a valid two-hand contact was refused | fix: do not refuse clean input")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", default=str(Path.cwd() / "classifier" / "rules.py"))
    args = ap.parse_args()
    failures = run(Path(args.rules).resolve())
    for f in failures:
        print("SMOKE_FAIL " + f)
    print("SMOKE_OK" if not failures else f"SMOKE_FAILED {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
