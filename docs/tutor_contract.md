# Tutor contract

The shared contract for the three files built next, in parallel, against this page:

| File | Owner of the change | What it does with this contract |
|---|---|---|
| `app/tutor.py` | new file | The tutor state machine. Owns the states, the ladder, the timers, the scoring and the log. Reads `lesson/tutor_params.json`. |
| `app/server.py` | existing | Ticks the tutor from the camera thread, puts its eight new fields on the `state` message, accepts the two new page messages. |
| `web/course/app.js` | existing | Renders `tutor_line` and `tutor_visual`, sends `tts` and `speech`, and stops computing first try for itself. |

Nothing else changes. `lesson/engine.py`, `lesson/scheduler.py`, `lesson/tally.py` and `classifier/` are untouched.
`lesson/tutor.py` (Ilan, W&B Inference) is a different module with a similar name: it phrases a line, `app/tutor.py`
decides whether a line is said at all. They never import each other.

Everything here is V0 policy. Section 6 says why every number lives in a file instead of in the code.

---

## 1. Message contract

One websocket, `/ws`, JSON both ways. No new message types in either direction beyond the two named below.

### 1.1 Page to server

Existing, unchanged:

| Message | Shape | Sent when |
|---|---|---|
| `start_node` | `{type, node: {id, kind, pairs, count}, state}` | The child opens a lesson, a boss or the start check. `state` is the learner record from localStorage. |
| `check` | `{type, value}` | An answer is submitted, typed or spoken. The only message that submits an answer. |
| `next` | `{type}` | The `n` key. Skip this exercise. |
| `quit` | `{type}` | Leaving the lesson, out of hearts, or the Back button. |
| `hello` | `{type, state}` | A client with no course shell. Accepted by the server today, not sent by the current page. |
| `hint` | `{type}` | The child asks for help. Accepted by the server today, not sent by the current page. It is the only child requested help, and the only help that costs first try (section 5). |
| `repeat` | `{type}` | Replay the current exercise. Accepted by the server today, not sent by the current page. |

New:

```json
{"type": "tts", "speaking": true}
{"type": "tts", "speaking": false}
```

Sent around every `speechSynthesis` utterance, from `speakNext` in `web/course/app.js`: `true` in `u.onstart`,
`false` in `u.onend` and in `u.onerror`, and `false` once more from `speak()` and `setMuted(true)` whenever the
queue is cancelled. The server may receive two `false` in a row and must treat that as idempotent. A muted page
still sends the pair, with no gap between them, so the tutor's clocks behave the same muted or not.

```json
{"type": "speech", "text": "is it fifty six"}
```

Anything the child says, whether or not it parses as a number, from `r.onresult` in `web/course/app.js` on every
final result. It is sent in addition to, never instead of, the `check` message the page already sends when a number
parses. `speech` never submits an answer. Its only effects are the two in section 2's invariants: the child speaking
pauses the pedagogical timers, and it proves engagement.

Text is sent verbatim, trimmed, capped at 200 characters. Interim results are not sent. Nothing is stored: the
server keeps only the arrival time and the fact that speech happened.

### 1.2 Server to page

One message type, `state`, exactly as today, with eight new fields. No new message types. `node_end` and
`session_end` are unchanged.

```json
{
  "type": "state",
  "state": "wrong_pose",
  "exercise": "8 x 7",
  "tally": "Almost. Your left hand is good. Move your right finger from 9 to 7.",
  "wrong": [{"hand": "right", "number": 9}],
  "match": [{"hand": "left", "number": 8}],
  "answer": null,
  "reasoning": [],
  "fingers": [
    {"hand": "left", "number": 6, "x": 0.3421, "y": 0.5512},
    {"hand": "right", "number": 9, "x": 0.6688, "y": 0.4907}
  ],
  "reason": "next_new",
  "reaction": null,
  "hint": {"hand": "right", "move_from": 9, "move_to": 7},
  "hint_level": 1,
  "hint_auto": true,
  "pose_slip": false,
  "fact": "7x8",
  "session": 4,
  "node": "u2-l3",
  "demo": false,

  "tutor_state": "WRONG_POSE",
  "intervention_level": 2,
  "tutor_line": "Almost. Your right hand needs 7, not 9.",
  "tutor_visual": {"kind": "correction", "wrong_hand": "right", "expected_finger": 7},
  "scored_gesture_error": false,
  "scored_math_error": false,
  "first_try": true,
  "mode": "normal"
}
```

`fingers` carries all ten fingertips when both hands are seen, in mirrored image coordinates, 0 to 1. The two shown
above are an extract.

The eight new fields:

