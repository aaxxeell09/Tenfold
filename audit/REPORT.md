# Tenfold audit, merged report

Date 2026-09-13. Source: the four area audits in this folder. Nothing here was re-run;
this is a merge, a dedup and a triage of what the four passes already established.

Long form for any entry is in its area file. The area identifier in brackets
(for example `[area1 G5]`) is the key to look it up.

- `audit/area1-server-camera.md`, server, camera thread, /video, /ws
- `audit/area2-web.md`, web/course
- `audit/area3-lesson-rules-demo.md`, lesson, classifier rules, demo mode
- `audit/area4-loop-data-tests.md`, loop, data, eval, scripts, tests, Makefile

Tags used below:
- **[ILAN]** the finding lives in a file Ilan owns (`classifier/rules.py`, `lesson/tutor.py`,
  `eval/`, `loop/`, `Makefile`, `tenfold/doctor.py`, `data/snapshot.json`). Do not edit.
- **[FROZEN]** the finding lives in a file frozen by the contract (`app/landmarks.py`,
  `app/normalize.py`, `classifier/schema.py`, `classifier/features.py`, `data/samples.jsonl`,
  `eval/scorers.py`). Needs both owners to agree.

Everything untagged is ours.

---

## 1. Summary

Raw counts as reported by each area, then the merged count after removing findings that two
areas reported as the same defect.

| Area | BLOCKER | BUG | CLEANUP | Total |
|---|---|---|---|---|
| area1, server and camera | 4 | 11 | 8 | 23 |
| area2, web | 0 | 10 | 13 | 23 |
| area3, lesson, rules, demo | 1 | 14 | 9 | 24 |
| area4, loop, data, tests | 3 | 8 | 10 | 21 |
| **Raw total** | **8** | **43** | **40** | **91** |
| Merged away as duplicates | 1 | 4 | 4 | 9 |
| **Unique total** | **7** | **39** | **36** | **82** |

Of the 82 unique findings, **19 are not ours to fix** (Ilan owned or frozen). They are listed
in section 5 with a one line ask each.

The nine duplicates merged: missing `app/main.py`; the demo scenario drift; the one process wide
`Lesson`; the frozen MJPEG image; the missing websocket reconnect; `_hello`; `watchCamera`;
the doctor camera probe; `waiting_answer`.

---

## 2. BLOCKER (7)

### B1. `make run` and `make demo` call `app/main.py`, which has never existed [ILAN, Makefile]
`Makefile:15-19`, also `README.md:18`, `app/landmarks.py:3`, `CLAUDE.md:33`.
Both targets die instantly with "can't open file app/main.py"; `git log --all` shows the file was
never committed. The live app is `app/server.py`. Second half of the same problem: even pointed at
`app/server.py`, `--demo` alone still opens the real webcam (`app/server.py:769-774`), so the camera
free command is `--mock --demo`. Done item 14 of SPEC.md section 15 is impossible today.
From: area1 B1 (`Makefile:16`, `:19`) and area4 B1 (`Makefile:15-19`).

### B2. On any day after the first, the start check asks a different fact and the path never opens
`lesson/scheduler.py:477-479`, `:520-531`, `:596-598`; `web/course/app.js:267`, `:303-341`.
`opening_fact` is picked by `_most_fragile` with no `_in_scope` filter, so a returning learner gets
served, say, 9x9 inside a check hard coded to say "Touch your 6 with your 6". 36 is refused, the node
never ends, `tenfold.checked` is never set and the whole course map stays locked. A laptop used on
Saturday and shown on Sunday hits this.
From: area3 B1.

### B3. Demo scenario drift, two mechanisms
`app/server.py:441-448` and `:766`; `lesson/scheduler.py:923-931`, `demo/scenario.json:5-12`,
`web/course/app.js:611-614`, `:673-681`, `:731-741`.
(a) `demo_played` lives on the process wide `Lesson`, so the scripted scenario plays once per server
process. Rehearsing the demo once silently destroys the reproducible sequence for every later run,
and nothing on screen says so. The undocumented workaround is a server restart before each run.
(b) `ScriptedScheduler.next_exercise` ignores `target` and `allowed`, so the six step script runs
inside a five question node and four of its six exercises are outside the node scope. The counter
clamps at "5 of 5", three stars now need six correct answers, and the header says "Hello eight"
while Tally asks 9x7, 9x9 and 10x10. Side effect: those out of scope facts land in the learner
record and come back as due reviews.
From: area1 B4 (mechanism a) and area3 BUG 4 (mechanism b). Same subsystem, same stage failure.

### B4. Any perception init failure kills the camera thread silently and leaks the camera
`app/server.py:527-560`, in particular `:543` `load_classifier()` and `:545` `HandDetector(...)`.
Both run after the camera is open and outside the `try`, so a bad rules file or a cold model cache
with no network kills the thread before the `finally`: the webcam stays held, `stop` is never set,
aiohttp keeps serving, `/video` never emits a byte and the page says "Getting ready" for ever.
Anything raising inside the loop (`detect`, `normalizer.update`, `cv2.flip`, `encode_jpeg`) dies the
same way. There is no supervision and no restart.
From: area1 B2.

