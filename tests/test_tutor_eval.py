"""eval/tutor_eval.py: the moments dataset and Tally's rules in code, with no network. Replies are synthetic."""

from eval import tutor_eval as te
from lesson import tally
from lesson import tutor_rules as tr


def test_moments_carry_tallys_line_for_their_context():
    rows = te.moments()
    assert len(rows) == 40 and len({r["id"] for r in rows}) == len(rows)
    for r in rows:
        assert r["line"] == tally.phrase(r["event"], r["context"])
    events = {r["event"] for r in rows}
    assert {"wrong_right_finger", "correct_pose", "answer_correct", "end_success"} <= events


def test_tallys_own_lines_never_fail_the_shared_rules():
    for r in te.moments():
        scores = te.score_row(r, {"text": r["line"], "latency_ms": 0})
        failed = {k: scores[k]["reason"] for k in te.RULES if scores[k]["status"] == tr.FAIL}
        assert not failed, (r["id"], r["line"], failed)
        if r["event"] in ("wrong_right_finger", "wrong_left_finger", "hint_1"):
            assert scores["correction_consistency"]["status"] == tr.PASS, (r["id"], r["line"])


def test_score_row_is_the_shared_scorer_plus_keeps_numbers():
    row = {"event": "wrong_right_finger", "context": {"exercise": "7 x 8", "hint": {"hand": "right", "move_from": 9,
           "move_to": 8}}, "line": "Almost. Your left hand is good. Move your right finger from 9 to 8."}
    out = {"text": "So close! Slide your left finger from nine to eight.", "latency_ms": 400}
    scores = te.score_row(row, out)
    assert {k: v for k, v in scores.items() if k != "keeps_numbers"} == \
        tr.score_intervention(row["event"], row["context"], out["text"], 400, te.IN_TIME_MS)
    assert scores["keeps_numbers"]["status"] == tr.PASS            # v1 would have stopped here
    assert scores["correction_consistency"]["status"] == tr.FAIL   # the hand is swapped
    dropped = te.score_row(row, {"text": "So close! Move your right finger a little.", "latency_ms": 400})
    assert dropped["keeps_numbers"]["status"] == tr.FAIL and "9" in dropped["keeps_numbers"]["reason"]


def test_keeps_numbers_accepts_digits_or_words():
    line = "Almost. Your left hand is good. Move your right finger from 9 to 8."
    assert te.check_keeps_numbers(line, "So close! Slide your right finger from nine to eight.")
    assert not te.check_keeps_numbers(line, "So close! Move your right finger a little.")


def test_score_row_timing_is_apart_from_content():
    row = {"event": "no_contact", "context": {"exercise": "6 x 9"}, "line": tally.phrase("no_contact")}
    fast = te.score_row(row, {"text": "So close, let those fingertips touch.", "latency_ms": 400})
    slow = te.score_row(row, {"text": "So close, let those fingertips touch.", "latency_ms": 4000})
    empty = te.score_row(row, {"text": "", "latency_ms": 100})
    assert fast["in_time"]["passed"] and slow["in_time"]["passed"] is False and slow["concise"]["passed"]
    assert empty["non_empty"]["passed"] is False and empty["concise"]["status"] == tr.NOT_APPLICABLE


def test_parse_judge_is_robust():
    assert te.parse_judge('<think>ok</think> {"warmth": 5, "clarity": 4}') == {"warmth": 5, "clarity": 4}
    assert te.parse_judge('{"warmth": 9, "clarity": "3"}') == {"warmth": None, "clarity": 3}
    assert te.parse_judge("no json here") == {"warmth": None, "clarity": None}


def test_summarize_counts_only_pass_or_fail_and_reports_not_verifiable():
    rows = [{"event": "wrong_right_finger", "context": {"exercise": "7 x 8", "hint": {"hand": "right",
             "move_from": 9, "move_to": 8}}, "line": "Move your right finger from 9 to 8."}] * 3
    outputs = [{"text": "Move your right finger from 9 to 8.", "latency_ms": 100, "usage": {"total_tokens": 50}},
               {"text": "Slide it over a little.", "latency_ms": 200, "usage": {"total_tokens": 10}},
               {"text": "", "latency_ms": None, "usage": None, "error": "503"}]
    scores = [te.score_row(r, o) for r, o in zip(rows, outputs)]
    judged = [{"warmth": 4, "clarity": 5}, {"warmth": None, "clarity": None}, {"warmth": None, "clarity": None}]
    s = te.summarize("m", rows, outputs, scores, judged)
    assert s["correction_consistency"] == 1.0          # one pass; not_verifiable and not_applicable left out
    assert s["non_empty"] == round(2 / 3, 3) and s["not_verifiable"] >= 2
    assert s["warmth"] == 4 and s["p50_ms"] == 150 and s["tokens"] == 60 and s["errors"] == 1
    assert s["scorer_version"] == tr.SCORER_VERSION


def test_flatten_reads_the_v2_summary_paths():
    summary = {te.SCORER: {k: {"passed": {"true_count": 1, "true_fraction": 0.5}} for k in te.RULES},
               "child_judge": {"warmth": {"mean": 4.0}, "clarity": {"mean": 3.5}}, "model_latency": {"mean": 0.812}}
    row = te.flatten(summary)
    assert row["no_spoiler"] == 0.5 and row["warmth"] == 4.0 and row["mean_s"] == 0.81
    assert te.EVALUATION.endswith("-v2") and te.LEADERBOARD.endswith("-v2")