| Field | Type | Meaning |
|---|---|---|
| `tutor_state` | string | One of the nine states of section 4. The page uses it for nothing but diagnostics in V0; `state` still drives the rendering. |
| `intervention_level` | integer 0 to 4 | The ladder of section 4, currently in force on this exercise. Not the same scale as `hint_level`. |
| `tutor_line` | string or null | The one sentence Tally should say now. Null means say nothing new. |
| `tutor_visual` | object or null | What to draw. Shapes in 1.3. Null means draw nothing beyond the finger circles the page already draws. |
| `scored_gesture_error` | boolean | True once a gesture error has been scored on this exercise. Section 5. |
| `scored_math_error` | boolean | True once a math error has been scored on this exercise. Section 5. |
| `first_try` | boolean | True while this exercise is still eligible for the first try bonus. Computed server side. Section 5. |
| `mode` | string | `supportive`, `normal` or `independent`. Section 2.4. |

Absent means false or null: a page that receives a message without one of these fields treats a boolean as false,
`tutor_line` and `tutor_visual` as null, `intervention_level` as 0, `tutor_state` as `"WORKING"` and `mode` as
`"normal"`. The server always sends all eight; the rule exists so a page can talk to an older server without a
special case.

### 1.3 tutor_visual

`null`, or an object whose `kind` is one of six values. `kind` is always present. No other kinds exist.

```json
{"kind": "pulse_finger", "hand": "right", "finger": 7}
{"kind": "correction", "wrong_hand": "right", "expected_finger": 7}
{"kind": "ghost", "hand": "right", "from": 9, "to": 7}
{"kind": "rescue_card", "tens": 5, "units": 6, "total": 56}
{"kind": "placement_zones"}
{"kind": "finger_numbers"}
```

- `hand` and `wrong_hand` are `"left"` or `"right"`, the child's own hands, which is also screen left and screen
  right in the mirrored image. `finger`, `expected_finger`, `from` and `to` are finger numbers 6 to 10.
- `ghost` carries `hand` as well as `from` and `to`. The page keys every fingertip by `hand:number`
  (`drawFingers`), so a ghost without a hand cannot be placed. `hand` is the hand the finger has to move on, the
  same value as `hint.hand`.
- `rescue_card.tens` is the count of tens, not the tens value: for 8 x 7 it is `5`, and the card reads 5 tens, so
  50. `units` is 6, `total` is 56. These are `Exercise.tens`, `Exercise.units` and `Exercise.result` from
  `lesson/engine.py`, unchanged.
- `placement_zones` and `finger_numbers` carry no payload. `placement_zones` is the two dashed rectangles of
  SPEC.md section 9. `finger_numbers` is the 6 to 10 labels lighting up on both hands, the same treatment the start
  check already plays in `checkMessage` step 1.

### 1.4 What happens to the existing fields

| Field | Verdict | Detail |
|---|---|---|
| `hint` | Stays, unchanged | The engine's geometric hint, `{hand, move_from, move_to}`. It is the input `app/tutor.py` builds `correction` and `ghost` from. Still sent on every message. |
| `hint_level` | Stays on the wire, changes role | Still 0 to 3, still the scheduler's `hint_1`, `hint_2`, `hint_3` and still what `Outcome.hint_level` records. It is no longer a rendering input: the page must stop testing `m.hint_level >= 2` in `drawFingers` and draw the ghost from `tutor_visual.kind === "ghost"` instead. Do not confuse it with `intervention_level`. |
| `hint_auto` | Stays on the wire, superseded for the page | Still says whether the clock or the child raised `hint_level`. It becomes an input to the server side `first_try` computation only. The page must stop reading it. |
| `pose_slip` | Superseded by `scored_gesture_error` | Same clock, wider meaning. The server keeps sending it for the length of this build so nothing breaks mid flight, and it is removed once the three files have landed. The page must stop reading it now. |
| `tally` | Stays | Still the phrase from `lesson/tally.py` for the current moment. It is the fallback line, and the only line for the start check. |
| everything else | Stays, unchanged | `state`, `exercise`, `wrong`, `match`, `answer`, `reasoning`, `fingers`, `reason`, `reaction`, `fact`, `session`, `node`, `demo`. |

### 1.5 First try moves to the server

`web/course/app.js` deletes `lesson.slipped` and the line that computes it:

```js
if (m.pose_slip === true || (m.hint_level > 0 && m.hint_auto !== true)) lesson.slipped = true;
```

and the two assignments to `lesson.slipped` in `onServerMessage` and `restartCounters`. The first try counter
becomes one line:

```js
if (fresh && m.state === "answer_correct") { lesson.correct += 1; if (m.first_try) lesson.firstTry += 1; }
```

The server has the clock, the params and the ladder; the page has none of them. `first_try` in the message is the
value as of that message, and the value the page must trust is the one riding the `answer_correct` message.

### 1.6 Speaking, once

