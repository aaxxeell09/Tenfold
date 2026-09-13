"""Gentle recovery: the hands come back and nothing is judged for a moment.

Losing the hands is the camera's doing. When they are back Tally says he can
see them, asks for the exercise again, and then holds a grace of
recovery_grace_ms in which no wrong pose is spoken, drawn or counted. A right
pose is still answered instantly: the grace never delays a yes.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.tutor import (
    RECOVERY_LINE_KEY,
    VISIBILITY_RECOVERY,
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

BACK = LINES[RECOVERY_LINE_KEY]
RELAUNCH = LINES["launch"].format(a=8, b=7)
ACK = LINES["pose_ack"]

FPS = 15
STEP = 1.0 / FPS
GRACE_S = 3.0
INITIAL_SILENCE = 4.0
WRONG_POSE_PROMPT = 2.0


def pose(left: int, right: int, contact: bool = True) -> GestureState:
    return GestureState(method="6-10", left=left, right=right, contact=contact,
                        confidence=0.9)


def fingers(hands: int = 2) -> list[dict[str, object]]:
    names = {0: (), 1: ("left",), 2: ("left", "right")}[hands]
    out: list[dict[str, object]] = []
    for hand in names:
        base = 0.3 if hand == "left" else 0.7
        for number in range(6, 11):
            out.append({"hand": hand, "number": number, "x": base,
                        "y": 0.5 + 0.02 * (number - 6)})
    return out


HINT = {"hand": "right", "move_from": 9, "move_to": 7}


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def params_with(**overrides: float) -> object:
    raw = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    raw["global"].update(overrides)
    path = Path(tempfile.mkdtemp(prefix="tenfold-recovery-")) / "params.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return load_params(path)


class Harness:
    """One tutor on an 8 x 7 exercise, with a camera that can look away."""

    def __init__(self, params: object | None = None) -> None:
        self.clock = Clock()
        self.base = datetime(2026, 9, 13, 18, 0, 0, tzinfo=timezone.utc)
        self.log: list[dict[str, object]] = []
        self.tutor = Tutor(params or load_params(PARAMS_FILE),
                           load_lines(LINES_FILE), clock=self.clock,
                           log=self.log.append, learner_id="9f31c0a7bd42",
                           utc_clock=self.utc)
        self.last = Decision()
        self.visuals: list[dict[str, object] | None] = []
        self.tutor.new_exercise(8, 7, node="u2-l3", now=self.clock.t)

    def utc(self) -> datetime:
        return self.base + timedelta(seconds=self.clock.t)

    def feed(self, seconds: float, gesture: GestureState | None = None,
             hands: int = 2, hint: dict[str, object] | None = None) -> list[str]:
        said: list[str] = []
        for _ in range(int(round(seconds * FPS))):
            self.clock.t += STEP
            self.last = self.tutor.observe(
                Observation(gesture=gesture, fingers=fingers(hands),
                            hands_seen=hands, hint=hint), self.clock.t)
            self.visuals.append(self.last.tutor_visual)
            if self.last.tutor_line:
                said.append(self.last.tutor_line)
        return said

    def lose_the_hands(self) -> list[str]:
        """Settle past the opening silence, then take the hands away."""
        said = self.feed(INITIAL_SILENCE + 0.5)
        assert self.tutor.state_fields()["tutor_state"] != VISIBILITY_RECOVERY
        said += self.feed(2.0, hands=0)
        assert self.tutor.state_fields()["tutor_state"] == VISIBILITY_RECOVERY
        self.visuals.clear()
        return said


def test_the_recovery_line_is_the_owner_wording() -> None:
    assert BACK == "Good, I can see your hands."


def test_the_grace_key_is_known_with_its_bounds() -> None:
    raw = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    params = load_params(PARAMS_FILE)
    assert raw["global"]["recovery_grace_ms"] == 3000
    assert raw["bounds"]["recovery_grace_ms"] == [1500, 6000]
    assert params.bound("recovery_grace_ms") == (1500.0, 6000.0)
    with pytest.raises(ParamsError) as excinfo:
        params_with(recovery_grace_ms=100)
    assert "recovery_grace_ms" in str(excinfo.value) and "1500" in str(excinfo.value)


def test_the_grace_is_the_same_for_every_child_and_every_mode() -> None:
    harness = Harness()
    for mode in ("supportive", "normal", "independent"):
        harness.tutor._mode = mode
        assert harness.tutor.effective("recovery_grace_ms") == 3000


def test_the_hands_coming_back_says_hello_then_the_exercise() -> None:
    harness = Harness()
    harness.lose_the_hands()
    said = harness.feed(1.5, gesture=pose(8, 9, False), hint=HINT)
    assert said[:2] == [BACK, RELAUNCH]
    assert harness.tutor.in_recovery is True


def test_nothing_is_said_drawn_or_counted_about_the_pose_inside_the_window() -> None:
    harness = Harness()
    harness.lose_the_hands()
    said = harness.feed(GRACE_S - 0.2, gesture=pose(8, 9, False), hint=HINT)
    # The two recovery lines and not one word about the wrong finger, though
    # the pose has been wrong for longer than wrong_pose_prompt.
    assert said == [BACK, RELAUNCH]
    assert harness.tutor.in_recovery is True
    kinds = {visual["kind"] for visual in harness.visuals if visual}
    assert not kinds & {"correction", "ghost", "pulse_finger"}
    assert harness.last.scored_gesture_error is False
    assert harness.last.intervention_level == 0
    assert harness.last.first_try is True


def test_a_right_pose_inside_the_window_is_still_answered_at_once() -> None:
    harness = Harness()
    harness.lose_the_hands()
    harness.feed(0.6)
    assert harness.tutor.in_recovery is True
    said = harness.feed(1.2, gesture=pose(8, 7, True))
    assert ACK in said, "the grace never delays a yes"
    assert said.index(ACK) <= 2


def test_the_ladder_starts_again_at_zero_when_the_window_closes() -> None:
    harness = Harness()
    harness.lose_the_hands()
    said = harness.feed(GRACE_S + 0.2, gesture=pose(8, 9, False), hint=HINT)
    assert said == [BACK, RELAUNCH]
    assert harness.tutor.in_recovery is False
    assert harness.last.intervention_level == 0
    # The clock of the wrong pose starts at the close of the window, so the
    # first correction lands wrong_pose_prompt after it and not before.
    early = harness.feed(WRONG_POSE_PROMPT - 0.4, gesture=pose(8, 9, False), hint=HINT)
    assert early == []
    late = harness.feed(1.0, gesture=pose(8, 9, False), hint=HINT)
    assert late, "the ladder has to climb again once the grace is over"
    assert harness.last.intervention_level == 1


def test_a_recovery_inside_the_opening_silence_opens_no_second_window() -> None:
    """One hush at a time. The opening silence is already the gentle one.

    The blind spell happens before initial_silence has run, so the recovery
    opens no window of its own. The correction that follows is then held by the
    ordinary rules only, and lands well before the moment a stacked window
    would have allowed it, which is recovery plus grace plus wrong_pose_prompt.
    """
    harness = Harness(params_with(min_verbal_gap=3.0))
    harness.feed(0.5)
    said = harness.feed(2.0, hands=0)
    assert harness.tutor.state_fields()["tutor_state"] == VISIBILITY_RECOVERY
    said += harness.feed(1.0, gesture=pose(8, 9, False), hint=HINT)
    assert BACK in said and RELAUNCH in said
    assert harness.tutor.in_recovery is False
    recovered_at = harness.clock.t

    corrected_at: float | None = None
    while corrected_at is None and harness.clock.t < 12.0:
        lines = harness.feed(0.2, gesture=pose(8, 9, False), hint=HINT)
        # Not once does a window open behind the opening silence.
        assert harness.tutor.in_recovery is False
        if lines:
            corrected_at = harness.clock.t
    assert corrected_at is not None, "the ladder never climbed at all"
    assert harness.last.intervention_level == 1
    assert corrected_at < recovered_at + GRACE_S + WRONG_POSE_PROMPT


def test_the_window_is_the_parameter() -> None:
    harness = Harness(params_with(recovery_grace_ms=5000))
    harness.lose_the_hands()
    harness.feed(GRACE_S + 0.5, gesture=pose(8, 9, False), hint=HINT)
    assert harness.tutor.in_recovery is True
    harness.feed(2.5, gesture=pose(8, 9, False), hint=HINT)
    assert harness.tutor.in_recovery is False


def test_a_second_blind_spell_recovers_the_same_way() -> None:
    harness = Harness()
    harness.lose_the_hands()
    assert harness.feed(GRACE_S + 0.5, gesture=pose(8, 9, False), hint=HINT)[:2] \
        == [BACK, RELAUNCH]
    # Long enough for the remembered pose to expire as well as the hands.
    harness.feed(4.0, hands=0)
    assert harness.tutor.state_fields()["tutor_state"] == VISIBILITY_RECOVERY
    said = harness.feed(1.5, gesture=pose(8, 9, False), hint=HINT)
    assert said[:2] == [BACK, RELAUNCH]
    assert harness.tutor.in_recovery is True
