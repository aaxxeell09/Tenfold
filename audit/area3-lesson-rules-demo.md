# Area 3 audit: lesson, classifier rules, demo mode

Read only pass over `lesson/scheduler.py`, `lesson/engine.py`, `lesson/tally.py`,
`lesson/tutor.py`, `classifier/rules.py`, `classifier/schema.py`,
`classifier/features.py`, `demo/scenario.json` and the two files that read them
(`app/server.py`, `web/course/app.js`). Nothing was changed. Every claim below was
run against the code, not inferred. `pytest tests/test_scheduler.py
tests/test_engine.py tests/test_server.py` is green (103 passed), so none of these
is caught by the current suite.

Ownership note, per CLAUDE.md section 12: `classifier/rules.py`, `lesson/tutor.py`
and everything under `eval/` and `loop/` belong to Ilan. Findings there are marked
**OWNER: Ilan, do not edit**. The missing `app/main.py` behind `make run` and
`make demo` is already written up in `audit/area1-server-camera.md` and is not
repeated here.

---

## BLOCKER

### B1. On any day after the first, the start check asks a different fact and the practice path can never open

`lesson/scheduler.py:477-479`, `lesson/scheduler.py:596-598`,
`app/server.py:422-439`, `web/course/app.js:267` and `web/course/app.js:303-341`.

`start_session` calls `set_scope(pairs, length)` first, then, when
`self.session >= 2` and the last session was on an earlier calendar day, sets
`self.opening_fact = self._most_fragile(self.session - 1)`. `_most_fragile`
(`lesson/scheduler.py:520-531`) searches all of `state.math` and never checks
`_in_scope`, and the `index == 0` branch of `_candidates`
(`lesson/scheduler.py:596-598`) yields it without a scope check either. The
comment three lines below it says the opposite: "never as the opening exercise: a
lesson named after two facts has to be about those two facts".

The start check is a one exercise node scoped to `[[6, 6]]`
(`web/course/app.js:267`). Reproduced:

    day 1: a node on 9x6 9x7 9x8 9x9, all answered wrong
    day 2: start_session(pairs=[[6, 6]], length=1)
      -> allowed ['6x6'], opening_fact '9x9'
      -> next_exercise() serves 9x9, 9 x 9, reason review

The check view is hard coded to 6 x 6: it says "Touch your 6 with your 6", the
banner says "That is a 6 and a 6" and the answer line says "Thirty six. Exactly."
(`web/course/app.js:303-334`). The child is shown 9 x 9, is told to touch 6 and 6,
and 36 is refused. `Lesson._check` only records and advances on a correct answer
(`app/server.py:468-488`), so the node never ends, `node_end` never arrives,
`checkEnd()` never runs and `sessionStorage["tenfold.checked"]` is never set
(`web/course/app.js:343-351`). The whole course map stays behind a check that
cannot be passed. Any returning learner, which includes a demo laptop used on
Saturday and shown on Sunday, hits this.

The same hole lets a normal lesson node open on a fact from another node, and that
opening pick is counted into `outside_reviews` (`lesson/scheduler.py:556-557`), so
it also burns the single `MAX_OUTSIDE_REVIEWS` slot. Reproduced: a node scoped to
`[[6, 6], [7, 7]]` served `10x10, 7x7, 6x6, 7x7, 6x6`.

---

## BUG

### 1. Malformed learner state from localStorage raises, and the lesson then silently never starts

`lesson/scheduler.py:242-262` (`LearnerState.from_dict`), `:142-150`
(`Attempt.from_dict`), `:186-194` (`Record.from_dict`), reached from
`app/server.py:427` and `app/server.py:459`.

The docstring at `lesson/scheduler.py:244-245` promises "Anything missing or
malformed becomes a fresh learner rather than an error: this arrives from a
browser's localStorage and must never break a lesson." Only the top level
`learner_id` check delivers that. Everything else is a bare cast. Measured:

    {"learner_id": "a", "sessions": "many"}            ValueError
    {"learner_id": "a", "pose": ["x"]}                 AttributeError: 'list' has no 'items'
    {"learner_id": "a", "math": {"6x7": 3}}            AttributeError: 'int' has no 'get'
    {"learner_id": "a", "math": {"6x7": {"mastery": "high"}}}  ValueError
    {"learner_id": "a", "math": {"6x7": {"attempts": 5}}}      TypeError: 'int' is not iterable
    {"learner_id": "a", "error_profile": {"tens_error": "x"}}  ValueError

