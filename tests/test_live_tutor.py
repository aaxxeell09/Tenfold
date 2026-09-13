"""app/tutor.py against docs/tutor_contract.md.

Injected clock, no sleeps anywhere: a whole lesson runs in microseconds and the
same lesson runs the same way twice. Every invariant of contract section 2.5 has
its own test, named for it, because those are the rules that decide the arguments
the numbers cannot.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pytest

from app.tutor import (
    ANSWER_RETRY,
    TIP_SPREAD_PALMS,
    BEAT_PARAMS,
    BEAT_PARTS,
    BEAT_SUCCESS,
    CHECK_ANSWER,
    DROP_REASONS,
    INDEPENDENT,
    LINE_KEYS,
    NORMAL,
    OPTIONAL_LINE_KEYS,
    PAUSED,
    POSE_READY,
    PROMPTING,
    REQUIRED_PARAMS,
    SUCCESS,
    SUPPORTIVE,
    VISIBILITY_RECOVERY,
    WORKING,
    WRONG_POSE,
    Decision,
    Observation,
    ParamsError,
    Tutor,
    TutorParams,
    jsonl_sink,
    load_lines,
    load_params,
    nearest_gap,
    tip_positions,
    NOMINAL_PALM,
    DROP_REPLACED,
    DROP_STALE,
    DROP_QUEUE_FULL,
)
from classifier.schema import GestureState

REPO = Path(__file__).resolve().parents[1]
PARAMS_FILE = REPO / "lesson" / "tutor_params.json"
LINES_FILE = REPO / "lesson" / "tally_lines.json"

FPS = 15
STEP = 1.0 / FPS
UNKNOWN = GestureState.unknown(0.1)


def pose(left: int, right: int, contact: bool = True,
         confidence: float = 0.9) -> GestureState:
    """A 6-10 reading. confidence below 0.8 is a classifier that is not sure."""
    return GestureState(method="6-10", left=left, right=right, contact=contact,
                        confidence=confidence)


def fingers(dx: float = 0.0, hands: int = 2,
            touch: tuple[int, int] | None = None) -> list[dict[str, object]]:
    """The fingertips of a frame, as app/server.py sends them.

    touch is the pair the classifier says is touching, left number then right.
    The tutor reads the gap between those two tips and refuses a pose held
    apart, so a fixture that claims contact has to put them in the same place,
    the way two hands meeting in the middle of the frame do.
    """
    names = {0: (), 1: ("left",), 2: ("left", "right")}[hands]
    out: list[dict[str, object]] = []
    for hand in names:
        base = 0.3 if hand == "left" else 0.7
        for number in range(6, 11):
            out.append({"hand": hand, "number": number, "x": base + dx,
                        "y": 0.5 + 0.02 * (number - 6)})
    if touch is not None and len(names) == 2:
        meeting = 0.5 + dx
        for tip in out:
            if ((tip["hand"] == "left" and tip["number"] == touch[0])
                    or (tip["hand"] == "right" and tip["number"] == touch[1])):
                tip["x"] = meeting
                tip["y"] = 0.5
    return out


def _touching(gesture: GestureState | None) -> tuple[int, int] | None:
    """The pair to draw as meeting: the one the classifier calls a touch."""
    if gesture is None or not gesture.contact:
        return None
    if gesture.left is None or gesture.right is None:
        return None
    return int(gesture.left), int(gesture.right)


class Clock:
    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def tick(self, dt: float = STEP) -> float:
        self.t += dt
        return self.t


def params_with(**overrides: float) -> object:
    """The real file with a few values replaced, still inside its own bounds."""
    raw = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    raw["global"].update(overrides)
    path = Path(_tmp()) / "tutor_params.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return load_params(path)


_TMP: list[str] = []


def _tmp() -> str:
    import tempfile
    if not _TMP:
        _TMP.append(tempfile.mkdtemp(prefix="tenfold-tutor-"))
    return _TMP[0]


class Harness:
    """One tutor, one clock, one log, and a way to feed it frames."""

    def __init__(self, params: object | None = None,
                 factors: dict[str, object] | None = None,
                 learner_id: str = "9f31c0a7bd42") -> None:
        self.clock = Clock()
        self.log: list[dict[str, object]] = []
        self.base = datetime(2026, 9, 13, 18, 0, 0, tzinfo=timezone.utc)
        self.tutor = Tutor(params or load_params(PARAMS_FILE),
                           load_lines(LINES_FILE), clock=self.clock,
                           log=self.log.append, learner_id=learner_id,
                           factors=factors, utc_clock=self.utc)
        self.last = Decision()

    def utc(self) -> datetime:
        return self.base + timedelta(seconds=self.clock.t)

    def feed(self, seconds: float, gesture: GestureState | None = UNKNOWN,
             hands: int = 2, hint: dict[str, object] | None = None,
             drift: float = 0.0) -> list[str]:
        said: list[str] = []
        for index in range(int(round(seconds * FPS))):
            self.clock.tick()
            obs = Observation(gesture=gesture,
                              fingers=fingers(drift * (index + 1), hands,
                                              _touching(gesture)),
                              hands_seen=hands, hint=hint)
            self.last = self.tutor.observe(obs, self.clock.t)
            if self.last.tutor_line:
                said.append(self.last.tutor_line)
        return said

    def feed_marked(self, seconds: float, gesture: GestureState | None = UNKNOWN,
                    hands: int = 2, hint: dict[str, object] | None = None,
                    ) -> list[tuple[str, bool]]:
        """Like feed, but every line comes back with its cut mark."""
        marked: list[tuple[str, bool]] = []
        for _ in range(int(round(seconds * FPS))):
            self.clock.tick()
            obs = Observation(gesture=gesture,
                              fingers=fingers(0.0, hands, _touching(gesture)),
                              hands_seen=hands, hint=hint)
            self.last = self.tutor.observe(obs, self.clock.t)
            if self.last.tutor_line:
                marked.append((self.last.tutor_line, self.last.tutor_line_cuts))
        return marked

    def feed_each(self, seconds: float,
                  gesture_at: "Callable[[int], GestureState | None]",
                  hands: int = 2, hint: dict[str, object] | None = None,
                  ) -> list[tuple[int, str]]:
        """Like feed, with the gesture chosen per frame; lines come back with
        the index of the frame they were said on."""
        said: list[tuple[int, str]] = []
        for index in range(int(round(seconds * FPS))):
            self.clock.tick()
            gesture = gesture_at(index)
            obs = Observation(gesture=gesture,
                              fingers=fingers(0.0, hands, _touching(gesture)),
                              hands_seen=hands, hint=hint)
            self.last = self.tutor.observe(obs, self.clock.t)
            if self.last.tutor_line:
                said.append((index, self.last.tutor_line))
        return said

    def kind(self, name: str) -> list[dict[str, object]]:
        return [line for line in self.log if line.get("kind") == name]

    def levels(self) -> list[int]:
        return [int(line["intervention"]) for line in self.kind("intervention")]


HINT = {"hand": "right", "move_from": 9, "move_to": 7}


def wrong_pose_harness(params: object | None = None) -> Harness:
    """An 8 x 7 exercise with 9 held on the right hand instead of 7."""
    harness = Harness(params=params)
    harness.tutor.new_exercise(8, 7, node="u2-l3", now=harness.clock.t)
    return harness


# --------------------------------------------------------------------------
# 1. the params file
# --------------------------------------------------------------------------


# Keys of the product that live in the same file without being tutor timings.
# The tutor never reads them, so they are allowed here and nowhere else.
# live_sample_windows sizes the perception buffer; gate_ready_button_s is how
# long the page waits before the Ready button of a gate with no microphone.
NON_TUTOR_PARAMS = frozenset({"live_sample_windows", "gate_ready_button_s"})


def test_params_file_has_exactly_the_contract_keys() -> None:
    raw = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    assert list(raw) == ["global", "bounds", "invariants"]
    # Strict about the tutor's own keys, so a typo in one of them still fails.
    assert set(REQUIRED_PARAMS) <= set(raw["global"])
    assert set(REQUIRED_PARAMS) <= set(raw["bounds"])
    assert set(raw["global"]) - set(REQUIRED_PARAMS) <= NON_TUTOR_PARAMS
    assert set(raw["bounds"]) - set(REQUIRED_PARAMS) <= NON_TUTOR_PARAMS
    assert set(raw["global"]) == set(raw["bounds"])
    assert len(raw["invariants"]) == 6


def test_params_values_are_the_contract_values() -> None:
    params = load_params(PARAMS_FILE)
    assert params.values["fps"] == 15
    assert params.values["pose_stable"] == 0.8
    assert params.values["rescue_delay"] == 14.0
    assert params.values["max_unsolicited_verbal"] == 3
    assert params.values["independent_mode_factor"] == 1.4
    assert params.bound("wrong_pose_prompt") == (1.2, 4.0)


def test_params_version_is_twelve_hex_and_ignores_the_invariants() -> None:
    raw = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    first = load_params(PARAMS_FILE).params_version
    assert len(first) == 12 and int(first, 16) >= 0
    raw["invariants"] = ["reworded, still prose"]
    path = Path(_tmp()) / "prose.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert load_params(path).params_version == first
    raw["global"]["idle_nudge"] = 5.5
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert load_params(path).params_version != first


def test_a_value_outside_its_bounds_is_a_startup_error() -> None:
    raw = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    raw["global"]["rescue_delay"] = 99.0
    path = Path(_tmp()) / "out_of_bounds.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ParamsError) as excinfo:
        load_params(path)
    message = str(excinfo.value)
    assert "rescue_delay" in message and "10.0" in message and "25.0" in message


def test_a_missing_key_is_a_startup_error() -> None:
    raw = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    del raw["global"]["pose_memory"]
    path = Path(_tmp()) / "missing_key.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ParamsError, match="pose_memory"):
        load_params(path)


def test_a_missing_block_is_a_startup_error() -> None:
    path = Path(_tmp()) / "missing_block.json"
    path.write_text(json.dumps({"global": {}, "bounds": {}}), encoding="utf-8")
    with pytest.raises(ParamsError, match="invariants"):
        load_params(path)


def test_invariant_no_parameter_can_leave_its_bounds() -> None:
    harness = Harness(factors={"pace_factor": 9.0, "help_factor": 9.0,
                               "tutor_observations": 500})
    for key in REQUIRED_PARAMS:
        low, high = harness.tutor.params.bound(key)
        assert low <= harness.tutor.effective(key) <= high, key
    harness.tutor._mode = SUPPORTIVE
    for key in REQUIRED_PARAMS:
        low, high = harness.tutor.params.bound(key)
        assert low <= harness.tutor.effective(key) <= high, key


# --------------------------------------------------------------------------
# 2. the twenty lines
# --------------------------------------------------------------------------


def test_there_are_exactly_twenty_lines() -> None:
    raw = json.loads(LINES_FILE.read_text(encoding="utf-8"))
    # The twenty required lines, plus whichever optional ones the file has
    # grown since: "next_one" closes the success beat and is added by hand.
    assert set(LINE_KEYS) <= set(raw)
    assert set(raw) - set(LINE_KEYS) <= set(OPTIONAL_LINE_KEYS)
    assert len(raw) >= 20
    for key, text in raw.items():
        assert len(text.split()) < 20, key


def test_a_missing_or_unknown_line_is_refused() -> None:
    raw = json.loads(LINES_FILE.read_text(encoding="utf-8"))
    short = dict(raw)
    short.pop("rescue")
    path = Path(_tmp()) / "short_lines.json"
    path.write_text(json.dumps(short), encoding="utf-8")
    with pytest.raises(ParamsError, match="rescue"):
        load_lines(path)
    extra = dict(raw, whistle="Tally whistles.")
    path.write_text(json.dumps(extra), encoding="utf-8")
    with pytest.raises(ParamsError, match="whistle"):
        load_lines(path)


# --------------------------------------------------------------------------
# 3. states and transitions
# --------------------------------------------------------------------------


def test_prompting_holds_until_tts_ends() -> None:
    harness = Harness()
    harness.tutor.new_exercise(8, 7, node="n", now=0.0)
    harness.tutor.tts_start(now=0.0)
    harness.feed(1.0, gesture=UNKNOWN)
    assert harness.last.tutor_state == PROMPTING
    harness.tutor.tts_end(now=harness.clock.t)
    harness.feed(1.0, gesture=UNKNOWN)
    assert harness.last.tutor_state == WORKING


def test_working_becomes_wrong_pose_once_the_grace_has_run() -> None:
    harness = wrong_pose_harness()
    grace = harness.tutor.effective("wrong_pose_prompt")
    harness.feed(grace, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.tutor_state == WORKING
    harness.feed(1.5, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.tutor_state == WRONG_POSE


def test_pose_ready_then_check_answer_then_success() -> None:
    harness = wrong_pose_harness()
    while harness.last.tutor_state != POSE_READY and harness.clock.t < 5.0:
        harness.feed(STEP, gesture=pose(8, 7, True))
    assert harness.last.tutor_state == POSE_READY
    assert harness.clock.t == pytest.approx(3 * STEP, abs=STEP)
    harness.feed(STEP, gesture=pose(8, 7, True))
    assert harness.last.tutor_state == CHECK_ANSWER
    harness.tutor.answer(56, correct=True, now=harness.clock.t)
    assert harness.tutor.state_fields()["tutor_state"] == SUCCESS


def test_a_wrong_answer_moves_to_answer_retry() -> None:
    harness = wrong_pose_harness()
    harness.feed(1.5, gesture=pose(8, 7, True))
    decision = harness.tutor.answer(54, correct=False, now=harness.clock.t)
    assert decision.tutor_state == ANSWER_RETRY
    assert decision.scored_math_error is True
    decision = harness.tutor.answer(56, correct=True, now=harness.clock.t)
    assert decision.tutor_state == SUCCESS


def test_no_hands_reaches_visibility_recovery() -> None:
    harness = wrong_pose_harness()
    harness.feed(2.0, gesture=None, hands=0)
    assert harness.last.tutor_state == VISIBILITY_RECOVERY
    assert harness.last.tutor_visual == {"kind": "placement_zones"}


def test_one_hand_reaches_visibility_recovery_and_says_so() -> None:
    harness = wrong_pose_harness()
    said = harness.feed(3.0, gesture=None, hands=1)
    assert harness.last.tutor_state == VISIBILITY_RECOVERY
    assert said == ["I can see one hand. Show me both."]


def test_paused_after_no_engagement_and_any_speech_leaves_it() -> None:
    harness = wrong_pose_harness()
    harness.feed(32.0, gesture=pose(8, 7, True))
    assert harness.last.tutor_state == PAUSED
    harness.tutor.speech("i am here", now=harness.clock.t)
    harness.feed(0.2, gesture=pose(8, 7, True))
    assert harness.last.tutor_state != PAUSED


# --------------------------------------------------------------------------
# 4. the ladder
# --------------------------------------------------------------------------


def test_the_whole_ladder_with_its_lines_and_visuals() -> None:
    harness = wrong_pose_harness(params_with(max_unsolicited_verbal=4))
    said = harness.feed(20.0, gesture=pose(8, 9, False), hint=HINT)
    assert len(said) >= 4
    assert said[0] == "Take your time. I'm watching your hands."
    assert said[1] == "Your left hand is right. Find 7 on your right hand."
    assert said[2] == "Move this finger here."
    assert said[3] == "5 tens make 50. 2 times 3 makes 6. That gives 56."
    assert harness.last.intervention_level == 4
    assert harness.last.tutor_visual == {"kind": "rescue_card", "tens": 5,
                                         "units": 6, "total": 56}
    harness.tutor.end_exercise(reason="skipped", now=harness.clock.t)
    assert harness.levels()[:4] == [1, 2, 3, 4]


def test_each_ladder_level_draws_its_own_visual() -> None:
    harness = wrong_pose_harness(params_with(max_unsolicited_verbal=4))
    seen = []
    for _ in range(4):
        before = harness.last.intervention_level
        while harness.last.intervention_level == before and harness.clock.t < 60:
            harness.feed(0.2, gesture=pose(8, 9, False), hint=HINT)
        seen.append(harness.last.tutor_visual)
    assert seen[0] == {"kind": "pulse_finger", "hand": "right", "finger": 7}
    assert seen[1] == {"kind": "correction", "wrong_hand": "right",
                       "expected_finger": 7}
    assert seen[2] == {"kind": "ghost", "hand": "right", "from": 9, "to": 7}
    assert seen[3]["kind"] == "rescue_card"


def test_the_level_never_goes_down_and_never_skips_a_step() -> None:
    harness = wrong_pose_harness(params_with(max_unsolicited_verbal=4))
    levels = []
    for _ in range(120):
        harness.feed(0.2, gesture=pose(8, 9, False), hint=HINT)
        levels.append(harness.last.intervention_level)
    for before, after in zip(levels, levels[1:]):
        assert after >= before
        assert after - before <= 1


def test_l3_is_barred_before_hint_2_delay() -> None:
    harness = wrong_pose_harness(params_with(initial_silence=2.5, min_verbal_gap=3.0,
                                             max_unsolicited_verbal=4))
    harness.feed(8.6, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.intervention_level == 2
    harness.feed(6.0, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.intervention_level >= 3


def test_l4_is_barred_before_rescue_delay() -> None:
    harness = wrong_pose_harness(params_with(initial_silence=2.5, min_verbal_gap=3.0,
                                             hint_2_delay=6.0,
                                             max_unsolicited_verbal=4))
    harness.feed(13.0, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.intervention_level == 3
    harness.feed(4.0, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.intervention_level == 4


def test_the_ladder_climbs_on_the_clocks_of_the_params_file() -> None:
    """A wrong pose held from the first frame, timed against the file.

    Nothing before initial_silence. L1 once the pose has been held
    wrong_pose_prompt, or at the end of the silence, whichever is later. L2 one
    wrong_pose_prompt after L1. L3, the first spoken line, at hint_2_delay on
    the ladder clock and never sooner than wrong_pose_prompt after L2. L4 at
    rescue_delay. No two spoken lines closer than post_line_grace_ms.
    """
    harness = wrong_pose_harness()
    reached: dict[int, float] = {}
    spoken: list[float] = []
    level = 0
    while harness.clock.t < 20.0:
        harness.feed(1.0 / FPS, gesture=pose(8, 9, False), hint=HINT)
        if harness.last.intervention_level != level:
            level = harness.last.intervention_level
            reached[level] = harness.clock.t
        if harness.last.tutor_line:
            spoken.append(harness.clock.t)
    slack = 0.3
    silence = GLOBALS["initial_silence"]
    prompt = GLOBALS["wrong_pose_prompt"]
    first_step = max(silence, prompt)
    assert sorted(reached) == [1, 2, 3, 4]
    assert first_step <= reached[1] <= first_step + slack
    assert reached[1] >= silence, "nothing inside the thinking silence"
    assert reached[1] + prompt <= reached[2] <= reached[1] + prompt + slack
    third = max(reached[2] + prompt, GLOBALS["hint_2_delay"])
    assert third <= reached[3] <= third + slack
    rescue = GLOBALS["rescue_delay"]
    assert rescue <= reached[4] <= rescue + slack
    assert spoken and spoken[0] == reached[3], "L3 is the first word"
    assert len(spoken) >= 2
    grace = GLOBALS["post_line_grace_ms"] / 1000.0
    gaps = [after - before for before, after in zip(spoken, spoken[1:])]
    assert min(gaps) >= grace
    assert min(gaps) >= GLOBALS["min_verbal_gap"]


def test_the_level_resets_on_a_new_exercise() -> None:
    harness = wrong_pose_harness()
    harness.feed(10.0, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.intervention_level >= 1
    decision = harness.tutor.new_exercise(6, 6, node="u2-l3", now=harness.clock.t)
    assert decision.intervention_level == 0


def test_no_contact_and_swapped_get_their_own_lines() -> None:
    harness = wrong_pose_harness()
    said = harness.feed(6.0, gesture=pose(8, 7, False))
    assert said and said[0] == "You found the fingers. Touch them together."
    assert harness.last.tutor_visual == {"kind": "finger_numbers"}

    other = wrong_pose_harness()
    said = other.feed(6.0, gesture=pose(7, 8, False))
    assert said and said[0] == ("Check both fingers. Find 8 on the left "
                                "and 7 on the right.")


def test_the_correction_names_the_hand_or_the_two_hands_that_are_wrong() -> None:
    left_hint = {"hand": "left", "move_from": 9, "move_to": 8}
    left = wrong_pose_harness()
    said = left.feed(16.0, gesture=pose(9, 7, False), hint=left_hint)
    assert "Your right hand is right. Find 8 on your left hand." in said

    both = wrong_pose_harness()
    said = both.feed(16.0, gesture=pose(10, 10, False), hint=left_hint)
    assert "Check both fingers. Find 8 on the left and 7 on the right." in said


def test_the_counting_nudge_counts_the_tens_then_the_fingers_above() -> None:
    harness = wrong_pose_harness()
    said = harness.feed(20.0, gesture=pose(8, 7, True))
    # The acknowledgement of the confirmed pose comes first, then the two halves
    # of the method in the order they are counted.
    assert said[:3] == ["You have the pose. Now count the tens.",
                        "Count the touching fingers and the ones below.",
                        "Now multiply the fingers above."]


def test_a_requested_hint_climbs_one_step_and_never_counts_as_nagging() -> None:
    harness = wrong_pose_harness()
    harness.feed(1.0, gesture=pose(8, 9, False), hint=HINT)
    decision = harness.tutor.hint_requested(now=harness.clock.t)
    assert decision.intervention_level == 1
    assert decision.tutor_line == "Start by finding 8 on your left hand."
    harness.tutor.end_exercise(reason="skipped", now=harness.clock.t)
    line = harness.kind("exercise")[0]
    assert line["unsolicited_interventions"] == 0
    assert line["first_try"] is False


# --- the second wrong answer, the third trigger of L4 ----------------------

NEXT_LINE = json.loads(LINES_FILE.read_text(encoding="utf-8"))["next_one"]
RESCUE_LINE = json.loads(LINES_FILE.read_text(encoding="utf-8"))["rescue"].format(
    tens=5, tens_value=50, u1=2, u2=3, units=6, total=56)


def answering_harness(params: object | None = None) -> Harness:
    """8 x 7 with the pose already made, so only the number is left to find."""
    harness = wrong_pose_harness(params)
    harness.feed(1.5, gesture=pose(8, 7, True))
    return harness


def wait(harness: Harness, seconds: float = 5.0) -> None:
    """Time passes with no frame and no line, so min_verbal_gap is satisfied."""
    harness.clock.tick(seconds)


def test_the_second_wrong_answer_reaches_the_rescue() -> None:
    harness = answering_harness()
    wait(harness)
    first = harness.tutor.answer(54, correct=False, now=harness.clock.t)
    assert first.tutor_line == "Keep the pose. Count the tens again."
    assert first.intervention_level < 4

    wait(harness)
    second = harness.tutor.answer(55, correct=False, now=harness.clock.t)
    # The canonical rescue line of lesson/tally_lines.json, numbers filled in.
    assert second.tutor_line == RESCUE_LINE
    assert second.tutor_line == "5 tens make 50. 2 times 3 makes 6. That gives 56."
    assert second.intervention_level == 4
    assert second.tutor_visual == {"kind": "rescue_card", "tens": 5,
                                   "units": 6, "total": 56}
    # The exercise then waits for the child to say the answer.
    assert second.tutor_state == ANSWER_RETRY
    assert harness.tutor.answer(56, correct=True,
                                now=harness.clock.t).tutor_state == SUCCESS
    harness.tutor.end_exercise(reason="answered", now=harness.clock.t)
    assert harness.kind("exercise")[0]["rescue_used"] is True


def test_the_wrong_answer_rescue_is_not_gated_by_rescue_delay() -> None:
    """Two wrong answers are the evidence, so the exercise clock never gates
    them: this trigger reaches L4 well before rescue_delay has run."""
    harness = answering_harness()
    wait(harness)
    harness.tutor.answer(54, correct=False, now=harness.clock.t)
    wait(harness)
    decision = harness.tutor.answer(55, correct=False, now=harness.clock.t)
    assert harness.clock.t < harness.tutor.effective("rescue_delay")
    assert decision.intervention_level == 4


def test_the_wrong_answer_rescue_sits_outside_max_unsolicited_verbal() -> None:
    """The cap is the budget of L1, L2 and L3 only, here spent to the last line
    before the two wrong answers ask for the rescue."""
    harness = wrong_pose_harness()
    assert harness.tutor.effective("max_unsolicited_verbal") == 3
    assert len(harness.feed(40.0, gesture=UNKNOWN, hands=2)) == 3
    assert any(line["reason"] == "budget_spent" for line in harness.kind("decision"))

    wait(harness)
    harness.tutor.answer(54, correct=False, now=harness.clock.t)
    wait(harness)
    decision = harness.tutor.answer(55, correct=False, now=harness.clock.t)
    assert decision.tutor_line == RESCUE_LINE
    assert decision.intervention_level == 4


def test_the_wrong_answer_rescue_obeys_min_verbal_gap() -> None:
    """A second wrong answer inside the gap is still a wrong answer: the rescue
    is dropped rather than queued, and the wrong answer keeps its own line."""
    harness = answering_harness()
    wait(harness)
    harness.tutor.answer(54, correct=False, now=harness.clock.t)
    harness.clock.tick(1.0)
    decision = harness.tutor.answer(55, correct=False, now=harness.clock.t)
    assert decision.tutor_line == "Let's do the tens first."
    assert decision.intervention_level < 4
    assert any(line["reason"] == "min_verbal_gap" for line in harness.kind("decision"))
    harness.tutor.end_exercise(reason="skipped", now=harness.clock.t)
    assert harness.kind("exercise")[0]["rescue_used"] is False


def test_an_utterance_that_does_not_parse_is_not_a_wrong_answer() -> None:
    """Only the wrong final numbers that score a math error count towards the
    two. Speech never submits, so it never brings the rescue closer."""
    harness = answering_harness()
    for text in ("uhh", "is it, um"):
        wait(harness)
        harness.tutor.speech(text, number=None, now=harness.clock.t)
    wait(harness)
    decision = harness.tutor.answer(54, correct=False, now=harness.clock.t)
    assert decision.tutor_line == "Keep the pose. Count the tens again."
    assert decision.intervention_level < 4
    harness.tutor.end_exercise(reason="skipped", now=harness.clock.t)
    line = harness.kind("exercise")[0]
    assert line["rescue_used"] is False
    assert line["scored_math_errors"] == 1


def test_the_wrong_answer_count_resets_on_a_new_exercise() -> None:
    harness = answering_harness()
    wait(harness)
    harness.tutor.answer(54, correct=False, now=harness.clock.t)
    wait(harness)
    harness.tutor.new_exercise(8, 7, node="u2-l3", now=harness.clock.t)
    harness.feed(1.5, gesture=pose(8, 7, True))

    wait(harness)
    first = harness.tutor.answer(54, correct=False, now=harness.clock.t)
    assert first.tutor_line == "Keep the pose. Count the tens again."
    assert first.intervention_level < 4
    wait(harness)
    assert harness.tutor.answer(55, correct=False,
                                now=harness.clock.t).intervention_level == 4


def test_the_wrong_answer_rescue_stays_one_per_exercise() -> None:
    harness = answering_harness()
    said = []
    for value in (54, 55, 57, 58):
        wait(harness)
        said.append(harness.tutor.answer(value, correct=False,
                                         now=harness.clock.t).tutor_line)
    # The rescue takes the place of wrong_answer_2, and the sequence goes on
    # from wrong_answer_3 for the wrong answers that follow it.
    assert said == ["Keep the pose. Count the tens again.",
                    RESCUE_LINE,
                    "Now check the fingers above.",
                    "Now check the fingers above."]
    harness.tutor.end_exercise(reason="skipped", now=harness.clock.t)
    assert harness.levels().count(4) == 1
    assert harness.kind("exercise")[0]["scored_math_errors"] == 4


def test_a_rescue_already_walked_through_is_never_walked_through_twice() -> None:
    """The clock got there first, so the two wrong answers find it spent."""
    harness = wrong_pose_harness()
    harness.feed(30.0, gesture=pose(8, 7, False))
    assert harness.last.intervention_level == 4
    said = []
    for value in (54, 55):
        wait(harness)
        said.append(harness.tutor.answer(value, correct=False,
                                         now=harness.clock.t).tutor_line)
    assert said == ["Keep the pose. Count the tens again.",
                    "Let's do the tens first."]
    harness.tutor.end_exercise(reason="skipped", now=harness.clock.t)
    assert harness.levels().count(4) == 1


def test_the_rescue_reached_by_the_clock_costs_first_try_and_scores_nothing() -> None:
    """Unchanged behaviour, guarded here: the pose holds the right numbers and
    is simply not touching, so nothing is ever scored, and the rescue on its
    own is what ends first try."""
    harness = wrong_pose_harness()
    harness.feed(30.0, gesture=pose(8, 7, False))
    assert harness.last.intervention_level == 4
    assert harness.last.first_try is False
    harness.tutor.end_exercise(reason="skipped", now=harness.clock.t)
    line = harness.kind("exercise")[0]
    assert line["rescue_used"] is True
    assert line["first_try"] is False
    assert line["scored_gesture_errors"] == 0
    assert line["scored_math_errors"] == 0


# --------------------------------------------------------------------------
# 5. the anti nag limits
# --------------------------------------------------------------------------


def test_max_unsolicited_verbal_caps_the_nudges_and_holds_the_fourth_back() -> None:
    harness = wrong_pose_harness()
    assert harness.tutor.effective("max_unsolicited_verbal") == 3
    said = harness.feed(40.0, gesture=UNKNOWN, hands=2)
    assert said == ["Show me 8 times 7 with your hands.",
                    "Take your time. I'm watching your hands.",
                    "Start by finding 8 on your left hand."]
    assert any(line["reason"] == "budget_spent" for line in harness.kind("decision"))


def test_the_ladder_walks_l1_l2_l3_and_still_reaches_the_rescue() -> None:
    """The cap is the budget of L1, L2 and L3. L4 is outside it, or it would
    never be reachable: the three steps below always spend the budget first."""
    harness = wrong_pose_harness()
    assert harness.tutor.effective("max_unsolicited_verbal") == 3
    said = harness.feed(40.0, gesture=pose(8, 9, False), hint=HINT)
    assert said == ["Take your time. I'm watching your hands.",
                    "Your left hand is right. Find 7 on your right hand.",
                    "Move this finger here.",
                    "5 tens make 50. 2 times 3 makes 6. That gives 56."]
    assert harness.last.intervention_level == 4
    harness.tutor.end_exercise(reason="skipped", now=harness.clock.t)
    assert harness.levels() == [1, 2, 3, 4]
    assert harness.kind("exercise")[0]["rescue_used"] is True


def test_the_rescue_is_walked_through_once_per_exercise() -> None:
    rescue = "5 tens make 50. 2 times 3 makes 6. That gives 56."
    harness = wrong_pose_harness()
    said = harness.feed(40.0, gesture=pose(8, 9, False), hint=HINT)
    assert said.count(rescue) == 1

    again: list[str] = []
    for _ in range(6):
        # Movement keeps the child engaged, so the clocks run on instead of
        # stopping at no_engagement_pause, and a second rescue would have time.
        harness.feed(0.4, gesture=pose(8, 9, False), hint=HINT, drift=0.05)
        again += harness.feed(4.0, gesture=pose(8, 9, False), hint=HINT)
    assert again == []
    assert harness.tutor.hint_requested(now=harness.clock.t).tutor_line is None
    harness.tutor.end_exercise(reason="skipped", now=harness.clock.t)
    assert harness.levels().count(4) == 1

    harness.tutor.new_exercise(8, 7, node="u2-l3", now=harness.clock.t)
    said = harness.feed(40.0, gesture=pose(8, 9, False), hint=HINT)
    assert said.count(rescue) == 1


def test_help_factor_raises_the_budget_and_never_a_duration() -> None:
    harness = Harness(factors={"pace_factor": 1.0, "help_factor": 5.0,
                               "tutor_observations": 50})
    assert harness.tutor.effective("max_unsolicited_verbal") == 4
    assert harness.tutor.effective("max_visibility_reminders") == 3
    written = load_params(PARAMS_FILE).values["wrong_pose_prompt"]
    assert harness.tutor.effective("wrong_pose_prompt") == written


def test_max_visibility_reminders_caps_the_visibility_help() -> None:
    harness = wrong_pose_harness()
    harness.feed(40.0, gesture=None, hands=0)
    visibility = [line for line in harness.kind("intervention")
                  if line["state_before"] in (WORKING, VISIBILITY_RECOVERY, PROMPTING)]
    assert len(visibility) == 2


def test_the_second_spoken_visibility_reminder_asks_for_the_hands_to_stay() -> None:
    harness = wrong_pose_harness()
    said = harness.feed(12.0, gesture=None, hands=1)
    assert said == ["I can see one hand. Show me both.",
                    "Keep your hands where I can see them."]


def test_min_verbal_gap_drops_a_line_rather_than_queueing_it() -> None:
    harness = wrong_pose_harness(params_with(initial_silence=2.5, min_verbal_gap=8.0,
                                             wrong_pose_prompt=1.2,
                                             max_unsolicited_verbal=4))
    harness.feed(9.0, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.intervention_level == 1
    assert any(line["reason"] == "min_verbal_gap" for line in harness.kind("decision"))
    harness.feed(3.0, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.intervention_level == 2


# --------------------------------------------------------------------------
# 6. the timers
# --------------------------------------------------------------------------


def test_timers_pause_while_tally_speaks_and_resume_half_a_second_after() -> None:
    quiet = wrong_pose_harness()
    quiet.feed(5.0, gesture=pose(8, 9, False), hint=HINT)
    assert quiet.last.intervention_level == 1

    talking = wrong_pose_harness()
    talking.tutor.tts_start(now=0.0)
    talking.feed(5.0, gesture=pose(8, 9, False), hint=HINT)
    assert talking.last.intervention_level == 0
    talking.tutor.tts_end(now=talking.clock.t)
    talking.feed(0.4, gesture=pose(8, 9, False), hint=HINT)
    assert talking.last.intervention_level == 0
    talking.feed(5.0, gesture=pose(8, 9, False), hint=HINT)
    assert talking.last.intervention_level == 1


def test_two_tts_false_in_a_row_are_one_event() -> None:
    harness = wrong_pose_harness()
    harness.tutor.tts_start(now=0.0)
    harness.tutor.tts_end(now=1.0)
    harness.tutor.tts_end(now=1.0)
    harness.clock.t = 1.0
    harness.feed(5.2, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.intervention_level == 1


def test_invariant_movement_means_tally_is_silent() -> None:
    moving = wrong_pose_harness()
    said = moving.feed(15.0, gesture=pose(8, 9, False), hint=HINT, drift=0.05)
    assert said == []
    assert moving.last.intervention_level == 0
    said = moving.feed(8.0, gesture=pose(8, 9, False), hint=HINT)
    assert said != []


def test_a_line_becomes_possible_again_after_post_movement_silence() -> None:
    harness = wrong_pose_harness()
    harness.feed(6.0, gesture=pose(8, 9, False), hint=HINT, drift=0.05)
    assert harness.last.intervention_level == 0
    harness.feed(1.5, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.intervention_level == 0
    harness.feed(6.0, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.intervention_level == 1


def test_invariant_the_child_speaking_pauses_the_pedagogical_timers() -> None:
    harness = wrong_pose_harness()
    for _ in range(30):
        harness.tutor.speech("is it fifty six", now=harness.clock.t)
        harness.feed(1.0, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.intervention_level == 0
    harness.feed(6.0, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.intervention_level == 1


def test_an_observation_faster_than_the_fps_is_dropped() -> None:
    harness = wrong_pose_harness()
    obs = Observation(gesture=pose(8, 7, True), fingers=fingers(), hands_seen=2)
    for index in range(100):
        harness.tutor.observe(obs, now=index * 0.0005)
    # One hundred frames inside a twentieth of a second are one observation, so
    # a pose that needs 0.8 s of holding is nowhere near held.
    assert harness.tutor._last_taken == 0.0
    assert harness.tutor.state_fields()["tutor_state"] == WORKING
    for index in range(30):
        harness.tutor.observe(obs, now=1.0 + index * STEP)
    assert harness.tutor._last_taken == pytest.approx(1.0 + 29 * STEP)
    assert harness.tutor.state_fields()["tutor_state"] in (POSE_READY, CHECK_ANSWER)


# --------------------------------------------------------------------------
# 7. scoring
# --------------------------------------------------------------------------


def test_a_gesture_error_needs_a_targeted_correction_first() -> None:
    harness = wrong_pose_harness()
    harness.feed(6.0, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.intervention_level == 1
    assert harness.last.scored_gesture_error is False


def test_a_gesture_error_is_scored_once_after_the_correction_holds() -> None:
    harness = wrong_pose_harness()
    harness.feed(20.0, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.intervention_level >= 2
    assert harness.last.scored_gesture_error is True
    harness.tutor.end_exercise(reason="skipped", now=harness.clock.t)
    assert harness.kind("exercise")[0]["scored_gesture_errors"] == 1


def test_invariant_unknown_vision_is_never_an_error() -> None:
    harness = wrong_pose_harness()
    harness.feed(40.0, gesture=UNKNOWN, hands=2)
    assert harness.last.scored_gesture_error is False
    assert harness.last.tutor_state != WRONG_POSE
    harness.tutor.end_exercise(reason="skipped", now=harness.clock.t)
    assert harness.kind("exercise")[0]["scored_gesture_errors"] == 0


def test_invariant_a_visibility_failure_is_never_a_pedagogical_error() -> None:
    harness = wrong_pose_harness()
    harness.feed(20.0, gesture=None, hands=0)
    harness.feed(20.0, gesture=None, hands=1)
    assert harness.last.scored_gesture_error is False
    assert harness.last.first_try is True
    harness.tutor.end_exercise(reason="skipped", now=harness.clock.t)
    line = harness.kind("exercise")[0]
    assert line["scored_gesture_errors"] == 0
    assert line["unsolicited_interventions"] == 0


def test_no_contact_and_hands_swapped_never_score() -> None:
    for gesture in (pose(8, 7, False), pose(7, 8, False)):
        harness = wrong_pose_harness()
        harness.feed(12.0, gesture=gesture)
        assert harness.last.scored_gesture_error is False
        assert harness.last.first_try is True
        # The clock still reaches the rescue, which ends first try on its own.
        # Neither pose holds a wrong finger, so nothing is ever scored.
        harness.feed(18.0, gesture=gesture)
        assert harness.last.scored_gesture_error is False
        harness.tutor.end_exercise(reason="skipped", now=harness.clock.t)
        assert harness.kind("exercise")[0]["scored_gesture_errors"] == 0


def test_a_hand_lost_within_pose_memory_never_scores() -> None:
    harness = wrong_pose_harness()
    harness.feed(1.5, gesture=pose(8, 7, True))
    assert harness.last.tutor_state in (POSE_READY, CHECK_ANSWER)
    for _ in range(6):
        harness.feed(1.5, gesture=None, hands=1)
        harness.feed(1.0, gesture=pose(8, 7, True), hands=2)
    assert harness.last.scored_gesture_error is False
    assert harness.last.first_try is True


def test_a_wrong_pose_before_any_stability_never_scores() -> None:
    harness = wrong_pose_harness()
    for _ in range(60):
        harness.feed(0.2, gesture=pose(8, 9, False), hint=HINT)
        harness.feed(0.2, gesture=pose(9, 9, False), hint=HINT)
    assert harness.last.scored_gesture_error is False


def test_a_math_error_is_scored_per_wrong_answer_with_no_cap() -> None:
    harness = wrong_pose_harness()
    harness.feed(1.5, gesture=pose(8, 7, True))
    said = [harness.tutor.answer(value, correct=False, now=harness.clock.t).tutor_line
            for value in (54, 55, 57, 58)]
    # One line per wrong answer, in order, the last one repeating.
    assert said == ["Keep the pose. Count the tens again.",
                    "Let's do the tens first.",
                    "Now check the fingers above.",
                    "Now check the fingers above."]
    harness.tutor.end_exercise(reason="skipped", now=harness.clock.t)
    assert harness.kind("exercise")[0]["scored_math_errors"] == 4


def test_invariant_speech_never_scores_and_never_submits() -> None:
    harness = wrong_pose_harness()
    harness.feed(1.5, gesture=pose(8, 7, True))
    harness.tutor.speech("is it fifty four", number=54, now=harness.clock.t)
    harness.feed(1.0, gesture=pose(8, 7, True))
    assert harness.last.scored_math_error is False
    assert harness.last.first_try is True
    assert harness.last.tutor_state != SUCCESS


def test_invariant_never_wrong_for_a_correct_answer_before_the_pose() -> None:
    harness = wrong_pose_harness()
    harness.feed(2.0, gesture=pose(8, 9, False), hint=HINT)
    decision = harness.tutor.answer(56, correct=True, now=harness.clock.t)
    assert decision.scored_math_error is False
    assert decision.first_try is True
    assert decision.tutor_line == "Yes. 8 times 7 is 56."
    assert "wrong" not in (decision.tutor_line or "").lower()
    said = harness.feed(2.0, gesture=pose(8, 7, True))
    assert "You have the pose. Now count the tens." in said


def test_a_commutative_inversion_is_a_correct_pose() -> None:
    harness = Harness()
    harness.tutor.new_exercise(6, 7, node="n", now=0.0)
    said = harness.feed(12.0, gesture=pose(7, 6, True))
    assert harness.last.tutor_state in (POSE_READY, CHECK_ANSWER)
    assert harness.last.scored_gesture_error is False
    assert not any("Almost" in line for line in said)


# --------------------------------------------------------------------------
# 8. first try
# --------------------------------------------------------------------------


def finish(harness: Harness, answer_correct: bool = True) -> dict[str, object]:
    harness.feed(1.5, gesture=pose(8, 7, True))
    harness.tutor.answer(56 if answer_correct else 54, correct=answer_correct,
                         now=harness.clock.t)
    harness.tutor.end_exercise(reason="answered", now=harness.clock.t)
    return harness.kind("exercise")[-1]


def test_first_try_survives_automatic_interventions() -> None:
    harness = wrong_pose_harness()
    harness.feed(6.0, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.intervention_level == 1
    line = finish(harness)
    assert line["first_try"] is True
    assert line["unsolicited_interventions"] == 1


def test_first_try_is_lost_by_a_requested_hint() -> None:
    harness = wrong_pose_harness()
    harness.tutor.hint_requested(now=harness.clock.t)
    assert finish(harness)["first_try"] is False


def test_first_try_is_lost_by_a_rescue() -> None:
    harness = wrong_pose_harness(params_with(max_unsolicited_verbal=4))
    harness.feed(20.0, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.intervention_level == 4
    line = finish(harness)
    assert line["first_try"] is False
    assert line["rescue_used"] is True


def test_first_try_is_lost_by_a_scored_gesture_error() -> None:
    harness = wrong_pose_harness()
    harness.feed(20.0, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.scored_gesture_error is True
    assert finish(harness)["first_try"] is False


def test_first_try_is_lost_by_a_math_error() -> None:
    harness = wrong_pose_harness()
    harness.feed(1.5, gesture=pose(8, 7, True))
    harness.tutor.answer(54, correct=False, now=harness.clock.t)
    assert finish(harness)["first_try"] is False


def test_first_try_needs_the_answer_to_be_right() -> None:
    harness = wrong_pose_harness()
    line = finish(harness, answer_correct=False)
    assert line["first_try"] is False


def test_a_clean_exercise_is_first_try_and_autonomous() -> None:
    harness = wrong_pose_harness()
    line = finish(harness)
    assert line["first_try"] is True
    assert line["autonomous_success"] is True
    assert line["abandoned"] is False


# --------------------------------------------------------------------------
# 9. modes
# --------------------------------------------------------------------------


def clean_exercise(harness: Harness, a: int = 8, b: int = 7,
                   node: str = "n") -> None:
    harness.tutor.new_exercise(a, b, node=node, now=harness.clock.t)
    harness.feed(1.5, gesture=pose(a, b, True))
    harness.tutor.answer(None, correct=True, now=harness.clock.t)
    harness.tutor.end_exercise(reason="answered", now=harness.clock.t)


def hard_exercise(harness: Harness, node: str = "n") -> None:
    harness.tutor.new_exercise(8, 7, node=node, now=harness.clock.t)
    harness.feed(20.0, gesture=pose(8, 9, False), hint=HINT)
    harness.tutor.end_exercise(reason="skipped", now=harness.clock.t)


def test_the_first_exercise_of_a_node_is_always_normal() -> None:
    harness = Harness()
    assert harness.tutor.new_exercise(8, 7, node="n", now=0.0).mode == NORMAL


def test_independent_after_three_autonomous_successes_said_once() -> None:
    harness = Harness()
    for _ in range(3):
        clean_exercise(harness)
    decision = harness.tutor.new_exercise(6, 6, node="n", now=harness.clock.t)
    assert decision.mode == INDEPENDENT
    assert decision.tutor_line == ("You solved that without a hint. "
                                   "I'll give you more space on the next one.")
    harness.tutor.end_exercise(reason="skipped", now=harness.clock.t)
    again = harness.tutor.new_exercise(6, 7, node="n", now=harness.clock.t)
    assert again.tutor_line is None


def test_supportive_after_two_hard_exercises_with_the_finger_numbers() -> None:
    harness = Harness()
    hard_exercise(harness)
    hard_exercise(harness)
    decision = harness.tutor.new_exercise(6, 6, node="n", now=harness.clock.t)
    assert decision.mode == SUPPORTIVE
    assert decision.tutor_line == "Take your time. I'm watching your hands."
    harness.feed(2.0, gesture=UNKNOWN)
    assert harness.last.tutor_visual == {"kind": "finger_numbers"}
    harness.feed(1.2, gesture=UNKNOWN)
    assert harness.last.tutor_visual is None


def test_one_autonomous_success_brings_supportive_back_to_normal() -> None:
    harness = Harness()
    hard_exercise(harness)
    hard_exercise(harness)
    assert harness.tutor.new_exercise(6, 6, node="n", now=harness.clock.t).mode == SUPPORTIVE
    harness.feed(1.5, gesture=pose(6, 6, True))
    harness.tutor.answer(36, correct=True, now=harness.clock.t)
    harness.tutor.end_exercise(reason="answered", now=harness.clock.t)
    assert harness.tutor.new_exercise(7, 7, node="n", now=harness.clock.t).mode == NORMAL


def test_a_new_node_starts_from_normal() -> None:
    harness = Harness()
    hard_exercise(harness, node="a")
    hard_exercise(harness, node="a")
    assert harness.tutor.new_exercise(6, 6, node="b", now=harness.clock.t).mode == NORMAL


def test_the_mode_factors_scale_every_duration() -> None:
    harness = Harness()
    normal = harness.tutor.effective("idle_nudge")
    harness.tutor._mode = SUPPORTIVE
    assert harness.tutor.effective("idle_nudge") == pytest.approx(normal * 0.8)
    harness.tutor._mode = INDEPENDENT
    assert harness.tutor.effective("idle_nudge") == pytest.approx(normal * 1.4)
    assert harness.tutor.effective("fps") == 15


# --------------------------------------------------------------------------
# 10. the adaptation schedule and the smoothing
# --------------------------------------------------------------------------


def test_schedule_zero_to_four_forces_both_factors_to_one() -> None:
    for observations in (0, 4):
        harness = Harness(factors={"pace_factor": 1.8, "help_factor": 0.4,
                                   "tutor_observations": observations})
        assert harness.tutor.learner_factors()["pace_factor"] == 1.0
        assert harness.tutor.learner_factors()["help_factor"] == 1.0


def test_schedule_five_to_fourteen_caps_at_fifteen_percent() -> None:
    harness = Harness(factors={"pace_factor": 9.0, "help_factor": 0.0,
                               "tutor_observations": 14})
    assert harness.tutor.learner_factors()["pace_factor"] == 1.15
    assert harness.tutor.learner_factors()["help_factor"] == 0.85


def test_schedule_fifteen_and_more_allows_the_full_range() -> None:
    harness = Harness(factors={"pace_factor": 2.0, "help_factor": 2.0,
                               "tutor_observations": 15})
    assert harness.tutor.learner_factors()["pace_factor"] == 1.2


def test_the_smoothing_is_eight_tenths_old_and_two_tenths_proposed() -> None:
    harness = Harness(factors={"pace_factor": 2.0, "tutor_observations": 40})
    assert harness.tutor.learner_factors()["pace_factor"] == pytest.approx(1.2)
    harness.tutor.set_learner("9f31c0a7bd42", {"pace_factor": 2.0,
                                               "tutor_observations": 40})
    assert harness.tutor.learner_factors()["pace_factor"] == pytest.approx(1.36)


def test_tutor_observations_rises_by_one_per_exercise_line() -> None:
    harness = Harness(factors={"tutor_observations": 7})
    clean_exercise(harness)
    clean_exercise(harness)
    assert harness.tutor.learner_factors()["tutor_observations"] == 9


# --------------------------------------------------------------------------
# 11. early stop
# --------------------------------------------------------------------------


def bad_exercise(harness: Harness) -> None:
    harness.tutor.new_exercise(8, 7, node="n", now=harness.clock.t)
    harness.feed(20.0, gesture=pose(8, 9, False), hint=HINT)
    harness.tutor.hint_requested(now=harness.clock.t)
    harness.tutor.hint_requested(now=harness.clock.t)
    harness.tutor.hint_requested(now=harness.clock.t)
    harness.tutor.hint_requested(now=harness.clock.t)
    harness.feed(1.0, gesture=pose(8, 9, False), hint=HINT)
    harness.tutor.answer(54, correct=False, now=harness.clock.t)
    harness.tutor.end_exercise(reason="skipped", now=harness.clock.t)


def test_early_stop_needs_three_exercises_first() -> None:
    harness = Harness()
    bad_exercise(harness)
    bad_exercise(harness)
    assert harness.tutor.early_stop is False


def test_early_stop_asks_one_mastered_fact_then_closes() -> None:
    harness = Harness()
    clean_exercise(harness)
    bad_exercise(harness)
    bad_exercise(harness)
    assert harness.tutor.early_stop is True
    assert harness.tutor.early_stop_plan()["stage"] == "mastered_fact"

    decision = harness.tutor.new_exercise(10, 10, node="n", now=harness.clock.t)
    assert decision.tutor_line == "Show me 10 times 10 with your hands."
    assert decision.mode == SUPPORTIVE
    harness.feed(1.5, gesture=pose(10, 10, True))
    assert harness.tutor.answer(100, correct=True,
                                now=harness.clock.t).tutor_line == \
        "Yes. 10 times 10 is 100."
    harness.tutor.end_exercise(reason="answered", now=harness.clock.t)
    plan = harness.tutor.early_stop_plan()
    assert plan["stage"] == "closing"
    # The twenty lines hold no goodbye, so the page falls back to tally.py.
    assert plan["line"] is None


# --------------------------------------------------------------------------
# 12. pose memory and the motion threshold
# --------------------------------------------------------------------------


def test_a_dropout_shorter_than_pose_memory_keeps_the_pose() -> None:
    harness = wrong_pose_harness()
    harness.feed(1.5, gesture=pose(8, 7, True))
    assert harness.last.tutor_state in (POSE_READY, CHECK_ANSWER)
    harness.feed(1.5, gesture=None, hands=1)
    assert harness.last.tutor_state in (POSE_READY, CHECK_ANSWER)


def test_a_dropout_longer_than_pose_memory_forgets_the_pose() -> None:
    harness = wrong_pose_harness()
    harness.feed(1.5, gesture=pose(8, 9, False), hint=HINT)
    assert harness.tutor._held_key == (8, 9, False)
    harness.feed(3.0, gesture=UNKNOWN, hands=2)
    assert harness.tutor._held_key is None


def test_the_motion_threshold_falls_back_to_its_floor() -> None:
    harness = Harness()
    assert harness.tutor.motion_threshold == 0.3
    harness.tutor.start_check()
    harness.feed(0.5, gesture=pose(6, 6, True))
    assert harness.tutor.end_check() == 0.3


def test_the_motion_threshold_is_four_times_the_measured_jitter() -> None:
    harness = Harness()
    harness.tutor.start_check()
    for index in range(40):
        harness.clock.tick()
        obs = Observation(gesture=pose(6, 6, True),
                          fingers=fingers(0.02 * index), hands_seen=2)
        harness.tutor.observe(obs, harness.clock.t)
    assert harness.tutor.end_check() == pytest.approx(0.8, abs=1e-6)


def test_the_motion_threshold_in_force_rides_every_intervention_line() -> None:
    harness = wrong_pose_harness()
    harness.feed(20.0, gesture=pose(8, 9, False), hint=HINT)
    harness.tutor.end_exercise(reason="skipped", now=harness.clock.t)
    for line in harness.kind("intervention"):
        assert "motion_threshold" in line["effective_params"]


# --------------------------------------------------------------------------
# 13. the log
# --------------------------------------------------------------------------

DECISION_KEYS = {"kind", "ts", "learner_id", "exercise", "state", "stable_for",
                 "intervention", "reason", "reading", "scored_error", "mode",
                 "params_version"}
INTERVENTION_KEYS = {"kind", "ts", "learner_id", "exercise", "intervention",
                     "trigger_after_s", "state_before", "child_was_already_moving",
                     "child_moved_after_s", "correct_pose_within_5s",
                     "next_hint_needed", "effective_params", "learner_factors"}
EXERCISE_KEYS = {"kind", "ts", "learner_id", "exercise", "autonomous_success",
                 "first_try", "light_hint_recovery", "rescue_used",
                 "scored_gesture_errors", "scored_math_errors",
                 "unsolicited_interventions", "abandoned", "duration_s", "mode"}
REASONS = {"prompt_end", "within_grace", "wrong_pose_held", "idle", "pose_ready",
           "answer_given", "answer_wrong", "hands_lost", "one_hand",
           "child_moving", "child_speaking", "min_verbal_gap", "budget_spent",
           "child_asked", "no_engagement"}


def test_the_decision_line_matches_the_schema() -> None:
    harness = wrong_pose_harness()
    harness.feed(20.0, gesture=pose(8, 9, False), hint=HINT)
    lines = harness.kind("decision")
    assert lines
    for line in lines:
        assert set(line) == DECISION_KEYS
        assert line["ts"].endswith("Z")
        assert line["learner_id"] == "9f31c0a7bd42"
        assert line["exercise"] == "8 x 7"
        assert line["reason"] in REASONS
        assert line["scored_error"] in (None, "gesture", "math")
        assert len(line["params_version"]) == 12
    assert any(line["scored_error"] == "gesture" for line in lines)


def test_no_more_than_two_decision_lines_in_one_second() -> None:
    harness = wrong_pose_harness()
    harness.feed(40.0, gesture=pose(8, 9, False), hint=HINT, drift=0.0)
    stamps = [line["ts"] for line in harness.kind("decision")]
    for stamp in set(stamps):
        assert stamps.count(stamp) <= 2


def test_the_intervention_line_matches_the_schema() -> None:
    harness = wrong_pose_harness()
    harness.feed(25.0, gesture=pose(8, 9, False), hint=HINT)
    harness.tutor.end_exercise(reason="skipped", now=harness.clock.t)
    lines = harness.kind("intervention")
    assert lines
    for line in lines:
        assert set(line) == INTERVENTION_KEYS
        assert 1 <= line["intervention"] <= 4
        assert isinstance(line["child_was_already_moving"], bool)
        assert line["child_moved_after_s"] is None or line["child_moved_after_s"] >= 0
        assert set(line["learner_factors"]) == {"pace_factor", "help_factor",
                                                "tutor_observations", "mode_factor"}
    assert lines[0]["next_hint_needed"] is True


def test_an_intervention_on_a_moving_child_is_marked_as_such() -> None:
    harness = wrong_pose_harness()
    harness.feed(6.0, gesture=pose(8, 9, False), hint=HINT)
    harness.feed(0.4, gesture=pose(8, 9, False), hint=HINT, drift=0.05)
    harness.tutor.end_exercise(reason="skipped", now=harness.clock.t)
    lines = harness.kind("intervention")
    assert lines
    assert lines[0]["child_moved_after_s"] is not None


def test_the_exercise_line_matches_the_schema() -> None:
    harness = wrong_pose_harness()
    harness.feed(6.0, gesture=pose(8, 9, False), hint=HINT)
    harness.feed(2.0, gesture=pose(8, 7, True))
    harness.tutor.answer(56, correct=True, now=harness.clock.t)
    harness.tutor.end_exercise(reason="answered", now=harness.clock.t)
    line = harness.kind("exercise")[0]
    assert set(line) == EXERCISE_KEYS
    assert line["light_hint_recovery"] is True
    assert line["autonomous_success"] is True
    assert line["duration_s"] > 0


def test_an_exercise_that_ends_without_an_answer_is_abandoned() -> None:
    harness = wrong_pose_harness()
    harness.feed(2.0, gesture=pose(8, 9, False), hint=HINT)
    harness.tutor.end_exercise(reason="quit", now=harness.clock.t)
    line = harness.kind("exercise")[0]
    assert line["abandoned"] is True
    assert line["autonomous_success"] is False


def test_a_skip_is_not_an_abandonment() -> None:
    harness = wrong_pose_harness()
    harness.feed(2.0, gesture=pose(8, 9, False), hint=HINT)
    harness.tutor.end_exercise(reason="skipped", now=harness.clock.t)
    assert harness.kind("exercise")[0]["abandoned"] is False


def test_a_log_sink_that_raises_never_takes_the_lesson_down() -> None:
    def angry(record: dict[str, object]) -> None:
        raise OSError("the disk is full")

    clock = Clock()
    tutor = Tutor(load_params(PARAMS_FILE), load_lines(LINES_FILE),
                  clock=clock, log=angry, learner_id="9f31c0a7bd42")
    tutor.new_exercise(8, 7, node="n", now=0.0)
    for index in range(200):
        clock.tick()
        tutor.observe(Observation(gesture=pose(8, 9, False), fingers=fingers(),
                                  hands_seen=2, hint=HINT), clock.t)
    assert tutor.end_exercise(reason="skipped", now=clock.t).mode == NORMAL


def test_the_jsonl_sink_appends_one_object_per_line(tmp_path: Path) -> None:
    target = tmp_path / "tutor_log.jsonl"
    sink = jsonl_sink(target)
    sink({"kind": "exercise", "exercise": "8 x 7"})
    sink({"kind": "decision", "exercise": "8 x 7"})
    lines = target.read_text(encoding="utf-8").strip().split("\n")
    assert [json.loads(line)["kind"] for line in lines] == ["exercise", "decision"]


# --------------------------------------------------------------------------
# 14. the ten fields on the wire
# --------------------------------------------------------------------------


def test_the_state_fields_are_the_ten_of_the_contract() -> None:
    harness = wrong_pose_harness()
    fields = harness.tutor.state_fields()
    assert set(fields) == {"tutor_state", "intervention_level", "tutor_line",
                           "tutor_visual", "scored_gesture_error",
                           "scored_math_error", "first_try", "mode",
                           "tutor_line_cuts", "tutor_beat"}
    assert fields["tutor_state"] == PROMPTING
    assert fields["intervention_level"] == 0
    assert fields["mode"] == NORMAL
    # No line, nothing to cut, and no beat outside a correct answer.
    assert fields["tutor_line"] is None
    assert fields["tutor_line_cuts"] is False
    assert fields["tutor_beat"] is None


def test_every_visual_kind_is_one_of_the_six() -> None:
    kinds = set()
    harness = wrong_pose_harness(params_with(max_unsolicited_verbal=4))
    for _ in range(150):
        harness.feed(0.2, gesture=pose(8, 9, False), hint=HINT)
        if harness.last.tutor_visual:
            kinds.add(harness.last.tutor_visual["kind"])
    harness.feed(10.0, gesture=None, hands=0)
    if harness.last.tutor_visual:
        kinds.add(harness.last.tutor_visual["kind"])
    assert kinds <= {"pulse_finger", "correction", "ghost", "rescue_card",
                     "placement_zones", "finger_numbers"}
    assert {"pulse_finger", "correction", "ghost", "rescue_card"} <= kinds


def test_a_line_is_only_returned_on_the_tick_it_is_said() -> None:
    harness = wrong_pose_harness()
    said = harness.feed(20.0, gesture=pose(8, 9, False), hint=HINT)
    assert len(said) == len(set(said))
    assert harness.last.tutor_line is None


# --------------------------------------------------------------------------
# 15. the opener, and the lines the anti nag limits hold back
# --------------------------------------------------------------------------


def test_two_hands_the_classifier_cannot_read_get_the_opener() -> None:
    harness = wrong_pose_harness()
    said = harness.feed(6.0, gesture=UNKNOWN, hands=2)
    assert said == ["Show me 8 times 7 with your hands."]
    assert harness.last.tutor_visual == {"kind": "finger_numbers"}
    assert harness.last.tutor_state == WORKING
    assert harness.last.scored_gesture_error is False
    said = harness.feed(6.0, gesture=UNKNOWN, hands=2)
    assert said == ["Take your time. I'm watching your hands."]
    said = harness.feed(6.0, gesture=UNKNOWN, hands=2)
    assert said == ["Start by finding 8 on your left hand."]
    reasons = {line["reason"] for line in harness.kind("decision")}
    assert {"prompt_end", "idle"} <= reasons


def test_a_line_held_back_by_movement_is_logged_once() -> None:
    harness = wrong_pose_harness()
    harness.feed(10.0, gesture=pose(8, 9, False), hint=HINT, drift=0.05)
    moving = [line for line in harness.kind("decision")
              if line["reason"] == "child_moving"]
    assert len(moving) == 1


def test_a_line_held_back_by_speech_is_logged() -> None:
    harness = wrong_pose_harness()
    harness.feed(5.0, gesture=pose(8, 9, False), hint=HINT)
    harness.tutor.speech("i think it is fifty six", now=harness.clock.t)
    harness.feed(1.0, gesture=pose(8, 9, False), hint=HINT)
    assert any(line["reason"] == "child_speaking"
               for line in harness.kind("decision"))


# --------------------------------------------------------------------------
# 16. the latency parameters and the acknowledgement that may cut
# --------------------------------------------------------------------------


ACK_LINE = "You have the pose. Now count the tens."

# The five latency keys: what they are set to, and the bounds they live in.
LATENCY_DEFAULTS: dict[str, tuple[float, tuple[float, float]]] = {
    "pose_confirm_frames": (3, (2, 6)),
    "pose_confirm_ms": (250, (120, 600)),
    "ack_delay_ms": (0, (0, 300)),
    "check_step_min_ms": (0, (0, 400)),
    "answer_first_number_ms": (0, (0, 400)),
}

# Every timing that was in the file before the latency keys were added. Cutting
# latency may not quietly move one of them.
UNCHANGED_GLOBALS: dict[str, float] = {
    "fps": 15, "hands_visible_stable": 0.5, "pose_stable": 0.8,
    "initial_silence": 4.0, "idle_nudge": 5.0, "wrong_pose_prompt": 2.0,
    "wrong_pose_error_after_help": 2.5, "correct_pose_nudge": 4.5,
    "hint_2_delay": 9.0, "rescue_delay": 14.0, "no_hands_visual": 1.5,
    "no_hands_voice": 3.0, "one_hand_voice": 2.5, "min_verbal_gap": 4.0,
    "post_movement_silence": 2.0, "no_engagement_pause": 30.0,
    "pose_memory": 2.0, "max_unsolicited_verbal": 3,
    "max_visibility_reminders": 2, "supportive_mode_factor": 0.8,
    "independent_mode_factor": 1.4, "live_sample_windows": 8,
}


def test_the_five_latency_keys_are_known_with_their_bounds() -> None:
    raw = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    params = load_params(PARAMS_FILE)
    for key, (value, bound) in LATENCY_DEFAULTS.items():
        assert key in REQUIRED_PARAMS, key
        assert raw["global"][key] == value, key
        assert raw["bounds"][key] == list(bound), key
        assert params.values[key] == value, key
        assert params.bound(key) == bound, key


def test_a_latency_key_is_clamped_and_never_scaled() -> None:
    harness = Harness(factors={"pace_factor": 9.0, "help_factor": 9.0,
                               "tutor_observations": 500})
    for mode in (NORMAL, SUPPORTIVE, INDEPENDENT):
        harness.tutor._mode = mode
        for key, (value, (low, high)) in LATENCY_DEFAULTS.items():
            assert low <= harness.tutor.effective(key) <= high, key
            # Latency is how fast the machine answers, not how patient Tally is,
            # so no learner factor and no mode factor touches it.
            assert harness.tutor.effective(key) == value, key
    raw = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    raw["global"]["ack_delay_ms"] = 900
    path = Path(_tmp()) / "ack_out_of_bounds.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ParamsError) as excinfo:
        load_params(path)
    assert "ack_delay_ms" in str(excinfo.value) and "300" in str(excinfo.value)


def test_the_acknowledgement_arrives_on_the_tick_the_pose_is_confirmed() -> None:
    harness = wrong_pose_harness()
    said: list[str] = []
    while not said and harness.clock.t < 5.0:
        said = harness.feed(STEP, gesture=pose(8, 7, True))
    assert said == [ACK_LINE]
    # The same tick the pose is confirmed, with ack_delay_ms at its default zero.
    assert harness.last.tutor_state == POSE_READY
    assert harness.clock.t == pytest.approx(0.8 + STEP, abs=STEP)


def test_ack_delay_ms_moves_the_acknowledgement_and_nothing_else() -> None:
    harness = wrong_pose_harness(params_with(ack_delay_ms=300))
    said: list[str] = []
    while not said and harness.clock.t < 5.0:
        said = harness.feed(STEP, gesture=pose(8, 7, True))
    assert said == [ACK_LINE]
    # The pose is confirmed at pose_stable, the line waits the delay and lands
    # on the first frame after it. Nothing else in the file moved with it.
    assert 0.8 + 0.3 <= harness.clock.t <= 0.8 + 0.3 + 3 * STEP


def test_only_the_acknowledgement_is_marked_as_able_to_cut() -> None:
    harness = wrong_pose_harness()
    marked = harness.feed_marked(20.0, gesture=pose(8, 9, False), hint=HINT)
    assert marked, "the wrong pose has to have been spoken to at all"
    assert not any(cuts for _, cuts in marked)
    marked = harness.feed_marked(2.0, gesture=pose(8, 7, True))
    assert marked[0] == (ACK_LINE, True)
    assert [line for line, cuts in marked if cuts] == [ACK_LINE]


def test_min_verbal_gap_never_holds_back_the_acknowledgement() -> None:
    harness = wrong_pose_harness(params_with(min_verbal_gap=8.0))
    said: list[str] = []
    while not said and harness.clock.t < 25.0:
        said = harness.feed(STEP, gesture=pose(8, 9, False), hint=HINT)
    assert said, "a line has to have been said for the gap to be in force"
    # The whole window below sits inside min_verbal_gap of that line, so it
    # holds every paced line back. The acknowledgement comes out of it anyway.
    marked = harness.feed_marked(1.5, gesture=pose(8, 7, True))
    assert marked == [(ACK_LINE, True)]


def test_the_grace_before_a_wrong_pose_is_scored_is_untouched() -> None:
    params = load_params(PARAMS_FILE)
    assert params.values["wrong_pose_prompt"] == 2.0
    assert params.values["wrong_pose_error_after_help"] == 2.5
    assert params.values["initial_silence"] == 4.0
    harness = wrong_pose_harness()
    harness.feed(4.0, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.scored_gesture_error is False
    harness.feed(16.0, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.scored_gesture_error is True


def test_no_other_value_in_the_params_file_moved() -> None:
    raw = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    assert (set(raw["global"])
            == set(UNCHANGED_GLOBALS) | set(LATENCY_DEFAULTS) | set(BEAT_DEFAULTS))
    for key, value in UNCHANGED_GLOBALS.items():
        assert raw["global"][key] == value, key
    for key, (value, bound) in LATENCY_DEFAULTS.items():
        assert raw["global"][key] == value, key
        assert raw["bounds"][key] == list(bound), key


# --------------------------------------------------------------------------
# 17. the success beat between two exercises
# --------------------------------------------------------------------------


# The two beat keys: what they are set to, and the bounds they live in.
BEAT_DEFAULTS: dict[str, tuple[float, tuple[float, float]]] = {
    "success_beat_ms": (1500, (600, 2500)),
    "next_pause_ms": (1500, (400, 2500)),
}
SUCCESS_LINE = "Yes. 8 times 7 is 56."
NEXT_LINE = "Next one."


def beat_of(harness: Harness, mode: str = NORMAL) -> dict[str, object] | None:
    """One correct answer in the given mode, and the beat it called for."""
    harness.tutor._mode = mode
    decision = harness.tutor.answer(56, correct=True, now=harness.clock.t)
    assert decision.tutor_line == SUCCESS_LINE
    assert decision.tutor_state == SUCCESS
    return decision.tutor_beat


def lines_with_next_one() -> dict[str, str]:
    """The real line file once the owner has added the beat's second line."""
    raw = json.loads(LINES_FILE.read_text(encoding="utf-8"))
    raw["next_one"] = NEXT_LINE
    path = Path(_tmp()) / "lines_with_next_one.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return dict(load_lines(path))


