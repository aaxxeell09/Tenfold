"""Continuous loop: a dataset change re-baselines the running version, and loop/watch.py pulls, iterates, idles and
pushes on its own, with a fake claude and a local bare repository standing in for GitHub."""
import json
import os
import subprocess

from test_guard_and_loop import FAKE, PY, commits_by_critic, make_repo, run_critic


def add_captures(repo, n: int = 30) -> None:
    """Append n copies of existing samples under new ids: a new capture session with the same kinds of holds."""
    path = repo / "data" / "samples.jsonl"
    lines = [l for l in path.read_text().splitlines() if l.strip()]
    extra = []
    for line in lines[:n]:
        s = json.loads(line)
        s["id"], s["hold_id"] = s["id"] + "_new", str(s.get("hold_id", s["id"])) + "_new"
        extra.append(json.dumps(s))
    path.write_text("\n".join(lines + extra) + "\n")


def versions(repo):
    return json.loads((repo / "data" / "metrics.json").read_text())["versions"]


def test_new_data_rebaselines_the_running_version_before_any_patch(tmp_path):
    repo = make_repo(tmp_path)
    assert "ACCEPTED v1" in run_critic(repo, "--skip-heldout").stdout
    n0 = versions(repo)[-1]["data"]["n_samples"]
    add_captures(repo)
    p = run_critic(repo, "--skip-heldout", mode="same")
    assert p.returncode == 0, p.stdout + p.stderr
    assert f"data refresh: dataset changed ({n0} -> {n0 + 30} samples" in p.stdout
    v = versions(repo)
    assert [x["tag"] for x in v] == ["v0", "v1", "v1-data1"]
    assert v[2]["kind"] == "data_refresh" and v[2]["version"] == 1 and v[2]["data"]["n_samples"] == n0 + 30
    assert v[2]["train"]["n_samples"] == n0 + 30
    # the candidate was gated against the refreshed baseline, on the same data
    assert "metric gate rejected: no improvement: exact_match stayed" in p.stdout
    again = run_critic(repo, "--skip-heldout", mode="same")
    assert "data refresh" not in again.stdout


def make_runner(tmp_path):
    src = make_repo(tmp_path)
    subprocess.run(["git", "branch", "-M", "main"], cwd=src, check=True)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(origin)], check=True)
    clones = []
    for name in ("runner", "teammate"):
        c = tmp_path / name
        subprocess.run(["git", "clone", "-q", str(origin), str(c)], check=True)
        subprocess.run(["git", "config", "user.name", name], cwd=c, check=True)
        subprocess.run(["git", "config", "user.email", f"{name}@t"], cwd=c, check=True)
        clones.append(c)
    return origin, clones[0], clones[1]


def run_watch(runner, *extra, mode="good"):
    env = {**os.environ, "FAKE_CLAUDE_PATCH": mode, "PYTHONPATH": str(runner)}
    env.pop("WANDB_API_KEY", None)
    critic_args = f"--mock-claude {FAKE} --local --skip-heldout --worktree {runner.parent / 'wt'}"
    return subprocess.run([PY, "loop/watch.py", "--once", "--push", "--max-cost", "100", "--critic-args", critic_args, *extra],
                          cwd=runner, env=env, capture_output=True, text=True, timeout=300)


def origin_log(origin):
    return subprocess.run(["git", "--git-dir", str(origin), "log", "--format=%an|%s", "main"], capture_output=True, text=True).stdout


def test_watch_iterates_idles_and_picks_up_a_teammates_new_captures(tmp_path):
    origin, runner, teammate = make_runner(tmp_path)

    first = run_watch(runner)  # nothing measured yet: counts as new data
    assert first.returncode == 0, first.stdout + first.stderr
    assert "running the critic, reason: new data" in first.stdout and "a version was accepted" in first.stdout
    assert "pushed:" in first.stdout
    log = origin_log(origin)
    assert "critic-agent|critic v1" in log and "snapshot: dashboard data after a watch cycle" in log

    second = run_watch(runner, mode="same")  # the last cycle improved: try again
    assert "reason: the last cycle improved" in second.stdout and "nothing accepted" in second.stdout

    third = run_watch(runner, mode="same")  # nothing new and nothing gained: wait, spend nothing
    assert "idle" in third.stdout and "running the critic" not in third.stdout

    add_captures(teammate)
    subprocess.run(["git", "commit", "-qam", "data: new capture session"], cwd=teammate, check=True)
    subprocess.run(["git", "pull", "-q", "--rebase", "origin", "main"], cwd=teammate, check=True)
    subprocess.run(["git", "push", "-q", "origin", "HEAD:main"], cwd=teammate, check=True)

    fourth = run_watch(runner, mode="same")
    assert fourth.returncode == 0, fourth.stdout + fourth.stderr
    assert "reason: new data" in fourth.stdout and "baseline refreshed on the new data" in fourth.stdout
    assert versions(runner)[-1]["kind"] == "data_refresh"
    snap = json.loads(subprocess.run(["git", "--git-dir", str(origin), "show", "main:data/snapshot.json"],
                                     capture_output=True, text=True).stdout)
    assert [v["kind"] for v in snap["informed"]][-1] == "data_refresh"
    state = json.loads((runner / "loop" / "watch-state.json").read_text())
    assert state["cycles"] == 4 and state["spent_usd"] > 0
    assert len(commits_by_critic(runner)) == 1


def test_watch_stops_at_its_budget_across_cycles(tmp_path):
    origin, runner, _ = make_runner(tmp_path)
    p = run_watch(runner, "--max-cost", "1")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "STOP, $0.00 spent of $1.00" in p.stdout and "running the critic" not in p.stdout
