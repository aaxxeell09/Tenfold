# Nimble: scoring the tutor's interventions

Nimble's tutor (Tally's voice, `lesson/tutor.py`, internal ids still `tenfold`) rewrites each Tally line with a model
on W&B Inference. One set of rules in code, `lesson/tutor_rules.py` (version `tutor-rules-v2`), scores those lines in
two places:

| Where | What is scored | Where to see it |
|---|---|---|
| Offline, `eval/tutor_eval.py` | 40 lesson moments built from `lesson/tally.py`, one Weave Evaluation per model | Evaluation `tally-voice-v2`, Leaderboard `tally-voice-leaderboard-v2` |
| Live, every lesson | every traced `tutor.call`, automatically | feedback `wandb.runnable.tutor_rules_v2` on that call, plus a `tutor_delivery` note |

Weave project: https://wandb.ai/ilan-sainteagathe-synthetic-swarm/tenfold/weave

## 1. A lesson and its scored traces

```bash
make run        # webcam; or: make demo  (the scripted demo, no camera)
```

With `WANDB_API_KEY` in `.env`, `app/server.py` routes Tally through `lesson.tutor.Tutor.phrase`. For each model line:

1. `tutor.call` is traced with only the event, the exercise, the expected correction (hand, from, to), the typed
   answer on an answer moment, and the model. No child name, no detected fingers, no image.
2. `Call.apply_scorer` (Weave SDK 0.53.9) runs `tutor_rules_v2` on that call. The result is stored by Weave as
   feedback on the call (`client.get_call(id).feedback`, or the call's Feedback tab).
3. A `tutor_delivery` feedback says what became of the line (below).

Scores and notes go through one bounded queue and one thread (`ScoreQueue`, 32 jobs). A full queue drops and counts
(`tutor.scoring.stats`). A missing or unreachable Weave never stops the lesson: the model line or Tally's canonical
line is shown as before.

In Weave: Traces, filter op `tutor.call`, open a call, Feedback tab.

### `tutor_delivery`: generated is not spoken

| status | meaning |
|---|---|
| `handed_to_server` | `Tutor.phrase` returned the model line to `app/server.py` for a state message |
| `late_held` | the line came after the 1.5 s timeout, was not shown in its moment, and is kept for the next time |
| `moment_ended_unserved` | the moment changed before the line was returned |
| `generation_failed`, `generation_empty` | the model call raised or said nothing; Tally's canonical line (the fallback) stayed |
| `handed_to_callback`, `dropped_late` | the same for `Tutor.say` |

A canonical fallback line is not a model call, so it has no trace and no score. No status says the page received a
line or that it was spoken: the page sends `tts` start and end without the text, so nothing ties a sound to a line.
A scored generation is a generation, not a heard intervention.

## 2. The offline evaluation

```bash
make tutor-eval                                          # every candidate model, published to Weave
python eval/tutor_eval.py --local --limit 6              # no Weave, prints the table
```

Same prompt, same settings and same rules as the live tutor, plus `keeps_numbers` (every number of Tally's line is
said), which needs Tally's own line and runs offline only. A judge model rates warmth and clarity, as before. Each
fraction is pass among pass or fail; `not_verifiable` is counted apart. The earlier `tally-voice` Evaluation used the v1
rules and is not comparable. No gesture data, held-out or validation, is used here.

## 3. What each score measures, and its limits

| rule | fails when | not applicable / not verifiable |
|---|---|---|
| `non_empty` | the reply is empty | |
| `concise` | more than 20 words | nothing was said |
| `safe_words` | says wrong, no or incorrect | nothing was said |
| `no_spoiler` | says the product before `answer_correct`: digits, English words (`fifty six`, `fifty-six`, `fiftysix`), or `5 tens and 6 units` | no exercise; the answer is validated; an exercise title it cannot read |
| `correction_consistency` | on `wrong_left_finger`, `wrong_right_finger`, `hint_1`, `hint_2`, `same_hand_twice`: moves the other hand, swaps from and to, or names another target | not a correction moment; the hint lacks a hand or a target; no hand named; numbers without from or to; both hands in the move |
| `in_time` | latency over the tutor timeout (timing, not content) | no latency recorded |

Limits:
- Patterns, not understanding. English only, the language the page speaks. A phrasing the patterns do not cover
  gives `not_verifiable`, never `pass`.
- The rules check the reply against the context the tutor was given. They do not check that the camera read the
  pose right, that the line is warm, or that the child learned anything. A child's correct answer after a line is
  not proof the line caused it.
- No overall score, confidence or pedagogical grade is computed.

## 4. The 60 second demo

1. **Dataset** (10 s). `eval/tutor_eval.py moments()`: 40 lesson moments, each with Tally's own line. In Weave:
   Dataset `tally-moments`.
2. **Evaluation** (15 s). `make tutor-eval`, run once beforehand (about 400 W&B Inference calls with the judge).
   Leaderboard `tally-voice-leaderboard-v2`: per model, `no_spoiler`, `correction_consistency`, `in_time` and the
   rest. As of 2026-09-13 the v2 Evaluation has not been published yet; only v1 `tally-voice` exists.
3. **Live interaction** (20 s). `make demo`, make a wrong pose: Tally's fixed line, then the model's line.
4. **Scored trace** (15 s). Traces, op `tutor.call`, the newest call: its inputs (event, exercise, hint, no name),
   Feedback `tutor_rules_v2` with a status and a reason per rule, and `tutor_delivery`.

Without a camera, `python eval/tutor_live_check.py --env ../tenfold/.env` sends two synthetic moments through the
same `Tutor.phrase` path (two short W&B Inference calls), renames the calls `nimble synthetic check`, reads the
feedback back from Weave and prints the links. Checked on 2026-09-13:
https://wandb.ai/ilan-sainteagathe-synthetic-swarm/tenfold/r/call/01a09c2c-c243-723b-b35b-9a35d96dfe59
(`wrong_right_finger`, every rule pass, `tutor_delivery` `moment_ended_unserved`).
