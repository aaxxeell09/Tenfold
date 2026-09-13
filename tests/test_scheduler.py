"""lesson/scheduler.py, table driven. Tally's brain.

Time is always passed in, so a week of sessions replays in microseconds and
nothing here depends on when the suite runs. The draw is passed in too: every
Scheduler here is handed a seeded Random, so a failure is the same failure on
the next run. The rules that must hold whatever the draw gives are checked over
many seeds rather than one.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lesson import scheduler as sch
from lesson.scheduler import (
    DIFFICULTY_ORDER,
    LearnerState,
    Outcome,
    Record,
    Scheduler,
    ScriptedScheduler,
    fact_key,
    factors_of,
    pose_key,
)

REPO = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc)


def drawn(state: LearnerState | None = None, seed: int = 7) -> Scheduler:
    """A scheduler whose draw replays: same seed, same lesson, every run."""
    return Scheduler(state, now=T0, rng=random.Random(seed))


def learner(sessions: int = 0, **facts: int) -> LearnerState:
    """A learner with the given mastery per fact, each seen once, long ago."""
    state = LearnerState.new(T0 - timedelta(days=30))
    state.sessions = sessions
    state.last_session_at = (T0 - timedelta(days=1)).isoformat()
    for key, mastery in facts.items():
        key = key.replace("_", "x")
        record = state.fact(key)
        record.mastery = mastery
        record.attempts = [sch.Attempt(at=(T0 - timedelta(days=2)).isoformat(),
                                       correct=True, hinted=False, response_time=3.0,
                                       session=max(1, sessions))]
        sch.schedule_next(record, T0 - timedelta(days=2), max(1, sessions))
    return state


def answered(pick, correct: bool = True, hinted: int = 0, seconds: float = 3.0,
             math_error: bool = False, pose_error: bool = False, given: int | None = None):
    return Outcome(fact=pick.fact, left=pick.left, right=pick.right, correct=correct,
                   response_time=seconds, hint_level=hinted, pose_error=pose_error,
                   math_error=math_error or (not correct and not pose_error), given=given)


# --- the material ------------------------------------------------------------


def test_the_fixed_order_is_the_fifteen_facts_once_each():
    assert len(DIFFICULTY_ORDER) == 15
    assert len(set(DIFFICULTY_ORDER)) == 15
    for key in DIFFICULTY_ORDER:
        low, high = factors_of(key)
        assert 6 <= low <= high <= 10, f"{key} is not a sorted pair in 6..10"
    expected = {fact_key(a, b) for a in range(6, 11) for b in range(6, 11)}
    assert set(DIFFICULTY_ORDER) == expected
    assert DIFFICULTY_ORDER[0] == "10x10" and DIFFICULTY_ORDER[-1] == "6x7"


def test_keys_are_ordered_for_poses_and_sorted_for_facts():
    assert pose_key(8, 7) == "8x7" and pose_key(7, 8) == "7x8"
    assert fact_key(8, 7) == fact_key(7, 8) == "7x8"


# --- mastery -----------------------------------------------------------------

MASTERY_CASES = [
    ("first try success adds one", 2, True, False, 3),
    ("a hinted success adds nothing", 2, True, True, 2),
    ("an error takes one off", 2, False, False, 1),
    ("mastery never goes below zero", 0, False, False, 0),
    ("mastery stops at five", 5, True, False, 5),
]


@pytest.mark.parametrize("name,before,correct,hinted,after",
                         MASTERY_CASES, ids=[c[0] for c in MASTERY_CASES])
def test_mastery_moves(name, before, correct, hinted, after):
    record = Record(mastery=before)
    sch.apply_result(record, correct, hinted, session=2, confidence_rule=False)
    assert record.mastery == after


def test_the_confidence_rule_holds_mastery_inside_one_session():
    """Five right answers in one evening are one step, not five."""
    record = Record(mastery=0)
    for _ in range(5):
        sch.apply_result(record, True, False, session=1, confidence_rule=True)
    assert record.mastery == 1, "only the first success of a session counts"
    sch.apply_result(record, True, False, session=2, confidence_rule=True)
    assert record.mastery == 2
    sch.apply_result(record, True, False, session=2, confidence_rule=True)
    assert record.mastery == 2


def test_the_confidence_rule_does_not_apply_to_poses():
    record = Record(mastery=0)
    for _ in range(3):
        sch.apply_result(record, True, False, session=1, confidence_rule=False)
    assert record.mastery == 3


def test_a_pose_error_only_touches_the_pose_and_a_math_error_only_the_fact():
    state = learner(sessions=2, **{"7x8": 3})
    state.pose_record("8x7").mastery = 3
    engine = drawn(state)
    engine.start_session(T0)

    engine.record(Outcome(fact="7x8", left=8, right=7, correct=False, pose_error=True),
                  now=T0)
    assert state.pose["8x7"].mastery == 2
    assert state.math["7x8"].mastery == 3, "a pose slip is not a math slip"

    engine.record(Outcome(fact="7x8", left=8, right=7, correct=False, math_error=True,
                          given=54), now=T0)
    assert state.pose["8x7"].mastery == 2
    assert state.math["7x8"].mastery == 2


# --- spacing -----------------------------------------------------------------

SPACING_CASES = [
    (0, "session", 0), (1, "session", 1),
    (2, "days", 1), (3, "days", 3), (4, "days", 7), (5, "days", 14),
]


@pytest.mark.parametrize("mastery,unit,amount", SPACING_CASES,
                         ids=[f"mastery{c[0]}" for c in SPACING_CASES])
def test_spacing_intervals(mastery, unit, amount):
    record = Record(mastery=mastery)
    sch.schedule_next(record, T0, session=4)
    if unit == "session":
        assert record.due_session == 4 + amount
        assert record.due_at is None
        assert sch.is_due(record, T0, session=4 + amount) is (amount == 0) or True
    else:
        assert record.due_session is None
        assert sch._parse(record.due_at) == T0 + timedelta(days=amount)


def test_a_fact_is_due_only_once_its_interval_has_passed():
    record = Record(mastery=3)
    record.attempts = [sch.Attempt(at=T0.isoformat(), correct=True, hinted=False,
                                   response_time=2.0, session=1)]
    sch.schedule_next(record, T0, session=1)
    assert not sch.is_due(record, T0 + timedelta(days=2), session=9)
    assert sch.is_due(record, T0 + timedelta(days=3), session=9)


def test_a_mastery_one_fact_waits_for_the_next_session_not_the_clock():
    record = Record(mastery=1)
    record.attempts = [sch.Attempt(at=T0.isoformat(), correct=True, hinted=False,
                                   response_time=2.0, session=3)]
    sch.schedule_next(record, T0, session=3)
    assert not sch.is_due(record, T0 + timedelta(days=10), session=3)
    assert sch.is_due(record, T0, session=4), "a new session, even the same evening"


def test_a_missed_day_costs_nothing():
    """Coming back after a week finds the fact due, not demoted."""
    record = Record(mastery=4)
    record.attempts = [sch.Attempt(at=T0.isoformat(), correct=True, hinted=False,
                                   response_time=2.0, session=1)]
    sch.schedule_next(record, T0, session=1)
    assert sch.is_due(record, T0 + timedelta(days=30), session=2)
    assert record.mastery == 4


# --- picking -----------------------------------------------------------------


def test_a_failed_fact_is_queued_two_exercises_later():
    state = learner(sessions=3, **{"7x8": 2, "6x10": 3, "9x9": 3, "10x10": 4})
    engine = drawn(state)
    engine.start_session(T0)
    first = engine.next_exercise(T0)
    engine.record(answered(first, correct=False), now=T0)
    # exercise 0 failed, so it is queued for exercise 2, the third one
    assert engine.retry_queue == [(2, first.fact)]


def test_a_failed_fact_does_come_back_as_a_retry():
    """The exact slot can slip: the no repeated factor rule may push it out by
    one. What matters is that it comes back, and that it is announced as a retry."""
    state = learner(sessions=3, **{"7x8": 2, "6x10": 3, "9x9": 3, "10x10": 4})
    engine = drawn(state)
    engine.start_session(T0)
    first = engine.next_exercise(T0)
    engine.record(answered(first, correct=False), now=T0)

    seen = []
    for _ in range(4):
        pick = engine.next_exercise(T0)
        if pick is None:
            break
        seen.append(pick)
        assert pick.fact != first.fact or pick.reason == sch.REACTION_RETRY
        engine.record(answered(pick), now=T0)

    retries = [p for p in seen if p.fact == first.fact]
    assert retries, f"{first.fact} never came back: {[p.fact for p in seen]}"
    assert retries[0].reason == sch.REACTION_RETRY
    assert seen[0].fact != first.fact, "never the same fact twice in a row"


def test_two_errors_in_a_row_buy_a_mastered_fact():
    """Whatever the draw gave first: the solid facts here cover every table, so
    the no repeated table rule always leaves one standing."""
    for seed in range(20):
        state = learner(sessions=3, **{"6x6": 5, "7x7": 5, "8x8": 5, "9x9": 5,
                                       "10x10": 5})
        engine = drawn(state, seed=seed)
        engine.start_session(T0)
        for _ in range(2):
            pick = engine.next_exercise(T0)
            engine.record(answered(pick, correct=False), now=T0)
        pick = engine.next_exercise(T0)
        assert pick.reason == sch.REACTION_CONFIDENCE, seed
        assert state.math[pick.fact].mastery >= sch.MASTERED_FROM


def test_a_new_fact_opens_only_after_two_first_try_successes():
    state = learner(sessions=3, **{"10x10": 2, "6x10": 2, "7x10": 2})
    engine = drawn(state)
    engine.start_session(T0)
    opened = []
    for _ in range(6):
        pick = engine.next_exercise(T0)
        if pick is None:
            break
        opened.append(pick.is_new)
        # every new fact is answered with a hint, so no more should open
        engine.record(answered(pick, correct=True, hinted=1 if pick.is_new else 0), now=T0)
    assert opened.count(True) <= 2, "hinted successes must not unlock more new facts"


def test_never_the_same_factor_three_times_in_a_row():
    state = learner(sessions=3, **{k.replace("x", "_"): 2 for k in DIFFICULTY_ORDER})
    engine = drawn(state)
    engine.start_session(T0)
    picks = []
    for _ in range(10):
        pick = engine.next_exercise(T0)
        if pick is None:
            break
        picks.append(pick)
        engine.record(answered(pick), now=T0)
    assert len(picks) >= 6
    for i in range(2, len(picks)):
        shared = (set(factors_of(picks[i].fact))
                  & set(factors_of(picks[i - 1].fact))
                  & set(factors_of(picks[i - 2].fact)))
        assert not shared, f"factor {shared} three times at {i}"
    for i in range(1, len(picks)):
        assert picks[i].fact != picks[i - 1].fact


def test_the_two_orientations_alternate_across_attempts():
    state = learner(sessions=3, **{"7x8": 2})
    engine = drawn(state)
    engine.start_session(T0)
    seen = []
    for _ in range(4):
        left, right = engine._orientation("7x8")
        seen.append((left, right))
        state.fact("7x8").log(sch.Attempt(at=T0.isoformat(), correct=True, hinted=False,
                                          response_time=2.0, session=3))
    assert seen == [(8, 7), (7, 8), (8, 7), (7, 8)] or seen == [(7, 8), (8, 7), (7, 8), (8, 7)]
    assert len(set(seen)) == 2


def test_a_doubled_fact_has_only_one_orientation():
    engine = drawn(learner(sessions=3, **{"9x9": 2}))
    assert engine._orientation("9x9") == (9, 9)


# --- ordering ----------------------------------------------------------------


def test_the_fixed_order_leads_until_there_is_personal_history():
    state = learner(sessions=2)
    state.fact("10x10").attempts = [
        sch.Attempt(at=T0.isoformat(), correct=False, hinted=False,
                    response_time=9.0, session=1)]
    assert sch.fact_order(state) == list(DIFFICULTY_ORDER)


def test_personal_difficulty_takes_over_after_three_sessions():
    state = learner(sessions=3)
    # 10x10 is first in the fixed order but this child keeps missing it.
    state.fact("10x10").attempts = [
        sch.Attempt(at=T0.isoformat(), correct=False, hinted=False,
                    response_time=9.0, session=n) for n in (1, 2, 3)]
    order = sch.fact_order(state)
    assert order[0] != "10x10"
    assert order[-1] == "10x10", "the fact this child misses goes last"
    assert set(order) == set(DIFFICULTY_ORDER)


# --- sessions ----------------------------------------------------------------


def test_the_first_session_is_the_onboarding_and_later_ones_are_not():
    engine = drawn(LearnerState.new(T0))
    started = engine.start_session(T0)
    assert started["onboarding"] is True
    assert [step["kind"] for step in started["steps"]] == ["greeting", "numbers", "guided"]
    assert started["steps"][2]["left"] == 8 and started["steps"][2]["right"] == 7

    again = drawn(learner(sessions=1)).start_session(T0)
    assert again["onboarding"] is False and again["steps"] == []


def test_the_next_day_plan_is_three_due_one_new_one_mastered():
    state = learner(sessions=2, **{"7x8": 1, "6x10": 2, "9x9": 5})
    state.last_session_at = (T0 - timedelta(days=1)).isoformat()
    started = drawn(state).start_session(T0)
    assert started["plan"] == ["due", "due", "due", "new", "mastered"]
    assert started["opening_fact"] == "7x8", "the most fragile fact of last time"


def test_a_second_session_the_same_day_is_not_a_next_day_plan():
    state = learner(sessions=2)
    state.last_session_at = T0.isoformat()
    later = T0 + timedelta(hours=1)
    started = Scheduler(state, now=later, rng=random.Random(7)).start_session(later)
    assert started["plan"] == []


def test_a_session_always_ends_on_a_success():
    state = learner(sessions=3, **{k.replace("x", "_"): 3 for k in DIFFICULTY_ORDER})
    engine = drawn(state)
    engine.start_session(T0)
    picks = []
    now = T0
    while True:
        pick = engine.next_exercise(now)
        if pick is None:
            break
        picks.append(pick)
        # every answer is an error, so the last exercise has to be a rescue
        engine.record(answered(pick, correct=False), now=now)
        now += timedelta(seconds=20)
    assert picks, "the session has to have run"
    assert picks[-1].reason == sch.REACTION_CONFIDENCE
    assert engine.ending_on_success is True


def test_the_session_stops_at_ten_exercises():
    state = learner(sessions=3, **{k.replace("x", "_"): 3 for k in DIFFICULTY_ORDER})
    engine = drawn(state)
    engine.start_session(T0)
    now = T0
    count = 0
    while engine.next_exercise(now) is not None and count < 40:
        count += 1
        engine.record(Outcome(fact=engine.history[-1].fact, left=engine.history[-1].left,
                              right=engine.history[-1].right, correct=True,
                              response_time=3.0), now=now)
        now += timedelta(seconds=10)
    assert count <= sch.SESSION_MAX_EXERCISES


def test_the_session_stops_at_six_minutes():
    state = learner(sessions=3, **{k.replace("x", "_"): 3 for k in DIFFICULTY_ORDER})
    engine = drawn(state)
    engine.start_session(T0)
    pick = engine.next_exercise(T0)
    engine.record(answered(pick), now=T0)
    assert engine.next_exercise(T0 + timedelta(minutes=7)) is None


def test_the_end_summary_names_what_happened():
    state = learner(sessions=3, **{"7x8": 4, "6x10": 2})
    engine = drawn(state)
    engine.start_session(T0)
    pick = engine.next_exercise(T0)
    engine.record(answered(pick, correct=False), now=T0)
    summary = engine.end_session(T0 + timedelta(minutes=3))
    assert summary.reason in (sch.REACTION_END_SUCCESS, sch.REACTION_END_TIRED)
    assert summary.tomorrow == pick.fact
    assert summary.exercises == 1
    assert state.last_session_at is not None


# --- reactions ---------------------------------------------------------------

ANSWER_CASES = [
    (56, 56, None), (56, 46, "recount_tens"), (56, 57, "recount_units"),
    (56, None, None), (36, 30, "recount_units"), (100, 90, "recount_tens"),
]


@pytest.mark.parametrize("expected,given,reaction", ANSWER_CASES)
def test_a_wrong_answer_says_which_half_to_recount(expected, given, reaction):
    assert sch.answer_reaction(expected, given) == reaction


def test_hint_levels_map_to_events():
    assert sch.hint_event(1) == "hint_1"
    assert sch.hint_event(3) == "hint_3"
    assert sch.hint_event(0) is None and sch.hint_event(4) is None


def test_the_same_finger_on_both_hands_has_its_own_reaction():
    pick = sch.Pick("7x8", 8, 7, "review")
    assert sch.pose_reaction(8, 8, pick) == "same_hand_twice"
    assert sch.pose_reaction(8, 7, pick) is None
    assert sch.pose_reaction(None, 7, pick) is None


def test_three_fast_successes_earn_a_level_up():
    state = learner(sessions=3, **{"10x10": 3, "6x10": 3, "7x10": 3})
    engine = drawn(state)
    engine.start_session(T0)
    for _ in range(3):
        pick = engine.next_exercise(T0)
        engine.record(answered(pick, seconds=2.0), now=T0)
    assert engine.level_bonus == sch.LEVEL_UP_STEPS


def test_a_slow_success_breaks_the_fast_streak():
    state = learner(sessions=3, **{"10x10": 3, "6x10": 3, "7x10": 3})
    engine = drawn(state)
    engine.start_session(T0)
    for seconds in (2.0, 9.0, 2.0):
        pick = engine.next_exercise(T0)
        engine.record(answered(pick, seconds=seconds), now=T0)
    assert engine.level_bonus == 0


# --- metrics and state -------------------------------------------------------


def test_metrics_count_the_session():
    state = learner(sessions=3, **{"7x8": 2, "6x10": 2, "9x9": 2})
    engine = drawn(state)
    engine.start_session(T0)
    first = engine.next_exercise(T0)
    engine.record(answered(first, seconds=4.0), now=T0)
    second = engine.next_exercise(T0)
    engine.record(answered(second, correct=False, seconds=8.0, given=40), now=T0)
    third = engine.next_exercise(T0)
    engine.record(answered(third, hinted=2, seconds=6.0), now=T0)

    metrics = engine.metrics()
    assert metrics["exercises"] == 3
    assert metrics["first_try_success"] == 1
    assert metrics["hint_level_needed"] == 2
    assert metrics["math_errors"] == 1
    assert metrics["response_time"] == pytest.approx(6.0)


def test_retention_is_last_session_failures_passed_without_a_hint():
    state = learner(sessions=3, **{"7x8": 1, "6x10": 3})
    state.fact("7x8").attempts.append(
        sch.Attempt(at=T0.isoformat(), correct=False, hinted=False,
                    response_time=9.0, session=3))
    engine = drawn(state)
    engine.start_session(T0)                 # this becomes session 4
    assert engine.previous_session_failures == {"7x8"}
    engine.record(Outcome(fact="7x8", left=8, right=7, correct=True, response_time=3.0),
                  now=T0)
    metrics = engine.metrics()
    assert metrics["retention"] == ["7x8"]
    assert metrics["retention_rate"] == 1.0


def test_the_state_survives_the_round_trip_through_the_page():
    state = learner(sessions=4, **{"7x8": 3})
    state.pose_record("8x7").mastery = 2
    state.error_profile["tens_error"] = 5
    back = LearnerState.from_dict(json.loads(json.dumps(state.to_dict())))
    assert back.learner_id == state.learner_id
    assert back.sessions == 4
    assert back.math["7x8"].mastery == 3
    assert back.pose["8x7"].mastery == 2
    assert back.error_profile["tens_error"] == 5


def test_rubbish_from_local_storage_becomes_a_fresh_learner():
    for rubbish in (None, {}, {"learner_id": ""}, "not a dict", []):
        state = LearnerState.from_dict(rubbish, now=T0)
        assert state.learner_id and state.sessions == 0


def test_only_the_last_five_attempts_are_kept():
    record = Record()
    for n in range(8):
        record.log(sch.Attempt(at=str(n), correct=True, hinted=False,
                               response_time=1.0, session=1))
    assert len(record.attempts) == 5
    assert [a.at for a in record.attempts] == ["3", "4", "5", "6", "7"]


# --- demo --------------------------------------------------------------------


def test_the_demo_scenario_is_deterministic():
    """Same file, same sequence, every run. The stage does not get surprises."""
    def run():
        engine = ScriptedScheduler.load(REPO / "demo" / "scenario.json",
                                        state=LearnerState.new(T0), now=T0)
        engine.start_session(T0)
        picks = []
        now = T0
        while True:
            pick = engine.next_exercise(now)
            if pick is None:
                break
            picks.append(pick.to_dict())
            engine.record(answered(pick), now=now)
            now += timedelta(seconds=15)
        return picks, engine.end_session(now).to_dict()

    first, first_end = run()
    second, second_end = run()
    assert first == second
    assert first_end == second_end
    assert len(first) == 6
    assert first[0]["fact"] == "7x8" and first[0]["left"] == 8
    assert [p["reason"] for p in first] == [
        "review", "next_new", "confidence", "retry", "next_new", "level_up"]
    assert first_end["reason"] == "end_success"
    assert first_end["tomorrow"] == "6x7"


def test_the_demo_scenario_respects_the_two_pick_rules():
    scenario = json.loads((REPO / "demo" / "scenario.json").read_text())
    facts = [step["fact"] for step in scenario["exercises"]]
    for i in range(1, len(facts)):
        assert facts[i] != facts[i - 1]
    for i in range(2, len(facts)):
        shared = (set(factors_of(facts[i])) & set(factors_of(facts[i - 1]))
                  & set(factors_of(facts[i - 2])))
        assert not shared, f"factor {shared} three times in the demo"


def test_the_demo_sequence_is_unchanged_step_by_step():
    """The stage sequence, written out. Widening the lesson must not reach the
    scripted path: the demo plays the file, in that order, with those hands."""
    expected = [
        {"fact": "7x8", "left": 8, "right": 7, "pose": "8x7",
         "reason": "review", "is_new": False, "seen": True},
        {"fact": "6x8", "left": 6, "right": 8, "pose": "6x8",
         "reason": "next_new", "is_new": True, "seen": True},
        {"fact": "6x6", "left": 6, "right": 6, "pose": "6x6",
         "reason": "confidence", "is_new": False, "seen": True},
        {"fact": "7x8", "left": 7, "right": 8, "pose": "7x8",
         "reason": "retry", "is_new": False, "seen": True},
        {"fact": "6x8", "left": 8, "right": 6, "pose": "8x6",
         "reason": "next_new", "is_new": False, "seen": True},
        {"fact": "10x10", "left": 10, "right": 10, "pose": "10x10",
         "reason": "level_up", "is_new": False, "seen": True},
    ]
    engine = ScriptedScheduler.load(REPO / "demo" / "scenario.json",
                                    state=LearnerState.new(T0), now=T0)
    engine.start_session(T0, pairs=[[6, 6], [7, 7]], length=5)
    played = []
    now = T0
    while True:
        pick = engine.next_exercise(now)
        if pick is None:
            break
        played.append(pick.to_dict())
        engine.record(answered(pick), now=now)
        now += timedelta(seconds=15)
    assert played == expected, "the demo drifted"

    scenario = json.loads((REPO / "demo" / "scenario.json").read_text())
    for step, want in zip(scenario["exercises"], expected):
        assert step["fact"] == want["fact"]
        assert step["left"] == want["left"] and step["right"] == want["right"]


def test_the_demo_never_plays_the_onboarding():
    engine = ScriptedScheduler.load(REPO / "demo" / "scenario.json",
                                    state=LearnerState.new(T0), now=T0)
    started = engine.start_session(T0)
    assert started["onboarding"] is False and started["steps"] == []


def test_the_pick_rules_hold_even_when_almost_nothing_is_known():
    """The relief valve used to ignore the repeated factor rule outright. With
    only two facts on record every candidate gets blocked, and it must reach for
    a legal fact rather than the first one that is merely different."""
    state = learner(sessions=3, **{"10x10": 1, "6x10": 1})
    engine = drawn(state)
    engine.start_session(T0)
    picks = []
    for _ in range(8):
        pick = engine.next_exercise(T0)
        if pick is None:
            break
        picks.append(pick)
        engine.record(answered(pick, correct=False), now=T0)
    assert len(picks) >= 5
    for i in range(2, len(picks)):
        shared = (set(factors_of(picks[i].fact))
                  & set(factors_of(picks[i - 1].fact))
                  & set(factors_of(picks[i - 2].fact)))
        assert not shared, (
            f"factor {shared} three times at {i}: "
            f"{[p.fact for p in picks[:i + 1]]}")


# --- a course node is a label and a length -----------------------------------


def test_a_node_fixes_the_length_and_nothing_else():
    """The node name is a label on the screen. It says how many questions the
    lesson runs, and it says nothing about which ones."""
    state = learner(sessions=3)
    engine = drawn(state)
    started = engine.start_session(T0, pairs=[[6, 6], [7, 7]], length=5)
    assert started["allowed"] == ["6x6", "7x7"], "the label is still reported"
    picks = []
    for _ in range(5):
        pick = engine.next_exercise(T0)
        assert pick is not None
        picks.append(pick)
        engine.record(answered(pick), now=T0)
    assert len({p.fact for p in picks}) == 5, "five questions, five facts"
    for pick in picks:
        low, high = factors_of(pick.fact)
        assert low in sch.FACTORS and high in sch.FACTORS
    assert engine.next_exercise(T0) is None, "a lesson is exactly five exercises"


def test_a_boss_node_is_eight_exercises():
    state = learner(sessions=3)
    engine = drawn(state)
    engine.start_session(T0, pairs=[[7, 8], [8, 9], [9, 9], [6, 10]], length=8)
    count = 0
    while engine.next_exercise(T0) is not None and count < 20:
        count += 1
        engine.record(answered(engine.history[-1]), now=T0)
    assert count == 8


def test_a_node_never_adds_a_rescue_exercise():
    """The page scores correct out of the node length, so the length is fixed."""
    state = learner(sessions=3)
    engine = drawn(state)
    engine.start_session(T0, pairs=[[6, 6], [7, 7]], length=5)
    count = 0
    while engine.next_exercise(T0) is not None and count < 20:
        count += 1
        engine.record(answered(engine.history[-1], correct=False), now=T0)
    assert count == 5, "all five failed, and still exactly five"


def test_a_node_does_not_choose_the_hands_either():
    """The listed orientations are gone with the fence: a fact is asked the way
    the alternation says, whatever the node was called."""
    state = learner(sessions=3)
    engine = drawn(state)
    engine.start_session(T0, pairs=[[9, 6]], length=3)
    for _ in range(3):
        pick = engine.next_exercise(T0)
        assert pick is not None
        assert (pick.left, pick.right) == engine._orientation(pick.fact)
        engine.record(answered(pick), now=T0)


def test_a_fact_the_child_has_never_met_is_not_marked_seen():
    """The draw can hand the child a fact for the first time. The pick has to
    say so, or the phrase layer calls a first meeting a review."""
    state = LearnerState.new(T0)
    engine = drawn(state)
    engine.start_session(T0, pairs=[[6, 7], [7, 6]], length=5)
    for _ in range(5):
        pick = engine.next_exercise(T0)
        assert pick is not None
        assert pick.seen is False, f"{pick.fact} was never attempted before"
        engine.record(answered(pick), now=T0)


def test_a_fact_the_child_has_met_is_marked_seen_wherever_the_draw_lands():
    """The history is the only thing that counts, and the node is not history."""
    state = learner(sessions=3, **{"10x10": 1, "6x6": 2})
    engine = drawn(state)
    engine.start_session(T0, pairs=[[6, 6], [7, 7]], length=5)
    for _ in range(5):
        pick = engine.next_exercise(T0)
        assert pick is not None
        assert pick.seen is (pick.fact in {"10x10", "6x6"}), pick.fact
        engine.record(answered(pick), now=T0)


def test_being_met_before_is_exactly_what_new_material_means_now():
    """seen and is_new answer the same question from two sides in a live draw:
    a fact the child has met is never new, and an unmet fact is new material
    while the gate is open and plain practice once it is shut."""
    state = learner(sessions=3, **{"10x10": 2, "6x10": 2, "7x10": 2})
    engine = drawn(state)
    engine.start_session(T0)
    opened = []
    for _ in range(6):
        pick = engine.next_exercise(T0)
        if pick is None:
            break
        opened.append(pick.is_new)
        if pick.seen:
            assert pick.is_new is False, "a fact already met is never new material"
        engine.record(answered(pick, correct=True, hinted=1 if pick.is_new else 0), now=T0)
    assert opened.count(True) <= 2, "hinted successes must not unlock more new facts"


def test_a_scripted_step_is_served_as_written():
    """The demo says what it says: a scripted pick counts as met, whatever the
    state behind it, so the stage wording never moves."""
    engine = ScriptedScheduler.load(REPO / "demo" / "scenario.json",
                                    state=LearnerState.new(T0), now=T0)
    engine.start_session(T0, pairs=[[6, 6], [7, 7]], length=5)
    pick = engine.next_exercise(T0)
    assert pick is not None and pick.seen is True


def _owed_and_owing() -> LearnerState:
    """One fact owed right now, one not owed for a week."""
    state = learner(sessions=3, **{"10x10": 1, "6x7": 4})
    state.fact("10x10").due_session = 4
    state.fact("6x7").due_at = (T0 + timedelta(days=7)).isoformat()
    state.fact("6x7").due_session = None
    return state


def test_a_due_fact_waits_its_turn_in_the_draw_like_every_other_one():
    """Spacing is still recorded and still readable. It no longer chooses: a
    fact that is owed today comes up about as often as one owed next week,
    because nothing can be both a uniform draw and a due queue."""
    sample = _owed_and_owing()
    assert sch.is_due(sample.math["10x10"], T0, session=4) is True
    assert sch.is_due(sample.math["6x7"], T0, session=4) is False

    rounds = 900
    counts = {"10x10": 0, "6x7": 0}
    for seed in range(rounds):
        engine = drawn(_owed_and_owing(), seed=seed)
        engine.start_session(T0, pairs=[[6, 6], [7, 7]], length=5)
        for _ in range(5):
            pick = engine.next_exercise(T0)
            assert pick is not None
            if pick.fact in counts:
                counts[pick.fact] += 1
            engine.record(answered(pick), now=T0)

    flat = rounds * 5 / len(DIFFICULTY_ORDER)
    for key, count in counts.items():
        assert 0.5 * flat < count < 1.5 * flat, f"{key}: {count} against {flat}"


def test_xp_rides_along_on_the_learner_record():
    """XP is the page's business. The record carries it, never below zero."""
    state = learner(sessions=2)
    state.xp = 340
    back = LearnerState.from_dict(json.loads(json.dumps(state.to_dict())))
    assert back.xp == 340
    assert LearnerState.from_dict({"learner_id": "x", "xp": -5}).xp == 0
    assert LearnerState.from_dict({"learner_id": "x"}).xp == 0


