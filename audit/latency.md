# Acknowledgement latency, before and after

The owner's report: when the child reaches the correct pose, there is a visible
wait before the screen says yes. This file measures that wait on the code as it
stood before the fix, and then again after it, with the same harness and the
same configuration, so the two runs can be read side by side.

Written by the measurement agent. Nothing outside this file was edited: every
probe is a monkeypatch or an injected script living in the harness, listed under
"What was added and removed" at the end.

---

## 1. What is being timed

**T0**, the first perception window in which the classifier sees the target
pose. Concretely, the first call into `Lesson.observe` (app/server.py) whose
`GestureState` has `method == "6-10"`, `contact == True` and both finger numbers
equal to the set the current exercise asks for, counted from the moment the
exercise was armed.

**T1**, the moment the page shows the acknowledgement. Two different things
arrive under that name, and they are timed separately because they are more than
a second apart:

- **T1 state**, the state message carrying `state == "correct_pose"` reaching the
  page and being written into the DOM (`#lesson[data-state]`, the camera frame
  look, the wrong finger aids clearing, Tally's mood art).
- **T1 bubble**, the lesson bubble (`#lesson .say`) actually reading the
  acknowledgement line, which in this build is
  `"That is it. Now count the tens, then the ones."` This is the sentence a child
  reads as yes, and it is the one the owner is waiting for.

The five segments in between:

| segment | from | to | where it is spent |
|---|---|---|---|
| confirmation | T0 | engine emits `correct_pose` | `lesson/engine.py`, the debounce |
| serve | engine emits | `Hub.publish` | app/server.py, the tutor, the message build |
| wire | publish | `onmessage` in the page | loopback websocket |
| render | `onmessage` | DOM written, then painted | web/course/app.js `renderPractice` |
| queue | `onmessage` | bubble text written | web/course/app.js paced speech queue |

## 2. Harness

Deterministic mock camera, driven by a real browser.

```
# terminal 1, the server, with read only timing probes attached from outside
PROBE_PORT=8870 python /tmp/claude-0/-home-user-Tenfold/8c948ff8-3a89-5f82-bc42-3163e62acbdd/scratchpad/lat/probe_server.py before_server.jsonl

# terminal 2, chromium driving the lesson and collecting the page side stamps
python /tmp/claude-0/-home-user-Tenfold/8c948ff8-3a89-5f82-bc42-3163e62acbdd/scratchpad/lat/measure.py before_page.json 30 8870

# the join
python /tmp/claude-0/-home-user-Tenfold/8c948ff8-3a89-5f82-bc42-3163e62acbdd/scratchpad/lat/report.py before_page.json
```

`probe_server.py` calls `app.server.create_app(mock=True)`, which is exactly what
`python app/server.py --mock --no-open --port 8870` runs: there is no `.env` in
the repo and no `WANDB_API_KEY` in the environment, so `load_env` and
`enable_tutor` are both no-ops and Tally speaks `lesson/tally.py` lines with no
network in the path. The probes wrap three methods without changing them:
`Engine.observe`, `Lesson.observe` and `Hub.publish`. Each state message carries
three extra fields out to the page, `probe_pub_ms`, `probe_t0_ms` and
`probe_engine_ms`, which the page ignores.

`measure.py` opens chromium 1194 from `/opt/pw-browsers`, injects a `WebSocket`
subclass that stamps every state message on arrival and two MutationObservers
(one on the bubble text, one on `#lesson[data-state]`), then opens lesson nodes
through `window.Tenfold.startLesson` and opens the next node whenever one ends.
Every stamp on both sides is `time.time() * 1000` and `Date.now()`, the same wall
clock on the same machine, so no offset is fitted.

The mock cycles unknown for 2 s, a near miss for 2 s, the target pose for 2 s,
then sends `next`. So each cycle gives exactly one correct pose, and the
acknowledgement has the rest of that phase to reach the screen before the lesson
moves on. That window is measured too, and it is 1.70 s (median over the run).

**Pinned configuration, to be repeated identically in the after run.** Muted
(`tenfold.muted = true` in localStorage). This container's chromium has no
speech voices, and app.js takes the same branch for a muted page and for a
browser with no voice at all: the line stands for `readMs`, 700 ms plus 320 ms
per word, capped at 6 s, then the `PAUSE_MS` beat of 1 s. Leaving it unmuted
here would have measured a headless artifact, a `speechSynthesis.speak` whose
`onend` never fires and whose 4 s guard timer runs instead. See section 4.

## 3. Before

Tree `2e007bb`, run 2026-09-13 16:48:20 to 16:51:33 UTC, one page load, eight
lesson nodes, **31 correct poses**.

### 3.1 Breakdown, milliseconds

| segment | n | median | p90 | worst | best |
|---|---|---|---|---|---|
| engine confirmation window | 31 | **305.7** | 307.2 | 310.4 | 304.8 |
| server and tutor deciding | 31 | 0.2 | 0.3 | 3.5 | 0.2 |
| publish to the page | 31 | 0.3 | 1.6 | 4.3 | 0.0 |
| page render, DOM written | 31 | 1.0 | 2.0 | 2.0 | 1.0 |
| page render, frame painted | 31 | 28.0 | 31.0 | 34.0 | 18.0 |
| paced speech queue wait | 6 | **349.0** | 827.0 | 1160.0 | 69.0 |

### 3.2 End to end

| T1 | n | median | worst |
|---|---|---|---|
| T0 to the state message on screen | 31 | **307.5** | 315.1 |
| T0 to the state message painted | 31 | 335 (approx) | 349 (approx) |
| T0 to the bubble, when the bubble got it | 6 | **655.2** | 1468.5 |
| T0 to the bubble, all 31 samples | 31 | **never** | **never** |

The six waits that did reach the bubble, in milliseconds after T0: 374.6, 380.0,
549.7, 760.8, 1132.4, 1468.5.

### 3.3 The finding

Three things, and the second one is the bigger fault.

1. **The engine's confirmation window is the whole of the server side wait.**
   305.7 ms of a 307.5 ms path to the state message. `DEBOUNCE_S = 0.3` in
   lesson/engine.py plus one mock tick of quantisation. Everything else on the
   server, the tutor included, is 0.2 ms, and the loopback websocket is 0.3 ms.
   The browser writes the DOM 1 ms after the message lands and paints 28 ms
   later.

2. **The acknowledgement sentence usually never arrives at all.** In 25 of 31
   correct poses the line `"That is it. Now count the tens, then the ones."` was
   never written into the bubble. It is queued behind whatever Tally is already
   saying, and the paced queue in web/course/app.js moves at roughly one line per
   5.9 s (a 13 word correction reads for 4.86 s, plus the 1 s beat), while the
   dialogue produces a new line every 1.5 s to 2 s. Each new line makes the one
   before it stale (`still: () => lesson.turn === turn` in `onServerMessage`), so
   when the queue finally pumps it drops everything but the newest item. The
   acknowledgement survives only when the pump happens to fall inside the pose
   window, which is 6 times out of 31 here.

   What the child was reading instead, over the 25 misses: the old correction
   `"Almost. Your left hand is good. Move your right finger from ..."` 10 times,
   `"Show me both hands, palms towards me."` 5 times, an empty bubble 5 times, a
   bubble that did not change at all 4 times, the opening line once.

3. **At correct_pose the picture barely moves.** `lessonLook` in app.js returns
   `"yes"` only for `answer_correct`; a correct pose returns `"waiting"`. So at
   307 ms what the child actually sees is the orange going out of the speech
   bubble and the wrong finger aids disappearing. There is no green, and the
   sentence that says so is in the queue. That is consistent with the report of a
   visible wait before the screen says yes.

So the answer to "how much is the engine, how much is the server and the tutor,
how much is the page and the queue" on this build is: 306 ms engine, under 1 ms
server and tutor, under 1 ms wire, 29 ms page render, and then the queue, which
is either a few hundred milliseconds or forever.

## 4. Limits of this measurement

- The mock builds a `GestureState` directly, so camera, MediaPipe, normalize and
  `classifier/rules.py` are not in the path. T0 here is the moment the correct
  classifier output enters the lesson, not the moment the child's hand was in
  front of the lens. The per frame perception cost is not measured and is not
  part of any number above. Measuring it would need stamps inside app/camera.py
  or app/landmarks.py, which this agent does not own.
- Muted, for the reason in section 2. A device with real voices paces on the
  utterance rather than on `readMs`. The two are close for these lines (a 13
  word sentence at rate 0.92 is around 5 s), but they are not identical, and no
  number here is a measurement of real text to speech.
- `painted` is the second `requestAnimationFrame` after the DOM mutation, which
  is an upper bound on the paint, not a compositor timestamp.
- One run. The before state can no longer be re-measured: the fix landed on disk
  at 16:51:17, three minutes into the run. The run itself is unaffected, the
  server process had already imported the old `lesson/engine.py` and the page had
  already loaded the old `app.js`, which the constant 305 ms confirmation window
  across all 31 samples confirms.

## 5. What was added and removed

No file in `app/`, `lesson/`, `web/course/` or `tests/` was edited, at any point,
for this measurement. The probes are a separate runner that wraps three methods
at import time and a playwright `addInitScript` that wraps `WebSocket` and adds
two MutationObservers in the page. Both live in the agent scratchpad
(`probe_server.py`, `probe_page.js`, `measure.py`, `report.py`) and neither is in
the repo. Nothing to revert, and nothing was reverted.

## 6. After

To be filled in by the same agent, on the same harness, after the three fixes
land. Same file names, same pinned configuration, same 30 sample target.