The page says exactly one line per message: `tutor_line` when it is not null, otherwise `tally`, and only when
that text differs from the last line spoken. `.tally-say` renders `tutor_line || tally`. The start check
(`checkMessage`) keeps its own scripted lines and ignores `tutor_line` entirely.

---

## 2. Params file schema: `lesson/tutor_params.json`

Three top level blocks, in this order: `global`, `bounds`, `invariants`.

- `global` holds every number the tutor uses. Nothing is hardcoded in `app/tutor.py`: no timing, no count, no
  factor. A value the code needs and the file does not have is a startup error.
- `bounds` holds `[min, max]` for every key in `global`. Every value is clamped to its bounds on load, and a value
  outside its bounds is a startup error, not a silent clamp. The clamp exists for the per learner factors of 2.4,
  which multiply these values at runtime.
- `invariants` is a list of strings. It is documentation. No code ever reads it. It is in the file so that the
  agent that proposes a change in Loop 2 reads the rules in the same breath as the numbers.

The file is loaded once at startup, into a frozen dataclass. There is no reload.

### 2.1 The file

```json
{
  "global": {
    "fps": 15,
    "hands_visible_stable": 0.5,
    "pose_stable": 0.8,
    "initial_silence": 4.0,
    "idle_nudge": 5.0,
    "wrong_pose_prompt": 2.0,
    "wrong_pose_error_after_help": 2.5,
    "correct_pose_nudge": 4.5,
    "hint_2_delay": 9.0,
    "rescue_delay": 14.0,
    "no_hands_visual": 1.5,
    "no_hands_voice": 3.0,
    "one_hand_voice": 2.5,
    "min_verbal_gap": 4.0,
    "post_movement_silence": 2.0,
    "no_engagement_pause": 30.0,
    "pose_memory": 2.0,
    "max_unsolicited_verbal": 3,
    "max_visibility_reminders": 2,
    "supportive_mode_factor": 0.8,
    "independent_mode_factor": 1.4
  },
  "bounds": {
    "fps": [15, 15],
    "hands_visible_stable": [0.3, 1.0],
    "pose_stable": [0.5, 1.5],
    "initial_silence": [2.5, 7.0],
    "idle_nudge": [3.0, 10.0],
    "wrong_pose_prompt": [1.2, 4.0],
    "wrong_pose_error_after_help": [1.5, 5.0],
    "correct_pose_nudge": [3.0, 10.0],
    "hint_2_delay": [6.0, 15.0],
    "rescue_delay": [10.0, 25.0],
    "no_hands_visual": [1.0, 3.0],
    "no_hands_voice": [2.0, 6.0],
    "one_hand_voice": [1.5, 5.0],
    "min_verbal_gap": [3.0, 8.0],
    "post_movement_silence": [1.0, 4.0],
    "no_engagement_pause": [20.0, 60.0],
    "pose_memory": [1.0, 4.0],
    "max_unsolicited_verbal": [2, 4],
    "max_visibility_reminders": [1, 3],
    "supportive_mode_factor": [0.65, 1.0],
    "independent_mode_factor": [1.1, 2.0]
  },
  "invariants": [
    "Movement means Tally is silent.",
    "Unknown or low confidence vision is never an error.",
    "A visibility failure is never a pedagogical error.",
    "The child speaking pauses the pedagogical timers.",
    "No parameter can leave its bounds.",
    "Tally never says wrong for a correct answer given before the pose."
  ]
}
```

### 2.2 What each value means

All durations are seconds. Counts are integers. Factors are unitless multipliers.

