"""Tally's brain: what to ask next, and what she noticed. No I/O, no clock.

Beyond SPEC.md, which defers the learner model to the roadmap as amendment F7.
Built on request. Everything here is a pure function of the state passed in plus
the time passed in, so a whole week of sessions replays identically in a test and
the demo is reproducible.

Two dimensions are tracked apart, because they fail for different reasons and a
child who can compute 8 x 7 but cannot hold the pose needs the opposite help of
one who holds it and miscounts:

  pose   per ordered pair, key "8x7" meaning 8 on the left hand, 7 on the right,
         25 of them. A pose error only ever moves pose mastery.
  math   per unordered fact, key "6x7" with the smaller factor first, 15 of them.
         A math error only ever moves math mastery.

Mastery is 0 to 5. A first try success adds one, a success that needed a hint
adds nothing, an error takes one off and never goes below zero.

Confidence rule, on math only, as specified: mastery only rises when the success
lands in a later session than the previous rise. Five correct answers inside one
session are one step, not five, because repeating a fact you were just shown is
recall, not memory.

Spacing by math mastery: 0 later in this session, 1 the next session, 2 one day,
3 three days, 4 seven days, 5 fourteen days. The first two are session counted
rather than clock counted, which is why a record carries both due_session and
due_at: a child who practises twice in one evening should not be shown a mastery
1 fact again that evening, and a child who skips three days is not punished.

Variety, because every lesson used to repeat the same facts. A course node is
the theme of its lesson and is served first, then the session widens to the
whole range of tables to fill its length:

  never the same question twice in one session, unless the child got it wrong,
  in which case the retry queue brings it back and it is the only repeat;
  never the same table twice in a row;
  the two orientations alternate, and 6 on the left with 7 on the right is a
  different question from 7 on the left with 6 on the right.

New material stays inside the node: the widened part of a lesson is practice of
facts the child has already met, never the place where a fact is taught. The
widening can still reach a fact the child has never met, which is practice and
not new material, so every Pick carries seen: the phrase layer needs it to stop
announcing a first meeting as a review, and the mastery gate ignores it.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

# --- the material ------------------------------------------------------------

FACTORS = (6, 7, 8, 9, 10)
MAX_MASTERY = 5

# Easiest first. Fixed, and only overridden once there is enough personal data.
DIFFICULTY_ORDER: tuple[str, ...] = (
    "10x10", "6x10", "7x10", "8x10", "9x10", "9x9", "8x8", "7x7",
    "8x9", "7x8", "6x9", "7x9", "6x8", "6x6", "6x7",
)
PERSONAL_ORDER_AFTER_SESSIONS = 3

SPACING_DAYS = {2: 1, 3: 3, 4: 7, 5: 14}

# --- session shape -----------------------------------------------------------

SESSION_MIN_EXERCISES = 6
SESSION_MAX_EXERCISES = 10
SESSION_HARD_STOP_S = 6 * 60
CONSECUTIVE_ERRORS_TO_STOP = 3
SLOW_FACTOR = 2.0
FAST_SUCCESS_S = 5.0
FAST_SUCCESSES_FOR_LEVEL_UP = 3
LEVEL_UP_STEPS = 2
RETRY_AFTER_EXERCISES = 2
MASTERED_FROM = 4
ERRORS_BEFORE_CONFIDENCE = 2
NEW_FACT_SUCCESSES_REQUIRED = 2
# The next day plan: three due, one new, one mastered.
NEXT_DAY_PLAN = ("due", "due", "due", "new", "mastered")

# --- reactions, phrased by lesson/tally.py -----------------------------------

REACTION_RETRY = "retry"
REACTION_REVIEW = "review"
REACTION_CONFIDENCE = "confidence"
REACTION_NEXT_NEW = "next_new"
REACTION_LEVEL_UP = "level_up"
REACTION_HINT_1 = "hint_1"
REACTION_HINT_2 = "hint_2"
REACTION_HINT_3 = "hint_3"
REACTION_SAME_HAND_TWICE = "same_hand_twice"
REACTION_RECOUNT_TENS = "recount_tens"
REACTION_RECOUNT_UNITS = "recount_units"
REACTION_CANNOT_SEE = "cannot_see"
REACTION_END_SUCCESS = "end_success"
REACTION_END_TIRED = "end_tired"

HINT_EVENTS = (REACTION_HINT_1, REACTION_HINT_2, REACTION_HINT_3)
CANNOT_SEE_AFTER_S = 2.0


def fact_key(a: int, b: int) -> str:
    """Unordered, smaller factor first."""
    low, high = sorted((a, b))
    return f"{low}x{high}"


def pose_key(left: int, right: int) -> str:
    """Ordered: which finger on which hand."""
    return f"{left}x{right}"


def factors_of(key: str) -> tuple[int, int]:
    a, b = key.split("x")
    return int(a), int(b)


def all_facts() -> tuple[str, ...]:
    return DIFFICULTY_ORDER


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def _parse(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# The record arrives from a browser's localStorage, where anything can have been
# written or half written. Every field below is read through one of these, so a
# malformed value falls back to its default instead of raising.


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if number != number else number      # NaN is not a duration


def _as_text(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _as_optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _as_optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# --- state -------------------------------------------------------------------


@dataclass
class Attempt:
    at: str
    correct: bool
    hinted: bool
    response_time: float
    session: int

    @classmethod
    def from_dict(cls, raw: Any) -> "Attempt":
        """Never raises: a malformed field falls back to its default."""
        if not isinstance(raw, dict):
            raw = {}
        return cls(
            at=_as_text(raw.get("at", "")),
            correct=bool(raw.get("correct", False)),
            hinted=bool(raw.get("hinted", False)),
            response_time=max(0.0, _as_float(raw.get("response_time", 0.0))),
            session=max(0, _as_int(raw.get("session", 0))),
        )


@dataclass
class Record:
    """One pose or one fact. Keeps the last five attempts and nothing older."""

    mastery: int = 0
    attempts: list[Attempt] = field(default_factory=list)
    due_at: str | None = None
    due_session: int | None = None
    last_increase_session: int | None = None
    # How many attempts have been logged in all, not just the five kept. The
    # orientation alternation counts serves, and len(attempts) stops at five,
    # which froze the alternation from the sixth attempt of a session on. Not
    # stored: a reloaded record restarts this count from the attempts it carries,
    # which is what the stored shape can say.
    logged: int = 0

    def log(self, attempt: Attempt) -> None:
        self.attempts.append(attempt)
        del self.attempts[:-5]
        self.logged += 1

    @property
    def seen(self) -> bool:
        return bool(self.attempts)

    @property
    def error_rate(self) -> float:
        if not self.attempts:
            return 0.0
        return sum(1 for a in self.attempts if not a.correct) / len(self.attempts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mastery": self.mastery,
            "attempts": [asdict(a) for a in self.attempts],
            "due_at": self.due_at,
            "due_session": self.due_session,
            "last_increase_session": self.last_increase_session,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "Record":
        """Never raises: a malformed record reads as an empty one, and a
        malformed attempt inside a sound record is dropped."""
        if not isinstance(raw, dict):
            return cls()
        listed = raw.get("attempts")
        attempts = [Attempt.from_dict(a) for a in listed
                    if isinstance(a, dict)][-5:] if isinstance(listed, list) else []
        return cls(
            mastery=max(0, min(MAX_MASTERY, _as_int(raw.get("mastery", 0)))),
            attempts=attempts,
            due_at=_as_optional_text(raw.get("due_at")),
            due_session=_as_optional_int(raw.get("due_session")),
            last_increase_session=_as_optional_int(raw.get("last_increase_session")),
            logged=len(attempts),
        )


ERROR_KINDS = ("wrong_left", "wrong_right", "no_contact", "tens_error",
               "units_error", "hint_level_needed")

# Which hand was actually misplaced, as the caller names it. The engine's own
# event names are accepted too, so a caller can pass straight through what
# lesson/engine.py emitted.
POSE_ERROR_KINDS: dict[str, str] = {
    "left": "wrong_left",
    "wrong_left": "wrong_left",
    "wrong_left_finger": "wrong_left",
    "right": "wrong_right",
    "wrong_right": "wrong_right",
    "wrong_right_finger": "wrong_right",
    "no_contact": "no_contact",
}


@dataclass
class LearnerState:
    """Everything Tally remembers. Round trips through the page's localStorage."""

    learner_id: str = ""
    display_name: str | None = None
    created_at: str = ""
    # XP is the page's gamification and is never lost. It is carried here so
    # the learner record is the one profile, and so the server can log it.
    xp: int = 0
    sessions: int = 0
    last_session_at: str | None = None
    pose: dict[str, Record] = field(default_factory=dict)
    math: dict[str, Record] = field(default_factory=dict)
    error_profile: dict[str, int] = field(
        default_factory=lambda: {kind: 0 for kind in ERROR_KINDS})

    @classmethod
    def new(cls, now: datetime, display_name: str | None = None) -> "LearnerState":
        return cls(learner_id=uuid.uuid4().hex[:12], display_name=display_name,
                   created_at=_iso(now) or "")

    def fact(self, key: str) -> Record:
        return self.math.setdefault(key, Record())

    def pose_record(self, key: str) -> Record:
        return self.pose.setdefault(key, Record())

    def to_dict(self) -> dict[str, Any]:
        return {
            "learner_id": self.learner_id,
            "display_name": self.display_name,
            "created_at": self.created_at,
            "xp": self.xp,
            "sessions": self.sessions,
            "last_session_at": self.last_session_at,
            "pose": {k: v.to_dict() for k, v in self.pose.items()},
            "math": {k: v.to_dict() for k, v in self.math.items()},
            "error_profile": dict(self.error_profile),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None, now: datetime | None = None) -> "LearnerState":
        """Anything missing or malformed becomes a fresh learner rather than an error:
        this arrives from a browser's localStorage and must never break a lesson."""
        if not isinstance(raw, dict) or not raw.get("learner_id"):
            return cls.new(now or now_utc())
        profile = {kind: 0 for kind in ERROR_KINDS}
        listed = raw.get("error_profile")
        if isinstance(listed, dict):
            for kind, count in listed.items():
                if kind in profile:
                    profile[kind] = max(0, _as_int(count))
        return cls(
            learner_id=str(raw["learner_id"]),
            display_name=_as_optional_text(raw.get("display_name")),
            created_at=_as_text(raw.get("created_at", "")),
            xp=max(0, _as_int(raw.get("xp", 0))),
            sessions=max(0, _as_int(raw.get("sessions", 0))),
            last_session_at=_as_optional_text(raw.get("last_session_at")),
            pose=cls._records(raw.get("pose")),
            math=cls._records(raw.get("math")),
            error_profile=profile,
        )

    @staticmethod
    def _records(raw: Any) -> dict[str, Record]:
        """One half of the memory. A key that is not a string, or an entry that
        is not a record, is dropped rather than raising."""
        if not isinstance(raw, dict):
            return {}
        return {key: Record.from_dict(value) for key, value in raw.items()
                if isinstance(key, str) and isinstance(value, dict)}


