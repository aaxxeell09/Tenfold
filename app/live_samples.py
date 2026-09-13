"""Live perception samples: real windows from real lessons, into data/live_samples.jsonl.

The dataset in data/samples.jsonl is one person in one room in front of one camera, labelled by
construction (SPEC.md 5.6). Every lesson a child takes is another camera, another room and another
pair of hands, and today none of it is kept. This writer keeps it.

One JSON object per line, the schema of data/samples.jsonl (classifier/schema.py) plus six fields:

    source                "live", always, so a merged file can always be split again
    learner_id            the child's name from the welcome screen, lowercased and trimmed
    exercise              the exercise title, for example "8 x 7"
    session_ts            when this server run started, ISO 8601 UTC
    pose_confidence       the classifier's confidence on that window
    confirmed_by_answer   true when a correct answer corroborated the pose

and two more that make a hard negative usable as a pair:

    seen_pose             what the classifier read on that window
    target_pose           the pose the exercise asked for

Two kinds of record:

    Confirmed pose. An exercise completes with a correct pose and a correct answer. The last N
    stable windows of the validated pose are written with confirmed_by_answer true. The label is
    what the classifier read on each window, corroborated by the answer, never the raw target: if
    the child posed 7 on the left and 8 on the right for 8 x 7, the label says 7 and 8.

    Hard negative. A scored gesture error, meaning a wrong pose still held after a correction was
    given. The windows of that held pose are written with confirmed_by_answer false and both poses
    on the row, so the pair (what the classifier saw, what the child was asked for) is the negative.

Whose windows. Every window is owned from the moment it is collected: by the learner, the session
and the exercise on screen, which begin() sets. A new exercise, a new learner, a quit or the end of
a node forgets everything collected before it, and an event is only ever written from windows its
own learner produced on its own exercise. Without an exercise on screen nothing is collected.

The validated pose. The engine latches a correct pose and the answer can come much later: the
child's hands leave the frame, and a rolling buffer of a few seconds has long moved past the pose.
pose_validated() keeps the stable run of that pose aside the moment it is latched, keeps it up to
date while the child still holds it, and holds it until the answer or the end of the exercise.

What this file is not: it is weakly labelled. A confirmed pose is only as good as the classifier
that latched it, and a hard negative is only as good as the tutor's own scoring. It carries the
bias of the classifier that accepted it, so training on it can reinforce that classifier's own
mistakes. docs/loop.md, section "Live arm", says how the loop is meant to treat it.

Honest gaps, stated rather than faked:
  angle and distance are "unknown": a lesson does not know where the camera is.
  split is "live": never "train", so nothing here reaches the train arm by accident, and never
  "test", so nothing here can touch the held out gate.
  kind follows what the classifier read: "positive" for a contact pose, "near_contact" for a 6-10
  reading without contact, "transition" when the reading is unknown. An unknown reading on a held
  wrong pose is the one place where kind is a guess; the row's label is unknown either way, and
  target_pose says what the pose should have been.
  a window the camera thread captured just before begin() may be owned by the new exercise: one
  frame at most, the same frame the engine itself scores against the new exercise.

Wiring, in app/server.py:

    live = LiveSampleWriter(LIVE_SAMPLES_PATH, windows_from_params(params), enabled=not (mock or demo), log=log)
    live.offer(window, state)                                        # camera thread, every window
    live.begin(learner_id, exercise, session)                        # an exercise is loaded
    live.pose_validated(learner_id, exercise, target)                # the engine latched the pose
    live.confirm_correct(learner_id, exercise, target)               # answer correct on a correct pose
    live.record_gesture_error(learner_id, exercise, seen, target)    # scored gesture error
    live.end()                                                       # quit, node end, gate

N comes from lesson/tutor_params.json, key live_sample_windows, through windows_from_params. This
module never reads that file and never imports the tutor.

Nothing here may ever take a lesson down: every write is wrapped and every failure is swallowed and
logged, the same discipline as the tutor log sink in docs/tutor_contract.md section 3.
"""

from __future__ import annotations

import json
import logging
import math
import sys
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Mapping, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from classifier.schema import FINGER_NUMBERS, Window, window_to_json  # noqa: E402

