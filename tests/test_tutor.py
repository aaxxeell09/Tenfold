import time

from lesson import tutor as tmod


def test_fallback_first_then_model(monkeypatch):
    calls = []
    t = tmod.Tutor(api_key="k", timeout=1.0)
    monkeypatch.setattr(t, "_call", lambda event, ctx: "Almost, move your right finger to 8.")
    got = t.say("wrong_right_finger", "7 x 8", {"left": 7, "right": 9, "contact": True}, "Leo",
                lambda text, sid, src: calls.append((src, text, sid)), state_id=3, operands=(7, 8))
    assert got.startswith("Almost") and calls[0][0] == "fallback" and calls[0][2] == 3
    time.sleep(0.2)
    assert calls[-1] == ("model", "Almost, move your right finger to 8.", 3)
    # second call with the same context is served from cache, synchronously
    calls.clear()
    t.say("wrong_right_finger", "7 x 8", {"left": 7, "right": 9, "contact": True}, "Leo",
          lambda text, sid, src: calls.append(src), state_id=4, operands=(7, 8))
    assert calls == ["fallback", "model"] and t.stats["hits"] == 1


def test_model_failure_keeps_fallback(monkeypatch):
    calls = []
    t = tmod.Tutor(api_key="k", timeout=0.5)

    def boom(event, ctx):
        raise RuntimeError("503")
    monkeypatch.setattr(t, "_call", boom)
    t.say("no_contact", "6 x 9", None, "Leo", lambda text, sid, src: calls.append(src))
    time.sleep(0.2)
    assert calls == ["fallback"] and t.stats["errors"] == 1


def test_late_model_answer_is_cached_but_not_shown(monkeypatch):
    calls = []
    t = tmod.Tutor(api_key="k", timeout=0.05)

    def slow(event, ctx):
        time.sleep(0.15)
        return "late line"
    monkeypatch.setattr(t, "_call", slow)
    t.say("correct_pose", "8 x 8", None, "Leo", lambda text, sid, src: calls.append(src))
    time.sleep(0.4)
    assert calls == ["fallback"] and t.stats["late"] == 1 and any(v == "late line" for v in t.cache.values())


def test_disabled_without_key():
    t = tmod.Tutor(api_key=None)
    calls = []
    assert t.say("answer_correct", "7 x 8", None, "Leo", lambda text, sid, src: calls.append(src), answer=56) == "That's it, 56! Great job."
    assert calls == ["fallback"] and not t.enabled


# ---------- drop-in phrase(event, context), the server's integration path ----------

from lesson import tally  # noqa: E402


def _wait_idle(t, limit=2.0):
    end = time.monotonic() + limit
    while t._inflight and time.monotonic() < end:
        time.sleep(0.01)


def test_phrase_matches_tally_when_disabled():
    t = tmod.Tutor(fallback=tally.phrase, api_key=None)
    ctx = {"hint": {"move_from": 9, "move_to": 8}, "answer": None, "exercise": "7 x 8"}
    assert t.phrase("wrong_right_finger", ctx) == tally.phrase("wrong_right_finger", ctx)
    assert t.phrase("intro") == tally.phrase("intro")


def test_phrase_fallback_first_then_model_on_next_update(monkeypatch):
    calls = []
    t = tmod.Tutor(fallback=tally.phrase, api_key="k", timeout=1.0)
    monkeypatch.setattr(t, "_call", lambda event, ctx: calls.append(event) or "Almost! Slide your right finger from 9 to 8.")
    ctx = {"hint": {"move_from": 9, "move_to": 8}, "answer": None, "exercise": "7 x 8"}
    first = t.phrase("wrong_right_finger", ctx)
    assert first == tally.phrase("wrong_right_finger", ctx)
    t.phrase("wrong_right_finger", ctx)  # still in flight: no second request
    _wait_idle(t)
    assert t.phrase("wrong_right_finger", ctx) == "Almost! Slide your right finger from 9 to 8."
    assert calls == ["wrong_right_finger"] and t.stats["hits"] == 1


def test_phrase_new_moment_does_not_reuse_other_line(monkeypatch):
    t = tmod.Tutor(fallback=tally.phrase, api_key="k", timeout=1.0)
    monkeypatch.setattr(t, "_call", lambda event, ctx: f"line for {ctx['hint']['move_to']}")
    a = {"hint": {"move_from": 9, "move_to": 8}, "answer": None, "exercise": "7 x 8"}
    b = {"hint": {"move_from": 10, "move_to": 8}, "answer": None, "exercise": "7 x 8"}
    t.phrase("wrong_right_finger", a); _wait_idle(t)
    assert t.phrase("wrong_right_finger", b) == tally.phrase("wrong_right_finger", b)


def test_phrase_late_line_waits_for_next_episode(monkeypatch):
    t = tmod.Tutor(fallback=tally.phrase, api_key="k", timeout=0.05)

    def slow(event, ctx):
        time.sleep(0.15)
        return "late but lovely"
    monkeypatch.setattr(t, "_call", slow)
    ctx = {"hint": None, "answer": None, "exercise": "8 x 8"}
    fb = t.phrase("correct_pose", ctx)
    _wait_idle(t)
    assert t.phrase("correct_pose", ctx) == fb and t.stats["late"] == 1      # same moment: no mid-moment swap
    t.phrase("answer_wrong", {**ctx, "answer": 60})                              # the moment changes...
    assert t.phrase("correct_pose", ctx) == "late but lovely"                    # ...and comes back: cached line shows


def test_phrase_model_error_keeps_fallback(monkeypatch):
    t = tmod.Tutor(fallback=tally.phrase, api_key="k", timeout=0.5)

    def boom(event, ctx):
        raise RuntimeError("503")
    monkeypatch.setattr(t, "_call", boom)
    ctx = {"hint": None, "answer": None, "exercise": "6 x 9"}
    fb = t.phrase("no_contact", ctx); _wait_idle(t)
    assert t.phrase("no_contact", ctx) == fb and t.stats["errors"] == 1


def test_injected_key_never_traces():
    assert tmod.Tutor(api_key="k").trace is False