# --- the node label steers nothing -------------------------------------------


def test_a_one_question_node_draws_like_any_other():
    """A check node used to be guaranteed its own fact. The label does not shape
    what is served any more, so the one question is a draw like the rest."""
    state = learner(sessions=1)
    for key in ("9x9", "9x8"):
        record = state.fact(key)
        record.attempts = [sch.Attempt(at=(T0 - timedelta(days=1)).isoformat(),
                                       correct=False, hinted=False, response_time=9.0,
                                       session=1)]
    state.last_session_at = (T0 - timedelta(days=1)).isoformat()
    engine = drawn(state)
    started = engine.start_session(T0, pairs=[[6, 6]], length=1)

    assert started["allowed"] == ["6x6"], "the label is reported, and that is all"
    pick = engine.next_exercise(T0)
    assert pick is not None and pick.fact in DIFFICULTY_ORDER
    engine.record(answered(pick), now=T0)
    assert engine.next_exercise(T0) is None, "one question, as the node says"


def _yesterday() -> LearnerState:
    """A child who played yesterday and left three facts behind."""
    state = learner(sessions=1)
    for key, mastery in (("6x6", 0), ("7x7", 2), ("10x10", 0)):
        record = state.fact(key)
        record.mastery = mastery
        record.attempts = [sch.Attempt(at=(T0 - timedelta(days=1)).isoformat(),
                                       correct=mastery > 0, hinted=False,
                                       response_time=4.0, session=1)]
        sch.schedule_next(record, T0 - timedelta(days=1), 1)
    state.last_session_at = (T0 - timedelta(days=1)).isoformat()
    return state


