"""lesson/tutor.py live scoring, against a fake Weave client: no key, no network, no paid call.

The fake stands in for weave.op, Op.call, Call.apply_scorer and Call.feedback.add, the SDK calls lesson/tutor.py
uses, so the tests can see which call each score and each delivery note lands on. Every moment and reply here is
synthetic, including the child's name.
"""
import inspect
import sys
import threading
import time

import pytest

from lesson import tally
from lesson import tutor as tmod
from lesson import tutor_rules as tr

HINT = {"hand": "right", "move_from": 9, "move_to": 7}
CORRECTION = {"hint": dict(HINT), "answer": None, "exercise": "7 x 7", "child": "Synthetic Kid",
              "detected": {"left": 7, "right": 9, "contact": True}}
POSE = {"hint": None, "answer": None, "exercise": "7 x 7", "child": "Synthetic Kid"}


class FakeFeedback:
    def __init__(self, board, call_id):
        self.board, self.call_id = board, call_id

    def add(self, feedback_type, payload=None, **kwargs):
        if self.board.down:
            raise ConnectionError("weave is down")
        self.board.notes.append((self.call_id, feedback_type, dict(payload or {})))


class FakeCall:
    def __init__(self, board, call_id, inputs, output, exception):
        self.board, self.id, self.inputs, self.output, self.exception = board, call_id, inputs, output, exception
        self.feedback = FakeFeedback(board, call_id)

    async def apply_scorer(self, scorer, additional_scorer_kwargs=None):
        if self.board.down:
            raise ConnectionError("weave is down")
        example = {**self.inputs, **(additional_scorer_kwargs or {})}
        names = [n for n in inspect.signature(scorer).parameters if n != "output" and n in example]
        result = scorer(output=self.output, **{n: example[n] for n in names})
        self.board.scores.append((self.id, result))
        return result


class FakeWeave:
    """Stands in for the weave module."""

    def __init__(self):
        self.calls, self.scores, self.notes, self.down = [], [], [], False

    def init(self, project):
        return None

    def op(self, name=None, postprocess_inputs=None, **kwargs):
        board = self

        def wrap(fn):
            if name != "tutor.call":
                return fn

            class Op:
                def __call__(self, *args):
                    return fn(*args)

                def call(self, event, context, model):
                    inputs = {"event": event, "context": context, "model": model}
                    inputs = postprocess_inputs(inputs) if postprocess_inputs else inputs
                    try:
                        output, exception = fn(event, context, model), None
                    except Exception as e:   # Op.call never raises, it keeps the error on the call
                        output, exception = None, repr(e)
                    call = FakeCall(board, f"call-{len(board.calls) + 1}", inputs, output, exception)
                    board.calls.append(call)
                    return output, call
            return Op()
        return wrap


@pytest.fixture
def board(monkeypatch):
    fake = FakeWeave()
    monkeypatch.setitem(sys.modules, "weave", fake)
    monkeypatch.setattr(tmod, "_WEAVE", {"tried": False, "op": None, "scorer": None})
    return fake


def reply(text, latency_ms=300):
    line = tmod.Line(text)
    line.model, line.latency_ms = "fake/model", latency_ms
    return line


def tutor(monkeypatch, replies, timeout=1.0, delay=0.0, seen=None):
    t = tmod.Tutor(fallback=tally.phrase, api_key="k", timeout=timeout, trace=True)

    def call(event, ctx):
        if seen is not None:
            seen.append(dict(ctx))
        time.sleep(delay)
        text = replies[event] if isinstance(replies, dict) else replies(event)
        return reply(text)
    monkeypatch.setattr(t, "_call", call)
    return t


def settle(t, limit=3.0):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        with t._lock:
            if not t._inflight:
                break
        time.sleep(0.005)
    assert t.scoring.wait(limit)


def statuses(board, call_id):
    return [p["status"] for cid, kind, p in board.notes if cid == call_id and kind == tmod.DELIVERY_FEEDBACK]


# ---------- the score lands on the call that produced the line ----------

def test_each_score_is_attached_to_the_call_that_generated_the_line(monkeypatch, board):
    t = tutor(monkeypatch, {"wrong_right_finger": "Almost. Move your right finger from 9 to 7.",
                            "correct_pose": "Great pose, that makes 49!"})
    t.phrase("wrong_right_finger", CORRECTION); settle(t)
    t.phrase("correct_pose", POSE); settle(t)
    served = t.phrase("wrong_right_finger", CORRECTION); settle(t)

    assert [c.id for c in board.calls] == ["call-1", "call-2"]
    scores = dict(board.scores)
    assert set(scores) == {"call-1", "call-2"}
    assert scores["call-1"]["correction_consistency"]["status"] == tr.PASS
    assert scores["call-2"]["no_spoiler"]["status"] == tr.FAIL
    assert scores["call-2"]["correction_consistency"]["status"] == tr.NOT_APPLICABLE
    for call in board.calls:
        assert scores[call.id] == tr.score_output(call.inputs["event"], call.inputs["context"], call.output, 1000)
        assert scores[call.id]["scorer_version"] == tr.SCORER_VERSION
    assert served == "Almost. Move your right finger from 9 to 7." and served.call is board.calls[0]


def test_weave_records_the_moment_and_the_correction_not_the_child(monkeypatch, board):
    seen = []
    t = tutor(monkeypatch, {"wrong_right_finger": "Slide your right finger to 7."}, seen=seen)
    t.phrase("wrong_right_finger", CORRECTION); settle(t)
    recorded = board.calls[0].inputs
    assert recorded == {"event": "wrong_right_finger", "model": t.model,
                        "context": {"exercise": "7 x 7", "hint": HINT}}
    assert "Synthetic Kid" not in repr(recorded) and "detected" not in repr(recorded)
    assert seen[0]["child"] == "Synthetic Kid"   # the model call itself is unchanged


