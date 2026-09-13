"""The live tutor: when Tally speaks, what she shows, and what counts as a mistake.

docs/tutor_contract.md is the contract this file implements. It is a pure state
machine: no camera, no websocket, no DOM, no wall clock of its own. Time is
injected, observations are fed in, decisions come back, so a whole lesson replays
in a test in microseconds.

Not to be confused with lesson/tutor.py (W&B Inference), which phrases a line.
This file decides whether a line is said at all. They never import each other.

Four principles are code here, not parameters:

  movement means silence          a moving child is working, so no clock for a
                                  spoken intervention advances while the
                                  fingertips move
  a stable state starts a timer   every trigger is measured from the moment the
                                  situation became what it is
  a state change resets the timer the child fixing one thing never inherits the
                                  clock of the thing they fixed
  escalate on persistence only    the ladder climbs one step, and only when the
                                  same problem is still there

Every timing, count and factor comes from lesson/tutor_params.json. The constants
below are shapes of the contract, not policy: changing one changes what the log
means, not how patient Tally is.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from classifier.schema import GestureState
from lesson.scheduler import now_utc

REPO = Path(__file__).resolve().parents[1]
PARAMS_PATH = REPO / "lesson" / "tutor_params.json"
LINES_PATH = REPO / "lesson" / "tally_lines.json"
LOG_PATH = REPO / "data" / "tutor_log.jsonl"

# --- contract constants, section 2.3, 3.2, 5 and 6 of the contract -----------
# These are the shape of the log and of the settled decisions, not policy dials.

INTERVENTION_FOLLOW_UP_S = 5.0      # how long an intervention line waits to be written
MOVEMENT_LOOKBACK_S = 0.5           # "was already moving" window before a delivery
TTS_RESUME_S = 0.5                  # pedagogical timers resume this long after tts end
SUPPORTIVE_VISUAL_S = 3.0           # finger numbers kept up at the start of a supportive exercise
JITTER_WINDOW_S = 2.0               # length of the still hands measurement
JITTER_MIN_PAIRS = 20               # fewer than this and the measurement is discarded
JITTER_PERCENTILE = 0.9
# The motion measure app/server.py hands over is a fingertip displacement divided by
# palm size, so it is in palm widths, not in image fractions. A palm is about a tenth
# of the frame, so the 0.03 of a frame the contract names is 0.3 of a palm. The
# measured jitter shares the unit, so only this floor had to be converted.
MOTION_FLOOR = 0.3                  # palm widths over the 400 ms window
MOTION_MULTIPLIER = 4.0
# app/server.py measures motion in palm widths. When it hands over no measure,
# the fallback below works from fingertip coordinates, which are frame fractions,
# so it divides by a nominal palm to land in the same unit.
NOMINAL_PALM = 0.1
# A hand held to the camera spans about one and a half palms from the thumb tip
# to the little finger tip. It is how the palm is recovered from the fingertips
# alone, until app/server.py sends the measurement it already has.
TIP_SPREAD_PALMS = 1.5
SMOOTHING_OLD = 0.8
SMOOTHING_NEW = 0.2
SCHEDULE_GLOBALS_ONLY = 4           # tutor_observations 0..4: factors forced to 1.0
SCHEDULE_NARROW = 14                # 5..14: at most 15 percent deviation
SCHEDULE_NARROW_DEVIATION = 0.15
STABILITY_NUMERATOR = 5             # "at least 10 of the last 12 frames" as a fraction
STABILITY_DENOMINATOR = 6
DECISIONS_PER_SECOND = 2
FPS_EPSILON = 1e-6                  # a camera running at exactly fps keeps every frame
AUTONOMOUS_FOR_INDEPENDENT = 3
HARD_FOR_SUPPORTIVE = 2
RESCUE_LEVEL = 4                    # the top of the ladder, outside the nag budget
WRONG_ANSWERS_FOR_RESCUE = 2        # wrong numbers on one exercise that reach it
EARLY_STOP_MIN_EXERCISES = 3
EARLY_STOP_CONSECUTIVE = 2
EARLY_STOP_ERRORS = 2

# --- the nine states ---------------------------------------------------------

PROMPTING = "PROMPTING"
WORKING = "WORKING"
WRONG_POSE = "WRONG_POSE"
POSE_READY = "POSE_READY"
CHECK_ANSWER = "CHECK_ANSWER"
ANSWER_RETRY = "ANSWER_RETRY"
VISIBILITY_RECOVERY = "VISIBILITY_RECOVERY"
SUCCESS = "SUCCESS"
PAUSED = "PAUSED"

STATES = (PROMPTING, WORKING, WRONG_POSE, POSE_READY, CHECK_ANSWER,
          ANSWER_RETRY, VISIBILITY_RECOVERY, SUCCESS, PAUSED)

# --- the three modes ---------------------------------------------------------

SUPPORTIVE = "supportive"
NORMAL = "normal"
INDEPENDENT = "independent"

# --- what the tutor thinks it is looking at ---------------------------------

SIT_NO_HANDS = "no_hands"
SIT_ONE_HAND = "one_hand"
SIT_UNKNOWN = "unknown"
SIT_WRONG = "wrong_finger"
SIT_NO_CONTACT = "no_contact"
SIT_SWAPPED = "hands_swapped"
SIT_CORRECT = "correct"

POSE_PROBLEMS = (SIT_WRONG, SIT_NO_CONTACT, SIT_SWAPPED)
# A visibility problem is the camera's, never the child's: its own budget, and
# it can never cost first try. Two hands in frame that the classifier cannot
# read is not one of these: nothing is hidden, the pose is simply not made yet.
VISIBILITY_PROBLEMS = (SIT_NO_HANDS, SIT_ONE_HAND)

# --- reading the pose --------------------------------------------------------
#
# The hands are described to the tutor twice over: the classifier names the two
# numbers it believes it can see, and the landmarks say where the ten fingertips
# actually are. A reading is what those two say together, and it is the input of
# the decision table below.
#
# Distances are in hand scales, the classifier's own unit, so contact_ratio can
# be compared against them as written.

READ_HANDS_GONE = "hands_gone"
READ_CORRECT = "correct"
READ_WRONG_PAIR_HELD = "wrong_pair_held"
READ_WRONG_PAIR = "wrong_pair"
READ_ONE_HAND_SEARCHING = "one_hand_searching"
READ_CLOSING_IN = "closing_in"
READ_SEARCHING = "searching"
READ_UNCLEAR = "unclear"

# The six tutor_visual kinds of contract section 1.3, plus the one this change
# adds. closing_in carries the two fingertips the child is bringing together, so
# the page can put a green dot on each. A page that does not know the kind still
# draws the two expected tips green, because any non null tutor_visual lights
# the tips the engine already marks.
VIS_PULSE_FINGER = "pulse_finger"
VIS_CORRECTION = "correction"
VIS_GHOST = "ghost"
VIS_RESCUE_CARD = "rescue_card"
VIS_PLACEMENT_ZONES = "placement_zones"
VIS_FINGER_NUMBERS = "finger_numbers"
VIS_CLOSING_IN = "closing_in"

# contact_ratio is the fingertip distance at which two fingertips count as
# touching, as a fraction of a palm. It comes from the parameter file, through
# RATIO_PARAMS, and _update_contact already reads the same key: the table and
# the contact check mean the same thing by touching, to the same number.
# "close but not touching" is twice that distance, contract wording.
CLOSING_MULTIPLE = 2.0

# --- decision log reasons, the V0 vocabulary of contract section 3.1 ---------

REASON_PROMPT_END = "prompt_end"
REASON_WITHIN_GRACE = "within_grace"
REASON_WRONG_POSE_HELD = "wrong_pose_held"
REASON_IDLE = "idle"
REASON_POSE_READY = "pose_ready"
REASON_ANSWER_GIVEN = "answer_given"
REASON_ANSWER_WRONG = "answer_wrong"
REASON_HANDS_LOST = "hands_lost"
REASON_ONE_HAND = "one_hand"
REASON_CHILD_MOVING = "child_moving"
REASON_CHILD_SPEAKING = "child_speaking"
REASON_MIN_VERBAL_GAP = "min_verbal_gap"
REASON_BUDGET_SPENT = "budget_spent"
REASON_CHILD_ASKED = "child_asked"
REASON_NO_ENGAGEMENT = "no_engagement"

# --- params ------------------------------------------------------------------

DURATION_PARAMS = (
    "hands_visible_stable", "pose_stable", "initial_silence", "idle_nudge",
    "wrong_pose_prompt", "wrong_pose_error_after_help", "correct_pose_nudge",
    "hint_2_delay", "rescue_delay", "no_hands_visual", "no_hands_voice",
    "one_hand_voice", "min_verbal_gap", "post_movement_silence",
    "no_engagement_pause", "pose_memory",
)
COUNT_PARAMS = ("max_unsolicited_verbal", "max_visibility_reminders")
# Shape of the world, not patience: how close two fingertips have to be, as a
# fraction of a palm, before the tutor believes they are touching. Read as
# written and clamped, like every other measurement.
RATIO_PARAMS = ("contact_ratio",)
FACTOR_PARAMS = ("supportive_mode_factor", "independent_mode_factor")
# Latency parameters. They are how fast the machine answers, not how patient
# Tally is, so no learner factor and no mode factor ever scales them: they are
# read as written and only clamped to their bounds.
FRAME_PARAMS = ("pose_confirm_frames",)
# pose_ready_delay_ms is the silence between the acknowledgement of a confirmed
# pose and the canonical cue that follows it: the length of a two beat line, so
# it is read as written like the other delivery timings.
# recovery_grace_ms is the hush after the hands come back into frame. It is the
# machine apologising for its own blind spell, not patience with a child, so it
# is the same for every child and in every mode: read as written, clamped only.
# visibility_prompt_ms is how long the opening waits, with the placement zones
# up and nothing else, before Tally calls the hands into frame.
# gate_step_pause_ms is the page's: the beat it holds after a gate step passes
# so the child hears the confirmation before the next instruction. The tutor
# reads it only to hand it over, bounded like everything else.
# post_line_grace_ms is the quiet after any line Tally says, the floor under
# min_verbal_gap. The two are measured from the same moment, the last line, so
# the quiet is the longer of them and never the sum.
# visibility_grace_ms is how long both hands, or the missing one, have to be
# out of frame without a break before the tutor believes they are gone at all.
# A tracker dropping a frame or two is the camera blinking, not a child
# leaving: within the grace nothing is said, nothing is drawn and no visibility
# situation is entered. It is the mirror of recovery_grace_ms: that one is the
# hush after the hands come back, this one is the doubt before they are
# declared missing. Camera latency, so it is read as written and only clamped.
MS_PARAMS = ("pose_confirm_ms", "ack_delay_ms", "check_step_min_ms",
             "answer_first_number_ms", "pose_ready_delay_ms", "recovery_grace_ms",
             "visibility_prompt_ms", "visibility_grace_ms", "gate_step_pause_ms",
             "post_line_grace_ms")
LATENCY_PARAMS = FRAME_PARAMS + MS_PARAMS
# The success beat, in milliseconds: how long the celebration itself runs, and
# how long the silence after it lasts before the next exercise is announced.
# They are the length of a piece of choreography the page plays, not the
# patience of Tally, so like the latency keys no learner factor and no mode
# factor ever scales them. Only the clamp applies.
BEAT_PARAMS = ("success_beat_ms", "next_pause_ms")
REQUIRED_PARAMS = ((("fps",) + DURATION_PARAMS + COUNT_PARAMS + FACTOR_PARAMS)
                   + LATENCY_PARAMS + BEAT_PARAMS + RATIO_PARAMS)
MS_PER_S = 1000.0

LINE_KEYS = (
    "launch", "hesitation_1", "hesitation_2", "visibility_none",
    "visibility_one", "visibility_keep", "wrong_right", "wrong_left",
    "wrong_both", "not_touching", "show", "pose_ready", "count_tens",
    "multiply_above", "wrong_answer_1", "wrong_answer_2", "wrong_answer_3",
    "rescue", "success", "autonomous",
)
# One line per wrong answer on the same exercise, the last one repeating.
WRONG_ANSWER_KEYS = ("wrong_answer_1", "wrong_answer_2", "wrong_answer_3")
# The two beats of a confirmed pose: the acknowledgement, said at once and
# allowed to cut, then the canonical cue pose_ready_delay_ms later. The
# acknowledgement is not one of the twenty, so a file without it is not an
# error: the beat then plays the canonical line alone, at once, as before.
ACK_LINE_KEY = "pose_ack"
POSE_READY_KEY = "pose_ready"
# The first of the two lines of a gentle recovery: the hands are back in frame
# and Tally says so before asking for anything. Optional like the
# acknowledgement, so a file without it re-prompts the exercise and no more.
RECOVERY_LINE_KEY = "hands_back"
LAUNCH_KEY = "launch"
# The opening call for hands that are not in frame yet. It is a child's line,
# not the reminder of a camera failure, so it has its own key and its own clock.
HANDS_PROMPT_KEY = "hands_come_here"
# Lines the page says and the tutor never renders: the ready gate, the home and
# path bubbles, the finish card, the profile. They live in the same file because
# Tally has one voice, and the page reads them from it rather than writing a
# sentence of its own.
PAGE_LINE_KEYS = (
    "gate_ready", "home_greeting", "home_greeting_plain", "home_camera_live",
    "showcase_unit_1", "showcase_unit_2", "showcase_unit_3",
    "path_next", "path_chest", "path_boss", "path_all_done", "path_locked",
    "show_both_hands", "gate_hands_ok", "gate_pose", "gate_pose_ok",
    "gate_banner", "gate_path_open", "link_trouble",
    "finish_need", "finish_unlocked", "finish_more_stars", "level_up_card",
    "profile_top_level",
)
# Lines the server says when perception is gone or the tab is not the one
# playing. They are Tally's voice too, so they live in the line file and not in
# app/server.py, where two of them used to be typed out.
SERVER_LINE_KEYS = ("camera_none", "camera_lost", "already_playing")
# The lines of the older lesson path, lesson/tally.py, which keeps no text of
# its own. They live in the same file because Tally has one voice and one place
# to keep it; the live tutor never says them and never has to have them.
LESSON_LINE_KEYS = (
    "intro", "exercise_shown", "unknown_gesture", "hands_swapped",
    "wrong_left_finger", "wrong_left_finger_detailed",
    "wrong_right_finger", "wrong_right_finger_detailed",
    "answer_correct", "count_fingers", "guided",
    "retry", "retry_detailed", "review", "review_detailed",
    "confidence", "confidence_detailed", "next_new", "next_new_detailed",
    "level_up", "hint_1", "hint_1_detailed", "hint_3",
    "same_hand_twice", "same_hand_twice_detailed",
    "end_success", "end_success_detailed", "end_tired",
)
# Lines the file may carry and does not have to. "next_one" closes the success
# beat, and lesson/tally_lines.json does not have it yet: until it does the beat
# hands the page no second line at all rather than a line nobody wrote.
OPTIONAL_LINE_KEYS = (("next_one", ACK_LINE_KEY, RECOVERY_LINE_KEY,
                       HANDS_PROMPT_KEY) + PAGE_LINE_KEYS + SERVER_LINE_KEYS
                      + LESSON_LINE_KEYS)
NEXT_LINE_KEY = "next_one"

# The success beat. The page plays the parts in this order inside success_ms,
# then keeps pause_ms of silence, then says next_line and fades the new
# exercise in. The tutor calls it; the page draws it.
BEAT_SUCCESS = "success"
BEAT_PARTS = ("line", "halo", "stars", "counter")

# A line the page could not speak, reported back from the page. The vocabulary
# of why, as one machine token. Anything else the page sends is logged as
# "unknown" with the raw text kept beside it: this is diagnostic data about
# things going wrong, so it is never the moment to throw a report away.
DROP_REPLACED = "replaced"
DROP_QUEUE_FULL = "queue_full"
DROP_INTERRUPTED = "interrupted"
DROP_STALE = "stale"
DROP_UNKNOWN = "unknown"
DROP_REASONS = (DROP_REPLACED, DROP_QUEUE_FULL, DROP_INTERRUPTED, DROP_STALE,
                DROP_UNKNOWN)
# The page's own words for the same things. web/course/app.js names a drop by
# what its queue did; the log keeps one token per cause, so the page's names
# are read here rather than logged as unknown.
DROP_SYNONYMS = {
    "replaced_by_newer_of_same_kind": DROP_REPLACED,
    "no_longer_true": DROP_STALE,
    "cut": DROP_INTERRUPTED,
}
# How much of a malformed reason or line is kept in the log.
DROP_TEXT_LIMIT = 120


class ParamsError(ValueError):
    """A parameter file the tutor refuses to start on."""


@dataclass(frozen=True)
class TutorParams:
    """The loaded, validated parameter file. Frozen: there is no reload."""

    values: Mapping[str, float]
    bounds: Mapping[str, tuple[float, float]]
    invariants: tuple[str, ...]
    params_version: str

    def bound(self, key: str) -> tuple[float, float]:
        return self.bounds[key]


def _as_number(key: str, raw: Any) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ParamsError(f"{key}: expected a number, got {raw!r}")
    return float(raw)


def load_params(path: Path | str = PARAMS_PATH) -> TutorParams:
    """Read the parameter file, or refuse to start.

    A value outside its bounds is a startup error naming the key and the bounds,
    never a silent clamp: a silently clamped file is a policy nobody chose.
    """
    text = Path(path).read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParamsError(f"{path}: not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ParamsError(f"{path}: expected an object at the top level")
    for block in ("global", "bounds", "invariants"):
        if block not in raw:
            raise ParamsError(f"{path}: missing the {block} block")
    values_raw = raw["global"]
    bounds_raw = raw["bounds"]
    if not isinstance(values_raw, dict) or not isinstance(bounds_raw, dict):
        raise ParamsError(f"{path}: global and bounds must both be objects")

    version = hashlib.sha256(
        json.dumps({"global": values_raw, "bounds": bounds_raw},
                   sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]

    values: dict[str, float] = {}
    bounds: dict[str, tuple[float, float]] = {}
    for key in REQUIRED_PARAMS:
        if key not in values_raw:
            raise ParamsError(f"global.{key} is missing")
        if key not in bounds_raw:
            raise ParamsError(f"bounds.{key} is missing")
        pair = bounds_raw[key]
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ParamsError(f"bounds.{key}: expected [min, max], got {pair!r}")
        low = _as_number(f"bounds.{key}[0]", pair[0])
        high = _as_number(f"bounds.{key}[1]", pair[1])
        if low > high:
            raise ParamsError(f"bounds.{key}: min {low} is above max {high}")
        value = _as_number(f"global.{key}", values_raw[key])
        if value < low or value > high:
            raise ParamsError(
                f"global.{key} = {value} is outside its bounds [{low}, {high}]"
            )
        # The clamp is a no-op after the check above. It stays because the same
        # rule runs on every runtime product in effective().
        value = min(max(value, low), high)
        if key == "fps" or key in COUNT_PARAMS or key in FRAME_PARAMS:
            values[key] = int(round(value))
            bounds[key] = (int(round(low)), int(round(high)))
        else:
            values[key] = value
            bounds[key] = (low, high)

    invariants = raw["invariants"]
    if not isinstance(invariants, list) or not all(isinstance(s, str) for s in invariants):
        raise ParamsError(f"{path}: invariants must be a list of strings")
    return TutorParams(
        values=MappingProxyType(values),
        bounds=MappingProxyType(bounds),
        invariants=tuple(invariants),
        params_version=version,
    )


def load_lines(path: Path | str = LINES_PATH) -> Mapping[str, str]:
    """Read Tally's lines. Every key is required and no other key is allowed."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ParamsError(f"{path}: expected an object of situation to line")
    missing = [key for key in LINE_KEYS if key not in raw]
    if missing:
        raise ParamsError(f"{path}: missing lines: {', '.join(sorted(missing))}")
    known = set(LINE_KEYS) | set(OPTIONAL_LINE_KEYS)
    extra = [key for key in raw if key not in known]
    if extra:
        raise ParamsError(f"{path}: unknown lines: {', '.join(sorted(extra))}")
    present = tuple(LINE_KEYS) + tuple(key for key in OPTIONAL_LINE_KEYS
                                       if key in raw)
    for key in present:
        if not isinstance(raw[key], str) or not raw[key].strip():
            raise ParamsError(f"{path}: line {key} is empty")
    return MappingProxyType({key: raw[key] for key in present})