# --- what the scheduler hands back -------------------------------------------


@dataclass(frozen=True)
class Pick:
    """One exercise, and why Tally chose it.

    is_new is new material: a fact the node is teaching now, which is what the
    mastery gate counts. seen is a plainer thing, and the two are not the same
    question: a widened fact is never new material, yet the child may never have
    met it, and announcing it as a review would be a small lie. seen says the
    child has attempted this fact before, so the phrase layer can tell the two
    apart. It defaults to True, which is what every caller that builds a Pick by
    hand means, the scripted demo included: their wording stays as it was.
    """

    fact: str
    left: int
    right: int
    reason: str
    is_new: bool = False
    seen: bool = True

    @property
    def pose(self) -> str:
        return pose_key(self.left, self.right)

    @property
    def result(self) -> int:
        return self.left * self.right

    def to_dict(self) -> dict[str, Any]:
        return {"fact": self.fact, "left": self.left, "right": self.right,
                "pose": self.pose, "reason": self.reason, "is_new": self.is_new,
                "seen": self.seen}


@dataclass
class Outcome:
    """What happened on one exercise, as the server saw it."""

    fact: str
    left: int
    right: int
    correct: bool
    response_time: float = 0.0
    hint_level: int = 0
    pose_error: bool = False
    math_error: bool = False
    given: int | None = None
    # Which hand the engine said was misplaced: left, right or no_contact. The
    # exercise itself cannot say, so without it a pose error is not attributed.
    wrong_hand: str | None = None