| Key | V0 | Meaning |
|---|---|---|
| `fps` | 15 | The tutor's own tick rate. Fixed: `[15, 15]`. The camera thread delivers frames faster than this, so the tutor drops anything arriving less than `1 / fps` after the last observation it took. It is the same 15 Hz the overlay already refreshes at (`FINGER_REFRESH_S` in `app/server.py`). |
| `hands_visible_stable` | 0.5 | Both hands have to be present this long before the tutor believes they are there. At 15 fps that is 8 frames, and the rule is the same shape as `pose_stable`: at least 7 of the last 8 frames carry both hands. |
| `pose_stable` | 0.8 | A pose counts as held when it has been identical this long. At 15 fps that is 12 frames, and stable means at least 10 of those 12 frames carry the identical `(left, right, contact)` triple. Below that the tutor is in movement and says nothing. |
| `initial_silence` | 4.0 | Silence after the prompt before any nudge, measured from the end of the prompt utterance, which is the `tts {speaking: false}` the page sends, not from the moment the message was built. With no `tts` message at all (a browser with no speech synthesis) the clock starts on the `exercise_shown` message. |
| `idle_nudge` | 5.0 | Nothing has moved and nothing has been said for this long: one L1 nudge. |
| `wrong_pose_prompt` | 2.0 | A wrong pose has to be held this long before Tally says anything about it. Below it, the child is still arranging their hands. |
| `wrong_pose_error_after_help` | 2.5 | After a targeted correction has been given, a pose still wrong this long is a scored gesture error. Section 5. |
| `correct_pose_nudge` | 4.5 | The pose is right and no answer has come for this long: one nudge toward counting. |
| `hint_2_delay` | 9.0 | Time on the exercise clock before the ladder may reach L3 (show). |
| `rescue_delay` | 14.0 | Time on the exercise clock before the ladder may reach L4 (rescue). |
| `no_hands_visual` | 1.5 | No hands seen this long: the visual reminder (`placement_zones`), silent. |
| `no_hands_voice` | 3.0 | No hands seen this long: one spoken visibility reminder. |
| `one_hand_voice` | 2.5 | One hand only, this long: one spoken reminder about the other hand. |
| `min_verbal_gap` | 4.0 | Minimum silence between two spoken lines, whatever asked for them. A line that would break this gap is dropped, not queued. |
| `post_movement_silence` | 2.0 | After the hands stop moving, wait this long before speaking. Movement means Tally is silent. |
| `no_engagement_pause` | 30.0 | No movement, no speech, no command for this long: the tutor enters PAUSED and stops all clocks. |
| `pose_memory` | 2.0 | How long a pose that has gone (a hand lost, a hand out of frame) is still treated as the current pose, so a MediaPipe dropout does not reset the exercise. |
| `max_unsolicited_verbal` | 3 | At most this many spoken interventions per exercise that the child did not ask for. A line that follows a `hint` message from the page does not count against it. |
| `max_visibility_reminders` | 2 | At most this many visibility reminders (no hands, one hand) per exercise, spoken or visual. |
| `supportive_mode_factor` | 0.8 | The mode multiplier in supportive mode: every duration is shorter, so help comes sooner. |
| `independent_mode_factor` | 1.4 | The mode multiplier in independent mode: every duration is longer, so the child gets more room. |

### 2.3 motion_threshold is not in the file

`motion_threshold` is the only quantity the tutor uses that is not a parameter, because it is a property of this
camera in this room, not a policy. It is computed once per server run:

```
motion_threshold = max(0.03, 4 * idle_jitter)
```

`idle_jitter` is measured during the camera check, which is the `check` node the page opens on the way into the
practice path (`startCheck` in `web/course/app.js`, `{"id": "check", "kind": "check", "pairs": [[6, 6]], "count": 1}`).
Step 1 of that check is exactly a still hands measurement: the child holds both hands up, palms forward, while the
numbers light up ten down to six, and nothing is being asked of them.

The measurement, in `app/tutor.py`:

1. Take the first 2.0 s of consecutive observations during the check node in which both hands are present, which is
   30 observations at 15 fps.
2. For each consecutive pair of observations, take the median over the ten fingertips of the euclidean distance
   between their two positions, in the mirrored image coordinates of the `fingers` array (`x` and `y` both 0 to 1,
   no aspect correction).
3. `idle_jitter` is the 90th percentile of those per frame medians.

If the check is skipped (`sessionStorage` already holds `tenfold.checked`, or the child goes straight into a
lesson), or fewer than 20 usable pairs are collected, `idle_jitter` is treated as 0 and `motion_threshold` is its
floor, 0.03. The value in force is written into `effective_params` on every `intervention` log line, so Loop 2 can
see which threshold produced which behaviour.

### 2.4 Per learner factors

Three values live on the learner record, under a `tutor` key:

```json
{"learner_id": "9f31c0a7bd42", "...": "the rest of the learner record",
 "tutor": {"pace_factor": 1.0, "help_factor": 1.0, "tutor_observations": 0}}
```

| Field | Default | Meaning |
|---|---|---|
| `pace_factor` | 1.0 | Multiplies every duration in `global`. Above 1 the child gets more time. |
| `help_factor` | 1.0 | Multiplies the two count parameters, `max_unsolicited_verbal` and `max_visibility_reminders`. The product is rounded to the nearest integer and clamped to the bounds of that key. It never touches a duration. |
| `tutor_observations` | 0 | How many `exercise` log lines this learner has produced. It gates the adaptation schedule below. |

The record round trips through the page's localStorage, like everything else the scheduler remembers.
`LearnerState.to_dict` and `from_dict` in `lesson/scheduler.py` do not know about the `tutor` key and drop unknown
keys, so `lesson/scheduler.py` is not the carrier and is not edited: `app/server.py` reads `state["tutor"]` off the
`hello` or `start_node` payload, hands it to the tutor, and writes the tutor's current factors back into the
`state` object of the `node_end` and `session_end` messages after `scheduler.state.to_dict()` has produced it. The
page already saves that object whole, so nothing changes in `web/course/app.js` for this.

