# Consolidation audit, after the morning passes

Commit audited: **554dc0d** (`Merge remote-tracking branch 'origin/main'`), which
carries origin/main as of the merge. Earlier reading was done on 331c4ef; every
finding below was re-checked against 554dc0d and anything already fixed was
dropped rather than reported stale.

Method: the whole suite, the mock flow driven end to end with playwright
(chromium 1194) through welcome, the start check and a lesson to the finish
card, the `--demo` replay, and then `app/tutor.py`, `lesson/engine.py`,
`app/server.py` and `web/course/app.js` read straight through as one system.

Classification: **BLOCKER** (the demo breaks or lies to the child),
**BUG** (wrong behaviour, not on the demo path), **CLEANUP** (dead or
contradictory, costs nothing today), **DECISION NEEDED** (two owners disagree in
code and only a person can settle it).

Nothing in `app/`, `lesson/`, `web/course/` or `tests/` was edited. Four other
agents hold those files. Everything found there is listed here with file and
line for its owner.

---

## 1. The suite

`python -m pytest -q`, full run on the pre-merge tree, then the affected files
re-run on 554dc0d.

### Reds

| Test | Failure | Reading |
|---|---|---|
| `tests/test_dashboard.py::test_dashboard_runs_on_every_kind_of_snapshot[committed]` | `ModuleNotFoundError: No module named 'altair'` at `dashboard/loop_dashboard.py:34` | **Neither the test nor the code is wrong. The environment is.** `altair>=5.4` is declared in `requirements.txt:12` (and `pandas>=2.0` on line 13) and is genuinely imported by the notebook. This container simply does not have it installed. `pip install -r requirements.txt` clears all three. Worth saying out loud because SPEC section 15 item 14 is "`make doctor` then `make demo` work on a clean machine": a clean machine that ran the requirements file will not see this. |
| `…[full]` | same | same |
| `…[empty]` | same | same |

Not counted as reds, per `audit/xfail.md`: the 30 stale tests in
`tests/test_live_tutor.py` and the `web/course/app.js` arm of
`tests/test_canonical_lines.py`. They are deliberately xfail and the file
explains each one.

`tests/test_server.py` passes 70/70 in isolation on 554dc0d.

### Not a red, but worth knowing

The full suite takes roughly forty minutes on this machine, almost all of it in
`tests/test_voice.py` (59 tests, each launching a browser). That is fine for a
nightly but it is not a pre-commit gate, and the metric gate of SPEC section 19
(E2/G1) will feel it.

---

## 2. Contradictions, ordered by how much they hurt the demo

### 2.1 BLOCKER. The start check asks for 6 x 6 and the server serves something else

- Page: `web/course/index.html:159` `"That is a 6 and a 6."`, line 160
  `<div class="result" id="result">36</div>`; `web/course/app.js:406` opens the
  node as `{id:"check", kind:"check", pairs:[[6,6]], count:1}`; the scripted
  lines are `"Touch your 6 with your 6."` (app.js:492) and
  `"Thirty six. Exactly."` (app.js:522).
- Server: `app/server.py:1250` `start_session(now, pairs=pairs, length=length)`.
- Scheduler: `lesson/scheduler.py:620-631` `set_scope` stores `self.allowed`
  and its own docstring says **"Neither one steers the draw."** `_choose` and
  `_draw` (`lesson/scheduler.py:710-750`) never consult `self.allowed`. The only
  reader in the whole repo is `app/server.py:947`, inside `_mastered_pick`.