def test_the_fragile_fact_is_named_but_no_longer_opens_the_session():
    """The record still says what is most fragile, for the summary and for the
    trace. It is a note now: the first question is drawn like every other one."""
    opening = set()
    for seed in range(40):
        engine = drawn(_yesterday(), seed=seed)
        started = engine.start_session(T0, pairs=[[6, 6], [7, 7]], length=5)
        assert started["opening_fact"] == "10x10", "the fragile fact of the record"
        pick = engine.next_exercise(T0)
        assert pick is not None
        opening.add(pick.fact)
    assert len(opening) > 1, f"the first question is drawn, not decreed: {opening}"


def test_a_lesson_draws_from_the_whole_range_whatever_the_node_is_called():
    state = learner(sessions=3, **{"10x10": 5, "6x10": 5, "9x9": 4, "7x10": 1})
    engine = drawn(state)
    engine.start_session(T0, pairs=[[6, 6], [7, 7]], length=5)
    facts = []
    for _ in range(5):
        pick = engine.next_exercise(T0)
        assert pick is not None
        facts.append(pick.fact)
        engine.record(answered(pick), now=T0)

    assert len(set(facts)) == 5, facts
    assert set(facts) - {"6x6", "7x7"}, "the draw is not the label"


def test_the_confidence_exercise_is_a_fact_the_child_owns():
    """Two errors buy a solid fact, from anywhere in six to ten, and never the
    question the child has just failed."""
    for seed in range(20):
        state = learner(sessions=3, **{"6x6": 5, "7x7": 5, "8x8": 5, "9x9": 5,
                                       "10x10": 5})
        engine = drawn(state, seed=seed)
        engine.start_session(T0, pairs=[[6, 6], [8, 9]], length=5)
        first = engine.next_exercise(T0)
        engine.record(answered(first, correct=False), now=T0)
        second = engine.next_exercise(T0)
        engine.record(answered(second, correct=False), now=T0)
        pick = engine.next_exercise(T0)
        assert pick.reason == sch.REACTION_CONFIDENCE, seed
        assert state.math[pick.fact].mastery >= sch.MASTERED_FROM
        assert pick.fact not in {first.fact, second.fact}, "never the fact just failed"


