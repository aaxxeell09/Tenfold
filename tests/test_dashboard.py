"""The marimo dashboard runs top to bottom on the committed snapshot, on a snapshot with every curve, and on an
empty one, with no W&B key and no network."""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NOTEBOOK = REPO / "dashboard" / "loop_dashboard.py"


def run_notebook(snapshot: Path | None) -> subprocess.CompletedProcess:
    env = {**os.environ, "MPLBACKEND": "Agg", "PYTHONPATH": str(REPO)}
    env.pop("WANDB_API_KEY", None)
    if snapshot is not None:
        env["TENFOLD_SNAPSHOT"] = str(snapshot)
    return subprocess.run([sys.executable, str(NOTEBOOK)], cwd=REPO, env=env, capture_output=True, text=True, timeout=180)


def full_snapshot() -> dict:
    snap = json.loads((REPO / "data" / "snapshot.json").read_text())
    base = snap["informed"][0]
    v1 = copy.deepcopy(base)
    v1.update(tag="v1", version=1, kind="patch", diff="--- a\n+++ b\n-X = 1\n+X = 2\n", gate="exact_match 0.458 -> 0.487")
    v1["train"] = {**base["train"], "exact_match": 0.487}
    refresh = copy.deepcopy(v1)
    refresh.update(tag="v1-data1", kind="data_refresh", data={"sha": "abc", "n_samples": 760}, diff=None)
    for v, h in ((base, 0.41), (v1, 0.45), (refresh, 0.44)):
        v["heldout"] = {**v["train"], "exact_match": h, "exact_match_ci95": [h - 0.05, h + 0.05]}
    snap["informed"] = [base, v1, refresh]
    snap["blind"] = [{**copy.deepcopy(base), "tag": "v0-blind"}, {**copy.deepcopy(v1), "tag": "v1-blind"}]
    snap["knn_heldout"] = {"exact_match": 0.38}
    snap["detection_ceiling"] = {"both_hands_contact_frames": 0.93}
    snap["rejected"] = [{"arm": "informed", "iteration": 1, "ts": "2026-09-13T00:45:59+00:00",
                         "reason": "class 7x8 fell 0.78 -> 0.67", "patch": "frame vote"}]
    return snap


@pytest.mark.parametrize("case", ["committed", "full", "empty"])
def test_dashboard_runs_on_every_kind_of_snapshot(tmp_path, case):
    if case == "committed":
        path = None
    else:
        path = tmp_path / "snapshot.json"
        data = full_snapshot() if case == "full" else {"informed": [], "blind": [], "rejected": [], "critic_commits": []}
        path.write_text(json.dumps(data))
    p = run_notebook(path)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    assert "Traceback" not in p.stdout + p.stderr