**Effective timing**

```
effective(key) = clamp(global[key] * pace_factor * mode_factor, bounds[key][0], bounds[key][1])
```

`mode_factor` is `supportive_mode_factor` in supportive mode, `1.0` in normal mode, `independent_mode_factor` in
independent mode. The clamp is the last operation, so no product can ever leave the bounds. `fps` is never scaled.
Counts use `help_factor` instead, by the rule in the table above.

**Mode**

`mode` is chosen by `app/tutor.py` alone, once per exercise, at the moment the exercise is loaded. `app/server.py`
and `web/course/app.js` only carry and render it. V0 rule:

- `supportive` when either of the last two exercises in this node ended with a scored error or a rescue.
- `independent` when the last three exercises in this node were all `first_try`.
- `normal` otherwise, and always on the first exercise of a node.

**Adaptation schedule**

The factors are proposed by Loop 2, never by the live app; the live app only enforces the schedule when it loads
them.

| `tutor_observations` | Allowed |
|---|---|
| 0 to 4 | Globals only. Both factors are forced to 1.0 whatever the record says. |
| 5 to 14 | At most 15 percent deviation: both factors are clamped to `[0.85, 1.15]`. |
| 15 and more | The full range, subject only to the effective value staying within its bounds. |

**Smoothing**

A new factor is never applied outright:

```
new = 0.8 * old + 0.2 * proposed
```

applied before the schedule clamp above.

### 2.5 The invariants, in words

They are in the file as strings and repeated here because they decide arguments the numbers cannot.

1. **Movement means Tally is silent.** While the fingertips are moving more than `motion_threshold` between
   observations, no line is spoken and no timer for a spoken intervention advances. A line becomes possible again
   `post_movement_silence` after the movement stops.
2. **Unknown or low confidence vision is never an error.** `method == "unknown"` produces no scored error, ever, and
   no wrong pose. It can produce a visibility reminder and nothing else.
3. **A visibility failure is never a pedagogical error.** No hands, one hand, a hand lost by MediaPipe, hands out of
   frame: none of these can score, none of these cost first try, and none of these count against
   `max_unsolicited_verbal`. They count against `max_visibility_reminders` only.
4. **The child speaking pauses the pedagogical timers.** A `speech` message freezes every intervention clock for
   `post_movement_silence`, and resets the no engagement clock. A child who is thinking out loud is working.
5. **No parameter can leave its bounds.** Enforced on load for the file, and on every multiplication for the
   effective values.
6. **Tally never says wrong for a correct answer given before the pose.** A correct answer that arrives before the
   correct pose is accepted as correct. It is not a math error, it does not cost first try, and the pose is then
   confirmed rather than demanded.

---

## 3. Log schema: `data/tutor_log.jsonl`

One JSON object per line, appended, never rewritten. UTF-8, no trailing spaces. The file is committed: `.gitignore`
covers `data/raw/` and `data/metrics*.json` only, and this log is the point of section 6. It is written by
`app/tutor.py`, opened in append mode and flushed per line, and a write failure is swallowed: no log line may ever
take the lesson down.

Every line has `ts`, `kind`, `learner_id` and `exercise`.

- `ts`: string, ISO 8601 UTC with a `Z`, from `lesson/scheduler.now_utc`.
- `kind`: string, one of `decision`, `intervention`, `exercise`. It is the discriminator, present on every line.
- `learner_id`: string, `LearnerState.learner_id`, the 12 hex character id. Never a name.
- `exercise`: string, `Exercise.title`, for example `"8 x 7"`. Ordered as it was asked, not as it was posed.

### 3.1 decision

Written when the tutor's state changes, when `intervention_level` changes, and when an error is scored. Never on a
plain frame tick, and never more than twice in one second.

| Key | Type | Meaning |
|---|---|---|
| `ts` | string | As above. |
| `learner_id` | string | As above. |
| `exercise` | string | As above. |
| `state` | string | The tutor state entered or held, from section 4. |
| `stable_for` | float | Seconds the current observation has been stable, 2 decimals. 0.0 while moving. |
| `intervention` | integer 0 to 4 | The ladder level in force after this decision. |
| `reason` | string | Why, as one machine token. V0 vocabulary: `prompt_end`, `within_grace`, `wrong_pose_held`, `idle`, `pose_ready`, `answer_given`, `answer_wrong`, `hands_lost`, `one_hand`, `child_moving`, `child_speaking`, `min_verbal_gap`, `budget_spent`, `child_asked`, `no_engagement`. |
| `scored_error` | string or null | `"gesture"`, `"math"`, or null when this decision scored nothing. |
| `mode` | string | `supportive`, `normal` or `independent`. |
| `params_version` | string | 12 hex characters. Section 3.4. |

