"""Retrospective validation split (eval/split.py), strict scoring (eval/strict.py), the V0 vs current comparison
(eval/retro_validate.py), and the loop reading only the train side."""
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from test_guard_and_loop import FAKE, PY, REPO, guard, make_repo, run_critic, worktree  # noqa: F401

sys.path.insert(0, str(REPO))
from eval import scorers, split, strict  # noqa: E402
from loop import synth  # noqa: E402

SPLIT = REPO / "eval" / "split.py"


def cls(row):
    return scorers.class_of(row["label"], row.get("kind", "positive"))


def write_dataset(tmp_path: Path) -> tuple[Path, list[dict]]:
    """Synthetic windows grouped two per hold, plus the two traps the split must handle: a hold_id string reused by a
    second session, and one window byte-identical across two holds of different classes."""
    rows = synth.synthetic_dataset(seed=3, session="train", hard=True)
    rows.sort(key=lambda r: (cls(r), r["id"]))
    seen: dict[str, int] = {}
    for r in rows:
        c = cls(r)
        seen[c] = seen.get(c, 0) + 1
        r["session"], r["hold_id"] = "s1", f"s1:{c}:{(seen[c] - 1) // 2}"
    reused = rows[0]["hold_id"]
    extra = synth.synthetic_dataset(seed=11, session="other", hard=True)[:2]
    for i, e in enumerate(extra):
        e["id"], e["session"], e["hold_id"] = f"second_{i}", "s2", reused
        e["label"], e["kind"] = rows[0]["label"], rows[0].get("kind", "positive")
    rows += extra
    first = rows[0]
    other = next(r for r in rows if cls(r) != cls(first) and r["hold_id"] != first["hold_id"])
    other["window"] = first["window"]
    path = tmp_path / "samples.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path, rows


def run_split(source: Path, train_dir: Path, val_dir: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(SPLIT), "--source", str(source), "--train-dir", str(train_dir),
                           "--validation-dir", str(val_dir), *extra], capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(REPO)}, timeout=120)


def lines(path: Path) -> list[str]:
    return [l for l in path.read_text().splitlines() if l.strip()]


def test_split_is_reproducible_and_keeps_every_example(tmp_path):
    source, rows = write_dataset(tmp_path)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    first = run_split(source, tmp_path / "a" / "train", tmp_path / "a" / "val")
    assert first.returncode == 0, first.stdout + first.stderr
    assert split.LABEL in first.stdout
    snapshot = {p: p.read_bytes() for p in (tmp_path / "a").rglob("*") if p.is_file()}
    again = run_split(source, tmp_path / "a" / "train", tmp_path / "a" / "val")
    assert again.returncode == 0 and {p: p.read_bytes() for p in snapshot} == snapshot
    elsewhere = run_split(source, tmp_path / "b" / "train", tmp_path / "b" / "val")
    assert elsewhere.returncode == 0
    for sub in ("train/train.jsonl", "val/validation.jsonl"):
        assert (tmp_path / "a" / sub).read_bytes() == (tmp_path / "b" / sub).read_bytes()
    ma, mb = (json.loads((tmp_path / d / "val" / "manifest.json").read_text()) for d in "ab")
    assert ma["assignment"] == mb["assignment"] and ma["counts"] == mb["counts"] and ma["seed"] == 42

    assert hashlib.sha256(source.read_bytes()).hexdigest() == before  # the source is only read
    train, val = lines(tmp_path / "a" / "train" / "train.jsonl"), lines(tmp_path / "a" / "val" / "validation.jsonl")
    assert sorted(train + val) == sorted(lines(source))
    ids_train, ids_val = {json.loads(l)["id"] for l in train}, {json.loads(l)["id"] for l in val}
    assert not ids_train & ids_val and len(ids_train | ids_val) == len(rows)
    assert ma["source"]["sha256"] == before and ma["files"]["validation"]["windows"] == len(val)


def test_no_hold_and_no_identical_window_on_both_sides(tmp_path):
    source, rows = write_dataset(tmp_path)
    assert run_split(source, tmp_path / "train", tmp_path / "val").returncode == 0
    m = json.loads((tmp_path / "val" / "manifest.json").read_text())
    train_groups, val_groups = set(m["assignment"]["train"]), set(m["assignment"]["validation"])
    assert not train_groups & val_groups and m["windows_on_both_sides"] == 0
    side_of = {g: "train" for g in train_groups} | {g: "validation" for g in val_groups}
    for path, side in ((tmp_path / "train" / "train.jsonl", "train"), (tmp_path / "val" / "validation.jsonl", "validation")):
        for l in lines(path):
            assert side_of[split.group_key(json.loads(l))] == side
    hashes = [{split.window_hash(json.loads(l)) for l in lines(p)}
              for p in (tmp_path / "train" / "train.jsonl", tmp_path / "val" / "validation.jsonl")]
    assert not hashes[0] & hashes[1]
    reused = rows[0]["hold_id"]
    assert f"s1::{reused}" in side_of and f"s2::{reused}" in side_of  # the session keeps same-named holds apart
    assert m["mixed_class_holds"] == [] and m["duplicate_windows"] and m["kept_in_train"]
    assert all(side_of[g] == "train" for unit in m["kept_in_train"] for g in unit["holds"])


