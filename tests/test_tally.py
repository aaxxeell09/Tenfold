"""lesson/tally.py: the phrases that carry the demo when the model is slow or down."""

from __future__ import annotations

import pytest

from lesson import engine as eng
from lesson import tally

ENGINE_EVENTS = (
    eng.EVENT_WRONG_LEFT, eng.EVENT_WRONG_RIGHT, eng.EVENT_NO_CONTACT,
    eng.EVENT_HANDS_SWAPPED, eng.EVENT_CORRECT_POSE, eng.EVENT_ANSWER_CORRECT,
    eng.EVENT_ANSWER_WRONG,
)
SCREEN_STATES = ("intro", "exercise_shown", "waiting_pose", "one_hand", "unknown_gesture")
CONTEXT = {"answer": 56, "hint": {"hand": "right", "move_from": 9, "move_to": 7}}

BANNED = ("wrong", "incorrect", "no,", "bad", "failed", "error")


@pytest.mark.parametrize("event", ENGINE_EVENTS + SCREEN_STATES)
def test_every_moment_has_a_phrase(event):
    text = tally.phrase(event, CONTEXT)
    assert text and text != tally.FALLBACK, f"{event} falls through to the generic line"


@pytest.mark.parametrize("event", ENGINE_EVENTS + SCREEN_STATES)
def test_phrases_stay_under_twenty_words(event):
    assert len(tally.phrase(event, CONTEXT).split()) < 20


@pytest.mark.parametrize("event", ENGINE_EVENTS + SCREEN_STATES)
def test_tally_never_tells_the_child_they_are_wrong(event):
    text = tally.phrase(event, CONTEXT).lower()
    for word in BANNED:
        assert word not in text, f"{event} says {word!r}"


@pytest.mark.parametrize("event", (eng.EVENT_WRONG_LEFT, eng.EVENT_WRONG_RIGHT))
def test_a_bad_pose_says_almost_and_names_the_finger_to_move(event):
    text = tally.phrase(event, CONTEXT)
    assert text.startswith("Almost")
    assert "7" in text, "the finger to move to has to be named"


def test_the_short_phrasing_is_used_when_the_finger_numbers_are_missing():
    text = tally.phrase(eng.EVENT_WRONG_LEFT, {})
    assert text.startswith("Almost")
    assert "{" not in text, "an unfilled placeholder must never reach the screen"


def test_an_unknown_event_still_returns_something_warm():
    assert tally.phrase("something_new_from_the_critic") == tally.FALLBACK
    assert tally.phrase("") == tally.FALLBACK


def test_no_phrase_ever_comes_back_empty():
    for event in list(tally.all_events()) + ["nonsense", ""]:
        assert tally.phrase(event, CONTEXT).strip()


def test_the_signature_matches_the_tutor_fallback():
    """lesson/tutor.py calls fallback(event, ctx) with its own context keys."""
    ctx = {"exercise": "8 x 7", "detected": None, "child": "Axel",
           "operands": (8, 7), "answer": 56}
    assert tally.phrase(eng.EVENT_ANSWER_CORRECT, ctx) == "Yes. 56 is exactly right."
