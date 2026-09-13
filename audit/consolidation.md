# Consolidation audit, after the morning passes

Commit audited: **dd3ae9e**, which carries origin/main as of the last merge
before this was written.

main moved five times while this was being read (331c4ef, 554dc0d, e15ab97,
c9e8224, dd3ae9e), so every finding was re-checked at dd3ae9e by grep before
being kept. Four were fixed under me and are marked as closed rather than
deleted, because what changed and why is half of what a consolidation audit is
for; two are new and exist only because of those fixes. Anything fixed and not
interesting was simply dropped.

That also means this is a photograph, not a guarantee. Every claim below names
the file and the line it was checked at, so the next reader can re-run the same
grep rather than trust the date.

Method: the whole suite; the mock flow driven end to end with playwright
(chromium 1194) through welcome, the start gate and a lesson to the finish card;
the `--demo` replay; and `app/tutor.py`, `lesson/engine.py`, `app/server.py` and
`web/course/app.js` read straight through as one system.

Classification: **BLOCKER** (the demo breaks or lies to the child),
**BUG** (wrong behaviour, not on the demo path), **CLEANUP** (dead or
contradictory, costs nothing today), **DECISION NEEDED** (two owners disagree in
code and only a person can settle it).

Nothing in `app/`, `lesson/`, `web/course/` or `tests/` was edited. Four other
agents hold those files. Everything found there is listed with file and line for
its owner. `docs/tutor_contract.md` is fixed in place, section 3.

---


## 1. The suite

`python -m pytest -q`, run four times as main moved, the last of them at dd3ae9e
with nothing else on the machine.

### Reds

**The only reds I ever reproduced were a missing dependency, and they are gone.**

`tests/test_dashboard.py::test_dashboard_runs_on_every_kind_of_snapshot` failed
all four cases (`committed`, `full`, `partial`, `empty`) with
`ModuleNotFoundError: No module named 'altair'`, raised inside the notebook
subprocess at `dashboard/loop_dashboard.py:37`.

Neither the test nor the code was wrong. `altair>=5.4` is declared in
`requirements.txt:12`, with `pandas>=2.0` on line 13, and the notebook genuinely
imports it at four `mo.ui.altair_chart` call sites; this container had simply
never installed it. It has since been installed (6.2.2) and the four pass. Worth
recording rather than deleting, because SPEC section 15 item 14 is "`make doctor`
then `make demo` on a clean machine": a clean machine that ran the requirements
file never saw this, so it was never evidence against item 14.

`tests/test_server.py` passes 70/70 in isolation.

Not counted as reds, per `audit/xfail.md`: the stale tests in
`tests/test_live_tutor.py` and the `web/course/app.js` arm of
`tests/test_canonical_lines.py`. They are deliberately xfail, the marking is not
strict, and that file explains each one. They show as `x`. There were 30 and a
31st was added at dd3ae9e.

### One red I could not attribute, and will not pretend I could

An earlier full run reported 54 failed, 50 of them `tests/test_voice.py` with
`Page.goto: net::ERR_CONNECTION_REFUSED`, the module's own mock server having
gone away. I could not reproduce that cleanly, and I am fairly sure it was mine:
I had two playwright driver sessions, two mock servers and a second pytest run
competing for the machine at the time. An idle mock server polled every 20 s for
two minutes stayed up and answered 200 every time, so there is no server that
dies on its own.

The final clean run at dd3ae9e was still in `tests/test_voice.py` when this was
written, with **one** failure in the preceding 92 percent and no dashboard
failures at all. I am reporting that honestly rather than rounding it to green:
the suite is not fully verified at dd3ae9e by me, and the one outstanding F is
unidentified. Anyone re-running it should give it a clear machine and about
fifteen minutes.

### Not a red, but worth knowing

A full run does not reliably finish inside fifteen minutes; the first attempt was
killed at 900 s having reached about 95 percent, and the fastest clean run was
475 s. Almost all of it is `tests/test_voice.py` (59 tests, each launching a
browser). That is fine for a nightly, but it is not a pre-commit gate, and the
metric gate of SPEC section 19 (E2/G1) will feel it. `make test` should probably
grow a fast default with the browser tests behind a marker.


## 2. Contradictions, ordered by how much they hurt the demo

### 2.1 BLOCKER. The start gate tells the child to touch 6 with 6 and the server is on another fact

**Rewritten on c9e8224.** On 331c4ef this was worse: the start check asked for
6 x 6, pre-rendered a 36, and marked a correct 36 wrong. The gate pass has since
removed the arithmetic step entirely, so nothing tells a child they are wrong for
being right any more. What is left is the same root cause with a different
symptom.

- Page: `web/course/app.js:498-500` still opens the node with
  `pairs: [[6, 6]]` whenever the pose step is in the gate.
