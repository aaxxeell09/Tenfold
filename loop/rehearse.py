"""Rehearse the loop end to end on hard synthetic data, in a throwaway clone. Never touches this repo.

    python loop/rehearse.py --iterations 2          # real claude; publishes to tenfold-smoke / tenfold-heldout-smoke
    python loop/rehearse.py --iterations 2 --mock   # fake claude, no API, no W&B
    python loop/rehearse.py --blind                 # rehearse the ablation arm

Uses the committed state of this repo (git clone), a hard synthetic train set standing in for Axel's capture,
and a separate hard synthetic held-out set standing in for other people's hands. Ends with data/snapshot.json.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from loop import synth  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=2)
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--blind", action="store_true")
    ap.add_argument("--dir")
    a = ap.parse_args()
    d = Path(a.dir or tempfile.mkdtemp(prefix="tenfold-rehearsal-"))
    d.mkdir(parents=True, exist_ok=True)
    clone = d / "tenfold"
    subprocess.run(["git", "clone", "-q", str(REPO), str(clone)], check=True)
    (clone / "data").mkdir(exist_ok=True)
    (d / "tenfold-heldout").mkdir(exist_ok=True)
    synth.write_jsonl(synth.synthetic_dataset(seed=11, hard=True, session="axel", holds_per_class=4), clone / "data" / "samples.jsonl")
    heldout = d / "tenfold-heldout" / "test.jsonl"
    synth.write_jsonl(synth.synthetic_dataset(seed=29, hard=True, session="other", split="test", holds_per_class=4), heldout)
    if (REPO / ".env").exists() and not a.mock:
        shutil.copy(REPO / ".env", clone / ".env")
    env = {**os.environ, "TENFOLD_PROJECT_SUFFIX": "-smoke", "TENFOLD_HELDOUT": str(heldout), "PYTHONPATH": str(clone)}
    cmd = [sys.executable, "-u", str(clone / "loop" / "critic.py"), "--repo", str(clone), "--iterations", str(a.iterations),
           "--timeout", "900"]
    if a.mock:
        cmd += ["--mock-claude", str(clone / "tests" / "fake_claude.py"), "--local"]
    if a.blind:
        cmd.append("--blind")
    print(f"rehearsal in {d}", flush=True)
    rc = subprocess.run(cmd, cwd=clone, env=env).returncode
    subprocess.run([sys.executable, str(clone / "loop" / "snapshot.py"), "--repo", str(clone)], env=env)
    return rc


if __name__ == "__main__":
    sys.exit(main())