def test_proportions_counts_and_rare_classes_are_reported(tmp_path):
    source, rows = write_dataset(tmp_path)
    assert run_split(source, tmp_path / "train", tmp_path / "val").returncode == 0
    m = json.loads((tmp_path / "val" / "manifest.json").read_text())
    total_holds = m["source"]["holds"]
    assert m["counts"]["train"]["holds"] + m["counts"]["validation"]["holds"] == total_holds
    assert m["counts"]["train"]["windows"] + m["counts"]["validation"]["windows"] == len(rows)
    assert m["counts"]["validation"]["holds"] == m["target_validation_holds"] == round(0.2 * total_holds)
    assert 0.15 <= m["proportions"]["validation_holds"] <= 0.25
    assert m["proportions"]["validation_windows"] == m["counts"]["validation"]["windows"] / len(rows)
    holds_by_class: dict[str, set] = {}
    for r in rows:
        holds_by_class.setdefault(cls(r), set()).add(split.group_key(r))
    assert m["rare_classes"] == sorted(c for c, h in holds_by_class.items() if len(h) < 2)
    for c in m["classes_only_in_validation"]:
        assert c not in m["counts"]["train"]["per_class"]
    assert any("participants are not identified" in l for l in m["limits"]) and m["label"] == split.LABEL


def test_validation_side_is_private_outside_the_repo_and_never_rewritten_from_another_source(tmp_path):
    source, _ = write_dataset(tmp_path)
    assert run_split(source, tmp_path / "train", tmp_path / "val").returncode == 0
    assert stat.S_IMODE((tmp_path / "val").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "val" / "validation.jsonl").stat().st_mode) == 0o600
    inside = run_split(source, tmp_path / "train2", REPO / "data" / "splits" / "must-not-exist")
    assert inside.returncode != 0 and "outside" in inside.stderr and not (REPO / "data" / "splits" / "must-not-exist").exists()
    assert run_split(source, tmp_path / "train", tmp_path / "val", "--check").returncode == 0
    with open(tmp_path / "train" / "train.jsonl", "a") as f:
        f.write('{"id": "tampered"}\n')
    assert run_split(source, tmp_path / "train", tmp_path / "val", "--check").returncode == 1
    changed = tmp_path / "changed.jsonl"
    changed.write_text("".join(lines(source)[:-1]) and "\n".join(lines(source)[:-1]) + "\n")
    refused = run_split(changed, tmp_path / "train", tmp_path / "val")
    assert refused.returncode != 0 and "another source" in refused.stderr


def test_missing_predictions_fail_under_strict_scoring_and_not_under_the_frozen_scorers():
    negative = {"id": "n", "hold_id": "h1", "kind": "transition", "target": {"method": "unknown"}, "output": None}
    positive = {"id": "p", "hold_id": "h2", "kind": "positive",
                "target": {"method": "6-10", "left": 7, "right": 8, "contact": True}, "output": None}
    frozen_neg, strict_neg = scorers.score_row(negative), strict.score_row_strict(negative)
    assert frozen_neg["exact_match"] is True and frozen_neg["negative_rejection"] is True  # the frozen defect
    assert strict_neg["exact_match"] is False and strict_neg["negative_rejection"] is False
    strict_pos = strict.score_row_strict(positive)
    assert strict_pos["exact_match"] is False and strict_pos["contact_accuracy"] is False
    assert strict_pos["false_unknown"] is True and strict_pos["negative_rejection"] is None
    answered = {**negative, "id": "n2", "hold_id": "h3", "output": {"method": "unknown"}}
    assert strict.score_row_strict(answered) == scorers.score_row(answered)
    rows = [negative, positive, answered]
    assert strict.aggregate_with(rows, scorers.score_row) == scorers.aggregate(rows)
    assert strict.aggregate_with(rows, strict.score_row_strict)["exact_match"] < scorers.aggregate(rows)["exact_match"]