# --- what goes in and what comes out ----------------------------------------


@dataclass(frozen=True)
class Observation:
    """One perception window, as app/server.py already has it in hand."""

    gesture: GestureState | None = None
    fingers: Sequence[Mapping[str, Any]] = ()
    hands_seen: int = 0
    hint: Mapping[str, Any] | None = None
    motion: float | None = None
    # Palm width in the same units as the fingertip coordinates, which is the
    # frame fraction app/server.py already works in. It is what makes a
    # fingertip distance mean something at any distance from the camera. When
    # the server does not send it the tutor estimates it from the fingertips
    # themselves, which is good enough to tell a touch from ten centimetres.
    palm: float | None = None


@dataclass(frozen=True)
class PoseReading:
    """What the hands are saying, read from the landmarks and the classifier.

    gap is the distance between the two fingertips the reading is about, in hand
    scales, or None when the landmarks gave nothing usable. pair is those two
    fingertips. hand, shows and wants describe the hand that is still looking:
    which hand it is, the number it is holding and the number the exercise asks
    of it.
    """

    name: str = READ_UNCLEAR
    gap: float | None = None
    pair: tuple[tuple[str, int], tuple[str, int]] | None = None
    hand: str | None = None
    shows: int | None = None
    wants: int | None = None

    def as_log(self) -> dict[str, Any]:
        """The reading as the loop reads it back, on the decision event."""
        aid = POSE_TABLE[self.name]
        return {
            "name": self.name,
            "gap": None if self.gap is None else round(self.gap, 2),
            "pair": None if self.pair is None else
                    [{"hand": hand, "finger": finger} for hand, finger in self.pair],
            "hand": self.hand,
            "level": aid.level,
            "visual": aid.visual,
        }


@dataclass(frozen=True)
class Aid:
    """One row of the decision table: what a reading asks the tutor to do.

    level    the step of the ladder this reading asks for, L0 to L4. L0 asks
             for no step at all. It is what the reading asks of the table, never
             a cap on the clocks: they stay the ceiling and climb past it.
    visual   the drawing the reading asks for on top of the step, or None to
             leave the level's own drawing alone. The ladder owns what each
             level draws, L1 the numbers, L2 the colour, L3 the ghost; the
             reading only says which hand it goes on, and adds the one drawing
             the ladder has no level for, the two dots of a child closing in.
    at_once  whether the step may be taken at the level named instead of one
             step at a time. Only an unambiguous reading earns it: a pair
             already touching does not need the numbers on every fingertip
             first, it needs to know which finger is wrong.
    after    the params key whose clock must have run on this reading before the
             step is taken, or None when the reading is its own trigger.

    No row speaks. What is said at a level, and whether anything is said at all,
    stays with _ladder_line: L1 and L2 are shown, L3 is the first with a voice.
    """

    level: int
    visual: str | None
    at_once: bool = False
    after: str | None = None


# The decision table. One row per reading, in the order _read_pose tries them,
# and the only place that says which aid answers which pose.
#
#   reading             what the hands are saying          the aid
#   hands_gone          a hand has drifted out of frame    the zones, then the come here line
#   correct             the pose is made                   nothing: the acknowledgement owns this moment
#   wrong_pair_held     the same wrong pair still touching the ghost move, and the voice with it
#                       hint_2_delay after the colours
#   wrong_pair          two fingertips touching, the       the colours, at once, and no voice yet
#                       numbers wrong
#   one_hand_searching  one hand on its number, the other  the colours on the searching hand,
#                       still looking                      the hand that is right left alone
#   closing_in          the two fingertips the exercise    a green dot on each of the two, and
#                       asks for, within twice the         nothing said
#                       contact distance
#   searching           nothing within contact distance,   the numbers on every fingertip
#                       the child does not know which
#                       fingers
#   unclear             the landmarks say nothing usable   nothing: the clocks decide alone
#
# The clocks stay the ceiling above all of it: a reading the table gives no aid
# for still escalates when its own clock runs out, in _consider below.
POSE_TABLE: Mapping[str, Aid] = MappingProxyType({
    READ_HANDS_GONE: Aid(level=1, visual=VIS_PLACEMENT_ZONES,
                         after="no_hands_visual"),
    READ_CORRECT: Aid(level=0, visual=None),
    READ_WRONG_PAIR_HELD: Aid(level=3, visual=VIS_GHOST),
    READ_WRONG_PAIR: Aid(level=2, visual=VIS_CORRECTION, at_once=True,
                         after="wrong_pose_prompt"),
    READ_ONE_HAND_SEARCHING: Aid(level=2, visual=VIS_CORRECTION,
                                 after="wrong_pose_prompt"),
    READ_CLOSING_IN: Aid(level=0, visual=VIS_CLOSING_IN),
    READ_SEARCHING: Aid(level=1, visual=VIS_FINGER_NUMBERS,
                        after="wrong_pose_prompt"),
    READ_UNCLEAR: Aid(level=0, visual=None),
})


