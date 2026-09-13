"""The pose confirmation is two beats, not one sentence.

The child makes the pose. Tally says yes at once, cutting whatever is playing,
and the canonical cue follows pose_ready_delay_ms later. Both lines come from
lesson/tally_lines.json: the acknowledgement is "pose_ack", the cue is the
canonical "pose_ready".

The harness here is deliberately its own: these tests own the beat and must not
move when another suite changes its fixtures.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.tutor import (
    ACK_LINE_KEY,
    POSE_READY_KEY,
    Decision,
    Observation,
    Tutor,
    load_lines,
    load_params,
)
from classifier.schema import GestureState

REPO = Path(__file__).resolve().parents[1]
PARAMS_FILE = REPO / "lesson" / "tutor_params.json"
LINES_FILE = REPO / "lesson" / "tally_lines.json"

LINES = json.loads(LINES_FILE.read_text(encoding="utf-8"))
ACK = LINES[ACK_LINE_KEY]
CUE = LINES[POSE_READY_KEY]

FPS = 15
STEP = 1.0 / FPS
POSE_STABLE = 0.8
# Amendment F8: the correct pose is confirmed in pose_confirm_frames frames or
# pose_confirm_ms, whichever first. At the harness fps the frames come first.
CONFIRM = 3 * STEP
DELAY_S = 0.8


def pose(left: int, right: int, contact: bool = True) -> GestureState:
    return GestureState(method="6-10", left=left, right=right, contact=contact,
                        confidence=0.9)


def fingers(hands: int = 2, gesture: GestureState | None = None,
            gap: float = 0.0) -> list[dict[str, object]]:
    """Fingertips as app/server.py sends them, in frame fractions.

    When the gesture claims a contact the two named tips are put where a real
    touch puts them, a hair apart, because the tutor measures the distance now
    and a pose held a hand's width apart is not a pose.
    """
    names = {0: (), 1: ("left",), 2: ("left", "right")}[hands]
    out: list[dict[str, object]] = []
    for hand in names:
        base = 0.45 if hand == "left" else 0.55
        for number in range(6, 11):
            out.append({"hand": hand, "number": number, "x": base,
                        "y": 0.5 + 0.02 * (number - 6)})
    if gesture is not None and gesture.contact and hands == 2:
        touching = {("left", gesture.left): 0.5, ("right", gesture.right): 0.5 + gap}
        for tip in out:
            where = touching.get((tip["hand"], tip["number"]))
            if where is not None:
                tip["x"], tip["y"] = where, 0.5
    return out


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def params_with(**overrides: float) -> object:
    raw = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    raw["global"].update(overrides)
    path = Path(tempfile.mkdtemp(prefix="tenfold-beat-")) / "params.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return load_params(path)


def lines_without(key: str) -> dict[str, str]:
    raw = dict(LINES)
    raw.pop(key, None)
    path = Path(tempfile.mkdtemp(prefix="tenfold-beat-")) / "lines.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return dict(load_lines(path))


class Harness:
    """One tutor on an 8 x 7 exercise, fed frame by frame."""

    def __init__(self, params: object | None = None,
                 lines: dict[str, str] | None = None) -> None:
        self.clock = Clock()
        self.base = datetime(2026, 9, 13, 18, 0, 0, tzinfo=timezone.utc)
        self.tutor = Tutor(params or load_params(PARAMS_FILE),
                           lines if lines is not None else load_lines(LINES_FILE),
                           clock=self.clock, log=None, learner_id="9f31c0a7bd42",
                           utc_clock=self.utc)
        self.last = Decision()
        self.tutor.new_exercise(8, 7, node="u2-l3", now=self.clock.t)

    def utc(self) -> datetime:
        return self.base + timedelta(seconds=self.clock.t)

    def feed(self, seconds: float, gesture: GestureState | None = None,
             hands: int = 2) -> list[tuple[float, str, bool]]:
        """Frames for this long, and every line with its moment and cut mark."""
        said: list[tuple[float, str, bool]] = []
        for _ in range(int(round(seconds * FPS))):
            self.clock.t += STEP
            self.last = self.tutor.observe(
                Observation(gesture=gesture, fingers=fingers(hands, gesture),
                            hands_seen=hands), self.clock.t)
            if self.last.tutor_line:
                said.append((round(self.clock.t, 3), self.last.tutor_line,
                             self.last.tutor_line_cuts))
        return said


def confirmed(harness: Harness, seconds: float = 3.0) -> list[tuple[float, str, bool]]:
    return harness.feed(seconds, gesture=pose(8, 7, True))


def test_the_two_beats_are_both_canonical_lines() -> None:
    assert ACK == "Yes, that's it."
    assert CUE == LINES["pose_ready"]
    assert ACK != CUE


def test_the_acknowledgement_comes_first_and_may_cut() -> None:
    said = confirmed(Harness())
    assert [line for _, line, _ in said] == [ACK, CUE]
    at_ack, _, cuts = said[0]
    assert cuts is True, "a child who has just made the pose hears yes now"
    assert at_ack == pytest.approx(CONFIRM, abs=2 * STEP)


def test_the_canonical_cue_follows_eight_hundred_milliseconds_later() -> None:
    said = confirmed(Harness())
    at_ack, _, _ = said[0]
    at_cue, line, cuts = said[1]
    assert line == CUE
    assert at_cue - at_ack == pytest.approx(DELAY_S, abs=2 * STEP)
    # Only the yes is allowed to cut. The cue waits its turn like every line.
    assert cuts is False


def test_the_delay_is_the_parameter_and_nothing_else_moves() -> None:
    harness = Harness(params_with(pose_ready_delay_ms=1500))
    said = confirmed(harness)
    assert [line for _, line, _ in said] == [ACK, CUE]
    assert said[1][0] - said[0][0] == pytest.approx(1.5, abs=2 * STEP)
    assert harness.tutor.effective("pose_ready_delay_ms") == 1500


def test_the_delay_key_is_known_with_its_bounds() -> None:
    raw = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    params = load_params(PARAMS_FILE)
    assert raw["global"]["pose_ready_delay_ms"] == 800
    assert raw["bounds"]["pose_ready_delay_ms"] == [300, 2000]
    assert params.bound("pose_ready_delay_ms") == (300.0, 2000.0)


def test_a_delay_outside_its_bounds_is_a_startup_error() -> None:
    from app.tutor import ParamsError

    with pytest.raises(ParamsError) as excinfo:
        params_with(pose_ready_delay_ms=5000)
    assert "pose_ready_delay_ms" in str(excinfo.value) and "2000" in str(excinfo.value)


def test_ack_delay_ms_moves_the_pair_and_keeps_its_shape() -> None:
    said = confirmed(Harness(params_with(ack_delay_ms=300)))
    assert [line for _, line, _ in said] == [ACK, CUE]
    assert said[0][0] == pytest.approx(CONFIRM + 0.3, abs=3 * STEP)
    assert said[1][0] - said[0][0] == pytest.approx(DELAY_S, abs=2 * STEP)


def test_without_the_acknowledgement_the_canonical_cue_plays_alone() -> None:
    """The key is optional. A file without it degrades, it does not invent."""
    said = confirmed(Harness(lines=lines_without(ACK_LINE_KEY)))
    assert [line for _, line, _ in said] == [CUE]
    assert said[0][2] is True, "the one line of the beat is still the instant one"
    assert said[0][0] == pytest.approx(CONFIRM, abs=2 * STEP)


def test_the_cue_is_dropped_when_the_exercise_is_over() -> None:
    """Nothing else about which line is said when moved with the beat."""
    harness = Harness()
    said = harness.feed(CONFIRM + 2 * STEP, gesture=pose(8, 7, True))
    assert [line for _, line, _ in said] == [ACK]
    harness.tutor.answer(56, correct=True, now=harness.clock.t)
    harness.tutor.end_exercise("answered", now=harness.clock.t)
    after = harness.feed(2.0, gesture=pose(8, 7, True))
    assert CUE not in [line for _, line, _ in after]


def test_an_answer_before_the_pose_still_hears_both_beats() -> None:
    harness = Harness()
    harness.tutor.answer(56, correct=True, now=harness.clock.t)
    said = harness.feed(3.0, gesture=pose(8, 7, True))
    assert [line for _, line, _ in said] == [ACK, CUE]


def test_a_second_exercise_starts_the_beat_again() -> None:
    harness = Harness()
    assert [line for _, line, _ in confirmed(harness)] == [ACK, CUE]
    harness.tutor.answer(56, correct=True, now=harness.clock.t)
    harness.tutor.end_exercise("answered", now=harness.clock.t)
    harness.tutor.new_exercise(7, 9, node="u2-l3", now=harness.clock.t)
    said = harness.feed(3.0, gesture=pose(7, 9, True))
    assert [line for _, line, _ in said if line in (ACK, CUE)] == [ACK, CUE]