### B5. No camera means a server that stays up in front of a frozen page
`app/server.py:536-541`, `:678-701`.
On `CameraError` the handler logs one line and returns. `stop` is not awaited by anything, so
`web.run_app` keeps serving. The single most likely demo failure (Zoom or Photo Booth holding the
webcam, macOS permission not granted) gives an open `/video` response that writes nothing and a page
that looks like it is still loading. `app/camera.py:94-98` writes a good error message that nobody
on the demo laptop ever sees.
From: area1 B3.

### B6. `make dashboard` points at a dashboard that does not exist [ILAN, Makefile]
`Makefile:45-46` runs `marimo run dashboard/loop_dashboard.py`. There is no `dashboard/` directory in
the repo and it was never committed, while `marimo` is still in `requirements.txt:10`. SPEC.md
section 10 and the demo script (section 14, 1:30 to 2:10) both hang on that notebook. Either the
notebook is missing work or the target is dead; somebody has to decide before the demo.
From: area4 B2.

### B7. The committed snapshot has no held-out curve, no blind curve, no kNN line, no ceiling [ILAN]
`data/snapshot.json`: `informed` v0 and v1 both `heldout=None`, `blind: []`, `knn_heldout: null`,
`detection_ceiling: null`. Done items 10 and 13 have no data in the repo as committed, and the
headline chart is one grey train line. Regenerating needs, in order: a held-out capture at
`../tenfold-heldout/test.jsonl`, `make eval-heldout`, `make knn`, a blind arm run, `make snapshot`.
From: area4 B3.

---

## 3. BUG (39)

### G1. The camera loop busy spins at 100 percent CPU on a failing read
`app/server.py:548-551`. `if not ok: continue`, no sleep, no counter, no exit. Unplug the camera or
give an index that opens but never delivers and one core runs flat out for ever while the page shows
the last frame. Fan noise and a dropped frame rate on everything else during the demo. [area1 G1]

### G2. The learner record leaks from one child to the next
`app/server.py:422-430`. `if raw is not None` skips the assignment when the browser sends `state:
null` (`web/course/app.js:614`, `:35`), so `self.learner` stays the previous child's record and
`_new_scheduler` hands it to the new session. `LearnerState.from_dict(None)` already does the right
thing (`lesson/scheduler.py:243-247`); the guard is what defeats it. [area1 G2]

### G3. Nothing reconnects the websocket, and a failed connect hangs the lesson for ever
`web/course/app.js:541-551` (area1), `:542-551` (area2). `onclose` only nulls the socket, there is no
retry, no timeout and no UI. Restart the server and `sendLesson` becomes a silent no op: answer keys,
mic and quit all do nothing. Start a lesson while the server is down and the page sits on "Getting
ready" for ever; the check sits on "Show me both hands." for ever.
From: area1 G3 and area2 BUG 4.

### G4. The MJPEG image never recovers, and each tab holds two permanent `/video` connections
`web/course/index.html:111`, `web/course/app.js:629-631`, `app/server.py:678-701`.
The check camera `<img>` is static markup, so the stream opens on the welcome screen and is held for
the life of the page (`hidden` does not stop an image loading); during a lesson one tab holds two of
Chrome's six connections per host. After a server restart the multipart body is cut off and the
`<img>` keeps painting the last frame it received: there is no `onerror` for a truncated multipart
and no reload anywhere. The camera looks alive and frozen, which is the worst failure mode on stage.
The server side of the stream itself is clean.
From: area1 G4 and area2 BUG 3.

### G5. A websocket that dies during the first send leaks its queue for the life of the process
`app/server.py:703-729`. `hub.subscribe()` at `:706` is outside the `try`, so a client that
disappears between `prepare` and the first `send_json` leaves its queue in `hub._subscribers` for
ever. Bounded slow leak, plus `_publish` walks a growing list of dead queues every frame. [area1 G5]

### G6. The pump task can raise on a closed socket and its exception is never retrieved
`app/server.py:709-713`. No `ws.closed` check, so asyncio logs "Task exception was never retrieved"
in the terminal. `pumping.cancel()` at `:727` is never awaited. Noise, not a hang, but noise in the
terminal during the demo. [area1 G6]

### G7. Ctrl+C very likely prints a thread traceback, and the worker is never joined
`app/server.py:209-214`, `:783-785`, `:816-822`. `on_stop` only sets `stop`; nothing joins the camera
thread, so its next `call_soon_threadsafe` can hit a closed loop and raise `RuntimeError`. Cosmetic,
the `finally` still releases the camera. Traced, not run. [area1 G7]

### G8. One process wide `Lesson`, so a second websocket silently steals the session
`app/server.py:240-278`, `:703-729`, `:764-766`, `:392-406`, `:457-466`.
`create_app` builds one `Lesson` per process. A reload mid lesson never sends `quit`, the reloaded
page opens a second socket and its `start_node` resets the scheduler under the first one. Two real
tabs fight over the same engine, scheduler and learner; the first tab then drops every message
because `m.node !== lesson.node.id` (`web/course/app.js:569`) and freezes with no error. `_hello`
also overwrites `self.learner` before the "already running" guard. The 20 s heartbeat caps the damage
at about 40 s for a dead peer, but nothing stops a live second client taking the session.
From: area1 G8 and area3 BUG 10.