def test_the_two_beat_keys_are_known_with_their_bounds() -> None:
    raw = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    params = load_params(PARAMS_FILE)
    for key, (value, bound) in BEAT_DEFAULTS.items():
        assert key in REQUIRED_PARAMS, key
        assert key in BEAT_PARAMS, key
        assert raw["global"][key] == value, key
        assert raw["bounds"][key] == list(bound), key
        assert params.values[key] == value, key
        assert params.bound(key) == bound, key


def test_a_beat_key_outside_its_bounds_is_a_startup_error() -> None:
    raw = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    raw["global"]["success_beat_ms"] = 9000
    path = Path(_tmp()) / "beat_out_of_bounds.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ParamsError) as excinfo:
        load_params(path)
    assert "success_beat_ms" in str(excinfo.value) and "2500" in str(excinfo.value)


def test_the_beat_timings_are_clamped_and_never_scaled() -> None:
    real = load_params(PARAMS_FILE)
    stretched = TutorParams(values=dict(real.values,
                                        success_beat_ms=9000.0, next_pause_ms=10.0),
                            bounds=real.bounds, invariants=real.invariants,
                            params_version=real.params_version)
    harness = Harness(params=stretched,
                      factors={"pace_factor": 9.0, "help_factor": 9.0,
                               "tutor_observations": 500})
    harness.tutor.new_exercise(8, 7, node="u2-l3", now=harness.clock.t)
    for mode in (NORMAL, SUPPORTIVE, INDEPENDENT):
        harness.tutor._mode = mode
        assert harness.tutor.effective("success_beat_ms") == 2500
        assert harness.tutor.effective("next_pause_ms") == 400
    beat = beat_of(harness)
    assert beat is not None
    assert (beat["success_ms"], beat["pause_ms"], beat["total_ms"]) == (2500, 400, 2900)


