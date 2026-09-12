"""The live practice screen: camera, perception, lesson, browser. SPEC.md sections 4.1, 8 and 9.

    python app/server.py                 # webcam, opens http://localhost:8000
    python app/server.py --mock          # no camera, cycles the three states every 2 s
    python app/server.py --camera 1      # force a camera index

One process. A worker thread owns the camera and runs the pipeline of SPEC.md
4.1, camera -> landmarks -> normalize -> classifier -> engine, and an aiohttp
server hands the result to the browser two ways: the mirrored video as MJPEG on
/video, and the lesson state as JSON on the /ws websocket. The page draws the
finger numbers from the coordinates in that JSON, over the video, so nothing is
composited in Python and the browser stays a thin renderer.

The classifier is the one pinned in data/BEST_VERSION, never HEAD, through
classifier/loader.py, which falls back to classifier/rules.py on its own.

Messages to the browser:

    {"type": "state", "state", "exercise", "tally", "wrong": [{hand, number}],
     "match": [{hand, number}], "answer", "reasoning": [str], "fingers":
     [{hand, number, x, y}]}

x and y are in 0..1 in the mirrored image, the same frame the MJPEG stream
shows, so the overlay lines up without the page knowing anything about cameras.

Messages from the browser: {"type": "check", "value": 56} and {"type": "next"}.

Weave traces lesson events only, never frames (CLAUDE.md), and only when
WANDB_API_KEY is set.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiohttp import WSMsgType, web  # noqa: E402

from classifier import features  # noqa: E402
from classifier.schema import FINGER_NUMBERS, GestureState, Window  # noqa: E402
from lesson import tally  # noqa: E402
from lesson.engine import Engine, Update  # noqa: E402
from tenfold.env import load_env  # noqa: E402

log = logging.getLogger("tenfold.server")

LESSON_KEY: web.AppKey = web.AppKey("lesson")
STOP_KEY: web.AppKey = web.AppKey("stop")

WEB_DIR = REPO_ROOT / "web"
INDEX = WEB_DIR / "tenfold.html"

DEFAULT_PORT = 8000
VIDEO_FPS = 20.0
# The lesson only changes on an event, but the fingertips move every frame, so
# the same message is refreshed at this rate to keep the overlay alive.
FINGER_REFRESH_S = 1 / 15
MOCK_STEP_S = 2.0
JPEG_QUALITY = 80

# Which tally line to show when no event carries the moment.
STATE_MOMENT = {
    "intro": "intro",
    "exercise_shown": "exercise_shown",
    "waiting_pose": "waiting_pose",
    "waiting_answer": "correct_pose",
}


# --- turning the pipeline into one message ---------------------------------


def fingers_from_window(window: Window) -> list[dict[str, Any]]:
    """The ten finger numbers, placed in mirrored image coordinates."""
    import numpy as np

    out: list[dict[str, Any]] = []
    for hand_name, frames in (("left", window.left), ("right", window.right)):
        frame = frames[-1] if frames else None
        if frame is None or not frame.present:
            continue
        try:
            points = features.image_points(frame)
        except (ValueError, TypeError):
            continue
        if not np.isfinite(points).all():
            continue
        for number in FINGER_NUMBERS:
            x, y = points[features.TIP_INDEX[number]]
            out.append({"hand": hand_name, "number": number,
                        "x": round(float(x), 4), "y": round(float(y), 4)})
    return out


def moment_of(update: Update, hands_seen: int) -> str:
    """The tally key for this update: the event if there is one, else the state."""
    if update.event:
        return update.event
    if update.state == "waiting_pose" and hands_seen == 1:
        return "one_hand"
    return STATE_MOMENT.get(update.state, update.state)


def build_message(update: Update, fingers: list[dict[str, Any]],
                  hands_seen: int) -> dict[str, Any]:
    context = {"hint": update.hint, "answer": update.answer,
               "exercise": update.exercise.title}
    return {
        "type": "state",
        "state": update.state,
        "exercise": update.exercise.title,
        "tally": tally.phrase(moment_of(update, hands_seen), context),
        "wrong": [f.as_dict() for f in update.wrong],
        "match": [f.as_dict() for f in update.match],
        "answer": update.answer,
        "reasoning": list(update.reasoning),
        "fingers": fingers,
    }


# --- shared state between the camera thread and the event loop --------------


class Hub:
    """One writer thread, many browser readers. The loop is the only mutator."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue] = set()
        self._message: dict[str, Any] = {"type": "state", "state": "intro",
                                         "exercise": "", "tally": tally.phrase("intro"),
                                         "wrong": [], "match": [], "answer": None,
                                         "reasoning": [], "fingers": []}
        self._frame: bytes | None = None
        self._frame_lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def message(self) -> dict[str, Any]:
        return self._message

    def publish(self, message: dict[str, Any]) -> None:
        """Called from the camera thread."""
        if self._loop is None:
            self._message = message
            return
        self._loop.call_soon_threadsafe(self._publish, message)

    def _publish(self, message: dict[str, Any]) -> None:
        self._message = message
        for queue in list(self._subscribers):
            if queue.qsize() > 8:      # a stalled browser never blocks the camera
                continue
            queue.put_nowait(message)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def set_frame(self, jpeg: bytes) -> None:
        with self._frame_lock:
            self._frame = jpeg

    def frame(self) -> bytes | None:
        with self._frame_lock:
            return self._frame