```json
{"kind": "decision", "ts": "2026-09-13T18:04:11.420Z", "learner_id": "9f31c0a7bd42", "exercise": "8 x 7", "state": "WRONG_POSE", "stable_for": 2.13, "intervention": 2, "reason": "wrong_pose_held", "scored_error": null, "mode": "normal", "params_version": "4c1d9ab0f772"}
```

### 3.2 intervention

One line per intervention actually delivered, spoken or visual. Four of its keys are only knowable afterwards, so
the line is written 5.0 s after the intervention, or at the end of the exercise, whichever comes first. The `ts` is
the moment of the intervention, not the moment of the write.

| Key | Type | Meaning |
|---|---|---|
| `ts` | string | When the intervention was delivered. |
| `learner_id` | string | As above. |
| `exercise` | string | As above. |
| `intervention` | integer 1 to 4 | The level delivered. L0 is not an intervention and is never logged here. |
| `trigger_after_s` | float | Seconds from the start of the exercise to the intervention, 2 decimals. |
| `state_before` | string | The tutor state the intervention was delivered from. |
| `child_was_already_moving` | boolean | True when the fingertips were moving more than `motion_threshold` in the 0.5 s before delivery. An intervention on a child who was already fixing it is unnecessary by definition. |
| `child_moved_after_s` | float or null | Seconds from delivery to the first movement over `motion_threshold`, or null if nothing moved within 5.0 s. |
| `correct_pose_within_5s` | boolean | The correct pose was reached within 5.0 s of delivery. |
| `next_hint_needed` | boolean | A higher level had to be delivered on this exercise after this one. |
| `effective_params` | object | The values actually in force, after `pace_factor` and the mode factor and the clamp, plus `motion_threshold`. Only the keys that took part in this decision, plus `motion_threshold` always. Floats, 3 decimals. |
| `learner_factors` | object | `{pace_factor, help_factor, tutor_observations, mode_factor}` as they stood. |

```json
{"kind": "intervention", "ts": "2026-09-13T18:04:11.420Z", "learner_id": "9f31c0a7bd42", "exercise": "8 x 7", "intervention": 2, "trigger_after_s": 6.84, "state_before": "WRONG_POSE", "child_was_already_moving": false, "child_moved_after_s": 1.32, "correct_pose_within_5s": true, "next_hint_needed": false, "effective_params": {"wrong_pose_prompt": 2.0, "wrong_pose_error_after_help": 2.5, "min_verbal_gap": 4.0, "post_movement_silence": 2.0, "motion_threshold": 0.042}, "learner_factors": {"pace_factor": 1.0, "help_factor": 1.0, "tutor_observations": 23, "mode_factor": 1.0}}
```

### 3.3 exercise

One line per exercise, written when the exercise ends: answered correctly, skipped, or abandoned by a `quit` or a
dropped socket. `tutor_observations` is incremented by exactly one per line written.

| Key | Type | Meaning |
|---|---|---|
| `ts` | string | When the exercise ended. |
| `learner_id` | string | As above. |
| `exercise` | string | As above. |
| `autonomous_success` | boolean | Answered correctly with no intervention above L1 and no rescue. |
| `first_try` | boolean | The formula of section 5, as it stood at the end. |
| `light_hint_recovery` | boolean | The correct pose was reached after an L1 or L2 intervention, without ever needing L3 or L4. False when no intervention was delivered. |
| `rescue_used` | boolean | L4 was delivered. |
| `scored_gesture_errors` | integer | Count, 0 or more. Section 5. |
| `scored_math_errors` | integer | Count, 0 or more. Section 5. |
| `unsolicited_interventions` | integer | Interventions the child did not ask for, L1 to L4, visibility reminders excluded. |
| `abandoned` | boolean | The exercise ended without an answer and without a skip. |
| `duration_s` | float | Seconds from the exercise being loaded to it ending, 2 decimals. |
| `mode` | string | The mode this exercise ran in. |

```json
{"kind": "exercise", "ts": "2026-09-13T18:04:19.007Z", "learner_id": "9f31c0a7bd42", "exercise": "8 x 7", "autonomous_success": false, "first_try": false, "light_hint_recovery": true, "rescue_used": false, "scored_gesture_errors": 0, "scored_math_errors": 0, "unsolicited_interventions": 2, "abandoned": false, "duration_s": 12.91, "mode": "normal"}
```

Note the shape of that example: the child needed a correction, recovered from it, and answered correctly. Nothing
was scored as an error, and the exercise still lost first try, because two automatic interventions were delivered.
The two facts do not contradict each other, and section 5 says why.

### 3.4 params_version

`params_version` identifies the parameter set that produced a decision, so a Loop 2 comparison can never mix two
policies in one cohort.