- Lines: `lesson/tally_lines.json:71` `gate_pose` is "Touch your 6 with your 6."
  and line 73 `gate_banner` is "That is a 6 and a 6."
- Server: `app/server.py` passes those pairs to `start_session`.
- Scheduler: `set_scope` (`lesson/scheduler.py:620-631`) records `self.allowed`,
  its docstring says **"Neither one steers the draw"**, and `_choose` never reads
  it. The only reader in the repo is `app/server.py:947`, inside `_mastered_pick`.

The gate's pose step passes on `posed(m)` (`app.js:544`), which is
`m.state === "correct_pose"`, and that is the engine's verdict on **whatever
exercise the scheduler happened to draw**.

Driven on the mock server at c9e8224:

```
t= 0 {"step": 2, "say": "Perfect.", "banner": "That is a 6 and a 6.", ...}
t= 1 {"step": 3, "say": "Yes, that's it.", ...}
t= 3 {"step": 3, "say": "Say: I'm ready!", ...}
--- exercise the server thinks it is on ---
  state=correct_pose exercise=8 x 10
```

The screen said "That is a 6 and a 6" about an 8 and a 10.

The mock passes because its hands always make the exercise the engine is on. A
real child does what the line says and holds 6 and 6, the engine is comparing
against 8 x 10, `correct_pose` never arrives, and **the gate never passes**. The
first screen of the app becomes a dead end on the one path where nothing else
can go wrong.

F10 is explicit that a node name is a label, so the fix is not to restore node
scoping. Either the gate's pose step stops going through the scheduler (it is a
camera check, not a question), or `set_scope` gets one exception for
`kind == "check"`. **DECISION NEEDED** on which, then a small fix.


### 2.2 BLOCKER. The success beat is decided, carried, and still never played

`app/tutor.py::_success_beat` builds the whole beat: `kind`, `parts`
(`line`, `halo`, `stars`, `counter`), `success_ms`, `pause_ms`, `total_ms`,
`line`, `next_line`. `app/server.py` carries it as `tutor_beat`, with a comment
saying the page choreographs it.

On c9e8224 the page finally mentions the field, once, at `app.js:1274`:

```js
if (m.tutor_beat || (fresh && m.state === "exercise_shown")) clearHeard();
```

That is the beat used as a bare signal to clear the answer pill. **Nothing plays
it.** `success_beat_ms` and `next_pause_ms` still have zero occurrences in
`web/course/app.js`, so the two numbers whose only purpose is to size the
celebration size nothing, and `parts` is never read at all.

Nothing on the server pauses either. `app/server.py::_check` pushes the
`answer_correct` message, then calls `_record`, then `_advance()`, which pushes
`exercise_shown` inside the same lock, microseconds later. The page gets the
celebration and its replacement back to back:

- `frame.classList.add("is-cheer")` is undone by the next message, because
  `if (!cheer) frame.classList.remove("is-cheer")` runs on `exercise_shown`.
- the recap band (`renderBand`, `RECAP_MS = 3000`) keys on `m.exercise`, so the
  new exercise wipes it before the 3 s timer it just set can fire.

Observed every run: `answer_correct` and the next exercise, or the finish card,
land inside the same 500 ms sample.

And the success line is not merely rushed, it is **cut off**. `ACK_STATES`
contains both `answer_correct` and `exercise_shown`, and `say()` handles an
`"ack"` with `speech.queue.unshift(item); cutLine();`. The `exercise_shown` line
that arrives microseconds after the success line cuts the success line mid
sentence, every time. In the run on e15ab97 "Yes. 6 times 8 is 48." never
reached the bubble at all.

Three passes disagreeing: the beat pass decided a 2.5 s beat, the speech queue
pass made every acknowledgement cut its predecessor, and nobody changed `_check`
to stop advancing instantly. **DECISION NEEDED**: there are two honest places to
enforce the beat, the server holding `_advance` for `total_ms`, or the page
holding the render. Doing it in neither is where we are; doing it in both is how
this happened.

### 2.3 CLEANUP, was a BLOCKER. The ready gate is built; two of its parameters are orphans

**Fixed while this audit was being written.** On 554dc0d the gate was a line, a
parameter, a bound, a comment and two tests, and no code. On c9e8224
`web/course/app.js` has it: `gateSteps()`, `STEP_FRAME`, `openStep`, `turnStep`,
the three steps `hands`, `pose`, `ready`, the pose step skipped for a child who
has passed it before, and a Ready button for a browser with no microphone.

Driven at c9e8224 it walks all three steps and reaches "Say: I'm ready!", and
the step dots advance with it, which also clears the step-2 dot bug reported
from the earlier run. What is left of the finding is small and worth fixing
before it rots:

- **`gate_step_pause_ms`** was read by no production code on c9e8224. **Fixed at
  dd3ae9e**: `web/course/app.js:460` now reads it as `GATE_PAUSE_KEY`. Closed.
