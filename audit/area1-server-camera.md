# Audit, area 1: app/server.py, the camera thread, /video, /ws

Read only audit, 2026-09-13. Scope: `app/server.py`, `app/camera.py`, `app/landmarks.py`,
`app/normalize.py`, and the browser side of the MJPEG stream and the websocket
(`web/course/app.js`, `web/course/index.html`). Every line number below was read, not guessed.
Where I could not run the failure I say so in the finding.

Context: `app/ui.py`, `app/main.py` and `app/replay.py` from SPEC.md section 4 do not exist.
The web shell replaced them. That is not itself a finding, but it is the cause of BLOCKER 1.

---

## BLOCKER

### B1. `make demo` and `make run` point at a file that does not exist
`Makefile:16` and `Makefile:19`, `README.md:18`, `app/landmarks.py:3`, `CLAUDE.md:33`.

```
run:   $(PY) app/main.py
demo:  $(PY) app/main.py --demo
```

`ls app/` gives `camera.py landmarks.py normalize.py server.py`. There is no `app/main.py`.
Both targets die instantly with "can't open file app/main.py", and the README's install
instructions are `make doctor && make demo`. SPEC.md section 15 item 14 ("`make doctor` then
`make demo` work on a clean machine without a key or a webcam in under 5 minutes") is currently
impossible.

Second half of the same problem: even once the target points at `app/server.py`, `--demo` alone
is not camera free. `create_app` picks the worker at `app/server.py:769-774`, `mock_loop` only
when `--mock` is passed, so `python app/server.py --demo` opens the real webcam. The camera free
command is `--mock --demo`. `tenfold/doctor.py:40` and `:54` both tell the user to fall back on
`make demo` when the camera is unreadable, which is exactly the case where the current `--demo`
cannot work.

Fix: `run: $(PY) app/server.py`, `demo: $(PY) app/server.py --mock --demo`.
Owner: Makefile and doctor are Ilan's, `app/server.py` is Axel's. No edit made.

### B2. Any perception init failure kills the camera thread silently and leaks the camera
`app/server.py:527-560`.

```
537      camera, _ = open_camera(camera_index)      # guarded by try/except CameraError
543  classify = load_classifier()                   # not guarded
545  detector = HandDetector(num_hands=2)           # not guarded
547  try:
548      while not stop.is_set(): ...
558  finally:
559      detector.close()
560      camera.release()
```

`load_classifier()` (`classifier/loader.py:53-67`) calls `exec_module` on the rules file pinned by
`data/BEST_VERSION` and then `getattr(mod, "classify")`. A rules file that fails to import, or one
without `classify`, raises here, not inside the guarded `safe_classify`.
`HandDetector.__init__` calls `ensure_model` (`app/landmarks.py:58-69`), which downloads 8 MB over
`urllib` on a cold cache; no network, a proxy, or a 120 s timeout all raise.

Both raise after the camera is open and before the `try`, so:
the thread dies with a traceback on stderr, `camera.release()` never runs (the webcam stays held,
LED on, and no other process can take it), `stop` is never set, aiohttp keeps serving, and the
browser gets an `<img src="/video">` that never produces a single byte plus a websocket that
never sends a `state` message. On screen that is "Getting ready" forever with no error.

The same silent death applies to anything raising inside the loop: `detector.detect`,
`normalizer.update`, `cv2.flip`, `encode_jpeg`. Only `classify` is wrapped (loader.py:58-64).
There is no supervision, no restart, no way for the page to learn that perception is gone.

Fix: wrap the whole body, and on any exception publish a message the page can render
("the camera stopped"), then set `stop`.

### B3. No camera means a server that stays up in front of a frozen page
`app/server.py:536-541`, `app/server.py:678-701`.

```
536  try:
537      camera, _ = open_camera(camera_index)
538  except CameraError as error:
539      log.error("server: %s", error)
540      stop.set()
541      return
```

`stop` is read only by the two worker loops (`:548`, `:613`) and set by `on_stop` (`:783-785`).
Nothing awaits it, so setting it does not stop `web.run_app`. The single most likely demo failure
(Zoom or Photo Booth holding the webcam, macOS camera permission not granted) produces one log
line the operator will not be looking at, a `/video` response that stays open forever writing
nothing (`hub.frame()` is `None`, so the `if jpeg:` at `:686` never fires), and a page that looks
like it is still loading. `app/camera.py:94-98` writes a genuinely good error message, and nobody
on the demo laptop ever sees it.

Fix: on `CameraError`, publish an error state on the hub and stop the runner, or fall back to the
mock loop so the screen at least says what happened.

### B4. The scripted demo scenario plays only once per server process
`app/server.py:441-448`, with `create_app`'s `lesson.demo_available = demo` at `:766`.

```
443  scripted = scripted_ok and self.demo_available and not self.demo_played
444  self.demo_played = self.demo_played or scripted
```

`demo_played` lives on the process wide `Lesson`. The start check does not consume it
(`scripted_ok = node.kind != "check"`, `:430`), but the first real lesson node does. Every later
node in the same process falls back to the live `Scheduler`, so the exercises, their order and
Tally's reasons change. SPEC.md section 15 item 12 asks for the demo to be rehearsed three times;
rehearsing it once on a running server silently destroys the reproducible sequence, and there is
nothing on screen to say so. The workaround is to restart the server before every run, which is
not written down anywhere.

Fix: reset `demo_played` when a node ends, or key it on the session rather than the process.

---

## BUG

### G1. The camera loop busy spins at 100 percent CPU on a failing read
`app/server.py:548-551`.

```
549  ok, frame = camera.read()
550  if not ok:
551      continue
```

No sleep, no counter, no exit. Unplug the camera, let another app grab it, or pass
`--camera N` for an index that opens but never delivers (common with V4L2 on Linux, and
`app/camera.py:72-78` takes an explicit index at face value without a test read), and this thread
spins one core flat out forever while the page shows the last frame. On a laptop that is fan noise
and a dropped frame rate on everything else during the demo.

Fix: `time.sleep(0.01)` on failure, and give up after N consecutive failures with a message to the
page.

### G2. The learner record leaks from one child to the next
`app/server.py:422-430`.

```
426  if raw is not None:
427      self.learner = LearnerState.from_dict(raw, now=now_utc())
```

`web/course/app.js:614` sends `state: learner()`, and `learner()` returns `null` for a browser that
has never played (`app.js:35`). JSON `null` arrives as `None`, the guard skips, and
`self.learner` stays the previous child's record. `_new_scheduler` (`:446-448`) then hands that
record to the new session, so mastery, due reviews and session counts from child A shape child B's
first node, and B's `node_end` writes A's record into B's localStorage (`app.js:556-561`).

The irony is that `LearnerState.from_dict(None)` already does the right thing: see
`lesson/scheduler.py:243-247`, "anything missing or malformed becomes a fresh learner". The
`if raw is not None` guard is what defeats it.

Fix: drop the guard, or reset `self.learner` whenever `raw is None`.

### G3. Nothing reconnects the websocket, and a failed connect hangs the lesson forever
`web/course/app.js:541-551`.

```
545  socket = new WebSocket(`ws://${location.host}/ws`);
546  socket.onopen = () => { socketReady = true; onOpen(); };
548  socket.onclose = () => { socketReady = false; socket = null; };
551  function sendLesson(payload) { if (socket && socketReady) socket.send(...); }
```

Restart the server while Chrome is open (or drop wifi for a moment) and the socket closes with no
retry. `sendLesson` becomes a silent no op, so the answer keys, the mic and `quit` all do nothing
and the child sees a frozen lesson with no message. Recovery only happens by accident, because the
next `startLesson` calls `connect` again and `socket` is back to `null`.

Worse case: start a lesson while the server is down. `connect` builds a socket, the handshake
fails, `onOpen` is never called, `startLesson` has already swapped the view to the lesson shell
(`app.js:602-615`), and the page sits on "Getting ready" with no timeout and no error. Same shape
if `connect` is called while a socket is still in CONNECTING and that attempt fails: the `open`
listener registered at `:544` never fires and the callback is dropped.

Fix: reconnect with backoff on `onclose`, and show a banner while disconnected.

### G4. The MJPEG image never recovers, and every tab holds two permanent /video connections
`web/course/index.html:111`, `web/course/app.js:631`, `app/server.py:678-701`.

There are two `<img src="/video">`. One is in the always present `#check-cam` markup (a `hidden`
section still fetches its images in Chrome), one is written by `shellPractice`. Both open a
multipart response that by design never ends, so a single tab permanently occupies two of Chrome's
six connections per host. Add a reload that leaves the old sockets in TIME_WAIT and static asset
requests can queue behind them.

