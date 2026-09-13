"""lesson/engine.py, table driven. SPEC.md section 8.

The exercise under test is 8 x 7 throughout, the one from the demo script, so
the expected result is 56: 5 tens and 6 units.
"""

from __future__ import annotations

import pytest

from classifier.schema import GestureState
from lesson import engine as eng
from lesson.engine import Engine, Exercise, default_exercises

EXERCISE = Exercise(8, 7)


def g(left: int | None, right: int | None, contact: bool,
      method: str = "6-10") -> GestureState:
    return GestureState(method=method, left=left, right=right, contact=contact,
                        confidence=0.9)


def fresh() -> Engine:
    e = Engine([EXERCISE])
    e.start()
    return e


def settle(e: Engine, gesture: GestureState, t0: float = 0.0) -> object:
    """Hold one gesture past the debounce window and return the update."""
    assert e.observe(gesture, t0) is None, "an event must not fire instantly"
    return e.observe(gesture, t0 + eng.DEBOUNCE_S)


# name, gesture, expected state, expected event
CASES = [
    ("both hands absent", g(None, None, False, "unknown"), eng.STATE_WAITING_POSE, None),
    ("unknown method", g(8, 7, True, "unknown"), eng.STATE_WAITING_POSE, None),
    ("exact pose", g(8, 7, True), eng.STATE_CORRECT_POSE, eng.EVENT_CORRECT_POSE),
    ("order free pose", g(7, 8, True), eng.STATE_CORRECT_POSE, eng.EVENT_CORRECT_POSE),
    ("right numbers not touching", g(8, 7, False), eng.STATE_WRONG_POSE, eng.EVENT_NO_CONTACT),
    ("numbers on the other hands", g(7, 8, False), eng.STATE_WRONG_POSE, eng.EVENT_HANDS_SWAPPED),
    ("right finger off", g(8, 9, False), eng.STATE_WRONG_POSE, eng.EVENT_WRONG_RIGHT),
    ("left finger off", g(6, 7, False), eng.STATE_WRONG_POSE, eng.EVENT_WRONG_LEFT),
    ("both fingers off", g(6, 10, False), eng.STATE_WRONG_POSE, eng.EVENT_WRONG_LEFT),
    ("right finger off but touching", g(8, 9, True), eng.STATE_WRONG_POSE, eng.EVENT_WRONG_RIGHT),
]


@pytest.mark.parametrize("name,gesture,state,event", CASES, ids=[c[0] for c in CASES])
def test_one_gesture_one_state(name, gesture, state, event):
    update = settle(fresh(), gesture)
    assert update is not None, f"{name}: expected an update"
    assert update.state == state
    assert update.event == event


def test_nothing_fires_before_the_debounce_window():
    e = fresh()
    wrong = g(8, 9, False)
    assert e.observe(wrong, 0.0) is None
    assert e.observe(wrong, eng.DEBOUNCE_S - 0.01) is None
    assert e.observe(wrong, eng.DEBOUNCE_S) is not None


def test_a_single_flickering_frame_does_not_move_the_lesson():
    """The classifier drops one frame to unknown, the lesson must not react."""
    e = fresh()
    wrong = g(8, 9, False)
    settle(e, wrong)
    assert e.observe(g(None, None, False, "unknown"), 0.4) is None
    assert e.observe(wrong, 0.45) is None
    assert e.observe(wrong, 1.0) is None, "still the same committed condition"


def test_a_committed_condition_is_not_re_emitted():
    e = fresh()
    wrong = g(8, 9, False)
    assert settle(e, wrong) is not None
    assert e.observe(wrong, 5.0) is None


def test_wrong_pose_names_the_finger_to_move():
    update = settle(fresh(), g(8, 9, False))
    assert [f.as_dict() for f in update.wrong] == [{"hand": "right", "number": 9}]
    assert [f.as_dict() for f in update.match] == [{"hand": "left", "number": 8}]
    assert update.hint == {"hand": "right", "move_from": 9, "move_to": 7}


def test_a_new_wrong_pose_replaces_the_stale_correction():
    """8 x 7 held as (8, 9), then as (9, 7). Both are "wrong", but the right hand is
    fixed and the left one moved: the advice has to follow the pose on screen now,
    once it has settled, not the one that was named first."""
    e = fresh()
    first = settle(e, g(8, 9, False))
    assert first.event == eng.EVENT_WRONG_RIGHT
    assert first.hint == {"hand": "right", "move_from": 9, "move_to": 7}

    moved = g(9, 7, False)
    assert e.observe(moved, 1.0) is None, "the new pose has to settle first"
    assert e.observe(moved, 1.0 + eng.DEBOUNCE_S - 0.01) is None
    update = e.observe(moved, 1.0 + eng.DEBOUNCE_S)
    assert update is not None, "the stale correction was kept"
    assert update.state == eng.STATE_WRONG_POSE
    assert update.event == eng.EVENT_WRONG_LEFT
    assert update.hint == {"hand": "left", "move_from": 9, "move_to": 8}
    assert [f.as_dict() for f in update.wrong] == [{"hand": "left", "number": 9}]
    assert [f.as_dict() for f in update.match] == [{"hand": "right", "number": 7}]
    assert e.snapshot().hint == update.hint
    assert e.observe(moved, 5.0) is None, "and it is said once"


