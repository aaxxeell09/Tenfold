"""Build data/snapshot.json: everything the marimo dashboard shows, in one committed file.

    python loop/snapshot.py        (or: make snapshot)

The dashboard reads only this file, so it runs on molab with no repo, no W&B key and no git. Schema in
docs/interfaces.md. Regenerate after the nightly (after both arms stop), then commit data/snapshot.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
KEEP = ("exact_match", "exact_match_ci95", "exact_match_unordered", "contact_accuracy", "false_unknown_rate",
        "negative_rejection_accuracy", "near_contact_accuracy", "n_samples", "n_holds", "per_class", "per_slice", "scorers_sha256")


def slim(m: dict | None) -> dict | None:
    return None if not m else {k: m.get(k) for k in KEEP}


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else ""


def version_rows(metrics: dict | None, repo: Path, with_diff: bool) -> list[dict]:
    rows = []
    for v in (metrics or {}).get("versions", []):
        row = {k: v.get(k) for k in ("tag", "version", "sha", "ts", "kind", "data", "diagnosis", "patch_hypothesis",
                                     "patch", "expected", "gate", "spent_usd")}
        row["train"], row["heldout"] = slim(v.get("train")), slim(v.get("heldout"))
        sha = v.get("sha")
        if with_diff and sha and sha != "no-commit" and (v.get("version") or 0) > 0:
            row["diff"] = git(repo, "show", "--format=", sha, "--", "classifier/rules.py")[:12000]
        rows.append(row)
    return rows


def retro_reports(repo: Path) -> list[dict]:
    """Every readable validation rétrospective report from eval/retro_validate.py, oldest first, strict scores."""
    base = repo.parent / "tenfold-validation"
    out = []
    for path in sorted(base.glob("*/report-*.json"), key=lambda p: p.stat().st_mtime) if base.is_dir() else []:
        r = read_json(path)
        if not r:
            continue
        try:
            side = {}
            for k in ("a", "b"):
                m = r["metrics"][f"{k}_strict"]
                side[k] = {"commit": r[k]["commit"], "rules_sha256": r[k]["sha256"], "exact_match": m["exact_match"],
                           "ci95": m["exact_match_ci95"], "near_contact_accuracy": m.get("near_contact_accuracy"),
                           "contact_accuracy": m.get("contact_accuracy"), "false_unknown_rate": m.get("false_unknown_rate")}
            near = [c for name, c in (r.get("per_class") or {}).items() if name.startswith("near:")]
            out.append({"label": r["label"], "caveat": r.get("caveat"), "name": r["validation"].get("manifest"),
                        "holds": r["validation"]["holds"], "windows": r["validation"]["windows"],
                        "near_holds": sum(c.get("holds") or 0 for c in near), "near_windows": sum(c.get("windows") or 0 for c in near),
                        "validation_sha256": r["validation"]["sha256"], "source_sha256": r["validation"].get("source_sha256"),
                        "scoring": "strict: a missing prediction fails", **side,
                        "paired": r["paired_exact_match_per_hold"]["strict"]})
        except (KeyError, TypeError):
            continue
    return out


def retrospective(repo: Path, best: str | None) -> dict | None:
    """The latest validation rétrospective report for the running version. Kept apart from the held-out fields on
    purpose: the critic saw these holds and participants are not identified."""
    return next((r for r in reversed(retro_reports(repo)) if not best or r["b"]["commit"] == best), None)


def retrospective_versions(repo: Path) -> list[dict]:
    """The latest report per compared version, all against the same baseline and the same validation file as the
    newest report, so the dashboard can draw every version's plausible range on one axis."""
    reports = retro_reports(repo)
    if not reports:
        return []
    newest = reports[-1]
    latest = {}
    for r in reports:
        if r["a"]["commit"] == newest["a"]["commit"] and r["validation_sha256"] == newest["validation_sha256"]:
            latest[r["b"]["commit"]] = r
    return list(latest.values())


EXAMPLE_CLASSES = ("near:6x10", "near:6x6", "near:10x8", "near:9x10", "6x6", "7x8", "10x10")
SWEEP_FACTORS = [round(0.5 + 0.05 * i, 2) for i in range(21)]  # 0.5x to 1.5x the running value
CONTACT_GRID = [round(0.1 + 0.0125 * i, 4) for i in range(33)]  # 0.10 to 0.50


def train_samples_path(repo: Path) -> Path:
    chosen = os.environ.get("TENFOLD_TRAIN_SAMPLES")
    if not chosen:
        return repo / "data" / "samples.jsonl"
    path = Path(chosen)
    return path if path.is_absolute() else repo / path