def test_a_correct_answer_calls_the_beat_with_its_parts() -> None:
    harness = answering_harness()
    wait(harness)
    beat = beat_of(harness)
    assert beat == {
        "kind": BEAT_SUCCESS,
        "parts": ["line", "halo", "stars", "counter"],
        "success_ms": 1500,
        "pause_ms": 1500,
        "total_ms": 3000,
        "line": SUCCESS_LINE,
        # The line that closes the beat, said as the next exercise fades in.
        "next_line": NEXT_LINE,
    }
    assert list(BEAT_PARTS) == beat["parts"]


def test_the_beat_rides_one_message_and_is_never_replayed() -> None:
    harness = answering_harness()
    wait(harness)
    assert harness.tutor.state_fields()["tutor_beat"] is None
    assert beat_of(harness) is not None
    # The page plays it once: every message after it says nothing about a beat.
    assert harness.tutor.state_fields()["tutor_beat"] is None
    harness.feed(1.0, gesture=pose(8, 7, True))
    assert harness.last.tutor_beat is None


def test_a_wrong_answer_calls_no_beat() -> None:
    harness = answering_harness()
    wait(harness)
    assert harness.tutor.answer(54, correct=False,
                                now=harness.clock.t).tutor_beat is None


def test_the_beat_is_identical_in_independent_mode() -> None:
    normal = answering_harness()
    wait(normal)
    independent = answering_harness()
    wait(independent)
    # Independent mode makes Tally talk less by stretching her timers. The beat
    # is not one of them: same parts, same milliseconds, same line.
    assert beat_of(independent, mode=INDEPENDENT) == beat_of(normal, mode=NORMAL)
    assert independent.tutor.mode_factor() == 1.4