# --- the landmarks, as geometry ---------------------------------------------


def tip_positions(fingers: Sequence[Mapping[str, Any]]
                  ) -> dict[str, dict[int, tuple[float, float]]]:
    """The fingertips app/server.py sends, keyed by hand and finger number."""
    out: dict[str, dict[int, tuple[float, float]]] = {}
    for finger in fingers or ():
        hand = finger.get("hand")
        if hand not in ("left", "right"):
            continue
        try:
            number = int(finger.get("number"))
            point = (float(finger.get("x")), float(finger.get("y")))
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in point):
            continue
        out.setdefault(str(hand), {})[number] = point
    return out


def tip_gap(tips: Mapping[str, Mapping[int, tuple[float, float]]], palm: float,
            left: int, right: int) -> float | None:
    """The distance between two named fingertips, in palms."""
    here = tips.get("left", {}).get(left)
    there = tips.get("right", {}).get(right)
    if here is None or there is None or not palm > 0.0:
        return None
    return math.dist(here, there) / palm


def nearest_gap(tips: Mapping[str, Mapping[int, tuple[float, float]]], palm: float
                ) -> tuple[float, tuple[tuple[str, int], tuple[str, int]]] | None:
    """The closest left fingertip to right fingertip pair, in palms."""
    if not palm > 0.0:
        return None
    best: tuple[float, tuple[tuple[str, int], tuple[str, int]]] | None = None
    for left, here in tips.get("left", {}).items():
        for right, there in tips.get("right", {}).items():
            gap = math.dist(here, there) / palm
            if best is None or gap < best[0]:
                best = (gap, (("left", left), ("right", right)))
    return best


@dataclass(frozen=True)
class Decision:
    """The ten fields of the state message, as of now.

    tutor_line_cuts is the ninth and the only one about delivery rather than
    pedagogy: true means this line may interrupt whatever is being spoken
    instead of queueing behind it. Only the acknowledgement of a confirmed pose
    ever sets it, so a page that ignores it still behaves as it did before.

    tutor_beat is the tenth. It is null on every message but the one that
    carries the success line, where it is the whole success beat the page has
    to play: kind, the parts in the order they run, the two timings in
    milliseconds and their sum, the line being said and the line that closes
    the beat. The page owns the pixels and reads the numbers here rather than
    inventing them; the tutor owns the decision and never skips it.
    """

    tutor_state: str = WORKING
    intervention_level: int = 0
    tutor_line: str | None = None
    tutor_visual: dict[str, Any] | None = None
    scored_gesture_error: bool = False
    scored_math_error: bool = False
    first_try: bool = True
    mode: str = NORMAL
    tutor_line_cuts: bool = False
    tutor_beat: dict[str, Any] | None = None

    def as_fields(self) -> dict[str, Any]:
        return {
            "tutor_state": self.tutor_state,
            "intervention_level": self.intervention_level,
            "tutor_line": self.tutor_line,
            "tutor_visual": dict(self.tutor_visual) if self.tutor_visual else None,
            "scored_gesture_error": self.scored_gesture_error,
            "scored_math_error": self.scored_math_error,
            "first_try": self.first_try,
            "mode": self.mode,
            "tutor_line_cuts": self.tutor_line_cuts,
            "tutor_beat": dict(self.tutor_beat) if self.tutor_beat else None,
        }


@dataclass
class ExerciseRecord:
    """What one finished exercise says about the next one.

    autonomous_success drives independent mode, hard drives supportive mode, and
    scored_errors with rescue_used decide whether the session should stop early.
    """

    autonomous_success: bool
    hard: bool
    scored_errors: int
    rescue_used: bool


@dataclass
class _Pending:
    """An intervention line waiting for the four things only the future knows."""

    ts: str
    level: int
    trigger_after_s: float
    state_before: str
    delivered_raw: float
    child_was_already_moving: bool
    keys: tuple[str, ...]
    child_moved_after_s: float | None = None
    correct_pose_within_5s: bool = False
    next_hint_needed: bool = False


def jsonl_sink(path: Path | str = LOG_PATH) -> Callable[[dict[str, Any]], None]:
    """Append one JSON object per line, flushed, and never take the lesson down."""

    target = Path(path)

    def sink(record: dict[str, Any]) -> None:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, separators=(",", ":"),
                                        ensure_ascii=False) + "\n")
                handle.flush()
        except OSError:
            return

    return sink


def _iso(moment: datetime) -> str:
    at = moment.astimezone(timezone.utc)
    return at.strftime("%Y-%m-%dT%H:%M:%S.") + f"{at.microsecond // 1000:03d}Z"


def _percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest rank, so a short sample never invents a value between two frames."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return float(ordered[min(rank, len(ordered)) - 1])


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