This is amendment F10 ("a course node does not shape what a session serves at
all") landing on top of the start check, which is the one node whose whole
script depends on being served exactly 6 x 6.

Observed, not inferred. Driving the mock flow through the check:

```
t= 0 {"step": 2, "say": "Perfect.", ...}
t= 1 {"step": 2, "say": "Almost. Your left hand is good. Move your right finger from 10 to 9.", ...}
t= 2 {"step": 3, "say": "Yes, that's it.", "frame": "... matched banner"}
t= 4 {"step": 3, "say": "Type the answer.", "dots": "tile-1-off,tile-2-off,tile-3-on"}
=== typing 36 ===
after: {'say': 'Almost. Count the tens again: the touched ones and below.'}
```

The exercise was 9 x 9. The screen said "That is a 6 and a 6", the pre-rendered
result read 36, the child typed 36, and Tally told them they were wrong. On a
second run it was 7 x 7. The very first thing a child does in this app is be
told they are wrong for being right.

F10 is explicit that the node name is a label, so the fix is not to put node
scoping back. Either the check node stops going through the scheduler at all
(it is one fixed exercise, not a lesson), or `set_scope` gets one exception for
`kind == "check"`. **DECISION NEEDED** on which, then a one-line fix.

### 2.2 BLOCKER. The success beat is decided and nothing plays it

`app/tutor.py:1731`-ish `_success_beat` builds the whole beat: `kind`, `parts`
(`line`, `halo`, `stars`, `counter`), `success_ms`, `pause_ms`, `total_ms`,
`line`, `next_line`. `app/server.py:196-199` carries it on the wire as
`tutor_beat`, with a comment saying the page choreographs it.

`web/course/app.js` contains **zero** occurrences of `tutor_beat`
(`grep -c tutor_beat web/course/app.js` → 0). The two parameters that exist only
to size it, `success_beat_ms` (1300) and `next_pause_ms` (1200), appear nowhere
outside `app/tutor.py` and `lesson/tutor_params.json`.

Worse, nothing on the server pauses either. `app/server.py::_check` pushes the
`answer_correct` message, then calls `_record`, then `_advance()`, which pushes
`exercise_shown` in the same lock, microseconds later. The page therefore gets
the celebration and its replacement back to back:

- `frame.classList.add("is-cheer")` at `app.js:1298` is removed by the very next
  message, because `if (!cheer) frame.classList.remove("is-cheer")` runs on the
  `exercise_shown` message.
- the recap band (`renderBand`, `RECAP_MS = 3000`) keys on `m.exercise`, so the
  new exercise wipes it before the 3 s timer it just set ever fires.

Observed in the mock run: `answer_correct` and the finish card landed inside the
same 500 ms sample. In the `--demo` replay the same, at `t=12`.

And the success line is not merely rushed, it is **cut off**. `ACK_STATES` at
`app.js:615` contains both `answer_correct` and `exercise_shown`; `say()` at
`app.js:672` handles an `"ack"` by `speech.queue.unshift(item); cutLine();`.
So the `exercise_shown` line that arrives microseconds after the success line
cuts the success line mid sentence, every single time. "Yes. 8 times 6 is 48."
never finishes.

This is three passes disagreeing: the success beat pass decided a 2.5 s beat,
the speech queue policy pass made every acknowledgement cut its predecessor, and
nobody changed `_check` to stop advancing instantly. **DECISION NEEDED**: the
beat has to be enforced somewhere, and there are only two honest places, the
server holding `_advance` for `total_ms`, or the page holding the render. Doing
it in both is how this happened.

### 2.3 BLOCKER. The ready gate does not exist

`lesson/tally_lines.json:25` carries `"gate_ready": "Say: I'm ready!"`.
`app/tutor.py:214-217` declares it:

```python
# Lines the page says and the tutor never renders: the three steps of the start
# gate. They live in the same file because Tally has one voice, and the server
# hands them to the page rather than the page writing them out again.
PAGE_LINE_KEYS = ("gate_ready",)
```

The server does not hand it to the page. `grep -n "gate" app/server.py` returns
**nothing**. `web/course/app.js` has no gate either: every `gate` in that file is
`gateVoice` / `voice.gate`, which is the microphone answer gate and predates
this. There is no "I'm ready" step at the start of any activity, no second or
third step of a three step gate, and `gate_step_pause_ms` (1000, bounds
[500, 2000], added to `lesson/tutor_params.json:33`) is read by nothing.

So the ready gate pass landed a line, a parameter, a bound and a comment
asserting an integration that was never written. Either it lands or the three
artefacts come out; leaving a parameter nobody reads inside a bounded policy
file is exactly what Loop 2 will later try to tune. **DECISION NEEDED.**

### 2.4 BLOCKER, on `make demo`. The mock loop skips a scripted step every six seconds

`Makefile:18-19`:

```
demo:           ## the fixed demo/scenario.json sequence through the real pipeline (Axel)
	$(PY) app/server.py --mock --demo
```

`app/server.py::mock_loop` sends `lesson.command({"type": "next"})` on every
`phase == 0`, that is every `3 * MOCK_STEP_S` = 6 s, unless the node is the
check. `_skip()` records the exercise as unanswered and advances.

The `--demo` replay reached the finish card, so the scenario does run end to
end, but it served only four of its five steps:

```
exercises served in order: ['8 × 7', '6 × 8', '6 × 6', '8 × 6']
```

Step 4 of `demo/scenario.json` (`{"fact": "7x8", "left": 7, "right": 8,
"reason": "retry"}`) was eaten by the auto-skip: the on-screen counter jumped
from "3 of 5" straight to "5 of 5". That retry step is SPEC section 14's
0:30-1:30 beat, the deliberate wrong finger and its correction, which is the
single most important thirty seconds of the demo.

Any child, or any presenter, who takes longer than six seconds on a question
loses it. This is the exact command SPEC section 15 item 14 names. **The mock
auto-skip must not fire while `--demo` holds a scripted scheduler**, the same
way it already does not fire inside the check (`in_check` at
`app/server.py::mock_loop`).

### 2.5 BUG. Two ladders, two line files, two voices on the same moment

There are two complete intervention ladders running at once on every exercise.

| | Server ladder | Tutor ladder |
|---|---|---|
| Clock | `HINT_AFTER_S = (5.0, 10.0, 20.0)`, `app/server.py:170` | `idle_nudge` 5.0, `hint_2_delay` 9.0, `rescue_delay` 14.0 |
| Level | `hint_level` 0-3, `Lesson.hint_level` | `intervention_level` 0-4 |
| Lines | `hint_1` / `hint_2` / `hint_3` in `lesson/tally_lines.json` | `hesitation_1` / `wrong_left` / `show` / `rescue` |
| Reaches the child as | `reaction` → the `tally` field | `tutor_line` |
| Read by the page? | `hint_level`: **no** (0 refs). `hint_auto`: **no** (0 refs). | `tutor_line`: yes |

`web/course/app.js:1075` is `const tutorLine = m.tutor_line || m.tally;`. The
tutor line wins when it exists, but it exists on only a handful of messages, so
the server ladder's lines reach the child on every message in between. The two
ladders are not coordinated by anything.

The visible result, straight out of the mock run, one second apart:

```
t=7  say: "That is it. Now count the tens, then the ones."      <- tally, correct_pose
t=8  say: "You have the pose. Now count the tens."              <- tutor, pose_ready
```

The child is told to count the tens twice, in two different wordings, inside a
second. (The canonical-lines pass has since reworded `pose_ready`, so the exact
sentences differ on 554dc0d, but the double utterance is structural: both layers
still speak on the confirmed pose.)

`hint_level` and `hint_auto` are still computed, still latched, still put on the
wire, and read by nobody but the server's own `first_try`. **DECISION NEEDED**:
one ladder owns the voice. Given the tutor owns the contract, the server ladder
should stop speaking and keep only what `Outcome.hint_level` needs.

### 2.6 BUG. `tutor_line_cuts` is dead, and a parallel mechanism does its job

`app/tutor.py` computes `tutor_line_cuts` and documents it as "the ninth field…
the only one about delivery rather than pedagogy". `app/server.py:193-195`
carries it. `web/course/app.js` never reads it (0 refs).

What the page reads instead, at `app.js:50-52`:

```js
const INTERRUPT_FIELDS = ["interrupt", "can_interrupt", "interrupts", "interruptible",
  "tally_interrupt", "tally_interrupts", "barge_in"];
function interrupts(m) { return INTERRUPT_FIELDS.some((name) => Boolean(m && m[name])); }
```

Seven guessed spellings, and the one the server actually sends is not among
them. `interrupts(m)` therefore returns false on every message ever sent, in
both `onServerMessage` (`app.js:1078`) and `checkMessage` (`app.js:455`).

Cutting still happens, but through a completely different route: `lineKind(m)`
returns `"ack"` for the four states in `ACK_STATES`, and `say()` cuts on
`item.kind === "ack"` regardless of `opts.interrupt`. So the behaviour is right
by accident, driven by page-side state inference rather than by the tutor's
decision, which is precisely what the field was added to stop.

Two mechanisms, one rule. Either the page reads `tutor_line_cuts` and
`INTERRUPT_FIELDS` goes, or the field comes off the wire. **CLEANUP** in effect,
**DECISION NEEDED** in principle.

### 2.7 BUG. Two thirds of the line-drop reports land as `unknown`

The page reports every dropped line back to the server so the tutor log can
measure lines decided against lines heard. The vocabularies do not match.

`app/tutor.py`:

```python
DROP_REASONS = (DROP_REPLACED, DROP_QUEUE_FULL, DROP_INTERRUPTED, DROP_UNKNOWN)
# "replaced", "queue_full", "interrupted", "unknown"
```

`web/course/app.js` actually sends:

| Sent | Where | Lands as |
|---|---|---|
| `"replaced_by_newer_of_same_kind"` | `app.js:646` | `unknown` |
| `"queue_full"` | `app.js:653` | `queue_full` |
| `"no_longer_true"` | `app.js:701` | `unknown` |

`"replaced"` and `"interrupted"` are never sent by anything. `"interrupted"` in
particular is unreachable: `cutLine()` (`app.js:757`) cuts the line in flight and
calls `closeItem`, but never `reportDrop`, so the one drop reason the interrupt
policy exists to measure is never reported at all.

Confirmed on the wire in the check run:

```
{"type":"line_drop","line":"check_touch","reason":"queue_full","at":1789322569000}
```

The log is the input to Loop 2 (contract section 6). A diagnostic channel whose
vocabulary does not line up is worse than no channel.

### 2.8 BUG. `_motion` returns two different units depending on how many fingertips are visible

`app/tutor.py:1639-1643`:

```python
        moves.sort()
        middle = len(moves) // 2
        if len(moves) % 2:
            return moves[middle]
        return (0.5 * (moves[middle - 1] + moves[middle])) / NOMINAL_PALM
```

The even branch divides by `NOMINAL_PALM` (0.1) to convert frame fractions into
palm widths. The odd branch does not. The module comment twenty lines above is
unambiguous about which is intended:

```
# app/server.py measures motion in palm widths. When it hands over no measure,
# the fallback below works from fingertip coordinates, which are frame fractions,
# so it divides by a nominal palm to land in the same unit.
```

With `MOTION_FLOOR = 0.3` palm widths, an odd fingertip count makes the fallback
report a number ten times too small, so `_update_motion` sees `moving = False`
almost always. Invariant 1, "movement means Tally is silent", silently stops
holding whenever an odd number of fingertips is tracked.

Mitigating: `app/server.py` normally supplies `obs.motion` from `MotionMeter`,
so the fallback only runs when the server hands over nothing. It is still a real
divergence between two branches of one function, and a one-character fix.

### 2.9 BUG. A bad `tutor_params.json` takes the server down, in the one place that decided it must not

`app/server.py::build_tutor` is explicit:

```python
    A params file that will not load is a startup error inside app/tutor.py, and
    it must not be one here: a lesson that refuses to run because of a number in
    a JSON file is worse on stage than a lesson with no tutor in it.
```

and `load_tutor_params` swallows `OSError` and `ValueError` for the same reason.

But `app/server.py:1780`:

```python
    lesson = Lesson(Engine(params=load_tutor_params().get("global", {})), hub, make_scheduler)
```

`Engine.__init__` calls `set_pose_confirm`, which calls `_policy`
(`lesson/engine.py:188-205`), which **raises `ValueError` on any value outside
its bounds, deliberately and by design**. That raise is not caught. A
`pose_confirm_ms` of 900 in the params file therefore kills `create_app` before
the tutor's careful tolerance ever gets a chance to apply.

Two files reached opposite conclusions about the same failure and the stricter
one runs first. **DECISION NEEDED**, though the cheap answer is to wrap that one
construction.

### 2.10 CLEANUP. `pose_confirm_ms` is required by the tutor and used only by the engine

`app/tutor.py:177` lists `pose_confirm_ms` in `MS_PARAMS`, so `load_params`
requires it in both `global` and `bounds` and refuses to start without it. The
tutor's own logic never reads it. (`pose_confirm_frames` **is** now read, at
`app/tutor.py:1731` and `1813`, for the contact ratio check; that half of the
finding is fixed on 554dc0d.)