def test_the_beat_is_never_held_back_by_min_verbal_gap() -> None:
    harness = answering_harness(params_with(min_verbal_gap=8.0))
    # No wait: the acknowledgement was said a moment ago and the gap is in
    # force, which holds every paced line back. The success line and its beat
    # never go through that gate.
    beat = beat_of(harness, mode=INDEPENDENT)
    assert beat is not None and beat["line"] == SUCCESS_LINE


def test_the_beat_closes_on_next_one_once_the_line_exists() -> None:
    harness = Harness()
    harness.tutor.lines = lines_with_next_one()
    harness.tutor.new_exercise(8, 7, node="u2-l3", now=harness.clock.t)
    harness.feed(1.5, gesture=pose(8, 7, True))
    wait(harness)
    beat = beat_of(harness)
    assert beat is not None
    assert beat["next_line"] == NEXT_LINE
    assert beat["line"] == SUCCESS_LINE


def test_the_line_file_may_carry_next_one_and_nothing_else_new() -> None:
    raw = json.loads(LINES_FILE.read_text(encoding="utf-8"))
    path = Path(_tmp()) / "lines_plus.json"
    path.write_text(json.dumps(dict(raw, next_one=NEXT_LINE)), encoding="utf-8")
    assert load_lines(path)["next_one"] == NEXT_LINE
    # An empty optional line is refused like any other empty line, and an
    # unknown key is still unknown.
    path.write_text(json.dumps(dict(raw, next_one="  ")), encoding="utf-8")
    with pytest.raises(ParamsError, match="next_one"):
        load_lines(path)
    path.write_text(json.dumps(dict(raw, whistle="Tally whistles.")),
                    encoding="utf-8")
    with pytest.raises(ParamsError, match="whistle"):
        load_lines(path)


