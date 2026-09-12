# The loop

Tenfold's perception layer, `classifier/rules.py`, rewrites itself. Nobody edits that file after V0. This page
says how to read the evidence, how to run one iteration yourself, and why the held-out score is honest.

## Read the evidence in 60 seconds

```
git log --author=critic-agent --format='%h %ad %s' --date=short
```

Every commit by `critic-agent` is one accepted iteration: `critic vN: <diagnosis> | patch: <what changed> |
expected: <effect>`. `git show <sha> -- classifier/rules.py` is the diff the agent wrote. `data/BEST_VERSION`
is the commit the live app runs. `data/metrics.json` has train and held-out metrics per version, and the
rejected patches with the reason (`rejected` list). Rejections are part of the loop: an agent that never gets
told no is not being measured.

Weave: project `tenfold` holds `tenfold-train-vN` evaluations and every critic call (`critic.diagnose`,
`critic.patch`, `critic.guard_agent`, `critic.guard_code`, `critic.metric_gate`, `critic.rejected_patch`,
`critic.heartbeat`). Project `tenfold-heldout`, on a different W&B account, holds `tenfold-heldout-vN`.

## One iteration

```
make doctor              # preflight: python 3.11, keys, claude auth, smoke test
make dry-run             # diagnostic -> patch -> guard agent -> guard.py, prints the diff, commits nothing
make loop N=1            # the same, then train eval, metric gate, commit, held-out eval
```

What happens inside `loop/critic.py`:

1. A stripped worktree (`../tenfold-critic`) is reset: `rules.py`, the frozen contract, the smoke test, the
   guard's self-check, the prompts, `eval/last_train_report.json`, and a critic-only `CLAUDE.md`. No spec,
   no data, no eval code, no `.env`, no git tools.
2. Diagnostic agent: reads the train report (per-class accuracy, ten worst samples as fingertip distances),
   writes one diagnosis and one hypothesis.
3. Patch agent: edits `rules.py` only, may run `python loop/guard.py --check` and `python loop/smoke.py`.
4. Guard agent: sees only the diagnosis and the diff, no tools; approves or rejects.
5. `loop/guard.py` (pinned copy, outside the worktree): only `rules.py` changed, importable, diff under 80
   lines, file under 400 lines, AST import whitelist, no `open`/`exec`/`getattr`/`sys`/`os`/`inspect`, six
   synthetic windows pass, transcript audit (no path outside the worktree, no mention of the held-out set).
   Each failure is one `GUARD_REJECT rule: what | cause: why | fix: change` line fed back to the patch agent.
   Two rejections and the iteration is dropped.
6. Train evaluation on the candidate, then the metric gate: `exact_match` must not drop, no class may lose
   more than 5 points, `false_unknown_rate` must stay under 5 %. A regression is reverted and logged.
7. Commit as `critic-agent`, update `data/BEST_VERSION`, then the held-out evaluation in a subprocess.

## Why the held-out score is honest

- The held-out set is other people's hands (train is one person), all angles, captured by a different person.
- It lives outside every worktree (`../tenfold-heldout/test.jsonl`), mode 700.
- It is evaluated by a subprocess whose W&B key (`WANDB_API_KEY_HELDOUT`) belongs to a different account
  than the critic's. The critic's environment never contains that key, so its MCP access cannot see the
  project.
- The guard audits the agent's tool inputs and text for any path outside the worktree or any mention of
  the held-out data. This is defense in depth; the real control is that the data is not reachable.
- Nothing stops the loop or picks a version based on the held-out score. The version shown is the last one
  the metric gate accepted on train.

## The ablation

`loop/nightly.sh` runs two arms overnight: the informed critic on `main`, and a blind critic in
`../tenfold-blind` whose diagnostic step gets no failure data ("improve the classifier"). Both are scored on
the same held-out set. The headline chart is informed versus blind: if the failure data did not matter, the
two curves would overlap.

## Rehearse without touching the real projects

```
TENFOLD_PROJECT_SUFFIX=-smoke make loop N=2      # publishes to tenfold-smoke / tenfold-heldout-smoke
python loop/critic.py --iterations 2 --mock-claude tests/fake_claude.py --local   # no API at all
```