# ---------- what happened to the line ----------

def test_a_served_line_is_marked_once_and_never_claimed_as_spoken(monkeypatch, board):
    t = tutor(monkeypatch, {"wrong_right_finger": "Slide your right finger from 9 to 7."})
    assert t.phrase("wrong_right_finger", CORRECTION) == tally.phrase("wrong_right_finger", CORRECTION)
    settle(t)
    for _ in range(3):
        assert t.phrase("wrong_right_finger", CORRECTION) == "Slide your right finger from 9 to 7."
    settle(t)
    assert statuses(board, "call-1") == ["handed_to_server"]
    note = board.notes[0][2]
    assert "not observed" in note["detail"] and note["source"] == "model"
    assert not any(s in tmod.DELIVERY_NOTES for s in ("spoken", "played", "delivered"))


def test_a_line_whose_moment_ended_is_marked_unserved(monkeypatch, board):
    t = tutor(monkeypatch, {"wrong_right_finger": "Slide your right finger to 7.",
                            "no_contact": "Let the fingertips touch."})
    t.phrase("wrong_right_finger", CORRECTION); settle(t)
    t.phrase("no_contact", POSE); settle(t)
    assert statuses(board, "call-1") == ["moment_ended_unserved"]


def test_a_late_line_is_marked_held_and_is_not_served_in_its_moment(monkeypatch, board):
    t = tutor(monkeypatch, {"wrong_right_finger": "Slide your right finger to 7."}, timeout=0.05, delay=0.2)
    fb = t.phrase("wrong_right_finger", CORRECTION); settle(t)
    assert t.phrase("wrong_right_finger", CORRECTION) == fb
    settle(t)
    assert statuses(board, "call-1") == ["late_held"] and dict(board.scores)["call-1"]["non_empty"]["passed"]


def test_a_failed_generation_is_marked_and_tallys_line_stays(monkeypatch, board):
    t = tmod.Tutor(fallback=tally.phrase, api_key="k", timeout=1.0, trace=True)

    def boom(event, ctx):
        raise RuntimeError("503")
    monkeypatch.setattr(t, "_call", boom)
    fb = t.phrase("wrong_right_finger", CORRECTION); settle(t)
    assert t.phrase("wrong_right_finger", CORRECTION) == fb == tally.phrase("wrong_right_finger", CORRECTION)
    assert board.calls[0].exception and board.scores == []
    assert statuses(board, "call-1") == ["generation_failed"] and t.stats["errors"] == 1


def test_an_empty_generation_is_scored_and_marked(monkeypatch, board):
    t = tutor(monkeypatch, {"no_contact": ""})
    fb = t.phrase("no_contact", POSE); settle(t)
    assert t.phrase("no_contact", POSE) == fb
    assert dict(board.scores)["call-1"]["non_empty"]["status"] == tr.FAIL
    assert statuses(board, "call-1") == ["generation_empty"]


def test_say_marks_the_callback_or_the_late_drop(monkeypatch, board):
    got = []
    done = threading.Event()

    def on_phrase(text, state_id, source):
        got.append(source)
        if source == "model":
            done.set()
    fast = tutor(monkeypatch, {"no_contact": "Let the fingertips touch."})
    fast.say("no_contact", "7 x 7", None, "Synthetic Kid", on_phrase)
    assert done.wait(2) and fast.scoring.wait(2)
    assert got == ["fallback", "model"] and statuses(board, "call-1") == ["handed_to_callback"]

    slow = tutor(monkeypatch, {"no_contact": "Let the fingertips touch."}, timeout=0.05, delay=0.2)
    slow.say("no_contact", "7 x 7", None, "Synthetic Kid", on_phrase)
    end = time.monotonic() + 2
    while slow.stats["late"] == 0 and time.monotonic() < end:
        time.sleep(0.01)
    assert slow.scoring.wait(2) and statuses(board, "call-2") == ["dropped_late"]


# ---------- Weave absent or down, and the bound ----------

def test_no_weave_installed_keeps_the_model_line_and_scores_nothing(monkeypatch):
    monkeypatch.setitem(sys.modules, "weave", None)   # import weave raises ImportError
    monkeypatch.setattr(tmod, "_WEAVE", {"tried": False, "op": None, "scorer": None})
    t = tutor(monkeypatch, {"no_contact": "Let the fingertips touch."})
    t.phrase("no_contact", POSE); settle(t)
    assert t.phrase("no_contact", POSE) == "Let the fingertips touch."
    assert t.scoring._thread is None and t.scoring.stats == {"scored": 0, "notes": 0, "dropped": 0, "errors": 0}


def test_weave_down_never_reaches_the_lesson(monkeypatch, board):
    board.down = True
    t = tutor(monkeypatch, {"no_contact": "Let the fingertips touch."})
    t.phrase("no_contact", POSE); settle(t)
    assert t.phrase("no_contact", POSE) == "Let the fingertips touch."
    settle(t)
    assert t.scoring.stats["errors"] == 2 and t.scoring.stats["scored"] == 0 and board.scores == []


def test_the_score_queue_is_bounded_and_never_blocks():
    q = tmod.ScoreQueue(maxsize=1)
    started, gate = threading.Event(), threading.Event()
    q.submit(lambda: (started.set(), gate.wait(2)))
    assert started.wait(1)
    t0 = time.monotonic()
    accepted = [q.submit(lambda: None) for _ in range(5)]
    assert time.monotonic() - t0 < 0.1
    assert accepted.count(True) == 1 and q.stats["dropped"] == 4
    gate.set()
    assert q.wait(2)