- **`gate_ready_button_s`** still stands. `web/course/app.js` reads it through
  `timing(READY_BUTTON_KEY, READY_BUTTON_S)` and its comment says
  "lesson/tutor_params.json owns the value". `grep -c gate_ready_button_s
  lesson/tutor_params.json` is 0, so the page silently falls back to its own 6
  seconds, and the one number deciding how long a child with a dead microphone
  stares at a screen with no way forward is not in the policy file, not bounded,
  and not tunable by Loop 2.

The fallback is graceful, which is why nothing failed, and which is why this
will sit here.

The two tests named for the gate (`tests/test_opening.py:125-136`) still assert
only that a constant and a string exist; they passed before the gate was built
and they pass now. See 4b.


### 2.4 BLOCKER, on `make demo`. The mock loop skips a scripted step every six seconds

`Makefile:18-19`:

```
demo:           ## the fixed demo/scenario.json sequence through the real pipeline (Axel)
	$(PY) app/server.py --mock --demo
```

`app/server.py::mock_loop` sends `lesson.command({"type": "next"})` on every
`phase == 0`, that is every `3 * MOCK_STEP_S` = 6 s, unless the node is the
check. `_skip()` records the exercise as unanswered and advances.

The `--demo` replay reached the finish card both times it was driven, so the
scenario does run end to end. It served four of its five steps both times, and
**not the same four**:

```
run 1, on the pre-merge tree:  ['8 × 7', '6 × 8', '6 × 6', '8 × 6']   step 4 lost
run 2, on e15ab97:             ['6 × 8', '6 × 6', '7 × 8', '8 × 6']   step 1 lost
```

`demo/scenario.json` has five steps for this node. Run 1 lost step 4
(`{"fact": "7x8", "left": 7, "right": 8, "reason": "retry"}`), the counter
jumping from "3 of 5" to "5 of 5". Run 2 lost step 1 (`8 x 7`) before the page
had even attached, opening on "2 of 5".

Which step is eaten depends on where the wall clock happens to sit in the
six second cycle when the node opens, so **the scripted demo is not
reproducible**, which is the entire reason `demo/scenario.json` exists. Step 4
is SPEC section 14's 0:30 to 1:30 beat, the deliberate wrong finger and its
correction, the single most important thirty seconds of the three minutes.

Any child, or any presenter, who takes longer than six seconds on a question
loses that question. This is the exact command SPEC section 15 item 14 names.
**The mock auto-skip must not fire while `--demo` holds a scripted scheduler**,
the same way it already does not fire inside the check (`in_check` at
`app/server.py::mock_loop`). It is one condition on one line.

### 2.4b BUG. The page sends `ready` and the server has no branch for it

Found on c9e8224, after the gate landed. `web/course/app.js:675`:

```js
    sendLesson({ type: "ready", node: "check" });
```

`app/server.py::command` handles `start_node`, `hello`, `quit`, `check`, `next`,
`hint`, `tts`, `speech`, `line_drop` and `repeat`. There is no `ready` branch and
no `else`, so the message passes the ownership check and then falls out of the
`if/elif` chain in silence. Not even a log line.

This is the brief's "a message the page sends that the server ignores", and it
is the direct cause of 2.4c.

### 2.4c BUG. The camera check measures a threshold that is thrown away, twice over

`docs/tutor_contract.md` 2.3: "It is computed once per server run." It is not.

**First**, `end_check()` is never called on the gate path. `app/server.py:1257`
calls `self.tutor.check_started()` when the node kind is `check`, which turns on
the jitter collection. The only call to `check_ended()` is at `app/server.py:965`,
inside `_end_session`. `_end_session` fires when the node's outcome count reaches
its target, and **the gate records no outcome at all now that it asks no
question**. The child leaves it through `quit`, and `_quit` (line 1330) calls
`self.tutor.close("quit", ...)` and never `check_ended()`.

So `_jitter_on` stays true for the rest of the run and `motion_threshold` is
never set from what was measured. It stays at `MOTION_FLOOR`.

**Second**, and this one would bite even if the first were fixed:
`TutorLink.open` (`app/server.py:519-527`) calls `build_tutor` unconditionally,
and `_start_node` calls `open` for **every node**. Each node therefore gets a
brand new `Tutor`, whose `motion_threshold` starts at `MOTION_FLOOR` again. A
threshold measured during the gate could not reach the lesson that follows it
even if `end_check()` ran, because the object holding it is discarded when the
lesson node starts.

The consequence is quiet and total: the whole camera check feature, measure the
jitter of this camera in this room and scale "the child is moving" to it, has no
effect on any lesson. Invariant 1 runs on the hard floor for every child in every
room. Nothing fails, nothing logs, and `effective_params.motion_threshold` on
every `intervention` line reads 0.3 forever, which is exactly the number Loop 2
would use to conclude the threshold does not matter.

