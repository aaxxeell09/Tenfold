"""eval/tutor_quality.py turns Weave's own records into the dashboard's tutor quality file without inventing anything:
no lesson origin without a marker, no score without feedback, no quality rate that mixes in timing or cancelled lines,
and a Weave that cannot be read keeps the previous file. Every example here is synthetic."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from eval import tutor_quality as tq  # noqa: E402

NOW = datetime(2026, 9, 13, 20, 0, tzinfo=timezone.utc)


def at(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def score(**statuses) -> dict:
    output = {"scorer_version": "tutor-rules-v2", "latency_ms": 400}
    for rule, status in statuses.items():
        output[rule] = {"status": status, "passed": {"pass": True, "fail": False}.get(status), "reason": f"{rule} {status}"}
    return {"type": "wandb.runnable.tutor_rules_v2", "payload": {"output": output}, "created_at": None}


def record(call_id: str, minutes_ago: float, feedback=(), attributes=None, event="wrong_right_finger") -> dict:
    return {"id": call_id, "started_at": at(minutes_ago), "display_name": None, "attributes": attributes or {},
            "inputs": {"event": event, "context": {"exercise": "7 x 8", "hint": {"hand": "right", "move_from": 9, "move_to": 8}},
                       "model": "Qwen/Qwen3-235B-A22B-Instruct-2507"},
            "output": {"text": "Move your right finger from 9 to 8.", "latency_ms": 420, "model": "Qwen/Qwen3-235B-A22B-Instruct-2507"},
            "exception": False, "url": f"https://wandb.ai/x/tenfold/r/call/{call_id}", "feedback": list(feedback)}


def test_an_evaluation_run_keeps_weave_fractions_and_names_its_rules_version():
    v2 = tq.evaluation_run("tally-voice-v2 Qwen3-235B-A22B-Instruct-2507", "2026-09-13T19:16:06+00:00", "u",
                           {"tally_rules_v2": {"latency_ms": {"mean": 542.7},
                                               "no_spoiler": {"passed": {"true_count": 33, "true_fraction": 1.0}},
                                               "non_empty": {"passed": {"true_count": 0, "true_fraction": 0.0}}},
                            "child_judge": {"warmth": {"mean": 3.85}, "clarity": {"mean": 4.125}}})
    assert v2["evaluation"] == "tally-voice-v2" and v2["model"] == "Qwen3-235B-A22B-Instruct-2507"
    assert v2["rules_version"] == "v2" and v2["latency_ms_mean"] == 542.7 and v2["warmth"] == 3.85
    assert v2["rules"]["no_spoiler"] == {"pass_fraction": 1.0, "pass_count": 33, "decided": 33}
    assert v2["rules"]["non_empty"]["decided"] is None  # a zero fraction cannot say how many were decided
    v1 = tq.evaluation_run("tally-voice gpt-oss-120b", None, None, {"tally_rules": {"short": {"true_count": 0, "true_fraction": 0.0}}})
    assert v1["rules_version"] == "v1" and "short" in v1["rules"]
    assert tq.evaluation_run("tenfold-train-v3", None, None, {"x": 1}) is None


def test_the_offline_block_shows_the_newest_evaluation_and_names_older_ones_apart():
    runs = [tq.evaluation_run("tally-voice A", "2026-09-13T13:00:00+00:00", "a1", {"tally_rules": {}}),
            tq.evaluation_run("tally-voice-v2 A", "2026-09-13T19:00:00+00:00", "a2", {"tally_rules_v2": {}}),
            tq.evaluation_run("tally-voice-v2 B", "2026-09-13T19:01:00+00:00", "b2", {"tally_rules_v2": {}})]
    block = tq.offline_block(runs, 40, {"tally-voice-v2": "https://board"})
    assert block["evaluation"] == "tally-voice-v2" and [r["model"] for r in block["runs"]] == ["A", "B"]
    assert block["rules_versions"] == ["v2"] and block["moments"] == 40 and block["leaderboard_url"] == "https://board"
    assert block["other_versions"] == [{"evaluation": "tally-voice", "rules_version": "v1", "runs": 1}]
    assert tq.offline_block([], None, {})["status"] == "unavailable"


def test_a_line_is_never_given_a_lesson_origin_or_a_score_it_does_not_have():
    first = NOW - timedelta(minutes=60)
    synthetic = tq.intervention_row(record("s", 50, [score(non_empty="pass")], attributes={"nimble_check": True}), first, NOW)
    unknown = tq.intervention_row(record("u", 50, [score(non_empty="pass")]), first, NOW)
    assert synthetic["origin"] == "synthetic_check" and unknown["origin"] == "not_identified"
    assert tq.intervention_row(record("p", 3), first, NOW)["state"] == "pending"
    assert tq.intervention_row(record("m", 30), first, NOW)["state"] == "score_missing"
    before = tq.intervention_row(record("b", 120), first, NOW)
    assert before["state"] == "before_scoring" and before["checks"] == {}
    delivered = tq.intervention_row(record("d", 50, [score(correction_consistency="not_verifiable"),
                                                     {"type": "tutor_delivery", "payload": {"status": "late_held"}, "created_at": "1"},
                                                     {"type": "tutor_delivery", "payload": {"status": "moment_ended_unserved"}, "created_at": "2"}]),
                                    first, NOW)
    assert delivered["delivery"] == ["late_held", "moment_ended_unserved"] and delivered["rules_version"] == "v2"
    assert delivered["checks"]["correction_consistency"]["status"] == "not_verifiable"


def test_the_lesson_summary_counts_only_marked_lessons_not_cancelled_and_leaves_timing_out(monkeypatch):
    monkeypatch.setattr(tq, "origin_of", lambda r: "lesson" if r["id"].startswith("lesson") else "not_identified")
    records = [record("lesson-ok", 20, [score(non_empty="pass", no_spoiler="fail", in_time="fail", correction_consistency="not_applicable")]),
               record("lesson-cancelled", 15, [score(non_empty="fail"), {"type": "tutor_delivery", "payload": {"status": "dropped_late"}, "created_at": None}]),
               record("other", 10, [score(non_empty="fail")]),
               record("old", 300)]
    block = tq.live_block(records, NOW, False)
    s = block["lesson_summary"]
    assert s["lines"] == 1 and s["cancelled_excluded"] == 1
    assert (s["checks_passed"], s["checks_decided"]) == (1, 2)  # in_time and not_applicable are not in the rate
    assert s["top_failures"] == [{"reason": "no_spoiler: no_spoiler fail", "count": 1}] and s["median_latency_ms"] == 420
    assert block["states"] == {"scored": 3, "before_scoring": 1} and all(r["state"] != "before_scoring" for r in block["rows"])


def test_a_weave_that_cannot_be_read_keeps_the_previous_file_and_says_so():
    previous = {"generated_at": "2026-09-13T19:00:00+00:00", "offline": {"status": "ok"}, "live": {"status": "ok", "rows": []}}

    def down():
        raise ConnectionError("no network")

    kept = tq.build(down, previous, NOW)
    assert kept["generated_at"] == previous["generated_at"] and kept["last_refresh_error"]["error"] == "ConnectionError"
    fresh = tq.build(down, None, NOW)
    assert fresh["offline"]["status"] == "unavailable" and fresh["live"]["status"] == "unavailable"


def test_the_file_holds_no_key_and_only_the_fields_the_trace_records(monkeypatch):
    monkeypatch.setenv("WANDB_API_KEY", "secret-should-never-appear")
    data = tq.build(lambda: {"weave_url": "https://wandb.ai/x/tenfold/weave", "calls": [record("c", 5, [score(non_empty="pass")])],
                             "evaluations": [], "moments": None, "leaderboards": {}}, None, NOW)
    text = json.dumps(data)
    assert "secret-should-never-appear" not in text and "WANDB" not in text
    assert set(data["live"]["rows"][0]) == {"id", "started_at", "origin", "state", "event", "exercise", "hint", "answer", "model",
                                            "text", "text_redacted", "latency_ms", "exception", "rules_version", "checks", "delivery", "url"}


def test_a_name_the_model_may_say_back_is_hidden_before_the_file_is_written():
    assert tq.redact_names("Great job, Leo!") == ("Great job, [name]!", True)
    assert tq.redact_names("Mia, move your right finger from 9 to 8.") == ("[name], move your right finger from 9 to 8.", True)
    assert tq.redact_names("That's it, Sam. Count the tens, then the ones.") == ("That's it, [name]. Count the tens, then the ones.", True)
    for kept in ("Almost. Your left hand is good. Move your right finger from 9 to 8.", "Tally says: Here's a new one: 8 times 6. Ready?",
                 "Yes! Show me both hands, palms facing me.", "That's right! Count the tens, then the ones."):
        assert tq.redact_names(kept) == (kept, False)
    row = tq.intervention_row(record("n", 5, [score(non_empty="pass")]) | {"output": {"text": "Well done, Zoe!", "latency_ms": 300}},
                              NOW - timedelta(minutes=60), NOW)
    assert row["text"] == "Well done, [name]!" and row["text_redacted"] is True