@dataclass
class SessionSummary:
    reason: str
    opened: list[str] = field(default_factory=list)
    became_solid: list[str] = field(default_factory=list)
    tomorrow: str | None = None
    exercises: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "opened": self.opened,
                "became_solid": self.became_solid, "tomorrow": self.tomorrow,
                "exercises": self.exercises}


# --- ordering ----------------------------------------------------------------


def fact_order(state: LearnerState) -> list[str]:
    """New facts easiest first.

    The fixed list until there is enough personal history, then the child's own
    error rate leads and the fixed list only breaks ties. A fact never attempted
    has an error rate of zero, so untouched facts keep their fixed order among
    themselves and sit ahead of the ones this child actually gets wrong.
    """
    if state.sessions < PERSONAL_ORDER_AFTER_SESSIONS:
        return list(DIFFICULTY_ORDER)
    index = {key: position for position, key in enumerate(DIFFICULTY_ORDER)}
    return sorted(
        DIFFICULTY_ORDER,
        key=lambda key: (state.math.get(key, Record()).error_rate, index[key]),
    )


def is_due(record: Record, now: datetime, session: int) -> bool:
    if not record.seen:
        return False
    if record.due_session is not None:
        return session >= record.due_session
    moment = _parse(record.due_at)
    return moment is not None and now >= moment


def schedule_next(record: Record, now: datetime, session: int) -> None:
    """Set when this fact comes back, from its mastery."""
    record.due_at = None
    record.due_session = None
    if record.mastery <= 0:
        record.due_session = session          # again later in this same session
    elif record.mastery == 1:
        record.due_session = session + 1      # the next session, whenever that is
    else:
        record.due_at = _iso(now + timedelta(days=SPACING_DAYS[record.mastery]))


def apply_result(record: Record, correct: bool, hinted: bool, session: int,
                 confidence_rule: bool) -> str:
    """Move one mastery. Returns up, down or flat, for the summary."""
    if not correct:
        record.mastery = max(0, record.mastery - 1)
        return "down"
    if hinted:
        return "flat"                          # helped is not known
    if confidence_rule and record.last_increase_session is not None \
            and session <= record.last_increase_session:
        return "flat"                          # already rose this session
    if record.mastery >= MAX_MASTERY:
        return "flat"
    record.mastery += 1
    record.last_increase_session = session
    return "up"


# --- in exercise reactions ---------------------------------------------------


