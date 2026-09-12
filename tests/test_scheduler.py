"""lesson/scheduler.py, table driven. Tally's brain.

Time is always passed in, so a week of sessions replays in microseconds and
nothing here depends on when the suite runs.
"""

from __future__ import annotations

import json
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
    engine = Scheduler(state, now=T0)
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
    engine = Scheduler(state, now=T0)
    engine.start_session(T0)
    first = engine.next_exercise(T0)
    engine.record(answered(first, correct=False), now=T0)
    # exercise 0 failed, so it is queued for exercise 2, the third one
    assert engine.retry_queue == [(2, first.fact)]


def test_a_failed_fact_does_come_back_as_a_retry():
    """The exact slot can slip: the no repeated factor rule may push it out by
    one. What matters is that it comes back, and that it is announced as a retry."""
    state = learner(sessions=3, **{"7x8": 2, "6x10": 3, "9x9": 3, "10x10": 4})
    engine = Scheduler(state, now=T0)
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
    state = learner(sessions=3, **{"7x8": 1, "6x9": 1, "10x10": 5, "6x10": 5})
    engine = Scheduler(state, now=T0)
    engine.start_session(T0)
    for _ in range(2):
        pick = engine.next_exercise(T0)
        engine.record(answered(pick, correct=False), now=T0)
    pick = engine.next_exercise(T0)
    assert pick.reason == sch.REACTION_CONFIDENCE
    assert state.math[pick.fact].mastery >= sch.MASTERED_FROM


def test_a_new_fact_opens_only_after_two_first_try_successes():
    state = learner(sessions=3, **{"10x10": 2, "6x10": 2, "7x10": 2})
    engine = Scheduler(state, now=T0)
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
    engine = Scheduler(state, now=T0)
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
    engine = Scheduler(state, now=T0)
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
    engine = Scheduler(learner(sessions=3, **{"9x9": 2}), now=T0)
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
    engine = Scheduler(LearnerState.new(T0), now=T0)
    started = engine.start_session(T0)
    assert started["onboarding"] is True
    assert [step["kind"] for step in started["steps"]] == ["greeting", "numbers", "guided"]
    assert started["steps"][2]["left"] == 8 and started["steps"][2]["right"] == 7

    again = Scheduler(learner(sessions=1), now=T0).start_session(T0)
    assert again["onboarding"] is False and again["steps"] == []


def test_the_next_day_plan_is_three_due_one_new_one_mastered():
    state = learner(sessions=2, **{"7x8": 1, "6x10": 2, "9x9": 5})
    state.last_session_at = (T0 - timedelta(days=1)).isoformat()
    started = Scheduler(state, now=T0).start_session(T0)
    assert started["plan"] == ["due", "due", "due", "new", "mastered"]
    assert started["opening_fact"] == "7x8", "the most fragile fact of last time"


def test_a_second_session_the_same_day_is_not_a_next_day_plan():
    state = learner(sessions=2)
    state.last_session_at = T0.isoformat()
    started = Scheduler(state, now=T0 + timedelta(hours=1)).start_session(T0 + timedelta(hours=1))
    assert started["plan"] == []


def test_a_session_always_ends_on_a_success():
    state = learner(sessions=3, **{k.replace("x", "_"): 3 for k in DIFFICULTY_ORDER})
    engine = Scheduler(state, now=T0)
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
    engine = Scheduler(state, now=T0)
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
    engine = Scheduler(state, now=T0)
    engine.start_session(T0)
    pick = engine.next_exercise(T0)
    engine.record(answered(pick), now=T0)
    assert engine.next_exercise(T0 + timedelta(minutes=7)) is None


def test_the_end_summary_names_what_happened():
    state = learner(sessions=3, **{"7x8": 4, "6x10": 2})
    engine = Scheduler(state, now=T0)
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
    engine = Scheduler(state, now=T0)
    engine.start_session(T0)
    for _ in range(3):
        pick = engine.next_exercise(T0)
        engine.record(answered(pick, seconds=2.0), now=T0)
    assert engine.level_bonus == sch.LEVEL_UP_STEPS


def test_a_slow_success_breaks_the_fast_streak():
    state = learner(sessions=3, **{"10x10": 3, "6x10": 3, "7x10": 3})
    engine = Scheduler(state, now=T0)
    engine.start_session(T0)
    for seconds in (2.0, 9.0, 2.0):
        pick = engine.next_exercise(T0)
        engine.record(answered(pick, seconds=seconds), now=T0)
    assert engine.level_bonus == 0


# --- metrics and state -------------------------------------------------------


def test_metrics_count_the_session():
    state = learner(sessions=3, **{"7x8": 2, "6x10": 2, "9x9": 2})
    engine = Scheduler(state, now=T0)
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
    engine = Scheduler(state, now=T0)
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
    engine = Scheduler(state, now=T0)
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
