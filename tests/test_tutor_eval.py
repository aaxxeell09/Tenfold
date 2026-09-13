"""eval/tutor_eval.py: the moments dataset and Tally's rules in code, with no network."""

from eval import tutor_eval as te
from lesson import tally


def test_moments_carry_tallys_line_for_their_context():
    rows = te.moments()
    assert len({r["id"] for r in rows}) == len(rows)
    for r in rows:
        assert r["line"] == tally.phrase(r["event"], r["context"])
    events = {r["event"] for r in rows}
    assert {"wrong_right_finger", "correct_pose", "answer_correct", "end_success"} <= events


def test_safe_words_and_short():
    assert te.check_safe_words("Almost! Move your right finger to 8.")
    assert not te.check_safe_words("No, that is wrong.")
    assert not te.check_safe_words("")
    assert te.check_short("Touch the two fingers tip to tip.")
    assert not te.check_short(" ".join(["word"] * 21))


def test_no_spoiler_before_the_answer_only():
    ctx = {"exercise": "7 x 8"}
    assert not te.check_no_spoiler("correct_pose", ctx, "Great, that makes 56!")
    assert te.check_no_spoiler("correct_pose", ctx, "Now count the tens, then the ones.")
    assert te.check_no_spoiler("answer_correct", {**ctx, "answer": 56}, "Yes, 56 is exactly right!")


def test_keeps_numbers_accepts_digits_or_words():
    line = "Almost. Your left hand is good. Move your right finger from 9 to 8."
    assert te.check_keeps_numbers(line, "So close! Slide your right finger from nine to eight.")
    assert not te.check_keeps_numbers(line, "So close! Move your right finger a little.")


def test_score_row_in_time_needs_a_line_and_speed():
    row = {"event": "no_contact", "context": {"exercise": "6 x 9"}, "line": tally.phrase("no_contact")}
    fast = te.score_row(row, {"text": "So close, let those fingertips touch.", "latency_ms": 400})
    slow = te.score_row(row, {"text": "So close, let those fingertips touch.", "latency_ms": 4000})
    empty = te.score_row(row, {"text": "", "latency_ms": 100})
    assert fast["in_time"] and not slow["in_time"] and not empty["in_time"] and not empty["short"]


def test_parse_judge_is_robust():
    assert te.parse_judge('<think>ok</think> {"warmth": 5, "clarity": 4}') == {"warmth": 5, "clarity": 4}
    assert te.parse_judge('{"warmth": 9, "clarity": "3"}') == {"warmth": None, "clarity": 3}
    assert te.parse_judge("no json here") == {"warmth": None, "clarity": None}


def test_summarize_averages_and_counts_tokens():
    rows = [{"event": "intro"}, {"event": "intro"}]
    outputs = [{"text": "a", "latency_ms": 100, "usage": {"total_tokens": 50}},
               {"text": "", "latency_ms": None, "usage": None, "error": "503"}]
    scores = [{"safe_words": True, "short": True, "no_spoiler": True, "keeps_numbers": True, "in_time": True},
              {"safe_words": False, "short": False, "no_spoiler": True, "keeps_numbers": False, "in_time": False}]
    judged = [{"warmth": 4, "clarity": 5}, {"warmth": None, "clarity": None}]
    s = te.summarize("m", rows, outputs, scores, judged)
    assert s["safe_words"] == 0.5 and s["no_spoiler"] == 1.0 and s["warmth"] == 4 and s["p50_ms"] == 100
    assert s["tokens"] == 50 and s["errors"] == 1