# --------------------------------------------------------------------------
# 18. the lines the page could not speak
# --------------------------------------------------------------------------


def test_a_dropped_line_is_logged_with_its_reason() -> None:
    harness = wrong_pose_harness()
    harness.feed(1.5, gesture=pose(8, 7, True))
    before = len(harness.log)
    state = harness.last.tutor_state
    level = harness.last.intervention_level
    harness.tutor.line_dropped(line=ACK_LINE, reason="replaced", at=5821.4,
                               now=harness.clock.t)
    drops = harness.kind("line_drop")
    assert len(drops) == 1
    drop = drops[0]
    assert drop == {
        "kind": "line_drop",
        "ts": drop["ts"],
        "learner_id": "9f31c0a7bd42",
        "exercise": "8 x 7",
        "line": ACK_LINE,
        # The acknowledgement has nothing to fill in, so it is recognised.
        "line_key": "pose_ready",
        "reason": "replaced",
        "reported_reason": None,
        "reported_at": 5821.4,
        "state": state,
        "intervention": level,
        "mode": NORMAL,
    }
    assert str(drop["ts"]).endswith("Z")
    # It is one log line and nothing else: no decision, no score, no state move.
    assert len(harness.log) == before + 1
    assert harness.tutor.state_fields()["tutor_state"] == state
    assert harness.tutor.state_fields()["scored_math_error"] is False