### G9. `node_end` writes the learner record before the node filter
`web/course/app.js:554-563`. `saveLearner(m.state)` at `:556` runs before the check at `:563` that
the message belongs to the lesson this page is running. Reproducible in `--mock`: an abandoned
session's `node_end` overwrites the freshly loaded page's record. With G2 the record written can be a
previous child's. [area1 G9]

### G10. `_hello` refuses to start a session once any node has been played, and nothing sends it
`app/server.py:457-466`, `:450-456`. `_quit` clears `node` but not `self.scheduler`, and
`scheduler.outcomes` is never emptied, so after the first node a `hello` client gets no session and
no message at all. Latent: the shipped page never sends `hello`, only `tests/test_server.py` does,
but `app/server.py:27-31` advertises it as supported.
From: area1 G10 and area3 CLEANUP 3.

### G11. `_publish` can drop `node_end`
`app/server.py:216-221`. The `qsize() > 8` drop is right for 15 per second state refreshes, but
`node_end` and `session_end` take the same path; dropping one leaves the page stuck on the last
question with no finish screen. Reasoned about, not reproduced. [area1 G11]

### G12. A lesson whose two pairs are the same fact freezes the counter and the first-try bonus
`web/course/app.js:572`, effects at `:674` and `:681`, node at `web/course/levels.js:25`.
Question counting hangs off a change of `m.fact`, but node `u1-l2` "Six and seven" has
`pairs: [[6,7],[7,6]]`, which is one fact. All five exercises carry `6x7`: the header reads "1 of 5"
throughout, the progress bar stays at 0 percent, and `slipped` is never reset so one slip on question
1 costs the 15 XP bonus for the whole node. [area2 BUG 1]

### G13. Leaving a running lesson by the hash leaves the lesson alive
`web/course/app.js:104-116`, `:898`, `:109`. `show()` never calls `leaveLesson()`, so Back leaves
`lesson` non null, the node running on the server and no `quit` sent. State messages keep arriving,
`renderPractice` keeps writing into the hidden `#lesson` and `speak(...)` keeps firing, so Tally
talks over the home screen. [area2 BUG 2]

### G14. A two level jump loses the accessory of the level it passed through
`web/course/app.js:742`, card at `:763-774`. A perfect boss is 220 XP and can cross two level spans
at once; `earnedAt(after.level)` is then null, so the glasses earned at level 2 appear on Tally with
no mention and only one card is shown. [area2 BUG 5]

### G15. A wrong pose costs the first-try XP, which contradicts the star score
`web/course/app.js:573` against `app/server.py` `_check`. The server says fixing a finger is how the
input works, not a mistake, and counts that answer in `correct`. The page sets `slipped` on
`wrong_pose` anyway, so the same exercise earns a star point and pays 10 XP instead of 15. The
`hint_level > 0` half of the rule is defensible, the `wrong_pose` half is not. [area2 BUG 6]

### G16. Quitting a lesson discards every correct answer already paid for
`web/course/app.js:820-825`, reached from `:870`. `addXp` only runs inside `finish()`, so three of
five correct then quit gives no XP at all, against the SPEC rule of 10 XP per correct exercise.
[area2 BUG 7]

### G17. Once the microphone is denied there is no way back inside the session
`web/course/app.js:479-506`, `:486-488`, `:869`. `voice.denied` is never cleared, so granting the
permission a moment later changes nothing until a reload. Related: `:897` arms recognition on the
first click anywhere, so the prompt appears on the welcome screen before anything explains why.
[area2 BUG 8]

### G18. The check's third step has no visible way to answer without a microphone
`web/course/index.html:120-124`, `web/course/app.js:889-892`, label at `:507`. On a browser with no
`SpeechRecognition` the check still says "Say the answer." and shows the mic pill. Typing does work,
digits land in `#check-miclabel`, but Backspace at `:891` does not update that label, so a deleted
digit stays on screen, and the learner is never told typing is possible. [area2 BUG 9]

### G19. The stylesheet blocks the first paint on a Google Fonts request
`web/course/styles.css:5`, with `web/course/index.html:9-10`. An `@import` is render blocking and
everything else in the app is local, so on venue wifi or offline the demo machine waits on a request
that cannot succeed before painting anything. [area2 BUG 10]

### G20. Malformed learner state from localStorage raises, and the lesson then silently never starts
`lesson/scheduler.py:242-262`, `:142-150`, `:186-194`, reached from `app/server.py:427` and `:459`.
Only the top level `learner_id` check is defensive; everything else is a bare cast, and six measured
shapes raise `ValueError`, `AttributeError` or `TypeError`. The websocket handler swallows and logs
it (`app/server.py:722-725`), so the socket survives but `self.running` was never set and no message
is ever sent: "Getting ready" for ever with no error and no timeout. [area3 BUG 1]

### G21. A correct answer after any finger correction is recorded as a failed exercise
`app/server.py:355-356`, `:490-500`, `lesson/scheduler.py:738-748`, `:834-848`.
`pose_error` is set the first time the engine reports `wrong_pose`, which happens on essentially
every exercise while the child brings their hands up, and it is ANDed into `Outcome.correct`. A
perfect node therefore lowers pose mastery, never raises math mastery, fills the retry queue, trips
the two errors in a row rescue and closes with "That is plenty for today". Math mastery stuck at 0
means `due_session = session` for ever, so the same two or three facts are re-served endlessly.
This is the server side twin of G15. [area3 BUG 2]

