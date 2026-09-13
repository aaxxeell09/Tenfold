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
    snap["retrospective_validation"] = {
        "label": "validation rétrospective, participants non identifiés", "holds": 55, "windows": 125,
        "a": {"commit": "a" * 40, "rules_sha256": "0" * 64, "exact_match": 0.436, "ci95": [0.31, 0.55]},
        "b": {"commit": v1.get("sha") or "b" * 40, "rules_sha256": "1" * 64, "exact_match": 0.467, "ci95": [0.34, 0.58]},
        "paired": {"holds": 55, "mean_difference": 0.03, "ci95": [0.0, 0.08], "better": 2, "worse": 0, "unchanged": 53}}
    left = [[0.30 + 0.01 * i, 0.50 + 0.005 * i] for i in range(21)]
    right = [[0.55 + 0.01 * i, 0.50 + 0.005 * i] for i in range(21)]
    answers = [{"threshold": round(0.1 + 0.0125 * i, 4), "method": "6-10", "left": 6, "right": 10,
                "contact": 0.1 + 0.0125 * i > 0.317} for i in range(33)]
    rows = [{"value": value, "exact_match": 0.492 + d, "exact_match_unordered": 0.492 + d, "contact_accuracy": 0.86,
             "false_unknown_rate": 0.047, "negative_rejection_accuracy": 0.36, "near_contact_accuracy": 0.73 + d,
             "per_class": {"near:6x10": 0.7 + d, "6x6": 0.67}, "gate": "PASS" if d > 0 else "FAIL",
             "why": "exact_match 0.492 -> 0.497" if d > 0 else "no improvement"}
            for value, d in ((0.25, -0.01), (0.2826, 0.005), (0.2975, 0.0))]
    snap["refused"] = [{"stage": "guard", "ts": "2026-09-12T22:29:09+00:00", "reason": "VERDICT: REJECT | Rule 1 | not allowed"},
                       {"stage": "gate", "ts": "2026-09-12T23:34:54+00:00", "reason": "class 9x9 fell 1.00 -> 0.67 (limit 5 points)"}]
    second = copy.deepcopy(snap["retrospective_validation"])
    second["b"]["commit"] = "c" * 40
    second["paired"].update(mean_difference=0.024, ci95=[-0.012, 0.073])
    snap["retrospective_versions"] = [snap["retrospective_validation"], second]
    snap["explorer"] = {
        "data": "train side only", "data_sha": "448d3292a217", "windows": 483, "rules_sha256": "2" * 64,
        "constants": {"CONTACT_THRESHOLD": 0.2975, "UNKNOWN_THRESHOLD": 0.525},
        "compare": {"a": {"commit": "a" * 40, "constants": {"CONTACT_THRESHOLD": 0.35}, "exact_match": 0.464, "near_contact_accuracy": 0.40},
                    "b": {"commit": "b" * 40, "constants": {"CONTACT_THRESHOLD": 0.2975}, "exact_match": 0.5, "near_contact_accuracy": 0.78},
                    "windows": 483, "holds": 220, "near_windows": 72, "near_holds": 24},
        "gate_base": {"tag": "v2", "exact_match": 0.492, "near_contact_accuracy": 0.73, "false_unknown_rate": 0.047,
                      "per_class": {"near:6x10": 0.7, "6x6": 0.67}},
        "sweep": {"CONTACT_THRESHOLD": {"current": 0.2975, "rows": rows}},
        "examples": [{"class": "near:6x10", "id": "s1", "hold_id": "h1", "angle": "front", "distance": "far",
                      "label": {"method": "6-10", "left": 6, "right": 10, "contact": False}, "left": left, "right": right,
                      "fingers": [6, 7, 8, 9, 10], "tip_index": [4, 8, 12, 16, 20],
                      "tip_distances": [[0.317 + 0.1 * (i + j) for j in range(5)] for i in range(5)], "mean_scale": 0.25,
                      "confidence": 0.99, "running": {"method": "6-10", "left": 6, "right": 10, "contact": False},
                      "answers_by_contact_threshold": answers}]}
    return snap