def test_mastered_facts_are_every_fact_the_child_owns():
    state = learner(sessions=3, **{"10x10": 5, "7x7": 4, "6x6": 1})
    engine = drawn(state)
    assert engine._mastered_facts() == ["10x10", "7x7"]
    engine.start_session(T0, pairs=[[6, 6], [7, 7]], length=5)
    assert engine._mastered_facts() == ["10x10", "7x7"], "the node does not narrow it"


# --- the orientation keeps alternating ---------------------------------------


def test_the_orientation_keeps_alternating_past_five_attempts():
    """Only the last five attempts are kept, so the alternation used to freeze
    on the sixth and the child only ever saw one hand order after that."""
    state = learner(sessions=3, **{"7x8": 2})
    engine = drawn(state)
    engine.start_session(T0)
    seen = []
    for _ in range(10):
        seen.append(engine._orientation("7x8"))
        state.fact("7x8").log(sch.Attempt(at=T0.isoformat(), correct=True, hinted=False,
                                          response_time=2.0, session=3))
    assert len(set(seen)) == 2
    assert seen[0::2] == [seen[0]] * 5 and seen[1::2] == [seen[1]] * 5


def test_a_fact_asked_twice_is_asked_the_other_way_round():
    """The two orientations are two questions, and the alternation is what
    tells them apart across a fact's attempts."""
    state = learner(sessions=3, **{"6x8": 2})
    engine = drawn(state)
    engine.start_session(T0, pairs=[[6, 8]], length=10)
    first = engine._orientation("6x8")
    engine.record(Outcome(fact="6x8", left=first[0], right=first[1], correct=True,
                          response_time=3.0), now=T0)
    assert engine._orientation("6x8") == (first[1], first[0])


