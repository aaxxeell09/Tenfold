"""The hidden gate (validation holds, real-lesson windows) and a train report that shows every failure family."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval import run_eval  # noqa: E402
from loop import gate, synth  # noqa: E402
from test_guard_and_loop import commits_by_critic, make_repo, run_critic  # noqa: E402

POS = {"method": "6-10", "left": 7, "right": 8, "contact": True}
UNKNOWN = {"method": "unknown", "left": None, "right": None, "contact": False}


# ---------- check_hidden ----------

def test_check_hidden_refuses_any_drop_and_an_unmeasured_candidate():
    assert not gate.check_hidden("validation", {"exact_match": 0.467}, {"exact_match": 0.461})[0]
    ok, why = gate.check_hidden("validation", {"exact_match": 0.467}, {"exact_match": 0.461})
    assert why.startswith("validation exact_match fell")
    assert gate.check_hidden("live", {"exact_match": 1.0}, {"exact_match": 1.0})[0]           # a tie passes
    assert gate.check_hidden("live", {"exact_match": 0.5}, {"exact_match": 0.6})[0]
    assert not gate.check_hidden("live", {"exact_match": 1.0}, None)[0]
    assert not gate.check_hidden("live", {"exact_match": 1.0}, {"exact_match": None})[0]
    assert gate.check_hidden("live", None, {"exact_match": 0.9})[0]                          # nothing to compare with


# ---------- the report ----------

def _row(i, hold, kind, target, output):
    return {"id": f"s{i:03d}", "hold_id": hold, "kind": kind, "target": target, "output": output}


def test_failures_by_class_ranks_failed_holds_and_counts_negatives():
    rows = [_row(0, "p1", "positive", POS, POS), _row(1, "p1", "positive", POS, UNKNOWN),      # one of two: hold fails
            _row(2, "t1", "transition", UNKNOWN, POS), _row(3, "t1", "transition", UNKNOWN, POS),
            _row(4, "t2", "transition", UNKNOWN, POS), _row(5, "t2", "transition", UNKNOWN, UNKNOWN)]
    table = run_eval.failures_by_class(rows)
    assert list(table) == ["transition", "7x8"]
    assert table["transition"] == {"failed_windows": 3, "windows": 4, "failed_holds": 2, "holds": 2}
    assert table["7x8"] == {"failed_windows": 1, "windows": 2, "failed_holds": 1, "holds": 1}


def test_failure_order_alternates_classes_instead_of_listing_positives_first():
    rows = [_row(i, f"p{i}", "positive", POS, UNKNOWN) for i in range(5)]
    rows += [_row(10 + i, f"t{i}", "transition", UNKNOWN, POS) for i in range(5)]
    kinds = [r["kind"] for r in run_eval.failure_order(rows)[:4]]
    assert set(kinds) == {"positive", "transition"} and kinds[0] != kinds[1]


# ---------- the loop, end to end ----------

def _near_contact_as_contact(seed: int) -> list[dict]:
    """Near-contact windows relabelled as touching: the fake patch that tightens the contact threshold wins train
    and loses exactly these, which is what a patch that only fits the train holds looks like."""
    rows = []
    for r in synth.synthetic_dataset(seed=seed, session="val", hard=True):
        if r["kind"] == "near_contact":
            rows.append({**r, "id": "v" + r["id"], "kind": "positive", "label": {**r["label"], "contact": True}})
    return rows


def test_loop_accepts_a_patch_that_holds_on_the_hidden_sets(tmp_path):
    repo = make_repo(tmp_path)
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    (hidden / "validation.jsonl").write_text((repo / "data" / "samples.jsonl").read_text())
    (hidden / "live.jsonl").write_text((repo / "data" / "samples.jsonl").read_text())
    runs = tmp_path / "runs"
    p = run_critic(repo, "--skip-heldout", "--validation-samples", str(hidden / "validation.jsonl"),
                   "--live-samples", str(hidden / "live.jsonl"), "--hidden-dir", str(runs))
    assert p.returncode == 0, p.stdout + p.stderr
    assert len(commits_by_critic(repo)) == 1 and "hidden gate passed" in p.stdout
    m = json.loads((repo / "data" / "metrics.json").read_text())
    v0, v1 = m["versions"]
    assert set(v0["hidden"]) == {"validation", "live"} and set(v1["hidden"]) == {"validation", "live"}
    assert v1["hidden"]["validation"]["exact_match"] >= v0["hidden"]["validation"]["exact_match"]
    assert any(runs.glob("validation-v1-candidate.json")) and not any((repo / "loop" / "transcripts").glob("validation-*"))


def test_loop_refuses_a_train_gain_that_costs_the_validation_set(tmp_path):
    repo = make_repo(tmp_path)
    before = (repo / "classifier" / "rules.py").read_text()
    val = tmp_path / "validation.jsonl"
    synth.write_jsonl(_near_contact_as_contact(seed=3), val)
    p = run_critic(repo, "--skip-heldout", "--validation-samples", str(val), "--hidden-dir", str(tmp_path / "runs"))
    assert p.returncode == 0, p.stdout + p.stderr
    assert "hidden gate rejected: validation exact_match fell" in p.stdout
    assert commits_by_critic(repo) == [] and (repo / "classifier" / "rules.py").read_text() == before
    m = json.loads((repo / "data" / "metrics.json").read_text())
    assert m["rejected"][-1]["reason"] == "hidden gate: validation"     # no hidden numbers in the reason


def test_missing_hidden_file_stops_before_any_agent(tmp_path):
    repo = make_repo(tmp_path)
    p = run_critic(repo, "--skip-heldout", "--validation-samples", str(tmp_path / "nope.jsonl"))
    assert p.returncode != 0 and "does not exist" in (p.stdout + p.stderr)