def answer_reaction(expected: int, given: int | None) -> str | None:
    """Which half of the count went wrong, from the number the child gave."""
    if given is None or given == expected:
        return None
    if given // 10 != expected // 10:
        return REACTION_RECOUNT_TENS
    if given % 10 != expected % 10:
        return REACTION_RECOUNT_UNITS
    return None


def hint_event(level: int) -> str | None:
    """hint_1 names the finger, hint_2 ghosts it, hint_3 shows the whole gesture."""
    if 1 <= level <= len(HINT_EVENTS):
        return HINT_EVENTS[level - 1]
    return None


def pose_reaction(detected_left: int | None, detected_right: int | None,
                  pick: Pick) -> str | None:
    """The same number on both hands is its own mistake, and its own advice."""
    if detected_left is None or detected_right is None:
        return None
    if detected_left == detected_right and pick.left != pick.right:
        return REACTION_SAME_HAND_TWICE
    return None


# --- onboarding --------------------------------------------------------------

ONBOARDING_FACT = "7x8"          # shown as 8 on the left, 7 on the right
ONBOARDING_LEFT, ONBOARDING_RIGHT = 8, 7


def onboarding_steps() -> list[dict[str, Any]]:
    """The first session only: hello, the numbers, then one guided exercise."""
    return [
        {"kind": "greeting", "event": "intro"},
        {"kind": "numbers", "event": "count_fingers", "fingers": list(FACTORS)},
        {"kind": "guided", "event": "guided", "fact": ONBOARDING_FACT,
         "left": ONBOARDING_LEFT, "right": ONBOARDING_RIGHT},
    ]


# --- the scheduler -----------------------------------------------------------