def explorer(repo: Path, informed: dict | None) -> dict | None:
    """Data for the dashboard's two explorables, computed from the train side only, never the validation holds:
    real hand geometry of a few holds with what the running rules answer at every contact threshold, and every
    numeric constant of the running rules swept with the metric gate's verdict. Landmarks only, no images."""
    samples_path, rules_path = train_samples_path(repo), repo / "classifier" / "rules.py"
    if not samples_path.exists() or not rules_path.exists():
        return None
    sys.path.insert(0, str(repo))
    try:
        from classifier import features
        from classifier.schema import FINGER_NUMBERS, window_from_json
        from eval.scorers import METRICS, class_of
        from loop import gate
        from loop.train_eval import evaluate, load_rules, numeric_constants
    except Exception as e:  # the dashboard must still render when the repo cannot be imported
        return {"error": f"{type(e).__name__}: {e}"}

    raw = samples_path.read_bytes()
    samples = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    data_sha = hashlib.sha256(raw).hexdigest()[:12]
    mod = load_rules(rules_path)
    constants = numeric_constants(mod)

    accepted = [v for v in (informed or {}).get("versions", []) if v.get("accepted")]
    base_tag = accepted[-1]["tag"] if accepted else None
    base = accepted[-1]["train"] if accepted and (accepted[-1].get("data") or {}).get("sha") == data_sha else None
    if base is None:
        base, _ = evaluate(mod.classify, samples)
        base_tag = "the rules as they are"

    def summary(m: dict) -> dict:
        return {**{k: m.get(k) for k in METRICS}, "per_class": m.get("per_class")}

    # The first committed rules.py (V0) and the running one, scored on this same file with the same scorers, so the
    # dashboard's "before the AI" numbers never mix datasets.
    compare = None
    first = git(repo, "log", "--reverse", "--format=%H", "--", "classifier/rules.py").split()
    if first:
        try:
            with tempfile.TemporaryDirectory() as tmp:
                first_path = Path(tmp) / "rules_first.py"
                first_path.write_text(git(repo, "show", f"{first[0]}:classifier/rules.py"))
                first_mod = load_rules(first_path)
                first_metrics, _ = evaluate(first_mod.classify, samples)
            now_metrics, _ = evaluate(mod.classify, samples)
            near = [s for s in samples if s.get("kind") == "near_contact"]
            last = git(repo, "log", "-1", "--format=%H", "--", "classifier/rules.py").strip()
            compare = {"a": {"commit": first[0], "constants": numeric_constants(first_mod), **{k: first_metrics.get(k) for k in METRICS}},
                       "b": {"commit": last, "constants": constants, **{k: now_metrics.get(k) for k in METRICS}},
                       "windows": len(samples), "holds": len({s.get("hold_id", s["id"]) for s in samples}),
                       "near_windows": len(near), "near_holds": len({s.get("hold_id", s["id"]) for s in near})}
        except Exception as e:  # a V0 that no longer loads must not take the rest of the snapshot down
            compare = {"error": f"{type(e).__name__}: {e}"}

    sweep = {}
    for name, value in constants.items():
        if isinstance(value, int) or not value:
            continue
        rows = []
        for v in sorted({round(value * f, 4) for f in SWEEP_FACTORS}):
            setattr(mod, name, v)
            m, _ = evaluate(mod.classify, samples)
            ok, why = gate.check(base, m)
            rows.append({"value": v, **summary(m), "gate": "PASS" if ok else "FAIL", "why": why})
        setattr(mod, name, value)
        sweep[name] = {"current": value, "rows": rows}

    def answer(window) -> dict:
        return {k: v for k, v in mod.classify(window).to_dict().items() if k in ("method", "left", "right", "contact")}

    def matches(out: dict, label: dict) -> bool:
        return all(out.get(k) == label.get(k) for k in ("method", "left", "right")) and bool(out.get("contact")) == bool(label.get("contact"))

    # One hold per class, the first one (in file order) that the running rules read right, so each example
    # shows the rule working; a class the rules never get right falls back to its first usable hold.
    examples = []
    for cls in EXAMPLE_CLASSES:
        usable = []
        for s in samples:
            if class_of(s["label"], s.get("kind", "positive")) != cls:
                continue
            window = window_from_json(s["window"])
            if features.last_valid_frame(window) is not None:
                usable.append((s, window))
        chosen = next(((s, w) for s, w in usable if matches(answer(w), s["label"])), usable[0] if usable else None)
        for s, window in [chosen] if chosen else []:
            left, right = features.last_valid_frame(window)
            answers = []
            if "CONTACT_THRESHOLD" in constants:
                for t in CONTACT_GRID:
                    mod.CONTACT_THRESHOLD = t
                    answers.append({"threshold": t, **{k: v for k, v in mod.classify(window).to_dict().items()
                                                      if k in ("method", "left", "right", "contact")}})
                mod.CONTACT_THRESHOLD = constants["CONTACT_THRESHOLD"]
            examples.append({
                "class": cls, "id": s["id"], "hold_id": s.get("hold_id"), "angle": s.get("angle"),
                "distance": s.get("distance"), "label": s["label"],
                "left": [[round(float(x), 4), round(float(y), 4)] for x, y in features.image_points(left)],
                "right": [[round(float(x), 4), round(float(y), 4)] for x, y in features.image_points(right)],
                "fingers": list(FINGER_NUMBERS), "tip_index": [features.TIP_INDEX[f] for f in FINGER_NUMBERS],
                "tip_distances": [[round(float(v), 4) for v in row] for row in features.tip_distance_matrix(left, right)],
                "mean_scale": round(float(features.mean_scale(left, right)), 5),
                "confidence": round(float(min(left.detection_conf, right.detection_conf)), 3),
                "running": {k: v for k, v in mod.classify(window).to_dict().items() if k in ("method", "left", "right", "contact")},
                "answers_by_contact_threshold": answers,
            })
            break

    return {"data": "train side only" if os.environ.get("TENFOLD_TRAIN_SAMPLES") else "whole dataset (no split active)",
            "data_sha": data_sha, "windows": len(samples),
            "rules_sha256": hashlib.sha256(rules_path.read_bytes()).hexdigest(),
            "constants": constants, "gate_base": {"tag": base_tag, **summary(base)}, "compare": compare,
            "sweep": sweep, "examples": examples}