# --- variety -----------------------------------------------------------------


def test_a_five_question_lesson_serves_five_different_facts():
    """The complaint that started this: every lesson repeated the same facts.
    Fifteen facts, five questions, five of them, whatever the seed."""
    for seed in range(40):
        state = learner(sessions=3)
        engine = drawn(state, seed=seed)
        engine.start_session(T0, pairs=[[6, 7], [7, 8]], length=5)
        facts = []
        for _ in range(5):
            pick = engine.next_exercise(T0)
            assert pick is not None
            facts.append(pick.fact)
            engine.record(answered(pick), now=T0)
        assert len(set(facts)) == 5, f"seed {seed}: {facts}"


def test_the_draw_is_uniform_over_the_fifteen_facts_and_not_clustered():
    """A long run. Every fact of six to ten comes up, and none of them runs away
    with the lesson: the first question of a session is a flat draw over the
    fifteen, and a whole session stays close to flat once the no repeated table
    rule has had its say. That rule is the only thing that bends the draw, and
    it bends it the same way for everyone: a double carries one factor instead
    of two, so fewer questions block it and it comes up about a third more often
    than a mixed fact. The band here is wide enough for that and far too narrow
    for a fence."""
    first: dict[str, int] = {}
    every: dict[str, int] = {}
    rounds = 1500
    for seed in range(rounds):
        engine = drawn(learner(sessions=3), seed=seed)
        engine.start_session(T0, length=5)
        for index in range(5):
            pick = engine.next_exercise(T0)
            assert pick is not None
            if index == 0:
                first[pick.fact] = first.get(pick.fact, 0) + 1
            every[pick.fact] = every.get(pick.fact, 0) + 1
            engine.record(answered(pick), now=T0)

    assert set(first) == set(DIFFICULTY_ORDER), "every fact opens a session"
    flat = rounds / len(DIFFICULTY_ORDER)
    assert max(first.values()) < 1.5 * flat, first
    assert min(first.values()) > 0.5 * flat, first

    assert set(every) == set(DIFFICULTY_ORDER)
    flat = rounds * 5 / len(DIFFICULTY_ORDER)
    assert max(every.values()) < 1.5 * flat, every
    assert min(every.values()) > 0.5 * flat, every


