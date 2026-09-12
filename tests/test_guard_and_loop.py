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


def make_repo(tmp_path: Path) -> Path:
    """A throwaway copy of the repo with synthetic train/heldout data and one baseline commit."""
    from loop import synth
    dst = tmp_path / "tenfold"
    for rel in ["classifier", "eval", "loop", "lesson", "tests", "tenfold"]:
        shutil.copytree(REPO / rel, dst / rel, ignore=shutil.ignore_patterns("__pycache__", "results", "transcripts"))
    (dst / "data").mkdir()
    synth.write_jsonl(synth.synthetic_dataset(seed=1, session="train"), dst / "data" / "samples.jsonl")
    (tmp_path / "tenfold-heldout").mkdir()
    synth.write_jsonl(synth.synthetic_dataset(seed=7, session="other", split="test"), tmp_path / "tenfold-heldout" / "test.jsonl")
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
    repo = make_repo(tmp_path)
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
    repo = make_repo(tmp_path)
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
    assert "CONTACT_THRESHOLD = 0.30" in (repo / "classifier" / "rules.py").read_text()
    assert "ACCEPTED v1" in p.stdout and "ACCEPTED v2" in p.stdout


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


def test_budget_cap_stops_the_arm(tmp_path):
    repo = make_repo(tmp_path)
    # each fake call reports 0.5 USD: iteration 1 spends 1.5 (diagnosis, patch, guard agent), above a 1.0 limit
    p = run_critic(repo, "--skip-heldout", "--max-cost", "1.0", iterations=3)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "STOP: budget reached ($1.50 spent, limit $1.00)" in p.stdout
    assert len(commits_by_critic(repo)) == 1
    m = json.loads((repo / "data" / "metrics.json").read_text())
    assert m["versions"][-1]["spent_usd"] == 1.5