class Scheduler:
    """Picks the next exercise and remembers what happened. Deterministic."""

    def __init__(self, state: LearnerState | None = None,
                 now: datetime | None = None) -> None:
        moment = now or now_utc()
        self.state = state or LearnerState.new(moment)
        self.session = self.state.sessions
        self.started_at = moment
        self.onboarding = False
        self.plan: list[str] = []
        self.opening_fact: str | None = None

        self.history: list[Pick] = []
        self.outcomes: list[Outcome] = []
        self.retry_queue: list[tuple[int, str]] = []      # (serve at index, fact)
        self.new_this_session: list[str] = []
        self.new_results: list[bool] = []
        self.fast_streak = 0
        self.level_bonus = 0
        self.consecutive_errors = 0
        self.served_confidence = False
        self.errors_since_confidence = 0
        self.ending_on_success = False
        self.previous_session_failures: set[str] = set()
        # A course node themes the session: its pairs are served first, only
        # its pairs may open as new material, and it fixes the length. Once the
        # theme has been served the session draws from every table, which is how
        # a two pair node fills five questions without repeating itself.
        self.allowed: set[str] | None = None
        self.orientations: dict[str, list[tuple[int, int]]] = {}
        self.target: int | None = None
        # Kept as a count of what the lesson drew from outside its node, for the
        # trace. It is no longer a budget: widening is the ordinary way a lesson
        # is filled.
        self.outside_reviews = 0

    # -- session lifecycle --

    def start_session(self, now: datetime | None = None,
                      pairs: Sequence[Sequence[int]] | None = None,
                      length: int | None = None) -> dict[str, Any]:
        moment = now or now_utc()
        self.started_at = moment
        self.set_scope(pairs, length)
        self.state.sessions += 1
        self.session = self.state.sessions
        self.onboarding = self.session == 1
        self.previous_session_failures = self._failures_in(self.session - 1)

        last = _parse(self.state.last_session_at)
        another_day = last is not None and last.date() < moment.date()
        if self.session >= 2 and another_day:
            self.plan = list(NEXT_DAY_PLAN)
            self.opening_fact = self._most_fragile(self.session - 1,
                                                   in_scope_only=True)
        else:
            self.plan = []
            self.opening_fact = None
        return {
            "session": self.session,
            "onboarding": self.onboarding,
            "target": self.target,
            "allowed": sorted(self.allowed) if self.allowed else [],
            "steps": onboarding_steps() if self.onboarding else [],
            "plan": list(self.plan),
            "opening_fact": self.opening_fact,
        }

    def set_scope(self, pairs: Sequence[Sequence[int]] | None,
                  length: int | None) -> None:
        """Restrict the session to one course node, or clear the restriction."""
        self.target = length
        if not pairs:
            self.allowed = None
            self.orientations = {}
            return
        self.allowed = set()
        self.orientations = {}
        for pair in pairs:
            left, right = int(pair[0]), int(pair[1])
            key = fact_key(left, right)
            self.allowed.add(key)
            self.orientations.setdefault(key, [])
            if (left, right) not in self.orientations[key]:
                self.orientations[key].append((left, right))

    def _in_scope(self, key: str) -> bool:
        return self.allowed is None or key in self.allowed

    def _failures_in(self, session: int) -> set[str]:
        return {
            key for key, record in self.state.math.items()
            if any(a.session == session and not a.correct for a in record.attempts)
        }

    def _most_fragile(self, session: int, in_scope_only: bool = False) -> str | None:
        """Lowest mastery among the facts touched last time, then most missed.

        The opening exercise of a session asks for the in scope answer only: a
        lesson named after two facts has to open on one of those two facts, and
        the one exercise start check has to ask the fact it is checking.
        """
        touched = [
            key for key, record in self.state.math.items()
            if any(a.session == session for a in record.attempts)
            and (not in_scope_only or self._in_scope(key))
        ]
        if not touched:
            return None
        order = {key: position for position, key in enumerate(DIFFICULTY_ORDER)}
        return min(touched, key=lambda key: (self.state.math[key].mastery,
                                             -self.state.math[key].error_rate,
                                             order.get(key, 99)))

    # -- picking --

    def next_exercise(self, now: datetime | None = None) -> Pick | None:
        """The next exercise, or None when the session is over."""
        moment = now or now_utc()
        stop = self._stop_reason(moment)
        if stop and not self._owes_a_success():
            return None
        if stop:
            # Always end on a success: one mastered fact, then the session ends.
            self.ending_on_success = True
            pick = self._mastered_pick() or self._any_pick()
            if pick is None:
                return None
            pick = Pick(pick.fact, pick.left, pick.right, REACTION_CONFIDENCE,
                        seen=self._met_before(pick.fact))
            self.history.append(pick)
            return pick

        pick = self._choose(moment)
        if pick is None:
            return None
        # Read before the attempt is recorded, so it says what the child knew
        # when the question was asked.
        pick = replace(pick, seen=self._met_before(pick.fact))
        if pick.is_new:
            self.new_this_session.append(pick.fact)
            self.level_bonus = 0              # the step is spent once it is served
        if not self._in_scope(pick.fact):
            self.outside_reviews += 1
        self.history.append(pick)
        return pick

    def _met_before(self, key: str) -> bool:
        """Whether the child has ever attempted this fact.

        Only the history says it. A fact carries the same answer wherever it was
        drawn from, the node or the wider range, so this never looks at scope.
        """
        record = self.state.math.get(key)
        return bool(record and record.seen)

    def _owes_a_success(self) -> bool:
        """Whether one more exercise is owed so the session ends on a success.

        Never inside a course node: its length is fixed and the page scores
        correct out of that length, so a rescue exercise would make three stars
        arithmetically impossible.
        """
        if self.target is not None:
            return False
        return bool(self.outcomes) and not self.outcomes[-1].correct \
            and not self.ending_on_success

    def _choose(self, now: datetime) -> Pick | None:
        index = len(self.history)
        candidates = list(self._candidates(now, index))
        # First pass: a fact this session has not asked yet, so the lesson keeps
        # moving. Second pass: the other orientation of a fact already asked,
        # which is a different question and still counts as variety.
        for fresh in (True, False):
            for candidate, reason, is_new in candidates:
                left, right = self._orientation(candidate)
                if self._allowed(candidate, left, right,
                                 repeat_ok=reason == REACTION_RETRY, fresh=fresh):
                    return Pick(candidate, left, right, reason, is_new)
        # Nothing satisfied the constraints, so relax them rather than stall.
        return self._any_pick()

    def _candidates(self, now: datetime, index: int) -> Iterable[tuple[str, str, bool]]:
        """Every candidate in priority order, best first.

        The node's own pairs are offered at every stage before the wider range,
        so a lesson named "Six and seven" opens on six and seven and only widens
        once its own facts are served or blocked.
        """
        # Two errors in a row preempt everything else: a mastered fact, to break
        # the spiral. The listed order puts this third, after retry and after the
        # due queue, where it could never fire, because a failed fact is queued
        # for retry and a mastery 0 fact is due in the same session, so one of the
        # two always has a candidate. The stop rule that counts errors after a
        # confidence exercise would never trigger either. Handing the child back
        # the fact they just failed twice is also the opposite of the intent.
        if self.consecutive_errors >= ERRORS_BEFORE_CONFIDENCE:
            for key in self._mastered_facts():
                yield key, REACTION_CONFIDENCE, False
            if index > 0:
                for key in self._mastered_facts(wide=True):
                    yield key, REACTION_CONFIDENCE, False

        # The session opens on the most fragile fact of the previous session,
        # and only ever on one that is in scope, so the opening exercise is
        # about the node the child chose.
        if index == 0 and self.opening_fact and self._in_scope(self.opening_fact):
            yield self.opening_fact, REACTION_REVIEW, False

        # 1. a fact queued for retry, served two exercises after the error. This
        # is the one repeat a session is allowed: a wrong fact always comes back.
        for due_index, key in self.retry_queue:
            if index >= due_index:
                yield key, REACTION_RETRY, False

        # 2. the oldest due fact of the node itself
        due = self._due_facts(now)
        for key in due:
            if self._in_scope(key):
                yield key, REACTION_REVIEW, False

        # 3. the next new fact, only once the last two new ones landed first try.
        # New material is the node's alone: the widening below is practice of
        # facts the child has met, never the place where a fact is taught.
        if self._may_open_a_new_fact():
            new = self._next_new_fact()
            if new is not None:
                reason = REACTION_LEVEL_UP if self.level_bonus else REACTION_NEXT_NEW
                yield new, reason, True

        # 4. something half learned in the node, then anything of the node
        for key in self._facts_at_mastery(1, 2):
            yield key, REACTION_REVIEW, False
        for key in fact_order(self.state):
            if self._in_scope(key) and self.state.math.get(key, Record()).seen:
                yield key, REACTION_REVIEW, False

        # 5. the node's own facts are served, so widen to FACTORS, every table
        # the app teaches, and fill the lesson without repeating. There is no
        # unlocking to derive: six to ten is the range for every child, new or
        # not. Never as the opening exercise: a lesson named after two facts
        # opens on those two facts.
        if index > 0:
            for key in due:
                if not self._in_scope(key):
                    yield key, REACTION_REVIEW, False
            for key in self._facts_at_mastery(1, 2, wide=True):
                if not self._in_scope(key):
                    yield key, REACTION_REVIEW, False
            for key in fact_order(self.state):
                if not self._in_scope(key) and self.state.math.get(key, Record()).seen:
                    yield key, REACTION_REVIEW, False
            for key in fact_order(self.state):
                if not self._in_scope(key):
                    yield key, REACTION_REVIEW, False

        # and finally anything of the node, so a session never stalls
        for key in fact_order(self.state):
            if self._in_scope(key):
                yield key, REACTION_REVIEW, False

    def _due_facts(self, now: datetime) -> list[str]:
        due = [key for key, record in self.state.math.items()
               if is_due(record, now, self.session)]
        order = {key: position for position, key in enumerate(DIFFICULTY_ORDER)}
        return sorted(due, key=lambda key: (self._last_seen(key), order.get(key, 99)))

    def _last_seen(self, key: str) -> str:
        record = self.state.math.get(key)
        return record.attempts[-1].at if record and record.attempts else ""

    def _mastered_facts(self, wide: bool = False) -> list[str]:
        """Solid facts of the node, or of every table when the lesson widens.

        The node's own solid facts come first, so the confidence exercise after
        two errors stays on the material the lesson is named after.
        """
        return [key for key in fact_order(self.state)
                if (wide or self._in_scope(key))
                and self.state.math.get(key, Record()).mastery >= MASTERED_FROM]

    def _facts_at_mastery(self, low: int, high: int, wide: bool = False) -> list[str]:
        return [key for key in fact_order(self.state)
                if (wide or self._in_scope(key))
                and low <= self.state.math.get(key, Record()).mastery <= high
                and self.state.math.get(key, Record()).seen]

    def _may_open_a_new_fact(self) -> bool:
        if len(self.new_results) < NEW_FACT_SUCCESSES_REQUIRED:
            return True                       # nothing opened yet, go ahead
        return all(self.new_results[-NEW_FACT_SUCCESSES_REQUIRED:])

    def _next_new_fact(self) -> str | None:
        unseen = [key for key in fact_order(self.state)
                  if self._in_scope(key)
                  and not self.state.math.get(key, Record()).seen]
        if not unseen:
            return None
        return unseen[min(self.level_bonus, len(unseen) - 1)]

    def _mastered_pick(self) -> Pick | None:
        for key in self._mastered_facts():
            left, right = self._orientation(key)
            return Pick(key, left, right, REACTION_CONFIDENCE)
        return None

    def _any_pick(self) -> Pick | None:
        """The relief valve: a session must never stall for want of a candidate.

        It gives the rules up one at a time, in the order they can be spared:
        first a fact this session has not asked, then a question it has not
        asked, then the table that just came up, and last of all the fact that
        was just asked. The node comes before the wider range at every step.
        """
        pool = [key for key in fact_order(self.state) if self._in_scope(key)]
        pool += [key for key in fact_order(self.state) if not self._in_scope(key)]
        for fresh in (True, False):
            for key in pool:
                left, right = self._orientation(key)
                if self._allowed(key, left, right, fresh=fresh):
                    return Pick(key, left, right, REACTION_REVIEW)
        previous = self.history[-1].fact if self.history else None
        for key in pool:
            if key != previous:
                left, right = self._orientation(key)
                return Pick(key, left, right, REACTION_REVIEW)
        return Pick(pool[0], *self._orientation(pool[0]), REACTION_REVIEW) if pool else None

    def _orientation(self, key: str) -> tuple[int, int]:
        """Alternate the two orientations of a fact across its attempts.

        A course node names its orientations explicitly, for example [6, 8] and
        [8, 6], so when one is in scope its list wins over the default pair.
        """
        seen = self.state.math.get(key, Record()).logged
        listed = self.orientations.get(key)
        if listed:
            return listed[seen % len(listed)]
        low, high = factors_of(key)
        if low == high:
            return low, high
        return (low, high) if seen % 2 == 0 else (high, low)

    def _served_facts(self) -> set[str]:
        return {pick.fact for pick in self.history}

    def _served_poses(self) -> set[tuple[int, int]]:
        """The questions already asked this session, hands and all: 6 on the
        left with 7 on the right is not the question 7 on the left with 6 on
        the right, and asking both is variety rather than repetition."""
        return {(pick.left, pick.right) for pick in self.history}

    def _is_node_fact(self, key: str) -> bool:
        return self.allowed is not None and key in self.allowed

    def _allowed(self, key: str, left: int, right: int,
                 repeat_ok: bool = False, fresh: bool = True) -> bool:
        """Whether this question may be asked now.

        repeat_ok is the retry queue: a fact the child got wrong comes back
        whatever else has been asked, and it is the only repeat a session gets.
        fresh is the first pass of _choose, which wants a fact this session has
        not touched at all; the node's own pairs are exempt, because showing
        their other orientation is the lesson doing its job.
        """
        if self.history and self.history[-1].fact == key:
            return False                      # never the same fact twice in a row
        if self.history and (set(factors_of(self.history[-1].fact))
                             & set(factors_of(key))):
            return False                      # never the same table twice in a row
        if repeat_ok:
            return True
        if (left, right) in self._served_poses():
            return False                      # never the same question twice
        if fresh and key in self._served_facts() and not self._is_node_fact(key):
            return False
        return True

    # -- recording --

    def record(self, outcome: Outcome, now: datetime | None = None) -> dict[str, Any]:
        moment = now or now_utc()
        self.outcomes.append(outcome)
        hinted = outcome.hint_level > 0
        attempt = Attempt(at=_iso(moment) or "", correct=outcome.correct,
                          hinted=hinted, response_time=outcome.response_time,
                          session=self.session)

        pose = self.state.pose_record(pose_key(outcome.left, outcome.right))
        fact = self.state.fact(outcome.fact)
        pose.log(attempt)
        fact.log(attempt)

        # A pose error only moves pose mastery, a math error only moves math.
        pose_moved = "flat"
        math_moved = "flat"
        if outcome.pose_error:
            pose_moved = apply_result(pose, False, hinted, self.session, False)
        elif outcome.correct:
            pose_moved = apply_result(pose, True, hinted, self.session, False)
        if outcome.math_error:
            math_moved = apply_result(fact, False, hinted, self.session, True)
        elif outcome.correct:
            math_moved = apply_result(fact, True, hinted, self.session, True)
        schedule_next(fact, moment, self.session)

        self._count_errors(outcome)
        # The index of the exercise being recorded, not the one after it: a retry
        # due "two exercises later" has to land on the third, not the fourth.
        index = len(self.history) - 1
        if outcome.correct:
            self.consecutive_errors = 0
            if not hinted and outcome.response_time <= FAST_SUCCESS_S:
                self.fast_streak += 1
                if self.fast_streak >= FAST_SUCCESSES_FOR_LEVEL_UP:
                    self.fast_streak = 0
                    self.level_bonus = LEVEL_UP_STEPS
            else:
                self.fast_streak = 0
        else:
            self.fast_streak = 0
            self.consecutive_errors += 1
            if self.served_confidence:
                self.errors_since_confidence += 1
            self.retry_queue.append((index + RETRY_AFTER_EXERCISES, outcome.fact))

        self.retry_queue = [(at, key) for at, key in self.retry_queue
                            if key != outcome.fact or at > index]
        if self.history and self.history[-1].reason == REACTION_CONFIDENCE:
            self.served_confidence = True
        if outcome.fact in self.new_this_session and \
                len(self.new_results) < len(self.new_this_session):
            self.new_results.append(outcome.correct and not hinted)

        return {"pose": pose_moved, "math": math_moved,
                "pose_mastery": pose.mastery, "math_mastery": fact.mastery}

    def _count_errors(self, outcome: Outcome) -> None:
        profile = self.state.error_profile
        if outcome.hint_level:
            profile["hint_level_needed"] += outcome.hint_level
        if outcome.pose_error:
            kind = POSE_ERROR_KINDS.get(outcome.wrong_hand or "")
            if kind is not None:
                profile[kind] += 1
        if outcome.math_error and outcome.given is not None:
            reaction = answer_reaction(outcome.left * outcome.right, outcome.given)
            if reaction == REACTION_RECOUNT_TENS:
                profile["tens_error"] += 1
            elif reaction == REACTION_RECOUNT_UNITS:
                profile["units_error"] += 1

    def note_pose_error(self, kind: str) -> None:
        """wrong_left, wrong_right or no_contact, straight from the engine event."""
        if kind in self.state.error_profile:
            self.state.error_profile[kind] += 1

    # -- stopping --

    def _stop_reason(self, now: datetime) -> str | None:
        done = len(self.outcomes)
        # A course node fixes its own length and overrides the open session
        # shape: five exercises for a lesson, eight for a boss.
        if self.target is not None:
            if done >= self.target:
                return REACTION_END_SUCCESS
            if (now - self.started_at).total_seconds() >= SESSION_HARD_STOP_S:
                return REACTION_END_TIRED
            return None
        if (now - self.started_at).total_seconds() >= SESSION_HARD_STOP_S:
            return REACTION_END_TIRED
        if done >= SESSION_MAX_EXERCISES:
            return REACTION_END_SUCCESS
        if done < SESSION_MIN_EXERCISES:
            return None
        if self.served_confidence and self.errors_since_confidence >= CONSECUTIVE_ERRORS_TO_STOP:
            return REACTION_END_TIRED
        if self._slowing_down():
            return REACTION_END_TIRED
        if self.plan and done >= len(self.plan):
            return REACTION_END_SUCCESS
        # Past the minimum and still going well: keep playing up to the maximum.
        return None

    def _slowing_down(self) -> bool:
        times = [o.response_time for o in self.outcomes if o.response_time > 0]
        if len(times) < 6:
            return False
        early = sum(times[:3]) / 3
        late = sum(times[-3:]) / 3
        return early > 0 and late >= SLOW_FACTOR * early

    def end_session(self, now: datetime | None = None) -> SessionSummary:
        moment = now or now_utc()
        reason = self._stop_reason(moment) or REACTION_END_SUCCESS
        if self.outcomes and not self.outcomes[-1].correct:
            reason = REACTION_END_TIRED
        self.state.last_session_at = _iso(moment)

        solid = [key for key in self.state.math
                 if self.state.math[key].mastery >= MASTERED_FROM
                 and any(a.session == self.session for a in self.state.math[key].attempts)]
        weak = [o.fact for o in self.outcomes if not o.correct]
        tomorrow = weak[-1] if weak else (self._most_fragile(self.session) or None)
        return SessionSummary(reason=reason, opened=list(self.new_this_session),
                              became_solid=solid, tomorrow=tomorrow,
                              exercises=len(self.outcomes))

    # -- metrics --

    def metrics(self) -> dict[str, Any]:
        total = len(self.outcomes)
        first_try = sum(1 for o in self.outcomes if o.correct and not o.hint_level)
        times = [o.response_time for o in self.outcomes if o.response_time > 0]
        retained = sorted(
            key for key in self.previous_session_failures
            if any(o.fact == key and o.correct and not o.hint_level for o in self.outcomes)
        )
        return {
            "session": self.session,
            "exercises": total,
            "first_try_success": first_try,
            "first_try_rate": round(first_try / total, 3) if total else 0.0,
            "hint_level_needed": sum(o.hint_level for o in self.outcomes),
            "pose_errors": sum(1 for o in self.outcomes if o.pose_error),
            "math_errors": sum(1 for o in self.outcomes if o.math_error),
            "response_time": round(sum(times) / len(times), 2) if times else 0.0,
            "retention": retained,
            "retention_rate": (round(len(retained) / len(self.previous_session_failures), 3)
                               if self.previous_session_failures else None),
        }


