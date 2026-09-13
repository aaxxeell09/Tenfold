# Interfaces between the two lanes

Ilan owns `classifier/rules.py`, `eval/`, `loop/`, `lesson/tutor.py`. Axel owns `app/`, `lesson/engine.py`,
`lesson/tally.py`, the capture and `dashboard/`. This page is everything one side needs from the other.

## 1. Classifier in the live app (Ilan provides, Axel calls)

```python
from classifier.loader import load_classifier

classify = load_classifier()     # rules.py pinned at data/BEST_VERSION (HEAD if none yet); never raises
state = classify(window)         # window: classifier.schema.Window built by app/normalize.py
state.method, state.left, state.right, state.contact, state.confidence
```

`classify` wraps any exception inside a critic-written `rules.py` and returns `GestureState.unknown()`, so the
demo never crashes on a bad version. Reload it (call `load_classifier()` again) only between exercises.

## 2. Tutor (Ilan provides, Axel calls from the engine events)

```python
from lesson.tutor import Tutor

tutor = Tutor(fallback=tally.phrase)      # tally.phrase(event: str, ctx: dict) -> str
tutor.say(event, exercise="7 x 8", detected=state.to_dict(), child="Leo",
          on_phrase=ui.set_phrase, state_id=engine.state_id, operands=(7, 8), answer=None)
```

- `on_phrase(text, state_id, source)` is called at once with the fallback (`source="fallback"`), then again with
  the model's line (`source="model"`) only if it arrives within 1.5 s. The UI ignores a phrase whose `state_id`
  is no longer current.
- `ctx` passed to `tally.phrase` has `exercise`, `detected`, `child`, `operands`, `answer`.
- Event names with a built-in fallback: `intro`, `exercise_shown`, `wrong_left_finger`, `wrong_right_finger`,
  `no_contact`, `hands_swapped`, `one_hand`, `correct_pose`, `answer_correct`, `answer_wrong`.
- Without `WANDB_API_KEY` the tutor only uses fallbacks. It never blocks the frame loop and never raises.

## 3. Dataset (Axel writes, Ilan reads)

Format: the docstring at the top of `classifier/schema.py` (one JSON object per line: `id`, `hold_id`,
`session`, `angle`, `distance`, `kind`, `label`, `split`, `window`). `hold_id` must be identical for every window
of the same hold: the scorers average windows within a hold, then holds.

