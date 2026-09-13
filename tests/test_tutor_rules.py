"""lesson/tutor_rules.py: the shared scorer. Every reply below is synthetic, written for these tests, never a real
model output or a real child's session."""

from lesson import tally
from lesson import tutor_rules as tr

RIGHT_HINT = {"hand": "right", "move_from": 9, "move_to": 7}
CORRECTION = {"exercise": "7 x 7", "hint": dict(RIGHT_HINT), "answer": None}


def status(event, context, text, rule, latency_ms=400):
    return tr.score_intervention(event, context, text, latency_ms)[rule]


# ---------- correction_consistency ----------

def test_a_correct_correction_passes():
    for text in ("So close! Slide your right finger from nine to seven.",
                 tally.phrase("wrong_right_finger", CORRECTION)):
        s = status("wrong_right_finger", CORRECTION, text, "correction_consistency")
        assert s["status"] == tr.PASS and s["passed"] is True, (text, s)


def test_the_wrong_hand_with_the_same_numbers_fails():
    text = "Almost. Your right hand is good. Move your left finger from 9 to 7."
    s = status("wrong_right_finger", CORRECTION, text, "correction_consistency")
    assert s["status"] == tr.FAIL and "left hand" in s["reason"]


def test_start_and_target_swapped_fails():
    s = status("wrong_right_finger", CORRECTION, "Almost. Move your right finger from 7 to 9.", "correction_consistency")
    assert s["status"] == tr.FAIL and "swapped" in s["reason"]


def test_another_target_fails():
    s = status("wrong_right_finger", CORRECTION, "Move your right finger to 8.", "correction_consistency")
    assert s["status"] == tr.FAIL and "expected 7" in s["reason"]


def test_a_reply_that_cannot_be_read_is_not_verifiable_not_a_pass():
    for text in ("Slide it over a little, you are close.", "Right finger: 9, then 7.", "Move a finger to 7."):
        s = status("wrong_right_finger", CORRECTION, text, "correction_consistency")
        assert s["status"] == tr.NOT_VERIFIABLE and s["passed"] is None, (text, s)


def test_tallys_detailed_hint_lines_pass():
    hint_ctx = {"exercise": "7 x 7", "hint": dict(RIGHT_HINT)}
    for event in ("hint_1", "same_hand_twice"):
        line = tally.phrase(event, hint_ctx)
        assert status(event, hint_ctx, line, "correction_consistency")["status"] == tr.PASS, line


def test_insufficient_context_and_rules_that_do_not_apply():
    no_hint = {"exercise": "7 x 7", "hint": None}
    s = status("wrong_right_finger", no_hint, "Move your right finger to 7.", "correction_consistency")
    assert s["status"] == tr.NOT_VERIFIABLE and "target" in s["reason"]
    s = status("no_contact", no_hint, "Touch the two fingers together.", "correction_consistency")
    assert s["status"] == tr.NOT_APPLICABLE and s["passed"] is None
    s = status("wrong_right_finger", {"hint": {"hand": "left", "move_from": 9, "move_to": 7}}, "Move to 7.",
               "correction_consistency")
    assert s["status"] == tr.NOT_VERIFIABLE and "disagrees" in s["reason"]


# ---------- no_spoiler ----------

def test_the_result_in_digits_fails():
    s = status("correct_pose", {"exercise": "7 x 8"}, "Great pose, that makes 56!", "no_spoiler")
    assert s["status"] == tr.FAIL and "56" in s["reason"]


def test_the_result_in_words_fails():
    for text in ("Nice, fifty six is right there!", "Nice, fifty-six is right there!", "Fiftysix, count it."):
        assert status("correct_pose", {"exercise": "7 x 8"}, text, "no_spoiler")["status"] == tr.FAIL, text


def test_the_result_in_tens_and_units_fails():
    s = status("correct_pose", {"exercise": "7 x 8"}, "You have 5 tens and 6 units.", "no_spoiler")
    assert s["status"] == tr.FAIL and "tens" in s["reason"]


def test_other_numbers_and_the_factors_pass():
    text = "Seven times eight: count the fingers at the bottom, then the top."
    assert status("correct_pose", {"exercise": "7 x 8"}, text, "no_spoiler")["status"] == tr.PASS


def test_the_result_is_allowed_once_the_answer_is_validated():
    s = status("answer_correct", {"exercise": "7 x 8", "answer": 56}, "Yes, seven times eight is 56!", "no_spoiler")
    assert s["status"] == tr.NOT_APPLICABLE and s["passed"] is None


def test_no_exercise_or_an_unreadable_one():
    assert status("intro", {"exercise": ""}, "Hi, show me your hands.", "no_spoiler")["status"] == tr.NOT_APPLICABLE
    assert status("correct_pose", {"exercise": "seven by eight"}, "Count.", "no_spoiler")["status"] == tr.NOT_VERIFIABLE


# ---------- length, emptiness, words, timing ----------

def test_an_empty_reply():
    scores = tr.score_intervention("no_contact", {"exercise": "7 x 8"}, "", None)
    assert scores["non_empty"]["status"] == tr.FAIL and scores["non_empty"]["reason"]
    assert scores["concise"]["status"] == tr.NOT_APPLICABLE and scores["no_spoiler"]["status"] == tr.NOT_APPLICABLE
    assert scores["in_time"]["status"] == tr.NOT_VERIFIABLE


def test_a_reply_over_the_limit():
    s = status("no_contact", {}, " ".join(["touch"] * 21), "concise")
    assert s["status"] == tr.FAIL and "21 words" in s["reason"]
    assert status("no_contact", {}, " ".join(["touch"] * 20), "concise")["status"] == tr.PASS


def test_safe_words():
    assert status("answer_wrong", {}, "No, that is wrong.", "safe_words")["status"] == tr.FAIL
    assert status("answer_wrong", {}, "Almost, count the bottom again.", "safe_words")["status"] == tr.PASS


def test_in_time_is_timing_only_and_latency_is_reported():
    fast = tr.score_intervention("no_contact", {}, "No, wrong.", 300, 1500)
    slow = tr.score_intervention("no_contact", {}, "Touch them together.", 2400, 1500)
    assert fast["in_time"]["status"] == tr.PASS and fast["safe_words"]["status"] == tr.FAIL
    assert slow["in_time"]["status"] == tr.FAIL and slow["latency_ms"] == 2400
    assert slow["scorer_version"] == tr.SCORER_VERSION


def test_every_rule_is_there_with_a_readable_reason():
    scores = tr.score_intervention("wrong_right_finger", CORRECTION, "Move your left finger to 9.", 100)
    for rule in tr.RULES:
        assert scores[rule]["status"] in (tr.PASS, tr.FAIL, tr.NOT_APPLICABLE, tr.NOT_VERIFIABLE)
        assert scores[rule]["reason"]
    assert len(str(scores)) < 1000   # a Weave feedback payload stays small


def test_score_output_reads_the_tutor_call_record():
    out = {"text": "Great pose, that makes 56!", "model": "m", "usage": None, "latency_ms": 700}
    scores = tr.score_output("correct_pose", {"exercise": "7 x 8"}, out, 1500)
    assert scores["no_spoiler"]["status"] == tr.FAIL and scores["latency_ms"] == 700