def test_every_drop_reason_of_the_vocabulary_is_kept_as_it_is() -> None:
    harness = wrong_pose_harness()
    for reason in DROP_REASONS:
        harness.tutor.line_dropped(line=ACK_LINE, reason=reason.upper(),
                                   now=harness.clock.t)
    assert [drop["reason"] for drop in harness.kind("line_drop")] == list(DROP_REASONS)


def test_a_malformed_drop_report_is_logged_and_never_raises() -> None:
    harness = wrong_pose_harness()
    harness.tutor.line_dropped(line=None, reason=None, at=None,
                               now=harness.clock.t)
    harness.tutor.line_dropped(line=42, reason={"why": "no idea"}, at="soon",
                               now=harness.clock.t)
    harness.tutor.line_dropped(line="   ", reason="voice_engine_asleep",
                               at=float("nan"), now=harness.clock.t)
    harness.tutor.line_dropped(now=harness.clock.t)
    drops = harness.kind("line_drop")
    assert len(drops) == 4
    # Nothing usable is invented, nothing unusable is thrown away.
    assert [drop["line"] for drop in drops] == [None, None, None, None]
    assert [drop["line_key"] for drop in drops] == [None, None, None, None]
    assert [drop["reason"] for drop in drops] == ["unknown"] * 4
    assert [drop["reported_at"] for drop in drops] == [None, None, None, None]
    assert drops[0]["reported_reason"] is None
    assert "no idea" in str(drops[1]["reported_reason"])
    assert drops[2]["reported_reason"] == "voice_engine_asleep"
    assert drops[3]["reported_reason"] is None


