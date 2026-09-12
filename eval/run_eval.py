"""Evaluate a rules.py on one split, print a table, write a JSON result, publish to Weave when a key is set.

    python eval/run_eval.py --split train                      # data/samples.jsonl, project tenfold
    python eval/run_eval.py --split heldout                    # ../tenfold-heldout/test.jsonl, project tenfold-heldout
    python eval/run_eval.py --split train --local              # no W&B at all
    python eval/run_eval.py --split train --rules /path/rules.py --tag candidate --out eval/results/x.json

The held-out split uses WANDB_API_KEY_HELDOUT (a different account than the critic's) and is always launched
by the critic as a subprocess whose environment never reaches the critic.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tenfold.env import load_env  # noqa: E402

load_env()

from tenfold.env import load_env  # noqa: E402

load_env()

from classifier import features  # noqa: E402
from classifier.schema import window_from_json  # noqa: E402
from eval import scorers  # noqa: E402

PROJECTS = {"train": "tenfold", "heldout": "tenfold-heldout"}
PROJECT_SUFFIX = os.environ.get("TENFOLD_PROJECT_SUFFIX", "")  # e.g. "-smoke" for rehearsals


def samples_path(split: str, override: str | None) -> Path:
    if override:
        return Path(override)
    if split == "train":
        return REPO / "data" / "samples.jsonl"
    return Path(os.environ.get("TENFOLD_HELDOUT", REPO.parent / "tenfold-heldout" / "test.jsonl"))


def load_samples(path: Path) -> list[dict]:
    rows: list[dict] = []
    skipped = 0
    with open(path) as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                skipped += 1
    if skipped:
        print(f"warning: skipped {skipped} malformed lines in {path}", file=sys.stderr)
    if len(rows) < 10:
        raise SystemExit(f"error: {path} has {len(rows)} samples, need at least 10. Run the capture first.")
    return rows


def predict(rules: Path, samples: Path) -> dict[str, dict]:
    """Predictions from an isolated subprocess (cwd outside the repo)."""
    with tempfile.TemporaryDirectory(prefix="tenfold-predict-") as tmp:
        env = {**os.environ, "PYTHONPATH": str(REPO)}
        for k in list(env):
            if k.startswith("WANDB_"):
                env.pop(k)
        proc = subprocess.run(
            [sys.executable, "-m", "eval.predict", "--rules", str(rules), "--samples", str(samples)],
            cwd=tmp, env=env, capture_output=True, text=True, timeout=600,
        )
    if proc.returncode != 0:
        raise SystemExit(f"error: prediction subprocess failed:\n{proc.stderr[-2000:]}")
    out: dict[str, dict] = {}
    for line in proc.stdout.splitlines():
        if line.strip():
            p = json.loads(line)
            out[p["id"]] = p
    return out


def build_rows(samples: list[dict], preds: dict[str, dict]) -> list[dict]:
    rows = []
    for s in samples:
        p = preds.get(s["id"], {"output": None, "error": "no prediction"})
        rows.append({
            "id": s["id"], "hold_id": s.get("hold_id", s["id"]), "kind": s.get("kind", "positive"),
            "target": s["label"], "output": p.get("output"), "error": p.get("error"),
        })
    return rows


def git_sha(path: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5).stdout.strip() or "none"
    except Exception:
        return "none"


def worst_samples(samples: list[dict], rows: list[dict], k: int = 10) -> list[dict]:
    """The failed samples the critic gets to see: predicted vs expected and fingertip distances, no arrays."""
    by_id = {s["id"]: s for s in samples}
    failed = [r for r in rows if not scorers.exact_match(r["output"], r["target"])]
    failed.sort(key=lambda r: (r.get("kind") != "positive", r["id"]))
    report = []
    for r in failed[:k]:
        s = by_id[r["id"]]
        item: dict = {"id": r["id"], "kind": r["kind"], "angle": s.get("angle"), "distance": s.get("distance"),
                      "expected": r["target"], "predicted": r["output"], "error": r.get("error")}
        frame = features.last_valid_frame(window_from_json(s["window"]))
        if frame is not None:
            d = features.tip_distance_matrix(*frame)
            item["closest_pairs"] = sorted(
                ({"left": lf, "right": rf, "dist": round(float(d[i, j]), 3)}
                 for i, lf in enumerate((6, 7, 8, 9, 10)) for j, rf in enumerate((6, 7, 8, 9, 10))),
                key=lambda x: x["dist"],
            )[:3]
            item["left_extended"] = [int(v) for v in features.extension_vector(frame[0])]
            item["right_extended"] = [int(v) for v in features.extension_vector(frame[1])]
            item["confidence"] = [frame[0].detection_conf, frame[1].detection_conf]
        else:
            item["closest_pairs"] = None
            item["note"] = "no frame with both hands present"
        report.append(item)
    return report


def print_table(metrics: dict, split: str, tag: str) -> None:
    print(f"\n{split} {tag}: {metrics['n_samples']} samples, {metrics['n_holds']} holds")
    for m in scorers.METRICS:
        v = metrics.get(m)
        print(f"  {m:28s} {'n/a' if v is None else f'{v:.3f}'}")
    ci = metrics.get("exact_match_ci95")
    if ci:
        print(f"  {'exact_match 95% CI':28s} [{ci[0]:.3f}, {ci[1]:.3f}]")
    worst = sorted(metrics["per_class"].items(), key=lambda kv: (kv[1] is None, kv[1]))[:5]
    print("  worst classes: " + ", ".join(f"{c}={v:.2f}" for c, v in worst if v is not None))


def publish_weave(split: str, tag: str, rows: list[dict], metrics: dict, sha: str) -> str | None:
    """Publish one weave.Evaluation with the precomputed predictions. Returns a URL-ish name or None."""
    if not os.environ.get("WANDB_API_KEY"):
        print("weave: no WANDB_API_KEY, not publishing (use --local to silence this)", file=sys.stderr)
        return None
    try:
        import asyncio

        import weave

        entity = os.environ.get("WANDB_ENTITY")
        project = PROJECTS[split] + PROJECT_SUFFIX
        weave.init(f"{entity}/{project}" if entity else project)
        by_id = {r["id"]: r for r in rows}

        @weave.op(name="tenfold_rules")
        def model(id: str) -> dict | None:
            return by_id[id]["output"]

        def mk(name):
            fn = getattr(scorers, name)

            def scorer(output, target, kind):
                v = fn(output, target, kind) if name in ("false_unknown", "negative_rejection", "near_contact_accuracy") else fn(output, target)
                return {name: v}
            scorer.__name__ = name
            return weave.op(name=name)(scorer)

        dataset = [{"id": r["id"], "target": r["target"], "kind": r["kind"]} for r in rows]
        ev = weave.Evaluation(name=f"tenfold-{split}-{tag}", dataset=dataset,
                              scorers=[mk(n) for n in ("exact_match", "contact_accuracy", "false_unknown",
                                                        "negative_rejection", "near_contact_accuracy")])
        with weave.attributes({"git_sha": sha, "split": split, "tag": tag, "hold_level": json.dumps(
                {k: v for k, v in metrics.items() if k != "per_class"})}):
            asyncio.run(ev.evaluate(model, __weave={"display_name": f"tenfold-{split}-{tag}"}))
        try:
            weave.finish()
        except Exception:
            pass
        return f"{project}/tenfold-{split}-{tag}"
    except Exception as e:  # publishing is never allowed to break the loop
        print(f"weave: publish failed ({type(e).__name__}: {e}); local metrics are still valid", file=sys.stderr)
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["train", "heldout"], default="train")
    ap.add_argument("--rules", default=str(REPO / "classifier" / "rules.py"))
    ap.add_argument("--samples")
    ap.add_argument("--tag", default="v0")
    ap.add_argument("--local", action="store_true", help="never talk to W&B")
    ap.add_argument("--out", help="JSON result path (default eval/results/<split>-<tag>.json)")
    ap.add_argument("--report", default=str(REPO / "eval" / "last_train_report.json"))
    args = ap.parse_args()

    if args.split == "heldout" and os.environ.get("WANDB_API_KEY_HELDOUT"):
        os.environ["WANDB_API_KEY"] = os.environ["WANDB_API_KEY_HELDOUT"]
        if os.environ.get("WANDB_ENTITY_HELDOUT"):
            os.environ["WANDB_ENTITY"] = os.environ["WANDB_ENTITY_HELDOUT"]  # the second account owns tenfold-heldout
    if args.local:
        os.environ.pop("WANDB_API_KEY", None)

    spath = samples_path(args.split, args.samples)
    if not spath.exists():
        raise SystemExit(f"error: {spath} not found. train: run data/capture.py; heldout: set TENFOLD_HELDOUT "
                         f"or put the file at ../tenfold-heldout/test.jsonl")
    samples = load_samples(spath)
    rows = build_rows(samples, predict(Path(args.rules).resolve(), spath))
    metrics = scorers.aggregate(rows)
    sha = git_sha(REPO)
    print_table(metrics, args.split, args.tag)

    out = Path(args.out) if args.out else REPO / "eval" / "results" / f"{args.split}-{args.tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    result = {"split": args.split, "tag": args.tag, "git_sha": sha, "rules": str(args.rules),
              "ts": datetime.now(timezone.utc).isoformat(), "metrics": metrics,
              "errors": sum(1 for r in rows if r.get("error"))}
    if not args.local:
        result["weave"] = publish_weave(args.split, args.tag, rows, metrics, sha)
    out.write_text(json.dumps(result, indent=2))

    if args.split == "train":
        Path(args.report).write_text(json.dumps({
            "tag": args.tag, "git_sha": sha, "success_metric": "exact_match must go up, false_unknown_rate < 0.05",
            "metrics": {k: v for k, v in metrics.items()},
            "worst_samples": worst_samples(samples, rows),
            "errors": [{"id": r["id"], "error": r["error"]} for r in rows if r.get("error")][:10],
        }, indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
