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

Messages to the browser: one type, "state", carrying

    type, state, exercise, tally, wrong: [{hand, number}],
    match: [{hand, number}], answer, reasoning: [str],
    fingers: [{hand, number, x, y}], reason, reaction,
    hint: {hand, move_from, move_to}, hint_level, hint_auto, pose_slip,
    fact, session, node, demo,
    tutor_state, intervention_level, tutor_line, tutor_visual,
    scored_gesture_error, scored_math_error, first_try, mode

The last eight are the tutor's, docs/tutor_contract.md section 1.2. Absent
means false or null, so a page can talk to an older server without a special
case, but this server always sends all eight. pose_slip is the old name of
scored_gesture_error and rides along until the page has stopped reading it.

x and y are in 0..1 in the mirrored image, the same frame the MJPEG stream
shows, so the overlay lines up without the page knowing anything about cameras.

Messages from the browser: {"type": "check", "value": 56}, {"type": "next"},
{"type": "hint"} when the child asks for help, {"type": "repeat"},
{"type": "quit"}, {"type": "start_node", "node": {...}, "state": {...}},
{"type": "tts", "speaking": true} and {"type": "tts", "speaking": false}
around every line the page speaks, and {"type": "speech", "text": "..."} for
anything the child says, which never submits an answer and is never stored.
{"type": "hello", "state": {...}} hands over the learner state the page kept
in localStorage. Without a hello the server starts a fresh learner in memory,
so the lesson still runs; nothing is persisted server side, on purpose, since
the child's record belongs in the child's browser.

At the end of a session the server sends
{"type": "session_end", "state", "metrics", "summary"}, with the tutor's per
learner factors written onto that state under "tutor", and the page stores the
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
import hashlib
import inspect
import json
import logging
import math
import os
import sys
import threading
import time
import webbrowser
from collections import deque
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiohttp import WSMsgType, web  # noqa: E402

