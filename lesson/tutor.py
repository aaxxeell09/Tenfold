"""Tally's voice: W&B Inference on top of Tally's fixed phrases. Never raises, never blocks the frame loop.

Two ways to use it.

1. Drop-in for tally.phrase, for code that rebuilds its message on every update (app/server.py):

    from lesson import tally
    from lesson.tutor import Tutor
    TUTOR = Tutor(fallback=tally.phrase)
    ... "tally": TUTOR.phrase(moment, context) ...        # same signature as tally.phrase(event, context)

   It returns the model's line for this moment when it is ready, else Tally's phrase, and fetches the model line
   in a background thread so the next update of the same moment shows it. A line that arrives after `timeout`
   seconds is kept for the next time that moment happens instead of swapping text mid-moment.

2. Push style, for code that wants a callback:

    tutor.say(event, exercise, detected, child, on_phrase, state_id)
   `on_phrase(text, state_id, source)` is called at once with the fallback ("fallback"), then with the model's
   line ("model") if it arrives within `timeout` seconds.

The model never invents the lesson: it gets the moment and Tally's own line for it, and says the same thing in its own
words (build_messages). Model calls are traced in Weave (op `tutor.call`, project tenfold) with the line, the served
model, token usage and latency, when WANDB_API_KEY comes from the environment. TENFOLD_TUTOR=0 switches the model off
and keeps Tally's phrases, for a stage with no network.
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
          "touch two fingertips, tens below, units above). Reply with at most two short sentences, under 20 words in "
          "total, in English, encouraging, never the words 'wrong', 'no' or 'incorrect'. Say 'almost' and name the "
          "finger to move and where to move it when the pose is off. Never reveal the result of the multiplication "
          "before the child types it. Reply with the words Tally says and nothing else: no quotes, no emoji.")

_WEAVE = {"tried": False, "op": None}
RETRY_S = 15.0      # after a failed model call, the same moment is not retried for this long
PAUSE_AFTER = 3     # consecutive failures before all model calls pause
PAUSE_S = 30.0      # length of that pause; Tally's phrases keep the experience going meanwhile


def default_fallback(event: str, ctx: dict[str, Any]) -> str:
    tpl = DEFAULT_FALLBACKS.get(event, "Let's try again.")
    a, b = ctx.get("operands") or (None, None)
    fields = {"child": ctx.get("child", "there"), "exercise": ctx.get("exercise", ""),
              "left": a, "right": b, "answer": ctx.get("answer", "")}
    try:
        return tpl.format(**fields)
    except (KeyError, IndexError, ValueError):
        return tpl


def _hint_fields(ctx: dict[str, Any]) -> tuple[Any, Any]:
    hint = ctx.get("hint")
    if isinstance(hint, dict):
        return hint.get("move_from"), hint.get("move_to")
    if hint is not None:
        return getattr(hint, "move_from", None), getattr(hint, "move_to", None)
    return ctx.get("move_from"), ctx.get("move_to")


class Line(str):
    """The model's words for one moment, carrying the served model, token usage and latency of the call."""
    model: Optional[str] = None
    usage: Optional[dict] = None
    latency_ms: Optional[int] = None


def build_messages(event: str, ctx: dict[str, Any], line: str) -> list[dict[str, str]]:
    """The chat prompt for one moment: what the camera and the lesson saw, and Tally's own line to say again."""
    move_from, move_to = _hint_fields(ctx)
    parts = [f"Event: {event}.", f"Exercise: {ctx.get('exercise') or 'none yet'}."]
    if ctx.get("operands"):
        parts.append(f"Expected fingers: {ctx.get('operands')}.")
    if ctx.get("detected"):
        parts.append(f"Detected: {ctx.get('detected')}.")
    if move_to is not None:
        parts.append(f"The child should move a finger from {move_from} to {move_to}.")
    if event.startswith("answer") and ctx.get("answer") is not None:
        parts.append(f"The child typed {ctx.get('answer')}.")
    parts.append(f"Child: {ctx.get('child', 'the child')}.")
    parts.append(f'Tally\'s line for this moment: "{line}"')
    parts.append("Say the same thing in your own words: keep every number and every instruction in it, and add "
                 "nothing the child has not done yet.")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": " ".join(parts)}]


def clean_reply(content: str | None) -> str:
    """The first spoken line of a reply: reasoning, blank lines and wrapping quotes removed, capped at 160 chars."""
    text = str(content or "").split("</think>")[-1]
    first = next((l.strip() for l in text.splitlines() if l.strip()), "")
    return first.strip('"“” ')[:160]


def as_record(text: str) -> dict[str, Any]:
    """What the tutor.call op returns, so Weave shows the line next to its model, usage and latency."""
    return {"text": str(text), "model": getattr(text, "model", None), "usage": getattr(text, "usage", None),
            "latency_ms": getattr(text, "latency_ms", None)}


