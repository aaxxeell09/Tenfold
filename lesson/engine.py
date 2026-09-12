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
one flickering frame never moves the lesson.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from classifier.schema import GestureState

DEBOUNCE_S = 0.3

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


class Engine:
    """One lesson. Feed it gestures, ask it for the next exercise, check answers."""

    def __init__(self, exercises: Sequence[Exercise] | None = None,
                 debounce_s: float = DEBOUNCE_S) -> None:
        # None means "use the standard lesson". An empty list is a caller mistake,
        # not a request for the default, so it is refused rather than replaced.
        self.exercises: list[Exercise] = (
            default_exercises() if exercises is None else list(exercises)
        )
        if not self.exercises:
            raise ValueError("a lesson needs at least one exercise")
        self.debounce_s = debounce_s
        self._index = 0
        self._state = STATE_INTRO
        self._answer: int | None = None
        self._wrong: tuple[Finger, ...] = ()
        self._match: tuple[Finger, ...] = ()
        self._reasoning: tuple[str, ...] = ()
        self._hint: dict[str, object] = {}
        self._event: str | None = None
        self._committed = ""
        self._pending = ""
        self._pending_since = 0.0
        self._latched = False

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

    def observe(self, gesture: GestureState, now: float) -> Update | None:
        """Consume one classified frame. None when nothing the UI shows changed."""
        if self._latched:
            return None

        condition = _condition(gesture, self.exercise)
        if condition != self._pending:
            self._pending = condition
            self._pending_since = now
        if now - self._pending_since < self.debounce_s:
            return None
        if condition == self._committed:
            return None

        self._committed = condition
        self._wrong, self._match, self._hint = _scored(gesture, self.exercise)

        if condition == COND_UNKNOWN:
            self._state, self._event = STATE_WAITING_POSE, None
            self._wrong, self._match, self._hint = (), (), {}
        elif condition == COND_CORRECT:
            self._state, self._event = STATE_CORRECT_POSE, EVENT_CORRECT_POSE
            self._reasoning = self.exercise.reasoning_lines()
            self._wrong, self._hint = (), {}
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
        self._committed = ""
        self._pending = ""
        self._pending_since = 0.0
        self._latched = False
        return self.snapshot()