REFUSALS = (("gate", re.compile(r"^(\S+) metric gate rejected: (.+)$")),
            ("guard", re.compile(r"^(\S+) patch attempt \d+ rejected: GUARD_REJECT agent: (.+)$")))


def refusals(repo: Path) -> list[dict]:
    """Every patch the guard agent or the metric gate refused, from the loop's logs (loop/*.log, loop/*.out), across
    runs, deduplicated by time and stage. The metrics files only keep the refusals of the run that wrote them."""
    seen, out = set(), []
    logs = sorted((repo / "loop").glob("*.log")) + sorted((repo / "loop").glob("*.out"))
    for path in logs:
        for line in path.read_text(errors="replace").splitlines():
            for stage, pattern in REFUSALS:
                m = pattern.match(line)
                if m and (m.group(1), stage) not in seen:
                    seen.add((m.group(1), stage))
                    out.append({"stage": stage, "ts": m.group(1), "reason": m.group(2)[:240]})
    return sorted(out, key=lambda r: r["ts"])


def build(repo: Path) -> dict:
    informed = read_json(repo / "data" / "metrics.json")
    blind = read_json(repo / "data" / "metrics-blind.json")
    knn = read_json(repo / "eval" / "results" / "knn-heldout.json")
    best_file = repo / "data" / "BEST_VERSION"
    best = best_file.read_text().strip() if best_file.exists() else None
    rejected = [{"arm": arm, **{k: r.get(k) for k in ("iteration", "ts", "reason", "patch")}}
                for arm, m in (("informed", informed), ("blind", blind)) for r in (m or {}).get("rejected", [])]
    commits = [dict(zip(("sha", "date", "subject"), line.split("\t", 2)))
               for line in git(repo, "log", "--author=critic-agent", "--format=%H%x09%aI%x09%s").splitlines() if line]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "best_version": best,
        "informed": version_rows(informed, repo, with_diff=True),
        "blind": version_rows(blind, repo, with_diff=False),
        "rejected": rejected,
        "refused": refusals(repo),
        "critic_commits": commits,
        "knn_heldout": slim((knn or {}).get("metrics")),
        "detection_ceiling": read_json(repo / "data" / "detection_ceiling.json"),
        "retrospective_validation": retrospective(repo, best),
        "retrospective_versions": retrospective_versions(repo),
        "explorer": explorer(repo, informed),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(REPO))
    a = ap.parse_args()
    repo = Path(a.repo).resolve()
    sys.path.insert(0, str(repo))
    from tenfold.env import load_env
    load_env(repo / ".env")  # TENFOLD_TRAIN_SAMPLES decides which file the explorables are computed from
    snap = build(repo)
    out = repo / "data" / "snapshot.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, indent=2))

    def em(m):
        return "n/a" if not m or m.get("exact_match") is None else f"{m['exact_match']:.3f}"
    for arm in ("informed", "blind"):
        for v in snap[arm]:
            print(f"{arm:8s} {v['tag']:10s} train {em(v['train'])}  heldout {em(v['heldout'])}")
    print(f"rejected {len(snap['rejected'])} | critic commits {len(snap['critic_commits'])} | knn heldout {em(snap['knn_heldout'])}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