The exception is raised inside `Lesson.command`, which the websocket handler
catches and logs (`app/server.py:722-725`). The socket survives, but `_start_node`
never got as far as `self.running = True` or `_advance()`, so no message is ever
sent back. The page sits on "Getting ready" with no error and no timeout. There is
no client side recovery path: `web/course/app.js:26-28` returns the stored object
as is.

### 2. A correct answer after any finger correction is recorded as a failed exercise

`app/server.py:355-356`, `app/server.py:490-500`, `lesson/scheduler.py:738-748`,
`lesson/scheduler.py:834-848`.

`Lesson.observe` sets `self.pose_error = True` the first time the engine reports
`wrong_pose`, which happens on essentially every exercise while the child brings
their hands up. `_record` then builds
`Outcome(correct=correct and not self.math_error and not self.pose_error)`, so the
outcome is `correct=False` even when the child fixed the finger and typed the right
answer. `Scheduler.record` reads only `outcome.correct` for the math half
(`lesson/scheduler.py:747-748`), so the fact stays flat forever, while the pose
half takes the `-1`. `app/server.py:479` deliberately still gives the node star,
which shows the intent was to keep the exercise "earned", but the scheduler never
sees that.

Reproduced, five exercises in a node, every one answered correctly after one pose
correction:

    7x8 -> {'pose': 'down', 'math': 'flat'}   (x5)
    consecutive_errors: 5
    retry_queue: [(5, '6x8'), (6, '7x8')]
    summary reason: 'end_tired'   ("That is plenty for today")
    metrics: first_try_success 0, first_try_rate 0.0, pose_errors 5
    math mastery: 6x8 0, 7x8 0

So a perfect lesson lowers pose mastery, never raises math mastery, fills the retry
queue, trips the two errors in a row rescue rule, and closes with the tired line,
which `web/course/app.js:740` prints as the finish pane subtitle. Because math
mastery never leaves 0, `schedule_next` keeps setting `due_session = session`
(`lesson/scheduler.py:353-354`), so the same two or three facts are re-served for
ever.

### 3. `state.sessions` counts course nodes, not sessions, which defeats the confidence rule and the spacing

`lesson/scheduler.py:470-471`, `:369-376`, `:349-358`, `app/server.py:436`.

Every `start_node` builds a fresh `Scheduler` and calls `start_session`, which does
`self.state.sessions += 1`. A course node is therefore a session, and so is the one
exercise start check, which runs once per visit to the practice view. The module
docstring (`lesson/scheduler.py:20-23`) states the opposite rule: "mastery only
rises when the success lands in a later session than the previous rise. Five
correct answers inside one session are one step, not five." Reproduced, four nodes
back to back at the same timestamp:

    after node 1: sessions=1  mastery 6x6=1
    after node 2: sessions=2  mastery 6x6=2
    after node 3: sessions=3  mastery 6x6=3
    after node 4: sessions=4  mastery 6x6=4

Four minutes of play takes a fact to mastery 4, which `MASTERED_FROM` counts as
solid and which `_mastered_facts` then hands back as confidence exercises. The
mastery 1 spacing "the next session, whenever that is"
(`lesson/scheduler.py:356-357`) means "the next node", roughly thirty seconds.
`PERSONAL_ORDER_AFTER_SESSIONS = 3` also flips to the personal difficulty order
after three nodes instead of three days.

### 4. Demo mode plays six exercises inside a five question node, four of them outside the node

`lesson/scheduler.py:923-931`, `demo/scenario.json:5-12`, `web/course/app.js:611-614`,
`web/course/app.js:673-681`, `web/course/app.js:731-741`.

