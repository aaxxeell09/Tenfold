"""Tally's voice: W&B Inference on events only, in a thread, fallback shown first, cache by context.

    tutor = Tutor(fallback=tally.phrase)           # tally.phrase(event, ctx) -> str is Axel's
    tutor.say(event, exercise, detected, child, on_phrase, state_id)

`on_phrase(text, state_id, source)` is called at once with the fallback ("fallback"), then again with the model's
line ("model") if it arrives within `timeout` seconds. The UI ignores a phrase whose state_id is stale.
Never raises; never blocks the frame loop. Traced by Weave when a key is set.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Optional

import httpx

DEFAULT_BASE_URL = "https://api.inference.wandb.ai/v1"
DEFAULT_MODEL = "Qwen/Qwen3-235B-A22B-Instruct-2507"

DEFAULT_FALLBACKS: dict[str, str] = {
    "intro": "Hi {child}! Show me both hands, palms to the camera.",
    "exercise_shown": "Let's do {exercise}. Touch the two fingers together.",
    "wrong_left_finger": "Almost. Your right hand is good. Move your left finger to {left}.",
    "wrong_right_finger": "Almost. Your left hand is good. Move your right finger to {right}.",
    "no_contact": "Touch the two fingers together, tip to tip.",
    "hands_swapped": "Swap your hands and try again.",
    "one_hand": "I see one hand. Show me the other one too.",
    "correct_pose": "Yes! Now count with me.",
    "answer_correct": "That's it, {answer}! Great job.",
    "answer_wrong": "Almost. Count the tens again.",
}

SYSTEM = ("You are Tally, a warm math tutor for a 7 year old learning finger multiplication (fingers numbered 6 to 10, "
          "touch two fingertips, tens below, units above). Reply with ONE sentence under 20 words, in English, "
          "encouraging, never the word 'wrong'. Say 'almost' and name the finger to move when the pose is wrong. "
          "Never reveal the answer before the child types it.")


def default_fallback(event: str, ctx: dict[str, Any]) -> str:
    tpl = DEFAULT_FALLBACKS.get(event, "Let's try again.")
    a, b = ctx.get("operands", (None, None))
    fields = {"child": ctx.get("child", "there"), "exercise": ctx.get("exercise", ""),
              "left": a, "right": b, "answer": ctx.get("answer", "")}
    try:
        return tpl.format(**fields)
    except (KeyError, IndexError):
        return tpl


def _traced(fn):
    if os.environ.get("WANDB_API_KEY"):
        try:
            import weave
            return weave.op(name="tutor.call")(fn)
        except Exception:
            return fn
    return fn


class Tutor:
    def __init__(self, fallback: Optional[Callable[[str, dict], str]] = None, timeout: float = 1.5,
                 model: Optional[str] = None, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 project: Optional[str] = None):
        self.fallback = fallback or default_fallback
        self.timeout = timeout
        self.model = model or os.environ.get("TENFOLD_TUTOR_MODEL", DEFAULT_MODEL)
        self.base_url = (base_url or os.environ.get("WANDB_INFERENCE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or os.environ.get("WANDB_API_KEY")
        self.project = project or os.environ.get("WANDB_INFERENCE_PROJECT")  # "entity/project"
        self.cache: dict[tuple, str] = {}
        self.enabled = bool(self.api_key)
        self.stats = {"calls": 0, "hits": 0, "late": 0, "errors": 0}
        self._lock = threading.Lock()

    def _key(self, event: str, ctx: dict[str, Any]) -> tuple:
        det = ctx.get("detected") or {}
        return (ctx.get("method", "6-10"), ctx.get("exercise"), event, det.get("left"), det.get("right"), det.get("contact"))

    @_traced
    def _call(self, event: str, ctx: dict[str, Any]) -> str:
        user = (f"Event: {event}. Exercise: {ctx.get('exercise')}. Expected fingers: {ctx.get('operands')}. "
                f"Detected: {ctx.get('detected')}. Child: {ctx.get('child', 'the child')}.")
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.project:
            headers["OpenAI-Project"] = self.project
        r = httpx.post(f"{self.base_url}/chat/completions", headers=headers, timeout=self.timeout, json={
            "model": self.model, "max_tokens": 60, "temperature": 0.7,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        })
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"].strip().split("\n")[0]
        return text[:160]

    def say(self, event: str, exercise: str, detected: Optional[dict], child: str,
            on_phrase: Callable[[str, Any, str], None], state_id: Any = None,
            operands: Optional[tuple] = None, answer: Any = None) -> str:
        ctx = {"exercise": exercise, "detected": detected, "child": child, "operands": operands or (None, None),
               "answer": answer}
        fb = self.fallback(event, ctx)
        on_phrase(fb, state_id, "fallback")
        key = self._key(event, ctx)
        with self._lock:
            cached = self.cache.get(key)
        if cached:
            self.stats["hits"] += 1
            on_phrase(cached, state_id, "model")
            return cached
        if not self.enabled:
            return fb

        def worker():
            t0 = time.monotonic()
            self.stats["calls"] += 1
            try:
                text = self._call(event, ctx)
            except Exception:
                self.stats["errors"] += 1
                return
            if not text:
                return
            with self._lock:
                self.cache[key] = text
            if time.monotonic() - t0 <= self.timeout:
                on_phrase(text, state_id, "model")
            else:
                self.stats["late"] += 1

        threading.Thread(target=worker, daemon=True).start()
        return fb