from app.live_samples import (  # noqa: E402
    LIVE_SAMPLES_PATH,
    LiveSampleWriter,
    windows_from_params,
)
from classifier import features  # noqa: E402
from classifier.schema import FINGER_NUMBERS, GestureState, Window  # noqa: E402
from lesson import tally  # noqa: E402
from lesson.engine import (  # noqa: E402
    EVENT_WRONG_LEFT,
    EVENT_WRONG_RIGHT,
    Engine,
    Exercise,
    Update,
)
from lesson.scheduler import (  # noqa: E402
    CANNOT_SEE_AFTER_S,
    DEFAULT_SCENARIO,
    MASTERED_FROM,
    REACTION_CANNOT_SEE,
    REACTION_CONFIDENCE,
    LearnerState,
    Outcome,
    Pick,
    Scheduler,
    ScriptedScheduler,
    answer_reaction,
    fact_order,
    factors_of,
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
# The design images, which the page asks for as art/<name>.png. They arrive
# while the app is running, so this directory is read per request.
ART_DIR = COURSE_DIR / "art"
# Five exercises for a lesson, eight for a boss, matching web/course/levels.js.
NODE_LENGTH = {"lesson": 5, "boss": 8, "check": 1}

# lesson/tutor_params.json, read here for one key, live_sample_windows: how many
# perception windows one live sample event is worth. app/tutor.py reads the same
# file for its own timings and owns every other key in it.
TUTOR_PARAMS_PATH = REPO_ROOT / "lesson" / "tutor_params.json"
# How many hex characters of the learner hash a live sample row carries. Twelve
# is what lesson/scheduler.py already gives a learner id, so the two read alike.
LEARNER_HASH_LENGTH = 12

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
# error once it is still wrong this long after Tally has named it. The tutor
# scores this itself, from wrong_pose_error_after_help in its params file; this
# is the clock the server keeps for a run with no app/tutor.py to ask.
POSE_GRACE_SECONDS = 4.0

# The eight fields app/tutor.py decides, and what the page is told before there
# is a tutor to decide them. Absent means false or null, contract section 1.2.
TUTOR_FIELDS: dict[str, Any] = {
    "tutor_state": "WORKING",
    "intervention_level": 0,
    "tutor_line": None,
    "tutor_visual": None,
    "scored_gesture_error": False,
    "scored_math_error": False,
    "first_try": False,
    "mode": "normal",
}
# The three per learner factors that ride on the learner record under "tutor",
# contract section 2.4. The server carries them, the tutor owns their values.
DEFAULT_FACTORS: dict[str, Any] = {"pace_factor": 1.0, "help_factor": 1.0,
                                   "tutor_observations": 0}
# Every name this file uses out of app/tutor.py, in one place: the two files
# are written against the same contract at the same time, so aligning them is
# meant to be one edit here and not a hunt through the file.
TUTOR_NAMES = {
    # building one
    "class": "Tutor",
    "params": "load_params",
    "lines": "load_lines",
    "log": "jsonl_sink",
    "observation": "Observation",
    # the shape of the session
    "exercise": "new_exercise",
    "end": "end_exercise",
    "check_start": "start_check",
    "check_end": "end_check",
    # what the tutor is told
    "observe": "observe",
    "tts_start": "tts_start",
    "tts_end": "tts_end",
    "speech": "speech",
    "hint": "hint_requested",
    "answer": "answer",
    # what the tutor decides
    "decision": "state_fields",
    "factors": "learner_factors",
    "early_stop": "early_stop",
}
# L4 on the ladder of contract section 4.2: the rescue, which ends first try.
RESCUE_LEVEL = 4
# Anything the child says is trimmed to this and then forgotten.
SPEECH_LIMIT = 200
# How far back the motion measure looks.
MOTION_WINDOW_S = 0.4
# The two engine events that mean a finger number is actually wrong. no_contact
# and hands_swapped mean both numbers are right, so neither can ever score.
WRONG_FINGER_EVENTS = (EVENT_WRONG_LEFT, EVENT_WRONG_RIGHT)

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
                  hint_auto: bool = False,
                  tutor: dict[str, Any] | None = None) -> dict[str, Any]:
    context = {"hint": update.hint, "answer": update.answer,
               "exercise": update.exercise.title}
    # A reaction from the scheduler outranks the screen state: it is the thing
    # Tally actually wants to say at this moment.
    moment = reaction or moment_of(update, hands_seen)
    message = {
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
    # The tutor's eight, last and always all of them: the page reads a missing
    # one as its default, and the eight defaults are the shape of no tutor.
    message.update(TUTOR_FIELDS)
    message.update({name: value for name, value in (tutor or {}).items()
                    if name in TUTOR_FIELDS})
    return message


# --- how much the hands are moving ------------------------------------------


def palm_size(window: Window) -> float | None:
    """Wrist to middle MCP in image coordinates, both hands averaged.

    The one length in the frame that scales with how close the child is sitting,
    so a measure divided by it says the same thing near and far.
    """
    scales = [frames[-1].scale for frames in (window.left, window.right)
              if frames and frames[-1].present and frames[-1].scale]
    return sum(scales) / len(scales) if scales else None


def _displacement(before: tuple[float, dict, float],
                  after: tuple[float, dict, float]) -> float:
    """The median fingertip move between two observations, in palm widths."""
    shared = set(before[1]) & set(after[1])
    if not shared:
        return 0.0
    scale = (before[2] + after[2]) / 2 or 1.0
    moves = [math.hypot(after[1][key][0] - before[1][key][0],
                        after[1][key][1] - before[1][key][1]) / scale
             for key in shared]
    return round(median(moves), 4)


class MotionMeter:
    """How much the fingertips moved, off the landmarks the page already gets.

    The median fingertip displacement over the last MOTION_WINDOW_S, in palm
    widths, so that a child leaning towards the camera does not read as a child
    fidgeting. It is what the tutor needs to hold invariant 1 of the contract,
    movement means Tally is silent, and it is also what the tutor collects its
    idle jitter from during the camera check: the threshold is built there,
    inside app/tutor.py, out of the numbers this meter hands over.
    """

    def __init__(self, window_s: float = MOTION_WINDOW_S) -> None:
        self.window_s = window_s
        self._samples: deque[tuple[float, dict[tuple[str, int], tuple[float, float]],
                                   float]] = deque()

    def update(self, fingers: list[dict[str, Any]], palm: float | None,
               now: float) -> float:
        """One observation in, the motion measure out."""
        points = {(str(f["hand"]), int(f["number"])): (float(f["x"]), float(f["y"]))
                  for f in fingers if f.get("hand") and f.get("number") is not None}
        sample = (now, points, float(palm) if palm and palm > 0 else 1.0)
        self._samples.append(sample)
        while len(self._samples) > 1 and now - self._samples[1][0] >= self.window_s:
            self._samples.popleft()
        return _displacement(self._samples[0], sample)


# --- the seam to app/tutor.py ------------------------------------------------


_TUTOR_FAULTS: set[str] = set()


def tutor_module() -> Any:
    """app/tutor.py, or None while it is not there or will not import."""
    try:
        from app import tutor as module
    except Exception:               # not written, or not importable: carry on
        log.warning("server: app/tutor.py is not available, no tutor this run")
        return None
    return module


def build_tutor(module: Any, learner_id: str, factors: dict[str, Any],
                keep_log: bool) -> Any:
    """One tutor, with its params, its lines and its log. None if anything is off.

    A params file that will not load is a startup error inside app/tutor.py, and
    it must not be one here: a lesson that refuses to run because of a number in
    a JSON file is worse on stage than a lesson with no tutor in it.
    """
    if module is None:
        return None
    entry = getattr(module, TUTOR_NAMES["class"], None)
    params = call_tutor(getattr(module, TUTOR_NAMES["params"], None))
    lines = call_tutor(getattr(module, TUTOR_NAMES["lines"], None))
    if entry is None or params is None or lines is None:
        log.warning("server: app/tutor.py did not load, no tutor this run")
        return None
    # The log is a record of children in front of a camera, contract section 3.
    # A mock run is not a child, so it never writes a line.
    sink = call_tutor(getattr(module, TUTOR_NAMES["log"], None)) if keep_log else None
    return call_tutor(entry, params=params, lines=lines, clock=time.monotonic,
                      log=sink, learner_id=learner_id, factors=dict(factors))


def call_tutor(target: Any, **kwargs: Any) -> Any:
    """Call into app/tutor.py with the arguments it declares, and never raise.

    The contract fixes the fields on the wire, not the Python signatures, and
    the two files are written at the same time. So everything is passed by name,
    only the names the callable declares are passed, and anything that goes
    wrong costs the lesson nothing: a tutor that is missing a method, or that
    raises, leaves the child playing on the defaults.
    """
    if target is None:
        return None
    try:
        parameters = inspect.signature(target).parameters
    except (TypeError, ValueError):
        parameters = {}
    if not any(p.kind is p.VAR_KEYWORD for p in parameters.values()):
        kwargs = {name: value for name, value in kwargs.items() if name in parameters}
    try:
        return target(**kwargs)
    except Exception:
        name = getattr(target, "__name__", "tutor")
        if name not in _TUTOR_FAULTS:
            _TUTOR_FAULTS.add(name)
            log.exception("server: app/tutor.py %s raised, carrying on without it", name)
        return None


def learner_factors(raw: Any) -> dict[str, Any]:
    """The three per learner factors, whatever the learner record carried.

    The adaptation schedule and the smoothing of contract section 2.4 are the
    tutor's; the server only carries the numbers to it and back.
    """
    factors = dict(DEFAULT_FACTORS)
    if isinstance(raw, Mapping):
        for name in DEFAULT_FACTORS:
            value = raw.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                factors[name] = value
    factors["pace_factor"] = float(factors["pace_factor"])
    factors["help_factor"] = float(factors["help_factor"])
    factors["tutor_observations"] = int(factors["tutor_observations"])
    return factors


def _value(attribute: Any) -> Any:
    """An attribute that may be a plain value or a method to call for one."""
    return call_tutor(attribute) if callable(attribute) else attribute


def _decided(payload: Any) -> dict[str, Any] | None:
    """The eight fields off whatever the tutor returned, mapping or object."""
    if payload is None or isinstance(payload, (str, bytes, int, float, bool)):
        return None
    if isinstance(payload, Mapping):
        found = {name: payload[name] for name in TUTOR_FIELDS if name in payload}
    else:
        found = {name: getattr(payload, name) for name in TUTOR_FIELDS
                 if hasattr(payload, name)}
    return found or None


class TutorLink:
    """The one seam between this file and app/tutor.py.

    app/tutor.py owns the states, the ladder, the timers, the scoring and the
    log; this file feeds it and carries what it decides. Every call goes through
    call_tutor and every name it uses is in TUTOR_NAMES, so a tutor that is not
    there costs nothing and realigning the two files is one edit in that table.
    """

    def __init__(self, module: Any = None, keep_log: bool = True) -> None:
        self._module = module
        self.keep_log = keep_log
        self.tutor: Any = None
        self.fields: dict[str, Any] = dict(TUTOR_FIELDS)
        self.factors: dict[str, Any] = dict(DEFAULT_FACTORS)
        self.early_stop = False

    @property
    def live(self) -> bool:
        """Whether there is a tutor deciding, or only the defaults."""
        return self.tutor is not None

    # -- the session --

    def open(self, learner_id: str, carried: Any) -> None:
        """A session starts: one tutor for this learner, with their factors."""
        self.factors = learner_factors(carried)
        self.fields = dict(TUTOR_FIELDS)
        self.early_stop = False
        if self._module is None:
            self._module = tutor_module()
        self.tutor = build_tutor(self._module, learner_id, self.factors,
                                 self.keep_log)

    def close(self, reason: str, now: float) -> None:
        """The session is over: close the exercise still open and read back.

        The tutor itself is kept until the next session replaces it, so the
        factors and the last decision are still there to be sent.
        """
        self.ended(reason, now)

    def exercise(self, title: str, pick: Pick, node: str | None, now: float) -> None:
        """A new exercise is on screen. The tutor chooses its mode here."""
        self.fields = dict(TUTOR_FIELDS)
        self._absorb(self._send("exercise", a=pick.left, b=pick.right, title=title,
                                node=node, now=now))

    def ended(self, reason: str, now: float) -> None:
        """One exercise is over: answered, skipped or quit. It writes its log line."""
        self._absorb(self._send("end", reason=reason, now=now))
        self.read_factors()

    def check_started(self) -> None:
        """The camera check: the still hands the motion threshold is measured on."""
        self._send("check_start")

    def check_ended(self) -> None:
        self._send("check_end")

    # -- what the tutor is told --

    def feed(self, verdict: GestureState, motion: float, now: float,
             fingers: list[dict[str, Any]], hands: int,
             hint: dict[str, Any] | None) -> None:
        """One perception window, every one of them, at the camera rate.

        The tutor drops what arrives faster than its own fps. Dropping here as
        well would halve the rate it actually sees, and its idle jitter needs
        twenty observations inside two seconds to mean anything.
        """
        if self.tutor is None:
            return
        observation = call_tutor(getattr(self._module, TUTOR_NAMES["observation"], None),
                                 gesture=verdict, fingers=fingers, hands_seen=hands,
                                 hint=hint, motion=motion)
        if observation is None:
            return
        self._absorb(self._send("observe", obs=observation, now=now))

    def said(self, speaking: bool, now: float) -> None:
        self._absorb(self._send("tts_start" if speaking else "tts_end", now=now))

    def heard(self, text: str, now: float) -> None:
        self._absorb(self._send("speech", text=text, now=now))

    def asked_for_help(self, now: float) -> None:
        self._absorb(self._send("hint", now=now))

    def answered(self, correct: bool, value: int | None, now: float) -> None:
        self._absorb(self._send("answer", correct=correct, value=value, now=now))

    # -- what the tutor decides --

    def read_factors(self) -> dict[str, Any]:
        """The factors as they stand, for the learner record the page stores."""
        if self.tutor is not None:
            carried = _value(getattr(self.tutor, TUTOR_NAMES["factors"], None))
            if isinstance(carried, Mapping):
                self.factors = learner_factors(carried)
        return dict(self.factors)

    def _send(self, role: str, **kwargs: Any) -> Any:
        method = getattr(self.tutor, TUTOR_NAMES[role], None)
        return call_tutor(method, **kwargs) if method is not None else None

    def _absorb(self, decided: Any) -> None:
        if self.tutor is None:
            return
        # The early stop is a decision, not a field: it never rides the wire.
        self.early_stop = self.early_stop or bool(
            _value(getattr(self.tutor, TUTOR_NAMES["early_stop"], False)))
        payload = _decided(decided)
        if payload is None:
            payload = _decided(_value(getattr(self.tutor,
                                              TUTOR_NAMES["decision"], None)))
        if payload:
            self.fields.update(payload)


# --- the live sample writer, app/live_samples.py -----------------------------


_LIVE_FAULTS: set[str] = set()


def load_tutor_params(path: Path = TUTOR_PARAMS_PATH) -> dict[str, Any]:
    """The tutor parameter file as plain JSON, or nothing at all.

    app/tutor.py validates that file and refuses to start on a bad one. Here it
    is read for a single number, so a file that is missing or unreadable is not
    an error: windows_from_params falls back to its own default and the lesson
    runs. Nothing is written back, ever.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        log.warning("server: %s could not be read, live samples use the default", path)
        return {}
    return raw if isinstance(raw, dict) else {}


def live_learner_id(learner_id: Any) -> str:
    """Who a live sample row belongs to: a hash of the child's identity.

    The identity on this device stays the first name from the welcome screen,
    trimmed and lowercased, and that is what keeps profiles, XP, tutor factors
    and the tutor log apart per child. data/live_samples.jsonl is a dataset that
    may one day reach the critic, so it carries a stable id derived from that
    name instead: the same child is the same learner on the same machine, two
    children stay two learners, and no row carries a child's first name.

    Reverting to the plain name is the one return below.
    """
    name = str(learner_id or "").strip().lower()
    if not name:
        return ""
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:LEARNER_HASH_LENGTH]


def live_failed(name: str) -> None:
    """A live sample call that raised. Logged once per call site, then ignored.

    app/live_samples.py swallows its own failures; this is the second belt, so
    that a writer which is missing a method or raises anyway costs a child in
    front of the camera nothing. Same discipline as call_tutor.
    """
    if name in _LIVE_FAULTS:
        return
    _LIVE_FAULTS.add(name)
    log.exception("server: live_samples %s raised, carrying on without it", name)


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
        # The tutor's scoring, contract section 5. These two replace the pose
        # and math error flags the server used to keep on its own, and they are
        # what Outcome.correct now follows.
        self.scored_gesture_error = False
        self.scored_math_error = False
        # The rest of the first try formula of section 5.4, computed here
        # because only the server has the clock, the params and the ladder.
        self.requested_hints = 0
        self.rescue_used = False
        self.correct_pose = False
        self.answer_correct = False
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
        # app/tutor.py, and the two things it needs that only this side can see:
        # how much the hands are moving, and the per learner factors.
        self.tutor = TutorLink()
        self.motion = MotionMeter()
        # Real windows from real lessons, app/live_samples.py. Disabled here, so
        # a Lesson built by hand records nothing; create_app hands over the one
        # that writes, and only when the camera is a real child's camera.
        self.live = LiveSampleWriter()
        # The tutor can ask for an early stop. It does not call the scheduler,
        # so the one mastered fact and the close are served from here.
        self.early_stop = False
        self.early_stop_served = False

    # -- pushing --

    def push(self, update: Update, reaction: str | None = None) -> None:
        message = build_message(
            update, self._fingers, self._hands_seen, reaction=reaction,
            pick=self.pick, hint_level=self.hint_level,
            session=self.scheduler.session,
            node=(self.node or {}).get("id"), demo=self.demo_available,
            pose_slip=self.scored_gesture_error, hint_auto=self.hint_auto,
            tutor=self.decided())
        if self.fault:
            message["tally"] = self.fault
        self.hub.publish(message)
        self._last_push = time.monotonic()

    @property
    def live_learner(self) -> str:
        """Whose live sample rows these are. The hash, never the child's name."""
        return live_learner_id(self.learner.learner_id)

    def decided(self) -> dict[str, Any]:
        """The eight tutor fields for this message.

        Five are the tutor's word. The two scored errors are latched here, since
        they feed the Outcome as well as the page, and first_try is the server's
        own arithmetic on them.
        """
        fields = dict(self.tutor.fields)
        fields["scored_gesture_error"] = self.scored_gesture_error
        fields["scored_math_error"] = self.scored_math_error
        fields["first_try"] = self.first_try()
        return fields

    def first_try(self) -> bool:
        """Contract section 5.4, on the server because the page has no clock.

        Automatic nudges and automatic hints never cost the bonus, however many
        were delivered: only help the child asked for, a scored error and a
        rescue do. first_try and a busy tutor coexisting is exactly the pair
        Loop 2 exists to reduce.
        """
        return (self.correct_pose and self.answer_correct
                and not self.scored_gesture_error and not self.scored_math_error
                and self.requested_hints == 0 and not self.rescue_used)

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
        now = time.monotonic()
        if self.pick is not None:
            self.tutor.ended(_reason_for(self.answer_correct), now)
        # A course node fixes its length, and the page scores the child out of
        # that length. The live scheduler stops there on its own; the scripted
        # demo plays its file to the end, so the count is enforced here for both.
        target = self.scheduler.target
        if target is not None and len(self.scheduler.outcomes) >= target:
            self._end_session()
            return
        if self.early_stop_served:
            self._end_session()         # the mastered fact has been played
            return
        if self.early_stop:
            pick = self._mastered_pick()
            if pick is None:
                self._end_session()
                return
            self.early_stop_served = True
            self.scheduler.ending_on_success = True
            self.scheduler.history.append(pick)
        else:
            pick = self.scheduler.next_exercise(now_utc())
        if pick is None:
            self._end_session()
            return
        self.pick = pick
        self.hint_level = 0
        self.hint_auto = False
        self.scored_gesture_error = False
        self.scored_math_error = False
        self.requested_hints = 0
        self.rescue_used = False
        self.correct_pose = False
        self.answer_correct = False
        self.wrong_since = None
        self.wrong_hand = None
        self.said_cannot_see = False
        self.unknown_since = None
        self.recorded = False
        self.started_at = now
        update = self.engine.load(Exercise(pick.left, pick.right))
        self.tutor.exercise(update.exercise.title, pick,
                            (self.node or {}).get("id"), now)
        self.trace("exercise", pick.to_dict())
        self.push(update, reaction=pick.reason)

    def _mastered_pick(self) -> Pick | None:
        """One fact the child already owns, to end an early stop on a success.

        The scheduler does the same thing when it stops a session itself, but
        the tutor's early stop is a decision from outside it, so the pick is
        made here rather than by editing lesson/scheduler.py.
        """
        allowed = self.scheduler.allowed
        for key in fact_order(self.scheduler.state):
            if allowed is not None and key not in allowed:
                continue
            record = self.scheduler.state.math.get(key)
            if record is not None and record.mastery >= MASTERED_FROM:
                left, right = factors_of(key)
                return Pick(key, left, right, REACTION_CONFIDENCE)
        return None

    def _end_session(self) -> None:
        if self.finished:
            return
        self.finished = True
        summary = self.scheduler.end_session(now_utc())
        metrics = self.scheduler.metrics()
        self.trace("session_end", {**metrics, **summary.to_dict()})
        if (self.node or {}).get("kind") == "check":
            self.tutor.check_ended()    # the measurement is fixed for the run
        self.tutor.close(_reason_for(self.answer_correct), time.monotonic())
        # LearnerState.to_dict drops the keys it does not know, so the per
        # learner tutor factors are written back on top of it here. The page
        # saves that object whole, which is how they reach the next session.
        state = self.scheduler.state.to_dict()
        state["tutor"] = self.tutor.read_factors()
        self.hub.publish({
            "type": "node_end" if self.node else "session_end",
            "node_id": (self.node or {}).get("id"),
            "correct": self.correct,
            "total": len(self.scheduler.outcomes),
            "state": state,
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
        self.early_stop = False
        self.early_stop_served = False
        # The stage demo is rehearsed, so its sequence has to be available again
        # on the next node rather than once per server process.
        self.demo_played = False

    # -- from the camera thread --

    def observe(self, gesture: GestureState, fingers: list[dict[str, Any]],
                hands_seen: int, now: float, palm: float | None = None) -> None:
        with self._lock:
            self._fingers = fingers
            self._hands_seen = hands_seen
            # The measure runs whatever the lesson is doing, because the camera
            # check carries no exercise and is where the jitter is collected.
            motion = self.motion.update(fingers, palm, now)
            if self.finished or not self.running:
                return
            update = self.engine.observe(gesture, now)
            self.correct_pose = self.correct_pose or self.engine.latched
            reaction = self._reaction(gesture, update, now)
            if update is not None:
                self._watch_pose(update, now)
            # The engine's geometric hint goes with it: it is what the tutor
            # builds its correction and its ghost out of.
            current = update if update is not None else self.engine.snapshot()
            self.tutor.feed(gesture, motion, now, fingers=fingers, hands=hands_seen,
                            hint=dict(current.hint))
            self._follow_tutor()
            scored = self._score_gesture(gesture, hands_seen, now)
            if scored and self.pick is not None:
                # A hard negative, and the only kind a lesson can label: the pose
                # the classifier read on the windows just offered, against the
                # pose the exercise asked for. A wrong pose on its own is not one
                # of these: the child is still assembling the gesture.
                try:
                    self.live.record_gesture_error(
                        self.live_learner, current.exercise.title, gesture,
                        (self.pick.left, self.pick.right))
                except Exception:
                    live_failed("record_gesture_error")
            if update is not None:
                self.push(update, reaction=reaction)
            elif reaction is not None:
                self.push(self.engine.snapshot(), reaction=reaction)
            elif scored or now - self._last_push >= FINGER_REFRESH_S:
                self.push(self.engine.snapshot())

    def _follow_tutor(self) -> None:
        """The two decisions that are not fields on the wire.

        The rescue ends first try, and the early stop has to reach the scheduler
        through this file because the tutor never calls it.
        """
        if (_as_int(self.tutor.fields.get("intervention_level")) or 0) >= RESCUE_LEVEL:
            self.rescue_used = True
        if self.tutor.early_stop:
            self.early_stop = True

    def _watch_pose(self, update: Update, now: float) -> None:
        """Start or clear the grace clock on the pose the engine just reported.

        Only a finger number that is actually wrong starts it. no_contact and
        hands_swapped mean both numbers are right and the pose is still being
        assembled, and a commutative inversion is a correct pose, so none of the
        three can ever be scored. wrong_hand still carries whatever the engine
        said, because it is the only thing that can attribute a pose error.
        """
        if update.state == "wrong_pose":
            self.wrong_hand = update.event or self.wrong_hand
            if update.event in WRONG_FINGER_EVENTS:
                if self.wrong_since is None:
                    self.wrong_since = now  # the moment Tally named the finger
                return
        # Fixed, hands dropped, or the pose is right: nothing is being held.
        self.wrong_since = None

    def _score_gesture(self, gesture: GestureState, hands_seen: int,
                       now: float) -> bool:
        """Score the gesture error once. Returns True on the frame it turns.

        Contract section 5.2: a wrong finger number, a targeted correction
        already given, the pose still wrong wrong_pose_error_after_help since
        that correction, the vision not unknown and both hands present. All of
        that is the tutor's clock. Without a tutor the server keeps its own
        grace, which starts where it always did, at the moment Tally named the
        finger, so a run with no app/tutor.py still behaves.
        """
        if self.scored_gesture_error:
            return False
        if self.tutor.live:
            if not self.tutor.fields.get("scored_gesture_error"):
                return False
        elif not self._grace_spent(gesture, hands_seen, now):
            return False
        self.scored_gesture_error = True
        return True

    def _grace_spent(self, gesture: GestureState, hands_seen: int,
                     now: float) -> bool:
        """The server's own clock, for a run with no tutor to ask."""
        if self.wrong_since is None or gesture.method == "unknown" or hands_seen < 2:
            return False
        return now - self.wrong_since >= POSE_GRACE_SECONDS

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
            elif kind == "tts":
                self._tts(bool(message.get("speaking")))
            elif kind == "speech":
                self._speech(message.get("text"))
            elif kind == "repeat":
                self.push(self.engine.repeat())

    def _tts(self, speaking: bool) -> None:
        """The page has started or finished saying a line.

        Two false in a row mean the same as one: the page sends one from onend
        and one more whenever it cancels its queue. A muted page sends the pair
        with no gap, so the tutor's clocks behave the same muted or not.
        """
        self.tutor.said(speaking, time.monotonic())

    def _speech(self, raw: Any) -> None:
        """Anything the child said, whether or not it was a number.

        It never submits an answer, and nothing is kept: the tutor is told that
        speech happened and when, which pauses its timers and proves engagement.
        """
        text = str(raw or "").strip()[:SPEECH_LIMIT]
        if not text:
            return
        self.tutor.heard(text, time.monotonic())

    def _hint_asked(self) -> None:
        """The child asked for the next level of help.

        Help the child asked for is the only help that costs first try, so it is
        counted here and the level rises with hint_auto false under it.
        """
        if not self.running or self.pick is None:
            return
        self.requested_hints += 1
        self.tutor.asked_for_help(time.monotonic())
        if self.hint_level < len(HINT_AFTER_S):
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
        self.tutor.open(self.learner.learner_id, _carried_factors(raw))

        # The start check is one exercise on 6x6; it never plays the demo script.
        self.scheduler = self._new_scheduler(scripted_ok=node.get("kind") != "check")
        # Step 1 of the check is a still hands measurement, and the only moment
        # in the app where nothing is being asked of the child.
        if node.get("kind") == "check":
            self.tutor.check_started()

        self.node = dict(node)
        self.owner = client
        self.correct = 0
        self.finished = False
        self.running = True
        self.early_stop = False
        self.early_stop_served = False
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
        self.early_stop = False
        self.early_stop_served = False
        self.tutor.close("quit", time.monotonic())
        self.demo_played = False

    def _hello(self, raw: Any) -> None:
        """A client with no course shell: open session, no node, no length."""
        self.learner = LearnerState.from_dict(raw, now=now_utc())
        # Whether a session is running, not whether one ever ran: outcomes are
        # never emptied, so testing them refused every hello after the first.
        if self.node is not None or self.running:
            return                      # a node is already running, keep it
        self.tutor.open(self.learner.learner_id, _carried_factors(raw))
        self.scheduler = self._new_scheduler()
        self.finished = False
        self.running = True
        self.early_stop = False
        self.early_stop_served = False
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
        # corrects their hand and then answers right has earned the point, and
        # so has a child who read 6 x 7 with 7 on the left. The gesture error
        # still lands on pose mastery through the outcome below.
        if correct and not self.scored_math_error:
            self.correct += 1
        reaction = None if correct else answer_reaction(self.pick.result, value)
        # Contract section 5.3: one math error per wrong answer, no cap. A
        # speech message never reaches here, so it can never score one.
        if correct:
            self.answer_correct = True
        else:
            self.scored_math_error = True
        self.tutor.answered(correct, value, time.monotonic())
        self.push(update, reaction=reaction)
        if not correct:
            return
        # A correct pose the engine latched, corroborated by a correct answer:
        # the one moment in a lesson where the windows behind it are worth
        # keeping. engine.check answers nothing without a latch, so correct_pose
        # is already true here; it is named because it is the rule.
        if self.correct_pose:
            try:
                self.live.confirm_correct(self.live_learner, update.exercise.title,
                                          (self.pick.left, self.pick.right))
            except Exception:
                live_failed("confirm_correct")
        self._record(correct=True, given=value)
        self._advance()

    def _record(self, correct: bool, given: int | None) -> None:
        if self.pick is None or self.recorded:
            return
        self.recorded = True
        # A finger fixed on the way is how the input works, so it is not a failed
        # exercise: the tutor scores a gesture error only for a pose still wrong
        # after it has said which finger to move, and that one does cost the
        # exercise. wrong_hand carries the engine's own event, which is the only
        # thing that can say which hand.
        self.scheduler.record(Outcome(
            fact=self.pick.fact, left=self.pick.left, right=self.pick.right,
            correct=(correct and not self.scored_math_error
                     and not self.scored_gesture_error),
            response_time=round(time.monotonic() - self.started_at, 2),
            hint_level=self.hint_level, pose_error=self.scored_gesture_error,
            math_error=self.scored_math_error, given=given,
            wrong_hand=self.wrong_hand,
        ), now=now_utc())


def _carried_factors(raw: Any) -> Any:
    """The tutor block off the learner record the page just sent, if any.

    LearnerState.from_dict drops the keys it does not know, so this is lifted
    off the payload before the record is built rather than out of the record.
    """
    return raw.get("tutor") if isinstance(raw, Mapping) else None


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


def _reason_for(answered: bool) -> str:
    """How the exercise that is ending ended, in app/tutor.py's vocabulary."""
    return "answered" if answered else "skipped"


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
            verdict = classify(window)
            # Every window, right off the classifier. It only buffers: no file
            # is touched here, because this thread has 100 ms per frame and
            # MediaPipe spends most of it.
            try:
                lesson.live.offer(window, verdict)
            except Exception:
                live_failed("offer")
            fingers = fingers_from_window(window)
            hands_seen = len({f["hand"] for f in fingers})
            lesson.observe(verdict, fingers, hands_seen, time.monotonic(),
                           palm=palm_size(window))
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
    # The design images are the one exception to naming every file: the page
    # asks for art/<name>.png, and they are still being copied in, so the
    # directory is served as it stands rather than listed once here. Nothing
    # outside it can be reached: a path that resolves out of the directory is
    # refused by the router, and symlinks are not followed.
    if ART_DIR.is_dir():
        app.router.add_static("/art/", ART_DIR)
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
    # data/tutor_log.jsonl is the dataset Loop 2 is built on, so a run with no
    # camera in front of a child never writes a line into it.
    lesson.tutor.keep_log = not mock
    # Real windows from real lessons, into data/live_samples.jsonl. The mock
    # camera has no hands in front of it and the demo is a rehearsed script, so
    # neither is a child and neither records a sample. How many windows one
    # event is worth comes from lesson/tutor_params.json, key
    # live_sample_windows, and falls back to eight without it.
    lesson.live = LiveSampleWriter(LIVE_SAMPLES_PATH,
                                   windows_from_params(load_tutor_params()),
                                   enabled=not (mock or demo), log=log)
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