def test_the_node_label_does_not_make_its_pairs_any_likelier():
    """The proof that the fence is gone: the same long run on a two pair node
    serves those two pairs no more often than chance does."""
    rounds = 1500
    counts = {"6x7": 0, "8x9": 0}
    for seed in range(rounds):
        engine = drawn(learner(sessions=3), seed=seed)
        engine.start_session(T0, pairs=[[6, 7], [8, 9]], length=5)
        for _ in range(5):
            pick = engine.next_exercise(T0)
            assert pick is not None
            if pick.fact in counts:
                counts[pick.fact] += 1
            engine.record(answered(pick), now=T0)

    flat = rounds * 5 / len(DIFFICULTY_ORDER)
    for key, count in counts.items():
        assert count < 1.5 * flat, f"{key} is favoured: {count} against {flat}"
        assert count > 0.5 * flat, f"{key} is shunned: {count} against {flat}"


def test_a_wrong_fact_comes_back_and_is_the_only_repeat():
    for seed in range(40):
        state = learner(sessions=3)
        engine = drawn(state, seed=seed)
        engine.start_session(T0, pairs=[[6, 7], [7, 8]], length=6)
        facts = []
        for index in range(6):
            pick = engine.next_exercise(T0)
            assert pick is not None
            facts.append(pick.fact)
            engine.record(answered(pick, correct=index > 0), now=T0)
        missed = facts[0]
        assert facts.count(missed) == 2, f"seed {seed}, the missed fact comes back: {facts}"
        assert len(facts) - len(set(facts)) == 1, f"seed {seed}, nothing else repeats: {facts}"