`ScriptedScheduler.next_exercise` overrides the base method completely: it never
consults `_stop_reason`, `self.target` or `self.allowed`. The page starts the first
lesson node with `count: 5` (`L.LESSON_QUESTIONS`) and the script has six steps.
With `--demo` and a seeded showcase the current node is `u2-l1` "Hello eight",
pairs `[[6,8],[8,6],[7,8]]`. Reproduced:

    target 5, allowed ['6x8', '7x8']
    1: 7x8   8x7   review      in_scope True
    2: 7x9   9x7   next_new    in_scope False
    3: 6x10  10x6  confidence  in_scope False
    4: 7x8   7x8   retry       in_scope True
    5: 9x9   9x9   next_new    in_scope False
    6: 10x10 10x10 level_up    in_scope False
    exercises played: 6, node count asked for: 5

Consequences on stage: the counter at `web/course/app.js:681` is clamped with
`Math.min(s.done, s.total)` so it reads "5 of 5" for both the fifth and the sixth
exercise, and the progress bar stops moving; at `node_end` the page takes the
server's real total (`web/course/app.js:559`), so `starsFor(correct, 6)` now needs
six correct answers for three stars rather than five; and the lesson header says
"Hello eight" while Tally asks 9 x 7, 9 x 9 and 10 x 10. Every id, pair and field
in `demo/scenario.json` does exist in the code (facts, `left`, `right`, `reason`
keys all resolve in `lesson/tally.py`, `onboarding` and `end` are both read), so
the file itself is consistent. The drift is the script length and scope against the
node it is played inside.

Side effect: `record` still runs for the scripted picks, so 7x9, 9x9, 10x10 and
6x10 land in the learner record at mastery 1 and come back as due reviews in the
next node.

### 5. Orientation alternation freezes after five attempts

`lesson/scheduler.py:163-165`, `lesson/scheduler.py:698-712`.

`Record.log` keeps only the last five attempts (`del self.attempts[:-5]`), and
`_orientation` picks the side with `len(attempts) % 2` (or `% len(listed)`). Once a
fact has five logged attempts the length saturates at 5, so the index never moves
again. Measured over ten consecutive attempts on 7x8:

    (7,8) (8,7) (7,8) (8,7) (7,8) (8,7) (8,7) (8,7) (8,7) (8,7)

and with a node's explicit orientations for 6x8:

    (6,8) (8,6) (6,8) (8,6) (6,8) (8,6) (8,6) (8,6) (8,6) (8,6)

From the sixth attempt on, the child only ever sees one hand order, which is the
thing the pose dimension exists to vary. `tests/test_scheduler.py:264` only walks
four attempts, so it passes.

### 6. `error_profile` attributes pose errors by symmetry, not by hand

`lesson/scheduler.py:787`.

    profile["wrong_left" if outcome.left != outcome.right else "wrong_right"] += 1

`outcome.left != outcome.right` says whether the exercise is a double, not which
hand was misplaced. Every pose error on an asymmetric fact is filed as
`wrong_left` and every pose error on 6x6, 7x7, 8x8, 9x9 or 10x10 as `wrong_right`.
Measured on five pose errors on 7x8 and 6x8: `wrong_left 5, wrong_right 0`. The
engine already emits `wrong_left_finger` and `wrong_right_finger`
(`lesson/engine.py:36-37`) and `Scheduler.note_pose_error`
(`lesson/scheduler.py:795-798`) exists to take exactly that, but nothing ever calls
it (see CLEANUP 4). The error profile is the only per child diagnostic in the
record and it currently carries no information.

### 7. Open session shaping is inert: every open session is exactly six exercises

`lesson/scheduler.py:802-824`.

After `done < SESSION_MIN_EXERCISES` returns `None`, the remaining branches all
fall through to the unconditional `return REACTION_END_SUCCESS` on line 824. So
`SESSION_MAX_EXERCISES = 10` (line 814) is unreachable, the five step
`NEXT_DAY_PLAN` check on line 822 can never fire (five is below the minimum of
six), and `CONSECUTIVE_ERRORS_TO_STOP` and `_slowing_down` only choose the reason
string, never the length. An open session is always six exercises, or seven when
the rescue in `_owes_a_success` adds one. `tests/test_scheduler.py:354` asserts
`count <= SESSION_MAX_EXERCISES`, which 6 satisfies. Impact today is limited
because the shipped page never opens an unscoped session (it always sends
`start_node` with a `count`), which is itself worth knowing.