class Tutor:
    def __init__(self, fallback: Optional[Callable[..., str]] = None, timeout: float = 1.5,
                 model: Optional[str] = None, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 project: Optional[str] = None, trace: Optional[bool] = None):
        self.fallback = fallback or default_fallback
        self.timeout = timeout
        self.model = model or os.environ.get("TENFOLD_TUTOR_MODEL", DEFAULT_MODEL)
        self.base_url = (base_url or os.environ.get("WANDB_INFERENCE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or os.environ.get("WANDB_API_KEY")
        self.project = project or os.environ.get("WANDB_INFERENCE_PROJECT")  # "entity/project"
        self.enabled = bool(self.api_key) and os.environ.get("TENFOLD_TUTOR", "1") != "0"
        # trace only with the environment's own key, never with a key injected by tests or callers
        self.trace = trace if trace is not None else (api_key is None and self.enabled and os.environ.get("TENFOLD_TRACE", "1") != "0")
        self.cache: dict[tuple, str] = {}
        self.stats = {"calls": 0, "hits": 0, "late": 0, "errors": 0}
        self._lock = threading.Lock()
        self._inflight: set[tuple] = set()
        self._episode_key: Optional[tuple] = None
        self._blocked: set[tuple] = set()
        self._retry_after: dict[tuple, float] = {}
        self._consecutive_errors = 0
        self._paused_until = 0.0

    def _backing_off(self, key: tuple) -> bool:
        now = time.monotonic()
        return now < self._paused_until or now < self._retry_after.get(key, 0.0)

    def _record(self, key: tuple, ok: bool) -> None:
        """Call with self._lock held."""
        if ok:
            self._consecutive_errors = 0
            self._retry_after.pop(key, None)
            return
        self.stats["errors"] += 1
        self._consecutive_errors += 1
        now = time.monotonic()
        self._retry_after[key] = now + RETRY_S
        if self._consecutive_errors >= PAUSE_AFTER:
            self._paused_until = now + PAUSE_S

    # ---------- model call ----------
    def _call(self, event: str, ctx: dict[str, Any]) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.project:
            headers["OpenAI-Project"] = self.project
        t0 = time.monotonic()
        r = httpx.post(f"{self.base_url}/chat/completions", headers=headers, timeout=max(self.timeout, 5.0), json={
            "model": self.model, "max_tokens": 60, "temperature": 0.7,
            "messages": build_messages(event, ctx, self.fallback(event, ctx)),
        })
        r.raise_for_status()
        body = r.json()
        line = Line(clean_reply(body["choices"][0]["message"]["content"]))
        line.model, line.usage = body.get("model") or self.model, body.get("usage")
        line.latency_ms = int((time.monotonic() - t0) * 1000)
        return line

    def _traced_call(self, event: str, ctx: dict[str, Any]) -> str:
        if self.trace and not _WEAVE["tried"]:
            _WEAVE["tried"] = True  # weave.init once per process
            try:
                import weave
                ent = os.environ.get("WANDB_ENTITY")
                weave.init(f"{ent}/tenfold" if ent else "tenfold")
                _WEAVE["op"] = weave.op
            except Exception:
                _WEAVE["op"] = None
        if self.trace and _WEAVE["op"] is not None and getattr(self, "_op", None) is None:
            # one op per instance, so a second Tutor never runs with the first one's key or model
            self._op = _WEAVE["op"](name="tutor.call")(lambda event, context, model: as_record(self._call(event, context)))
        if self.trace and getattr(self, "_op", None) is not None:
            out = self._op(event, {k: v for k, v in ctx.items() if k != "hint"} | {"hint": _hint_fields(ctx)}, self.model)
            return out["text"] if isinstance(out, dict) else out
        return self._call(event, ctx)

    # ---------- drop-in for tally.phrase ----------
    def _moment_key(self, event: str, ctx: dict[str, Any]) -> tuple:
        move_from, move_to = _hint_fields(ctx)
        det = ctx.get("detected") or {}
        answer = ctx.get("answer") if event.startswith("answer") else None
        return (event, ctx.get("exercise"), move_from, move_to, answer, det.get("left"), det.get("right"), det.get("contact"))

    def phrase(self, event: str, context: Optional[dict[str, Any]] = None) -> str:
        ctx: dict[str, Any] = dict(context or {})
        fb = self.fallback(event, ctx)
        if not self.enabled:
            return fb
        key = self._moment_key(event, ctx)
        with self._lock:
            if key != self._episode_key:
                self._episode_key = key
                self._blocked.clear()
            cached = self.cache.get(key)
            if cached is not None and key not in self._blocked:
                self.stats["hits"] += 1
                return cached
            if cached is not None or key in self._inflight or self._backing_off(key):
                return fb
            self._inflight.add(key)

        def worker() -> None:
            t0 = time.monotonic()
            self.stats["calls"] += 1
            try:
                text = self._traced_call(event, ctx)
            except Exception:
                text = ""
            with self._lock:
                self._inflight.discard(key)
                self._record(key, bool(text))
                if not text:
                    return
                self.cache[key] = text
                if time.monotonic() - t0 > self.timeout:
                    self.stats["late"] += 1
                    if self._episode_key == key:
                        self._blocked.add(key)

        threading.Thread(target=worker, daemon=True).start()
        return fb

    # ---------- push style ----------
    def say(self, event: str, exercise: str, detected: Optional[dict], child: str,
            on_phrase: Callable[[str, Any, str], None], state_id: Any = None,
            operands: Optional[tuple] = None, answer: Any = None) -> str:
        ctx = {"exercise": exercise, "detected": detected, "child": child, "operands": operands or (None, None),
               "answer": answer}
        fb = self.fallback(event, ctx)
        on_phrase(fb, state_id, "fallback")
        key = self._moment_key(event, ctx)
        with self._lock:
            cached = self.cache.get(key)
        if cached:
            self.stats["hits"] += 1
            on_phrase(cached, state_id, "model")
            return cached
        with self._lock:
            if not self.enabled or self._backing_off(key):
                return fb

        def worker() -> None:
            t0 = time.monotonic()
            self.stats["calls"] += 1
            try:
                text = self._traced_call(event, ctx)
            except Exception:
                text = ""
            with self._lock:
                self._record(key, bool(text))
                if not text:
                    return
                self.cache[key] = text
            if time.monotonic() - t0 <= self.timeout:
                on_phrase(text, state_id, "model")
            else:
                self.stats["late"] += 1

        threading.Thread(target=worker, daemon=True).start()
        return fb
