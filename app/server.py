"""The live practice screen: camera, perception, lesson, browser. SPEC.md sections 4.1, 8 and 9.

    python app/server.py                 # webcam, opens http://localhost:8000
    python app/server.py --mock          # no camera, cycles the three states every 2 s
    python app/server.py --demo          # the fixed demo/scenario.json sequence
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
     [{hand, number, x, y}], "hint_level", "hint_auto", "pose_slip"}

x and y are in 0..1 in the mirrored image, the same frame the MJPEG stream
shows, so the overlay lines up without the page knowing anything about cameras.

Messages from the browser: {"type": "check", "value": 56}, {"type": "next"},
{"type": "hint"} when the child asks for help, and
{"type": "hello", "state": {...}} which hands over the learner state the page
kept in localStorage. Without a hello the server starts a fresh learner in
memory, so the lesson still runs; nothing is persisted server side, on purpose,
since the child's record belongs in the child's browser.

At the end of a session the server sends
{"type": "session_end", "state", "metrics", "summary"} and the page stores the
state back. The page wiring for hello and for storing that state is not in this
file.

lesson/scheduler.py decides which exercise comes next and why; the reason rides
on every state message as "reason" and lesson/tally.py phrases it.

Weave traces lesson events only, never frames (CLAUDE.md), and only when
WANDB_API_KEY is set.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiohttp import WSMsgType, web  # noqa: E402

from classifier import features  # noqa: E402
from classifier.schema import FINGER_NUMBERS, GestureState, Window  # noqa: E402
from lesson import tally  # noqa: E402
from lesson.engine import Engine, Exercise, Update  # noqa: E402
from lesson.scheduler import (  # noqa: E402
    CANNOT_SEE_AFTER_S,
    DEFAULT_SCENARIO,
    REACTION_CANNOT_SEE,
    LearnerState,
    Outcome,
    Pick,
    Scheduler,
    ScriptedScheduler,
    answer_reaction,
    hint_event,
    now_utc,
    pose_reaction,
)
from tenfold.env import load_env  # noqa: E402

log = logging.getLogger("tenfold.server")

LESSON_KEY: web.AppKey = web.AppKey("lesson")
STOP_KEY: web.AppKey = web.AppKey("stop")

WEB_DIR = REPO_ROOT / "web"
COURSE_DIR = WEB_DIR / "course"
INDEX = COURSE_DIR / "index.html"
# Five exercises for a lesson, eight for a boss, matching web/course/levels.js.
NODE_LENGTH = {"lesson": 5, "boss": 8, "check": 1}

DEFAULT_PORT = 8000
VIDEO_FPS = 20.0
# The lesson only changes on an event, but the fingertips move every frame, so
# the same message is refreshed at this rate to keep the overlay alive.
FINGER_REFRESH_S = 1 / 15
MOCK_STEP_S = 2.0
JPEG_QUALITY = 80
# How long the shutdown waits for the camera thread to leave its loop. Longer
# than one frame, short enough that Ctrl+C still feels instant.
WORKER_JOIN_S = 2.0
# A camera that stops delivering: wait this long between reads, and give up
# after this many in a row, so a dead device cannot spin a core for ever.
READ_RETRY_S = 0.01
READ_FAILURES_ALLOWED = 200
# A sitting is one visit to the app. Every node started within this gap of the
# previous one belongs to the same session, so the course does not count a
# session per node. Longer than a lesson, shorter than an evening.
SITTING_GAP_S = 20 * 60

# What the page is told when perception is gone. These ride on the tally line of
# the state message, which the page already renders, so no new message type is
# needed for the child or the operator to learn what happened.
CAMERA_NONE = "I cannot open the camera. Check its permission, then start me again."
CAMERA_LOST = "I have lost the camera. Start me again once it is back."
ALREADY_PLAYING = "Tenfold is already open in another tab. Close it to play here."
# How long a child may struggle before Tally offers the next level of help, on
# the clock of the current exercise: a word at five seconds, the ghost finger the
# page draws from hint_level 2 at ten, the whole gesture at twenty. Each level
# fires once per exercise and stops as soon as the pose is right.
HINT_AFTER_S = (5.0, 10.0, 20.0)
# A wrong finger is how the input works, not a mistake. It only counts as a pose
# error once it is still wrong this long after Tally has named it.
POSE_GRACE_SECONDS = 4.0

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
                  hands_seen: int, reaction: str | None = None,
                  pick: Pick | None = None, hint_level: int = 0,
                  session: int = 0, node: str | None = None,
                  demo: bool = False, pose_slip: bool = False,
                  hint_auto: bool = False) -> dict[str, Any]:
    context = {"hint": update.hint, "answer": update.answer,
               "exercise": update.exercise.title}
    # A reaction from the scheduler outranks the screen state: it is the thing
    # Tally actually wants to say at this moment.
    moment = reaction or moment_of(update, hands_seen)
    return {
        "type": "state",
        "state": update.state,
        "exercise": update.exercise.title,
        "tally": PHRASE(moment, context),
        "wrong": [f.as_dict() for f in update.wrong],
        "match": [f.as_dict() for f in update.match],
        "answer": update.answer,
        "reasoning": list(update.reasoning),
        "fingers": fingers,
        "reason": pick.reason if pick else None,
        "reaction": reaction,
        "hint": dict(update.hint),
        "hint_level": hint_level,
        # True when the clock raised this hint level, false when the child asked
        # for it. Only help the child asked for costs the page's first try bonus.
        "hint_auto": hint_auto,
        # True once a wrong pose has been held past the grace and counted as a
        # pose error. The page reads it for the first try bonus: only the server
        # has the clock the rule needs.
        "pose_slip": pose_slip,
        "fact": pick.fact if pick else None,
        "session": session,
        # Which course node this belongs to. The page drops anything that is not
        # the node it started, so a message left over from before can never be
        # rendered in the middle of a lesson.
        "node": node,
        # Set when the server runs --demo, so the page can seed the showcase
        # progress once and the stage demo is reproducible from one command.
        "demo": demo,
    }


# --- shared state between the camera thread and the event loop --------------


class Hub:
    """One writer thread, many browser readers. The loop is the only mutator."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue] = set()
        self._message: dict[str, Any] = {"type": "state", "state": "intro",
                                         "exercise": "", "tally": PHRASE("intro"),
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
        """Called from the camera thread.

        On Ctrl+C the loop closes while the thread is still inside a frame, so
        the last publish of the run can land on a loop that is already gone.
        That is a shutdown, not an error, and it must not print a traceback.
        """
        if self._loop is None:
            self._message = message
            return
        if self._loop.is_closed():
            return
        try:
            self._loop.call_soon_threadsafe(self._publish, message)
        except RuntimeError:
            pass                       # the loop closed between the check and here

    def _publish(self, message: dict[str, Any]) -> None:
        self._message = message
        for queue in list(self._subscribers):
            # A stalled browser never blocks the camera, but only a state refresh
            # may be dropped: node_end and session_end are the page's only finish
            # screen, and losing one leaves the child on the last question.
            if message.get("type") == "state" and queue.qsize() > 8:
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
    """The engine, the scheduler and the lock that keeps the threads apart.

    The camera thread calls observe many times a second; the browser calls
    command from the event loop. Every mutation of the engine or the scheduler
    goes through the one lock here.
    """

    def __init__(self, engine: Engine, hub: Hub,
                 make_scheduler: Callable[[bool], Scheduler]) -> None:
        self.engine = engine
        self.hub = hub
        self.make_scheduler = make_scheduler
        self.scheduler = make_scheduler(False)
        self.learner = self.scheduler.state
        self.demo_played = False
        self._lock = threading.Lock()
        self._fingers: list[dict[str, Any]] = []
        self._hands_seen = 0
        self._last_push = 0.0
        self.trace: Callable[[str, dict[str, Any]], None] = lambda event, payload: None

        self.pick: Pick | None = None
        self.started_at = 0.0
        self.hint_level = 0
        # Whether the clock raised that level, rather than the child asking.
        self.hint_auto = False
        self.pose_error = False
        self.math_error = False
        # When the current wrong pose was first named, and which hand it was.
        self.wrong_since: float | None = None
        self.wrong_hand: str | None = None
        self.said_cannot_see = False
        self.unknown_since: float | None = None
        self.recorded = False
        self.finished = False
        self.node: dict[str, Any] | None = None
        self.correct = 0
        self.demo_available = False
        # Set once perception is gone. It rides on every message from then on,
        # so the page says what happened instead of looking like it is loading.
        self.fault: str | None = None
        # Which client owns the running node. A second tab may not take the
        # session from under it; a socket that goes away gives it back.
        self.owner: object | None = None
        # The session number of this sitting, and whose it is. A course node is
        # not a session: every node of one visit shares the number.
        self.sitting: int | None = None
        self.sitting_learner: str | None = None
        self.sitting_at: datetime | None = None
        # Nothing is scored, recorded or advanced until somebody starts a
        # session, so the mock loop cannot write into the learner record while
        # the child is still looking at the course map.
        self.running = False

    # -- pushing --

    def push(self, update: Update, reaction: str | None = None) -> None:
        message = build_message(
            update, self._fingers, self._hands_seen, reaction=reaction,
            pick=self.pick, hint_level=self.hint_level,
            session=self.scheduler.session,
            node=(self.node or {}).get("id"), demo=self.demo_available,
            pose_slip=self.pose_error, hint_auto=self.hint_auto)
        if self.fault:
            message["tally"] = self.fault
        self.hub.publish(message)
        self._last_push = time.monotonic()

    def fail(self, text: str) -> None:
        """Perception is gone for good. Say so, now and on every later message.

        Called from the camera thread. The page renders the tally line of a
        state message, so a failure that used to be a traceback on stderr and a
        page stuck on "Getting ready" is now a sentence the child can read.
        """
        with self._lock:
            self.fault = text
            self.push(self.engine.snapshot())

    def start(self) -> None:
        """Ready, but idle.

        No session begins here. The course shell starts a node, and a bare client
        says hello; either way the session is scoped by whoever asked for it.
        Starting an open session at boot used to write its outcomes into the
        learner record, so every fact it touched then came back as a due review
        inside the first node the child opened.
        """
        with self._lock:
            self.engine.start()
            self.hub.publish(build_message(
                self.engine.snapshot(), [], 0, session=0, demo=self.demo_available))

    def _advance(self) -> None:
        """Load the next exercise, or end the session. Call with the lock held."""
        # A course node fixes its length, and the page scores the child out of
        # that length. The live scheduler stops there on its own; the scripted
        # demo plays its file to the end, so the count is enforced here for both.
        target = self.scheduler.target
        if target is not None and len(self.scheduler.outcomes) >= target:
            self._end_session()
            return
        pick = self.scheduler.next_exercise(now_utc())
        if pick is None:
            self._end_session()
            return
        self.pick = pick
        self.hint_level = 0
        self.hint_auto = False
        self.pose_error = False
        self.math_error = False
        self.wrong_since = None
        self.wrong_hand = None
        self.said_cannot_see = False
        self.unknown_since = None
        self.recorded = False
        self.started_at = time.monotonic()
        update = self.engine.load(Exercise(pick.left, pick.right))
        self.trace("exercise", pick.to_dict())
        self.push(update, reaction=pick.reason)

    def _end_session(self) -> None:
        if self.finished:
            return
        self.finished = True
        summary = self.scheduler.end_session(now_utc())
        metrics = self.scheduler.metrics()
        self.trace("session_end", {**metrics, **summary.to_dict()})
        self.hub.publish({
            "type": "node_end" if self.node else "session_end",
            "node_id": (self.node or {}).get("id"),
            "correct": self.correct,
            "total": len(self.scheduler.outcomes),
            "state": self.scheduler.state.to_dict(),
            "metrics": metrics,
            "summary": summary.to_dict(),
            "tally": PHRASE(summary.reason,
                                  {"tomorrow": _fact_title(summary.tomorrow)}),
        })
        self.node = None
        self.pick = None
        self.running = False
        self.owner = None
        self.sitting_at = now_utc()
        # The stage demo is rehearsed, so its sequence has to be available again
        # on the next node rather than once per server process.
        self.demo_played = False

    # -- from the camera thread --

    def observe(self, gesture: GestureState, fingers: list[dict[str, Any]],
                hands_seen: int, now: float) -> None:
        with self._lock:
            self._fingers = fingers
            self._hands_seen = hands_seen
            if self.finished or not self.running:
                return
            update = self.engine.observe(gesture, now)
            reaction = self._reaction(gesture, update, now)
            if update is not None:
                self._watch_pose(update, now)
            slipped = self._count_pose_slip(now)
            if update is not None:
                self.push(update, reaction=reaction)
            elif reaction is not None:
                self.push(self.engine.snapshot(), reaction=reaction)
            elif slipped or now - self._last_push >= FINGER_REFRESH_S:
                self.push(self.engine.snapshot())

    def _watch_pose(self, update: Update, now: float) -> None:
        """Start or clear the grace clock on the pose the engine just reported."""
        if update.state == "wrong_pose":
            if self.wrong_since is None:
                self.wrong_since = now      # the moment Tally named the finger
            self.wrong_hand = update.event or self.wrong_hand
        else:
            # Fixed, hands dropped, or the pose is right: nothing is being held.
            self.wrong_since = None

    def _count_pose_slip(self, now: float) -> bool:
        """Count the pose error once the wrong pose outlives the grace.

        Bringing the hands up puts a wrong finger in front of the camera on
        nearly every exercise. Counting that as a mistake made a perfect lesson
        read as a failed one, so only a pose still wrong POSE_GRACE_SECONDS after
        the correction was said is a slip. Returns True on the frame it turns.
        """
        if self.pose_error or self.wrong_since is None:
            return False
        if now - self.wrong_since < POSE_GRACE_SECONDS:
            return False
        self.pose_error = True
        return True

    def _reaction(self, gesture: GestureState, update: Update | None,
                  now: float) -> str | None:
        """Anything Tally noticed that the engine does not model itself."""
        if self.pick is None or self.engine.latched:
            return None

        # Hands not readable for more than two seconds. Not an error: the child
        # may simply be out of the light, and being told off for that is unfair.
        if gesture.method == "unknown":
            self.unknown_since = self.unknown_since or now
            if not self.said_cannot_see and now - self.unknown_since >= CANNOT_SEE_AFTER_S:
                self.said_cannot_see = True
                return REACTION_CANNOT_SEE
            return None
        self.unknown_since = None

        same_hand = pose_reaction(gesture.left, gesture.right, self.pick)
        if same_hand and update is not None:
            return same_hand

        elapsed = now - self.started_at
        level = sum(1 for threshold in HINT_AFTER_S if elapsed >= threshold)
        if level > self.hint_level:
            self.hint_level = level
            self.hint_auto = True       # the clock raised it, not the child
            return hint_event(level)
        return None

    # -- from the browser --

    def command(self, message: dict[str, Any], client: object | None = None) -> None:
        """One command from one client. client identifies the socket it came from."""
        kind = message.get("type")
        with self._lock:
            if kind == "start_node":
                self._start_node(message.get("node") or {}, message.get("state"), client)
            elif kind == "quit":
                self._quit()
            elif kind == "hello":
                self._hello(message.get("state"))
            elif kind == "check":
                self._check(_as_int(message.get("value")))
            elif kind == "next":
                self._skip()
            elif kind == "hint":
                self._hint_asked()
            elif kind == "repeat":
                self.push(self.engine.repeat())

    def _hint_asked(self) -> None:
        """The child asked for the next level of help.

        Help the child asked for is the only help that costs the first try bonus
        on the page, so the level rises with hint_auto false under it.
        """
        if not self.running or self.pick is None:
            return
        if self.hint_level >= len(HINT_AFTER_S):
            return
        self.hint_level += 1
        self.hint_auto = False
        self.push(self.engine.snapshot(), reaction=hint_event(self.hint_level))

    def _skip(self) -> None:
        """The n key. A skipped exercise still counts and still comes back.

        Without this a session could never end, because nothing would be
        recorded, and the same unopened fact would be offered forever. It is not
        counted as a failure either: mastery only moves on an actual pose or
        math error, so skipping costs the child nothing.
        """
        if not self.running:
            return
        if self.pick is not None and not self.recorded:
            self._record(correct=False, given=None)
        self._advance()

    def _start_node(self, node: dict[str, Any], raw: Any,
                    client: object | None = None) -> None:
        """Begin a course node: its pairs scope the session, its kind its length."""
        if self.running and self.owner is not None and client is not self.owner:
            # Another tab is in the middle of a lesson. Taking the engine from
            # under it would freeze it with no message, so say no, out loud.
            self._refuse(node)
            return
        pairs = [list(pair) for pair in node.get("pairs") or []]
        length = int(node.get("count") or NODE_LENGTH.get(node.get("kind", "lesson"), 5))
        # A missing state is a browser that has never played, and
        # LearnerState.from_dict(None) is a fresh learner: without this the
        # previous child's record would shape this child's first node.
        self.learner = LearnerState.from_dict(raw, now=now_utc())

        # The start check is one exercise on 6x6; it never plays the demo script.
        self.scheduler = self._new_scheduler(scripted_ok=node.get("kind") != "check")

        self.node = dict(node)
        self.owner = client
        self.correct = 0
        self.finished = False
        self.running = True
        now = now_utc()
        self._hold_sitting(now)
        started = self.scheduler.start_session(now, pairs=pairs, length=length)
        self.sitting = self.scheduler.session
        self.sitting_learner = self.learner.learner_id
        self.sitting_at = now
        started["node"] = node.get("id")
        self.trace("node_start", started)
        self._advance()

    def _hold_sitting(self, now: datetime) -> None:
        """Keep every node of one visit inside one session.

        Scheduler.start_session counts a session, and the course shell starts one
        per node, so four minutes of play used to read as four sessions: mastery
        rose four times, the spacing of a mastery 1 fact meant thirty seconds and
        the personal difficulty order arrived after three nodes. A session is a
        sitting, so the count is rewound before the second and later nodes of the
        same visit, by the same learner, start their session.
        """
        if self.sitting is None or self.sitting_at is None:
            return
        if self.sitting_learner != self.learner.learner_id:
            return
        if (now - self.sitting_at).total_seconds() > SITTING_GAP_S:
            return
        self.learner.sessions = max(0, self.sitting - 1)

    def _refuse(self, node: dict[str, Any]) -> None:
        """Tell a second tab, on the node it asked for, that it cannot have it."""
        message = build_message(
            self.engine.snapshot(), [], 0, session=self.scheduler.session,
            node=node.get("id"), demo=self.demo_available)
        message["tally"] = ALREADY_PLAYING
        self.hub.publish(message)

    def release(self, client: object) -> None:
        """A socket went away. A reload can then start its node cleanly."""
        with self._lock:
            if self.owner is client:
                self.owner = None

    def _new_scheduler(self, scripted_ok: bool = True) -> Scheduler:
        """A fresh session on the same learner. The demo plays its fixed
        sequence once, on the first session of the run that may take it."""
        scripted = scripted_ok and self.demo_available and not self.demo_played
        self.demo_played = self.demo_played or scripted
        scheduler = self.make_scheduler(scripted)
        scheduler.state = self.learner
        return scheduler

    def _quit(self) -> None:
        """The child left the lesson. Nothing is recorded, nothing is scored."""
        self.node = None
        self.pick = None
        self.finished = True
        self.running = False
        self.owner = None
        self.sitting_at = now_utc()
        self.demo_played = False

    def _hello(self, raw: Any) -> None:
        """A client with no course shell: open session, no node, no length."""
        self.learner = LearnerState.from_dict(raw, now=now_utc())
        # Whether a session is running, not whether one ever ran: outcomes are
        # never emptied, so testing them refused every hello after the first.
        if self.node is not None or self.running:
            return                      # a node is already running, keep it
        self.scheduler = self._new_scheduler()
        self.finished = False
        self.running = True
        self.trace("session_start", self.scheduler.start_session(now_utc()))
        self._advance()

    def _check(self, value: int | None) -> None:
        if not self.running:
            return
        update = self.engine.check(value)
        if update is None or self.pick is None:
            return
        correct = update.state == "answer_correct"
        # What counts for the node stars is the answer, first try. Fixing a
        # finger on the way is how the input works, not a mistake: a child who
        # corrects their hand and then answers right has earned the point.
        # The pose error still lands on pose mastery through the outcome below.
        if correct and not self.math_error:
            self.correct += 1
        reaction = None if correct else answer_reaction(self.pick.result, value)
        if not correct:
            self.math_error = True
        self.push(update, reaction=reaction)
        if not correct:
            return
        self._record(correct=True, given=value)
        self._advance()

    def _record(self, correct: bool, given: int | None) -> None:
        if self.pick is None or self.recorded:
            return
        self.recorded = True
        # A finger fixed on the way is how the input works, so it is not a failed
        # exercise: pose_error only holds a pose the child kept wrong past the
        # grace, and that one does cost the exercise. wrong_hand carries the
        # engine's own event, which is the only thing that can say which hand.
        self.scheduler.record(Outcome(
            fact=self.pick.fact, left=self.pick.left, right=self.pick.right,
            correct=correct and not self.math_error and not self.pose_error,
            response_time=round(time.monotonic() - self.started_at, 2),
            hint_level=self.hint_level, pose_error=self.pose_error,
            math_error=self.math_error, given=given, wrong_hand=self.wrong_hand,
        ), now=now_utc())


def _fact_title(fact: str | None) -> str | None:
    if not fact:
        return None
    a, b = fact.split("x")
    return f"{a} x {b}"


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
    """Camera, landmarks, normalize, classifier, engine. One frame at a time.

    Every step is inside the one try, including loading the classifier and
    building the detector: those two raise on a rules file that will not import
    and on a model download that cannot reach the network, and they used to kill
    this thread before the camera was ever released, leaving the webcam held,
    the server up and the page loading for ever.
    """
    camera = None
    detector = None
    try:
        import cv2

        from app.camera import CameraError, open_camera
        from app.landmarks import HandDetector
        from app.normalize import Normalizer
        from classifier.loader import load_classifier

        try:
            camera, _ = open_camera(camera_index)
        except CameraError as error:
            # The likeliest failure of the whole demo: another app holds the
            # webcam, or the permission was never granted. Say it on the page,
            # then keep the screen alive on the mock loop rather than serve a
            # page that looks like it is still loading.
            log.error("server: %s", error)
            lesson.fail(CAMERA_NONE)
            mock_loop(lesson, stop)
            return

        classify = load_classifier()
        log.info("server: classifier from %s", getattr(classify, "rules_path", "?"))
        detector = HandDetector(num_hands=2)
        normalizer = Normalizer()
        failures = 0
        while not stop.is_set():
            ok, frame = camera.read()
            if not ok:
                # A camera that stops delivering must not spin a core: wait, and
                # give up once it is clear the device is gone for good.
                failures += 1
                if failures >= READ_FAILURES_ALLOWED:
                    raise CameraError("the camera stopped delivering frames")
                time.sleep(READ_RETRY_S)
                continue
            failures = 0
            frame = cv2.flip(frame, 1)
            lesson.hub.set_frame(encode_jpeg(frame))
            window = normalizer.update(detector.detect(frame))
            fingers = fingers_from_window(window)
            hands_seen = len({f["hand"] for f in fingers})
            lesson.observe(classify(window), fingers, hands_seen, time.monotonic())
    except Exception:
        log.exception("server: the camera thread stopped")
        lesson.fail(CAMERA_LOST)
    finally:
        stop.set()
        if detector is not None:
            detector.close()
        if camera is not None:
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
            # Never inside the start check: it is one exercise that waits for the
            # child's answer, and skipping it would end the check on its own.
            in_check = (lesson.node or {}).get("kind") == "check"
            if phase == 0 and step >= 0 and not in_check:
                lesson.command({"type": "next"})
            step = slot
        gesture = mock_gestures(lesson.engine.exercise)[phase]
        hands = 0 if gesture.method == "unknown" else 2
        fingers = mock_fingers(slot) if hands else []
        lesson.observe(gesture, fingers, hands, now)
        time.sleep(1 / 30)


# --- tally's voice -----------------------------------------------------------

# What Tally says. Tally's fixed phrases by default, so tests and keyless runs never touch the network.
PHRASE: Callable[..., str] = tally.phrase


def enable_tutor() -> None:
    """Give Tally W&B Inference lines (lesson/tutor.py) when WANDB_API_KEY is set. Tutor.phrase has the signature
    of tally.phrase and never blocks: it returns the fixed phrase at once and shows the model's line on the next
    update of the same moment, each call traced in Weave as tutor.call."""
    global PHRASE
    if not os.environ.get("WANDB_API_KEY"):
        return
    from lesson.tutor import Tutor
    PHRASE = Tutor(fallback=tally.phrase).phrase
    log.info("server: Tally speaks through W&B Inference")


# --- weave -----------------------------------------------------------------


def make_tracer() -> Callable[[str, dict[str, Any]], None]:
    """Trace lesson events only, and only with a key. Never one op per frame."""
    if not os.environ.get("WANDB_API_KEY"):
        return lambda event, payload: None
    try:
        import weave
    except ImportError:
        log.warning("server: WANDB_API_KEY is set but weave is not installed")
        return lambda event, payload: None

    project = "tenfold" + os.environ.get("TENFOLD_PROJECT_SUFFIX", "")
    entity = os.environ.get("WANDB_ENTITY")
    weave.init(f"{entity}/{project}" if entity else project)

    @weave.op
    def lesson_event(event: str, payload: dict[str, Any]) -> dict:
        return {"event": event, **payload}

    def trace(event: str, payload: dict[str, Any]) -> None:
        try:
            lesson_event(event, payload)
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

    def course_file(path: Path) -> Any:
        async def handler(request: web.Request) -> web.StreamResponse:
            return web.FileResponse(path, headers={"Cache-Control": "no-store"})
        return handler

    async def video(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={
            "Content-Type": "multipart/x-mixed-replace; boundary=frame",
            "Cache-Control": "no-store",
        })
        await response.prepare(request)
        try:
            while not request.transport or not request.transport.is_closing():
                jpeg = hub.frame()
                if jpeg:
                    await response.write(
                        b"--frame\r\nContent-Type: image/jpeg\r\n"
                        b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                        + jpeg + b"\r\n"
                    )
                await asyncio.sleep(1 / VIDEO_FPS)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Any way a viewer can go away has to end this loop. A stream that
            # keeps writing to a closed socket piles up until the server stops
            # answering new requests, which looks like the page hanging.
            pass
        return response

    async def websocket(request: web.Request) -> web.StreamResponse:
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)

        async def pump() -> None:
            while not ws.closed:
                message = await queue.get()
                if ws.closed:
                    return
                try:
                    await ws.send_json(message)
                except (ConnectionResetError, RuntimeError):
                    return          # the peer went away between publish and send

        # Everything the teardown has to undo is created inside the try: a
        # socket that dies on the very first send used to leave its queue in the
        # hub for the life of the process.
        queue = hub.subscribe()
        pumping: asyncio.Task | None = None
        try:
            await ws.send_json(hub.message)
            pumping = asyncio.create_task(pump())
            async for raw in ws:
                if raw.type is not WSMsgType.TEXT:
                    continue
                try:
                    lesson.command(json.loads(raw.data), client=ws)
                except json.JSONDecodeError:
                    log.warning("server: ignored a malformed command")
                except Exception:
                    # A bad command must never take the socket down with it: the
                    # child would be left looking at a frozen lesson.
                    log.exception("server: command failed")
        finally:
            if pumping is not None:
                pumping.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pumping
            hub.unsubscribe(queue)
            lesson.release(ws)
        return ws

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/video", video)
    app.router.add_get("/ws", websocket)
    # The course shell asks for styles.css, levels.js and app.js next to the
    # page, so each file is routed by name. Naming them one by one rather than
    # serving the directory keeps path traversal off the table entirely.
    if COURSE_DIR.exists():
        for item in sorted(COURSE_DIR.iterdir()):
            if item.is_file() and item.name != "index.html":
                app.router.add_get(f"/{item.name}", course_file(item))
    app[LESSON_KEY] = lesson
    return app


