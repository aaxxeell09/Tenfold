# Tenfold critic rules (this file replaces CLAUDE.md inside the critic worktree)

You improve the hand-gesture classifier in classifier/rules.py. That is the only file you may edit.

## Success
exact_match on the train evaluation goes up while false_unknown_rate stays under 5%. Other scorers (contact_accuracy, per_class_accuracy, negative_rejection_accuracy, near_contact_accuracy) are diagnostic signals, not targets.

## Inputs
- eval/last_train_report.json: per-class accuracy, the 10 worst samples reduced to fingertip distances.
- The W&B MCP server (read only) if you need more detail on the train evaluation.

## One iteration = one hypothesis, one coherent patch, one justification, one expected effect
State the most frequent failure and your hypothesis about its cause before editing.

## The guard will reject your patch if
- any file other than classifier/rules.py changes, or an untracked file appears
- rules.py no longer imports, or exceeds 400 lines, or the diff exceeds 80 lines
- rules.py imports anything outside math, statistics, dataclasses, typing, numpy, classifier.features, or uses open, exec, eval, __import__, getattr, globals, sys, inspect
- classify() raises or returns out-of-domain values on the smoke windows (run `python loop/smoke.py` yourself)
- any tool input references a path outside this worktree
Run `python loop/guard.py --check` before you finish. A rejection comes back as `GUARD_REJECT <rule>: <what> | cause: <why> | fix: <what to change>`.

## Style
No em dashes. Comments explain the hypothesis, not the mechanics.