### 8. The confidence pick and the end on a success pick ignore the node scope

`lesson/scheduler.py:592-594`, `:647-649`, `:672-676`.

`_mastered_facts` iterates `fact_order(self.state)` with no `_in_scope` filter, and
both the two errors in a row branch of `_candidates` and `_mastered_pick` use it
directly. Inside a node that means a mastered fact from another unit can be served
as a confidence exercise, on top of the one allowed outside review and on top of
the opening fact of B1, so a five exercise node can legitimately contain three
facts it is not named after. `_facts_at_mastery` right below it does filter by
scope, which shows the omission is accidental.

### 9. A malformed `demo/scenario.json` takes the lesson down silently

`app/server.py:746-754`, `lesson/scheduler.py:888-907`, `app/server.py:715-725`.

`make_scheduler` guards only the missing file case. `ScriptedScheduler.load` calls
`json.loads` (raises on bad JSON) and the constructor does `str(step["fact"])`,
`int(step["left"])`, `int(step["right"])` (KeyError or ValueError on a step missing
or mistyping any of the three). Those run inside `_new_scheduler`, called from
`_start_node`, called from `command`, whose exception is swallowed and logged at
`app/server.py:723-725`. Result is the same hang as BUG 1: the socket is alive,
`self.running` is still False, the page waits for ever. The file is well formed
today; a one character edit on stage is enough.

### 10. One global `Lesson` is shared by every websocket

`app/server.py:764-766`, `app/server.py:392-406`, `app/server.py:457-466`.

`create_app` builds a single `Lesson` and `make_app` closes over it, so every
browser tab drives the same engine, scheduler and learner. A second tab, or a
reload mid lesson, sends `start_node` and replaces `self.scheduler` under the first
one; the first tab then drops every message because `m.node !== lesson.node.id`
(`web/course/app.js:569`) and freezes with no error. `_hello` also overwrites
`self.learner` (`app/server.py:459`) before the "a node is already running" guard
on the next line, so the running scheduler keeps writing into the old state object
while `self.learner` points at a different one.

### 11. OWNER: Ilan, do not edit. `classifier/rules.py` answers confidently on a NaN detection confidence

`classifier/rules.py:26-28`.

    confidence = min(float(left.detection_conf), float(right.detection_conf))
    if confidence < UNKNOWN_THRESHOLD: ...

With `detection_conf = nan` on one hand, `min` returns `nan`, `nan < 0.5` is False,
so the unknown gate is skipped, and `max(0.0, min(1.0, nan))` returns `1.0`.
Measured:

    GestureState(method='6-10', left=10, right=6, contact=False, confidence=1.0)

A hand MediaPipe was unsure about is reported as a full confidence 6-10 read.
`classifier/schema.py:129-133` would have caught a NaN confidence, but the value
handed to it is already 1.0, so the guard's smoke test cannot see this.

### 12. OWNER: Ilan, do not edit. `classifier/rules.py` raises on a None detection confidence

`classifier/rules.py:26`. `float(None)` raises `TypeError: float() argument must be
a string or a real number, not 'NoneType'`. In the live app
`classifier/loader.py:58-64` catches it and returns unknown, so the demo is safe,
but any caller that imports `classify` directly is not.

### 13. OWNER: Ilan, do not edit. Degenerate geometry is reported as a confident 6 and 6

`classifier/rules.py:29-36` with `classifier/features.py:52-54` (frozen).

`HandFrame.present` accepts a negative `scale` (`bool(scale)` is the only check,
`classifier/schema.py:42`). When the two scales cancel, `mean_scale` is 0,
`tip_distance_matrix` returns an all infinity matrix, and `nearest_pair` takes
`argmin` of that, which is index 0. Measured:

    GestureState(method='6-10', left=6, right=6, contact=False, confidence=0.9)