# --- demo mode ---------------------------------------------------------------

DEFAULT_SCENARIO = "demo/scenario.json"


class ScriptedScheduler(Scheduler):
    """The same interface, but the sequence comes from a file.

    The three minute demo has to run the same way every time, on a stage, with
    the same beats. Recording and metrics stay the real ones, so the demo still
    exercises the code the lesson uses.
    """

    def __init__(self, scenario: dict[str, Any], state: LearnerState | None = None,
                 now: datetime | None = None) -> None:
        super().__init__(state=state, now=now)
        self.scenario = scenario
        self.script = [
            Pick(fact=str(step["fact"]), left=int(step["left"]), right=int(step["right"]),
                 reason=str(step.get("reason", REACTION_REVIEW)),
                 is_new=bool(step.get("is_new", False)),
                 # The script is the script, down to the wording: a scripted
                 # step is served as written, so seen stays at its default.
                 seen=bool(step.get("seen", True)))
            for step in scenario.get("exercises", [])
        ]
        self.cursor = 0

    @classmethod
    def load(cls, path: Any, state: LearnerState | None = None,
             now: datetime | None = None) -> "ScriptedScheduler":
        import json
        from pathlib import Path

        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(raw, state=state, now=now)

    def start_session(self, now: datetime | None = None,
                      pairs: Sequence[Sequence[int]] | None = None,
                      length: int | None = None) -> dict[str, Any]:
        # The scope is accepted and recorded, but the script is the script: a
        # demo that reshuffled itself to fit the node would not be a demo.
        started = super().start_session(now, pairs=pairs, length=length)
        # The demo never plays the onboarding, whatever the stored state says.
        self.onboarding = bool(self.scenario.get("onboarding", False))
        started["onboarding"] = self.onboarding
        started["steps"] = onboarding_steps() if self.onboarding else []
        started["plan"] = [step.fact for step in self.script]
        started["opening_fact"] = self.script[0].fact if self.script else None
        return started

    def next_exercise(self, now: datetime | None = None) -> Pick | None:
        if self.cursor >= len(self.script):
            return None
        pick = self.script[self.cursor]
        self.cursor += 1
        if pick.is_new:
            self.new_this_session.append(pick.fact)
        self.history.append(pick)
        return pick

    def end_session(self, now: datetime | None = None) -> SessionSummary:
        summary = super().end_session(now)
        end = self.scenario.get("end") or {}
        return SessionSummary(
            reason=str(end.get("reason", summary.reason)),
            opened=list(end.get("opened", summary.opened)),
            became_solid=list(end.get("became_solid", summary.became_solid)),
            tomorrow=end.get("tomorrow", summary.tomorrow),
            exercises=len(self.outcomes),
        )
