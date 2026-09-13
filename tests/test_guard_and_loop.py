"""Guard rules and the whole loop with a fake claude: the 2 a.m. tests."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FAKE = REPO / "tests" / "fake_claude.py"
PY = sys.executable


def make_repo(tmp_path: Path, hard: bool = True) -> Path:
    """A throwaway copy of the repo with synthetic train/heldout data and one baseline commit.
    hard=True (default) uses the real-camera failure modes, where tightening the contact threshold is a strict
    improvement, so the strict metric gate has something to accept."""
    from loop import synth
    dst = tmp_path / "tenfold"
    for rel in ["classifier", "eval", "loop", "lesson", "tests", "tenfold"]:
        shutil.copytree(REPO / rel, dst / rel, ignore=shutil.ignore_patterns("__pycache__", "results", "transcripts"))
    (dst / "data").mkdir()
    synth.write_jsonl(synth.synthetic_dataset(seed=1, session="train", hard=hard), dst / "data" / "samples.jsonl")
    (tmp_path / "tenfold-heldout").mkdir()
    synth.write_jsonl(synth.synthetic_dataset(seed=7, session="other", split="test", hard=hard), tmp_path / "tenfold-heldout" / "test.jsonl")
    subprocess.run(["git", "init", "-q"], cwd=dst, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "root"], cwd=dst, check=True)
    subprocess.run(["git", "add", "-A"], cwd=dst, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "baseline"], cwd=dst, check=True)
    return dst


def run_critic(repo: Path, *extra: str, mode: str = "good", iterations: int = 1, timeout: int = 120) -> subprocess.CompletedProcess:
    env = {**os.environ, "FAKE_CLAUDE_PATCH": mode, "TENFOLD_HELDOUT": str(repo.parent / "tenfold-heldout" / "test.jsonl"),
           "PYTHONPATH": str(repo)}
    env.pop("WANDB_API_KEY", None)
    return subprocess.run([PY, str(repo / "loop" / "critic.py"), "--repo", str(repo), "--worktree", str(repo.parent / "critic-wt"),
                           "--iterations", str(iterations), "--mock-claude", str(FAKE), "--local", *extra],
                          cwd=repo, env=env, capture_output=True, text=True, timeout=timeout)


def commits_by_critic(repo: Path) -> list[str]:
    out = subprocess.run(["git", "log", "--format=%an|%s", "--author=critic-agent"], cwd=repo, capture_output=True, text=True).stdout
    return [l for l in out.splitlines() if l]


# ---------- guard unit tests ----------

def guard(worktree: Path, transcript: Path | None = None) -> tuple[int, str]:
    cmd = [PY, str(REPO / "loop" / "guard.py"), "--worktree", str(worktree)]
    if transcript:
        cmd += ["--transcript", str(transcript)]
    p = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(REPO)})
    return p.returncode, p.stdout


@pytest.fixture
def worktree(tmp_path):
    wt = tmp_path / "wt"
    for rel in ["classifier", "loop"]:
        shutil.copytree(REPO / rel, wt / rel, ignore=shutil.ignore_patterns("__pycache__", "transcripts"))
    subprocess.run(["git", "init", "-q"], cwd=wt, check=True)
    subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base"], cwd=wt, check=True)
    return wt


def test_guard_accepts_a_clean_threshold_change(worktree):
    r = worktree / "classifier" / "rules.py"
    r.write_text(r.read_text().replace("0.35", "0.30"))
    rc, out = guard(worktree)
    assert rc == 0 and "GUARD_OK" in out, out


def test_guard_rejects_no_change(worktree):
    rc, out = guard(worktree)
    assert rc == 1 and "GUARD_REJECT no_change" in out


def test_guard_rejects_other_files(worktree):
    (worktree / "classifier" / "features.py").write_text("# tampered\n" + (worktree / "classifier" / "features.py").read_text())
    rc, out = guard(worktree)
    assert rc == 1 and "GUARD_REJECT files" in out and "features.py" in out


def test_guard_rejects_big_diff(worktree):
    r = worktree / "classifier" / "rules.py"
    r.write_text(r.read_text() + "\n".join(f"PAD_{i} = {i}" for i in range(100)) + "\n")
    rc, out = guard(worktree)
    assert rc == 1 and "GUARD_REJECT diff_size" in out


def test_guard_rejects_forbidden_import_and_label_lookup(worktree):
    r = worktree / "classifier" / "rules.py"
    r.write_text("import os\n" + r.read_text().replace("    frame = features", "    open('data/samples.jsonl')\n    frame = features"))
    rc, out = guard(worktree)
    assert rc == 1 and "GUARD_REJECT imports: import os" in out and "GUARD_REJECT forbidden_name: use of open" in out


def test_guard_rejects_smoke_failure(worktree):
    r = worktree / "classifier" / "rules.py"
    r.write_text(r.read_text().replace("    frame = features.last_valid_frame(window)", "    frame = features.last_valid_frame(window)\n    1 / 0"))
    rc, out = guard(worktree)
    assert rc == 1 and "GUARD_REJECT smoke" in out and "ZeroDivisionError" in out


def test_guard_rejects_unknown_everywhere(worktree):
    r = worktree / "classifier" / "rules.py"
    r.write_text(r.read_text().replace("    frame = features.last_valid_frame(window)", "    return GestureState.unknown()\n    frame = features.last_valid_frame(window)"))
    rc, out = guard(worktree)
    assert rc == 1 and "five_valid_contact" in out


def test_guard_transcript_audit(worktree, tmp_path):
    r = worktree / "classifier" / "rules.py"
    r.write_text(r.read_text().replace("0.35", "0.30"))
    t = tmp_path / "t.jsonl"
    lines = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "../tenfold-heldout/test.jsonl"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "tenfold-heldout listed in a tool result is fine"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Let me look at /Users/someone/secret"}]}},
    ]
    t.write_text("\n".join(json.dumps(l) for l in lines))
    rc, out = guard(worktree, t)
    assert rc == 1 and "GUARD_REJECT held_out" in out and "GUARD_REJECT path" in out
    # tool results alone must not trigger
    t.write_text(json.dumps(lines[1]))
    rc, out = guard(worktree, t)
    assert rc == 0


def test_guard_check_mode_from_inside_worktree(worktree):
    p = subprocess.run([PY, "loop/guard.py", "--check"], cwd=worktree, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(worktree)})
    assert p.returncode == 0 and "GUARD_OK" in p.stdout


# ---------- eval ----------

def test_run_eval_local_and_report(tmp_path):
    repo = make_repo(tmp_path, hard=False)
    p = subprocess.run([PY, "eval/run_eval.py", "--split", "train", "--local", "--tag", "t"], cwd=repo, capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": str(repo)})
    assert p.returncode == 0, p.stderr
    res = json.loads((repo / "eval" / "results" / "train-t.json").read_text())
    assert res["metrics"]["exact_match"] > 0.9 and res["errors"] == 0
    rep = json.loads((repo / "eval" / "last_train_report.json").read_text())
    assert "worst_samples" in rep and "success_metric" in rep
    for w in rep["worst_samples"]:
        assert "window" not in w and "points" not in json.dumps(w)


def test_run_eval_refuses_empty_file(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "data" / "samples.jsonl").write_text("not json\n")
    p = subprocess.run([PY, "eval/run_eval.py", "--split", "train", "--local"], cwd=repo, capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": str(repo)})
    assert p.returncode != 0 and "need at least 10" in p.stderr


def test_knn_baseline(tmp_path):
    repo = make_repo(tmp_path, hard=False)
    p = subprocess.run([PY, "eval/baseline_knn.py", "--split", "heldout"], cwd=repo, capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": str(repo), "TENFOLD_HELDOUT": str(tmp_path / "tenfold-heldout" / "test.jsonl")})
    assert p.returncode == 0, p.stderr
    assert json.loads((repo / "eval" / "results" / "knn-heldout.json").read_text())["metrics"]["exact_match"] > 0.5


# ---------- the loop, end to end with a fake claude ----------

def test_loop_accepts_good_patch_twice_and_evaluates_heldout(tmp_path):
    repo = make_repo(tmp_path)
    p = run_critic(repo, iterations=2)
    assert p.returncode == 0, p.stdout + p.stderr
    assert len(commits_by_critic(repo)) == 2
    m = json.loads((repo / "data" / "metrics.json").read_text())
    assert [v["tag"] for v in m["versions"]] == ["v0", "v1", "v2"]
    assert all(v["heldout"] is not None for v in m["versions"])
    assert (repo / "data" / "BEST_VERSION").read_text().strip() == subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    assert "CONTACT_THRESHOLD = 0.25" in (repo / "classifier" / "rules.py").read_text()
    assert "ACCEPTED v1" in p.stdout and "ACCEPTED v2" in p.stdout
    em = [v["train"]["exact_match"] for v in m["versions"]]
    assert em[0] < em[1] < em[2]


def test_loop_rejects_regression_and_keeps_rules(tmp_path):
    repo = make_repo(tmp_path)
    before = (repo / "classifier" / "rules.py").read_text()
    p = run_critic(repo, "--skip-heldout", mode="bad")
    assert p.returncode == 0, p.stdout + p.stderr
    assert commits_by_critic(repo) == []
    assert (repo / "classifier" / "rules.py").read_text() == before
    assert "rejected" in p.stdout and "five_valid_contact" in p.stdout  # the smoke test caught it before the gate


@pytest.mark.parametrize("mode,marker", [("cheat", "GUARD_REJECT imports"), ("outside", "GUARD_REJECT files"),
                                         ("huge", "GUARD_REJECT diff_size"), ("noop", "GUARD_REJECT no_change")])
def test_loop_guard_rejections_never_commit(tmp_path, mode, marker):
    repo = make_repo(tmp_path)
    p = run_critic(repo, "--skip-heldout", mode=mode)
    assert p.returncode == 0, p.stdout + p.stderr
    assert marker in p.stdout and commits_by_critic(repo) == []


def test_loop_survives_a_hanging_claude(tmp_path):
    repo = make_repo(tmp_path)
    p = run_critic(repo, "--skip-heldout", "--timeout", "3", mode="hang", timeout=90)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "timed out" in p.stdout and commits_by_critic(repo) == []


def test_dry_run_prints_diff_and_commits_nothing(tmp_path):
    repo = make_repo(tmp_path)
    p = run_critic(repo, "--dry-run", "--skip-heldout")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "DRY RUN" in p.stdout and "-CONTACT_THRESHOLD = 0.35" in p.stdout and commits_by_critic(repo) == []


def test_blind_arm_never_touches_repo_state(tmp_path):
    repo = make_repo(tmp_path)
    before = (repo / "classifier" / "rules.py").read_text()
    wt = tmp_path / "blind-wt"
    p = run_critic(repo, "--blind", "--skip-heldout", "--worktree", str(wt))
    assert p.returncode == 0, p.stdout + p.stderr
    assert commits_by_critic(repo) == []
    assert (repo / "classifier" / "rules.py").read_text() == before
    assert "CONTACT_THRESHOLD = 0.30" in (repo / "loop" / "blind-rules.py").read_text()
    assert (repo / "data" / "metrics-blind.json").exists() and not (repo / "data" / "metrics.json").exists()
    assert not (repo / "eval" / "last_train_report.json").exists()
    assert not (wt / "data" / "train.jsonl").exists()
    assert not (wt / "eval" / "last_train_report.json").exists()
    assert not (wt / "loop" / "train_eval.py").exists()
    m = json.loads((repo / "data" / "metrics-blind.json").read_text())
    assert [v["tag"] for v in m["versions"]] == ["v0-blind", "v1-blind"]


def test_informed_worktree_gets_train_tools_and_critic_commits_only_rules(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "notes.txt").write_text("human work in progress")
    subprocess.run(["git", "add", "notes.txt"], cwd=repo, check=True)
    p = run_critic(repo, "--skip-heldout")
    assert p.returncode == 0, p.stdout + p.stderr
    files = subprocess.run(["git", "log", "--author=critic-agent", "--name-only", "--format="], cwd=repo,
                           capture_output=True, text=True).stdout.split()
    assert files == ["classifier/rules.py"]
    wt = repo.parent / "critic-wt"
    assert (wt / "data" / "train.jsonl").exists() and (wt / "loop" / "train_eval.py").exists()
    te = subprocess.run([PY, "loop/train_eval.py"], cwd=wt, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(wt)})
    assert te.returncode == 0 and "exact_match" in te.stdout, te.stdout + te.stderr
    assert json.loads((repo / "eval" / "last_train_report.json").read_text())["tag"] == "v1-candidate"


def test_guard_transcript_ignores_code_payloads_and_denied_calls(worktree, tmp_path):
    r = worktree / "classifier" / "rules.py"
    r.write_text(r.read_text().replace("0.35", "0.30"))
    t = tmp_path / "t.jsonl"
    events = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a", "name": "Edit", "input": {
            "file_path": str(worktree / "classifier" / "rules.py"), "old_string": "x / y", "new_string": "a / b  # ratio /2"}}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "b", "name": "Write", "input": {
            "file_path": "/tmp/eval_local.py", "content": "print(1)"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "b", "is_error": True, "content": "denied"}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "c", "name": "Bash", "input": {
            "command": "python loop/smoke.py --rules classifier/rules.py"}}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "this should also generalise to held-out hands in /Users/x"}]}},
    ]
    t.write_text("\n".join(json.dumps(e) for e in events))
    rc, out = guard(worktree, t)
    assert rc == 0, out
    events.append({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "d", "name": "Bash", "input": {"command": "cat ~/.ssh/id_rsa"}}]}})
    t.write_text("\n".join(json.dumps(e) for e in events))
    rc, out = guard(worktree, t)
    assert rc == 1 and "GUARD_REJECT path: Bash used ~/.ssh/id_rsa" in out


def test_snapshot_has_versions_diffs_and_commits(tmp_path):
    repo = make_repo(tmp_path)
    assert run_critic(repo, iterations=1).returncode == 0
    p = subprocess.run([PY, "loop/snapshot.py", "--repo", str(repo)], cwd=repo, capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": str(repo)})
    assert p.returncode == 0, p.stderr
    snap = json.loads((repo / "data" / "snapshot.json").read_text())
    assert [v["tag"] for v in snap["informed"]] == ["v0", "v1"]
    assert "CONTACT_THRESHOLD = 0.30" in snap["informed"][1]["diff"]
    assert snap["best_version"] == snap["informed"][1]["sha"]
    assert snap["informed"][1]["heldout"]["exact_match"] is not None
    assert len(snap["critic_commits"]) == 1 and snap["blind"] == [] and snap["knn_heldout"] is None
    assert set(snap["informed"][1]["train"]) >= {"exact_match", "exact_match_ci95", "per_class"}


def test_budget_cap_stops_before_an_iteration_it_cannot_afford(tmp_path):
    repo = make_repo(tmp_path)
    # each fake call costs 0.5: two iterations spend 3.0, and 0.5 left cannot pay for a diagnosis plus a patch
    p = run_critic(repo, "--skip-heldout", "--max-cost", "3.5", iterations=3)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "STOP: $3.00 spent of $3.50, not enough left for another iteration" in p.stdout
    assert len(commits_by_critic(repo)) == 2
    m = json.loads((repo / "data" / "metrics.json").read_text())
    assert m["versions"][-1]["spent_usd"] == 3.0


def test_patch_agent_is_capped_mid_call_and_its_edit_is_still_judged(tmp_path):
    repo = make_repo(tmp_path)
    # the patch agent would spend 5.0; with 3.0 in total it gets 3.0 - 0.1 (diagnosis) - 0.5 (guard reserve) = 2.4
    env_cost = {"FAKE_CLAUDE_COST": "0.1", "FAKE_CLAUDE_PATCH_COST": "5.0"}
    os.environ.update(env_cost)
    try:
        p = run_critic(repo, "--skip-heldout", "--max-cost", "3.0")
    finally:
        for k in env_cost:
            os.environ.pop(k, None)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "stopped at its budget; the edit it left goes through guard and gate" in p.stdout
    assert "ACCEPTED v1" in p.stdout and len(commits_by_critic(repo)) == 1
    m = json.loads((repo / "data" / "metrics.json").read_text())
    assert m["versions"][1]["spent_usd"] == pytest.approx(2.6) and m["versions"][1]["spent_usd"] <= 3.0


def _metrics(em, fu, per_class=None):
    return {"exact_match": em, "false_unknown_rate": fu, "per_class": per_class or {"7x8": em}}


def test_gate_rules():
    sys.path.insert(0, str(REPO))
    from loop.critic import Critic
    gate = Critic.gate
    assert gate(None, _metrics(0.46, 0.053), _metrics(0.60, 0.053))[0]
    assert gate(None, _metrics(0.46, 0.053), _metrics(0.60, 0.070))[0]            # +1.7 points: tolerated
    ok, why = gate(None, _metrics(0.46, 0.053), _metrics(0.60, 0.080))             # +2.7 points: rejected
    assert not ok and "false_unknown_rate rose 0.053 -> 0.080" in why
    ok, why = gate(None, _metrics(0.60, 0.05), _metrics(0.59, 0.05))
    assert not ok and "exact_match fell" in why
    ok, why = gate(None, _metrics(0.60, 0.05, {"7x8": 0.9, "8x8": 0.8}), _metrics(0.62, 0.05, {"7x8": 1.0, "8x8": 0.7}))
    assert not ok and "class 8x8 fell" in why


def test_accepted_version_records_patch_hypothesis(tmp_path):
    repo = make_repo(tmp_path)
    assert run_critic(repo, "--skip-heldout").returncode == 0
    m = json.loads((repo / "data" / "metrics.json").read_text())
    assert m["versions"][1]["patch_hypothesis"].startswith("near-contact gaps")


def test_gate_requires_strict_improvement(tmp_path):
    from loop.critic import Critic
    ok, why = Critic.gate(None, _metrics(0.60, 0.05), _metrics(0.60, 0.05))
    assert not ok and "no improvement" in why


def test_loop_rejects_a_patch_that_does_not_move_the_score(tmp_path):
    repo = make_repo(tmp_path)
    p = run_critic(repo, "--skip-heldout", mode="same")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "metric gate rejected: no improvement" in p.stdout and commits_by_critic(repo) == []
    m = json.loads((repo / "data" / "metrics.json").read_text())
    assert "no improvement" in m["rejected"][0]["reason"]


def test_loop_salvages_the_edit_when_the_agent_runs_out_of_turns(tmp_path):
    repo = make_repo(tmp_path)
    p = run_critic(repo, "--skip-heldout", mode="maxturns")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "ran out of turns; the edit it left goes through guard and gate" in p.stdout and "ACCEPTED v1" in p.stdout
    assert len(commits_by_critic(repo)) == 1
    m = json.loads((repo / "data" / "metrics.json").read_text())
    assert m["versions"][1]["patch"] == "edit left by a session that ran out of turns"


def test_loop_out_of_turns_without_edit_fails_cleanly(tmp_path):
    repo = make_repo(tmp_path)
    p = run_critic(repo, "--skip-heldout", mode="maxturns_noedit")
    assert p.returncode == 0, p.stdout + p.stderr
    assert p.stdout.count("agent ran out of turns with no edit") == 2 and commits_by_critic(repo) == []


def test_patch_prompt_announces_the_turn_budget():
    text = (REPO / "loop" / "prompts" / "patch.md").read_text()
    assert "{max_turns}" in text and "{finalize_by}" in text


# ---------- the guard agent reads the patch agent's account ----------

def test_guard_prompt_orders_diagnosis_account_diff():
    text = (REPO / "loop" / "prompts" / "guard.md").read_text()
    assert text.index("{diagnosis}") < text.index("{account}") < text.index("{diff}")


def test_guard_agent_is_shown_the_patch_agents_account(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    monkeypatch.setenv("FAKE_CLAUDE_GUARD", "need_account")
    p = run_critic(repo, "--skip-heldout")
    assert p.returncode == 0 and "ACCEPTED v1" in p.stdout, p.stdout + p.stderr


def test_an_unexplained_salvaged_edit_reaches_the_guard_without_an_account(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    monkeypatch.setenv("FAKE_CLAUDE_GUARD", "need_account")
    p = run_critic(repo, "--skip-heldout", mode="maxturns")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "rejected: GUARD_REJECT agent: VERDICT: REJECT | Rule 1" in p.stdout and commits_by_critic(repo) == []


# ---------- train_eval overrides and sweeps ----------

def _train_eval(repo: Path, *args: str) -> subprocess.CompletedProcess:
    shutil.copy(repo / "data" / "samples.jsonl", repo / "data" / "train.jsonl")
    return subprocess.run([PY, "loop/train_eval.py", *args], cwd=repo, capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(repo)})


def test_train_eval_sweep_scores_each_value_without_touching_rules(tmp_path):
    repo = make_repo(tmp_path)
    before = (repo / "classifier" / "rules.py").read_text()
    p = _train_eval(repo, "--sweep", "CONTACT_THRESHOLD=0.30,0.35")
    assert p.returncode == 0, p.stdout + p.stderr
    rows = [l.strip() for l in p.stdout.splitlines() if l.strip().startswith("CONTACT_THRESHOLD=")]
    assert len(rows) == 2 and all("exact_match=" in r and "worst:" in r for r in rows)
    em = [float(r.split("exact_match=")[1].split()[0]) for r in rows]
    assert em[0] > em[1]  # hard synthetic data: 0.30 beats the V0 threshold
    assert rows[0].endswith("gate: PASS") or "gate: FAIL class" in rows[0], rows[0]
    assert "gate: FAIL no improvement" in rows[1]  # the file value cannot beat itself
    assert (repo / "classifier" / "rules.py").read_text() == before


def test_train_eval_set_overrides_and_names_the_valid_constants(tmp_path):
    repo = make_repo(tmp_path)
    base = _train_eval(repo)
    over = _train_eval(repo, "--set", "CONTACT_THRESHOLD=0.30")
    assert over.returncode == 0 and "overrides: CONTACT_THRESHOLD=0.30" in over.stdout, over.stdout + over.stderr
    assert base.stdout.split("failing samples")[0] != over.stdout.split("failing samples")[0]
    bad = _train_eval(repo, "--set", "NOPE=1")
    assert bad.returncode != 0 and "CONTACT_THRESHOLD=0.35" in bad.stderr


# ---------- latency ----------

def test_guard_rejects_a_slow_classifier(worktree):
    r = worktree / "classifier" / "rules.py"
    r.write_text(r.read_text().replace("    frame = features.last_valid_frame(window)",
                                       "    sum(i * i for i in range(300000))\n    frame = features.last_valid_frame(window)"))
    rc, out = guard(worktree)
    assert rc == 1 and "GUARD_REJECT latency: classify() p95" in out, out


# ---------- capture conditions ----------

def test_per_slice_groups_tags_and_skips_constant_or_small_ones():
    sys.path.insert(0, str(REPO))
    from eval.slices import per_slice
    target = {"method": "6-10", "left": 7, "right": 8, "contact": True, "folded": None}
    samples, rows = [], []
    for i in range(50):
        samples.append({"id": f"s{i}", "angle": "front" if i < 30 else "side", "distance": "near",
                        "lighting": "lamp" if i < 45 else "dark"})
        rows.append({"id": f"s{i}", "hold_id": f"s{i}", "kind": "positive", "target": target,
                     "output": target if i < 30 else {"method": "unknown"}})
    s = per_slice(samples, rows)
    assert s["angle=front"]["exact_match"] == 1.0 and s["angle=side"]["exact_match"] == 0.0
    assert s["angle=side"]["n_samples"] == 20
    assert not any(k.startswith("distance=") for k in s)  # a single value says nothing
    assert "lighting=dark" not in s and "lighting=lamp" in s  # 5 samples is too few to gate on


def test_gate_rejects_a_capture_condition_that_falls():
    from loop.critic import Critic
    prev = {**_metrics(0.50, 0.05), "per_slice": {"angle=front": {"exact_match": 0.60}, "angle=side": {"exact_match": 0.40}}}
    cand = {**_metrics(0.55, 0.05), "per_slice": {"angle=front": {"exact_match": 0.80}, "angle=side": {"exact_match": 0.30}}}
    ok, why = Critic.gate(None, prev, cand)
    assert not ok and "condition angle=side fell 0.40 -> 0.30" in why
    cand["per_slice"]["angle=side"]["exact_match"] = 0.37
    assert Critic.gate(None, prev, cand)[0]
    assert Critic.gate(None, _metrics(0.50, 0.05), cand)[0]  # a baseline evaluated before slices existed is not blocked


def test_run_eval_and_train_report_carry_conditions(tmp_path):
    repo = make_repo(tmp_path, hard=False)
    p = subprocess.run([PY, "eval/run_eval.py", "--split", "train", "--local", "--tag", "t"], cwd=repo, capture_output=True,
                       text=True, env={**os.environ, "PYTHONPATH": str(repo)})
    assert p.returncode == 0, p.stderr
    slices = json.loads((repo / "eval" / "results" / "train-t.json").read_text())["metrics"]["per_slice"]
    assert any(k.startswith("angle=") for k in slices) and "by condition:" in p.stdout
    assert json.loads((repo / "eval" / "last_train_report.json").read_text())["metrics"]["per_slice"] == slices
    te = _train_eval(repo)
    assert te.returncode == 0 and "by condition: angle=" in te.stdout, te.stdout + te.stderr