def test_a_dropped_line_the_tutor_does_not_know_has_no_key() -> None:
    harness = wrong_pose_harness()
    harness.tutor.line_dropped(line="Something the page made up.",
                               reason="queue_full", now=harness.clock.t)
    drop = harness.kind("line_drop")[0]
    assert drop["line"] == "Something the page made up."
    assert drop["line_key"] is None
    assert drop["reason"] == "queue_full"


# --------------------------------------------------------------------------
# 13. reading the pose: one test per row of POSE_TABLE
#
# Every one of them injects fingertip coordinates and lets the tutor measure
# them. Nothing here is driven by the classifier's contact flag: the tips are
# placed a named number of palms apart, and contact_ratio decides what that
# distance means.
# --------------------------------------------------------------------------


GLOBALS = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))["global"]
CONTACT = GLOBALS["contact_ratio"]
INITIAL_SILENCE = GLOBALS["initial_silence"]
HINT_2_DELAY = GLOBALS["hint_2_delay"]
POSE_STABLE = GLOBALS["pose_stable"]
WRONG_POSE_PROMPT = GLOBALS["wrong_pose_prompt"]

ACK = "Yes, that's it."
WRONG_LEFT = "Your right hand is right. Find 8 on your left hand."
WRONG_RIGHT = "Your left hand is right. Find 7 on your right hand."
COME_HERE = "Come here, hands!"

TOUCHING = 0.4 * CONTACT          # the two tips are together
NEARLY = 1.5 * CONTACT            # close, and not touching
APART = 4.0                       # four palms: nothing is near anything


PALM = 0.06                       # the palm these fixtures are drawn in
LEFT_STEP = 0.30 * PALM           # the gap between one hand's own fingertips
RIGHT_STEP = 0.45 * PALM          # the other hand's, wider on purpose


def hands_at(gap: float, pair: tuple[int, int] = (8, 7), hands: int = 2
             ) -> list[dict[str, object]]:
    """Ten fingertips, with one named pair exactly gap palms apart.

    The palm the tutor reads in is the widest distance inside a hand over
    TIP_SPREAD_PALMS, averaged over the hands, so the two steps below are
    chosen to make that palm come out at PALM, and gap is measured in it. The
    fingertips of one hand are spaced wider than the other's, which leaves the
    named pair the only pair level with each other, and so the only nearest
    pair the tutor can find.
    """
    dx = gap * PALM
    out: list[dict[str, object]] = []
    for hand in {0: (), 1: ("left",), 2: ("left", "right")}[hands]:
        x = 0.5 - dx / 2.0 if hand == "left" else 0.5 + dx / 2.0
        anchor = pair[0] if hand == "left" else pair[1]
        step = LEFT_STEP if hand == "left" else RIGHT_STEP
        for number in range(6, 11):
            out.append({"hand": hand, "number": number, "x": x,
                        "y": 0.5 + (number - anchor) * step})
    return out


def climb(harness: Harness, level: int, limit: float = 40.0, **kwargs: object
          ) -> list[str]:
    """Feed until the ladder reaches a level, and say what was said on the way."""
    said: list[str] = []
    while harness.last.intervention_level < level and harness.clock.t < limit:
        said += watch(harness, 0.2, **kwargs)
    assert harness.last.intervention_level == level, (
        f"the ladder never reached L{level}")
    return said


def watch(harness: Harness, seconds: float, gesture: GestureState | None = UNKNOWN,
          gap: float = APART, pair: tuple[int, int] = (8, 7), hands: int = 2,
          hint: dict[str, object] | None = None,
          tips: list[dict[str, object]] | None = None) -> list[str]:
    """Feed placed fingertips for a while, and collect what was said."""
    said: list[str] = []
    for _ in range(int(round(seconds * FPS))):
        harness.clock.tick()
        obs = Observation(gesture=gesture,
                          fingers=hands_at(gap, pair, hands) if tips is None else tips,
                          hands_seen=hands, hint=hint)
        harness.last = harness.tutor.observe(obs, harness.clock.t)
        if harness.last.tutor_line:
            said.append(harness.last.tutor_line)
    return said


def readings(harness: Harness) -> list[str]:
    """The reading on every decision the tutor logged."""
    return [line["reading"]["name"] for line in harness.kind("decision")]


def test_the_geometry_fixture_places_the_pair_where_it_says() -> None:
    """The rows below are worth no more than the fixture they inject."""
    placed = tip_positions(hands_at(NEARLY))
    spreads = [max(abs(one[1] - other[1]) for one in hand.values()
                   for other in hand.values()) for hand in placed.values()]
    assert sum(spreads) / len(spreads) / TIP_SPREAD_PALMS == pytest.approx(PALM)
    nearest = nearest_gap(placed, PALM)
    assert nearest is not None
    assert nearest[1] == (("left", 8), ("right", 7))
    assert nearest[0] == pytest.approx(NEARLY, abs=1e-9)
    far = nearest_gap(tip_positions(hands_at(APART)), PALM)
    assert far is not None and far[0] == pytest.approx(APART, abs=1e-9)


def test_hands_open_and_far_apart_get_the_numbers_on_every_fingertip() -> None:
    """Row 1. Nothing within the contact distance: the child does not know."""
    harness = wrong_pose_harness()
    said = watch(harness, INITIAL_SILENCE + 0.5, gap=APART)
    assert harness.tutor.reading.name == "searching"
    assert said == [], "the numbers are shown, not announced"
    assert climb(harness, 1) == []
    assert harness.last.tutor_visual == {"kind": "finger_numbers"}
    assert "searching" in readings(harness)


def test_two_fingertips_closing_in_are_left_alone_with_a_dot_each() -> None:
    """Row 2. Close and not touching: say nothing, mark the two, wait."""
    harness = wrong_pose_harness()
    said = watch(harness, INITIAL_SILENCE + 1.5, gap=NEARLY)
    assert harness.tutor.reading.name == "closing_in"
    assert said == []
    assert harness.last.intervention_level == 0
    assert harness.last.tutor_visual == {
        "kind": "closing_in",
        "tips": [{"hand": "left", "finger": 8}, {"hand": "right", "finger": 7}],
    }


def test_a_wrong_pair_already_touching_is_answered_in_colour_and_silence() -> None:
    """Row 3. The gesture is made and the count is wrong: colours, no voice."""
    harness = wrong_pose_harness()
    wrong = pose(9, 7, contact=False)
    said = watch(harness, INITIAL_SILENCE + 0.5, gesture=wrong, gap=TOUCHING,
                 pair=(9, 7))
    assert harness.tutor.reading.name == "wrong_pair"
    assert harness.tutor.reading.hand == "left"
    said += climb(harness, 2, gesture=wrong, gap=TOUCHING, pair=(9, 7))
    assert said == [], "L2 is shown, not said"
    assert harness.last.tutor_visual == {"kind": "correction",
                                         "wrong_hand": "left",
                                         "expected_finger": 8}
    assert "wrong_pair" in readings(harness)


def test_the_same_wrong_pair_still_held_earns_the_voice_and_the_ghost() -> None:
    """Row 4. Held through hint_2_delay after the colours: L3, and a word.

    The ladder clock is the ceiling over this row and always reaches L3 first,
    since it counts hint_2_delay from the exercise and this row counts it from
    the colours. Both ask for the same aid, so what is asserted here is the aid
    and the reading, never which of the two got there.
    """
    harness = wrong_pose_harness(params_with(hint_2_delay=6.0, rescue_delay=25.0))
    wrong = pose(9, 7, contact=False)
    held = {"gesture": wrong, "gap": TOUCHING, "pair": (9, 7)}
    watch(harness, INITIAL_SILENCE + 0.5, **held)
    climb(harness, 2, **held)
    said = climb(harness, 3, **held)
    assert said == [WRONG_LEFT], "L3 is the first level with a voice"
    assert harness.last.tutor_visual == {"kind": "ghost", "hand": "left",
                                         "from": 9, "to": 8}
    while harness.tutor.reading.name != "wrong_pair_held" and harness.clock.t < 30:
        watch(harness, 0.2, **held)
    assert harness.tutor.reading.name == "wrong_pair_held"
    assert harness.last.intervention_level == 3


def test_one_hand_right_colours_the_hand_that_is_still_looking() -> None:
    """Row 5. The colour goes on the searching hand, and nowhere else."""
    harness = wrong_pose_harness()
    wrong = pose(8, 9, contact=False)
    watch(harness, INITIAL_SILENCE + 0.5, gesture=wrong, gap=APART)
    assert harness.tutor.reading.name == "one_hand_searching"
    assert harness.tutor.reading.hand == "right"
    said = climb(harness, 1, gesture=wrong, gap=APART)
    assert harness.last.tutor_visual == {"kind": "finger_numbers"}, "L1 first"
    said += climb(harness, 2, gesture=wrong, gap=APART)
    assert harness.last.tutor_visual == {"kind": "correction",
                                         "wrong_hand": "right",
                                         "expected_finger": 7}
    assert said == []


def test_hands_drifting_out_of_frame_get_the_come_here_line() -> None:
    """Row 6. A visibility failure, and never a word about the pose."""
    harness = wrong_pose_harness()
    said = watch(harness, 4.0, gesture=None, hands=0)
    assert harness.tutor.reading.name == "hands_gone"
    assert said == [COME_HERE]
    assert harness.last.tutor_visual == {"kind": "placement_zones"}


def test_the_table_never_delays_the_acknowledgement_of_a_correct_pose() -> None:
    """Whatever the table is in the middle of, a right pose is a yes at once."""
    harness = wrong_pose_harness()
    watch(harness, INITIAL_SILENCE + WRONG_POSE_PROMPT + 0.5, gap=APART)
    assert harness.last.intervention_level == 1, "the table is mid ladder"
    at = harness.clock.t
    said = watch(harness, POSE_STABLE + 4 * STEP, gesture=pose(8, 7, True),
                 gap=TOUCHING)
    assert said[:1] == [ACK]
    assert harness.clock.t - at <= POSE_STABLE + 5 * STEP
    assert harness.tutor.reading.name == "correct"


def test_a_clock_still_escalates_when_the_reading_is_ambiguous() -> None:
    """No landmarks, no reading, and the ladder climbs on its clocks alone."""
    harness = wrong_pose_harness()
    wrong = pose(8, 9, contact=False)
    said = watch(harness, INITIAL_SILENCE + 0.5, gesture=wrong, hint=HINT, tips=[])
    assert harness.tutor.reading.name == "unclear"
    assert said == []
    said += climb(harness, 1, gesture=wrong, hint=HINT, tips=[])
    assert said == [], "the clock climbs, and L1 is still shown rather than said"
    said += climb(harness, 3, gesture=wrong, hint=HINT, tips=[])
    assert said == [WRONG_RIGHT]


def test_the_motion_fallback_is_in_palm_widths_whatever_the_fingertip_count() -> None:
    """One unit on both branches of the median: an odd count of tips used to
    come back in frame fractions, ten times too small for the threshold."""
    harness = Harness()
    tutor = harness.tutor
    before = [{"hand": "left", "number": n, "x": 0.3, "y": 0.5} for n in (6, 7, 8)]
    after = [{"hand": "left", "number": n, "x": 0.3 + 0.05, "y": 0.5} for n in (6, 7, 8)]
    tutor._prev_fingers = before
    odd = tutor._motion(after)
    tutor._prev_fingers = before[:2]
    even = tutor._motion(after[:2])
    assert odd == pytest.approx(even)
    assert odd == pytest.approx(0.05 / NOMINAL_PALM)


def test_the_page_drop_reasons_land_in_the_log_vocabulary() -> None:
    """Two of the three reasons the page sends used to be logged as unknown."""
    harness = Harness()
    harness.tutor.new_exercise(8, 7, node="u2-l3", now=harness.clock.t)
    for sent, kept in (("replaced_by_newer_of_same_kind", DROP_REPLACED),
                       ("no_longer_true", DROP_STALE),
                       ("queue_full", DROP_QUEUE_FULL),
                       ("something_else", "unknown")):
        harness.tutor.line_dropped(line="pose_ready", reason=sent, at=1.0,
                                   now=harness.clock.tick())
        record = harness.kind("line_drop")[-1]
        assert record["reason"] == kept, sent
        assert record["reported_reason"] == (None if kept != "unknown" else sent)