### G22. `state.sessions` counts course nodes, not sessions
`lesson/scheduler.py:470-471`, `:369-376`, `:349-358`, `app/server.py:436`. Every `start_node` builds
a fresh `Scheduler` and increments `sessions`, and so does the one exercise start check. Four minutes
of play takes a fact to mastery 4, which `MASTERED_FROM` counts as solid. The mastery 1 spacing "the
next session" becomes about thirty seconds, and `PERSONAL_ORDER_AFTER_SESSIONS = 3` flips after three
nodes instead of three days. Directly contradicts the module docstring at `:20-23`. [area3 BUG 3]

### G23. Orientation alternation freezes after five attempts
`lesson/scheduler.py:163-165`, `:698-712`. `Record.log` keeps only the last five attempts and
`_orientation` indexes on `len(attempts) % 2`, which saturates. From the sixth attempt on the child
only ever sees one hand order, which is the thing the pose dimension exists to vary.
`tests/test_scheduler.py:264` walks only four attempts, so it passes. [area3 BUG 5]

### G24. `error_profile` attributes pose errors by symmetry, not by hand
`lesson/scheduler.py:787`. `outcome.left != outcome.right` says whether the fact is a double, not
which hand was wrong, so every pose error on an asymmetric fact is filed as `wrong_left`. The only
per child diagnostic in the record carries no information. `Scheduler.note_pose_error` at `:795-798`
is the correct home for the fix and has no caller. [area3 BUG 6]

### G25. Open session shaping is inert: every open session is exactly six exercises
`lesson/scheduler.py:802-824`. Every branch falls through to the unconditional
`return REACTION_END_SUCCESS` on line 824, so `SESSION_MAX_EXERCISES = 10` is unreachable and the
five step `NEXT_DAY_PLAN` check can never fire. Impact is limited today because the shipped page
always sends a `count`. [area3 BUG 7]

### G26. The confidence pick and the end on a success pick ignore the node scope
`lesson/scheduler.py:592-594`, `:647-649`, `:672-676`. `_mastered_facts` has no `_in_scope` filter,
so a mastered fact from another unit can be served as a confidence exercise, on top of the one
allowed outside review and on top of B2's opening fact. A five exercise node can legitimately contain
three facts it is not named about. `_facts_at_mastery` right below does filter, which shows the
omission is accidental. [area3 BUG 8]

### G27. A malformed `demo/scenario.json` takes the lesson down silently
`app/server.py:746-754`, `lesson/scheduler.py:888-907`, `app/server.py:715-725`. `make_scheduler`
guards only the missing file case; bad JSON or a step missing `fact`, `left` or `right` raises inside
`command`, which is swallowed and logged. Same hang as G20. The file is well formed today, and a one
character edit on stage is enough. [area3 BUG 9]

### G28. `classifier/rules.py` answers confidently on a NaN detection confidence **[ILAN]**
`classifier/rules.py:26-28`. `nan < UNKNOWN_THRESHOLD` is False so the unknown gate is skipped, and
the clamp turns `nan` into `1.0`. A hand MediaPipe was unsure about is reported as a full confidence
6-10 read. `classifier/schema.py:129-133` would have caught a NaN, but it is handed 1.0.
[area3 BUG 11]

### G29. `classifier/rules.py` raises on a None detection confidence **[ILAN]**
`classifier/rules.py:26`. `float(None)` raises `TypeError`. The live app is safe because
`classifier/loader.py:58-64` catches it, but any caller importing `classify` directly is not.
[area3 BUG 12]

### G30. Degenerate geometry is reported as a confident 6 and 6 **[ILAN]** **[FROZEN]**
`classifier/rules.py:29-36` with `classifier/features.py:52-54` and `classifier/schema.py:42`.
A negative `scale` passes `present`; when the two scales cancel, `mean_scale` is 0, the distance
matrix is all infinity and `argmin` returns index 0, giving confidence 0.9 for 6 and 6 instead of
unknown. The infinity sentinel is never distinguished from a real reading. [area3 BUG 13]

### G31. `lesson/tutor.py` is wired to nothing **[ILAN]**
245 lines imported only by `tests/test_tutor.py`. `app/server.py` imports `lesson.tally` and never
`lesson.tutor`, so the W&B Inference call, its thread, its cache and the `tutor.call` Weave op from
SPEC.md sections 4.1 and 8 do not run in the product. Every phrase the child hears is the fallback.
[area3 BUG 14]

### G32. The loop tests copy untracked local files into their fixture repo
`tests/test_guard_and_loop.py:21-23`. The ignore list misses `eval/last_train_report.json`,
`loop/blind-rules.py`, `loop/watch-state.json` and `loop/nightly*.log`, so running `make eval` or a
blind arm once makes `:233` and `:231` fail on the next `make test`. Green right now only because
both files happen to be absent. Fix: extend the ignore patterns or copy only `git ls-files`.
[area4 G1]

