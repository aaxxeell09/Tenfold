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