```
params_version = sha256(json.dumps({"global": ..., "bounds": ...}, sort_keys=True, separators=(",", ":")))[:12]
```

- Computed once at load, from the file as read, before any clamping.
- The `invariants` block is excluded: it is prose, and editing it must not split a cohort.
- Per learner factors are excluded: they vary per child and are logged separately as `learner_factors`.
- It changes when, and only when, any value in `global` or `bounds` changes. Adding a key changes it. Reordering
  keys or reformatting the file does not.

It is on the `decision` line only. The `intervention` line carries the effective values themselves, which is
strictly more information, and the `exercise` line is joined to its decisions by `learner_id` and `ts`.

---

## 4. States and ladder

The shared vocabulary for `tutor_state` and `intervention_level`. These are the exact strings and integers on the
wire.

### 4.1 States

| State | Meaning | What the page renders |
|---|---|---|
| `PROMPTING` | The exercise has just been shown and the prompt is being said. Timers start at `tts {speaking: false}`. | The exercise title, Tally talking, no zones, no numbers. |
| `WORKING` | Hands are up and the child is arranging them. Nothing is wrong yet, or a wrong pose has been held for less than `wrong_pose_prompt`. | Finger circles only. `tutor_visual` is usually null. |
| `WRONG_POSE` | A wrong pose has been held past `wrong_pose_prompt`. | `pulse_finger`, then `correction`, then `ghost`, as the ladder climbs. |
| `POSE_READY` | The correct pose has been held for `pose_stable`. The engine has latched. | Green fingers, the reasoning band, Tally counting. |
| `CHECK_ANSWER` | Waiting for the answer, by voice or keyboard. | The answer field, the mic pill, the reasoning band. |
| `ANSWER_RETRY` | The answer was wrong and the child is trying again. | The shake, the recount line, the reasoning band kept. |
| `VISIBILITY_RECOVERY` | No hands, or one hand, past `no_hands_visual` or `one_hand_voice`. Not a pedagogical state. | `placement_zones`, the camera veil, no judgement anywhere. |
| `SUCCESS` | The answer was right. | The result, the flash, the star or XP animation. |
| `PAUSED` | No movement, no speech, no command for `no_engagement_pause`. Every clock is stopped. | Nothing new. The screen holds. Any movement, speech or key leaves the state. |

### 4.2 The ladder

`intervention_level`, integer, on the wire. L0 to L4 are the human names.

| Level | Name | Meaning | What the page renders |
|---|---|---|---|
| 0 | L0 observe | Say nothing, draw nothing. The default, and where the tutor spends most of its time. | `tutor_visual` null, `tutor_line` null. Finger circles only. |
| 1 | L1 nudge | One short line pointing at the hands, no specifics. Costs one `max_unsolicited_verbal` when the clock raised it. | `pulse_finger` or `finger_numbers`, and one line. |
| 2 | L2 targeted correction | Name the hand and the finger it needs. | `correction`, and one line naming the hand and the number. |
| 3 | L3 show | Show where the finger goes. Never before `hint_2_delay` on the exercise clock. | `ghost` at the target fingertip, and one line. |
| 4 | L4 rescue | Walk the whole thing through. Sets `rescue_used`, and ends first try. | `rescue_card` with the tens, the units and the total. |

The level never goes down inside one exercise, and it resets to 0 on every new exercise.

It normally climbs one step at a time, but the rescue has three triggers of its own and two of them can
raise it from any level: two wrong numeric answers on the same exercise, and the child's third help request.
The third trigger, a correct pose held with no answer after earlier help, is the only one gated by
`rescue_delay`. The rescue is delivered at most once per exercise, it obeys `min_verbal_gap` like every other
line, and it sits outside `max_unsolicited_verbal`, which is the budget of L1, L2 and L3 alone.

---

## 5. Scoring

### 5.1 Never an error

None of these can set `scored_gesture_error` or `scored_math_error`, and none of them costs first try:

- A wrong pose while the hands are being raised, that is, before `pose_stable` has ever been satisfied on this
  exercise.
- A wrong pose held for less than `wrong_pose_prompt`.
- A hand lost by MediaPipe, within `pose_memory`.
- Hands out of frame, at any length.
- Speech that does not parse, or parses to nothing.
- A correct answer given before the correct pose. Invariant 6: it is accepted, and Tally asks for the pose after
  praising the answer, never before.
- A commutative inversion. 7 on the left and 6 on the right for 6 x 7 is a correct pose, not a wrong one.
- `hands_swapped` and `no_contact` from `lesson/engine.py`. Both mean the two numbers are right and the pose is
  still being assembled. Only a condition in which a finger number is actually wrong can ever score.

### 5.2 scored_gesture_error

Increments once, at most once per exercise, when all of these hold:

1. The pose is wrong in the sense of a wrong finger number, not `no_contact` and not `hands_swapped`.
2. A targeted correction (L2 or above) naming that hand has already been delivered.
3. The pose has stayed wrong for `wrong_pose_error_after_help` since that correction.
4. The vision is not `unknown` and both hands are present.

This is the same clock the server calls `pose_slip` today (`_count_pose_slip`, `POSE_GRACE_SECONDS`), with the
grace now coming from the params file and now starting at the correction rather than at the first sight of the
wrong pose. It feeds `Outcome.pose_error` in exactly the place `pose_error` is set today.

### 5.3 scored_math_error

Increments once per wrong answer submitted through a `check` message, with no cap per exercise. A `speech` message
never increments it. It feeds `Outcome.math_error`, unchanged.

### 5.4 first_try

```
first_try = correct_pose
        AND correct_answer
        AND scored_gesture_errors == 0
        AND scored_math_errors == 0
        AND requested_hints == 0
        AND rescue_used == False
```

`requested_hints` counts only the `hint` messages the page sent, that is, help the child asked for. Automatic
nudges and automatic hints never cost first try, however many of them were delivered: `first_try` and
`unsolicited_interventions > 0` coexist happily, and that pair is precisely what Loop 2 exists to reduce.

`first_try` rides every `state` message as a live boolean. The value that counts is the one on the
`answer_correct` message, and the page reads it there and nowhere else.

### 5.5 Commutative inversion

7 on the left and 6 on the right for 6 x 7 is accepted everywhere, with no penalty and no comment:

- `lesson/engine.py`: `_condition` compares `frozenset((left, right))` to `Exercise.expected`, so the order never
  matters, and `_assign` picks the kinder reading of which finger is aiming at which target.
- `lesson/scheduler.py`: `fact_key` sorts its factors, and `Scheduler.record` keys the pose record on
  `pose_key(outcome.left, outcome.right)`, which is the pose that was asked for, never the one that was detected.
  An inversion therefore cannot land on a different record.
- `app/tutor.py`: an inversion is never a wrong pose, never an intervention trigger, never a scored error.
- `web/course/app.js`: it renders what the server sends, and the server sends `correct_pose`.
- The star score: `app/server.py` counts `self.correct` on `answer_correct`, which an inversion reaches normally.

The one thing an inversion does change is the wording: while the fingers are not yet touching, the engine emits
`hands_swapped` and Tally says swap your hands, because at that moment it is better advice than naming two fingers.
That line is advice, not a judgement, and it scores nothing.

---

## 6. Loop 2

For Ilan. Nothing in this section is built today. What is built today is the log.

Every timing in section 2 is a V0 policy parameter, not behaviour. That is the whole point of the file. Nobody
believes 2.0 s is the right time to wait before naming a wrong finger; we believe it is a defensible starting
point, and we believe the way to find out is the same way we find out about `classifier/rules.py`.

Two loops, the same shape, one file each:

| Loop | Improves | Scored by | Gate |
|---|---|---|---|
| Loop 1, perception | `classifier/rules.py` | `eval/scorers.py` on `data/samples.jsonl` | `exact_match` on the train split, held out blind |
| Loop 2, tutoring | `lesson/tutor_params.json` | `eval/tutor_score.py` on offline replay | TutorScore does not fall on replay, then a new cohort |

Loop 2 reads `data/tutor_log.jsonl`, proposes one change inside the bounds, and uses `eval/tutor_score.py` on an
offline replay of the log as its safety gate. The gate is a filter, not a proof: replay can only tell you that a
proposal does not obviously make things worse on sessions that already happened. Only a new cohort comparison,
children playing under the new parameters against children playing under the old ones, counts as evidence that
the change helped. `params_version` on every `decision` line is what makes that comparison possible.

### TutorScore

```
TutorScore = 4.0 * autonomous_success_rate
           + 2.0 * first_try_rate
           + 2.0 * light_hint_recovery_rate
           - 2.0 * unnecessary_intervention_rate
           - 2.0 * rescue_rate
           - 1.0 * abandonment_rate
```

The three rates over exercises come straight off the `exercise` lines: `autonomous_success`, `first_try`,
`light_hint_recovery`, `rescue_used` and `abandoned`, each divided by the number of exercises in the cohort.

`unnecessary_intervention_rate` comes off the `intervention` lines. An intervention is unnecessary when

```
child_was_already_moving == true   OR   child_moved_after_s < 0.5
```

that is, the child was already fixing it, or was about to. The rate is unnecessary interventions divided by all
interventions. Interrupting a child who is already solving it is the single worst thing a tutor can do, which is
why it carries the same weight as a rescue.

The log exists from today so that the data is there before Loop 2 is built. Every hour the app runs with children
in front of it is an hour of the only dataset that can settle these numbers.