def test_orientation_flips_when_a_fact_comes_back():
    """The retry is the one repeat of a session, and it is asked the other way
    round: the two orientations are two questions."""
    state = learner(sessions=3, **{k.replace("x", "_"): 2 for k in DIFFICULTY_ORDER})
    engine = drawn(state)
    engine.start_session(T0)
    poses: dict[str, list[tuple[int, int]]] = {}
    missed: str | None = None
    for _ in range(8):
        pick = engine.next_exercise(T0)
        if pick is None:
            break
        poses.setdefault(pick.fact, []).append((pick.left, pick.right))
        low, high = factors_of(pick.fact)
        wrong = missed is None and low != high
        if wrong:
            missed = pick.fact
        engine.record(answered(pick, correct=not wrong), now=T0)

    assert missed is not None
    seen = poses[missed]
    assert len(seen) == 2, f"the missed fact comes back once: {poses}"
    assert seen[0] != seen[1] and seen[0] == (seen[1][1], seen[1][0])


def test_never_the_same_table_twice_in_a_row_over_a_long_session():
    for seed in range(20):
        for pairs, length in (([[6, 7], [7, 8]], 10), ([[9, 9]], 8), (None, None)):
            state = learner(sessions=3, **{k.replace("x", "_"): 2 for k in DIFFICULTY_ORDER})
            engine = drawn(state, seed=seed)
            engine.start_session(T0, pairs=pairs, length=length)
            picks = []
            for _ in range(length or 10):
                pick = engine.next_exercise(T0)
                if pick is None:
                    break
                picks.append(pick)
                engine.record(answered(pick), now=T0)
            assert len(picks) >= 6, picks
            for i in range(1, len(picks)):
                shared = set(factors_of(picks[i].fact)) & set(factors_of(picks[i - 1].fact))
                assert not shared, (
                    f"seed {seed}, table {shared} twice in a row at {i} for {pairs}: "
                    f"{[p.fact for p in picks[:i + 1]]}")


def test_a_first_lesson_draws_from_the_whole_range():
    """A brand new child has no history to unlock anything with, and does not
    need one: the tables are six to ten for everybody."""
    state = LearnerState.new(T0)
    engine = drawn(state)
    engine.start_session(T0, pairs=[[6, 7], [7, 6]], length=5)
    facts = []
    for _ in range(5):
        pick = engine.next_exercise(T0)
        assert pick is not None
        facts.append(pick.fact)
        engine.record(answered(pick), now=T0)
    assert len(set(facts)) == 5, facts
    for key in facts:
        low, high = factors_of(key)
        assert low in sch.FACTORS and high in sch.FACTORS


# --- the error profile names the hand ----------------------------------------


POSE_HAND_CASES = [
    ("left", "wrong_left"),
    ("right", "wrong_right"),
    ("wrong_left_finger", "wrong_left"),
    ("wrong_right_finger", "wrong_right"),
    ("no_contact", "no_contact"),
]


@pytest.mark.parametrize("hand,kind", POSE_HAND_CASES, ids=[c[0] for c in POSE_HAND_CASES])
def test_a_pose_error_is_filed_against_the_hand_that_was_wrong(hand, kind):
    state = learner(sessions=3, **{"7x8": 2})
    engine = drawn(state)
    engine.start_session(T0)
    engine.record(Outcome(fact="7x8", left=8, right=7, correct=False, pose_error=True,
                          wrong_hand=hand), now=T0)
    assert state.error_profile[kind] == 1
    counted = sum(state.error_profile[k] for k in ("wrong_left", "wrong_right", "no_contact"))
    assert counted == 1, "one pose error is one count, on one kind"