The only consumer of `pose_confirm_ms` is `lesson/engine.py`, which is amendment
F8's home. The consequence is a real one: F8 says validation has to feel
instant, and the engine honours it by latching after 3 frames or 250 ms. The
tutor's own acknowledgement rides `pose_stable` (0.8 s) instead
(`app/tutor.py:1475`, `1491`), so the two halves of the system confirm the same
pose 550 ms apart, and the child hears yes on the slower of the two clocks.

### 2.11 CLEANUP. Fields on the state message nobody renders

Checked by name against `web/course/app.js` on 554dc0d.

| Field | Refs in the page | Note |
|---|---|---|
| `pose_slip` | 0 | `app/server.py` docstring says it "rides along until the page has stopped reading it". The page has stopped. It can go. The contract (1.4) already says so. |
| `hint_level` | 0 | Still needed by `Outcome.hint_level`, but nothing renders it. |
| `hint_auto` | 0 | Feeds only the server's own `first_try`. Contract 1.4 already says the page must stop reading it, and it has. |
| `scored_gesture_error` | 0 | Latched on the server, on the wire, unread. |
| `scored_math_error` | 0 | Same. |
| `tutor_line_cuts` | 0 | See 2.6. |
| `tutor_beat` | 0 | See 2.2. |

Seven of the message's fields are write-only. `first_try`, `tutor_line`,
`tutor_state`, `tutor_visual`, `intervention_level` and `mode` are genuinely
read.