When the server restarts, the multipart body is cut off. An `<img>` in that state keeps painting
the last frame it received; there is no `onerror` for a truncated multipart and there is no
reload anywhere in `app.js`. The camera therefore looks alive and frozen, which is the worst
possible failure mode for a demo, until somebody reloads the page.

The server side of the stream is in reasonable shape: `response.write` raises
`ConnectionResetError` once the transport is closing, `CancelledError` is re-raised (`:694`) and
everything else ends the loop (`:696-700`). I could not make the generator leak.

Fix: on the page, `img.onerror` plus a periodic cache busting reassignment of `src`; consider
serving `/video` only while a lesson is on screen.

### G5. A websocket that dies during the first send leaks its queue for the life of the process
`app/server.py:703-729`.

```
706  queue = hub.subscribe()
707  await ws.send_json(hub.message)
...
714  try:
...
726  finally:
727      pumping.cancel()
728      hub.unsubscribe(queue)
```

`subscribe` happens before the `try`. If the client disappears between `prepare` and that first
`send_json` (a reload at the wrong moment, a probe, a page closed during startup), the exception
propagates out of the handler before the `finally` exists, and the queue stays in
`hub._subscribers` forever. Every later publish pushes into it until `qsize() > 8`
(`:219-221`) and then skips it, so it is a bounded slow leak rather than a crash. It also means
`_publish` walks a growing list of dead queues on every frame refresh.