class Tutor:
    """The tutor state machine. Feed it observations and events, read decisions."""

    def __init__(self, params: TutorParams, lines: Mapping[str, str],
                 clock: Callable[[], float],
                 log: Callable[[dict[str, Any]], None] | None = None,
                 learner_id: str = "",
                 factors: Mapping[str, Any] | None = None,
                 utc_clock: Callable[[], datetime] = now_utc) -> None:
        self.params = params
        self.lines = lines
        self._clock = clock
        self._log = log
        self._utc = utc_clock
        self.learner_id = learner_id

        # per learner factors, section 2.4
        self._pace_factor = 1.0
        self._help_factor = 1.0
        self._observations = 0
        if factors is not None:
            self.set_learner(learner_id, factors)

        # the camera, section 2.3
        self.motion_threshold = MOTION_FLOOR
        self._jitter_on = False
        self._jitter: list[float] = []
        self._jitter_start: float | None = None

        # clocks
        self._now: float | None = None
        self._ped = 0.0
        self._tts_speaking = False
        self._tts_end_raw: float | None = None
        self._speech_raw: float | None = None
        self._moving = False
        self._motion_stop_raw: float | None = None
        self._engage_idle = 0.0
        self._paused = False

        # perception
        self._last_taken: float | None = None
        self._prev_fingers: list[Mapping[str, Any]] = []
        self._motion_log: deque[tuple[float, bool]] = deque(maxlen=600)
        self._raw_key: tuple[int, int, bool] | None = None
        self._raw_since = 0.0
        self._raw_misses = 0
        self._raw_frames = 0
        self._held_key: tuple[int, int, bool] | None = None
        self._gone_since: float | None = None
        self._hands_ok = False
        self._hands_misses = 0
        self._hands_since = 0.0
        # Pedagogical clock reading of the first frame of the current run of
        # frames with fewer than two hands, None while both hands are in frame.
        self._missing_since: float | None = None
        self._contact_ok = True
        self._contact_frames = 0

        # session and node
        self._node: Any = None
        self._node_history: list[ExerciseRecord] = []
        self._session_history: list[ExerciseRecord] = []
        self._said_independent = False
        self._said_supportive = False
        self._mode = NORMAL
        self._early_stage = "none"
        self._decision_times: deque[float] = deque(maxlen=8)

        # per exercise
        self._open = False
        self._reset_exercise(0.0, 0, 0, "")

    # -- learner ------------------------------------------------------------

    def set_learner(self, learner_id: str,
                    factors: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Take the tutor key off the learner record, smoothed and scheduled."""
        self.learner_id = learner_id or self.learner_id
        proposed = dict(factors or {})
        try:
            observations = int(proposed.get("tutor_observations", 0) or 0)
        except (TypeError, ValueError):
            observations = 0
        self._observations = max(0, observations)
        self._pace_factor = self._schedule(self._smooth(self._pace_factor,
                                                        proposed.get("pace_factor")))
        self._help_factor = self._schedule(self._smooth(self._help_factor,
                                                        proposed.get("help_factor")))
        return self.learner_factors()

    def _smooth(self, old: float, proposed: Any) -> float:
        try:
            value = float(proposed)
        except (TypeError, ValueError):
            return old
        if not math.isfinite(value):
            return old
        return SMOOTHING_OLD * old + SMOOTHING_NEW * value

    def _schedule(self, value: float) -> float:
        if self._observations <= SCHEDULE_GLOBALS_ONLY:
            return 1.0
        if self._observations <= SCHEDULE_NARROW:
            return _clamp(value, 1.0 - SCHEDULE_NARROW_DEVIATION,
                          1.0 + SCHEDULE_NARROW_DEVIATION)
        return value

    def learner_factors(self) -> dict[str, Any]:
        """What app/server.py writes back onto the learner record."""
        return {"pace_factor": round(self._pace_factor, 4),
                "help_factor": round(self._help_factor, 4),
                "tutor_observations": self._observations}

    # -- effective values ---------------------------------------------------

    def mode_factor(self) -> float:
        if self._mode == SUPPORTIVE:
            return float(self.params.values["supportive_mode_factor"])
        if self._mode == INDEPENDENT:
            return float(self.params.values["independent_mode_factor"])
        return 1.0

    def effective(self, key: str) -> float:
        """global[key] scaled, then clamped. The clamp is always the last step."""
        raw = float(self.params.values[key])
        low, high = self.params.bound(key)
        if key in BEAT_PARAMS:
            # The beat is the same length for every child and in every mode:
            # no learner factor, no mode factor, and the clamp is still the
            # last step. This is the path INDEPENDENT mode may not touch.
            return float(_clamp(raw, low, high))
        if (key == "fps" or key in FACTOR_PARAMS or key in LATENCY_PARAMS
                or key in RATIO_PARAMS):
            # A ratio between two fingertips is geometry. No child is helped by
            # a contact distance that moves with their pace factor.
            return raw
        if key in COUNT_PARAMS:
            return float(_clamp(round(raw * self._help_factor), low, high))
        return float(_clamp(raw * self._pace_factor * self.mode_factor(), low, high))

    def _count(self, key: str) -> int:
        return int(self.effective(key))

    # -- the camera check, section 2.3 --------------------------------------

    def start_check(self) -> None:
        """Begin the still hands measurement of the check node."""
        self._jitter_on = True
        self._jitter = []
        self._jitter_start = None

    def end_check(self) -> float:
        """Finish it and fix motion_threshold for the rest of the run."""
        self._jitter_on = False
        jitter = (_percentile(self._jitter, JITTER_PERCENTILE)
                  if len(self._jitter) >= JITTER_MIN_PAIRS else 0.0)
        self.motion_threshold = max(MOTION_FLOOR, MOTION_MULTIPLIER * jitter)
        return self.motion_threshold

    # -- events from the page ------------------------------------------------

    def tts_start(self, now: float | None = None) -> Decision:
        """Tally started speaking: every pedagogical timer stops here."""
        self._tick(now)
        self._tts_speaking = True
        return self._decision()

    def tts_end(self, now: float | None = None) -> Decision:
        """Tally stopped speaking. Idempotent: two false in a row are one event."""
        moment = self._tick(now)
        if self._tts_speaking or self._tts_end_raw is None:
            self._tts_end_raw = moment
        self._tts_speaking = False
        if not self._prompt_done:
            self._prompt_done = True
            self._prompt_end_ped = self._ped
        return self._decision()

    def speech(self, text: str, number: int | None = None,
               now: float | None = None) -> Decision:
        """The child said something, whether or not it parsed as a number.

        Nothing is stored, and number is deliberately unused: speech never
        submits an answer and never scores. Its two effects are that the
        pedagogical timers freeze and that the child has proved they are here.
        """
        moment = self._tick(now)
        self._speech_raw = moment
        self._engage()
        return self._decision()

    def line_dropped(self, line: Any = None, reason: Any = None,
                     at: Any = None, now: float | None = None) -> Decision:
        """The page could not speak a line it was given, and says so.

        One line of the page's queue that never reached the child: a newer line
        of the same kind replaced it, the queue was full, or speech was cut off.
        It changes nothing in the lesson, scores nothing and moves no timer of
        its own; it is written to the log as a line_drop event so that the gap
        between lines decided and lines heard can be measured.

        Nothing here raises and nothing here is refused. A line that is not a
        string, a reason outside the vocabulary and a timestamp that is not a
        number are all logged as they came, because a malformed report about a
        line going missing is itself the evidence.
        """
        self._tick(now)
        text = line if isinstance(line, str) and line.strip() else None
        if text is not None:
            text = text.strip()[:DROP_TEXT_LIMIT]
        token = reason if isinstance(reason, str) else None
        if token is not None:
            token = token.strip().lower()
            token = DROP_SYNONYMS.get(token, token)
        known = token if token in DROP_REASONS else DROP_UNKNOWN
        raw = None if token in DROP_REASONS else self._reported(reason)
        reported_at: float | None = None
        if isinstance(at, (int, float)) and not isinstance(at, bool):
            reported_at = round(float(at), 3) if math.isfinite(float(at)) else None
        self._write({
            "kind": "line_drop",
            "ts": _iso(self._utc()),
            "learner_id": self.learner_id,
            "exercise": self._title,
            "line": text,
            "line_key": self._key_for_line(text),
            "reason": known,
            "reported_reason": raw,
            "reported_at": reported_at,
            "state": self._state,
            "intervention": self._level,
            "mode": self._mode,
        })
        return self._decision()

    @staticmethod
    def _reported(reason: Any) -> str | None:
        """A reason the vocabulary does not know, kept as the page sent it."""
        if reason is None:
            return None
        try:
            text = reason if isinstance(reason, str) else repr(reason)
        except Exception:
            return None
        text = text.strip()[:DROP_TEXT_LIMIT]
        return text or None

    def _key_for_line(self, text: str | None) -> str | None:
        """Which of Tally's lines this was, when it was one of them verbatim.

        Only the lines with nothing to fill in can be recognised this way, and
        the acknowledgement of a confirmed pose is one of them, which is the
        whole point: how many acknowledgements never reached the child.
        """
        if text is None:
            return None
        for key, template in self.lines.items():
            if template == text:
                return key
        return None

    def hint_requested(self, now: float | None = None) -> Decision:
        """The child asked for help. The only help that costs first try."""
        moment = self._tick(now)
        self._engage()
        if not self._open:
            return self._decision()
        self._requested_hints += 1
        target = min(RESCUE_LEVEL, self._level + 1)
        target = self._gate_level(target)
        if target > self._level:
            self._level = target
            self._max_level = max(self._max_level, target)
        if self._level >= RESCUE_LEVEL and self._rescue_used:
            # The rescue is walked through once per exercise, whatever asked
            # for it. Asking again does not buy a second one.
            return self._decision()
        line = self._ladder_line(self._level, asked=True)
        self._deliver(moment, self._level, line, solicited=True,
                      reason=REASON_CHILD_ASKED, keys=("hint_2_delay", "rescue_delay"))
        return self._decision(line=line)

    def answer(self, value: int | None, correct: bool,
               now: float | None = None) -> Decision:
        """A final answer, submitted through a check message.

        value is kept for the caller's shape and is deliberately unused: the
        canonical lines name the exercise and its result, never the digits typed.
        """
        moment = self._tick(now)
        self._engage()
        if not self._open:
            return self._decision()
        self._answered = True
        if correct:
            self._answer_correct = True
            if not self._correct_pose_seen:
                # Invariant 6: accepted, praised, and the pose is confirmed after.
                self._pose_waived = True
            line = self._render("success", a=self._a, b=self._b,
                                total=self._result)
            self._set_state(SUCCESS, moment, REASON_ANSWER_GIVEN)
            self._say(line, moment)
            # Never gated and never skipped: the beat is called here, on the
            # same path as the success line, outside _offer and _blocked, so
            # min_verbal_gap, the unsolicited budget and the mode leave it
            # alone. A correct answer always gets its beat.
            self._beat = self._success_beat(line)
        else:
            self._math_errors += 1
            self._set_state(ANSWER_RETRY, moment, REASON_ANSWER_WRONG,
                            scored="math")
            line = self._answer_rescue(moment)
            if line is None:
                key = WRONG_ANSWER_KEYS[min(self._math_errors,
                                            len(WRONG_ANSWER_KEYS)) - 1]
                line = self._render(key)
                self._say(line, moment)
        return self._decision(line=line)

    def new_exercise(self, a: int, b: int, title: str | None = None,
                     node: Any = None, now: float | None = None) -> Decision:
        """Load an exercise. Mode is chosen here, once, and the ladder resets."""
        moment = self._tick(now)
        if self._open:
            self.end_exercise(reason="skipped", now=moment)
        if node != self._node:
            self._node = node
            self._node_history = []
            self._said_independent = False
            self._said_supportive = False
            self._mode = NORMAL
        previous_mode = self._mode
        self._mode = self._choose_mode()
        self._reset_exercise(moment, a, b, title or f"{a} x {b}")
        self._engage()

        line: str | None = None
        if self._early_stage == "mastered_fact":
            self._early_stage = "closing_pending"
            # One already mastered fact, with all the help it needs.
            self._mode = SUPPORTIVE
            line = self._render("launch", a=a, b=b)
        elif self._mode == INDEPENDENT and not self._said_independent:
            self._said_independent = True
            line = self._render("autonomous")
        elif self._mode == SUPPORTIVE and (previous_mode != SUPPORTIVE
                                           or not self._said_supportive):
            self._said_supportive = True
            line = self._render("hesitation_1")
            # Supportive opens on the first hesitation line, so an idle nudge
            # later in this exercise goes to the second one instead of repeating.
            self._hesitations += 1
        if line is not None:
            self._say(line, moment)
        self._log_decision(REASON_WITHIN_GRACE, None)
        return self._decision(line=line)

    def end_exercise(self, reason: str = "answered",
                     now: float | None = None) -> Decision:
        """Answered, skipped, quit or dropped. Writes the exercise log line."""
        moment = self._tick(now)
        if not self._open:
            return self._decision()
        self._open = False
        self._flush_pending(moment, force=True)
        abandoned = (not self._answer_correct and not self._answered
                     and reason not in ("answered", "skipped"))
        autonomous = (self._answer_correct and self._max_level <= 1
                      and not self._rescue_used)
        first_try = self.first_try
        scored = self._gesture_errors + self._math_errors
        hard = (self._gesture_errors > 0 or self._math_errors > 0
                or self._max_level >= 2)
        light = (self._correct_pose_seen and self._max_level in (1, 2)
                 and self._taught > 0)
        self._observations += 1
        self._write({
            "kind": "exercise",
            "ts": _iso(self._utc()),
            "learner_id": self.learner_id,
            "exercise": self._title,
            "autonomous_success": autonomous,
            "first_try": first_try,
            "light_hint_recovery": light,
            "rescue_used": self._rescue_used,
            "scored_gesture_errors": self._gesture_errors,
            "scored_math_errors": self._math_errors,
            "unsolicited_interventions": self._unsolicited,
            "abandoned": abandoned,
            "duration_s": round(max(0.0, moment - self._ex_start), 2),
            "mode": self._mode,
        })
        record = ExerciseRecord(autonomous_success=autonomous, hard=hard,
                                scored_errors=scored,
                                rescue_used=self._rescue_used)
        self._node_history.append(record)
        self._session_history.append(record)
        self._check_early_stop()
        return self._decision()

    # -- the perception tick -------------------------------------------------

    def observe(self, obs: Observation, now: float | None = None) -> Decision:
        """One perception window. Anything faster than 1/fps is dropped."""
        moment = self._tick(now)
        gap = 1.0 / float(self.params.values["fps"]) - FPS_EPSILON
        if self._last_taken is not None and moment - self._last_taken < gap:
            return self._decision()
        self._last_taken = moment

        motion = obs.motion if obs.motion is not None else self._motion(obs.fingers)
        self._prev_fingers = [dict(f) for f in obs.fingers]
        self._update_motion(motion, moment)
        self._collect_jitter(motion, obs, moment)
        self._update_hands(obs.hands_seen, moment)
        self._update_contact(obs)
        self._update_pose(obs, moment)
        # The acknowledgement reads the raw clock, never a pedagogical one: the
        # child who just reached the pose is moving, and movement must not hold
        # back the one line that says the pose is right.
        self._release_ack(moment)
        if obs.hint:
            self._hint = dict(obs.hint)

        situation = self._situation(obs)
        # What the hands are saying, read before anything acts on it. A reading
        # that changes starts its own clock, the same rule the situation follows.
        reading = self._read_pose(obs, situation)
        if reading.name != self._reading.name:
            self._reading_since = self._ped
        self._reading = reading
        if situation != self._situation_now:
            # A state change resets the timer: the child who fixed one thing
            # never inherits the clock of the thing they fixed.
            self._situation_now = situation
            self._situation_since = self._ped
            if situation in VISIBILITY_PROBLEMS and self._missing_since is not None:
                # The hands have been gone for the whole of visibility_grace_ms
                # before the tutor admits it. The reminders' clocks read from
                # the moment they went, not from the moment of the admission,
                # so no_hands_visual, no_hands_voice, one_hand_voice and the
                # opening call keep the meaning they had: the grace delays the
                # verdict, never the reminder once the verdict is in. Never
                # earlier than this exercise, whose hands were never lost.
                self._situation_since = max(self._missing_since, self._ex_start_ped)
            if situation != SIT_WRONG:
                self._correction_since = None
        self._update_recovery(moment, situation)
        self._release_queued(moment)
        self._update_paused(moment)
        line = self._run(moment, situation)
        cuts = False
        if self._pending_line is not None:
            # A line waiting from this same tick wins over anything the ladder
            # picked: it is the acknowledgement, and it is allowed to cut.
            line, self._pending_line = self._pending_line, None
            cuts, self._pending_cuts = self._pending_cuts, False
        self._flush_pending(moment)
        return self._decision(line=line, cuts=cuts)

    # -- the decision --------------------------------------------------------

    def _run(self, moment: float, situation: str) -> str | None:
        state = self._next_state(situation)
        if state != self._state:
            self._set_state(state, moment, self._state_reason(situation))
        if not self._open or self._paused:
            return None
        self._score_gesture(moment, situation)
        blocked = self._blocked(moment)
        if blocked is not None:
            if blocked in (REASON_CHILD_MOVING, REASON_CHILD_SPEAKING):
                self._suppress(blocked)
            return None
        if self._suppressed in (REASON_CHILD_MOVING, REASON_CHILD_SPEAKING):
            self._suppressed = None
        return self._consider(moment, situation)

    def _next_state(self, situation: str) -> str:
        if self._paused:
            return PAUSED
        if not self._open:
            return self._state
        if self._answer_correct:
            return SUCCESS
        if self._answered and not self._answer_correct:
            return ANSWER_RETRY
        if self._correct_pose_seen:
            return CHECK_ANSWER if self._state in (POSE_READY, CHECK_ANSWER) else POSE_READY
        if not self._prompt_done and self._tts_speaking:
            return PROMPTING
        held = self._ped - self._situation_since
        if situation == SIT_NO_HANDS and held >= self.effective("no_hands_visual"):
            return VISIBILITY_RECOVERY
        if situation == SIT_ONE_HAND and held >= self.effective("one_hand_voice"):
            return VISIBILITY_RECOVERY
        if situation in POSE_PROBLEMS and held >= self.effective("wrong_pose_prompt"):
            return WRONG_POSE
        return WORKING

    def _state_reason(self, situation: str) -> str:
        if self._paused:
            return REASON_NO_ENGAGEMENT
        if self._answer_correct:
            return REASON_ANSWER_GIVEN
        if self._correct_pose_seen:
            return REASON_POSE_READY
        if situation == SIT_NO_HANDS:
            return REASON_HANDS_LOST
        if situation == SIT_ONE_HAND:
            return REASON_ONE_HAND
        if situation in POSE_PROBLEMS:
            return REASON_WRONG_POSE_HELD
        return REASON_WITHIN_GRACE

    def _consider(self, moment: float, situation: str) -> str | None:
        """Pick at most one intervention for this tick, highest priority first."""
        held = self._ped - self._situation_since

        # 1. Visibility. Never a pedagogical error, its own budget, its own gate,
        # and never followed by a pedagogical line: the hands are not there to
        # be taught.
        if situation in VISIBILITY_PROBLEMS:
            return self._visibility(moment, situation, held)

        # 2. Nothing is taught to hands that are not steadily in frame. The pose
        # may still be the one remembered from before they left, and the ladder
        # never climbs on a memory: no hands, no step, however long they are away.
        if not self._hands_ok:
            return None

        # 3. What the hands are saying. The table picks the aid from the
        # reading; the clocks below stay the ceiling and still fire for a
        # reading the table has no aid for.
        handled, said = self._table_aid(moment, situation)
        if handled:
            return said

        # 3. A pose problem that has outlived its grace.
        if situation in POSE_PROBLEMS:
            if not self._past_initial_silence() or self.in_recovery:
                return None
            if held < self.effective("wrong_pose_prompt"):
                return None
            since = self._ped - self._last_delivery.get(situation, -math.inf)
            if self._level > 0 and since < self.effective("wrong_pose_prompt"):
                return None
            return self._escalate(moment, situation)

        # 4. The pose is right. Counting is the only thing left to nudge towards,
        # so this branch never falls through to the opener below.
        if situation == SIT_CORRECT:
            if self._answered or not self._past_initial_silence():
                return None
            since = self._ped - self._last_delivery.get(SIT_CORRECT, -math.inf)
            if held >= self.effective("correct_pose_nudge") and since >= self.effective("correct_pose_nudge"):
                # The two halves of the method, in the order they are counted.
                line = self._render("count_tens" if self._counting_nudges == 0
                                    else "multiply_above")
                said = self._offer(moment, max(1, self._level), line, SIT_CORRECT,
                                   REASON_POSE_READY, ("correct_pose_nudge",))
                if said is not None:
                    self._counting_nudges += 1
                return said
            return None

        # 5. Both hands are in frame and the classifier cannot read a pose: the
        # child is frozen, or holding something that is not a 6-10 pose at all.
        # SPEC.md section 9: numbers in white, no judgement, then one nudge.
        if self._level > 1 or not self._past_initial_silence():
            return None
        last_line = self._prompt_end_ped if self._last_line_ped is None else self._last_line_ped
        quiet = self._ped - max(self._prompt_end_ped, last_line, self._last_any_delivery)
        if quiet >= self.effective("idle_nudge"):
            first = not self._said_anything
            if first:
                line = self._render("launch", a=self._a, b=self._b)
            elif self._hesitations == 0:
                line = self._render("hesitation_1")
            else:
                # Still nothing: the second hesitation line names the first step.
                line = self._render("hesitation_2", a=self._a)
            said = self._offer(moment, max(1, self._level), line, "idle",
                               REASON_PROMPT_END if first else REASON_IDLE,
                               ("idle_nudge", "initial_silence"))
            if said is not None and not first:
                self._hesitations += 1
            return said
        return None

    def _visibility(self, moment: float, situation: str, held: float) -> str | None:
        budget = self._count("max_visibility_reminders")
        if self._visibility_reminders >= budget:
            self._suppress(REASON_BUDGET_SPENT)
            return None
        if situation == SIT_NO_HANDS:
            visual_at = self.effective("no_hands_visual")
            # The opening is its own moment: the exercise has been asked and the
            # hands have never been in frame, so Tally calls them in with a
            # child's line of his own, on the opening clock. Once the hands have
            # been seen, losing them is the camera failing and the usual
            # visibility reminder is the right thing to say.
            opening = not self._hands_ever
            voice_at = (self.effective("visibility_prompt_ms") / MS_PER_S if opening
                        else self.effective("no_hands_voice"))
            if held >= voice_at and self._visibility_voiced < budget:
                line = self._visibility_line(HANDS_PROMPT_KEY if opening
                                             else "visibility_none")
                said = self._offer(moment, 1, line, situation, REASON_HANDS_LOST,
                                   ("no_hands_voice", "min_verbal_gap"),
                                   visibility=True)
                # Counted when it is said, not when it is tried: a line the
                # verbal gap dropped is a reminder the child never heard.
                if said is not None:
                    self._visibility_voiced += 1
                return said
            if held >= visual_at and not self._visibility_shown:
                self._visibility_shown = True
                self._visibility_reminders += 1
                self._deliver(moment, 1, None, solicited=False,
                              reason=REASON_HANDS_LOST,
                              keys=("no_hands_visual",), visibility=True)
                return None
            return None
        if situation == SIT_ONE_HAND:
            if held >= self.effective("one_hand_voice") and self._visibility_voiced < budget:
                line = self._visibility_line("visibility_one")
                said = self._offer(moment, 1, line, situation, REASON_ONE_HAND,
                                   ("one_hand_voice", "min_verbal_gap"),
                                   visibility=True)
                if said is not None:
                    self._visibility_voiced += 1
                return said
            return None
        return None

    def _visibility_line(self, key: str) -> str | None:
        """The first spoken reminder names what is missing.

        A later one asks for the hands to stay where the camera can read them:
        repeating the first line word for word is nagging, not teaching.
        """
        if self._visibility_voiced > 0:
            key = "visibility_keep"
        return self._render(key)

    # -- the decision table --------------------------------------------------

    def _read_pose(self, obs: Observation, situation: str) -> PoseReading:
        """Turn one perception window into one row of POSE_TABLE.

        Read top to bottom, in the order of the table. The landmarks say what is
        touching and what is closing in, through contact_ratio and the same palm
        _update_contact measures in; the classifier says which number each hand
        is holding, because a fingertip position on its own cannot.
        """
        if situation in VISIBILITY_PROBLEMS:
            return PoseReading(name=READ_HANDS_GONE)
        if situation == SIT_CORRECT:
            return PoseReading(name=READ_CORRECT)
        hand, shows, wants = self._searching_hand()
        tips = tip_positions(obs.fingers)
        palm = self._palm(obs) if tips else 0.0
        nearest = nearest_gap(tips, palm)
        if nearest is None:
            # Nothing usable came back from the camera this window. The table
            # says nothing rather than guessing, and the clocks decide alone.
            return PoseReading(name=READ_UNCLEAR, hand=hand, shows=shows,
                               wants=wants)
        contact = self.effective("contact_ratio")
        gap, pair = nearest
        left_wants, right_wants = self._expected_numbers()
        asked = tip_gap(tips, palm, left_wants, right_wants)
        expected_pair = (("left", left_wants), ("right", right_wants))
        if gap <= contact and situation == SIT_WRONG:
            # Two fingertips together and a finger number actually wrong: the
            # child has the gesture and the count is off.
            name = (READ_WRONG_PAIR_HELD if self._colours_held(pair)
                    else READ_WRONG_PAIR)
            return PoseReading(name=name, gap=gap, pair=pair, hand=hand,
                               shows=shows, wants=wants)
        if asked is not None and asked <= CLOSING_MULTIPLE * contact:
            # The two fingertips the exercise asks for are within twice the
            # contact distance: the child knows which fingers and is closing in,
            # or has just arrived and the classifier has yet to confirm it.
            # Either way there is nothing to correct.
            return PoseReading(name=READ_CLOSING_IN, gap=asked,
                               pair=expected_pair)
        if hand is not None:
            return PoseReading(name=READ_ONE_HAND_SEARCHING, gap=gap, pair=pair,
                               hand=hand, shows=shows, wants=wants)
        return PoseReading(name=READ_SEARCHING, gap=gap, pair=pair)

    @property
    def reading(self) -> PoseReading:
        """What the tutor thinks it is looking at, as of the last observation."""
        return self._reading

    def _expected_numbers(self) -> tuple[int, int]:
        """The number each hand is asked for, in the kinder of the two readings.

        An inversion is never a wrong pose, so the pair the child is nearest to
        is the pair the exercise is taken to have asked for.
        """
        if self._held_key is None:
            return self._a, self._b
        left, right, _ = self._held_key
        straight = int(left != self._a) + int(right != self._b)
        crossed = int(left != self._b) + int(right != self._a)
        if crossed < straight:
            return self._b, self._a
        return self._a, self._b

    def _searching_hand(self) -> tuple[str | None, int | None, int | None]:
        """The one hand still looking, the number it holds and the one it wants.

        None when both hands are right, and None when both are wrong: the row of
        the table that colours one hand only is for one hand only.
        """
        left_wrong, right_wrong = self._wrong_hands()
        if left_wrong == right_wrong:
            return None, None, None
        hand = "left" if left_wrong else "right"
        left_wants, right_wants = self._expected_numbers()
        wants = left_wants if left_wrong else right_wants
        shows: int | None = None
        if self._held_key is not None:
            shows = self._held_key[0] if left_wrong else self._held_key[1]
        return hand, shows, wants

    def _colours_held(self, pair: tuple[tuple[str, int], tuple[str, int]]) -> bool:
        """The same wrong pair, still held hint_2_delay after the colours."""
        if self._colours_since is None or self._colours_pair != pair:
            return False
        return self._ped - self._colours_since >= self.effective("hint_2_delay")

    def _table_aid(self, moment: float, situation: str) -> tuple[bool, str | None]:
        """Take the step this reading asks for, or leave the tick to the clocks.

        Returns whether the table acted, and the line the ladder said if it said
        one. The step is the ladder's in every other respect: the level climbs
        one at a time unless the reading is unambiguous enough to earn its level
        outright, _ladder_line decides what is said at it, which is nothing below
        L3, and the two graces come first.
        """
        aid = POSE_TABLE[self._reading.name]
        if aid.level <= 0 or self._answered or self.in_recovery:
            return False, None
        if not self._past_initial_silence():
            return False, None
        if situation in VISIBILITY_PROBLEMS:
            # The zones and the come here line have their own budget and their
            # own gate, in _visibility above. The table names the aid; that
            # branch is the one that gives it.
            return False, None
        if self._last_line_ped is not None:
            if self._ped - self._last_line_ped < self._quiet_after_a_line():
                # A child changing a pose is not corrected mid movement, whatever
                # the table can see.
                return False, None
        if aid.after is not None:
            if self._ped - self._reading_since < self.effective(aid.after):
                return False, None
        target = self._gate_level(min(RESCUE_LEVEL, aid.level if aid.at_once
                                      else self._level + 1))
        target = min(target, max(aid.level, self._level))
        if target <= self._level:
            return False, None
        since = self._ped - self._last_delivery.get(situation, -math.inf)
        if self._level > 0 and since < self.effective("wrong_pose_prompt"):
            return False, None
        keys = ("wrong_pose_prompt", "wrong_pose_error_after_help",
                "min_verbal_gap", "post_line_grace_ms", "hint_2_delay",
                "rescue_delay")
        said = self._offer(moment, target, self._ladder_line(target, situation=situation),
                           situation, REASON_WRONG_POSE_HELD, keys,
                           rescue=target >= RESCUE_LEVEL)
        return True, said

    def _reading_visual(self) -> dict[str, Any] | None:
        """The drawing this reading asks for, or None to leave the ladder alone.

        The ladder owns what each level draws: the numbers at L1, the colour at
        L2, the ghost at L3. All the reading does is put the colour and the
        ghost on the hand the pose says is still looking, rather than on the
        hand the engine guessed, and draw the one thing the ladder has no level
        for: the two dots of a child closing in, before any step is taken.
        """
        reading = self._reading
        aid = POSE_TABLE[reading.name]
        if aid.visual == VIS_CLOSING_IN and reading.pair is not None:
            if self._level > 0:
                # The ladder is already drawing at this level, and the level's
                # drawing is the level's own.
                return None
            return {"kind": VIS_CLOSING_IN,
                    "tips": [{"hand": hand, "finger": finger}
                             for hand, finger in reading.pair]}
        if reading.hand is None or reading.wants is None:
            return None
        if aid.visual in (VIS_CORRECTION, VIS_GHOST):
            if self._level >= 3 and reading.shows is not None:
                return {"kind": VIS_GHOST, "hand": reading.hand,
                        "from": reading.shows, "to": reading.wants}
            if self._level == 2:
                return {"kind": VIS_CORRECTION, "wrong_hand": reading.hand,
                        "expected_finger": reading.wants}
        return None

    def _escalate(self, moment: float, situation: str) -> str | None:
        target = self._gate_level(min(RESCUE_LEVEL, self._level + 1))
        if target <= self._level and self._level > 0:
            # hint_2_delay or rescue_delay is holding the ladder here. Saying the
            # same thing again is nagging, and it would spend the budget the step
            # above still needs, so wait in silence.
            return None
        line = self._ladder_line(target, situation=situation)
        keys = ("wrong_pose_prompt", "wrong_pose_error_after_help", "min_verbal_gap",
                "post_movement_silence", "hint_2_delay", "rescue_delay")
        said = self._offer(moment, target, line, situation,
                           REASON_WRONG_POSE_HELD, keys,
                           rescue=target >= RESCUE_LEVEL)
        return said

    def _answer_rescue(self, moment: float) -> str | None:
        """The second wrong number on one exercise walks the whole thing through.

        One of the three triggers of L4, and the only one that reads no clock:
        two wrong answers are the evidence, so rescue_delay does not gate them.
        It stays outside max_unsolicited_verbal, which remains the budget of L1,
        L2 and L3, it stays one per exercise, and it still obeys min_verbal_gap,
        which drops it rather than queueing it. Dropped or already spent, the
        wrong answer keeps its own line and the ladder stays where it was.
        """
        if self._math_errors < WRONG_ANSWERS_FOR_RESCUE or self._rescue_used:
            return None
        return self._offer(moment, RESCUE_LEVEL, self._ladder_line(RESCUE_LEVEL),
                           None, REASON_ANSWER_WRONG, ("min_verbal_gap",),
                           rescue=True)

    def _gate_level(self, target: int) -> int:
        """L3 and L4 are barred until their own clock on the exercise has run.

        That clock is the ladder's, which only runs with both hands in frame.
        """
        elapsed = self._ladder_ped
        if target >= 4 and elapsed < self.effective("rescue_delay"):
            target = 3
        if target >= 3 and elapsed < self.effective("hint_2_delay"):
            target = 2
        return max(target, self._level)

    def _offer(self, moment: float, level: int, line: str | None,
               problem: str | None, reason: str, keys: Sequence[str],
               visibility: bool = False, rescue: bool = False) -> str | None:
        """Apply the anti nag limits, then deliver or drop. Never queue."""
        if line is None:
            # A silent step of the ladder. Nothing is spoken, so no verbal gate
            # applies and no verbal budget is spent, but the step is taken: the
            # level climbs, the drawing changes and the log records it.
            self._deliver(moment, level, None, solicited=False, reason=reason,
                          keys=keys, problem=problem, visibility=visibility)
            return None
        if self._last_line_ped is not None:
            if self._ped - self._last_line_ped < self._quiet_after_a_line():
                self._suppress(REASON_MIN_VERBAL_GAP)
                return None
        if rescue:
            # max_unsolicited_verbal is the budget of L1, L2 and L3. The rescue
            # sits outside it: the three steps below always spend it first, and a
            # rescue refused for budget would never be reachable at all. Its own
            # limit is one per exercise, and it still obeys min_verbal_gap above.
            if self._rescue_used:
                return None
        elif not visibility:
            if self._unsolicited >= self._count("max_unsolicited_verbal"):
                self._suppress(REASON_BUDGET_SPENT)
                return None
        if visibility:
            self._visibility_reminders += 1
        self._deliver(moment, level, line, solicited=False, reason=reason,
                      keys=keys, problem=problem, visibility=visibility)
        return line

    def _quiet_after_a_line(self) -> float:
        """How long nothing is said after Tally has said something.

        Two rules answer the same question from different sides: min_verbal_gap
        is the pace of the dialogue, post_line_grace_ms is the beat a child needs to
        change a pose without being judged mid movement. They start at the same
        moment, the last line, so the quiet is the longer of the two and never
        their sum. Nothing here touches the acknowledgement of a right pose,
        which goes nowhere near this gate.
        """
        return max(self.effective("min_verbal_gap"),
                   self.effective("post_line_grace_ms") / MS_PER_S)

    def _deliver(self, moment: float, level: int, line: str | None,
                 solicited: bool, reason: str, keys: Sequence[str],
                 problem: str | None = None, visibility: bool = False) -> None:
        """Record one intervention actually delivered, spoken or visual."""
        if not visibility and level > self._level:
            self._level = level
            self._max_level = max(self._max_level, level)
        if level >= RESCUE_LEVEL and not visibility:
            self._rescue_used = True
        if line is not None:
            self._say(line, moment)
            if not solicited and not visibility:
                self._unsolicited += 1
        if problem is not None:
            self._last_delivery[problem] = self._ped
        if line is not None or visibility:
            # A drawing on its own is not something Tally said, so it buys no
            # silence from the clocks that decide when she speaks next. The
            # visibility drawing keeps its place there: it is the first half of
            # a reminder whose second half is spoken.
            self._last_any_delivery = self._ped
        if not visibility:
            self._taught += 1
        self._suppressed = None
        if not visibility and level == 2:
            # The colours are up, and only L2 puts them up. The table measures
            # hint_2_delay from here, on this pair alone: a different wrong pair
            # is a different problem, and the steps above are not the colours.
            self._colours_since = self._ped
            self._colours_pair = self._reading.pair
        if not visibility and level >= 2 and self._hint.get("hand"):
            # The clock of section 5.2 starts at the correction, not at the
            # first sight of the wrong pose.
            self._correction_hand = str(self._hint["hand"])
            self._correction_since = self._ped
        for pending in self._pending:
            if level > pending.level:
                pending.next_hint_needed = True
        self._pending.append(_Pending(
            ts=_iso(self._utc()),
            level=max(1, level),
            trigger_after_s=round(max(0.0, moment - self._ex_start), 2),
            state_before=self._state,
            delivered_raw=moment,
            child_was_already_moving=self._was_moving(moment),
            keys=tuple(keys),
        ))
        self._log_decision(reason, None)

    def _ladder_line(self, level: int, situation: str | None = None,
                     asked: bool = False) -> str | None:
        """What the ladder says at this level, and None where it only shows.

        Tally shows before he speaks. L1 puts the numbers on the fingertips and
        L2 colours the two fingers that matter, both without a word; L3 is the
        first level with a voice and says the correction; L4 walks the whole
        thing through. Help the child asked for is always answered in words:
        silence in reply to a question is not patience.
        """
        situation = situation or self._situation_now
        if level >= RESCUE_LEVEL:
            return self._render("rescue", tens=self._tens,
                                tens_value=self._tens * 10, u1=self._u1,
                                u2=self._u2, units=self._units,
                                total=self._result)
        if asked and level <= 1:
            # Help the child asked for is worth more than a nudge: the first
            # concrete step, on the hand they start from.
            return self._render("hesitation_2", a=self._a)
        if level < 3 and not asked:
            # L1 and L2 are shown, not said.
            return None
        if situation == SIT_NO_CONTACT:
            return self._render("not_touching")
        if situation == SIT_SWAPPED:
            # The two numbers are right and each is on the other hand, so the
            # line that says which number belongs where is the swap advice.
            return self._render("wrong_both", a=self._a, b=self._b)
        return self._correction_line()

    def _correction_line(self) -> str | None:
        """Name the hand, or the two hands, holding a finger nobody asked for."""
        left_wrong, right_wrong = self._wrong_hands()
        if left_wrong and right_wrong:
            return self._render("wrong_both", a=self._a, b=self._b)
        if right_wrong:
            return self._render("wrong_right", b=self._b)
        if left_wrong:
            return self._render("wrong_left", a=self._a)
        return self._render("hesitation_1")

    def _wrong_hands(self) -> tuple[bool, bool]:
        """Which hands hold a finger the exercise did not ask for.

        Both readings of the pair are tried and the kinder one wins, the same
        way lesson/engine.py aims a wrong finger at the target it is nearest to.
        """
        if self._held_key is None:
            hand = (self._hint or {}).get("hand")
            return (hand == "left", hand == "right")
        left, right, _ = self._held_key
        straight = (left != self._a, right != self._b)
        crossed = (left != self._b, right != self._a)
        if sum(crossed) < sum(straight):
            return crossed
        return straight

    def _visual(self) -> dict[str, Any] | None:
        """What to draw now. Derived, never stored, so it can never go stale."""
        if not self._open:
            return None
        if self._rescue_used:
            return {"kind": "rescue_card", "tens": self._tens,
                    "units": self._units, "total": self._result}
        situation = self._situation_now
        hint = self._hint or {}
        if situation in (SIT_NO_HANDS, SIT_ONE_HAND):
            # A visibility failure is never a pedagogical error, so it never
            # shows a pedagogical drawing: the zones, or nothing.
            return {"kind": "placement_zones"} if self._visibility_shown else None
        if self.in_recovery and situation in POSE_PROBLEMS:
            # No orange, no ghost, no pointed finger while the grace runs: the
            # child gets their bearings back before anything is corrected.
            return {"kind": "finger_numbers"} if self._level >= 1 else None
        drawn = self._reading_visual()
        if drawn is not None:
            return drawn
        # The visual ladder: numbers at L1, colour from L2, the ghost at L3.
        if situation == SIT_WRONG and self._level >= 2 and hint.get("hand"):
            if self._level >= 3:
                # L3: the ghost finger moves from the wrong finger to the right
                # one, and this is the level where Tally speaks as well.
                return {"kind": "ghost", "hand": hint["hand"],
                        "from": hint.get("move_from"), "to": hint.get("move_to")}
            # L2: colour on the two fingers that matter, and still no voice.
            return {"kind": "correction", "wrong_hand": hint["hand"],
                    "expected_finger": hint.get("move_to")}
        if situation != SIT_CORRECT and self._level >= 1:
            # L1: the numbers on every fingertip, no judgement and no word.
            return {"kind": "finger_numbers"}
        if (self._mode == SUPPORTIVE and self._now is not None
                and self._now - self._ex_start < SUPPORTIVE_VISUAL_S):
            # Supportive mode keeps 6 to 10 drawn on the fingers to start with.
            return {"kind": "finger_numbers"}
        return None

    # -- scoring -------------------------------------------------------------

    def _score_gesture(self, moment: float, situation: str) -> None:
        if self._gesture_errors or situation != SIT_WRONG:
            return
        if self.in_recovery:
            # The hands have just come back. Nothing held in that window is the
            # child's mistake, so nothing in it is counted as one.
            return
        if self._correction_hand is None or self._correction_since is None:
            return
        if not self._ever_stable or not self._hands_ok:
            return
        if (self._hint or {}).get("hand") != self._correction_hand:
            return
        if self._ped - self._correction_since < self.effective("wrong_pose_error_after_help"):
            return
        self._gesture_errors += 1
        self._log_decision(REASON_WRONG_POSE_HELD, "gesture")

    @property
    def first_try(self) -> bool:
        """Live eligibility. The value that counts rides answer_correct."""
        eligible = (self._gesture_errors == 0 and self._math_errors == 0
                    and self._requested_hints == 0 and not self._rescue_used)
        if not self._answered:
            return eligible
        pose = self._correct_pose_seen or self._pose_waived
        return eligible and self._answer_correct and pose

    # -- early stop ----------------------------------------------------------

    def _check_early_stop(self) -> None:
        if self._early_stage != "none":
            if self._early_stage == "closing_pending":
                self._early_stage = "closing"
            return
        if len(self._session_history) < EARLY_STOP_MIN_EXERCISES:
            return
        last = self._session_history[-EARLY_STOP_CONSECUTIVE:]
        if len(last) < EARLY_STOP_CONSECUTIVE:
            return
        if not all(r.scored_errors >= EARLY_STOP_ERRORS for r in last):
            return
        if not any(r.rescue_used for r in last):
            return
        self._early_stage = "mastered_fact"

    @property
    def early_stop(self) -> bool:
        return self._early_stage != "none"

    def early_stop_plan(self) -> dict[str, Any]:
        """What app/server.py should do next. The tutor never calls the scheduler.

        The twenty lines hold no goodbye, so the closing stage carries no line
        and the page falls back to the phrase of lesson/tally.py.
        """
        return {"stop": self.early_stop, "stage": self._early_stage, "line": None}

    # -- modes ---------------------------------------------------------------

    def _choose_mode(self) -> str:
        history = self._node_history
        if not history:
            return NORMAL
        recent = history[-HARD_FOR_SUPPORTIVE:]
        if len(recent) == HARD_FOR_SUPPORTIVE and all(r.hard for r in recent):
            return SUPPORTIVE
        streak = history[-AUTONOMOUS_FOR_INDEPENDENT:]
        if (len(streak) == AUTONOMOUS_FOR_INDEPENDENT
                and all(r.autonomous_success for r in streak)):
            return INDEPENDENT
        return NORMAL

    @property
    def mode(self) -> str:
        return self._mode

    # -- clocks --------------------------------------------------------------

    def _tick(self, now: float | None) -> float:
        moment = self._clock() if now is None else now
        if self._now is None:
            self._now = moment
            return moment
        step = moment - self._now
        if step < 0:
            step = 0.0
        self._now = moment
        if self._blocked(moment) is None:
            self._ped += step
        if not self._tts_speaking and not self._paused:
            self._engage_idle += step
        return moment

    def _blocked(self, moment: float) -> str | None:
        """Why no pedagogical clock may advance right now, or None."""
        if self._paused:
            return REASON_NO_ENGAGEMENT
        if self._tts_speaking:
            return "tts"
        if self._tts_end_raw is not None and moment - self._tts_end_raw < TTS_RESUME_S:
            return "tts"
        silence = self.effective("post_movement_silence")
        if self._moving:
            return REASON_CHILD_MOVING
        if self._motion_stop_raw is not None and moment - self._motion_stop_raw < silence:
            return REASON_CHILD_MOVING
        if self._speech_raw is not None and moment - self._speech_raw < silence:
            return REASON_CHILD_SPEAKING
        return None

    def _engage(self) -> None:
        """Movement, speech or a command: the child is here."""
        self._engage_idle = 0.0
        self._paused = False

    def _suppress(self, reason: str) -> None:
        """An intervention the anti nag limits held back. Logged once, not per frame."""
        if self._suppressed == reason:
            return
        # Only remember it once it has actually been written: a line the two per
        # second throttle swallowed is a line Loop 2 never sees.
        if self._log_decision(reason, None):
            self._suppressed = reason

    def _update_paused(self, moment: float) -> None:
        if self._paused:
            return
        if self._engage_idle >= self.effective("no_engagement_pause"):
            self._paused = True
            self._log_decision(REASON_NO_ENGAGEMENT, None)

    def _past_initial_silence(self) -> bool:
        """Has the child had their thinking silence with both hands in frame.

        It is read off the ladder clock, so the silence starts when the hands
        arrive and not when the exercise was announced to an empty frame.
        """
        return self._ladder_ped >= self.effective("initial_silence")

    def _say(self, line: str | None, moment: float) -> None:
        if line is None:
            return
        self._last_line_ped = self._ped
        self._said_anything = True

    # -- the acknowledgement of a confirmed pose -----------------------------

    def _arm_ack(self, moment: float) -> None:
        """The pose is confirmed: two beats, not one sentence.

        First the acknowledgement, at once, which may cut whatever is being
        said: a child who has just made the pose has to hear yes now or not at
        all. Then, pose_ready_delay_ms later, the canonical cue that tells them
        what to do with the pose they are holding.

        ack_delay_ms still moves the first beat, and the second is measured from
        it, so the pair keeps its shape wherever the first one lands. Neither
        goes through _offer: min_verbal_gap, the unsolicited budget and the
        ladder all leave the confirmation of a right pose alone.

        A line file without the acknowledgement is not an error. The beat then
        degrades to the canonical cue alone, said at once and allowed to cut,
        which is exactly what this moment sounded like before the second beat.
        """
        if not self._open:
            return
        ack = self._render(ACK_LINE_KEY)
        cue = self._render(POSE_READY_KEY)
        due = moment + self.effective("ack_delay_ms") / MS_PER_S
        if ack is None:
            self._ack_line, self._ack_due = cue, due
            self._cue_line = None
            return
        self._ack_line, self._ack_due = ack, due
        self._cue_line = cue
        self._cue_due = due + self.effective("pose_ready_delay_ms") / MS_PER_S

    def _release_ack(self, moment: float) -> None:
        """Hand the two beats over, in order, as each falls due.

        One line rides one message, so when both are due on the same tick the
        acknowledgement goes first and the cue waits for the next one: the child
        never hears the second beat before the first.
        """
        if self._ack_line is not None:
            if moment < self._ack_due:
                return
            line, self._ack_line = self._ack_line, None
            self._say(line, moment)
            self._pending_line = line
            self._pending_cuts = True
            return
        if self._cue_line is None or moment < self._cue_due:
            return
        line, self._cue_line = self._cue_line, None
        if not self._open or self._paused:
            # The exercise the cue was about is gone. Everything else about
            # which line is said when is exactly as it was before the beat.
            return
        self._say(line, moment)
        self._pending_line = line
        # The cue is not the yes. It waits its turn in the page's queue like
        # every other line, so only one line of the pair may ever cut.
        self._pending_cuts = False

    # -- the gentle recovery -------------------------------------------------

    def _update_recovery(self, moment: float, situation: str) -> None:
        """Losing the hands is the camera's doing. Getting them back is not a test.

        The hands come back, Tally says he can see them, asks for the exercise
        again, and then keeps a grace of recovery_grace_ms in which no wrong
        pose is spoken, drawn or counted. When it closes the ladder starts again
        at L0 with its clocks reset, as if the exercise had just been set.

        The one grace already in the file is the opening silence, and the two
        never stack: a recovery that happens while initial_silence is still
        running opens no window of its own, because the hush the child is in is
        already the hush this would ask for.
        """
        if not self._open:
            self._lost = False
            self._recover_until = None
            return
        if self._recover_until is not None and moment >= self._recover_until:
            self._recover_until = None
            # Whatever the child is holding, its clock starts now: the ladder
            # may not climb on a pose nobody was allowed to correct.
            self._situation_since = self._ped
            self._reading_since = self._ped
            self._correction_since = None
            self._level = 0
        if self._state == VISIBILITY_RECOVERY:
            # Hands that have never been in frame are not lost hands: the
            # opening has its own call and its own order, and welcoming back
            # hands that never arrived would talk over it.
            self._lost = self._hands_ever
            return
        if not self._lost or situation in VISIBILITY_PROBLEMS or not self._hands_ok:
            return
        self._lost = False
        self._level = 0
        self._correction_since = None
        self._last_delivery.clear()
        self._suppressed = None
        self._queue(moment, self._render(RECOVERY_LINE_KEY))
        self._queue(moment, self._render(LAUNCH_KEY, a=self._a, b=self._b))
        if self._past_initial_silence():
            self._recover_until = moment + self.effective("recovery_grace_ms") / MS_PER_S

    @property
    def in_recovery(self) -> bool:
        """Whether the grace after a blind spell is still running."""
        return (self._recover_until is not None and self._now is not None
                and self._now < self._recover_until)

    def _queue(self, due: float, line: str | None) -> None:
        """A line to hand over as soon as a message can carry it."""
        if line is not None:
            self._queued.append((due, line))

    def _release_queued(self, moment: float) -> None:
        """One queued line per message, in the order they were queued.

        The acknowledgement of a confirmed pose always wins the message it lands
        on: a yes the child has earned is never held behind anything.
        """
        if self._pending_line is not None or not self._queued:
            return
        due, line = self._queued[0]
        if moment < due:
            return
        self._queued.pop(0)
        self._say(line, moment)
        self._pending_line = line
        self._pending_cuts = False

    # -- perception helpers --------------------------------------------------

    def _motion(self, fingers: Sequence[Mapping[str, Any]]) -> float | None:
        if not fingers or not self._prev_fingers:
            return None
        here = {(f.get("hand"), f.get("number")): (f.get("x"), f.get("y"))
                for f in fingers}
        there = {(f.get("hand"), f.get("number")): (f.get("x"), f.get("y"))
                 for f in self._prev_fingers}
        moves: list[float] = []
        for key, (x, y) in here.items():
            other = there.get(key)
            if other is None or x is None or y is None:
                continue
            if other[0] is None or other[1] is None:
                continue
            moves.append(math.hypot(float(x) - float(other[0]),
                                    float(y) - float(other[1])))
        if not moves:
            return None
        moves.sort()
        middle = len(moves) // 2
        # Frame fractions into palm widths on both branches: an odd count of
        # fingertips used to come back ten times too small, and the movement
        # invariant silently stopped holding.
        if len(moves) % 2:
            return moves[middle] / NOMINAL_PALM
        return (0.5 * (moves[middle - 1] + moves[middle])) / NOMINAL_PALM

    def _update_motion(self, motion: float | None, moment: float) -> None:
        moving = motion is not None and motion > self.motion_threshold
        if self._moving and not moving:
            self._motion_stop_raw = moment
        if moving:
            self._engage()
        self._moving = moving
        self._motion_log.append((moment, moving))

    def _was_moving(self, moment: float) -> bool:
        return any(moving for at, moving in self._motion_log
                   if moment - at <= MOVEMENT_LOOKBACK_S)

    def _collect_jitter(self, motion: float | None, obs: Observation,
                        moment: float) -> None:
        if not self._jitter_on:
            return
        if obs.hands_seen < 2 or motion is None:
            self._jitter = []
            self._jitter_start = None
            return
        if self._jitter_start is None:
            self._jitter_start = moment
        if moment - self._jitter_start <= JITTER_WINDOW_S:
            self._jitter.append(motion)

    def _frames(self, seconds: float) -> int:
        return max(1, int(round(seconds * float(self.params.values["fps"]))))

    def _tolerance(self, frames: int) -> int:
        required = max(1, math.ceil(frames * STABILITY_NUMERATOR / STABILITY_DENOMINATOR))
        return max(0, frames - required)

    def _update_hands(self, hands_seen: int, moment: float) -> None:
        self._advance_ladder_clock()
        both = hands_seen >= 2
        # The run of frames with a hand missing, on the pedagogical clock the
        # visibility reminders are timed on. Any frame with both hands ends it:
        # the grace is continuous, a flicker never adds up to an absence.
        if both:
            self._missing_since = None
        elif self._missing_since is None:
            self._missing_since = self._ped
        tolerance = self._tolerance(self._frames(self.effective("hands_visible_stable")))
        if both == self._hands_ok:
            self._hands_misses = 0
            self._hands_ever = self._hands_ever or self._hands_ok
            return
        self._hands_misses += 1
        if self._hands_misses > tolerance:
            self._hands_ok = both
            self._hands_misses = 0
            self._hands_since = moment
        self._hands_ever = self._hands_ever or self._hands_ok

    def _advance_ladder_clock(self) -> None:
        """The ladder's own clock, which runs only with both hands in frame.

        The opening order is the exercise, then the hands, then the thinking
        silence, then the ladder. Time spent with the hands out of frame is the
        camera's problem and it advances nothing: initial_silence and the gates
        of L3 and L4 are all read off this clock, so a child who put their hands
        down comes back to the step they left, not three steps further on.
        """
        step = self._ped - self._ped_mark
        self._ped_mark = self._ped
        if step > 0 and self._hands_ok and self._open:
            self._ladder_ped += step

    def _update_contact(self, obs: Observation) -> None:
        """Is what the classifier calls a touch really a touch.

        classifier/rules.py owns the contact flag and nobody here may edit it,
        so this is a second opinion taken from the fingertips themselves: the
        two tips the classifier named have to be within contact_ratio of a palm
        of each other, and to stay there for pose_confirm_frames frames, before
        the pose counts as made. Anything wider is NO_CONTACT, whatever the
        flag says, because a pose held ten centimetres apart teaches nothing.

        When the fingertips are not in the observation there is nothing to
        measure and the flag is taken as it comes: this refuses a bad touch, it
        never invents one.
        """
        measured = self._tip_distance(obs)
        if measured is None:
            self._contact_ok = True
            self._contact_frames = 0
            return
        distance, palm = measured
        if distance <= self.effective("contact_ratio") * palm:
            self._contact_frames += 1
        else:
            self._contact_frames = 0
        self._contact_ok = self._contact_frames >= int(self.effective("pose_confirm_frames"))

    def _tip_distance(self, obs: Observation) -> tuple[float, float] | None:
        """The gap between the two named fingertips, and the palm it is read in."""
        gesture = obs.gesture
        if gesture is None or gesture.left is None or gesture.right is None:
            return None
        tips = {(str(f.get("hand")), f.get("number")): (f.get("x"), f.get("y"))
                for f in obs.fingers}
        here = tips.get(("left", int(gesture.left)))
        there = tips.get(("right", int(gesture.right)))
        if here is None or there is None:
            return None
        try:
            distance = math.hypot(float(here[0]) - float(there[0]),
                                  float(here[1]) - float(there[1]))
        except (TypeError, ValueError):
            return None
        palm = self._palm(obs)
        if not math.isfinite(distance) or palm <= 0:
            return None
        return distance, palm

    def _palm(self, obs: Observation) -> float:
        """The palm width the server sent, or the best estimate of it."""
        if isinstance(obs.palm, (int, float)) and not isinstance(obs.palm, bool):
            palm = float(obs.palm)
            if math.isfinite(palm) and palm > 0:
                return palm
        spreads: list[float] = []
        for hand in ("left", "right"):
            points = [(f.get("x"), f.get("y")) for f in obs.fingers
                      if str(f.get("hand")) == hand]
            if len(points) < 2:
                continue
            try:
                widest = max(math.hypot(float(a[0]) - float(b[0]),
                                        float(a[1]) - float(b[1]))
                             for a in points for b in points)
            except (TypeError, ValueError):
                continue
            if math.isfinite(widest) and widest > 0:
                spreads.append(widest)
        if not spreads:
            return NOMINAL_PALM
        return (sum(spreads) / len(spreads)) / TIP_SPREAD_PALMS

    def _update_pose(self, obs: Observation, moment: float) -> None:
        key = self._key_of(obs.gesture)
        if key is None:
            if self._held_key is not None:
                if self._gone_since is None:
                    self._gone_since = moment
                elif moment - self._gone_since >= self.effective("pose_memory"):
                    self._held_key = None
                    self._raw_key = None
                    self._gone_since = None
            return
        self._gone_since = None
        tolerance = self._tolerance(self._frames(self.effective("pose_stable")))
        if self._raw_key is None:
            # First readable pose of the exercise: nothing to be unsure about.
            self._raw_key = key
            self._raw_since = moment
            self._raw_misses = 0
            self._raw_frames = 1
        elif key == self._raw_key:
            self._raw_misses = 0
            self._raw_frames += 1
        else:
            self._raw_misses += 1
            if self._raw_misses > tolerance:
                self._raw_key = key
                self._raw_since = moment
                self._raw_misses = 0
                self._raw_frames = 1
        if self._raw_key is None:
            return
        # A wrong pose is evaluated on time and on frames, both: a new pose has
        # to have been seen pose_confirm_frames times and held pose_stable
        # before anything is said about it, so a camera that drops frames
        # cannot have a pose judged on two of them, and a hand passing through
        # a wrong shape on its way is not corrected. The correct pose is the
        # other way round, amendment F8: pose_confirm_frames frames or
        # pose_confirm_ms, whichever comes first, because the child is already
        # right while a slower clock would still be counting.
        frames = int(self.effective("pose_confirm_frames"))
        held = moment - self._raw_since
        if self._condition(self._raw_key) == SIT_CORRECT:
            confirmed = (self._raw_frames >= frames
                         or held >= self.effective("pose_confirm_ms") / MS_PER_S)
        else:
            confirmed = held >= self.effective("pose_stable") and self._raw_frames >= frames
        if confirmed:
            self._held_key = self._raw_key
            self._ever_stable = True
            if self._condition(self._held_key) == SIT_CORRECT:
                if not self._correct_pose_seen:
                    self._correct_pose_seen = True
                    self._arm_ack(moment)

    @staticmethod
    def _key_of(gesture: GestureState | None) -> tuple[int, int, bool] | None:
        if gesture is None or gesture.method != "6-10":
            return None
        if gesture.left is None or gesture.right is None:
            return None
        return (int(gesture.left), int(gesture.right), bool(gesture.contact))

    def _condition(self, key: tuple[int, int, bool] | None) -> str:
        if key is None:
            return SIT_UNKNOWN
        left, right, contact = key
        if frozenset((left, right)) != frozenset((self._a, self._b)):
            return SIT_WRONG
        if contact and self._contact_ok:
            return SIT_CORRECT
        if self._a != self._b and (left, right) == (self._b, self._a):
            return SIT_SWAPPED
        return SIT_NO_CONTACT

    def _situation(self, obs: Observation) -> str:
        if self._held_key is not None:
            return self._condition(self._held_key)
        if obs.hands_seen >= 2:
            return SIT_UNKNOWN
        if not self._hands_gone:
            # A hand the tracker lost a moment ago is not a hand that left.
            # Until visibility_grace_ms of unbroken absence the tutor reads the
            # frame as two hands it cannot read a pose from: no reminder, no
            # zones, no recovery state, and nothing for the hands coming back
            # in the next frame to be welcomed back from.
            return SIT_UNKNOWN
        if obs.hands_seen <= 0:
            return SIT_NO_HANDS
        return SIT_ONE_HAND

    @property
    def _hands_gone(self) -> bool:
        """Whether a hand has been missing for the whole of visibility_grace_ms."""
        if self._missing_since is None:
            return False
        grace = self.effective("visibility_grace_ms") / MS_PER_S
        return self._ped - self._missing_since >= grace

    # -- the log -------------------------------------------------------------

    def _write(self, record: dict[str, Any]) -> None:
        if self._log is None:
            return
        try:
            self._log(record)
        except Exception:
            # No log line may ever take the lesson down.
            return

    def _log_decision(self, reason: str, scored: str | None) -> bool:
        if self._now is None:
            return False
        recent = [at for at in self._decision_times if self._now - at < 1.0]
        if len(recent) >= DECISIONS_PER_SECOND:
            return False
        self._decision_times.append(self._now)
        stable = 0.0 if self._moving else max(0.0, self._now - self._raw_since)
        self._write({
            "kind": "decision",
            "ts": _iso(self._utc()),
            "learner_id": self.learner_id,
            "exercise": self._title,
            "state": self._state,
            "stable_for": round(stable, 2),
            "intervention": self._level,
            "reason": reason,
            "reading": self._reading.as_log(),
            "scored_error": scored,
            "mode": self._mode,
            "params_version": self.params.params_version,
        })
        return True

    def _set_state(self, state: str, moment: float, reason: str,
                   scored: str | None = None) -> None:
        if state == self._state and scored is None:
            return
        self._state = state
        self._log_decision(reason, scored)

    def _flush_pending(self, moment: float, force: bool = False) -> None:
        keep: list[_Pending] = []
        for pending in self._pending:
            age = moment - pending.delivered_raw
            if pending.child_moved_after_s is None:
                for at, moving in self._motion_log:
                    if at > pending.delivered_raw and moving:
                        pending.child_moved_after_s = round(at - pending.delivered_raw, 2)
                        break
            if self._correct_pose_seen and age <= INTERVENTION_FOLLOW_UP_S:
                pending.correct_pose_within_5s = True
            if not force and age < INTERVENTION_FOLLOW_UP_S:
                keep.append(pending)
                continue
            moved = pending.child_moved_after_s
            if moved is not None and moved > INTERVENTION_FOLLOW_UP_S:
                moved = None
            self._write({
                "kind": "intervention",
                "ts": pending.ts,
                "learner_id": self.learner_id,
                "exercise": self._title,
                "intervention": pending.level,
                "trigger_after_s": pending.trigger_after_s,
                "state_before": pending.state_before,
                "child_was_already_moving": pending.child_was_already_moving,
                "child_moved_after_s": moved,
                "correct_pose_within_5s": pending.correct_pose_within_5s,
                "next_hint_needed": pending.next_hint_needed,
                "effective_params": self._effective_params(pending.keys),
                "learner_factors": dict(self.learner_factors(),
                                        mode_factor=round(self.mode_factor(), 3)),
            })
        self._pending = keep

    def _effective_params(self, keys: Sequence[str]) -> dict[str, float]:
        out = {key: round(self.effective(key), 3) for key in keys}
        out["motion_threshold"] = round(self.motion_threshold, 3)
        return out

    # -- lines ---------------------------------------------------------------

    def _render(self, key: str, **context: Any) -> str | None:
        """Fill one of the twenty lines, or say nothing rather than say nonsense."""
        template = self.lines.get(key)
        if not template:
            return None
        if any(value is None for value in context.values()):
            return None
        try:
            return template.format(**context)
        except (KeyError, IndexError, ValueError):
            return None

    # -- the success beat ----------------------------------------------------

    def _beat_ms(self, key: str) -> int:
        """One beat timing, in whole milliseconds, inside its bounds."""
        return int(round(self.effective(key)))

    def _success_beat(self, line: str | None) -> dict[str, Any]:
        """The beat between a correct answer and the next exercise.

        The tutor decides that it runs and how long each half lasts; the page
        decides what green and a star burst look like. next_line is None while
        lesson/tally_lines.json has no line for it, and the page then closes the
        beat in silence rather than on words the tutor made up.
        """
        success_ms = self._beat_ms("success_beat_ms")
        pause_ms = self._beat_ms("next_pause_ms")
        return {
            "kind": BEAT_SUCCESS,
            "parts": list(BEAT_PARTS),
            "success_ms": success_ms,
            "pause_ms": pause_ms,
            "total_ms": success_ms + pause_ms,
            "line": line,
            "next_line": self._render(NEXT_LINE_KEY),
        }

    # -- bookkeeping ---------------------------------------------------------

    def _reset_exercise(self, moment: float, a: int, b: int, title: str) -> None:
        self._a, self._b, self._title = a, b, title
        self._tens = (a - 5) + (b - 5)
        self._u1, self._u2 = 10 - a, 10 - b
        self._units = self._u1 * self._u2
        self._result = self._tens * 10 + self._units
        self._open = bool(title)
        self._ex_start = moment
        self._ex_start_ped = self._ped
        self._state = PROMPTING
        self._level = 0
        self._max_level = 0
        self._gesture_errors = 0
        self._math_errors = 0
        self._requested_hints = 0
        self._rescue_used = False
        self._unsolicited = 0
        self._visibility_reminders = 0
        self._visibility_voiced = 0
        self._visibility_shown = False
        self._hesitations = 0
        self._counting_nudges = 0
        self._taught = 0
        self._suppressed: str | None = None
        self._pending_line: str | None = None
        self._pending_cuts = False
        self._ack_line: str | None = None
        self._ack_due = 0.0
        self._cue_line: str | None = None
        self._cue_due = 0.0
        self._queued: list[tuple[float, str]] = []
        self._lost = False
        self._recover_until: float | None = None
        self._ladder_ped = 0.0
        self._ped_mark = self._ped
        self._hands_ever = False
        self._beat: dict[str, Any] | None = None
        self._answered = False
        self._answer_correct = False
        self._correct_pose_seen = False
        self._pose_waived = False
        self._ever_stable = False
        self._said_anything = False
        self._prompt_done = False
        self._prompt_end_ped = self._ped
        self._last_line_ped: float | None = None
        self._last_any_delivery = self._ped
        self._last_delivery: dict[str, float] = {}
        self._correction_hand: str | None = None
        self._correction_since: float | None = None
        self._hint: dict[str, Any] = {}
        self._situation_now = SIT_UNKNOWN
        self._situation_since = self._ped
        self._reading = PoseReading()
        self._reading_since = self._ped
        self._colours_since: float | None = None
        self._colours_pair: tuple[tuple[str, int], tuple[str, int]] | None = None
        self._pending: list[_Pending] = []
        self._held_key = None
        self._raw_key = None
        self._raw_since = moment
        self._raw_misses = 0
        self._raw_frames = 0
        self._gone_since = None

    def _decision(self, line: str | None = None, cuts: bool = False) -> Decision:
        # The beat rides exactly one decision, the one that carries the success
        # line, and is cleared as it is read: a page that replayed it on every
        # message would loop the celebration forever.
        beat, self._beat = self._beat, None
        return Decision(
            tutor_state=self._state,
            intervention_level=self._level,
            tutor_line=line,
            tutor_visual=self._visual(),
            scored_gesture_error=self._gesture_errors > 0,
            scored_math_error=self._math_errors > 0,
            first_try=self.first_try,
            mode=self._mode,
            tutor_line_cuts=cuts and line is not None,
            tutor_beat=beat,
        )

    def state_fields(self) -> dict[str, Any]:
        """The ten new fields of the state message, as of now."""
        return self._decision().as_fields()
