"""Evaluate classifier/rules.py on this worktree's train copy (data/train.jsonl). No W&B, no network, no held-out.

    python loop/train_eval.py              # metrics, the 6 worst classes, up to 8 failing samples
    python loop/train_eval.py --class 7x8  # failures of one class only (names as in per_class)

The patch agent uses it to test a hypothesis before and after editing rules.py. The blind arm does not have it.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from classifier import features  # noqa: E402
from classifier.schema import GestureState, window_from_json  # noqa: E402
from eval import scorers  # noqa: E402


def load_classify(path: Path):
    spec = importlib.util.spec_from_file_location("tenfold_train_eval_rules", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod.classify


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--class", dest="cls")
    ap.add_argument("--limit", type=int, default=8)
    a = ap.parse_args()
    data = ROOT / "data" / "train.jsonl"
    if not data.exists():
        print("no data/train.jsonl in this worktree: reason from the code instead")
        return 1
    classify = load_classify(ROOT / "classifier" / "rules.py")
    samples = [json.loads(l) for l in data.read_text().splitlines() if l.strip()]
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
    m = scorers.aggregate(rows, bootstrap=0)
    print(f"train: {m['n_samples']} samples, {m['n_holds']} holds")
    for k in scorers.METRICS:
        v = m.get(k)
        print(f"  {k:28s} {'n/a' if v is None else f'{v:.3f}'}")
    worst = sorted(((c, v) for c, v in m["per_class"].items() if v is not None), key=lambda kv: kv[1])[:6]
    print("  worst classes: " + ", ".join(f"{c}={v:.2f}" for c, v in worst))
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