### G33. `data/capture.py` spins for ever when the camera stops delivering, with no way to quit
`data/capture.py:404-407`. In `_wait_for_block` there is no timeout and no `cv2.waitKey`, so a camera
that opens then stops returning frames leaves a silent busy loop with no window update and no way to
press `q`. Only Ctrl-C gets out. Same shape as G1, different process. [area4 G2]

### G34. Ctrl-C during a capture ends in a traceback
`data/capture.py:351-395`, `:510-549`. No `KeyboardInterrupt` handler anywhere. No data is lost (the
`finally` blocks close everything) but the operator gets a traceback mid session, which reads like a
crash. `--dry-run` is worse: `run_dry:341-348` sleeps through about 21 minutes with Ctrl-C the only
exit. [area4 G3]

### G35. The detection ceiling the dashboard draws is never produced by anything **[ILAN, partly]**
`loop/snapshot.py:68` reads `data/detection_ceiling.json`; `docs/interfaces.md:52,82,93` documents it
as committed and as a line on the headline chart. Nothing in the repo writes it.
`scripts/gonogo.py:90-113` measures exactly those numbers and only prints them. So
`detection_ceiling` stays `null` and the MediaPipe ceiling line of SPEC.md section 6 cannot appear.
`loop/snapshot.py` is Ilan's; `scripts/gonogo.py` is ours. [area4 G4]

### G36. `eval/baseline_knn.py` reads the held-out set outside `eval/run_eval.py` **[ILAN]**
`eval/baseline_knn.py:31,57-58` opens `../tenfold-heldout/test.jsonl` in its own process, which is
the default mode and the only one `Makefile:30-31` uses. No actual leak to the critic, but this is
the rule the whole blindness story rests on and a judge reading the code will find the exception.
[area4 G5]

### G37. `make doctor` fails on exactly the machine done item 14 describes **[ILAN]**
`tenfold/doctor.py:82-104`. If the `claude` CLI is on PATH but not authenticated, the "claude auth"
line is a FAIL, so doctor exits 1 and prints "DOCTOR FAILED". A laptop with Claude Code installed and
no Tenfold `.env` is the normal judge machine. Everything else optional is correctly a WARN.
[area4 G6]

### G38. `loop/nightly.sh` and `make watch` are macOS only and fail silently elsewhere **[ILAN]**
`loop/nightly.sh:15,18` and `Makefile:43` prefix the run with `caffeinate -i`. On Linux both arms
exit 127, the log says "nightly done informed=127 blind=127" and the 20 iteration night does nothing.
[area4 G7]

### G39. The early stop counter only counts metric gate rejections **[ILAN]**
`loop/critic.py:423,497,512,521`. `no_improve` is incremented only in the metric gate branch, so an
iteration that dies in the guard, in a claude timeout or in a failed train eval leaves it untouched
and the early stop never fires. The nightly passes `--no-early-stop`, so the night is unaffected; a
daytime `make loop N=10` with a broken agent is not. [area4 G8]

---

## 4. CLEANUP (36)

### C1. `watchCamera()` is dead code, and so is the CSS it targets
`web/course/app.js:902-912`, called at `:917`; `web/course/styles.css:770-771`.
`$(".panel-camera-art")` matches nothing (the right rail is `.panel-hands`), so the function returns
on its third line. Had it run it would have opened a third `/video` stream for a 96 by 72 thumbnail.
From: area1 C1 and area2 C1.

### C2. Every camera frame is JPEG encoded whether or not anyone reads `/video`
`app/server.py:552-553`. Unconditional at camera rate, on the same thread as MediaPipe, wasted while
the child is on the course map. A reader counter would fix it. [area1 C2]

### C3. Dead `except KeyboardInterrupt`
`app/server.py:816-819`. aiohttp 3.14.3 already handles it inside `run_app`. [area1 C3]

### C4. The browser opening `Timer` is never cancelled
`app/server.py:813-814`. If the port is in use, `run_app` raises and the process still opens a tab
pointing at whatever is already on that port. `Timer.cancel()` in the `finally`. [area1 C4]

### C5. `continuity == by_x` deep compares two hands every frame **[FROZEN]**
`app/normalize.py:161-163`. `continuity` is assigned `by_x` itself, so `is` is what is meant; `==`
compares 21 coordinate triples per hand. No correctness impact. [area1 C5]

### C6. The model download uses a fixed temp filename **[FROZEN]**
`app/landmarks.py:63-68`. `hand_landmarker.task.part` is a constant, so `app/server.py` and
`data/capture.py` started together on a cold cache corrupt each other's download.
`tempfile.NamedTemporaryFile(dir=..., delete=False)` fixes it. [area1 C6]

### C7. `waiting_answer()` is unreachable in the live app
`lesson/engine.py:247-251` and `:267-270` (`next`) are called only from `tests/test_engine.py`.
`app/server.py` drives the engine with `load()` and `repeat()` only, yet `app/server.py:112`
(`STATE_MOMENT`) and `web/course/app.js:527`, `:601` all branch on the state. Either wire it after
the reasoning lines or drop it.
From: area1 C7 and area3 CLEANUP 1.

