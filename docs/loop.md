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

Weave: project `tenfold` holds one evaluation per measured version, labelled `tenfold-train-<tag> <run>
<verdict>`: `v0 ... baseline`, `vN-candidate ... candidate` for every patch the gate measured, and
`vN ... accepted` again for each one it accepted, so the comparison to show is `v0 baseline` against the latest
`accepted`. It also holds every critic call (`critic.diagnose`,
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
   guard's self-check, the prompts, `eval/last_train_report.json`, a copy of the train set with
   `loop/train_eval.py`, and a critic-only `CLAUDE.md`. No spec, no held-out data, no eval pipeline, no `.env`,
   no git tools.
2. Diagnostic agent: reads the train report (per-class accuracy, ten worst samples as fingertip distances),
   writes one diagnosis and one hypothesis. With `--text-agents wandb` (or `TENFOLD_TEXT_AGENTS=wandb` in
   `.env`) this agent and the guard agent run on W&B Inference (Qwen3 235B, served on CoreWeave, billed to W&B
   credits, traced in Weave with token usage); each call is kept as `loop/transcripts/diag-N.json`. The patch
   agent stays on Claude Code because it needs tools.
3. Patch agent: edits `rules.py` only, may run `python loop/train_eval.py` (train metrics and failing samples,
   before and after its edit; `--sweep NAME=v1,v2,...` scores several values of a constant in one call, each with the metric gate's
   verdict from `loop/gate.py`, the same code the critic decides with; it starts with `--sweep-all`, every
   numeric constant tried around its value, before building new logic),
   `python loop/guard.py --check` and `python loop/smoke.py`. The diagnosis is a guess made without running
   anything, so the patch agent may fix the named failures with another mechanism, or, when measurement
   refutes the diagnosis, fix the failure it measured instead; either way it states its hypothesis with the
   train numbers. Its prompt announces its turn budget (45 by default); if it runs out of turns or money, the
   edit it left still goes through the guard and the gate instead of being thrown away.
4. Guard agent: sees the diagnosis, the patch agent's closing account (HYPOTHESIS, PATCH, EXPECTED) and the
   diff, no tools. It rejects a diff that acts on neither the diagnosis nor the measured failure the account
   names, or that games the metric (refusing more often, best-case confidence, memorised values).
5. `loop/guard.py` (pinned copy, outside the worktree): only `rules.py` changed, importable, diff under 80
   lines, file under 400 lines, AST import whitelist, no `open`/`exec`/`getattr`/`sys`/`os`/`inspect`, six
   synthetic windows pass, `classify()` p95 under 5 ms (the live frame budget is 100 ms and MediaPipe takes
   most of it), transcript audit (no path outside the worktree, no mention of the held-out set).
   Each failure is one `GUARD_REJECT rule: what | cause: why | fix: change` line fed back to the patch agent.
   Two rejections and the iteration is dropped.
6. Train evaluation on the candidate, then the metric gate: `exact_match` must strictly improve, no class and
   no capture condition (`per_slice`: camera angle, distance; slices of 20 samples or more) may lose more than
   5 points, `false_unknown_rate` may not rise by more than 2 points. Anything else is reverted
   and logged, so every critic commit in the history is a measured improvement.
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
`../tenfold-blind` whose diagnostic step gets no failure data ("improve the classifier"), with no train copy,
no report and no evaluation tool. The blind arm keeps its rules in `loop/blind-rules.py` and never commits.
Both are scored on the same held-out set. The headline chart is informed versus blind: if the failure data
did not matter, the two curves would overlap.

Money is capped per arm by `TENFOLD_MAX_COST_USD` (default 40 USD) or `--max-cost`. An iteration only starts
with at least 2 USD left. Every claude call runs with `--max-budget-usd` set to what is left, and the patch
agent's cap keeps 0.50 USD back so the guard agent can still judge the edit it leaves. The CLI checks its cap
between model turns, so one call can overshoot by at most one turn.

## Rehearse without touching the real projects

```
make rehearse            # real agents on hard synthetic data in a throwaway clone, Weave projects *-smoke
make rehearse-mock       # same with a fake claude, no API, no W&B
python loop/rehearse.py --nightly --mock --iterations 2   # both arms in parallel through nightly.sh
```

## Where the real loop runs

The canonical runner is a separate clone, `../tenfold-run`, with its own `.env`. The critic commits there
and those commits are pushed to `main` after each run, so no human working copy ever shares a worktree
with the loop.

## Continuous: new captures in, better perception out

```
caffeinate -i ../tenfold/.venv/bin/python loop/watch.py --push --max-cost 20 --critic-args=--skip-heldout
```

Pass critic flags with `=` (`--critic-args=--skip-heldout`): a separate value that starts with `--` and has no
space is read as an option of `watch.py` itself.

`loop/watch.py` runs in the runner clone. Every 10 minutes it rebases on `origin/main`. It runs the critic when
the dataset changed since the last accepted version (a new capture was pushed) or when the previous cycle got a
version accepted; otherwise it waits and spends nothing. Every version in `data/metrics.json` records the
fingerprint of the data it was measured on (`data.sha`, `data.n_samples`). When the critic starts on a
different dataset it first re-evaluates the running rules as a `data_refresh` version (tag `vN-dataK`), so the
gate never compares a candidate on today's data with a baseline on yesterday's. Accepted versions and a fresh
`data/snapshot.json` are pushed, so the dashboard shows each data refresh as its own point: the score can drop
when harder captures arrive, then climb back as the critic adapts. The money cap holds across cycles
(`loop/watch-state.json`); the log is `loop/watch.log`, the critic's output `loop/watch-critic.out`.

## Validation rétrospective, participants non identifiés

```
make split             # eval/split.py: seed 42, about 20 % of whole holds set aside
make retro-validate    # eval/retro_validate.py: V0 vs the running rules on those holds, local only
```

The first critic iterations read the whole dataset, so the holds set aside now are a retrospective check, not
unseen data and not a test on other people: the dataset records one session and one person label, which do not
identify participants. Never publish these scores as held-out or "other people".

- Groups are `session::hold_id`, so every window of a hold stays on one side. Byte-identical windows glue their
  holds into one unit, and a unit spanning several holds stays in train (on the first dataset: windows with no
  hand detected, shared by holds of many classes). Classes are the strata. `manifest.json` records the seed, the
  source sha256, every group on each side, counts per class, rare classes and the limits.
- `data/splits/<name>/train.jsonl` stays in the repo (gitignored). `validation.jsonl` and the manifest go to
  `../tenfold-validation/<name>/` (mode 700), outside the repo and the critic worktree. `loop/guard.py` rejects any
  critic tool call that mentions `tenfold-validation` or `validation.jsonl`.
- The loop keeps reading the whole dataset until `TENFOLD_TRAIN_SAMPLES=data/splits/<name>/train.jsonl` is set in
  `.env`; `critic.py`, `run_eval.py`, `baseline_knn.py` and `watch.py` then read the train side only, and the next
  critic run records a data refresh. A split is a snapshot: captures pushed later reach the loop through a new split
  under a new name, and `watch.py` logs a warning until then.
- `eval/scorers.py` is frozen and scores `output=None` as a correct `unknown` on negatives, against SPEC.md 5.7.
  `retro_validate.py` reports every version with the frozen scorers and with `eval/strict.py`, where a missing
  prediction fails, applied identically to both versions.
