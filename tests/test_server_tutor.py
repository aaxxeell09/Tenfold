"""Tally's lines come from W&B Inference only when the server's own main() finds a key; tests and keyless runs keep
Tally's fixed phrases and never touch the network."""
from app import server


def test_tally_keeps_fixed_phrases_without_a_key(monkeypatch):
    monkeypatch.setattr(server, "PHRASE", server.tally.phrase)
    monkeypatch.delenv("WANDB_API_KEY", raising=False)
    server.enable_tutor()
    assert server.PHRASE is server.tally.phrase


def test_tally_speaks_through_the_tutor_with_a_key(monkeypatch):
    monkeypatch.setattr(server, "PHRASE", server.tally.phrase)
    monkeypatch.setenv("WANDB_API_KEY", "test-key-never-sent")
    monkeypatch.setenv("TENFOLD_TRACE", "0")
    server.enable_tutor()
    tutor = getattr(server.PHRASE, "__self__", None)
    assert type(tutor).__name__ == "Tutor" and tutor.fallback is server.tally.phrase


def test_build_message_uses_the_wired_phrase(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "PHRASE", lambda moment, context=None: calls.append(moment) or "from the tutor")
    source = server.build_message.__code__.co_names
    assert "PHRASE" in source and "tally" not in source