### 2.5 BUG. Two ladders on one voice, now sharing a line file

Two complete intervention ladders run at once on every exercise.

| | Server ladder | Tutor ladder |
|---|---|---|
| Clock | `HINT_AFTER_S = (5.0, 10.0, 20.0)`, `app/server.py:176` | `idle_nudge` 5.0, `hint_2_delay` 9.0, `rescue_delay` 14.0 |
| Level | `hint_level` 0-3 | `intervention_level` 0-4 |
| Reaches the child as | `reaction` to `hint_event`, then the `tally` field | `tutor_line` |
| Read by the page? | `hint_level` 0 refs, `hint_auto` 0 refs | `tutor_line`, yes |

`web/course/app.js:1291` is `const tutorLine = m.tutor_line || m.tally;`. The
tutor line wins where it exists, and it exists on only a handful of messages, so
the server ladder's lines reach the child on every message in between. Nothing
coordinates the two clocks.

On 331c4ef this was audible. One second apart in the mock run:

```
t=7  say: "That is it. Now count the tens, then the ones."      <- tally, correct_pose
t=8  say: "You have the pose. Now count the tens."              <- tutor, pose_ready
```

**The canonical-lines pass fixed this properly, and it deserves saying.** It did
not copy strings between two files. `lesson/tally.py` now keeps no text at all:
it is a `KEYS` map from the moment to a key in the single
`lesson/tally_lines.json`, and the overlapping moments point straight at the
tutor's own canonical line:

```python
    "correct_pose": "pose_ready",
    "hint_2": "show",
    "recount_tens": "count_tens",
    "no_contact": "not_touching",
```

The comment above it is exactly right: "the pose being right is `pose_ready` and
nothing else, which is how the second wording of that moment stopped existing."
Confirmed in the re-run: the doubled line is gone, and where both ladders land on
the same key the page's own `tutorLine !== lesson.said` swallows the repeat.

**What is left is the structure, and at dd3ae9e it got worse rather than better.**
The visual ladder pass made the tutor deliberately silent on its first two rungs.
`_ladder_line` at `app/tutor.py:1649`:

```python
        if level < 3 and not asked:
            # L1 and L2 are shown, not said.
            return None
```

Its docstring states the policy plainly: "Tally shows before he speaks. L1 puts
the numbers on the fingertips and L2 colours the two fingers that matter, both
without a word."

**The server ladder talks straight through that silence.** It is on its own
clock, it knows nothing about the tutor's level, and it produces a spoken
`reaction` at 5, 10 and 20 seconds regardless:

| Moment | Server ladder says | Tutor ladder does |
|---|---|---|
| 5 s | `hint_1`, "Look at your hands. One finger needs to move." | L1: shows the numbers, says nothing |
| 10 s | `hint_2`, mapped to `show`, "Move this finger here." | L2: colours the two fingers, says nothing |
| 14 s | nothing | L3: the first rung with a voice |
| 20 s | `hint_3`, "Watch me do it, then copy me." | L4 available from `rescue_delay` |

Because `tutor_line` is null while the tutor is silent, `m.tutor_line || m.tally`
falls through to the tally line every time, so what the child actually hears on
the first two rungs is the server's ladder. The pass that decided Tally should
show before he speaks is defeated by a ladder in another file that nobody
switched off.

The dedupe that saved the doubled counting line does not help here: these are
different keys at different moments, so there is nothing to deduplicate.

`hint_level` and `hint_auto` are still computed, latched and sent, and read by
nobody but the server's own `first_try`. **DECISION NEEDED**: one ladder owns the
voice. The contract says the tutor, so the server ladder should stop producing
`reaction` lines and keep only what `Outcome.hint_level` records.


### 2.5b CLEANUP. A seventh `tutor_visual` kind the page has never heard of

Landed at dd3ae9e with the pose reading table. `app/tutor.py` now defines seven
visual kinds where the contract fixes six:

```python
VIS_PULSE_FINGER, VIS_CORRECTION, VIS_GHOST, VIS_RESCUE_CARD,
VIS_PLACEMENT_ZONES, VIS_FINGER_NUMBERS, VIS_CLOSING_IN
```

`grep -c closing_in web/course/app.js` is 0. `drawFingers` branches on
`pulse_finger`, `correction`, `ghost`, `placement_zones` and `finger_numbers`,
and `renderBand` on `rescue_card`. The seventh falls through all of them.

**To be fair, this one was designed to.** The comment in `app/tutor.py` says so
in advance:

```
# A page that does not know the kind still draws the two expected tips green,
# because any non null tutor_visual lights the tips the engine already marks.
```

Checked, and it is true: `marked(tag)` in `drawFingers` includes
`visual !== null && match.has(tag)`, so a `closing_in` visual does light the two
tips. That is a genuinely careful piece of forward compatibility and it is the
right way to add a kind across an ownership line.