Fix: move `subscribe` inside the `try`, or use `try/finally` around both.

### G6. The pump task can raise on a closed socket and its exception is never retrieved
`app/server.py:709-713`.

```
709  async def pump() -> None:
710      while True:
711          await ws.send_json(await queue.get())
```

No `ws.closed` check. When the peer goes away between a publish and the send, `send_json` raises
inside a task nobody awaits, so asyncio logs "Task exception was never retrieved" when it is
collected. In practice the `async for` reader ends at the same moment and the `finally` cancels
the task, so this is noise rather than a hang, but it is noise in the terminal during the demo.
The `pumping.cancel()` at `:727` is also never awaited, so the task is still pending when the
handler returns.

Fix: `while not ws.closed:` and swallow `ConnectionResetError`, then `await` the cancelled task
with `contextlib.suppress(CancelledError)`.

### G7. Ctrl+C very likely prints a thread traceback, and the worker is never joined
`app/server.py:209-214`, `:783-785`, `:816-822`.

`on_stop` only does `stop.set()`. Nothing joins the worker thread and nothing waits for it to
leave the loop. `web.run_app` runs cleanup and then closes the event loop, while the camera thread
is almost certainly inside `camera.read()` or `detector.detect()`, which take tens of
milliseconds. Its next `Hub.publish` reaches

```
214  self._loop.call_soon_threadsafe(self._publish, message)
```

and `call_soon_threadsafe` raises `RuntimeError: Event loop is closed` on a closed loop. That
propagates out of `camera_loop`, runs the `finally` and prints a traceback from the thread
excepthook. I traced the code path but did not run it here (no camera in this environment), so
treat the "very likely" as read: the race window is real, the outcome is a cosmetic traceback, not
a hang, because the `finally` at `:558-560` still releases the camera.

