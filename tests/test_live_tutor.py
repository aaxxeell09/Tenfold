"""app/tutor.py against docs/tutor_contract.md.

Injected clock, no sleeps anywhere: a whole lesson runs in microseconds and the
same lesson runs the same way twice. Every invariant of contract section 2.5 has
its own test, named for it, because those are the rules that decide the arguments
the numbers cannot.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.tutor import (
    ANSWER_RETRY,
    CHECK_ANSWER,
    INDEPENDENT,
    LINE_KEYS,
    NORMAL,
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
    jsonl_sink,
    load_lines,
    load_params,
)
from classifier.schema import GestureState

REPO = Path(__file__).resolve().parents[1]
PARAMS_FILE = REPO / "lesson" / "tutor_params.json"
LINES_FILE = REPO / "lesson" / "tally_lines.json"

FPS = 15
STEP = 1.0 / FPS
UNKNOWN = GestureState.unknown(0.1)


def pose(left: int, right: int, contact: bool = True) -> GestureState:
    return GestureState(method="6-10", left=left, right=right, contact=contact,
                        confidence=0.9)


def fingers(dx: float = 0.0, hands: int = 2) -> list[dict[str, object]]:
    names = {0: (), 1: ("left",), 2: ("left", "right")}[hands]
    out: list[dict[str, object]] = []
    for hand in names:
        base = 0.3 if hand == "left" else 0.7
        for number in range(6, 11):
            out.append({"hand": hand, "number": number, "x": base + dx,
                        "y": 0.5 + 0.02 * (number - 6)})
    return out


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
            obs = Observation(gesture=gesture, fingers=fingers(drift * (index + 1), hands),
                              hands_seen=hands, hint=hint)
            self.last = self.tutor.observe(obs, self.clock.t)
            if self.last.tutor_line:
                said.append(self.last.tutor_line)
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


def test_params_file_has_exactly_the_contract_keys() -> None:
    raw = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
    assert list(raw) == ["global", "bounds", "invariants"]
    assert set(raw["global"]) == set(REQUIRED_PARAMS)
    assert set(raw["bounds"]) == set(REQUIRED_PARAMS)
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
    assert len(raw) == 20
    assert set(raw) == set(LINE_KEYS)
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
    harness.feed(2.0, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.tutor_state == WORKING
    harness.feed(1.5, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.tutor_state == WRONG_POSE


def test_pose_ready_then_check_answer_then_success() -> None:
    harness = wrong_pose_harness()
    while harness.last.tutor_state != POSE_READY and harness.clock.t < 5.0:
        harness.feed(STEP, gesture=pose(8, 7, True))
    assert harness.last.tutor_state == POSE_READY
    assert harness.clock.t == pytest.approx(0.8 + STEP, abs=STEP)
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
    assert any("other hand" in line for line in said)


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
    assert said[0] == "Look at your hands. One finger needs to move."
    assert said[1] == "Almost. Your right hand needs 7, not 9."
    assert said[2] == "See the faint circle? Move your right finger from 9 to 7."
    assert said[3].startswith("Together: 5 tens makes 50")
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


def test_the_level_resets_on_a_new_exercise() -> None:
    harness = wrong_pose_harness()
    harness.feed(10.0, gesture=pose(8, 9, False), hint=HINT)
    assert harness.last.intervention_level >= 1
    decision = harness.tutor.new_exercise(6, 6, node="u2-l3", now=harness.clock.t)
    assert decision.intervention_level == 0


def test_no_contact_and_swapped_get_their_own_lines() -> None:
    harness = wrong_pose_harness()
    said = harness.feed(6.0, gesture=pose(8, 7, False))
    assert said and said[0] == "So close. Let those two fingertips touch."
    assert harness.last.tutor_visual == {"kind": "finger_numbers"}

    other = wrong_pose_harness()
    said = other.feed(6.0, gesture=pose(7, 8, False))
    assert said and said[0] == "Almost. Swap your hands and try again."


def test_the_counting_nudge_comes_when_the_pose_is_right_and_no_answer() -> None:
    harness = wrong_pose_harness()
    said = harness.feed(10.0, gesture=pose(8, 7, True))
    assert "That is it. Now count the tens, then the ones." in said


def test_a_requested_hint_climbs_one_step_and_never_counts_as_nagging() -> None:
    harness = wrong_pose_harness()
    harness.feed(1.0, gesture=pose(8, 9, False), hint=HINT)
    decision = harness.tutor.hint_requested(now=harness.clock.t)
    assert decision.intervention_level == 1
    assert decision.tutor_line
    harness.tutor.end_exercise(reason="skipped", now=harness.clock.t)
    line = harness.kind("exercise")[0]
    assert line["unsolicited_interventions"] == 0
    assert line["first_try"] is False


# --------------------------------------------------------------------------
# 5. the anti nag limits
# --------------------------------------------------------------------------


def test_max_unsolicited_verbal_caps_the_spoken_interventions() -> None:
    harness = wrong_pose_harness()
    said = harness.feed(40.0, gesture=pose(8, 9, False), hint=HINT)
    assert len(said) == 3
    assert any(line["reason"] == "budget_spent" for line in harness.kind("decision"))


def test_help_factor_raises_the_budget_and_never_a_duration() -> None:
    harness = Harness(factors={"pace_factor": 1.0, "help_factor": 5.0,
                               "tutor_observations": 50})
    assert harness.tutor.effective("max_unsolicited_verbal") == 4
    assert harness.tutor.effective("max_visibility_reminders") == 3
    assert harness.tutor.effective("wrong_pose_prompt") == 2.0


def test_max_visibility_reminders_caps_the_visibility_help() -> None:
    harness = wrong_pose_harness()
    harness.feed(40.0, gesture=None, hands=0)
    visibility = [line for line in harness.kind("intervention")
                  if line["state_before"] in (WORKING, VISIBILITY_RECOVERY, PROMPTING)]
    assert len(visibility) == 2


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
        harness.feed(30.0, gesture=gesture)
        assert harness.last.scored_gesture_error is False
        assert harness.last.first_try is True


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
    for value in (54, 55, 57):
        harness.tutor.answer(value, correct=False, now=harness.clock.t)
    harness.tutor.end_exercise(reason="skipped", now=harness.clock.t)
    assert harness.kind("exercise")[0]["scored_math_errors"] == 3


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
    assert "right" in (decision.tutor_line or "")
    assert "wrong" not in (decision.tutor_line or "").lower()
    said = harness.feed(2.0, gesture=pose(8, 7, True))
    assert any("There it is" in line for line in said)


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
    assert decision.tutor_line == "You are flying today. I will watch quietly."
    harness.tutor.end_exercise(reason="skipped", now=harness.clock.t)
    again = harness.tutor.new_exercise(6, 7, node="n", now=harness.clock.t)
    assert again.tutor_line is None


def test_supportive_after_two_hard_exercises_with_the_finger_numbers() -> None:
    harness = Harness()
    hard_exercise(harness)
    hard_exercise(harness)
    decision = harness.tutor.new_exercise(6, 6, node="n", now=harness.clock.t)
    assert decision.mode == SUPPORTIVE
    assert decision.tutor_line == "Let us take this one slowly, together."
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
    assert decision.tutor_line == "One last easy one, just for fun: what is 10 x 10?"
    assert decision.mode == SUPPORTIVE
    harness.feed(1.5, gesture=pose(10, 10, True))
    harness.tutor.answer(100, correct=True, now=harness.clock.t)
    harness.tutor.end_exercise(reason="answered", now=harness.clock.t)
    plan = harness.tutor.early_stop_plan()
    assert plan["stage"] == "closing"
    assert plan["line"] == "Lovely work today. Come back soon and we keep going."


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
    assert harness.tutor.motion_threshold == 0.03
    harness.tutor.start_check()
    harness.feed(0.5, gesture=pose(6, 6, True))
    assert harness.tutor.end_check() == 0.03


def test_the_motion_threshold_is_four_times_the_measured_jitter() -> None:
    harness = Harness()
    harness.tutor.start_check()
    for index in range(40):
        harness.clock.tick()
        obs = Observation(gesture=pose(6, 6, True),
                          fingers=fingers(0.02 * index), hands_seen=2)
        harness.tutor.observe(obs, harness.clock.t)
    assert harness.tutor.end_check() == pytest.approx(0.08, abs=1e-6)


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
                 "intervention", "reason", "scored_error", "mode", "params_version"}
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
# 14. the eight fields on the wire
# --------------------------------------------------------------------------


def test_the_state_fields_are_the_eight_of_the_contract() -> None:
    harness = wrong_pose_harness()
    fields = harness.tutor.state_fields()
    assert set(fields) == {"tutor_state", "intervention_level", "tutor_line",
                           "tutor_visual", "scored_gesture_error",
                           "scored_math_error", "first_try", "mode"}
    assert fields["tutor_state"] == PROMPTING
    assert fields["intervention_level"] == 0
    assert fields["mode"] == NORMAL


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
    assert said == ["Your turn: make 8 x 7 with your fingers."]
    assert harness.last.tutor_visual == {"kind": "finger_numbers"}
    assert harness.last.tutor_state == WORKING
    assert harness.last.scored_gesture_error is False
    said = harness.feed(6.0, gesture=UNKNOWN, hands=2)
    assert said == ["Hands up and flat, please, so I can see every finger."]
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
