"""Slower after a correction: a child changing a pose is not judged mid movement.

After any line Tally says, no further correction is spoken for
post_line_grace_ms. A changed pose is evaluated only once it has been seen
pose_confirm_frames times. And through all of it a right pose is acknowledged
at once: no grace, anywhere, ever delays a yes.

The quiet after a line is the longer of min_verbal_gap and post_line_grace_ms,
never their sum: both are measured from the same moment, the last line.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.tutor import (
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

ACK = LINES["pose_ack"]
WRONG_RIGHT = LINES["wrong_right"].format(b=7)
WRONG_LEFT = LINES["wrong_left"].format(a=8)
INITIAL_SILENCE = GLOBALS["initial_silence"]
HINT_2_DELAY = GLOBALS["hint_2_delay"]

FPS = 15
STEP = 1.0 / FPS
HINT = {"hand": "right", "move_from": 9, "move_to": 7}
OTHER_HINT = {"hand": "left", "move_from": 6, "move_to": 8}


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


def params_with(**overrides: float) -> object:
    raw = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    raw["global"].update(overrides)
    path = Path(tempfile.mkdtemp(prefix="tenfold-grace-")) / "params.json"
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
        self.tutor.new_exercise(8, 7, node="u2-l3", now=self.clock.t)

    def utc(self) -> datetime:
        return self.base + timedelta(seconds=self.clock.t)

    def feed(self, seconds: float, gesture: GestureState | None = None,
             hands: int = 2, hint: dict[str, object] | None = None,
             step: float = STEP) -> list[tuple[float, str]]:
        said: list[tuple[float, str]] = []
        for _ in range(int(round(seconds / step))):
            self.clock.t += step
            self.last = self.tutor.observe(
                Observation(gesture=gesture, fingers=fingers(hands, gesture),
                            hands_seen=hands, hint=hint), self.clock.t)
            if self.last.tutor_line:
                said.append((round(self.clock.t, 3), self.last.tutor_line))
        return said


def spoken_harness(**overrides: float) -> tuple[Harness, float]:
    """A harness that has just heard its first spoken correction, at L3."""
    harness = Harness(params_with(**overrides) if overrides else None)
    said: list[tuple[float, str]] = []
    while not said and harness.clock.t < 20.0:
        said = harness.feed(0.2, gesture=pose(8, 9, False), hint=HINT)
    assert said and said[0][1] == WRONG_RIGHT
    return harness, said[0][0]


def test_the_grace_key_is_known_with_its_bounds() -> None:
    raw = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    params = load_params(PARAMS_FILE)
    assert raw["global"]["post_line_grace_ms"] == 3000
    assert raw["bounds"]["post_line_grace_ms"] == [1500, 5000]
    assert params.bound("post_line_grace_ms") == (1500.0, 5000.0)
    with pytest.raises(ParamsError) as excinfo:
        params_with(post_line_grace_ms=200)
    assert "post_line_grace_ms" in str(excinfo.value)


def test_the_quiet_after_a_line_is_the_longer_rule_and_never_the_sum() -> None:
    harness = Harness(params_with(min_verbal_gap=3.0, post_line_grace_ms=5000))
    assert harness.tutor._quiet_after_a_line() == 5.0
    harness = Harness(params_with(min_verbal_gap=6.0, post_line_grace_ms=2500))
    assert harness.tutor._quiet_after_a_line() == 6.0
    # Never 3.0 + 5.0, and never 6.0 + 2.5.


def test_no_second_correction_inside_the_grace() -> None:
    """The child changes the pose the moment they are told. Tally waits."""
    harness, spoke_at = spoken_harness(min_verbal_gap=3.0, post_line_grace_ms=5000)
    # A different wrong pose, held from now on: nothing may be said about it
    # until the grace is over, however long it is held.
    said = harness.feed(4.5, gesture=pose(6, 7, False), hint=OTHER_HINT)
    assert said == []
    later = harness.feed(6.0, gesture=pose(6, 7, False), hint=OTHER_HINT)
    assert later, "the ladder speaks again once the grace is over"
    assert later[0][0] - spoke_at >= 5.0


def test_two_corrections_are_never_closer_than_the_grace() -> None:
    harness, spoke_at = spoken_harness(min_verbal_gap=3.0, post_line_grace_ms=4000)
    said = harness.feed(20.0, gesture=pose(8, 9, False), hint=HINT)
    moments = [spoke_at] + [at for at, _ in said]
    gaps = [b - a for a, b in zip(moments, moments[1:])]
    assert gaps, "there has to be more than one line to compare"
    assert min(gaps) >= 4.0


def test_a_right_pose_is_acknowledged_inside_the_grace() -> None:
    harness, _ = spoken_harness(min_verbal_gap=3.0, post_line_grace_ms=5000)
    said = harness.feed(1.5, gesture=pose(8, 7, True))
    assert [line for _, line in said][:1] == [ACK]


def test_a_new_pose_waits_for_pose_confirm_frames_before_it_is_judged() -> None:
    """A camera dropping frames may not have a pose judged on two of them."""
    harness = Harness(params_with(pose_confirm_frames=6, pose_stable=0.5))
    # Four frames a second: pose_stable has run long before six frames have.
    said = harness.feed(1.25, gesture=pose(8, 7, True), step=0.25)
    assert said == [], "five slow frames are not a confirmed pose"
    said = harness.feed(0.5, gesture=pose(8, 7, True), step=0.25)
    assert [line for _, line in said] == [ACK]


def test_the_grace_does_not_hold_back_the_acknowledgement_of_a_second_pose() -> None:
    harness = Harness(params_with(post_line_grace_ms=5000))
    harness.feed(INITIAL_SILENCE + 1.0, gesture=pose(8, 9, False), hint=HINT)
    said = harness.feed(1.5, gesture=pose(8, 7, True))
    assert [line for _, line in said][:1] == [ACK]