### C8. `tenfold/doctor.py` opens the camera its own way **[ILAN]**
`tenfold/doctor.py:36-55` and `:43-48` do `cv2.VideoCapture(0)` with no backend and only index 0,
while `app/camera.py:35-37` forces `CAP_AVFOUNDATION` on macOS and `:80-92` probes 0 to 3 and rejects
black devices. So doctor can warn about a camera the app would have used, or pass on index 0 while
the app picks index 2. It is a WARN, so nothing breaks; it just tells the truth less often.
From: area1 C8 and area4 C8.

### C9. About a third of `styles.css` targets markup that no longer exists
`web/course/styles.css`, three concatenated stylesheets. Roughly 95 of 318 class names match nothing.
`.cam` is defined twice with different geometry (`:183` and `:666`) and `.dev-reset` twice (`:316`,
`:754`); both current `.cam` users override width, so it works by accident. [area2 C2]

### C10. Dead state selectors: the page never writes those state values
`web/course/styles.css:53`, `:200-202`, `:683`, `:689`, `:694`, `:714-717`. `body[data-state=...]`
never matches (the page sets `body.dataset.view`), and the two panels that matter are driven by
`[data-reaction="cannot_see"]` and `.has-reasons`. [area2 C3]

### C11. Unused icon symbols
`web/course/icons.svg`: `icon-check`, `icon-grid`, `icon-user` are never referenced. [area2 C4]

### C12. Two sources of truth for the learner's name
`web/course/app.js:16`, `:37-47`. `tenfold.name` duplicates `learner.display_name`, and the dev reset
at `:872` removes `tenfold.learner` but not `tenfold.name`, so after a reset the learner keeps a name
with zero XP and never sees the welcome screen again. [area2 C5]

### C13. `unitAt` survives leaving the practice view
`web/course/app.js:57`, `:203-207`, set at `:866`. Switch to unit 3, go home, come back and the map
still opens on unit 3 rather than on the current node's unit. [area2 C6]

### C14. `recordLesson` can return without `stars`, and `finish` compares it to 0
`web/course/levels.js:175` returns `{progress, error: "locked"}`; `web/course/app.js:743` tests
`res.stars === 0`. Not reachable from the current UI, but the guard should test the error.
[area2 C7]

### C15. Only 6 of the 10 levels are reachable in one pass
Every scored node played perfectly is 1020 XP, which is level 6, while `web/course/levels.js:59-71`
advertises thresholds to 3200 and accessories at 8 and 10, listed in the profile ladder at
`web/course/app.js:844-851`. They need replays, which grant full XP again every time. [area2 C8]

### C16. Two tabs on the same app both write the learner record
`web/course/app.js:554-561` runs in every open tab because `node_end` is broadcast. XP is protected by
the `Math.max` at `:558`, but the two writes race and the losing tab's scheduler memory wins.
[area2 C9]

### C17. The camera check can be skipped, then comes back later
`web/course/index.html:62` links straight to `#practice` and `route()` allows it; `tenfold.checked` is
only set by `checkEnd`, so the learner is sent through the check next time they use the home door.
[area2 C10]

### C18. `fitOverlay` runs before the first frame has a size
`web/course/app.js:655-670`. `videoRatio` falls back to 4 by 3 while `naturalWidth` is 0 and nothing
listens for the image's `load`, so the first overlay is laid out for the wrong aspect ratio and the
finger circles are offset until the next state message. Line 659 also queries inside `stage` before
line 660 checks `stage` is not null. [area2 C11]

### C19. `decorate` on every frame of a boss lesson
`web/course/app.js:677` recomputes `levelInfo`, walks four `data-level-*` queries and chains two async
svg fetches on every state message, to expand one flame icon. [area2 C12]

### C20. Recognition stays live on every screen
`web/course/app.js:500-506`, `:246`. Once armed, `voice.wanted` stays true and `onend` restarts
recognition for the life of the page, so Chrome's microphone indicator is lit on welcome, home and
profile. Only `voice.gate` stops a heard number being submitted. [area2 C13]

### C21. The `repeat` command is never sent, and would misbehave if it were
`app/server.py:405-406`. Not in any `sendLesson` call. If wired it would rearm the engine without
resetting `hint_level`, `started_at`, `pose_error`, `math_error` or `recorded`, so the repeated
exercise would still be recorded as failed with the hint level maxed. [area3 CLEANUP 2]

### C22. `Scheduler.note_pose_error` has no caller anywhere
`lesson/scheduler.py:795-798`. It is the correct home for the fix to G24. [area3 CLEANUP 4]

### C23. Unreachable Tally phrases
`lesson/tally.py:30` (`unknown_gesture`) cannot fire: the unknown condition leaves the engine in
`waiting_pose` with no event (`lesson/engine.py:229-231`). `:40-41` (`count_fingers`, `guided`) ride
on the `start_session` return value and `web/course/app.js` never reads `steps`, `plan` or
`opening_fact`. [area3 CLEANUP 5]

### C24. `engine.load` appends any unseen ordered pair for the life of the process
`lesson/engine.py:276-285`. Bounded at 25 so not a leak, but the list, `self._index` and the wrap in
`next()` are vestigial now that the scheduler owns the order. [area3 CLEANUP 6]

