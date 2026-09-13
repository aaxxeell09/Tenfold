"""Build data/snapshot.json: everything the marimo dashboard shows, in one committed file.

    python loop/snapshot.py        (or: make snapshot)

The dashboard reads only this file, so it runs on molab with no repo, no W&B key and no git. Schema in
docs/interfaces.md. Regenerate after the nightly (after both arms stop), then commit data/snapshot.json.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
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


def build(repo: Path) -> dict:
    informed = read_json(repo / "data" / "metrics.json")
    blind = read_json(repo / "data" / "metrics-blind.json")
    knn = read_json(repo / "eval" / "results" / "knn-heldout.json")
    best_file = repo / "data" / "BEST_VERSION"
    rejected = [{"arm": arm, **{k: r.get(k) for k in ("iteration", "ts", "reason", "patch")}}
                for arm, m in (("informed", informed), ("blind", blind)) for r in (m or {}).get("rejected", [])]
    commits = [dict(zip(("sha", "date", "subject"), line.split("\t", 2)))
               for line in git(repo, "log", "--author=critic-agent", "--format=%H%x09%aI%x09%s").splitlines() if line]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "best_version": best_file.read_text().strip() if best_file.exists() else None,
        "informed": version_rows(informed, repo, with_diff=True),
        "blind": version_rows(blind, repo, with_diff=False),
        "rejected": rejected,
        "critic_commits": commits,
        "knn_heldout": slim((knn or {}).get("metrics")),
        "detection_ceiling": read_json(repo / "data" / "detection_ceiling.json"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(REPO))
    a = ap.parse_args()
    repo = Path(a.repo).resolve()
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