For the record, `aiohttp` 3.14.3 already catches both `GracefulExit` and `KeyboardInterrupt`
inside `run_app` (`aiohttp/web.py:503`), so there is no GracefulExit traceback to fix and the
`except KeyboardInterrupt` at `:818` is dead. I found nothing that could produce MediaPipe's
"cannot schedule new futures after shutdown": the landmarker runs synchronously in VIDEO mode and
there is no executor anywhere in this area.

Fix: guard `publish` with `if self._loop.is_closed(): return`, and join the worker with a timeout
in `on_stop`.

### G8. One process wide Lesson, so a second websocket silently steals the session
`app/server.py:240-278` (one `Lesson` per process, built in `create_app:765`), `:703-729` (no
limit on concurrent sockets, no identity).

A page reload mid lesson never sends `quit` (the page only sends it from `leaveLesson` and
`stopCheck`, `app.js:820-824`, `:275-280`), so the server keeps `running = True` and `node` set.
The reloaded page then opens a second socket, and its `start_node` resets the scheduler under the
first one. Two real tabs on the same server fight over the same engine, the same scheduler and the
same learner. The `heartbeat=20` on the WebSocketResponse (`:704`) does eventually reap a socket
whose peer is gone, which limits the damage to about 40 seconds, and the `node` field on every
message plus the page's filter (`app.js:563`, `:569`) stops a stale message from rendering inside
the wrong lesson. But nothing stops the second client from taking the session.

Fix: at minimum, ignore `start_node` from a socket that is not the one that owns the current
node, or scope `Lesson` per connection.

### G9. `node_end` writes the learner record before the node filter
`web/course/app.js:554-563`.

```
554  if (m.type === "node_end") {
556    if (m.state) { ... saveLearner(m.state); }
562    if (checkRun && m.node_id === "check") return checkEnd();
563    if (!lesson || (m.node_id && m.node_id !== lesson.node.id)) return;
```

The save happens before the message is checked against the lesson the page is actually running.
Reproducible in `--mock`: reload the page mid lesson, the abandoned session keeps advancing on the
server (`mock_loop` sends `next` every 6 s, `:621-623`), it eventually ends, and its `node_end`
overwrites the freshly loaded page's learner record. Combined with G2 the record written can be a
previous child's.

Fix: move the `saveLearner` call below the node filter.

### G10. `_hello` refuses to start a session once any node has been played
`app/server.py:457-466`.

```
460  if self.node is not None or self.scheduler.outcomes:
461      return                      # a node is already running, keep it
```

`_quit` (`:450-456`) clears `node` but not `self.scheduler`, and `scheduler.outcomes` is never
emptied, so after the first node a `hello` client gets no session, no `_advance`, and no message
at all. The shipped page never sends `hello` (only `tests/test_server.py:137, 168, 243` do), so
this is latent rather than live, but the docstring at `app/server.py:27-31` advertises it as a
supported client and it does not work.

Fix: test `self.running` rather than `self.scheduler.outcomes`.

### G11. `_publish` can drop `node_end`
`app/server.py:216-221`.

```
219  if queue.qsize() > 8:      # a stalled browser never blocks the camera
220      continue
```

The rule is right for state refreshes at 15 per second, but `node_end` and `session_end` go
through the same path, and dropping one leaves the page stuck on the last question of a lesson
with no finish screen. Over localhost a queue of 9 is unlikely, so I am flagging this as a
possibility I reasoned about rather than one I reproduced.

Fix: never drop a message whose `type` is not `state`.

---

## CLEANUP

### C1. `watchCamera()` is dead code
`web/course/app.js:902-912`, called at `:917`. It looks for `.panel-camera-art`, which does not
exist anywhere in `web/course/index.html`, so it returns at the first line. Had the element
existed it would have opened a third permanent `/video` connection (see G4). Delete it.

### C2. Every camera frame is JPEG encoded whether or not anyone reads /video
`app/server.py:552-553`. `hub.set_frame(encode_jpeg(frame))` runs unconditionally at camera rate.
Cheap enough at 640x480 and quality 80, but it is pure waste when the child is on the course map
and no `<img>` is streaming, and it is on the same thread as MediaPipe. Skipping the encode when
`hub` has no reader would cost a counter.