### C25. `set_scope` is not defensive about its input
`lesson/scheduler.py:493-509`. `int(pair[0])` raises IndexError on a one element pair, an empty
`pairs` silently gives an unscoped session, and a pair outside 6 to 10 empties the `_any_pick` pool
and ends the node with zero exercises and zero stars. Not reachable from `levels.js` today.
[area3 CLEANUP 7]

### C26. `_quit` says "nothing is recorded", but outcomes survive in memory
`app/server.py:450-455`. Everything recorded before the quit is already in `self.learner` and
`state.sessions`; it is only discarded because the page re-sends its own copy on the next
`start_node`, which does not happen when the page has no learner yet. [area3 CLEANUP 8]

### C27. `addXp` and `setName` write a learner object with no `learner_id`
`web/course/app.js:48-53`. `LearnerState.from_dict` discards such a record wholesale
(`lesson/scheduler.py:246-247`). XP round trips anyway thanks to the `Math.max` at `:558`, but
`display_name` and any history in such a record are silently dropped server side. [area3 CLEANUP 9]

### C28. A whole test file silently skips itself on a machine set up by `make install`
`tests/test_voice.py:22` and `:94`: `playwright` is not in `requirements.txt`, so 15 tests never run
and `make test` stays green. Same shape in `tests/test_levels_js.py:22-25` when `node` is missing.
[area4 C1]

### C29. Hardcoded browser path
`tests/test_voice.py:26-29` pins `/opt/pw-browsers/chromium-1194/chrome-linux/chrome`. It degrades
gracefully, so the only cost is confusion. [area4 C2]

### C30. A test caps how good the critic is allowed to get
`tests/test_features_rules.py:87-92` asserts `exact_match < 0.85` on the live `classifier/rules.py`,
the file the critic rewrites. Measured 0.642 today, so there is room, but a good enough night turns
`make test` red for the wrong reason. `test_rules_v0_contact` and
`test_rules_v0_near_contact_is_not_contact` (`:40-48`) have the same shape. Fix: score
`tests/rules_v0.py` instead. [area4 C3]

### C31. Timing based tests
`tests/test_tutor.py:15,32,45,64,79,104,149` sleep 0.08 s to 0.4 s to synchronise with a background
thread, and `:110,133` assert on wall clock classifications. Margins are not tight, but this is the
file that will flake on a loaded laptop at 2 a.m. [area4 C4]

### C32. Duplicated blocks **[ILAN, partly]**
`eval/baseline_knn.py:20-26` imports and calls `load_env()` twice **[ILAN]**; `.env.example:9-18` and
`:19-23` both carry a "critic model backend (pick one)" block with different wording **[ILAN]**;
`.gitignore:9` and `:14` both `.venv/`, `:19` and `:20` both `.claude-critic/` (ours). [area4 C5]

### C33. `scripts/gonogo.py` has no caller
221 lines referenced by nothing but SPEC.md section 13. It needs a camera to run and it is the only
place that computes the detection ceiling numbers of G35. Either wire a `make gonogo` target that
writes `data/detection_ceiling.json`, or delete it after copying the measured numbers into the
README. [area4 C6]

### C34. Stale references **[ILAN, partly]**
`tenfold/doctor.py:81` points at `python app/capture.py`; the script is `data/capture.py` **[ILAN]**.
`docs/loop.md` tells the reader to read `data/metrics.json`, which `.gitignore:17` ignores, so a
cloned repo never has it; the committed artefact is `data/snapshot.json`. `TODOS.md` still lists as
deferred the browser app and TTS, both shipped. [area4 C7]

### C35. Small dead or loose ends in the eval and loop code **[ILAN]**
`eval/run_eval.py:97` `git_sha(path)` never uses `path`. `eval/run_eval.py:46-62` `load_samples`
accepts any row regardless of its `split` field, so one stray test row appended to
`data/samples.jsonl` would be scored as train and copied into the critic worktree. `loop/guard.py:77`
uses `timeout=30.0` where SPEC.md section 7 specifies 5 s. [area4 C9]

### C36. Dataset coverage is thinner than SPEC.md 5.6 **[FROZEN]**
`data/samples.jsonl`: 608 windows against a target of 800 to 1000, all `person: axel`, all
`split: train`, and three of the six angle by distance blocks are empty
(`top_near 1`, `top_far 0`, `side_far 0`). `eval/slices.py:311` skips any slice under 20 samples, so
`angle=top` never reaches the metric gate. A capture decision, not an edit. [area4 C10]

---

## 5. Not ours to fix, for Ilan

19 items. One line ask each. Nothing below should be edited by us.

**Makefile and doctor**
1. B1, `Makefile:15-19`: point `run` at `$(PY) app/server.py` and `demo` at
   `$(PY) app/server.py --mock --demo --no-open`, so the camera free path really is camera free.
2. B6, `Makefile:45-46`: decide before the demo whether `make dashboard` gets a notebook or the
   target gets deleted, and drop `marimo` from requirements if it goes.
3. G37, `tenfold/doctor.py:82-104`: make the "claude auth" line a WARN, and FAIL only when the loop
   is actually being run, so done item 14 can pass on a judge's laptop.
