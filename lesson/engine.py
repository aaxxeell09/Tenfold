"""The lesson state machine, SPEC.md section 8. Deterministic, no I/O, no clock of its own.

Time is always passed in, so a test can drive a whole lesson in a few microseconds
and the demo replays identically. The engine never talks to the camera, the
classifier or the screen: it consumes a GestureState and returns what changed.

Acceptance is order free (SPEC.md section 3): 7 x 8 and 8 x 7 are both correct,
the expected value is the set {a, b}. hands_swapped therefore cannot mean "this
is wrong". It means the two numbers are right but sitting on the opposite hands
from the way the exercise is written, and the fingers are not touching yet: at
that moment "swap your hands" is better advice than naming two fingers to move.
Touching as is still gives correct_pose.

An event is only emitted once its condition has held for the debounce window, so
one flickering frame never moves the lesson. What has to hold is what the screen
shows, the condition together with the fingers it names: two different wrong
poses are both "wrong", and the advice for the first one is stale on the second.

The correct pose is the exception, because that window is the wait the child
feels: a pose that already matches is confirmed after pose_confirm_frames
consecutive matching frames or pose_confirm_ms, whichever comes first, and never
later than that. One frame alone is never enough, so a flicker still cannot
validate a wrong pose. Everything else, wrong fingers included, keeps the 300 ms
debounce: a correction that appears too fast is read as nagging, and the 4 s
grace before a wrong pose is scored is a different clock, in app/server.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from classifier.schema import GestureState

DEBOUNCE_S = 0.3

# The correct pose confirmation window, SPEC.md section 8. Policy, so the live
# values come from lesson/tutor_params.json under these exact names and these
# are only the fallback when the caller passes nothing.
POSE_CONFIRM_FRAMES = 3
POSE_CONFIRM_FRAMES_BOUNDS = (2, 6)
POSE_CONFIRM_MS = 250.0
POSE_CONFIRM_MS_BOUNDS = (120.0, 600.0)

STATE_INTRO = "intro"
STATE_EXERCISE_SHOWN = "exercise_shown"
STATE_WAITING_POSE = "waiting_pose"
STATE_WRONG_POSE = "wrong_pose"
STATE_CORRECT_POSE = "correct_pose"
STATE_WAITING_ANSWER = "waiting_answer"
STATE_ANSWER_CORRECT = "answer_correct"
STATE_ANSWER_WRONG = "answer_wrong"

EVENT_WRONG_LEFT = "wrong_left_finger"
EVENT_WRONG_RIGHT = "wrong_right_finger"
EVENT_NO_CONTACT = "no_contact"
EVENT_HANDS_SWAPPED = "hands_swapped"
EVENT_CORRECT_POSE = "correct_pose"
EVENT_ANSWER_CORRECT = "answer_correct"
EVENT_ANSWER_WRONG = "answer_wrong"

# Conditions read off one GestureState, before debouncing.
COND_UNKNOWN = "unknown"
COND_CORRECT = "correct"
COND_NO_CONTACT = "no_contact"
COND_SWAPPED = "swapped"
COND_WRONG = "wrong"


@dataclass(frozen=True)
class Exercise:
    """One multiplication, and the reasoning the child is being taught."""

    a: int
    b: int

    @property
    def expected(self) -> frozenset[int]:
        return frozenset((self.a, self.b))

    @property
    def tens(self) -> int:
        return (self.a - 5) + (self.b - 5)

    @property
    def units(self) -> int:
        return (10 - self.a) * (10 - self.b)

    @property
    def result(self) -> int:
        return self.tens * 10 + self.units

    @property
    def title(self) -> str:
        return f"{self.a} x {self.b}"

    def reasoning_lines(self) -> tuple[str, str, str]:
        return (
            f"Touched and below: {self.tens} tens, so {self.tens * 10}",
            f"Above: {10 - self.a} x {10 - self.b} = {self.units} units",
            f"{self.tens * 10} + {self.units} = {self.result}",
        )


def default_exercises() -> list[Exercise]:
    """Every pair in 6..10, order free, so each pair appears once."""
    return [Exercise(a, b) for a in range(6, 11) for b in range(a, 11)]


@dataclass(frozen=True)
class Finger:
    hand: str
    number: int

    def as_dict(self) -> dict[str, object]:
        return {"hand": self.hand, "number": self.number}


@dataclass(frozen=True)
class Update:
    """What the lesson looks like now. The UI renders this and nothing else."""

    state: str
    exercise: Exercise
    event: str | None = None
    wrong: tuple[Finger, ...] = ()
    match: tuple[Finger, ...] = ()
    answer: int | None = None
    reasoning: tuple[str, ...] = ()
    hint: dict[str, object] = field(default_factory=dict)


def _condition(gesture: GestureState, exercise: Exercise) -> str:
    if gesture.method != "6-10" or gesture.left is None or gesture.right is None:
        return COND_UNKNOWN
    seen = frozenset((gesture.left, gesture.right))
    if seen == exercise.expected:
        if gesture.contact:
            return COND_CORRECT
        # Right numbers, not touching. Swapped only when the child could not
        # simply be reading the exercise in the other direction.
        if exercise.a != exercise.b and (gesture.left, gesture.right) == (exercise.b, exercise.a):
            return COND_SWAPPED
        return COND_NO_CONTACT
    return COND_WRONG


def _assign(gesture: GestureState, exercise: Exercise) -> tuple[int, int]:
    """Which expected finger each hand is aiming at, under the kinder reading."""
    straight = (exercise.a, exercise.b)
    crossed = (exercise.b, exercise.a)
    detected = (gesture.left, gesture.right)
    hits_straight = sum(1 for d, t in zip(detected, straight) if d == t)
    hits_crossed = sum(1 for d, t in zip(detected, crossed) if d == t)
    return straight if hits_straight >= hits_crossed else crossed


def _scored(gesture: GestureState, exercise: Exercise) -> tuple[tuple[Finger, ...], tuple[Finger, ...], dict]:
    """Fingers to paint as wrong, fingers to paint as placed, and the hint."""
    if gesture.left is None or gesture.right is None:
        return (), (), {}
    target_left, target_right = _assign(gesture, exercise)
    wrong: list[Finger] = []
    match: list[Finger] = []
    hint: dict[str, object] = {}
    for hand, seen, target in (("left", gesture.left, target_left),
                               ("right", gesture.right, target_right)):
        if seen == target:
            match.append(Finger(hand, seen))
        else:
            wrong.append(Finger(hand, seen))
            if not hint:
                hint = {"hand": hand, "move_from": seen, "move_to": target}
    return tuple(wrong), tuple(match), hint


def _view(gesture: GestureState, exercise: Exercise,
          condition: str) -> tuple[tuple[Finger, ...], tuple[Finger, ...], dict]:
    """What the screen paints for this condition: wrong fingers, placed fingers, hint."""
    if condition == COND_UNKNOWN:
        return (), (), {}
    wrong, match, hint = _scored(gesture, exercise)
    if condition == COND_CORRECT:
        return (), match, {}
    return wrong, match, hint


def _policy(params: Mapping[str, object] | None, key: str, default: float,
            bounds: tuple[float, float]) -> float:
    """One policy number off the parameter mapping, or its fallback.

    A value outside its bounds is refused, never clamped: app/tutor.py treats a
    parameter file that way too, and a silently clamped file is a policy nobody
    chose.
    """
    if params is None or key not in params:
        return float(default)
    raw = params[key]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"{key}: expected a number, got {raw!r}")
    value = float(raw)
    low, high = bounds
    if not low <= value <= high:
        raise ValueError(f"{key} = {value} is outside its bounds [{low}, {high}]")
    return value


class Engine:
    """One lesson. Feed it gestures, ask it for the next exercise, check answers."""

    def __init__(self, exercises: Sequence[Exercise] | None = None,
                 debounce_s: float = DEBOUNCE_S,
                 params: Mapping[str, object] | None = None) -> None:
        # None means "use the standard lesson". An empty list is a caller mistake,
        # not a request for the default, so it is refused rather than replaced.
        self.exercises: list[Exercise] = (
            default_exercises() if exercises is None else list(exercises)
        )
        if not self.exercises:
            raise ValueError("a lesson needs at least one exercise")
        self.debounce_s = debounce_s
        self.pose_confirm_frames: int = POSE_CONFIRM_FRAMES
        self.pose_confirm_ms: float = POSE_CONFIRM_MS
        self.set_pose_confirm(params)
        self._index = 0
        self._state = STATE_INTRO
        self._answer: int | None = None
        self._wrong: tuple[Finger, ...] = ()
        self._match: tuple[Finger, ...] = ()
        self._reasoning: tuple[str, ...] = ()
        self._hint: dict[str, object] = {}
        self._event: str | None = None
        # The debounced key: the condition and the fingers and hint it shows.
        self._committed: tuple | None = None
        self._pending: tuple | None = None
        self._pending_since = 0.0
        self._pending_frames = 0
        self._latched = False

    def set_pose_confirm(self, params: Mapping[str, object] | None = None) -> None:
        """Set the correct pose confirmation window from the tutor parameters.

        Reads pose_confirm_frames and pose_confirm_ms, each falling back to its
        default when the mapping does not carry it. Raises ValueError on a value
        outside its bounds, so a bad parameter file stops the lesson at startup
        rather than teaching with a window nobody chose.
        """
        self.pose_confirm_frames = int(round(_policy(
            params, "pose_confirm_frames", POSE_CONFIRM_FRAMES,
            POSE_CONFIRM_FRAMES_BOUNDS)))
        self.pose_confirm_ms = _policy(
            params, "pose_confirm_ms", POSE_CONFIRM_MS, POSE_CONFIRM_MS_BOUNDS)

    @property
    def exercise(self) -> Exercise:
        return self.exercises[self._index]

    @property
    def latched(self) -> bool:
        """True once the pose is correct: gestures stop moving the lesson."""
        return self._latched

    def snapshot(self) -> Update:
        return Update(
            state=self._state,
            exercise=self.exercise,
            event=self._event,
            wrong=self._wrong,
            match=self._match,
            answer=self._answer,
            reasoning=self._reasoning,
            hint=dict(self._hint),
        )

    def start(self) -> Update:
        """Leave the intro and show the first exercise."""
        self._state = STATE_EXERCISE_SHOWN
        self._event = None
        return self.snapshot()

    def _confirmed(self, condition: str, now: float) -> bool:
        """Has the pending condition held long enough to be shown?

        A matching pose is what the child is waiting on, so it is confirmed as
        soon as either counter is reached, whichever comes first: never later
        than pose_confirm_ms, never on a single frame since the window opens on
        the first frame at zero elapsed and the bounds keep both numbers above
        one frame. Every other condition keeps the 300 ms debounce.
        """
        held = now - self._pending_since
        if condition == COND_CORRECT:
            return (self._pending_frames >= self.pose_confirm_frames
                    or held >= self.pose_confirm_ms / 1000.0)
        return held >= self.debounce_s

    def observe(self, gesture: GestureState, now: float) -> Update | None:
        """Consume one classified frame. None when nothing the UI shows changed."""
        if self._latched:
            return None

        condition = _condition(gesture, self.exercise)
        wrong, match, hint = _view(gesture, self.exercise, condition)
        # 8 x 7 held as (8, 9) then as (9, 7) is "wrong" both times, but the
        # finger to move is not the same one: keying on the condition alone kept
        # the first correction on screen for the second pose.
        key = (condition, wrong, match, tuple(sorted(hint.items())))
        if key != self._pending:
            self._pending = key
            self._pending_since = now
            self._pending_frames = 1
        else:
            self._pending_frames += 1
        if not self._confirmed(condition, now):
            return None
        if key == self._committed:
            return None

        self._committed = key
        self._wrong, self._match, self._hint = wrong, match, hint

        if condition == COND_UNKNOWN:
            self._state, self._event = STATE_WAITING_POSE, None
        elif condition == COND_CORRECT:
            self._state, self._event = STATE_CORRECT_POSE, EVENT_CORRECT_POSE
            self._reasoning = self.exercise.reasoning_lines()
            self._latched = True
        elif condition == COND_NO_CONTACT:
            self._state, self._event = STATE_WRONG_POSE, EVENT_NO_CONTACT
        elif condition == COND_SWAPPED:
            self._state, self._event = STATE_WRONG_POSE, EVENT_HANDS_SWAPPED
        else:
            self._state = STATE_WRONG_POSE
            self._event = (EVENT_WRONG_LEFT if self._hint.get("hand") == "left"
                           else EVENT_WRONG_RIGHT)
        return self.snapshot()

    def waiting_answer(self) -> Update:
        """The reasoning has been read out, now the child types. SPEC.md section 8."""
        if self._state == STATE_CORRECT_POSE:
            self._state, self._event = STATE_WAITING_ANSWER, None
        return self.snapshot()

    def check(self, value: int | None) -> Update | None:
        """Validate a typed answer. None when there is nothing to validate."""
        if not self._latched or value is None:
            return None
        if value == self.exercise.result:
            self._state = STATE_ANSWER_CORRECT
            self._event = EVENT_ANSWER_CORRECT
            self._answer = self.exercise.result
        else:
            self._state = STATE_ANSWER_WRONG
            self._event = EVENT_ANSWER_WRONG
            self._answer = None
        return self.snapshot()

    def next(self) -> Update:
        """Move to the next exercise and unlatch. Wraps around at the end."""
        self._index = (self._index + 1) % len(self.exercises)
        return self._rearm()

    def repeat(self) -> Update:
        """Same exercise, clean slate."""
        return self._rearm()

    def load(self, exercise: Exercise) -> Update:
        """Replace the current exercise outright.

        The lesson order is the scheduler's business, not the engine's: the
        engine only ever knows which exercise is up now.
        """
        if exercise not in self.exercises:
            self.exercises.append(exercise)
        self._index = self.exercises.index(exercise)
        return self._rearm()

    def _rearm(self) -> Update:
        self._state = STATE_EXERCISE_SHOWN
        self._event = None
        self._answer = None
        self._wrong = ()
        self._match = ()
        self._reasoning = ()
        self._hint = {}
        self._committed = None
        self._pending = None
        self._pending_since = 0.0
        self._pending_frames = 0
        self._latched = False
        return self.snapshot()