LIVE_SAMPLES_PATH: Path = REPO_ROOT / "data" / "live_samples.jsonl"

# data/samples.jsonl is frozen by CLAUDE.md and ../tenfold-heldout/test.jsonl is the held out set.
# The writer refuses both by name, whatever directory they are handed to it in.
FROZEN_FILENAMES: tuple[str, ...] = ("samples.jsonl", "test.jsonl")

SOURCE: str = "live"
SPLIT: str = "live"
LIVE_ANGLE: str = "unknown"
LIVE_DISTANCE: str = "unknown"
ID_PREFIX: str = "l"

# The tutor params key and its fallback.
PARAM_KEY: str = "live_sample_windows"
DEFAULT_WINDOWS: int = 8

# Fingertip landmark indices, SPEC.md 5.1: thumb, index, middle, ring, pinky, that is 6 to 10.
FINGERTIPS: tuple[int, ...] = (4, 8, 12, 16, 20)

# Dedupe: two windows are near identical when no fingertip moved more than this, in normalized hand
# units (points are wrist origin divided by the wrist to middle MCP distance, SPEC.md 5.2). One
# finger step, 9 to 7 say, is 0.5 to 1.0 of those units, and MediaPipe's own jitter on a hand held
# still is 0.01 to 0.04, so 0.005 is an order of magnitude below both: it collapses the same
# measurement offered twice (a frozen camera, a repeated event, a window handed over twice) and
# never two honest measurements of the same pose.
DEDUPE_DISTANCE: float = 0.005
# How many written windows are remembered for that comparison, per process.
DEDUPE_MEMORY: int = 64
# The rolling buffer holds at least this many windows, so a stable run of N is always reachable.
BUFFER_MIN: int = 32


