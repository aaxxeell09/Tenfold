"""Smoke test for a candidate rules.py: seven synthetic windows, domain-validated output, no exceptions, the poses
two of them must read as, and a latency ceiling so a patch cannot buy accuracy with work the live camera loop
cannot afford.

    python loop/smoke.py                      # classifier/rules.py in the current directory
    python loop/smoke.py --rules path/to/rules.py

Exit 0 = every case returned a valid GestureState fast enough. Exit 1 = at least one case failed; each failure is
printed as one line the patch agent can act on. The guard runs this in a subprocess with a timeout.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from classifier.schema import validate_state  # noqa: E402
from loop.synth import smoke_cases  # noqa: E402

# The live loop has 100 ms per frame (SPEC section 4.1) and MediaPipe takes most of it; the classifier gets 5 ms.
LATENCY_P95_MS = float(os.environ.get("TENFOLD_CLASSIFY_P95_MS", "5"))
# Failures that describe a wrong pose rather than a crash or an out-of-domain value. The guard rejects both kinds;
# make doctor only warns on these, since the running rules can carry one until the critic fixes it.
EXPECTATION_PREFIXES = ("smoke apart_7x8: expected",)


def load(path: Path):
    spec = importlib.util.spec_from_file_location("tenfold_smoke_rules", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def p95_ms(classify, window, runs: int = 200, max_seconds: float = 3.0) -> float:
    """95th percentile of classify(window) wall time after a short warm-up; stops early on a slow classifier."""
    for _ in range(5):
        classify(window)
    times: list[float] = []
    start = time.perf_counter()
    for _ in range(runs):
        t0 = time.perf_counter()
        classify(window)
        times.append((time.perf_counter() - t0) * 1000.0)
        if time.perf_counter() - start > max_seconds:
            break
    times.sort()
    return times[min(len(times) - 1, int(0.95 * len(times)))]


def run(path: Path) -> list[str]:
    failures: list[str] = []
    try:
        mod = load(path)
    except Exception as e:
        return [f"import: {path} failed to import | cause: {type(e).__name__}: {e} | fix: make the file importable"]
    classify = getattr(mod, "classify", None)
    if classify is None:
        return ["import: no classify(window) function | cause: it was renamed or removed | fix: keep classify(window)"]
    cases = smoke_cases()
    for name, window in cases.items():
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
        if name == "apart_7x8" and not (getattr(state, "method", None) == "6-10" and getattr(state, "left", None) == 7
                                        and getattr(state, "right", None) == 8 and not getattr(state, "contact", True)):
            failures.append(
                f"smoke apart_7x8: expected fingers 7 and 8 apart (method '6-10', left 7, right 8, contact False), got "
                f"method {getattr(state, 'method', None)!r}, left {getattr(state, 'left', None)!r}, right "
                f"{getattr(state, 'right', None)!r}, contact {getattr(state, 'contact', None)!r} | cause: a still, "
                "confident two-hand pose with fingertips 7 and 8 clearly apart was not read as those two fingers apart, "
                "so the tutor cannot tell the child to bring them together | fix: answer 6-10 with contact False for "
                "two distinct fingers that are simply apart, and refuse only what is not a 6-10 pose")
    if not failures:
        ms = p95_ms(classify, cases["five_valid_contact"])
        if ms > LATENCY_P95_MS:
            failures.append(f"latency: classify() p95 is {ms:.2f} ms on a 5-frame window, limit {LATENCY_P95_MS:g} ms | "
                            "cause: too much work per call for a live 30 fps camera | "
                            "fix: compute each feature once per frame, no nested loops over samples or frames beyond the window")
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