What is still worth saying: the payload `closing_in` carries, the two fingertips
the child is bringing together, is discarded, so the green dot on each that the
table describes is not what the child sees; they get the ordinary match marking
they would have got anyway. And `docs/tutor_contract.md` 1.3 said "one of six
values. No other kinds exist", which is now false. Fixed there.

### 2.6 BUG. `tutor_line_cuts` is dead, and a parallel mechanism does its job

`app/tutor.py` computes `tutor_line_cuts` and documents it as "the ninth field…
the only one about delivery rather than pedagogy". `app/server.py:193-195`
carries it. `web/course/app.js` never reads it (0 refs).

What the page reads instead, at `app.js:50-52` (unchanged through dd3ae9e):

```js
const INTERRUPT_FIELDS = ["interrupt", "can_interrupt", "interrupts", "interruptible",
  "tally_interrupt", "tally_interrupts", "barge_in"];
function interrupts(m) { return INTERRUPT_FIELDS.some((name) => Boolean(m && m[name])); }
```

Seven guessed spellings, and the one the server actually sends is not among
them. `interrupts(m)` therefore returns false on every message ever sent, in
both `onServerMessage` (`app.js:1314`) and `checkMessage` (`app.js:560`). The
comment above it is honest about what happened: "The mark is being added on the
server side and may not be on the message at all: every spelling it could arrive
under is read." The page was written against a field that did not exist yet, by
guessing, and the guess missed.

**A green test covers this gap too**, the same shape as 2.3.
`tests/test_voice.py:895` `test_the_line_marked_by_the_server_cuts_the_one_in_flight`
is named for the server's mark and never involves the server:

```python
    Tenfold.say('Yes, that is it.', null, null, { interrupt: true });
```

It calls the page's own `say()` with the option set by hand. The option works.
The link from `tutor_line_cuts` on the wire to that option is what is broken,
and nothing tests it.

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
| `"replaced_by_newer_of_same_kind"` | `app.js:819`, `enqueue` | `unknown` |
| `"queue_full"` | `app.js:826`, `enqueue` | `queue_full` |
| `"no_longer_true"` | `app.js:874`, `pump` | `unknown` |

`"replaced"` and `"interrupted"` are never sent by anything. `"interrupted"` in
particular is unreachable: `cutLine()` cuts the line in flight and
calls `closeItem`, but never `reportDrop`, so the one drop reason the interrupt
policy exists to measure is never reported at all.

Confirmed on the wire in the check run:

```
{"type":"line_drop","line":"check_touch","reason":"queue_full","at":1789322569000}
```

The log is the input to Loop 2 (contract section 6). A diagnostic channel whose
vocabulary does not line up is worse than no channel.

### 2.8 BUG. `_motion` returns two different units depending on how many fingertips are visible

`app/tutor.py:2026-2030` at dd3ae9e:

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

But `app/server.py:1783`:

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

### 2.10 BUG. The engine and the tutor read amendment F8 in opposite directions

F8 is explicit: "The correct pose is confirmed in `pose_confirm_frames` frames or
`pose_confirm_ms`, **whichever comes first**... Validation has to feel instant:
the child is already right while the old clock was still counting."

`lesson/engine.py:291-293` implements exactly that:

```python
        if condition == COND_CORRECT:
            return (self._pending_frames >= self.pose_confirm_frames
                    or held >= self.pose_confirm_ms / 1000.0)
```

`app/tutor.py:2200-2202` does not:

```python
        frames = int(self.effective("pose_confirm_frames"))
        if (moment - self._raw_since >= self.effective("pose_stable")
                and self._raw_frames >= frames):
```

Three differences in three lines. The tutor uses **and** where F8 says **or**;
it measures against **`pose_stable`** (0.8 s) where F8 names `pose_confirm_ms`
(250 ms); and `pose_confirm_ms` is therefore read by nobody but the engine, while
`app/tutor.py:216` still lists it in `MS_PARAMS` and refuses to start without it
in `global` and `bounds`.

The comment in `app/tutor.py` is careful about why frames and time are both
required, "a camera that drops frames cannot have a pose judged on two of them",
and that is a good reason. It is also a different rule from the one written into
SPEC section 19, decided in a different file, with no note that it disagrees.

The effect the child feels: the engine goes green at about 250 ms, the tutor
says yes at 800 ms. The morning's acknowledgement pass narrowed the gap at the
other end, splitting `pose_ack` (immediate, may cut) from the canonical cue
(`pose_ready_delay_ms` later), so the yes is prompt **once the tutor has decided**.
It still decides on the slower clock, and F8 exists precisely to say that clock
is too slow.

**DECISION NEEDED**, and it is a one line decision: either the tutor adopts F8's
`or`, or F8 is amended to say the tutor's confirmation is a different, slower
thing from the engine's latch and `pose_confirm_ms` belongs to the engine alone.