### 2.12 CLEANUP. Messages the server handles that nothing sends

`app/server.py::command` accepts `start_node`, `hello`, `quit`, `check`, `next`,
`hint`, `tts`, `speech`, `line_drop`, `repeat`.

The page sends `start_node`, `check`, `hint`, `line_drop`, `next`, `quit`,
`speech`, `tts`.

- `hello` is deliberate (a bare client with no course shell) and documented.
- `repeat` has no sender at all. There is no `r` key in the tablet app: the
  keydown handler at `app.js:1779-1794` handles digits, Backspace, Enter, `n`
  and Escape only. `repeat` is a leftover of the `app/ui.py` OpenCV screen of
  SPEC section 9, along with `p` and `d`, which are gone entirely.

### 2.13 CLEANUP. Dead line keys and a dead module constant

- `"unknown_gesture"` (now `lesson/tally_lines.json:31`, formerly
  `lesson/tally.py` PHRASES) has **no producer anywhere**. `moment_of` in
  `app/server.py` maps engine state through `STATE_MOMENT`, which has no such
  key, and no engine event or scheduler reaction produces it.
- The `no_contact` ladder wording never escalates: `_ladder_line`
  (`app/tutor.py`) returns `"not_touching"` for `SIT_NO_CONTACT` **before** the
  level checks, so L1, L2 and L3 all say the same sentence while the level
  counter climbs and the budget is spent. Probably intended; worth a comment if
  so.