def test_retro_validate_compares_two_fixed_commits_on_the_same_holds(tmp_path):
    repo = make_repo(tmp_path)
    assert "ACCEPTED v1" in run_critic(repo, "--skip-heldout").stdout
    env = {**os.environ, "PYTHONPATH": str(repo)}
    env.pop("WANDB_API_KEY", None)
    val_dir = tmp_path / "validation"
    made = subprocess.run([PY, "eval/split.py", "--validation-dir", str(val_dir), "--name", "t"], cwd=repo, env=env,
                          capture_output=True, text=True)
    assert made.returncode == 0, made.stdout + made.stderr
    p = subprocess.run([PY, "eval/retro_validate.py", "--validation", str(val_dir / "validation.jsonl")], cwd=repo,
                       env=env, capture_output=True, text=True, timeout=180)
    assert p.returncode == 0, p.stdout + p.stderr
    assert split.LABEL in p.stdout and "matches the manifest" in p.stdout
    report = json.loads(next(val_dir.glob("report-*.json")).read_text())
    history = subprocess.run(["git", "log", "--format=%H", "--", "classifier/rules.py"], cwd=repo,
                             capture_output=True, text=True).stdout.split()
    assert report["a"]["commit"] == history[-1]
    assert report["b"]["commit"] == (repo / "data" / "BEST_VERSION").read_text().strip() == history[0]
    for key in ("a", "b"):
        content = subprocess.run(["git", "show", f"{report[key]['commit']}:classifier/rules.py"], cwd=repo,
                                 capture_output=True).stdout
        assert report[key]["sha256"] == hashlib.sha256(content).hexdigest()
    n_holds = report["validation"]["holds"]
    assert report["paired_exact_match_per_hold"]["strict"]["holds"] == n_holds
    assert report["metrics"]["a_strict"]["n_holds"] == report["metrics"]["b_strict"]["n_holds"] == n_holds
    assert report["label"] == split.LABEL and "not a test on other people" in report["caveat"]


def test_critic_trains_only_on_the_train_side_when_asked(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    env = {**os.environ, "PYTHONPATH": str(repo)}
    assert subprocess.run([PY, "eval/split.py", "--validation-dir", str(tmp_path / "validation"), "--name", "t"],
                          cwd=repo, env=env, capture_output=True).returncode == 0
    train = repo / "data" / "splits" / "t" / "train.jsonl"
    monkeypatch.setenv("TENFOLD_TRAIN_SAMPLES", "data/splits/t/train.jsonl")
    p = run_critic(repo, "--skip-heldout")
    assert p.returncode == 0, p.stdout + p.stderr
    assert (repo.parent / "critic-wt" / "data" / "train.jsonl").read_bytes() == train.read_bytes()
    v0 = json.loads((repo / "data" / "metrics.json").read_text())["versions"][0]
    assert v0["train"]["n_samples"] == v0["data"]["n_samples"] == len(lines(train))
    monkeypatch.setenv("TENFOLD_TRAIN_SAMPLES", "data/splits/missing/train.jsonl")
    assert "not found" in run_critic(repo, "--skip-heldout").stderr


def test_guard_rejects_a_transcript_that_reaches_for_the_validation_set(worktree, tmp_path):
    r = worktree / "classifier" / "rules.py"
    r.write_text(r.read_text().replace("0.35", "0.30"))
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "x", "name": "Read",
                 "input": {"file_path": "../tenfold-validation/retro-seed42/validation.jsonl"}}]}}))
    rc, out = guard(worktree, t)
    assert rc == 1 and "GUARD_REJECT held_out" in out


def test_watch_fingerprints_the_train_split_and_flags_a_stale_one(tmp_path, monkeypatch):
    from loop import watch
    monkeypatch.delenv("TENFOLD_TRAIN_SAMPLES", raising=False)
    assert watch.train_samples_path() == watch.REPO / "data" / "samples.jsonl"
    monkeypatch.setenv("TENFOLD_TRAIN_SAMPLES", "data/splits/retro-seed42/train.jsonl")
    assert watch.train_samples_path() == watch.REPO / "data" / "splits" / "retro-seed42" / "train.jsonl"
    source = tmp_path / "samples.jsonl"
    source.write_text('{"id": "a"}\n')
    train = tmp_path / "split" / "train.jsonl"
    train.parent.mkdir()
    train.write_text('{"id": "a"}\n')
    (train.parent / "source.json").write_text(json.dumps({"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}))
    assert watch.stale_split_warning(train, source) is None
    source.write_text('{"id": "a"}\n{"id": "b"}\n')
    assert "changed since the split" in watch.stale_split_warning(train, source)
    assert watch.stale_split_warning(source, source) is None
