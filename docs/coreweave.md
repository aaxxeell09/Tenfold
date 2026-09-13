# Tenfold on CoreWeave

Where Tenfold runs on CoreWeave (through W&B Inference), how to see it in Weave, and how to reproduce every number.
Weave project: https://wandb.ai/ilan-sainteagathe-synthetic-swarm/tenfold/weave
W&B Report with the same evidence: https://wandb.ai/ilan-sainteagathe-synthetic-swarm/tenfold/reports/Tenfold-on-CoreWeave:-W&B-Inference-and-Weave-evidence--VmlldzoxNzkyNDE2Mw==

| Part | On W&B Inference | What breaks without it | Proof in Weave |
|---|---|---|---|
| Tally's voice, live app | every Tally line, rewritten by Qwen3 235B from Tally's fixed phrase | Tally repeats the same fixed sentences | op `tutor.call`: line, served model, tokens, latency |
| Model choice | 5 candidate models and a judge model | the voice model is a guess | Evaluation `tally-voice`, Leaderboard `tally-voice-leaderboard` |
| The loop | diagnostic agent and guard agent (`TENFOLD_TEXT_AGENTS=wandb`) | every agent runs on Claude Code, several times the Anthropic spend | op `critic.inference` under `critic.diagnose` and `critic.guard_agent` |
| Preflight | a five token call in `make doctor` | a dead key is found on stage | doctor line `W&B Inference` |

## 1. Tally's voice in the live app

`app/server.py` builds every Tally line through `make_phrase()`. With `WANDB_API_KEY` set, that is
`lesson.tutor.Tutor(fallback=tally.phrase).phrase`: Tally's fixed phrase is returned at once, and the model's line
is fetched in a background thread and shown on the next update of the same moment. The model gets the moment and
Tally's own line, and says the same thing in its own words, keeping every number (`lesson.tutor.build_messages`).
`TENFOLD_TUTOR=0` keeps the fixed phrases for a stage with no network.

Checked live on 2026-09-13 with `app/server.py --mock --demo` and a real lesson session: 27 `tutor.call` traces,
every call HTTP 200, the model's line on screen about 0.4 s after the fixed phrase.

| Fixed phrase | W&B Inference, Qwen3 235B |
|---|---|
| Almost. Your left hand is good. Move your right finger from 8 to 7. | Almost. Keep your left hand as it is. Move your right finger from 8 to 7. |
| Show me both hands, palms towards me. | Show me both hands, palms facing me, ready for 9 times 7. |
| That is it. Now count the tens, then the ones. | That's right! Count the tens first, then the ones. |

## 2. Which model speaks for Tally

`make tutor-eval` (`eval/tutor_eval.py`) runs one Weave Evaluation per W&B Inference model over 40 lesson moments
from `lesson/tally.py`, with the live tutor's exact prompt, max_tokens and temperature. Scored in code on Tally's
rules, plus a judge model on W&B Inference (DeepSeek V3.1, never a candidate) for warmth and clarity to a 7 year old.

| Model | safe words | 20 words max | no spoiler | numbers kept | in 1.5 s | warmth /5 | clarity /5 | mean latency |
|---|---|---|---|---|---|---|---|---|
| Qwen3 235B A22B Instruct (default) | 1.00 | 1.00 | 1.00 | 0.93 | 1.00 | 4.08 | 4.45 | 0.80 s |
| Qwen3 30B A3B Instruct | 1.00 | 1.00 | 1.00 | 0.98 | 1.00 | 3.90 | 4.23 | 0.78 s |
| Llama 3.3 70B Instruct | 1.00 | 1.00 | 1.00 | 0.88 | 1.00 | 3.45 | 4.10 | 0.64 s |
| DeepSeek V4 Flash | 1.00 | 1.00 | 1.00 | 0.80 | 0.75 | 3.70 | 3.90 | 1.28 s |
| gpt-oss 120B | 0.00 | 0.00 | 1.00 | 0.45 | 0.00 | | | 2.69 s |

Qwen3 235B stays the default: best warmth and clarity, every hard rule passed. gpt-oss 120B spends the 60 token
budget on reasoning and returns no line. Leaderboard:
https://wandb.ai/ilan-sainteagathe-synthetic-swarm/tenfold/weave/leaderboards/tally-voice-leaderboard

## 3. The loop

`TENFOLD_TEXT_AGENTS=wandb` puts the two agents that only read and write text on W&B Inference. The patch agent
needs tools (Read, Edit, `train_eval.py`) and stays on Claude Code. The runner clone's `.env` already sets it, so
every watcher cycle uses it.

One real iteration on 2026-09-13, in an isolated critic worktree with `--no-commit`:

| Step | Backend | Result |
|---|---|---|
| diagnostic agent | Qwen3 235B on W&B Inference | 4,856 tokens, about 3 s |
| patch agent | Claude Code | measured and refuted the diagnosis's guess, lowered `CONTACT_THRESHOLD` 0.2975 to 0.2826 |
| guard code and guard agent | Qwen3 235B on W&B Inference | 1,312 tokens, `VERDICT: APPROVE` |
| metric gate | code | accepted: exact_match 0.487 to 0.490, near_contact_accuracy 0.733 to 0.767, no class regressed |
| Anthropic spend | | $0.85 for the whole iteration |

That run did not commit: only the watcher commits to main. It shows the backend works end to end; the next
watcher cycle reproduces it on main.

Reproduce: `python loop/critic.py --iterations 1 --text-agents wandb --dry-run`.

## 4. Still open

**Held-out score (SPEC section 15, items 10 and 13).** Not run yet: `tenfold-heldout` holds one smoke call and no
Evaluation, and `make watch` passes `--skip-heldout`. On the machine that has `../tenfold-heldout/test.jsonl` and
`WANDB_API_KEY_HELDOUT` in `.env`:

```
git show 85a08c9:classifier/rules.py > /tmp/rules_v0.py
python eval/run_eval.py --split heldout --rules /tmp/rules_v0.py --tag v0
python eval/run_eval.py --split heldout --tag v1
python loop/snapshot.py
```

**For Axel (his files).**
- `app/server.py`: `make_phrase()` and `Lesson.phrase` are new; `build_message` takes `phrase=`, default `tally.phrase`. Tests in `tests/test_server_tutor.py`.
- The model's line replaces the fixed phrase within the same moment, and `web/course/app.js` speaks every new line, so the voice can cut the first sentence. If it sounds jarring on the demo speaker, speak only the model line when it arrives within the window, or run the demo with `TENFOLD_TUTOR=0`.
- The model once added a direction that Tally's line did not give ("move your right finger down a little"); `keeps_numbers` does not catch that.
- README "Sponsor tools" (SPEC section 16): the table at the top of this file is ready to paste.