### 2.11 CLEANUP. Fields on the state message nobody renders

Counted by name against `web/course/app.js` at dd3ae9e.

| Field | Refs in the page | Note |
|---|---|---|
| `pose_slip` | 0 | `app/server.py` says it "rides along until the page has stopped reading it". The page has stopped. It can go, and `docs/tutor_contract.md` 1.4 now says so. |
| `hint_level` | 0 | Still needed by `Outcome.hint_level`; nothing renders it. |
| `hint_auto` | 0 | Feeds only the server's own `first_try`, as the contract asked. |
| `scored_gesture_error` | 0 | Latched on the server, on the wire, unread. |
| `scored_math_error` | 0 | Same. |
| `tutor_line_cuts` | 0 | See 2.6. |
| `tutor_beat` | 1 | Read as a bare truthy signal to clear the answer pill. Its contents are never used. See 2.2. |

Seven of the message's fields are effectively write-only. `first_try`,
`tutor_line`, `tutor_state`, `tutor_visual`, `intervention_level`, `mode`,
`reasoning`, `fingers`, `state`, `exercise`, `tally`, `reaction`, `fact`,
`node` and `demo` are genuinely read.

### 2.12 CLEANUP. Messages nothing sends, and one nothing receives

`app/server.py::command` accepts `start_node`, `hello`, `quit`, `check`, `next`,
`hint`, `tts`, `speech`, `line_drop`, `repeat`.

The page sends `start_node`, `check`, `hint`, `line_drop`, `next`, `quit`,
`speech`, `tts`, **`ready`**.

- `ready` is sent and not handled: 2.4b.
- `hello` is deliberate, a bare client with no course shell, and documented.
- `repeat` has no sender. `grep -c '"repeat"' web/course/app.js` is 0 and there
  is no `r` key: the keydown handler takes digits, Backspace, Enter, `n` and
  Escape. `repeat` is a leftover of the `app/ui.py` OpenCV screen of SPEC
  section 9, along with `p` and `d`, which are gone entirely.


### 2.13 CLEANUP. Dead line keys and a dead module constant

- `"unknown_gesture"` (now `lesson/tally_lines.json:31`, formerly
  `lesson/tally.py` PHRASES) has **no producer anywhere**. `moment_of` in
  `app/server.py` maps engine state through `STATE_MOMENT`, which has no such
  key, and no engine event or scheduler reaction produces it.
- (The `no_contact` wording repeating at L1, L2 and L3, reported from the
  earlier tree, is **fixed**: `_ladder_line` now returns None below L3, so
  `not_touching` is said once and only at L3.)

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
drifted on eighteen points. **All eighteen fixed** (`docs/` is
mine; nothing in `app/`, `lesson/`, `web/course/` or `tests/` was touched).

| Section | Was | Now |
|---|---|---|
| header table, 1.1, 1.2 | "eight new fields", "the two new page messages" | ten and three |
| 1.1 | "No new message types beyond the two named below" | three. `line_drop` documented: its shape, where it is sent from, what it is for, and a block quote naming the vocabulary mismatch of 2.7 |
| 1.2 | example message and field table stop at `mode` | `tutor_line_cuts` and `tutor_beat` added to both, with the beat's own shape, and a block quote saying neither is read by the page today and why the beat never plays |
| 1.4 | `pose_slip` "the page must stop reading it now" | it has, and the server still sends it, so it can go. Plus a new paragraph listing all seven write-only fields, measured rather than assumed |
| 2.1 | the 21 key file | the 29 key file as it stands on 554dc0d |
| 2.2 | 21 rows | `initial_silence` 3.0 noted, then a second table for the eight added keys, each naming who actually reads it, and a paragraph on `gate_step_pause_ms`, which nothing reads |
| 2.3 | `motion_threshold = max(0.03, 4 * idle_jitter)` | `max(0.3, ...)`, in palm widths, with the unit conversion the code's own comment describes |
| 2.3 | the check node's `pairs` taken at face value | a block quote saying F10 ignores it, with what the check actually served when driven |
| 2.3 | step 2 of the jitter measurement | plus the odd/even unit split in `_motion` (2.8) |
| 2.4 | supportive "when **either** of the last two exercises ended with a scored error or a rescue" | **both**, which is what `_choose_mode` does (`all(r.hard for r in recent)`) |
| 2.4 | independent when the last three were "all `first_try`" | all `autonomous_success`, which is a different and weaker predicate, with the difference spelled out |
| 3 | `kind` is one of `decision`, `intervention`, `exercise` | four, with `line_drop`'s fields |
| 4.1 | `POSE_READY` "has been held for `pose_stable`. The engine has latched." | the two clocks separated, with the 550 ms gap and what it costs F8 |
| 4.2 | "the rescue has three triggers and **two** can raise it from any level… the child's third help request" | exactly one can. `hint_requested` passes its target through `_gate_level` like every other route, so `rescue_delay` gates it too; only two wrong answers bypass the clock, and `_answer_rescue`'s own docstring says so |
| 5.2 | refers to `_count_pose_slip`, which no longer exists | `_score_gesture` / `_grace_spent`, with `POSE_GRACE_SECONDS` now the no-tutor fallback only |
| 1.1 | `repeat` "not sent by the current page" | and never will be: there is no `r` key. Plus a new row for `ready`, which the page sends and the server drops |
| 1.2 | the note said the page reads neither new field | `tutor_beat` is now read, as a bare signal, and still never played; the note says which half landed |
| 2.2 | `gate_step_pause_ms` "belongs to the start gate, which is not built" | the gate is built; the paragraph now names both orphan parameters, the one the file has and the page ignores and the one the page reads and the file has not got |
| 2.3 | the check node described as a 6 x 6 question | the three step gate as it is, with the `pairs` still ignored and what the banner said about an 8 and a 10 |
| 2.3 | "computed once per server run" | a second block quote: `end_check()` never runs on the gate path, and the tutor is rebuilt per node, so the measured threshold reaches nothing |