4. C8, `tenfold/doctor.py:36-55`: probe the camera through `app.camera.open_camera` instead of a bare
   `cv2.VideoCapture(0)`, so doctor and the app agree about whether there is a camera.
5. C34, `tenfold/doctor.py:81`: the fix hint says `app/capture.py`; the script is `data/capture.py`.

**Loop and eval**
6. G36, `eval/baseline_knn.py:31,57-58`: route the kNN line through `run_eval.py` (a `--model knn`
   flag) or write the exemption into CLAUDE.md, so the held-out rule holds mechanically.
7. G38, `loop/nightly.sh:15,18` and `Makefile:43`: guard `caffeinate` with
   `command -v caffeinate >/dev/null && CAF="caffeinate -i" || CAF=""`, or the night is lost on Linux.
8. G39, `loop/critic.py:423,497,512,521`: increment `no_improve` on guard rejections, claude timeouts
   and failed train evals too, so early stop can fire on a broken agent.
9. G35, `loop/snapshot.py:68`: either accept a `data/detection_ceiling.json` written by
   `scripts/gonogo.py` (ours, we can do that half) or drop the key from the snapshot and the docs.
10. C35, `eval/run_eval.py:46-62`: a two line `split` filter in `load_samples` would make the
    "never read test outside run_eval" rule mechanical. Also `git_sha(path)` ignores `path` (`:97`)
    and `loop/guard.py:77` uses a 30 s timeout where SPEC.md section 7 says 5 s.
11. C32, `eval/baseline_knn.py:20-26`: `load_env` imported and called twice.
12. C32, `.env.example:9-18` and `:19-23`: the critic backend block is there twice with different
    wording, pick one.

**Snapshot and data**
13. B7, `data/snapshot.json`: regenerate after a held-out capture, `make eval-heldout`, `make knn`
    and a blind arm run, otherwise the headline chart is one grey train line and done items 10 and 13
    have no evidence.

**classifier/rules.py and tutor.py**
14. G28, `classifier/rules.py:26-28`: a NaN detection confidence skips the unknown gate and clamps to
    1.0; gate on `math.isnan` before the threshold test.
15. G29, `classifier/rules.py:26`: `float(None)` raises; the live app survives only because the
    loader catches it.
16. G30, `classifier/rules.py:29-36`: when `mean_scale` is 0 the all infinity distance matrix is read
    as a confident 6 and 6; distinguish the sentinel from a real reading.
17. G31, `lesson/tutor.py`: it is imported by nothing but its own test, so the W&B Inference path of
    SPEC.md 4.1 and 8 does not run in the product. Wire it or say on stage that Tally speaks from
    `tally.py`.

**Frozen files, need both owners**
18. C5, `app/normalize.py:161-163` and C6, `app/landmarks.py:63-68`: an `==` that should be `is`, and
    a fixed temp filename that two processes can corrupt on a cold cache. Both harmless today, both
    need a joint nod.
19. C36, `data/samples.jsonl`: 608 rows, one person, three of six angle by distance blocks empty, so
    `angle=top` never reaches the metric gate. Capture decision, not an edit.

---

## 6. What to watch during the demo

Even with everything above fixed, these can still go wrong on stage.

1. **Something else is holding the webcam.** Zoom, Photo Booth, Chrome in another profile, a virtual
   camera. Quit them all before starting, and start the server before opening the browser.
2. **The camera picture is frozen, not dead.** If you restart the server while Chrome stays open, the
   image keeps showing the last frame it ever received. It looks alive. If the hands stop moving on
   screen, reload the page, do not debug the server.
3. **Never reload mid lesson, and never open a second tab.** One lesson lives in the whole server
   process, so the second tab takes the session and the first one goes quiet with no error.
4. **Restart the server before each rehearsal of the scripted demo.** The script plays once per
   process. The second run is live scheduling with different exercises in a different order.
5. **Use a fresh browser profile, or reset before you start.** A learner record left over from a
   rehearsal changes which facts come up and can leave the day-2 start check asking the wrong
   question.
6. **If the page sits on "Getting ready", it is not slow, it is stuck.** Nothing times out and no
   error is ever shown. Reload; if it happens twice, restart the server.
7. **Venue wifi.** The stylesheet still pulls a Google font, and a cold model cache downloads 8 MB.
   Load the page once on the venue network before the demo so both are warm, or work offline on
   purpose.
8. **The microphone prompt shows up on the very first click.** If anyone denies it, voice is off for
   the rest of the session and only a reload brings it back. Accept it once, early, off stage.
9. **Do not press Back or the browser gesture during a lesson.** The lesson keeps running underneath
   and Tally keeps talking over whatever screen you are on.
10. **The counter and the stars can disagree with what you see.** On a node whose two pairs are the
    same fact the header stays at "1 of 5", and a corrected finger can cost XP while still earning
    the star. Do not read the numbers out loud as proof of anything.
11. **Watch the terminal, not just the screen.** A dead camera thread, a swallowed exception and a
    dropped `node_end` all look identical from the front row: a page that just stops.
12. **The evidence chart.** Unless `data/snapshot.json` has been regenerated, the headline chart has
    one train line and no held-out, blind, kNN or ceiling curve. Know what you will say if someone
    asks for the held-out number.
