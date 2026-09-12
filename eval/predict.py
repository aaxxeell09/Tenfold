"""Run a rules.py over a samples file in an isolated process and print one prediction per line.

    python -m eval.predict --rules PATH --samples PATH > predictions.jsonl

run_eval.py launches this with cwd in a temp directory so a rules file cannot reach the repo by relative path
(the guard's AST whitelist forbids file access anyway). Any exception inside classify() becomes output=null.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from classifier.schema import GestureState, window_from_json


def load_rules(path: Path):
    spec = importlib.util.spec_from_file_location("tenfold_candidate_rules", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod.classify


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", required=True)
    ap.add_argument("--samples", required=True)
    args = ap.parse_args()
    classify = load_rules(Path(args.rules).resolve())
    with open(args.samples) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            sample = json.loads(line)
            try:
                state = classify(window_from_json(sample["window"]))
                out = state.to_dict() if isinstance(state, GestureState) else None
                err = None if isinstance(state, GestureState) else f"returned {type(state).__name__}"
            except Exception as e:  # a candidate rules file must never abort the evaluation
                out, err = None, f"{type(e).__name__}: {e}"
            sys.stdout.write(json.dumps({"id": sample["id"], "output": out, "error": err}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
