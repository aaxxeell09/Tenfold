"""The ladder shows before it speaks, and a touch has to be a touch.

The four steps, and what the page is handed at each of them:

  L0  observe                   no line, no drawing
  L1  the numbers on the tips   tutor_visual finger_numbers, no line
  L2  colour on the two fingers tutor_visual correction, no line
  L3  the ghost finger moves    tutor_visual ghost, and the correction spoken
  L4  the rescue                tutor_visual rescue_card, and the walk through

A correct pose is still acknowledged the instant it is confirmed, whatever the
ladder is doing.

Contact is measured here rather than believed: the two fingertips the
classifier names have to be within contact_ratio of a palm of each other for
pose_confirm_frames frames. classifier/rules.py owns the flag and is not
touched; this is a second opinion taken from the fingertips themselves.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.tutor import (
    RESCUE_LEVEL,
    Decision,
    Observation,
    ParamsError,
    Tutor,
    load_lines,
    load_params,
)
from classifier.schema import GestureState

REPO = Path(__file__).resolve().parents[1]
PARAMS_FILE = REPO / "lesson" / "tutor_params.json"
LINES_FILE = REPO / "lesson" / "tally_lines.json"
LINES = json.loads(LINES_FILE.read_text(encoding="utf-8"))

ACK = LINES["pose_ack"]
NOT_TOUCHING = LINES["not_touching"]
WRONG_RIGHT = LINES["wrong_right"].format(b=7)
RESCUE = LINES["rescue"].format(tens=5, tens_value=50, u1=2, u2=3, units=6, total=56)
HESITATION_2 = LINES["hesitation_2"].format(a=8)

FPS = 15
STEP = 1.0 / FPS
HINT = {"hand": "right", "move_from": 9, "move_to": 7}


def pose(left: int, right: int, contact: bool = True) -> GestureState:
    return GestureState(method="6-10", left=left, right=right, contact=contact,
                        confidence=0.9)


def fingers(hands: int = 2, gesture: GestureState | None = None,
            gap: float = 0.0) -> list[dict[str, object]]:
    """Fingertips in frame fractions, with the claimed touch gap apart."""
    names = {0: (), 1: ("left",), 2: ("left", "right")}[hands]
    out: list[dict[str, object]] = []
    for hand in names:
        base = 0.45 if hand == "left" else 0.55
        for number in range(6, 11):
            out.append({"hand": hand, "number": number, "x": base,
                        "y": 0.5 + 0.02 * (number - 6)})
    if gesture is not None and gesture.contact and hands == 2:
        where = {("left", gesture.left): 0.5, ("right", gesture.right): 0.5 + gap}
        for tip in out:
            place = where.get((tip["hand"], tip["number"]))
            if place is not None:
                tip["x"], tip["y"] = place, 0.5
    return out


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def params_with(**overrides: float) -> object:
    raw = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    raw["global"].update(overrides)
    path = Path(tempfile.mkdtemp(prefix="tenfold-ladder-")) / "params.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return load_params(path)


class Harness:
    def __init__(self, params: object | None = None) -> None:
        self.clock = Clock()
        self.base = datetime(2026, 9, 13, 18, 0, 0, tzinfo=timezone.utc)
        self.tutor = Tutor(params or load_params(PARAMS_FILE),
                           load_lines(LINES_FILE), clock=self.clock, log=None,
                           learner_id="9f31c0a7bd42", utc_clock=self.utc)
        self.last = Decision()
        self.steps: list[tuple[int, str | None, str | None]] = []
        self.tutor.new_exercise(8, 7, node="u2-l3", now=self.clock.t)

    def utc(self) -> datetime:
        return self.base + timedelta(seconds=self.clock.t)

    def feed(self, seconds: float, gesture: GestureState | None = None,
             hands: int = 2, hint: dict[str, object] | None = None,
             gap: float = 0.0, palm: float | None = None) -> list[str]:
        said: list[str] = []
        for _ in range(int(round(seconds * FPS))):
            self.clock.t += STEP
            self.last = self.tutor.observe(
                Observation(gesture=gesture, fingers=fingers(hands, gesture, gap),
                            hands_seen=hands, hint=hint, palm=palm), self.clock.t)
            visual = self.last.tutor_visual
            step = (self.last.intervention_level,
                    visual["kind"] if visual else None, self.last.tutor_line)
            if not self.steps or self.steps[-1][:2] != step[:2] or step[2]:
                self.steps.append(step)
            if self.last.tutor_line:
                said.append(self.last.tutor_line)
        return said

    def at_level(self, level: int) -> tuple[int, str | None, str | None]:
        """The first record of that level: what it drew and what it said."""
        for step in self.steps:
            if step[0] == level:
                return step
        raise AssertionError(f"the ladder never reached L{level}: {self.steps}")


def wrong_pose(harness: Harness, seconds: float) -> list[str]:
    return harness.feed(seconds, gesture=pose(8, 9, False), hint=HINT)


# --- the ladder --------------------------------------------------------------


def test_level_zero_watches_in_silence() -> None:
    harness = Harness()
    said = wrong_pose(harness, 3.0)
    assert said == []
    assert harness.last.intervention_level == 0
    assert harness.last.tutor_visual is None


def test_level_one_puts_the_numbers_up_and_says_nothing() -> None:
    harness = Harness()
    said = wrong_pose(harness, 7.0)
    assert said == []
    level, kind, line = harness.at_level(1)
    assert (kind, line) == ("finger_numbers", None)


def test_level_two_colours_the_two_fingers_and_says_nothing() -> None:
    harness = Harness()
    said = wrong_pose(harness, 8.5)
    assert said == []
    assert harness.at_level(2)[1:] == ("correction", None)
    assert harness.last.tutor_visual == {"kind": "correction",
                                         "wrong_hand": "right",
                                         "expected_finger": 7}


def test_level_three_speaks_the_correction_and_moves_the_ghost() -> None:
    harness = Harness()
    said = wrong_pose(harness, 11.0)
    assert said == [WRONG_RIGHT], "the first word of the ladder is at L3"
    assert harness.at_level(3)[1] == "ghost"
    assert harness.last.tutor_visual == {"kind": "ghost", "hand": "right",
                                         "from": 9, "to": 7}


def test_level_four_is_the_rescue_as_before() -> None:
    harness = Harness()
    said = wrong_pose(harness, 20.0)
    assert said[0] == WRONG_RIGHT
    assert RESCUE in said
    assert harness.last.intervention_level == RESCUE_LEVEL
    assert harness.last.tutor_visual == {"kind": "rescue_card", "tens": 5,
                                         "units": 6, "total": 56}


def test_a_child_who_asks_for_help_is_answered_in_words() -> None:
    """Silence is the ladder's patience, never an answer to a question."""
    harness = Harness()
    wrong_pose(harness, 3.0)
    decision = harness.tutor.hint_requested(now=harness.clock.t)
    assert decision.tutor_line == HESITATION_2