### SPEC.md section 19, against the code

SPEC.md is not mine to edit, so these are reported, not fixed.

- **F8 is implemented twice, once per file, with different logic.**
  `lesson/engine.py` has the amendment as written, `or`. `app/tutor.py` has
  `pose_stable` **and** `pose_confirm_frames`, which is a different rule and a
  slower one. Full detail in 2.10; this is the one where SPEC and the code
  disagree outright rather than drifting.
- **F10 broke the start check, and the gate inherited the break.** The amendment
  is clear and the code obeys it; the casualty is 2.1. F10 needs one sentence
  saying the check node is the exception, or the gate has to stop going through
  the scheduler.
- **The visual ladder is in no amendment either, and it contradicts one.** L1 and
  L2 are now silent by design. Nothing in section 19 records that decision, and
  SPEC section 9's own table still describes `wrong_pose` as "doigt fautif en
  orange, fleche courbe... bandeau orange" with a spoken fallback phrase. The
  code is probably right and the spec is certainly behind, which is the state
  that produced 2.5.
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
  and it now describes a program that is not in the repo. `p`, the recorded
  landmark replay that section 9 calls "the safety net of the demo", is gone
  with it, and SPEC section 14 still leans on a safety net existing.
- **The start gate is in no amendment.** F7 bis brought in the scheduler and the
  course nodes; nothing in section 19 mentions a three step gate in front of
  every activity, its own line keys, or the two parameters of 2.3. It is the one
  morning pass with no written decision behind it, and it is now the first screen
  a child meets.

---

## 4. The mock flow and the demo replay

Driven with playwright, chromium at
`/opt/pw-browsers/chromium-1194/chrome-linux/chrome`. Each flow was run on more
than one tree as main moved; what follows is c9e8224 unless it says otherwise.
The drivers live in the scratchpad and are not committed.

**`python app/server.py --mock --no-open --port <p>`**, welcome, the name, the
start gate, a lesson, the finish card.

- Welcome, the name and the child roster work. The record is stored per child and
  the view routes.
- The gate walks all three steps, `hands`, `pose`, `ready`, the step dots follow,
  and it reaches "Say: I'm ready!". The step-2 dot bug seen on the earlier tree
  is gone.
- The gate's banner reads "That is a 6 and a 6" while the server is on 8 x 10:
  2.1. The mock passes it only because the mock's hands always make whatever the
  engine is on.
- The lesson runs: five questions, the counter, the hearts, the finish card,
  70 XP.
- The counter skipped a question on one run ("1 of 5" straight to "3 of 5"), the
  mock auto-skip of 2.4.
- On the run at e15ab97 the success line never reached the bubble at all: the
  `exercise_shown` line cut it. 2.2.

**`python app/server.py --mock --demo --no-open --port <p>`**: the scripted
scenario runs end to end and reaches the finish card both times, 83 XP on the
first tree and 62 on e15ab97. Four of the five steps both times, a different four
each time. 2.4.

**A false alarm, recorded so nobody re-finds it.** A mock server of mine died
mid session and the symptom, `ERR_CONNECTION_REFUSED`, matched the one that made
50 `tests/test_voice.py` tests fail in an earlier run. It was neither: the server
was a background job of a shell that exited, and the 50 failures were my own
playwright sessions and pytest runs competing for the machine. An idle mock
server polled every 20 s for two minutes stayed up and answered 200 every time.
There is no server that dies on its own.


## 4b. The pattern under most of this

Three of the four blockers have the same shape, and it is worth naming because
it is what parallel passes do to a codebase rather than what any one author did
wrong.

A pass lands its own end of a feature, with tests, and the tests pass. The other
end is somebody else's file, so it is described in a comment instead of written.
Nobody re-reads the pair, because each half has a green test.