### C3. Dead `except KeyboardInterrupt`
`app/server.py:816-819`. `aiohttp` 3.14.3 handles it inside `run_app` (`aiohttp/web.py:503`).

### C4. The browser opening Timer is never cancelled
`app/server.py:813-814`. If the port is already in use, `run_app` raises, and the process still
lingers about a second and then opens a tab pointing at whatever is already on that port, which is
confusing during a rushed demo. `Timer.cancel()` in the `finally`.

### C5. `continuity == by_x` deep compares two hands every frame
`app/normalize.py:161-163`. `continuity` is assigned `by_x` itself when `keep <= flip`, so `is` is
what is meant; `==` on two frozen dataclasses compares 21 coordinate triples per hand. The file is
frozen by the contract (`CLAUDE.md`), so this needs both owners to agree. No correctness impact.

### C6. The model download uses a fixed temp filename
`app/landmarks.py:63-68`. `hand_landmarker.task.part` is a constant, so `app/server.py` and
`data/capture.py` started together on a cold cache write into the same file and corrupt each
other's download. `tempfile.NamedTemporaryFile(dir=path.parent, delete=False)` fixes it. Also
frozen, so mention it before changing it.

### C7. `waiting_answer()` is unreachable in the live app
`lesson/engine.py:247-250`. Nothing in `app/server.py` calls it, so `STATE_WAITING_ANSWER` never
occurs live, even though `app/server.py:112` (`STATE_MOMENT`) and `web/course/app.js:601` (`MOOD`)
both handle it. Either wire it after the reasoning lines or drop the state.

### C8. `tenfold/doctor.py` probes the camera differently from the app
`tenfold/doctor.py:36-55` opens `cv2.VideoCapture(0)` with no backend and only index 0.
`app/camera.py:35-37` forces `CAP_AVFOUNDATION` on macOS precisely because the default can fall
back to FFMPEG and report no camera at all, and `app/camera.py:80-92` probes 0 to 3 and rejects
black devices. So `make doctor` can warn about a camera the app would have used, or pass on index
0 while the app picks index 2. It then advises `make demo`, which is broken (B1). Reuse
`open_camera` in the doctor. Owner: Ilan.

---

## Checked and found clean

- `/video` teardown. A viewer that disappears ends the loop through `ConnectionResetError`;
  `CancelledError` is re-raised; no path leaves the generator spinning on a dead socket.
- `Hub` frame handoff. `set_frame` and `frame` are both under `_frame_lock`, bytes are immutable,
  and there is no torn read. Multiple concurrent `/video` clients share the same bytes safely.
- Lock discipline in `Lesson`. The camera thread and the event loop both take `self._lock`, and
  `call_soon_threadsafe` never blocks, so there is no deadlock and no unlocked mutation of the
  engine or the scheduler.
- Malformed browser input. `json.JSONDecodeError` and every other exception are caught at
  `app/server.py:720-725`, and `LearnerState.from_dict` is defensive
  (`lesson/scheduler.py:243-247`). A bad command cannot take the socket down.
- `classify` cannot crash the thread: `classifier/loader.py:58-64` wraps it and falls back to
  `GestureState.unknown()`.
- `fingers_from_window` (`app/server.py:119-138`) guards absence, `ValueError`, `TypeError` and
  non finite coordinates, and `tests/test_server.py:66-86` covers it.
- No hardcoded absolute paths and no platform assumption beyond the deliberate macOS branch in
  `app/camera.py:35-37`. The model cache honours `XDG_CACHE_HOME` (`app/landmarks.py:36-40`).
- `mock_loop` cannot trip over a missing exercise: `Engine.exercise`
  (`lesson/engine.py:185-187`) always resolves because the constructor refuses an empty list.
- The `--mock` path drives the real `Engine`, the real `Lesson` and the real message builder; the
  only divergence from the live path is perception itself, which is what it is for.
