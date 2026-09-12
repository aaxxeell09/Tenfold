# Tenfold, Claude Code rules

Read SPEC.md before any task. SPEC.md is the source of truth. If a request contradicts SPEC.md, say so and stop. Section 19 of SPEC.md lists the amendments already folded into the text.

## Execution Rules
- When 2 or more tasks are independent (different files, no shared state), spawn parallel subagents, one per task.
- Sequential only when task B needs task A's output or both touch the same file.
- State the parallelization plan before starting, in one line.

## Frozen after the contract session (never edit without both team members agreeing)
app/landmarks.py, app/normalize.py, classifier/schema.py, classifier/features.py, data/samples.jsonl, eval/scorers.py

## The critic
Only loop/critic.py may modify classifier/rules.py after V0, through the guarded loop in SPEC.md section 7.
The critic runs in a stripped worktree (../tenfold-critic) with loop/CLAUDE.critic.md; it never sees this file, SPEC.md, eval/, data/ or .env.
Never read samples with split == "test" outside eval/run_eval.py. Held-out data lives in ../tenfold-heldout/, never in this repo.
Never pass test metrics to the critic. Held-out evaluation runs in a subprocess with WANDB_API_KEY_HELDOUT.

## Style
- No em dashes or en dashes anywhere: code comments, docstrings, commit messages, README.
- Commit every 30 minutes at most. Commit message says what changed and why. Never commit to main while the nightly loop holds the worktree.
- No secrets in the repo. .env is gitignored. .env.example lists variable names only. MCP servers are registered at user scope, never project scope.
- No images stored, only landmarks.
- Python 3.11, type hints, dataclasses, pytest. Every new codepath gets its test in the same commit.

## Weave
- weave.init("tenfold") only when WANDB_API_KEY is set, in app/main.py and eval/run_eval.py. run_eval.py --local runs without W&B.
- In the live app, trace events only, never every frame.
- Evaluations are named tenfold-train-vN (project tenfold) and tenfold-test-vN (project tenfold-heldout), git hash as attribute; ablation runs are suffixed -blind.

## Done means
The 14 items in SPEC.md section 15. Nothing else is a priority.