| Feature | The half that landed, tested | The half that did not | The test that passes anyway |
|---|---|---|---|
| the success beat | `app/tutor.py` decides it (`tests/test_live_tutor.py:1560-1658`), `app/server.py` sends it (`tests/test_server.py:56`) | `web/course/app.js` plays it | both halves of the pipe are asserted; nothing asserts a beat is ever played |
| the line that may cut | `app/tutor.py` sets `tutor_line_cuts`, the page's `say(opts.interrupt)` honours it | the wire between them | `tests/test_voice.py:895`, which sets the option by hand |
| the ready gate | the line and the parameter exist and are in bounds | everything else, until it landed at c9e8224 | `tests/test_opening.py:125-136`, which asserts a constant and a string, and passed equally before and after the gate existed |
| the `ready` message | the page sends it | `app/server.py::command` has no branch | nothing tests the pair, so nothing noticed |

The test suite is 753 green and cannot see any of the three, because in each case
the two ends were tested separately and the join was not tested at all. A field
on the state message is not covered by a test that it is sent; it is covered by a
test that something reads it.

The cheapest guard, and it would have caught all four: one test that asserts
every key in `TUTOR_FIELDS` appears somewhere in `web/course/app.js`. That is
crude, and it is exactly the check nobody was in a position to write, because it
spans two owners' files.

**And the counter example, which is the pattern done right.** The `closing_in`
visual of 2.5b crosses the same ownership line and does not break anything,
because its author wrote down what happens on a page that has never heard of it
and then made sure that fallback was real. The difference is not care taken over
one's own half. It is one sentence about the other half, written as a claim
somebody can check, rather than as an intention:

```
# A page that does not know the kind still draws the two expected tips green,
# because any non null tutor_visual lights the tips the engine already marks.
```

Every one of the four failures above has a comment in the same position saying
what the other side will do. The difference is that those four say what the other
side *should* do.

---

## 5. What needs an owner's decision

Ordered by cost if nobody touches it.

1. **The gate's pose step and F10** (2.1). Exempt `kind == "check"` from the
   uniform draw, or take the gate off the scheduler. Today the gate says "Touch
   your 6 with your 6" while the engine is on another fact, so a child who obeys
   cannot pass it. Axel.
2. **Where the success beat is enforced** (2.2). The server holding `_advance`
   for `total_ms`, or the page holding the render. One of them, not both, not
   neither. Axel, with whoever holds the page.
3. **The mock auto-skip under `--demo`** (2.4). One condition on one line, and
   without it `make demo` is not reproducible, which is the whole point of
   `demo/scenario.json`. Axel.
4. **`ready` and `end_check`** (2.4b, 2.4c). The page sends a message the server
   drops, and the camera check's threshold is both never fixed and thrown away
   per node. Decide whether the measurement is meant to survive a node at all; if
   it is, it cannot live on an object rebuilt per node. Axel and Ilan.
5. **Which ladder speaks** (2.5). This is now the second most expensive one
   after the gate. The visual ladder pass decided Tally shows before he speaks
   and made L1 and L2 silent; the server's `hint_1..3` ladder speaks at 5 s and
   10 s on its own clock and fills that silence, because `tutor_line` is null
   there and the page falls through to `tally`. The pass did not land. The
   contract says the tutor owns the voice, so the server ladder should stop
   producing `reaction` lines and keep only what `Outcome.hint_level` records.
   Axel and Ilan.
6. **`tutor_line_cuts`** (2.6). The page reads it, or it comes off the wire.
   Right now the page guesses seven other names and gets the behaviour by
   accident through `lineKind`.
7. **The line-drop vocabulary** (2.7). One list, in one place. Two of the three
   reasons the page sends are logged as `unknown`, and `interrupted` is
   unreachable. Ilan owns the tutor's end.
8. **F8, `or` or `and`** (2.10). The engine and the tutor implement the same
   amendment differently, and the tutor's is the slower of the two by 550 ms on
   the one event F8 was written to make instant. Either the tutor adopts the
   `or`, or F8 is amended to say the two confirmations are different things.
   Axel and Ilan, and it needs SPEC.md edited either way.
9. **Params-file strictness** (2.9). `build_tutor` decided a bad params file must
   not stop the lesson; `Engine.__init__` raises on one, and runs first.
10. **The orphan gate parameter** (2.3). `gate_ready_button_s`: the page reads
    it, the params file has not got it, so how long a child with a dead
    microphone waits for a way forward is a constant in the page rather than a
    bounded policy number. Small, and it will rot quietly because the fallback
    is graceful.

And one that is nobody's feature but everybody's problem: **a test that a field
is sent is not a test that anything reads it** (4b). Four of the findings above
survived a green suite that way, and the one cross-file feature that did not,
`closing_in`, is the one whose author wrote down what the other side would do
and made that fallback real.