def _now_iso() -> str:
    """ISO 8601 UTC with milliseconds and a Z, the shape data/tutor_log.jsonl uses."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _param_scopes(params: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    """Where a tutor param may sit: the global block first, then the top level."""
    if not isinstance(params, Mapping):
        return []
    scopes: list[Mapping[str, Any]] = []
    block = params.get("global")
    if isinstance(block, Mapping):
        scopes.append(block)
    scopes.append(params)
    return scopes


def windows_from_params(params: Mapping[str, Any] | None,
                        default: int = DEFAULT_WINDOWS) -> int:
    """How many windows one event is worth, read from the tutor params the caller loaded.

    The caller passes what it read from lesson/tutor_params.json; this module never opens that file
    and never adds the key to it. An absent, unreadable or nonsensical value falls back to 8.
    """
    for scope in _param_scopes(params):
        if PARAM_KEY not in scope:
            continue
        try:
            count = int(scope[PARAM_KEY])
        except (TypeError, ValueError):
            return default
        return count if count >= 1 else default
    return default


def _window_count(value: Any) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return DEFAULT_WINDOWS
    return count if count >= 1 else DEFAULT_WINDOWS


def _learner_key(learner_id: Any) -> str:
    """The child's name as an id: trimmed and lowercased, so one child is one learner.

    Empty is not an id. A record without one is dropped rather than written anonymously, because a
    row nobody can attribute is a row nobody can remove either.
    """
    if learner_id is None:
        return ""
    return str(learner_id).strip().lower()


def _title(exercise: Any) -> str:
    return str(exercise if exercise is not None else "").strip()


@dataclass(frozen=True)
class Pose:
    """A 6-10 pose, as the classifier read it or as the exercise asked for it.

    Built from anything the server has at hand: a GestureState, another Pose, a label dict, a
    (left, right) pair, or None for "no pose at all".
    """

    method: str = "unknown"
    left: Optional[int] = None
    right: Optional[int] = None
    contact: bool = False

    @classmethod
    def of(cls, value: Any) -> "Pose":
        if value is None:
            return cls()
        if isinstance(value, Pose):
            return value
        if isinstance(value, Mapping):
            return cls._build(value.get("method"), value.get("left"),
                              value.get("right"), value.get("contact"))
        if hasattr(value, "method"):
            return cls._build(getattr(value, "method", None), getattr(value, "left", None),
                              getattr(value, "right", None), getattr(value, "contact", None))
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 2:
            # A target as the engine states it: the pair of fingers, in contact.
            return cls._build("6-10", value[0], value[1], True)
        return cls()

    @classmethod
    def _build(cls, method: Any, left: Any, right: Any, contact: Any) -> "Pose":
        def finger(value: Any) -> Optional[int]:
            try:
                number = int(value)
            except (TypeError, ValueError):
                return None
            return number if number in FINGER_NUMBERS else None

        if method != "6-10":
            return cls()
        return cls(method="6-10", left=finger(left), right=finger(right), contact=bool(contact))

    @property
    def known(self) -> bool:
        return self.method == "6-10" and self.left is not None and self.right is not None

    def to_label(self) -> dict[str, Any]:
        """The label shape of data/samples.jsonl: the four keys, or method unknown alone."""
        if not self.known:
            return {"method": "unknown"}
        return {"method": self.method, "left": self.left, "right": self.right,
                "contact": self.contact}

    def matches(self, other: "Pose", unordered: bool = False) -> bool:
        """Same pose. Unordered compares {left, right} as a set: the lesson engine accepts 7 x 8
        for 8 x 7, so a confirmed pose is matched that way; a hard negative is not, because a
        swapped pair is a different error."""
        if not self.known or not other.known:
            return not self.known and not other.known
        if self.contact != other.contact:
            return False
        if unordered:
            return {self.left, self.right} == {other.left, other.right}
        return self.left == other.left and self.right == other.right

    @property
    def kind(self) -> str:
        """The capture kind of classifier/schema.KINDS this reading belongs to."""
        if self.known and self.contact:
            return "positive"
        if self.known:
            return "near_contact"
        return "transition"

    @property
    def class_token(self) -> str:
        """The class name eval/scorers.class_of would give this reading, used in hold_id."""
        if self.known and self.contact:
            return f"{self.left}x{self.right}"
        if self.known:
            return f"near{self.left}x{self.right}"
        return "unknown"


@dataclass(frozen=True)
class Owner:
    """Whose windows these are: one learner, one session, one exercise, one epoch.

    The epoch changes on every begin() and end(), so the same exercise title met twice, or the same
    child coming back, is still a different owner from the one before it.
    """

    learner: str
    exercise: str
    session: str
    epoch: int


@dataclass
class _Observation:
    """One window as it arrived, with what the classifier made of it and whose it is."""

    window: Window
    pose: Pose
    confidence: float
    epoch: int


def _confidence(state: Any) -> float:
    if isinstance(state, Mapping):
        raw = state.get("confidence", 0.0)
    else:
        raw = getattr(state, "confidence", 0.0)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if value != value:  # NaN
        return 0.0
    return max(0.0, min(1.0, value))


Signature = tuple[tuple[float, ...], ...]


def _signature(window: Window) -> Signature:
    """The ten fingertips of the newest frame, per hand, in normalized hand units.

    The newest frame rather than an average over the window: two overlapping windows of the same
    hold differ by a full frame of jitter there, while the same window handed over twice gives
    exactly the same numbers. An absent hand is an empty tuple, so presence is part of the
    comparison and a one hand window is never near identical to a two hand one.
    """
    hands: list[tuple[float, ...]] = []
    for frames in (window.left, window.right):
        frame = frames[-1] if frames else None
        if frame is None or not frame.present or frame.points is None:
            hands.append(())
            continue
        hands.append(tuple(
            float(coordinate)
            for index in FINGERTIPS
            for coordinate in (frame.points[index][0], frame.points[index][1])
        ))
    return tuple(hands)


def _near_identical(left: Signature, right: Signature,
                    threshold: float = DEDUPE_DISTANCE) -> bool:
    """True when no fingertip moved more than the threshold between the two windows.

    The worst fingertip, not the average one: a single finger moving from 9 to 7 is a different
    pose, and an average over ten fingers would hide it behind the nine that did not move.
    """
    if len(left) != len(right):
        return False
    for hand_left, hand_right in zip(left, right):
        if len(hand_left) != len(hand_right):
            return False
    for hand_left, hand_right in zip(left, right):
        for index in range(0, len(hand_left), 2):
            moved = math.hypot(hand_left[index] - hand_right[index],
                               hand_left[index + 1] - hand_right[index + 1])
            if not moved <= threshold:  # NaN lands here too, and NaN is never a duplicate
                return False
    return True


class LiveSampleWriter:
    """Appends live windows to data/live_samples.jsonl. Never raises.

    path      where the rows go. data/samples.jsonl and test.jsonl are refused.
    windows   N, the number of windows one event is worth. See windows_from_params.
    enabled   False for the demo scenario and the mock camera. Off by default: a caller that
              forgets the flag records nothing rather than filling the dataset with fake hands.
    log       where failures go. Anything with .warning, .error and .exception.

    offer runs on the camera thread and everything else on the event loop, so one lock guards the
    buffer, the owner and the validated pose.
    """

    def __init__(self, path: str | Path = LIVE_SAMPLES_PATH, windows: int = DEFAULT_WINDOWS,
                 enabled: bool = False, log: logging.Logger | None = None, *,
                 session_ts: str | None = None) -> None:
        self.log = log if log is not None else logging.getLogger("tenfold.live_samples")
        self.path = Path(path)
        self.windows = _window_count(windows)
        self.session_ts = session_ts or _now_iso()
        self.enabled = bool(enabled)
        if self.enabled and self.path.name in FROZEN_FILENAMES:
            self.log.error("live_samples: refusing %s, that file is frozen by the contract",
                           self.path)
            self.enabled = False
        self._lock = threading.Lock()
        self._buffer: Deque[_Observation] = deque(maxlen=max(BUFFER_MIN, self.windows * 4))
        self._recent: Deque[tuple[str, str, Signature]] = deque(maxlen=DEDUPE_MEMORY)
        self._next_index: Optional[int] = None
        self._events: int = 0
        self._epoch: int = 0
        self._owner: Optional[Owner] = None
        # The validated pose, kept aside from the rolling buffer until the answer.
        self._held: Deque[_Observation] = deque(maxlen=self.windows)
        self._held_pose: Optional[Pose] = None
        self._held_open: bool = False

    # --- whose windows -----------------------------------------------------

    @property
    def owner(self) -> Optional[Owner]:
        return self._owner

    def begin(self, learner_id: Any, exercise: Any, session: Any = "") -> None:
        """An exercise is on screen for this learner. Everything collected before it is forgotten.

        Called on every exercise the server loads, which also covers a new node, a new learner and
        a restart: none of them may ever reach back into the windows of the exercise before.
        """
        if not self.enabled:
            return
        with self._lock:
            self._epoch += 1
            self._owner = Owner(_learner_key(learner_id), _title(exercise),
                                str(session if session is not None else ""), self._epoch)
            self._forget()

    def end(self) -> None:
        """No exercise on screen any more: quit, node end, gate. Forget and stop collecting."""
        with self._lock:
            self._epoch += 1
            self._owner = None
            self._forget()

    def reset(self) -> None:
        """The older name of end(), kept for callers that still use it."""
        self.end()

    def _forget(self) -> None:
        self._buffer.clear()
        self._held.clear()
        self._held_pose = None
        self._held_open = False

    def _owns(self, learner_id: Any, exercise: Any) -> bool:
        owner = self._owner
        return (owner is not None and bool(owner.learner)
                and _learner_key(learner_id) == owner.learner and _title(exercise) == owner.exercise)

    # --- the camera thread -------------------------------------------------

    def offer(self, window: Window, state: Any) -> None:
        """Take one window and what the classifier made of it. Cheap: it only buffers.

        Called on every window, so no file is touched and no signature is computed here: the live
        loop has 100 ms per frame and MediaPipe spends most of it. Without an exercise on screen the
        window is not kept at all.
        """
        if not self.enabled or self._owner is None:
            return
        try:
            if not hasattr(window, "left") or not hasattr(window, "right"):
                raise TypeError(f"a Window was expected, got {type(window).__name__}")
            pose = Pose.of(state)
            with self._lock:
                if self._owner is None:
                    return
                observation = _Observation(window, pose, _confidence(state), self._epoch)
                self._buffer.append(observation)
                self._follow_held(observation)
        except Exception:  # a malformed window must not take the lesson down
            self.log.exception("live_samples: a window could not be buffered")

    def _follow_held(self, observation: _Observation) -> None:
        """Keep the validated pose's run current while the child still holds it.

        A window of the same pose extends it, a window of a different known pose closes it, and an
        unknown reading (hands gone from the frame) does neither. The pose held again after being
        closed starts a fresh run that replaces the old one: the latest hold of the validated pose
        is the one the answer corroborates.
        """
        if self._held_pose is None:
            return
        if observation.pose.matches(self._held_pose, unordered=True):
            if not self._held_open:
                self._held.clear()
                self._held_open = True
            self._held.append(observation)
        elif observation.pose.known:
            self._held_open = False

    # --- the events ----------------------------------------------------------

    def pose_validated(self, learner_id: Any, exercise: Any, target: Any = None) -> int:
        """The engine latched the pose. Keep its stable run aside until the answer.

        target is the pose the exercise asked for; without it the most recent pose the classifier
        read is used. Returns how many windows are kept.
        """
        if not self.enabled:
            return 0
        try:
            with self._lock:
                if not self._owns(learner_id, exercise):
                    self.log.warning("live_samples: a validated pose for another learner or "
                                     "exercise than the one on screen was ignored")
                    return 0
                reference = Pose.of(target)
                if not reference.known:
                    reference = self._latest_known_pose()
                if not reference.known:
                    return 0
                run = self._stable_run(reference, unordered=True)
                self._held.clear()
                self._held.extend(run)
                self._held_pose = reference
                self._held_open = bool(run)
                return len(self._held)
        except Exception:
            self.log.exception("live_samples: a validated pose could not be kept")
            return 0

    def confirm_correct(self, learner_id: str, exercise: Any, target: Any = None) -> int:
        """The exercise completed: a correct pose, confirmed by a correct answer.

        Writes the last N stable windows of the validated pose, confirmed_by_answer true: the run
        pose_validated kept aside, or without one the last run still in the buffer. Only this
        learner's windows on this exercise are ever used. Returns how many rows were written.
        """
        if not self.enabled:
            return 0
        try:
            with self._lock:
                if not self._owns(learner_id, exercise):
                    self.log.warning("live_samples: a confirmed pose for another learner or "
                                     "exercise than the one on screen was not written")
                    return 0
                reference = Pose.of(target)
                if not reference.known:
                    reference = self._held_pose or self._latest_known_pose()
                if not reference.known:
                    return 0
                if (self._held and self._held_pose is not None
                        and self._held_pose.matches(reference, unordered=True)):
                    run = list(self._held)
                else:
                    run = self._stable_run(reference, unordered=True)
                return self._write(learner_id, exercise, run, reference, confirmed=True)
        except Exception:
            self.log.exception("live_samples: a confirmed pose was dropped")
            return 0

    def record_gesture_error(self, learner_id: str, exercise: Any, seen: Any,
                             target: Any) -> int:
        """A scored gesture error: a wrong pose still held after a correction was given.

        Writes the windows of that held pose, confirmed_by_answer false, with both the pose the
        classifier saw and the pose the exercise asked for, so the row is usable as a hard
        negative. Only this learner's windows on this exercise are used. Returns how many rows were
        written.
        """
        if not self.enabled:
            return 0
        try:
            with self._lock:
                if not self._owns(learner_id, exercise):
                    self.log.warning("live_samples: a gesture error for another learner or "
                                     "exercise than the one on screen was not written")
                    return 0
                seen_pose = Pose.of(seen)
                target_pose = Pose.of(target)
                run = self._stable_run(seen_pose, unordered=False)
                return self._write(learner_id, exercise, run, target_pose, confirmed=False,
                                   seen_hint=seen_pose)
        except Exception:
            self.log.exception("live_samples: a gesture error was dropped")
            return 0

    # --- picking the windows ----------------------------------------------

    def _current(self) -> list[_Observation]:
        """The buffered windows of the owner on screen, oldest first."""
        return [observation for observation in self._buffer if observation.epoch == self._epoch]

    def _latest_known_pose(self) -> Pose:
        for observation in reversed(self._current()):
            if observation.pose.known:
                return observation.pose
        return Pose()

    def _stable_run(self, reference: Pose, unordered: bool) -> list[_Observation]:
        """The last run of up to N windows on which the classifier read this pose.

        Scanned backwards from the newest window, because the answer is typed after the pose was
        held and the child's hands have usually left the frame by then; the run is contiguous, so a
        different pose in between ends it.
        """
        entries = self._current()
        end = -1
        for index in range(len(entries) - 1, -1, -1):
            if entries[index].pose.matches(reference, unordered=unordered):
                end = index
                break
        if end < 0:
            return []
        run: list[_Observation] = []
        index = end
        while (index >= 0 and len(run) < self.windows
               and entries[index].pose.matches(reference, unordered=unordered)):
            run.append(entries[index])
            index -= 1
        run.reverse()
        return run

    # --- writing -----------------------------------------------------------

    def _write(self, learner_id: str, exercise: Any, run: list[_Observation], target: Pose,
               confirmed: bool, seen_hint: Pose | None = None) -> int:
        who = _learner_key(learner_id)
        if not who:
            self.log.warning("live_samples: %d windows dropped, the learner id was empty",
                             len(run))
            return 0
        if not run:
            return 0

        title = _title(exercise)
        if self._next_index is None:
            self._next_index = self._count_existing()
        self._events += 1
        reference = seen_hint if seen_hint is not None else run[-1].pose
        hold = (f"{who}_{self.session_ts}:{reference.class_token}:"
                f"{LIVE_ANGLE}:{LIVE_DISTANCE}:{self._events}")

        rows: list[dict[str, Any]] = []
        signatures: list[tuple[str, str, Signature]] = []
        index = self._next_index
        for observation in run:
            label = observation.pose.to_label()
            key = json.dumps(label, sort_keys=True, separators=(",", ":"))
            signature = _signature(observation.window)
            if self._is_duplicate(who, key, signature, signatures):
                continue
            rows.append(self._row(index, who, title, hold, label, observation, target, confirmed))
            signatures.append((who, key, signature))
            index += 1

        if not rows:
            return 0
        if not self._append(rows):
            return 0
        self._next_index = index
        self._recent.extend(signatures)
        # A written run is consumed, so a repeated event cannot write the same hold twice.
        self._buffer.clear()
        self._held.clear()
        self._held_pose = None
        self._held_open = False
        return len(rows)

    def _row(self, index: int, who: str, title: str, hold: str, label: dict[str, Any],
             observation: _Observation, target: Pose, confirmed: bool) -> dict[str, Any]:
        """One line: the data/samples.jsonl schema, then the live fields, then the window."""
        return {
            "id": f"{ID_PREFIX}{index:06d}",
            "hold_id": hold,
            "session": f"{who}_{self.session_ts}",
            "person": who,
            "angle": LIVE_ANGLE,
            "distance": LIVE_DISTANCE,
            "kind": observation.pose.kind,
            "label": label,
            "split": SPLIT,
            "source": SOURCE,
            "learner_id": who,
            "exercise": title,
            "session_ts": self.session_ts,
            "pose_confidence": round(observation.confidence, 4),
            "confirmed_by_answer": confirmed,
            "seen_pose": observation.pose.to_label(),
            "target_pose": target.to_label(),
            "window": window_to_json(observation.window),
        }

    def _is_duplicate(self, who: str, key: str, signature: Signature,
                      pending: list[tuple[str, str, Signature]]) -> bool:
        """Near identical to a window already written, for the same learner and the same label.

        Two learners never dedupe against each other, and two different labels never do either: a
        row is only redundant when it says the same thing about the same child.
        """
        for other_who, other_key, other_signature in list(self._recent) + pending:
            if other_who != who or other_key != key:
                continue
            if _near_identical(signature, other_signature):
                return True
        return False

    def _count_existing(self) -> int:
        try:
            if not self.path.exists():
                return 0
            with open(self.path, encoding="utf-8") as handle:
                return sum(1 for line in handle if line.strip())
        except Exception:
            self.log.exception("live_samples: %s could not be counted, ids restart at 0",
                               self.path)
            return 0

    def _append(self, rows: list[dict[str, Any]]) -> bool:
        """Append every row and flush. Any failure is swallowed: a lesson never stops for this."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, separators=(",", ":")) + "\n")
                handle.flush()
            return True
        except Exception:
            self.log.exception("live_samples: %d rows dropped, writing to %s failed",
                               len(rows), self.path)
            self._next_index = None
            return False