def test_snapshot_explorer_is_computed_from_the_train_file_with_the_real_rules(tmp_path):
    sys.path.insert(0, str(REPO / "tests"))
    from test_guard_and_loop import make_repo
    repo = make_repo(tmp_path)
    env = {**os.environ, "PYTHONPATH": str(repo)}
    env.pop("TENFOLD_TRAIN_SAMPLES", None)
    p = subprocess.run([sys.executable, "loop/snapshot.py", "--repo", str(repo)], cwd=repo, capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    explorer = json.loads((repo / "data" / "snapshot.json").read_text())["explorer"]
    windows = sum(1 for line in (repo / "data" / "samples.jsonl").read_text().splitlines() if line.strip())
    assert explorer and "error" not in explorer and explorer["windows"] == windows
    assert explorer["data"].startswith("whole dataset")  # no split active in this repo, and the page says so
    rows = explorer["sweep"]["CONTACT_THRESHOLD"]["rows"]
    assert len(rows) >= 10 and {r["gate"] for r in rows} <= {"PASS", "FAIL"}
    assert any(abs(r["value"] - explorer["constants"]["CONTACT_THRESHOLD"]) < 1e-9 and r["gate"] == "FAIL" for r in rows)
    for example in explorer["examples"]:
        assert len(example["left"]) == len(example["right"]) == 21 and len(example["tip_distances"]) == 5
        assert len(example["answers_by_contact_threshold"]) == 33
    # "before the AI" is the first committed rules.py scored on the same file; in this repo it is the running one
    compare = explorer["compare"]
    assert "error" not in compare and compare["windows"] == windows
    assert compare["a"]["commit"] == compare["b"]["commit"] and compare["a"]["exact_match"] == compare["b"]["exact_match"]
    assert compare["a"]["constants"]["CONTACT_THRESHOLD"] == 0.35


def test_snapshot_collects_refusals_from_every_run_log_once(tmp_path):
    sys.path.insert(0, str(REPO / "tests"))
    from test_guard_and_loop import make_repo
    repo = make_repo(tmp_path)
    gate_line = "2026-09-12T23:34:54+00:00 metric gate rejected: class 9x9 fell 1.00 -> 0.67 (limit 5 points), 1 class(es) regressed"
    guard_line = "2026-09-12T22:29:09+00:00 patch attempt 1 rejected: GUARD_REJECT agent: VERDICT: REJECT | Rule 1 | not allowed"
    (repo / "loop" / "nightly-run1.log").write_text(f"{guard_line}\n2026-09-12T22:30:00+00:00 diagnosis: something\n{gate_line}\n")
    (repo / "loop" / "nightly.log").write_text(f"{gate_line}\n")  # the latest log repeats a run already archived
    p = subprocess.run([sys.executable, "loop/snapshot.py", "--repo", str(repo)], cwd=repo, capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": str(repo)})
    assert p.returncode == 0, p.stderr
    refused = json.loads((repo / "data" / "snapshot.json").read_text())["refused"]
    assert [(r["stage"], r["ts"][:13]) for r in refused] == [("guard", "2026-09-12T22"), ("gate", "2026-09-12T23")]
    assert refused[1]["reason"].startswith("class 9x9 fell")


def test_snapshot_carries_the_retrospective_validation_under_its_own_name(tmp_path):
    sys.path.insert(0, str(REPO / "tests"))
    from test_guard_and_loop import make_repo
    repo = make_repo(tmp_path)
    reports = tmp_path / "tenfold-validation" / "retro-seed42"
    reports.mkdir(parents=True)
    side = {"commit": "c" * 40, "sha256": "d" * 64}
    metrics = {"exact_match": 0.5, "exact_match_ci95": [0.4, 0.6]}
    (reports / "report-cccccccc-cccccccc.json").write_text(json.dumps({
        "label": "validation rétrospective, participants non identifiés", "caveat": "not a test on other people",
        "validation": {"manifest": "retro-seed42", "holds": 55, "windows": 125, "sha256": "e" * 64, "source_sha256": "f" * 64},
        "a": side, "b": side, "metrics": {"a_strict": metrics, "b_strict": metrics},
        "paired_exact_match_per_hold": {"strict": {"holds": 55, "mean_difference": 0.0}}}))
    p = subprocess.run([sys.executable, "loop/snapshot.py", "--repo", str(repo)], cwd=repo, capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": str(repo)})
    assert p.returncode == 0, p.stderr
    snap = json.loads((repo / "data" / "snapshot.json").read_text())
    retro = snap["retrospective_validation"]
    assert retro["label"].startswith("validation rétrospective") and retro["holds"] == 55
    assert all(v["heldout"] is None for v in snap["informed"])  # never folded into the held-out fields


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