def test_one_frame_of_another_wrong_pose_keeps_the_correction():
    e = fresh()
    wrong = g(8, 9, False)
    settle(e, wrong)
    assert e.observe(g(9, 7, False), 0.4) is None
    assert e.observe(wrong, 0.45) is None
    assert e.observe(wrong, 1.0) is None, "the flicker never settled"
    assert e.snapshot().hint == {"hand": "right", "move_from": 9, "move_to": 7}


def test_the_same_wrong_finger_moved_elsewhere_is_named_again():
    """(8, 9) then (8, 10): same hand, but "from 9" is no longer what the child sees."""
    e = fresh()
    settle(e, g(8, 9, False))
    update = settle(e, g(8, 10, False), t0=1.0)
    assert update is not None
    assert update.event == eng.EVENT_WRONG_RIGHT
    assert update.hint == {"hand": "right", "move_from": 10, "move_to": 7}


def test_correct_pose_clears_the_wrong_fingers_and_shows_the_reasoning():
    update = settle(fresh(), g(8, 7, True))
    assert update.wrong == ()
    assert update.hint == {}
    assert update.reasoning == EXERCISE.reasoning_lines()
    assert len(update.reasoning) == 3


def test_correct_pose_latches_until_an_answer_or_the_next_exercise():
    e = fresh()
    settle(e, g(8, 7, True))
    assert e.latched
    # Hands drop, the lesson holds. SPEC.md section 9: locked until answer or n.
    assert e.observe(g(None, None, False, "unknown"), 10.0) is None
    assert e.observe(g(6, 6, False), 20.0) is None
    assert e.snapshot().state == eng.STATE_CORRECT_POSE


def test_waiting_answer_follows_correct_pose():
    e = fresh()
    settle(e, g(8, 7, True))
    assert e.waiting_answer().state == eng.STATE_WAITING_ANSWER


def test_the_answer_is_checked_only_once_the_pose_is_correct():
    e = fresh()
    assert e.check(56) is None, "no answer before the pose"
    settle(e, g(8, 7, True))
    assert e.check(None) is None, "enter on an empty field is ignored"
    update = e.check(56)
    assert update.state == eng.STATE_ANSWER_CORRECT
    assert update.event == eng.EVENT_ANSWER_CORRECT
    assert update.answer == 56


def test_a_wrong_answer_keeps_the_pose_and_asks_again():
    e = fresh()
    settle(e, g(8, 7, True))
    update = e.check(54)
    assert update.state == eng.STATE_ANSWER_WRONG
    assert update.event == eng.EVENT_ANSWER_WRONG
    assert update.answer is None
    # Still latched, so a second try is possible without redoing the pose.
    assert e.check(56).state == eng.STATE_ANSWER_CORRECT


def test_next_unlatches_and_moves_on():
    e = Engine([Exercise(8, 7), Exercise(6, 6)])
    e.start()
    settle(e, g(8, 7, True))
    e.check(56)
    update = e.next()
    assert update.state == eng.STATE_EXERCISE_SHOWN
    assert update.exercise == Exercise(6, 6)
    assert update.answer is None and update.reasoning == () and update.wrong == ()
    assert not e.latched


def test_next_wraps_around():
    e = Engine([Exercise(8, 7)])
    e.start()
    assert e.next().exercise == Exercise(8, 7)


def test_repeat_keeps_the_exercise_and_clears_the_state():
    e = fresh()
    settle(e, g(8, 7, True))
    update = e.repeat()
    assert update.exercise == EXERCISE
    assert update.state == eng.STATE_EXERCISE_SHOWN
    assert not e.latched


@pytest.mark.parametrize("a,b,tens,units,result", [
    (8, 7, 5, 6, 56),
    (6, 6, 2, 16, 36),
    (10, 10, 10, 0, 100),
    (9, 6, 5, 4, 54),
    (7, 7, 4, 9, 49),
])
def test_the_reasoning_arithmetic(a, b, tens, units, result):
    exercise = Exercise(a, b)
    assert (exercise.tens, exercise.units, exercise.result) == (tens, units, result)
    assert exercise.result == a * b
    assert str(result) in exercise.reasoning_lines()[2]


def test_the_default_lesson_is_every_pair_once():
    exercises = default_exercises()
    assert len(exercises) == 15
    pairs = {frozenset((e.a, e.b)) for e in exercises}
    assert len(pairs) == 15
    assert all(6 <= e.a <= 10 and 6 <= e.b <= 10 for e in exercises)


def test_an_empty_lesson_is_refused():
    with pytest.raises(ValueError):
        Engine([])
