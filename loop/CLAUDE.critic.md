# Tenfold critic rules (this file replaces CLAUDE.md inside the critic worktree)

You improve the hand-gesture classifier in classifier/rules.py. That is the only file you may edit.

## Success
exact_match on the train evaluation goes up and false_unknown_rate does not rise (part of it is tracker hand loss, which no rule can recover). Other scorers (contact_accuracy, per_class_accuracy, negative_rejection_accuracy, near_contact_accuracy) are diagnostic signals, not targets.

## Inputs
- eval/last_train_report.json: per-class accuracy, the 10 worst samples reduced to fingertip distances.
- If data/train.jsonl exists here: `python loop/train_eval.py` evaluates your current rules.py on the train set
  (metrics, worst classes, failing samples with per-frame nearest pairs; `--class 7x8` to focus). Run it before
  and after your edit. Do not create scratch files or run other commands: they are denied.
- The W&B MCP server (read only) if you need more detail on the train evaluation.

The held-out set is not in this worktree and never will be.

## One iteration = one hypothesis, one coherent patch, one justification, one expected effect
State the most frequent failure and your hypothesis about its cause before editing.

## The guard will reject your patch if
- any file other than classifier/rules.py changes, or an untracked file appears
- rules.py no longer imports, or exceeds 400 lines, or the diff exceeds 80 lines
- rules.py imports anything outside math, statistics, dataclasses, typing, numpy, numpy.linalg, classifier.schema, classifier.features (no relative or star imports)
- rules.py uses open, exec, eval, compile, __import__, getattr, setattr, globals, vars, help, exit, sys, os, inspect, importlib, subprocess, builtins, ctypes, operator or types, as a name or as an attribute
- rules.py reads or writes files or evaluates strings on any object: np.load, np.save, np.loadtxt, np.genfromtxt, np.fromfile, np.memmap, arr.tofile, typing.get_type_hints
- rules.py reads an attribute that starts with an underscore (x.__class__, dataclasses._create_fn, obj._cache); module-level helpers named _like_this are fine
- rules.py reaches a module through an allowed one (np.lib, np.ctypeslib, dataclasses.sys, features.np.random), or stores, passes or assigns a module (m = np, features.nearest_pair = ...)
- classify() raises, is too slow, or returns out-of-domain values on the smoke windows
- the module body or classify() touches files, processes, the network, environment variables or new imports at runtime (blocked and rejected even if caught), exits, or prints a verdict
- any tool input references a path outside this worktree
The smoke test only runs once every static rule passes; `GUARD_NOTE smoke not run` means fix the rejections above it first. Run `python loop/guard.py --check` before you finish: it runs the smoke test the way the guard does, which `python loop/smoke.py` alone does not. A rejection comes back as `GUARD_REJECT <rule>: <what> | cause: <why> | fix: <what to change>`.

## Style
No em dashes. Comments explain the hypothesis, not the mechanics.