def test_a_right_pose_is_acknowledged_at_any_level() -> None:
    harness = Harness()
    wrong_pose(harness, 8.5)
    assert harness.last.intervention_level == 2
    said = harness.feed(1.2, gesture=pose(8, 7, True))
    # The pose still has to be confirmed, and the ladder may be mid sentence
    # about the pose that was there a moment ago, but the yes arrives inside
    # pose_stable and it is the acknowledgement, not a level of the ladder.
    assert ACK in said
    assert harness.last.tutor_state in ("POSE_READY", "CHECK_ANSWER")


# --- real contact ------------------------------------------------------------


def test_the_contact_key_is_known_with_its_bounds() -> None:
    raw = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    params = load_params(PARAMS_FILE)
    assert raw["global"]["contact_ratio"] == 0.35
    assert raw["bounds"]["contact_ratio"] == [0.2, 0.6]
    assert params.bound("contact_ratio") == (0.2, 0.6)
    with pytest.raises(ParamsError) as excinfo:
        params_with(contact_ratio=0.9)
    assert "contact_ratio" in str(excinfo.value) and "0.6" in str(excinfo.value)


def test_fingertips_a_hand_apart_are_not_a_touch_whatever_the_flag_says() -> None:
    harness = Harness()
    # The classifier says contact; the tips are a tenth of the frame apart,
    # which is about ten centimetres of child.
    said = harness.feed(11.0, gesture=pose(8, 7, True), gap=0.1, hint=HINT)
    assert ACK not in said
    assert NOT_TOUCHING in said, "L3 asks for the touch that is missing"


def test_fingertips_together_are_a_touch() -> None:
    harness = Harness()
    said = harness.feed(2.0, gesture=pose(8, 7, True), gap=0.0)
    assert said[0] == ACK


def test_the_touch_has_to_hold_for_pose_confirm_frames() -> None:
    harness = Harness(params_with(pose_confirm_frames=6))
    # Five frames of a real touch is not enough on its own.
    said = harness.feed(5 * STEP, gesture=pose(8, 7, True))
    assert said == []
    said = harness.feed(2.0, gesture=pose(8, 7, True))
    assert said[0] == ACK


def test_the_palm_the_server_sends_is_the_one_used() -> None:
    harness = Harness()
    # A palm of a third of the frame makes the same gap a touch.
    said = harness.feed(2.0, gesture=pose(8, 7, True), gap=0.1, palm=0.33)
    assert said[0] == ACK
    tight = Harness()
    said = tight.feed(2.0, gesture=pose(8, 7, True), gap=0.1, palm=0.05)
    assert said == []


def test_without_fingertips_the_classifier_is_taken_at_its_word() -> None:
    """This refuses a bad touch. It never invents one it cannot measure."""
    harness = Harness()
    said: list[str] = []
    for _ in range(int(2.0 * FPS)):
        harness.clock.t += STEP
        harness.last = harness.tutor.observe(
            Observation(gesture=pose(8, 7, True), fingers=(), hands_seen=2),
            harness.clock.t)
        if harness.last.tutor_line:
            said.append(harness.last.tutor_line)
    assert said[0] == ACK