instead of unknown. Same shape of answer for the all infinity case in general: the
sentinel is never distinguished from a real reading.

### 14. OWNER: Ilan, do not edit. `lesson/tutor.py` is wired to nothing

`lesson/tutor.py` (245 lines) is imported only by `tests/test_tutor.py`.
`app/server.py` imports `lesson.tally` and never `lesson.tutor`, so the W&B
Inference call, its thread, its cache and its `tutor.call` Weave op described in
SPEC.md sections 4.1 and 8 do not run in the product. Every phrase the child hears
is the `tally.py` fallback. This is both a missing feature and, as it stands, the
single largest unused file in the area.

---

## CLEANUP

1. `lesson/engine.py:247-251` (`waiting_answer`) and `:267-270` (`next`) are called
   only from `tests/test_engine.py`. `app/server.py` drives the engine with
   `load()` and `repeat()` only, so `STATE_WAITING_ANSWER` is never reached, yet
   `app/server.py:112` maps it in `STATE_MOMENT` and `web/course/app.js:527` and
   `:601` both branch on it.
2. `app/server.py:405-406`, the `repeat` command, is never sent by the page (the
   only `sendLesson` calls are at `web/course/app.js:267, 279, 537, 578, 614, 821,
   892, 893`). If it were wired it would rearm the engine without resetting
   `hint_level`, `started_at`, `pose_error`, `math_error` or `recorded`, so the
   repeated exercise would still be recorded as failed and the hint level would
   stay maxed.
3. `app/server.py:457-466`, `_hello`, is likewise never sent by the shipped page;
   only `tests/test_server.py` uses it.
4. `lesson/scheduler.py:795-798`, `Scheduler.note_pose_error`, has no caller
   anywhere in the repo. It is the correct home for the fix to BUG 6.
5. `lesson/tally.py:30`, the `unknown_gesture` phrase, is unreachable: the unknown
   condition puts the engine in `waiting_pose` with no event
   (`lesson/engine.py:229-231`), so `moment_of` returns `waiting_pose`. The
   onboarding phrases `count_fingers` and `guided` (`lesson/tally.py:40-41`) are
   equally unreachable: `onboarding_steps()` rides on the `start_session` return
   value (`lesson/scheduler.py:488`) and `web/course/app.js` never reads `steps`,
   `plan` or `opening_fact`.
6. `lesson/engine.py:276-285`, `load`, appends any ordered pair it has not seen to
   `self.exercises` for the life of the process. It is bounded at 25 so it is not a
   leak, but the list, `self._index` and the wrap in `next()` are vestigial once
   the scheduler owns the order.
7. `lesson/scheduler.py:493-509`, `set_scope`, is not defensive about its input:
   `int(pair[0])` raises IndexError on a one element pair, an empty `pairs` list
   silently produces an unscoped session with a fixed length, and a pair outside
   6..10 produces a key that is not in `DIFFICULTY_ORDER`, which empties the
   `_any_pick` pool (`lesson/scheduler.py:686`) and ends the node with zero
   exercises and zero stars. Not reachable from `web/course/levels.js` as it stands.
8. `app/server.py:450-455`, `_quit`, says "Nothing is recorded, nothing is scored",
   but every outcome recorded before the quit is already in `self.learner` and in
   `state.sessions`. It survives in memory and is only discarded because the page
   re-sends its own copy on the next `start_node`, which does not happen when the
   page has no learner yet (`raw is None` keeps the dirty state,
   `app/server.py:426-427`).
9. `web/course/app.js:48-53`, `addXp` and `setName` write a learner object with no
   `learner_id`, which `LearnerState.from_dict` discards wholesale
   (`lesson/scheduler.py:246-247`). XP itself round trips correctly because the
   page keeps the higher of the two at `web/course/app.js:558` and the server never
   writes `xp` at all, so the "server never lowers XP" property holds; but
   `display_name` and any history in such a record are silently dropped server
   side. Verified: `from_dict({"xp": 210, "display_name": "Zoe"})` returns a fresh
   learner with `xp=0`.
