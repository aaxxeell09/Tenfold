"""lesson/tally.py: the phrases that carry the demo when the model is slow or down."""

from __future__ import annotations

import json
from pathlib import Path

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
    # The canonical success line, with the numbers of the exercise in it.
    assert tally.phrase(eng.EVENT_ANSWER_CORRECT, ctx) == "Yes. 8 times 7 is 56."


# --- every word comes from lesson/tally_lines.json ---------------------------


def test_the_module_keeps_no_text_of_its_own():
    """Tally has one voice and one place to keep it."""
    lines = json.loads((Path(tally.__file__).with_name("tally_lines.json"))
                       .read_text(encoding="utf-8"))
    assert tally.LINES == {k: v for k, v in lines.items() if v.strip()}
    for event, key in tally.KEYS.items():
        assert key in lines, f"{event} points at {key}, which nobody wrote"
    for event, key in tally.DETAILED_KEYS.items():
        assert key in lines, f"{event} points at {key}, which nobody wrote"
    assert tally.FALLBACK_KEY in lines
    for text in list(tally.PHRASES.values()) + list(tally.DETAILED.values()):
        assert text in lines.values()


def test_the_pose_being_right_says_the_canonical_line_and_only_that():
    """The bug that started this: a second wording of pose_ready."""
    lines = json.loads((Path(tally.__file__).with_name("tally_lines.json"))
                       .read_text(encoding="utf-8"))
    assert tally.KEYS["correct_pose"] == "pose_ready"
    assert tally.phrase(eng.EVENT_CORRECT_POSE, CONTEXT) == lines["pose_ready"]
    assert "Now count the tens, then the ones." not in lines.values()


def test_a_line_file_that_cannot_be_read_leaves_tally_silent(monkeypatch):
    """Silence is recoverable. Words this module invented are not."""
    monkeypatch.setattr(tally, "LINES", {})
    monkeypatch.setattr(tally, "FALLBACK", "")
    assert tally.phrase(eng.EVENT_CORRECT_POSE, CONTEXT) == ""
    assert tally.phrase("review", {"exercise": "8 x 9", "seen": False}) == ""


# --- a fact the child has never met is not a review --------------------------

NEVER_MET = {"exercise": "8 x 9", "seen": False}
MET_BEFORE = {"exercise": "8 x 9", "seen": True}


def test_a_widened_fact_never_met_takes_the_neutral_launch_line():
    """A lesson fills its count from the whole range, so it can serve a fact for
    the first time. It is announced, not reviewed."""
    text = tally.phrase("review", NEVER_MET)
    assert text == "Show me 8 times 9 with your hands."
    assert "again" not in text and "met before" not in text


def test_a_fact_never_met_has_no_subtitle():
    assert tally.subtitle("review", NEVER_MET) is None
    assert tally.subtitle("retry", NEVER_MET) is None


def test_a_fact_met_before_keeps_its_review_wording_and_its_subtitle():
    assert tally.phrase("review", MET_BEFORE) == "Here is 8 x 9 again."
    assert tally.subtitle("review", MET_BEFORE) == "Here is 8 x 9 again."
    assert tally.phrase("retry", MET_BEFORE) == "Let us try 8 x 9 again. You were close."
    assert tally.subtitle("retry", MET_BEFORE) == "Let us try 8 x 9 again. You were close."


def test_a_context_that_says_nothing_keeps_the_wording_it_always_had():
    """Every caller that knows nothing about the history gets today's line."""
    assert tally.phrase("review", {"exercise": "8 x 9"}) == "Here is 8 x 9 again."
    assert tally.phrase("review") == tally.PHRASES["review"]


def test_the_node_own_new_fact_is_still_announced_as_new():
    """New material is taught inside the node and says so. Only the wording that
    claims a past meeting is withheld."""
    assert tally.phrase("next_new", NEVER_MET) == "A brand new one: 8 x 9. Ready?"
    assert tally.subtitle("next_new", NEVER_MET) == "A brand new one: 8 x 9. Ready?"
    assert tally.phrase("level_up", NEVER_MET) == tally.PHRASES["level_up"]


def test_the_launch_line_is_read_from_the_shared_table():
    """It is not copied into this file: one sentence, one home."""
    assert tally.LAUNCH == "Show me {a} times {b} with your hands."


def test_the_operands_are_found_however_the_caller_names_them():
    for ctx in ({"a": 8, "b": 9}, {"left": 8, "right": 9}, {"operands": (8, 9)},
                {"exercise": "8 x 9"}):
        assert tally.phrase("review", {**ctx, "seen": False}) == \
            "Show me 8 times 9 with your hands."


def test_a_never_met_fact_with_no_numbers_falls_back_to_a_neutral_line():
    """No launch line can be built without the two factors, and a placeholder
    must never reach the child, so a neutral line already in the table is used."""
    text = tally.phrase("review", {"seen": False})
    assert text == tally.PHRASES["exercise_shown"]
    assert "{" not in text and "met before" not in text


def test_only_a_pick_moment_can_carry_a_subtitle():
    assert tally.subtitle("answer_correct", MET_BEFORE) is None
    assert tally.subtitle("", MET_BEFORE) is None
