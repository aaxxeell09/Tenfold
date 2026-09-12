"""Evaluate classifier/rules.py on this worktree's train copy (data/train.jsonl). No W&B, no network, no held-out.

    python loop/train_eval.py                                          # metrics, 6 worst classes, up to 8 failing samples
    python loop/train_eval.py --class 7x8                              # failures of one class only (names as in per_class)
    python loop/train_eval.py --set CONTACT_THRESHOLD=0.3              # same report with a module constant overridden
    python loop/train_eval.py --sweep CONTACT_THRESHOLD=0.2,0.25,0.3   # one metrics line per value, in one call

--set and --sweep change the loaded module only, never the file, and only reach constants that classify() reads
at call time. The patch agent uses this to test a hypothesis before and after editing rules.py. The blind arm
does not have it.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from classifier import features  # noqa: E402
from classifier.schema import GestureState, window_from_json  # noqa: E402
from eval import scorers  # noqa: E402

CONSTANT = re.compile(r"[A-Z][A-Z0-9_]*")


def load_rules(path: Path):
    spec = importlib.util.spec_from_file_location("tenfold_train_eval_rules", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_classify(path: Path):
    return load_rules(path).classify


def numeric_constants(mod) -> dict[str, float]:
    return {k: v for k, v in vars(mod).items()
            if CONSTANT.fullmatch(k) and isinstance(v, (int, float)) and not isinstance(v, bool)}


def parse_assignment(text: str, mod) -> tuple[str, list[float]]:
    """NAME=V or NAME=V1,V2,... -> (NAME, values). Exits with a usable message on anything else."""
    name, sep, raw = text.partition("=")
    name = name.strip()
    known = numeric_constants(mod)
    if not sep or name not in known:
        sys.exit(f"train_eval: {text!r} is not NAME=VALUE for a numeric constant of rules.py; "
                 f"available: {', '.join(f'{k}={v}' for k, v in known.items()) or 'none'}")
    values: list[float] = []
    for part in raw.split(","):
        part = part.strip()
        try:
            values.append(int(part) if re.fullmatch(r"-?\d+", part) and isinstance(known[name], int) else float(part))
        except ValueError:
            sys.exit(f"train_eval: {part!r} in {text!r} is not a number")
    return name, values


def frame_summary(sample: dict) -> list[dict]:
    out = []
    for left, right in features.both_present_frames(window_from_json(sample["window"])):
        try:
            lf, rf, dist = features.nearest_pair(left, right)
            out.append({"pair": [lf, rf], "dist": round(dist, 3),
                        "conf": [round(left.detection_conf, 2), round(right.detection_conf, 2)]})
        except (ValueError, TypeError):
            out.append({"pair": None})
    return out


def evaluate(classify, samples: list[dict]) -> tuple[dict, list[dict]]:
    rows = []
    for s in samples:
        try:
            state = classify(window_from_json(s["window"]))
            out = state.to_dict() if isinstance(state, GestureState) else None
            err = None if out is not None else f"returned {type(state).__name__}"
        except Exception as e:
            out, err = None, f"{type(e).__name__}: {e}"
        rows.append({"id": s["id"], "hold_id": s.get("hold_id", s["id"]), "kind": s.get("kind", "positive"),
                     "target": s["label"], "output": out, "error": err, "sample": s})
    return scorers.aggregate(rows, bootstrap=0), rows


def worst(m: dict, n: int = 6) -> str:
    items = sorted(((c, v) for c, v in m["per_class"].items() if v is not None), key=lambda kv: kv[1])[:n]
    return ", ".join(f"{c}={v:.2f}" for c, v in items)


def fmt(v) -> str:
    return "n/a" if v is None else f"{v:.3f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--class", dest="cls")
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--set", dest="sets", action="append", default=[], metavar="NAME=VALUE",
                    help="override a numeric constant of rules.py for this run (repeatable)")
    ap.add_argument("--sweep", metavar="NAME=V1,V2,...", help="evaluate several values of one constant, one line each")
    a = ap.parse_args()
    data = ROOT / "data" / "train.jsonl"
    if not data.exists():
        print("no data/train.jsonl in this worktree: reason from the code instead")
        return 1
    mod = load_rules(ROOT / "classifier" / "rules.py")
    for text in a.sets:
        name, values = parse_assignment(text, mod)
        if len(values) != 1:
            sys.exit(f"train_eval: --set takes one value, got {text!r}; use --sweep for several")
        setattr(mod, name, values[0])
    samples = [json.loads(l) for l in data.read_text().splitlines() if l.strip()]

    if a.sweep:
        name, values = parse_assignment(a.sweep, mod)
        file_value = getattr(mod, name)
        overrides = f" with {', '.join(a.sets)}" if a.sets else ""
        print(f"train: {len(samples)} samples; sweeping {name} (currently {file_value}){overrides}")
        for v in values:
            setattr(mod, name, v)
            m, _ = evaluate(mod.classify, samples)
            print(f"  {name}={v}  " + "  ".join(f"{k}={fmt(m.get(k))}" for k in scorers.METRICS) + f"  worst: {worst(m, 3)}")
        return 0

    m, rows = evaluate(mod.classify, samples)
    print(f"train: {m['n_samples']} samples, {m['n_holds']} holds" + (f" (overrides: {', '.join(a.sets)})" if a.sets else ""))
    for k in scorers.METRICS:
        print(f"  {k:28s} {fmt(m.get(k))}")
    print("  worst classes: " + worst(m))
    failed = [r for r in rows if not scorers.exact_match(r["output"], r["target"])]
    if a.cls:
        failed = [r for r in failed if scorers.class_of(r["target"], r["kind"]) == a.cls]
    print(f"\nfailing samples: {len(failed)} (showing {min(len(failed), a.limit)})")
    for r in failed[: a.limit]:
        print(json.dumps({"id": r["id"], "class": scorers.class_of(r["target"], r["kind"]), "expected": r["target"],
                          "predicted": r["output"], "error": r["error"], "frames_oldest_first": frame_summary(r["sample"])}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