- Train (Axel's hands): `data/samples.jsonl`.
- Held-out (other people): `../tenfold-heldout/test.jsonl`, outside the repo, never committed.
- `kind` in `positive`, `near_contact`, `partial_hand`, `out_of_frame`, `transition`, `rest`. Negatives use
  `"label": {"method": "unknown"}`; near-contact keeps the fingers and sets `"contact": false`.

Check a file: `python eval/run_eval.py --split train --local` (train) or `--split heldout --local`.

## 4. Detection ceiling (Axel writes, optional)

`data/detection_ceiling.json`, committed, from the go/no-go measurement:

```json
{"both_hands_contact_frames": 0.93, "lr_swap_rate": 0.01, "tip_jitter_px": 4.2, "measured_at": "2026-09-12T18:00:00Z"}
```

The dashboard draws `both_hands_contact_frames` as the horizontal "detection ceiling" line: no rule edit can
recover frames where MediaPipe lost a hand.

## 5. Dashboard input (Ilan writes, Axel reads)

`data/snapshot.json`, committed, rebuilt with `make snapshot` after the nightly. The notebook needs nothing else.

```json
{
  "generated_at": "2026-09-13T08:30:00+00:00",
  "best_version": "<sha of the version the app runs>",
  "informed": [
    {"tag": "v0", "version": 0, "sha": "...", "ts": "...", "kind": "baseline", "data": {"sha": "3f2a9c1b0d4e", "n_samples": 410},
     "diagnosis": "baseline", "patch": null, "expected": null, "gate": null, "train": METRICS, "heldout": METRICS},
    {"tag": "v0-data1", "version": 0, "kind": "data_refresh", "data": {"sha": "9b1c...", "n_samples": 608},
     "diagnosis": "data refresh: 410 -> 608 samples", "train": METRICS, "heldout": METRICS},
    {"tag": "v1", "version": 1, "sha": "...", "ts": "...", "diagnosis": "DIAGNOSIS: ...", "patch": "...",
     "expected": "...", "gate": "exact_match 0.608 -> 0.750", "train": METRICS, "heldout": METRICS,
     "diff": "unified diff of classifier/rules.py"}
  ],
  "blind": [ {"tag": "v0-blind", "...": "same fields, no diff"} ],
  "rejected": [ {"arm": "informed", "iteration": 3, "ts": "...", "reason": "exact_match fell 0.750 -> 0.700", "patch": "..."} ],
  "refused": [ {"stage": "guard or gate", "ts": "...", "reason": "class 9x9 fell 1.00 -> 0.67 ..."} ],
  "retrospective_validation": {"label": "validation rétrospective, participants non identifiés", "holds": 55, "windows": 125,
     "near_holds": 6, "near_windows": 18, "a": {"commit": "V0", "exact_match": 0.436, "ci95": [], "near_contact_accuracy": 0.44},
     "b": {"commit": "best"}, "paired": {"mean_difference": 0.024, "ci95": [-0.012, 0.073], "better": 2, "worse": 1}},
  "retrospective_versions": [ "the same shape, latest report per compared version, same baseline and validation file" ],
  "explorer": {"data": "train side only", "data_sha": "...", "windows": 483, "constants": {"CONTACT_THRESHOLD": 0.2826},
     "compare": {"a": {"commit": "first rules.py commit", "constants": {}, "exact_match": 0.464}, "b": {"commit": "last rules.py commit"},
                 "windows": 483, "holds": 220, "near_windows": 72, "near_holds": 24},
     "sweep": {"CONTACT_THRESHOLD": {"current": 0.2826, "rows": ["value, metrics, gate PASS or FAIL, why"]}},
     "examples": ["hand landmarks of one practice hold per class, the answer at every contact threshold"]},
  "critic_commits": [ {"sha": "...", "date": "...", "subject": "critic v1: ... | patch: ... | expected: ..."} ],
  "knn_heldout": METRICS,
  "detection_ceiling": {"both_hands_contact_frames": 0.93, "...": "..."}
}
```

`METRICS` = `exact_match`, `exact_match_ci95` ([low, high] or null), `exact_match_unordered`, `contact_accuracy`,
`false_unknown_rate`, `negative_rejection_accuracy`, `near_contact_accuracy`, `n_samples`, `n_holds`,
`per_class` (class name to accuracy; `7x8`, `near:7x8`, `transition`, ...), `per_slice` (capture condition to
`{exact_match, false_unknown_rate, n_samples, n_holds}`; `angle=side`, `distance=far`, ...; absent on versions
evaluated before it existed). Any metric can be null.

Every `informed` row also carries `rules_sha256` (classifier/rules.py at the row's commit) and `scorers`
(`{"sha256", "source"}`: recorded with the metrics, or eval/scorers.py at the row's commit). `explorer.compare` carries
`data_sha`, `scorers_sha256` and `rules_sha256` for `a` and `b`. The dashboard compares two scores only when data and
scorers are both identified and equal, and says "not verified" otherwise.

Chart mapping (SPEC section 10): held-out `exact_match` by version for `informed` (solid green) and `blind`
(solid grey), train dashed, `knn_heldout.exact_match` dotted, `detection_ceiling.both_hands_contact_frames`
horizontal, CI bands from `exact_match_ci95`, commit subject on hover, `diff` under the fold.

## 6. Commands

```
make doctor            # preflight
make eval-local        # score rules.py on data/samples.jsonl, no W&B
make knn               # kNN reference line on the held-out set
make loop N=1          # one guarded critic iteration
make snapshot          # rebuild data/snapshot.json
make rehearse          # full loop on hard synthetic data in a throwaway clone (real claude)
make rehearse-mock     # same, fake claude, no API
```