class Lesson:
    """The engine plus the lock that keeps the camera thread and the browser apart."""

    def __init__(self, engine: Engine, hub: Hub) -> None:
        self.engine = engine
        self.hub = hub
        self._lock = threading.Lock()
        self._fingers: list[dict[str, Any]] = []
        self._hands_seen = 0
        self._last_push = 0.0
        self.trace: Callable[[Update], None] = lambda update: None

    def push(self, update: Update) -> None:
        self.hub.publish(build_message(update, self._fingers, self._hands_seen))
        self._last_push = time.monotonic()

    def observe(self, gesture: GestureState, fingers: list[dict[str, Any]],
                hands_seen: int, now: float) -> None:
        with self._lock:
            self._fingers = fingers
            self._hands_seen = hands_seen
            update = self.engine.observe(gesture, now)
            if update is not None:
                self.trace(update)
                self.push(update)
            elif now - self._last_push >= FINGER_REFRESH_S:
                self.push(self.engine.snapshot())

    def command(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        with self._lock:
            if kind == "check":
                update = self.engine.check(_as_int(message.get("value")))
            elif kind == "next":
                update = self.engine.next()
            elif kind == "repeat":
                update = self.engine.repeat()
            else:
                return
            if update is not None:
                self.trace(update)
                self.push(update)

    def start(self) -> None:
        with self._lock:
            self.push(self.engine.start())


def _as_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# --- the two worker loops ---------------------------------------------------


def encode_jpeg(frame: Any) -> bytes:
    import cv2

    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    return buffer.tobytes() if ok else b""


def camera_loop(lesson: Lesson, stop: threading.Event, camera_index: int | None) -> None:
    """Camera, landmarks, normalize, classifier, engine. One frame at a time."""
    import cv2

    from app.camera import CameraError, open_camera
    from app.landmarks import HandDetector
    from app.normalize import Normalizer
    from classifier.loader import load_classifier

    try:
        camera, _ = open_camera(camera_index)
    except CameraError as error:
        log.error("server: %s", error)
        stop.set()
        return

    classify = load_classifier()
    log.info("server: classifier from %s", getattr(classify, "rules_path", "?"))
    detector = HandDetector(num_hands=2)
    normalizer = Normalizer()
    try:
        while not stop.is_set():
            ok, frame = camera.read()
            if not ok:
                continue
            frame = cv2.flip(frame, 1)
            lesson.hub.set_frame(encode_jpeg(frame))
            window = normalizer.update(detector.detect(frame))
            fingers = fingers_from_window(window)
            hands_seen = len({f["hand"] for f in fingers})
            lesson.observe(classify(window), fingers, hands_seen, time.monotonic())
    finally:
        detector.close()
        camera.release()


def mock_gestures(exercise: Any) -> tuple[GestureState, GestureState, GestureState]:
    """No hands, then a near miss, then the pose, for whichever exercise is up.

    Derived from the live exercise rather than hard coded, otherwise the mock
    stops reaching correct_pose the moment the lesson moves on.
    """
    near = exercise.b + 1 if exercise.b < 10 else exercise.b - 1
    return (
        GestureState(method="unknown", confidence=0.2),
        GestureState(method="6-10", left=exercise.a, right=near, contact=False, confidence=0.9),
        GestureState(method="6-10", left=exercise.a, right=exercise.b, contact=True, confidence=0.95),
    )


MOCK_PHASES = 3


def mock_fingers(step: int) -> list[dict[str, Any]]:
    """Ten plausible fingertips, drifting a little so the overlay visibly lives."""
    drift = 0.01 * ((step % 4) - 1.5)
    out = []
    for hand, base_x in (("left", 0.32), ("right", 0.68)):
        for index, number in enumerate(FINGER_NUMBERS):
            spread = (index - 2) * 0.035
            out.append({
                "hand": hand,
                "number": number,
                "x": round(base_x + (spread if hand == "left" else -spread), 4),
                "y": round(0.52 - abs(index - 2) * 0.045 + drift, 4),
            })
    return out


def mock_frame() -> Any:
    import cv2
    import numpy as np

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[:] = (28, 24, 21)
    cv2.putText(frame, "mock camera", (150, 250), cv2.FONT_HERSHEY_SIMPLEX,
                1.2, (90, 84, 78), 2, cv2.LINE_AA)
    return frame


def mock_loop(lesson: Lesson, stop: threading.Event) -> None:
    """No camera. Drive the real engine through waiting, wrong and correct."""
    jpeg = encode_jpeg(mock_frame())
    lesson.hub.set_frame(jpeg)
    step = -1
    started = time.monotonic()
    while not stop.is_set():
        now = time.monotonic()
        slot = int((now - started) / MOCK_STEP_S)
        phase = slot % MOCK_PHASES
        if slot != step:
            # The correct pose latches the lesson, so move on before replaying it.
            if phase == 0 and step >= 0:
                lesson.command({"type": "next"})
            step = slot
        gesture = mock_gestures(lesson.engine.exercise)[phase]
        hands = 0 if gesture.method == "unknown" else 2
        fingers = mock_fingers(slot) if hands else []
        lesson.observe(gesture, fingers, hands, now)
        time.sleep(1 / 30)


# --- weave -----------------------------------------------------------------


def make_tracer() -> Callable[[Update], None]:
    """Trace lesson events only, and only with a key. Never one op per frame."""
    if not os.environ.get("WANDB_API_KEY"):
        return lambda update: None
    try:
        import weave
    except ImportError:
        log.warning("server: WANDB_API_KEY is set but weave is not installed")
        return lambda update: None

    project = "tenfold" + os.environ.get("TENFOLD_PROJECT_SUFFIX", "")
    entity = os.environ.get("WANDB_ENTITY")
    weave.init(f"{entity}/{project}" if entity else project)

    @weave.op
    def lesson_event(event: str, state: str, exercise: str, answer: int | None) -> dict:
        return {"event": event, "state": state, "exercise": exercise, "answer": answer}

    def trace(update: Update) -> None:
        if not update.event:
            return
        try:
            lesson_event(update.event, update.state, update.exercise.title, update.answer)
        except Exception:       # tracing must never take the demo down
            log.exception("server: weave trace failed")

    return trace


# --- http -------------------------------------------------------------------


def make_app(lesson: Lesson) -> web.Application:
    hub = lesson.hub

    async def index(request: web.Request) -> web.StreamResponse:
        if not INDEX.exists():
            raise web.HTTPNotFound(text=f"missing {INDEX}")
        return web.FileResponse(INDEX, headers={"Cache-Control": "no-store"})

    async def video(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={
            "Content-Type": "multipart/x-mixed-replace; boundary=frame",
            "Cache-Control": "no-store",
        })
        await response.prepare(request)
        try:
            while True:
                jpeg = hub.frame()
                if jpeg:
                    await response.write(
                        b"--frame\r\nContent-Type: image/jpeg\r\n"
                        b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                        + jpeg + b"\r\n"
                    )
                await asyncio.sleep(1 / VIDEO_FPS)
        except (asyncio.CancelledError, ConnectionResetError, ConnectionAbortedError):
            pass
        return response

    async def websocket(request: web.Request) -> web.StreamResponse:
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        queue = hub.subscribe()
        await ws.send_json(hub.message)

        async def pump() -> None:
            while True:
                await ws.send_json(await queue.get())

        pumping = asyncio.create_task(pump())
        try:
            async for raw in ws:
                if raw.type is not WSMsgType.TEXT:
                    continue
                try:
                    lesson.command(json.loads(raw.data))
                except (json.JSONDecodeError, AttributeError):
                    log.warning("server: ignored a malformed command")
        finally:
            pumping.cancel()
            hub.unsubscribe(queue)
        return ws

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/video", video)
    app.router.add_get("/ws", websocket)
    if WEB_DIR.exists():
        app.router.add_static("/web", WEB_DIR, name="web")
    app[LESSON_KEY] = lesson
    return app


def create_app(mock: bool = False, camera: int | None = None) -> web.Application:
    """Wire the hub, the lesson and the worker thread into one aiohttp app.

    Split out of main so the tests can drive the whole thing in mock mode without
    a camera, a browser or a port.
    """
    hub = Hub()
    lesson = Lesson(Engine(), hub)
    lesson.trace = make_tracer()
    stop = threading.Event()
    worker = threading.Thread(
        target=mock_loop if mock else camera_loop,
        args=(lesson, stop) if mock else (lesson, stop, camera),
        daemon=True,
        name="tenfold-capture",
    )

    app = make_app(lesson)

    async def on_start(_: web.Application) -> None:
        hub.bind(asyncio.get_running_loop())
        lesson.start()
        worker.start()

    async def on_stop(_: web.Application) -> None:
        stop.set()

    app.on_startup.append(on_start)
    app.on_cleanup.append(on_stop)
    app[STOP_KEY] = stop
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tenfold live practice screen")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--camera", type=int, default=None,
                        help="camera index, probed over 0 to 3 when not given")
    parser.add_argument("--mock", action="store_true",
                        help="no camera, cycle the three states every 2 s")
    parser.add_argument("--no-open", action="store_true", help="do not open the browser")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    load_env()

    app = create_app(mock=args.mock, camera=args.camera)

    url = f"http://localhost:{args.port}"
    print(f"tenfold: {url}" + ("  (mock)" if args.mock else ""))
    if not args.no_open:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()

    try:
        web.run_app(app, host=args.host, port=args.port, print=None)
    except KeyboardInterrupt:
        pass
    finally:
        app[STOP_KEY].set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
