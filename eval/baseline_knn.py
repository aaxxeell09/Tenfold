"""kNN reference line: a learned classifier on the frozen features, trained on train, scored like the rules.

    python eval/baseline_knn.py --split heldout        # writes eval/results/knn-heldout.json

Answers "why not ML?" with a number on the same held-out set. Pure numpy, k = 5, no tuning.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tenfold.env import load_env  # noqa: E402

load_env()

from tenfold.env import load_env  # noqa: E402

load_env()

from classifier import features  # noqa: E402
from classifier.schema import window_from_json  # noqa: E402
from eval import scorers  # noqa: E402
from eval.run_eval import load_samples, print_table, samples_path  # noqa: E402


def label_key(s: dict) -> str:
    return json.dumps(s["label"], sort_keys=True)


def fit_predict(train: list[dict], test: list[dict], k: int = 5) -> list[dict | None]:
    X = np.stack([features.feature_vector(window_from_json(s["window"])) for s in train])
    keys = [label_key(s) for s in train]
    outs = []
    for s in test:
        x = features.feature_vector(window_from_json(s["window"]))
        idx = np.argsort(np.linalg.norm(X - x, axis=1))[:k]
        winner = Counter(keys[i] for i in idx).most_common(1)[0][0]
        outs.append({**json.loads(winner), "folded": None, "confidence": 1.0})
    return outs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["train", "heldout"], default="heldout")
    ap.add_argument("--train", help="train samples path override")
    ap.add_argument("--samples", help="evaluated samples path override")
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args()
    train = load_samples(samples_path("train", args.train))
    test = load_samples(samples_path(args.split, args.samples))
    preds = fit_predict(train, test, args.k)
    rows = [{"id": s["id"], "hold_id": s.get("hold_id", s["id"]), "kind": s.get("kind", "positive"),
             "target": s["label"], "output": o} for s, o in zip(test, preds)]
    metrics = scorers.aggregate(rows)
    print_table(metrics, args.split, f"knn{args.k}")
    out = REPO / "eval" / "results" / f"knn-{args.split}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"split": args.split, "tag": f"knn{args.k}", "metrics": metrics}, indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
