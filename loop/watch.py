"""Keep the loop running on whatever data the team pushes: pull, iterate when there is something to gain, push
what the gate accepted with a fresh dashboard snapshot, sleep, repeat. Runs in the runner clone (../tenfold-run).

    python loop/watch.py --push                         # a cycle every 10 minutes until the budget is spent
    python loop/watch.py --once --push                  # one cycle (cron, tests)
    python loop/watch.py --every 300 --max-cost 20 --iterations 2

A cycle runs the critic when the dataset changed since the last accepted version (someone pushed a new capture)
or when the previous cycle got a version accepted (there may be more to gain). Otherwise it waits for new data, so
an idle watcher spends nothing. loop/critic.py re-evaluates the running version on new data before any patch,
so the gate always compares like with like. Money is capped across cycles by --max-cost.
State: loop/watch-state.json. Log: loop/watch.log, critic output in loop/watch-critic.out.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from loop.critic import ITERATION_MIN_USD, data_fingerprint  # noqa: E402

STATE = REPO / "loop" / "watch-state.json"
LOG = REPO / "loop" / "watch.log"
CRITIC_OUT = REPO / "loop" / "watch-critic.out"
SPENT = re.compile(r"\$([0-9]+(?:\.[0-9]+)?) spent|spent \$([0-9]+(?:\.[0-9]+)?)")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg: str) -> None:
    line = f"{now()} {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)


def load_state() -> dict:
    try:
        return {"spent_usd": 0.0, "cycles": 0, "last_outcome": None, **json.loads(STATE.read_text())}
    except (FileNotFoundError, json.JSONDecodeError):
        return {"spent_usd": 0.0, "cycles": 0, "last_outcome": None}


def save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2))


def accepted_data_sha(metrics_path: Path) -> str | None:
    """Dataset fingerprint the last accepted version was measured on; None before the first run."""
    try:
        versions = json.loads(metrics_path.read_text())["versions"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None
    accepted = [v for v in versions if v.get("accepted")]
    return (accepted[-1].get("data") or {}).get("sha") if accepted else None


def sync() -> bool:
    """Rebase the runner's local critic commits on origin/main. Never touches a dirty tree."""
    if git("status", "--porcelain", "--untracked-files=no").stdout.strip():
        log("SKIP pull: tracked files have uncommitted changes in the runner clone")
        return False
    p = git("pull", "--rebase", "-q", "origin", "main")
    if p.returncode != 0:
        git("rebase", "--abort")
        log(f"SKIP pull: {p.stderr.strip()[-300:]}")
        return False
    return True


def publish(push: bool) -> None:
    subprocess.run([sys.executable, str(REPO / "loop" / "snapshot.py"), "--repo", str(REPO)], cwd=REPO,
                   capture_output=True, text=True)
    if git("status", "--porcelain", "--", "data/snapshot.json").stdout.strip():
        git("add", "data/snapshot.json")
        git("commit", "-q", "-m", "snapshot: dashboard data after a watch cycle", "--", "data/snapshot.json")
    ahead = git("log", "--format=%h %an %s", "origin/main..HEAD").stdout.strip()
    if not ahead:
        return
    if not push:
        log("not pushed (run with --push):\n" + ahead)
        return
    if not sync():
        log("push postponed to the next cycle")
        return
    p = git("push", "-q", "origin", "HEAD:main")
    log(("pushed:\n" + ahead) if p.returncode == 0 else f"push failed: {p.stderr.strip()[-300:]}")


def cycle(a: argparse.Namespace, state: dict) -> None:
    state["cycles"] += 1
    n = state["cycles"]
    sync()
    fp = data_fingerprint(REPO / "data" / "samples.jsonl")
    new_data = accepted_data_sha(REPO / a.metrics) != fp["sha"]
    momentum = state.get("last_outcome") == "accepted"
    if not (new_data or momentum or a.force):
        log(f"cycle {n}: idle, dataset {fp['sha']} ({fp['n_samples']} samples) unchanged and the last cycle "
            "improved nothing; waiting for new data")
        return
    left = a.max_cost - state["spent_usd"]
    if left < ITERATION_MIN_USD:
        log(f"cycle {n}: STOP, ${state['spent_usd']:.2f} spent of ${a.max_cost:.2f}; raise --max-cost to continue")
        state["stopped"] = True
        return
    reason = f"new data ({fp['n_samples']} samples, {fp['sha']})" if new_data else ("the last cycle improved" if momentum else "forced")
    log(f"cycle {n}: running the critic, reason: {reason}, budget left ${left:.2f}")
    cmd = [sys.executable, "-u", str(REPO / "loop" / "critic.py"), "--iterations", str(a.iterations),
           "--max-cost", f"{left:.2f}", *shlex.split(a.critic_args)]
    try:
        p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=a.cycle_timeout)
        out, rc = p.stdout + p.stderr, p.returncode
    except subprocess.TimeoutExpired as e:
        out, rc = (e.stdout or "") if isinstance(e.stdout, str) else "", 124
    with open(CRITIC_OUT, "a") as f:
        f.write(f"===== cycle {n} {now()}\n{out}\n")
    spent = max((float(x or y) for x, y in SPENT.findall(out)), default=0.0)
    state["spent_usd"] = round(state["spent_usd"] + spent, 4)
    accepted = "ACCEPTED v" in out
    refreshed = "data refresh:" in out
    state["last_outcome"] = "accepted" if accepted else ("failed" if rc else "rejected")
    log(f"cycle {n}: critic rc={rc}, {'a version was accepted' if accepted else 'nothing accepted'}"
        f"{', baseline refreshed on the new data' if refreshed else ''}, spent ${spent:.2f} "
        f"(total ${state['spent_usd']:.2f} of ${a.max_cost:.2f})")
    if accepted or refreshed:
        publish(a.push)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=int, default=600, help="seconds between cycles")
    ap.add_argument("--once", action="store_true", help="run one cycle and exit")
    ap.add_argument("--push", action="store_true", help="push accepted versions and the snapshot to origin/main")
    ap.add_argument("--force", action="store_true", help="run the critic on the first cycle even with nothing new")
    ap.add_argument("--iterations", type=int, default=1, help="critic iterations per cycle")
    ap.add_argument("--max-cost", type=float, default=float(os.environ.get("TENFOLD_WATCH_MAX_COST_USD", "20")),
                    help="USD cap across all cycles, kept in loop/watch-state.json")
    ap.add_argument("--metrics", default="data/metrics.json")
    ap.add_argument("--critic-args", default="", help='extra flags for loop/critic.py, e.g. "--skip-heldout"')
    ap.add_argument("--cycle-timeout", type=int, default=3600)
    a = ap.parse_args()
    state = load_state()
    state.pop("stopped", None)
    log(f"watch start: every {a.every}s, {a.iterations} iteration(s) per cycle, budget ${a.max_cost:.2f} "
        f"(${state['spent_usd']:.2f} already spent), push={'on' if a.push else 'off'}")
    first = True
    while True:
        force = a.force
        a.force = force and first
        cycle(a, state)
        a.force, first = force, False
        save_state(state)
        if a.once or state.get("stopped"):
            break
        time.sleep(a.every)
    return 0


if __name__ == "__main__":
    sys.exit(main())
