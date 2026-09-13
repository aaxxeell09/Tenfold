"""make doctor checks W&B Inference with one tiny call, and never FAILs on it."""

import httpx

from tenfold import doctor


class Resp:
    def __init__(self, status):
        self.status_code = status


def test_no_key_warns(monkeypatch, capsys):
    monkeypatch.delenv("WANDB_API_KEY", raising=False)
    assert doctor.check_inference() is True
    assert capsys.readouterr().out.startswith("WARN")


def test_answer_passes_and_sends_the_project(monkeypatch, capsys):
    monkeypatch.setenv("WANDB_API_KEY", "k")
    monkeypatch.setenv("WANDB_INFERENCE_PROJECT", "team/tenfold")
    seen = {}

    def post(url, headers, timeout, json):
        seen.update(url=url, headers=headers, model=json["model"])
        return Resp(200)

    monkeypatch.setattr(httpx, "post", post)
    assert doctor.check_inference() is True
    out = capsys.readouterr().out
    assert out.startswith("PASS") and seen["headers"]["OpenAI-Project"] == "team/tenfold"
    assert seen["url"].endswith("/chat/completions")


def test_http_error_warns(monkeypatch, capsys):
    monkeypatch.setenv("WANDB_API_KEY", "k")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: Resp(401))
    assert doctor.check_inference() is True
    assert "HTTP 401" in capsys.readouterr().out