def test_the_correct_pose_is_confirmed_in_frames_or_ms_whichever_first() -> None:
    """Amendment F8, on the tutor's side of the fence as well as the engine's."""
    harness = Harness()
    harness.tutor.new_exercise(6, 6, node="u1-l1", now=harness.clock.t)
    harness.feed(0.5, gesture=UNKNOWN, hands=0)
    stable = harness.tutor.effective("pose_stable")
    confirm_ms = harness.tutor.effective("pose_confirm_ms") / 1000.0
    assert confirm_ms < stable, "the test only means something on a faster clock"
    right = pose(6, 6, True)
    said = harness.feed(confirm_ms + 3 * STEP, gesture=right)
    assert harness.last.tutor_state == "POSE_READY", "confirmed before pose_stable"
    assert said, "the acknowledgement rides the confirmation"


# --------------------------------------------------------------------------
# 19. praise only a sure hand
# --------------------------------------------------------------------------

# 8 x 7 with 9 held on the left: the right hand shows 7, the finger it wants.
LEFT_HINT = {"hand": "left", "move_from": 9, "move_to": 8}
PRAISING = "Your right hand is right. Find 8 on your left hand."


def _lines() -> dict[str, str]:
    return json.loads(LINES_FILE.read_text(encoding="utf-8"))


def test_a_sure_hand_at_the_right_finger_is_praised() -> None:
    harness = wrong_pose_harness()
    said = harness.feed(16.0, gesture=pose(9, 7, False, confidence=0.9),
                        hint=LEFT_HINT)
    assert PRAISING in said
    assert _lines()["correction_neutral"] not in said
    praise = harness.kind("praise")
    assert praise and praise[0]["praised"] is True
    assert praise[0]["line_key"] == "wrong_left"


def test_an_unsure_hand_is_never_called_good() -> None:
    """The bug seen live: the right hand was called good on a reading nobody
    would bet on. Below 0.8 the neutral correction is said instead."""
    harness = wrong_pose_harness()
    said = harness.feed(16.0, gesture=pose(9, 7, False, confidence=0.7),
                        hint=LEFT_HINT)
    assert PRAISING not in said
    assert _lines()["correction_neutral"] in said
    praise = harness.kind("praise")
    assert praise and praise[0]["praised"] is False
    assert praise[0]["line_key"] == "correction_neutral"
    assert praise[0]["frames_matched"] == 0


def test_a_hand_right_for_fewer_frames_than_pose_confirm_frames_is_not_praised() -> None:
    frames = int(load_params(PARAMS_FILE).values["pose_confirm_frames"])
    assert frames >= 2, "the test needs a streak that can fall short"
    unsure = pose(9, 7, False, confidence=0.7)
    sure = pose(9, 7, False, confidence=0.9)
    # First run: find the frame on which the correction is chosen. Confidence
    # changes nothing on the clocks, so the second run lands on the same frame.
    probe = wrong_pose_harness()
    chosen: list[int] = []

    def watch(index: int) -> GestureState:
        if len(probe.kind("praise")) > len(chosen):
            chosen.append(index - 1)
        return unsure

    probe.feed_each(16.0, watch, hint=LEFT_HINT)
    assert chosen, "no correction was chosen"
    at = chosen[0]
    # Second run: the right hand becomes sure frames - 1 observations before the
    # choice, one short of what praise needs.
    switch = at - (frames - 2)
    harness = wrong_pose_harness()
    said = harness.feed_each(16.0, lambda i: sure if i >= switch else unsure,
                             hint=LEFT_HINT)
    spoken = [line for _, line in said]
    assert PRAISING not in spoken
    assert _lines()["correction_neutral"] in spoken
    event = harness.kind("praise")[0]
    assert event["praised"] is False
    assert 0 < int(event["frames_matched"]) < frames, event

    # The same switch one frame earlier reaches the streak, and praise with it.
    enough = wrong_pose_harness()
    said = enough.feed_each(16.0, lambda i: sure if i >= switch - 1 else unsure,
                            hint=LEFT_HINT)
    assert PRAISING in [line for _, line in said]
    assert enough.kind("praise")[0]["frames_matched"] == frames


def test_the_praise_decision_is_logged_once_with_its_fields() -> None:
    harness = wrong_pose_harness()
    harness.feed(16.0, gesture=pose(9, 7, False, confidence=0.9), hint=LEFT_HINT)
    praise = harness.kind("praise")
    # One line per decision, not per frame: sixteen seconds at 15 fps is 240
    # observations and the ladder chose the correction once.
    assert len(praise) == 1
    event = praise[0]
    assert set(event) >= {"hand", "finger_read", "expected", "confidence",
                          "frames_matched", "praised", "line_key", "ts",
                          "exercise", "learner_id"}
    assert event["hand"] == "right"
    assert event["finger_read"] == 7
    assert event["expected"] == 7
    assert event["confidence"] == pytest.approx(0.9)
    frames = int(harness.tutor.effective("pose_confirm_frames"))
    assert int(event["frames_matched"]) >= frames
    assert event["praised"] is True
    assert event["line_key"] == "wrong_left"


# --------------------------------------------------------------------------
# 19. the grace before a visibility line
# --------------------------------------------------------------------------
#
# A tracker that drops a frame or two is the camera blinking, not a child
# leaving. Nothing about visibility is said, drawn or entered until a hand has
# been missing for visibility_grace_ms without a single frame of both hands in
# between. Once it has, the reminders come when they always did: their clocks
# read from the frame the hands went, not from the end of the grace.

VISIBILITY_GRACE_S = GLOBALS["visibility_grace_ms"] / 1000.0
VISIBILITY_PROMPT_S = GLOBALS["visibility_prompt_ms"] / 1000.0
# Feeding this many frames with a hand missing stays inside the grace: the
# elapsed time is measured from the first of them, so it is one frame short.
# One more frame and the grace has run out.
GRACE_FRAMES = int(math.ceil(VISIBILITY_GRACE_S * FPS))
VISIBILITY_LINES = load_lines(LINES_FILE)
NO_HANDS_LINE = VISIBILITY_LINES["visibility_none"]
ONE_HAND_LINE = VISIBILITY_LINES["visibility_one"]


def watch_hands(harness: Harness, frames: int, hands: int,
                ) -> tuple[list[str], set[str | None], set[str]]:
    """Feed frames with this many hands and no pose; collect lines, visuals, states."""
    said: list[str] = []
    visuals: set[str | None] = set()
    states: set[str] = set()
    for _ in range(frames):
        harness.clock.tick()
        obs = Observation(gesture=None, fingers=fingers(0.0, hands), hands_seen=hands)
        harness.last = harness.tutor.observe(obs, harness.clock.t)
        if harness.last.tutor_line:
            said.append(harness.last.tutor_line)
        visual = harness.last.tutor_visual
        visuals.add(visual["kind"] if visual else None)
        states.add(harness.last.tutor_state)
    return said, visuals, states


def settled_harness() -> Harness:
    """Both hands in frame for a second and no pose yet: the tutor knows they are there."""
    harness = wrong_pose_harness()
    harness.feed(1.0, gesture=UNKNOWN, hands=2)
    assert harness.tutor._hands_ok is True
    assert harness.last.tutor_state == WORKING
    return harness


def test_the_visibility_grace_key_is_known_with_its_bounds() -> None:
    raw = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    params = load_params(PARAMS_FILE)
    assert "visibility_grace_ms" in REQUIRED_PARAMS
    assert raw["global"]["visibility_grace_ms"] == 1500
    assert raw["bounds"]["visibility_grace_ms"] == [500, 3000]
    assert params.values["visibility_grace_ms"] == 1500
    assert params.bound("visibility_grace_ms") == (500.0, 3000.0)
    for outside in (100, 9000):
        with pytest.raises(ParamsError) as excinfo:
            params_with(visibility_grace_ms=outside)
        message = str(excinfo.value)
        assert "visibility_grace_ms" in message
        assert "500" in message and "3000" in message


def test_the_visibility_grace_is_read_as_written_for_every_child() -> None:
    """Camera latency, like recovery_grace_ms: no pace factor, no mode factor."""
    harness = Harness(factors={"pace_factor": 9.0, "help_factor": 9.0,
                               "tutor_observations": 500})
    for mode in (SUPPORTIVE, NORMAL, INDEPENDENT):
        harness.tutor._mode = mode
        assert harness.tutor.effective("visibility_grace_ms") == 1500


@pytest.mark.parametrize("hands", (0, 1))
def test_one_lost_frame_says_nothing_and_changes_no_state(hands: int) -> None:
    harness = settled_harness()
    before = harness.tutor.state_fields()
    situation = (harness.tutor._situation_now, harness.tutor._situation_since)
    said, visuals, states = watch_hands(harness, 1, hands)
    assert said == [] and visuals == {None} and states == {WORKING}
    assert harness.tutor.state_fields() == before
    assert (harness.tutor._situation_now, harness.tutor._situation_since) == situation
    assert harness.tutor._hands_ok is True
    said, visuals, states = watch_hands(harness, FPS, hands=2)
    assert said == [] and visuals == {None} and states == {WORKING}
    assert harness.kind("intervention") == []


@pytest.mark.parametrize("hands", (0, 1))
def test_hands_missing_for_less_than_the_grace_get_nothing(hands: int) -> None:
    harness = settled_harness()
    said, visuals, states = watch_hands(harness, GRACE_FRAMES, hands)
    assert said == [] and visuals == {None} and states == {WORKING}
    assert harness.tutor._situation_now not in ("no_hands", "one_hand")
    said, visuals, states = watch_hands(harness, FPS, hands=2)
    assert said == [] and visuals == {None} and states == {WORKING}
    assert harness.kind("intervention") == []


def test_no_hands_for_the_grace_get_the_zones_then_the_line_as_before() -> None:
    harness = settled_harness()
    said, _, _ = watch_hands(harness, GRACE_FRAMES + 1, hands=0)
    assert said == []
    # The grace has run out: the zones and the recovery state, at the
    # no_hands_visual the file always had, because the clock reads from the
    # frame the hands went.
    assert harness.last.tutor_state == VISIBILITY_RECOVERY
    assert harness.last.tutor_visual == {"kind": "placement_zones"}
    voice_frames = int(math.ceil(GLOBALS["no_hands_voice"] * FPS)) + 2
    said, _, _ = watch_hands(harness, voice_frames - (GRACE_FRAMES + 1), hands=0)
    assert said == [NO_HANDS_LINE]


def test_one_hand_for_the_grace_gets_the_line_as_before() -> None:
    harness = settled_harness()
    said, visuals, _ = watch_hands(harness, GRACE_FRAMES + 1, hands=1)
    assert said == [] and visuals == {None}
    voice_frames = int(math.ceil(GLOBALS["one_hand_voice"] * FPS)) + 2
    said, _, _ = watch_hands(harness, voice_frames - (GRACE_FRAMES + 1), hands=1)
    assert said == [ONE_HAND_LINE]
    assert harness.last.tutor_state == VISIBILITY_RECOVERY


def test_the_grace_is_continuous_so_a_flicker_never_adds_up() -> None:
    """Twelve seconds of hands mostly gone, one good frame short of the grace each time."""
    harness = settled_harness()
    said: list[str] = []
    visuals: set[str | None] = set()
    states: set[str] = set()
    for _ in range(8):
        for hands, frames in ((0, GRACE_FRAMES), (2, 1)):
            more, kinds, seen = watch_hands(harness, frames, hands)
            said += more
            visuals |= kinds
            states |= seen
    assert said == [] and visuals == {None}
    assert VISIBILITY_RECOVERY not in states
    assert harness.kind("intervention") == []


def test_hands_already_missing_when_the_exercise_opens_start_its_clock_then() -> None:
    """The grace ran out before the exercise: the opening still waits its own time."""
    harness = Harness()
    harness.feed(5.0, gesture=None, hands=0)
    harness.tutor.new_exercise(8, 7, node="u2-l3", now=harness.clock.t)
    said, visuals, _ = watch_hands(harness, FPS, hands=0)
    assert said == [] and visuals == {None}
    said, _, _ = watch_hands(harness, int(math.ceil(VISIBILITY_PROMPT_S * FPS)) - FPS + 2,
                             hands=0)
    assert said == [COME_HERE]


def test_the_recovery_grace_is_untouched_by_the_visibility_grace() -> None:
    """Entering is gated by one grace, leaving still opens the other."""
    harness = settled_harness()
    harness.feed(INITIAL_SILENCE, gesture=UNKNOWN, hands=2)
    watch_hands(harness, GRACE_FRAMES + 1, hands=0)
    assert harness.last.tutor_state == VISIBILITY_RECOVERY
    harness.feed(1.0, gesture=UNKNOWN, hands=2)
    assert harness.tutor.in_recovery is True
