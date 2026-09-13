"""The opening of an exercise, in order, and the clock the ladder runs on.

  a. the exercise line, and nothing else said or drawn
  b. the hands are not in frame: the placement zones, and after
     visibility_prompt_ms the call "Come here, hands!". No advice, no ladder.
  c. both hands in frame: initial_silence of nothing at all. The child thinks.
  d. then the ladder, L1 numbers, L2 colours, L3 voice and ghost, L4 rescue.

The part that has to be exact is the clock: it runs only while both hands are
in frame. A child who takes their hands out of shot for a minute comes back to
the step they left. A right pose is acknowledged at any moment of any of this.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.tutor import (
    HANDS_PROMPT_KEY,
    PAGE_LINE_KEYS,
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
GLOBALS = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))["global"]

COME_HERE = LINES[HANDS_PROMPT_KEY]
ACK = LINES["pose_ack"]
INITIAL_SILENCE = GLOBALS["initial_silence"]
VISIBILITY_PROMPT_S = GLOBALS["visibility_prompt_ms"] / 1000.0
WRONG_POSE_PROMPT = GLOBALS["wrong_pose_prompt"]
# The first step of the ladder on a wrong pose held from the first frame: the
# end of the thinking silence, or wrong_pose_prompt, whichever comes later.
FIRST_STEP = max(INITIAL_SILENCE, WRONG_POSE_PROMPT)

FPS = 15
STEP = 1.0 / FPS
HINT = {"hand": "right", "move_from": 9, "move_to": 7}


def pose(left: int, right: int, contact: bool = True) -> GestureState:
    return GestureState(method="6-10", left=left, right=right, contact=contact,
                        confidence=0.9)


def fingers(hands: int = 2,
            gesture: GestureState | None = None) -> list[dict[str, object]]:
    names = {0: (), 1: ("left",), 2: ("left", "right")}[hands]
    out: list[dict[str, object]] = []
    for hand in names:
        base = 0.45 if hand == "left" else 0.55
        for number in range(6, 11):
            out.append({"hand": hand, "number": number, "x": base,
                        "y": 0.5 + 0.02 * (number - 6)})
    if gesture is not None and gesture.contact and hands == 2:
        touching = {("left", gesture.left), ("right", gesture.right)}
        for tip in out:
            if (tip["hand"], tip["number"]) in touching:
                tip["x"], tip["y"] = 0.5, 0.5
    return out


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class Harness:
    def __init__(self) -> None:
        self.clock = Clock()
        self.base = datetime(2026, 9, 13, 18, 0, 0, tzinfo=timezone.utc)
        self.tutor = Tutor(load_params(PARAMS_FILE), load_lines(LINES_FILE),
                           clock=self.clock, log=None, learner_id="9f31c0a7bd42",
                           utc_clock=self.utc)
        self.last = Decision()
        self.visuals: list[str | None] = []
        self.tutor.new_exercise(8, 7, node="u2-l3", now=self.clock.t)

    def utc(self) -> datetime:
        return self.base + timedelta(seconds=self.clock.t)

    def feed(self, seconds: float, gesture: GestureState | None = None,
             hands: int = 2, hint: dict[str, object] | None = None) -> list[str]:
        said: list[str] = []
        for _ in range(int(round(seconds * FPS))):
            self.clock.t += STEP
            self.last = self.tutor.observe(
                Observation(gesture=gesture, fingers=fingers(hands, gesture),
                            hands_seen=hands, hint=hint), self.clock.t)
            visual = self.last.tutor_visual
            self.visuals.append(visual["kind"] if visual else None)
            if self.last.tutor_line:
                said.append(self.last.tutor_line)
        return said


def test_the_two_new_keys_are_known_with_their_bounds() -> None:
    raw = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    params = load_params(PARAMS_FILE)
    assert raw["global"]["visibility_prompt_ms"] == 2500
    assert raw["bounds"]["visibility_prompt_ms"] == [1000, 5000]
    assert raw["global"]["gate_step_pause_ms"] == 1000
    assert raw["bounds"]["gate_step_pause_ms"] == [500, 2000]
    assert params.bound("gate_step_pause_ms") == (500.0, 2000.0)


def test_the_thinking_silence_is_the_length_the_owner_asked_for() -> None:
    assert 2.5 <= INITIAL_SILENCE <= 3.0


def test_the_gate_pause_is_read_as_written_for_every_child() -> None:
    """The page performs it. The tutor only hands it over, inside its bounds."""
    harness = Harness()
    for mode in ("supportive", "normal", "independent"):
        harness.tutor._mode = mode
        assert harness.tutor.effective("gate_step_pause_ms") == 1000


def test_the_gate_says_its_third_step_from_the_line_file() -> None:
    # The page has its own lines now, the gate's among them. The one this test
    # is about is the third step, and it is the page's, not the tutor's.
    assert "gate_ready" in PAGE_LINE_KEYS
    assert LINES["gate_ready"] == "Say: I'm ready!"
    assert "6" not in LINES["gate_ready"], "no calculation of any kind"


def test_nothing_is_said_or_drawn_before_the_hands_are_expected() -> None:
    harness = Harness()
    said = harness.feed(1.0, hands=0)
    assert said == []
    assert set(harness.visuals) == {None}


def test_the_hands_are_called_in_and_the_ladder_stays_where_it_is() -> None:
    harness = Harness()
    said = harness.feed(VISIBILITY_PROMPT_S + 0.5, hands=0)
    assert said == [COME_HERE]
    # Only the placement zones, and no step of the ladder at all.
    assert set(harness.visuals) <= {None, "placement_zones"}
    assert "placement_zones" in harness.visuals
    assert harness.last.intervention_level == 0


def test_the_ladder_clock_does_not_run_while_the_hands_are_away() -> None:
    harness = Harness()
    harness.feed(20.0, hands=0)
    assert harness.last.intervention_level == 0
    # The hands arrive on a wrong pose. The thinking silence starts here, and
    # the ladder takes its first step at the end of it, or once the pose has
    # been held wrong_pose_prompt, exactly as it would have if the hands had
    # been there from the first second.
    harness.feed(FIRST_STEP - 0.3, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.intervention_level == 0
    harness.feed(0.6, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.intervention_level == 1
    assert harness.last.tutor_visual == {"kind": "finger_numbers"}


def test_taking_the_hands_away_freezes_the_ladder_where_it_was() -> None:
    harness = Harness()
    harness.feed(FIRST_STEP + 0.3, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.intervention_level == 1
    harness.feed(12.0, hands=0)
    assert harness.last.intervention_level == 1, "no ladder with no hands"
    # Coming back is the gentle recovery's business, and it starts the ladder
    # again at L0 on purpose. What matters here is that the absence itself
    # climbed nothing: twelve seconds away, still L1.
    assert harness.tutor.in_recovery is False


def test_a_right_pose_is_acknowledged_even_in_the_opening_silence() -> None:
    harness = Harness()
    harness.feed(5.0, hands=0)
    said = harness.feed(1.5, gesture=pose(8, 7, True))
    assert said[0] == ACK, "the opening never delays a yes"


def test_a_hand_lost_later_gets_the_camera_line_not_the_opening_call() -> None:
    harness = Harness()
    harness.feed(2.0, gesture=pose(8, 9, False), hint=HINT)
    said = harness.feed(6.0, hands=0)
    assert COME_HERE not in said
    assert LINES["visibility_none"] in said


def test_a_line_file_without_the_opening_call_stays_quiet() -> None:
    raw = dict(LINES)
    raw.pop(HANDS_PROMPT_KEY)
    path = Path(tempfile.mkdtemp(prefix="tenfold-opening-")) / "lines.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    harness = Harness()
    harness.tutor.lines = dict(load_lines(path))
    said = harness.feed(VISIBILITY_PROMPT_S + 1.0, hands=0)
    assert said == []


def test_an_opening_prompt_outside_its_bounds_is_a_startup_error() -> None:
    raw = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    raw["global"]["visibility_prompt_ms"] = 20000
    path = Path(tempfile.mkdtemp(prefix="tenfold-opening-")) / "params.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ParamsError) as excinfo:
        load_params(path)
    assert "visibility_prompt_ms" in str(excinfo.value)