### 2.14 CLEANUP. `early_stage == "mastered_fact"` is named for the opposite of what triggers it

`app/tutor.py::_check_early_stop` sets `self._early_stage = "mastered_fact"`
when the last two exercises **each scored two or more errors** and at least one
used the rescue. That is a child who is struggling badly. `new_exercise` then
reads it and comments "One already mastered fact, with all the help it needs."

The behaviour is right (end the session on something the child owns, in
supportive mode). The name says the trigger, and the trigger is failure. Anyone
reading `early_stop` cold will get it backwards.

### 2.15 CLEANUP. `_update_pose` has an unreachable branch

`app/tutor.py`, in `_update_pose`: the `if self._raw_key is None: return` that
follows the `if / elif / else` cannot fire, because every branch above assigns
`self._raw_key`. Left over from an earlier shape.

---

## 3. Docs against code

`docs/tutor_table.md` does not exist. `docs/tutor_contract.md` does, and had
drifted on eleven points. **Fixed in this commit** (docs are mine):

| Section | Was | Now |
|---|---|---|
| header table, 1.2 | "eight new fields" throughout | ten, with `tutor_line_cuts` and `tutor_beat` documented and both marked as not yet read by the page |
| 1.1 | "No new message types beyond the two named below" | three: `tts`, `speech` and `line_drop`, with `line_drop`'s shape and its reason vocabulary, and the mismatch of 2.7 called out |
| 1.2 | example message missing the two new fields | both added |
| 1.4 | `pose_slip` "the page must stop reading it now" | it has; the server still sends it |
| 2.1 | the 21 key file | the 29 key file as it stands on 554dc0d, including `initial_silence` 3.0 |
| 2.2 | 21 rows | rows for the eight added keys, each saying who reads it and whether anything does |
| 2.3 | `motion_threshold = max(0.03, 4 * idle_jitter)` | `max(0.3, 4 * idle_jitter)` in palm widths, with the unit conversion the code's own comment describes |
| 2.4 | supportive "when **either** of the last two exercises ended with a scored error or a rescue" | **both**, which is what `_choose_mode` does (`all(r.hard for r in recent)`) |
| 2.4 | independent when the last three were "all `first_try`" | all `autonomous_success`, which is a different and weaker predicate |
| 4.2 | the rescue's triggers, "two of them can raise it from any level… the child's third help request" | one of them does. `hint_requested` goes through `_gate_level`, so `rescue_delay` gates it like every other route; only two wrong answers bypass the clock, and the code says so itself |
| 5.2 | refers to `_count_pose_slip` and `POSE_GRACE_SECONDS` | `_score_gesture` / `_grace_spent`, and `POSE_GRACE_SECONDS` is now the no-tutor fallback only |
| 3 | `kind` is one of `decision`, `intervention`, `exercise` | four; `line_drop` documented with its fields |