def make_scheduler(demo: bool = False) -> Scheduler:
    """The real scheduler, or the fixed demo sequence when asked for one."""
    if not demo:
        return Scheduler(now=now_utc())
    path = REPO_ROOT / DEFAULT_SCENARIO
    try:
        return ScriptedScheduler.load(path, now=now_utc())
    except (OSError, ValueError, TypeError, KeyError) as error:
        # Missing, unparsable, or a step short of a field. A scenario edited one
        # character wrong on stage must cost the demo its script, not its lesson.
        log.warning("server: %s is unusable (%s), falling back to the live scheduler",
                    path, error)
        return Scheduler(now=now_utc())


def create_app(mock: bool = False, camera: int | None = None,
               demo: bool = False) -> web.Application:
    """Wire the hub, the lesson and the worker thread into one aiohttp app.

    Split out of main so the tests can drive the whole thing in mock mode without
    a camera, a browser or a port.
    """
    hub = Hub()
    lesson = Lesson(Engine(), hub, make_scheduler)
    lesson.demo_available = demo
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
        # Let the worker leave its loop before the loop it publishes into is
        # closed. It never waits on the event loop, so joining here cannot
        # deadlock, and the timeout keeps Ctrl+C instant either way.
        if worker.is_alive():
            worker.join(WORKER_JOIN_S)

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
    parser.add_argument("--demo", action="store_true",
                        help=f"run the fixed sequence in {DEFAULT_SCENARIO}")
    parser.add_argument("--no-open", action="store_true", help="do not open the browser")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    load_env()
    enable_tutor()

    app = create_app(mock=args.mock, camera=args.camera, demo=args.demo)

    url = f"http://localhost:{args.port}"
    labels = [name for name, on in (("mock", args.mock), ("demo", args.demo)) if on]
    print(f"tenfold: {url}" + (f"  ({', '.join(labels)})" if labels else ""))
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