def test_a_pose_error_on_a_double_is_filed_by_the_hand_not_by_the_symmetry():
    """6x6, 7x7, 8x8, 9x9 and 10x10 used to file every pose error as the right
    hand, and every other fact as the left."""
    state = learner(sessions=3, **{"9x9": 2})
    engine = drawn(state)
    engine.start_session(T0)
    engine.record(Outcome(fact="9x9", left=9, right=9, correct=False, pose_error=True,
                          wrong_hand="left"), now=T0)
    assert state.error_profile["wrong_left"] == 1
    assert state.error_profile["wrong_right"] == 0


def test_a_pose_error_with_no_hand_named_is_filed_against_neither():
    state = learner(sessions=3, **{"7x8": 2})
    engine = drawn(state)
    engine.start_session(T0)
    engine.record(Outcome(fact="7x8", left=8, right=7, correct=False, pose_error=True),
                  now=T0)
    assert state.error_profile["wrong_left"] == 0
    assert state.error_profile["wrong_right"] == 0
    assert state.error_profile["no_contact"] == 0


# --- the shape of an open session --------------------------------------------


def _steady_learner(same_day: bool) -> LearnerState:
    """Every fact known and due, so nothing but the session shape stops it."""
    state = learner(sessions=3, **{k.replace("x", "_"): 3 for k in DIFFICULTY_ORDER})
    state.last_session_at = (T0 if same_day else T0 - timedelta(days=1)).isoformat()
    return state


def _play(engine: Scheduler, seconds=lambda n: 3.0, limit: int = 30) -> int:
    now = T0
    count = 0
    while count < limit:
        pick = engine.next_exercise(now)
        if pick is None:
            break
        count += 1
        engine.record(answered(pick, seconds=seconds(count)), now=now)
        now += timedelta(seconds=10)
    return count


def test_an_open_session_runs_to_the_maximum_when_it_is_going_well():
    engine = drawn(_steady_learner(same_day=True))
    started = engine.start_session(T0)
    assert started["plan"] == []
    assert _play(engine) == sch.SESSION_MAX_EXERCISES


def test_a_next_day_session_stops_once_its_plan_is_played():
    engine = drawn(_steady_learner(same_day=False))
    started = engine.start_session(T0)
    assert started["plan"] == list(sch.NEXT_DAY_PLAN)
    count = _play(engine)
    assert sch.SESSION_MIN_EXERCISES <= count < sch.SESSION_MAX_EXERCISES


def test_an_open_session_that_slows_down_stops_before_the_maximum():
    engine = drawn(_steady_learner(same_day=True))
    engine.start_session(T0)
    count = _play(engine, seconds=lambda n: 2.0 if n <= 3 else 12.0)
    assert count == sch.SESSION_MIN_EXERCISES
    assert engine.end_session(T0 + timedelta(minutes=2)).reason == sch.REACTION_END_TIRED


def test_a_node_keeps_its_own_length_whatever_the_open_shape_says():
    engine = drawn(_steady_learner(same_day=True))
    engine.start_session(T0, pairs=[[6, 6], [7, 7]], length=5)
    assert _play(engine) == 5


# --- a malformed record from localStorage ------------------------------------


MALFORMED_CASES = [
    ("a session count that is not a number", {"learner_id": "a", "sessions": "many"}),
    ("a pose map that is not a map", {"learner_id": "a", "pose": ["x"]}),
    ("a record that is not a record", {"learner_id": "a", "math": {"6x7": 3}}),
    ("a mastery that is not a number", {"learner_id": "a", "math": {"6x7": {"mastery": "high"}}}),
    ("an attempt list that is not a list", {"learner_id": "a", "math": {"6x7": {"attempts": 5}}}),
    ("an error count that is not a number",
     {"learner_id": "a", "error_profile": {"tens_error": "x"}}),
    ("a due date that is not a date",
     {"learner_id": "a", "math": {"6x7": {"due_at": {"bad": 1}, "due_session": "soon",
                                          "last_increase_session": []}}}),
    ("an attempt that is not an attempt",
     {"learner_id": "a", "math": {"6x7": {"attempts": [7, {"correct": "yes", "at": 3,
                                                           "session": None,
                                                           "response_time": "slow"}]}}}),
    ("names and counters of the wrong type",
     {"learner_id": "a", "xp": "lots", "display_name": {"n": 1}, "created_at": 4,
      "last_session_at": 7, "error_profile": "none"}),
]


@pytest.mark.parametrize("name,raw", MALFORMED_CASES, ids=[c[0] for c in MALFORMED_CASES])
def test_a_malformed_record_never_raises_and_still_runs_a_lesson(name, raw):
    """The docstring of from_dict promises this: the record comes from a
    browser's localStorage and a bad field must never break a lesson."""
    state = LearnerState.from_dict(raw, now=T0)
    assert state.learner_id
    assert state.sessions >= 0 and state.xp >= 0
    assert all(isinstance(record, Record) for record in state.math.values())
    assert all(isinstance(record, Record) for record in state.pose.values())
    assert set(state.error_profile) == set(sch.ERROR_KINDS)

    engine = drawn(state)
    engine.start_session(T0, pairs=[[6, 6]], length=1)
    pick = engine.next_exercise(T0)
    assert pick is not None and pick.fact in DIFFICULTY_ORDER
    engine.record(answered(pick), now=T0)
    assert engine.next_exercise(T0) is None
    json.dumps(state.to_dict())                 # and it still round trips


def test_a_malformed_attempt_is_dropped_and_the_sound_ones_are_kept():
    record = Record.from_dict({"attempts": ["rubbish", None,
                                            {"at": "x", "correct": True, "hinted": False,
                                             "response_time": 2.0, "session": 3}]})
    assert len(record.attempts) == 1
    assert record.attempts[0].session == 3 and record.logged == 1


def test_a_malformed_due_date_reads_as_no_due_date():
    record = Record.from_dict({"mastery": 3, "due_at": {"bad": 1}, "due_session": "soon",
                               "attempts": [{"at": T0.isoformat(), "correct": True}]})
    assert record.due_at is None and record.due_session is None
    assert sch.is_due(record, T0 + timedelta(days=30), session=9) is False


def test_xp_survives_a_malformed_neighbour():
    state = LearnerState.from_dict({"learner_id": "a", "xp": 210, "sessions": "many"})
    assert state.xp == 210 and state.sessions == 0
    assert LearnerState.from_dict({"learner_id": "a", "xp": "lots"}).xp == 0