### SPEC.md section 19, against the code

SPEC.md is not mine to edit, so these are reported, not fixed.

- **F8 is half honoured.** `lesson/engine.py` implements it exactly
  (`pose_confirm_frames` or `pose_confirm_ms`, whichever first). `app/tutor.py`
  does not: its acknowledgement waits `pose_stable` = 0.8 s. See 2.10.
- **F10 broke the start check.** The amendment is clear and the code obeys it;
  the casualty is 2.1. F10 deserves a sentence saying the check node is the one
  exception, or the check has to stop going through the scheduler.
- **F10 vs `demo/scenario.json`.** The scenario's `comment` field still explains
  itself in terms of "the one review from outside a node is allowed", which is
  `MAX_OUTSIDE_REVIEWS`, which F10 deleted. The comment is stale. `demo/` is
  Axel's.
- **F9 is accurate** about `app/tutor.py`, `lesson/tutor_params.json`,
  `lesson/tally_lines.json`, `data/tutor_log.jsonl` and `eval/tutor_score.py`.
  It points at `docs/tutor_contract.md` section "Loop 2", which exists.
- **SPEC section 9's key list** (`n`, `r`, `p`, `d`, `q`) describes the
  `app/ui.py` OpenCV screen, which no longer exists. The tablet app has `n`,
  Escape, digits, Backspace and Enter. Not urgent, but section 9 is the UI spec
  and it now describes a program that is not in the repo.

---

## 4. The mock flow and the demo replay

Both were driven with playwright, chromium at
`/opt/pw-browsers/chromium-1194/chrome-linux/chrome`.

**`python app/server.py --mock --no-open --port 8890`** — welcome → check →
lesson → finish card. The lesson path works: five questions, the counter, the
hearts, the finish card, 70 XP. The check is 2.1 above, and additionally its
step-2 dot never lights, because `setStepDots(2)` lives in the `onStart` of the
"Touch your 6 with your 6." line, which the queue drops as `queue_full` before
it is ever spoken. The child goes from step 1 straight to step 3 on screen.

**`python app/server.py --mock --demo --no-open --port 8891`** — the scripted
scenario runs end to end and reaches the finish card with 83 XP. It served four
of its five steps, losing the retry beat, for the reason in 2.4.

No page errors in either run. One `ERR_CONNECTION_RESET` on first load, before
the server finished binding; harmless.

---

## 5. What needs an owner's decision

1. **The start check's exercise** (2.1). Exempt `kind == "check"` from F10, or
   take the check off the scheduler. Blocks the demo. Axel.
2. **Where the success beat is enforced** (2.2). Server-side hold in `_advance`,
   or page-side hold. Not both. Blocks the demo. Axel and whoever holds the page.
3. **The ready gate** (2.3). Land it, or remove `gate_ready`,
   `gate_step_pause_ms` and `PAGE_LINE_KEYS`. Blocks nothing today but a bounded
   parameter nobody reads is a trap for Loop 2.
4. **The mock auto-skip under `--demo`** (2.4). Blocks `make demo`. Axel.
5. **Which ladder speaks** (2.5). The server's `hint_1..3` or the tutor's L1..L4.
   Axel and Ilan; the contract says the tutor.
6. **`tutor_line_cuts`** (2.6). Page reads it, or it comes off the wire.
7. **The line-drop vocabulary** (2.7). One list, in one place. Ilan owns the
   tutor's end.
8. **Params-file strictness** (2.9). The tutor tolerates a bad file, the engine
   refuses to start on one, and the engine runs first.
