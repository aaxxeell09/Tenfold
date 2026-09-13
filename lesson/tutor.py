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

Live scoring. Every traced tutor.call gets lesson/tutor_rules.py applied to it with Weave's own Call.apply_scorer, so
the score is feedback on that call (type wandb.runnable.tutor_rules_v2), not a field of its output. What happened to
the line afterwards is a second feedback on the same call, type tutor_delivery (DELIVERY_NOTES). Both go through one
bounded queue and one daemon thread (ScoreQueue): nothing on the frame loop waits on Weave, a full queue drops and
counts, and a Weave that is missing or down costs the lesson nothing. tutor.call records only the event, the
exercise, the expected correction, the typed answer on an answer moment and the model (trace_context), never the
child's name or what the camera detected.
"""
from __future__ import annotations

import asyncio
import os
import queue
import threading
import time
from typing import Any, Callable, Optional

import httpx

from lesson import tutor_rules

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

_WEAVE: dict[str, Any] = {"tried": False, "op": None, "scorer": None}
RETRY_S = 15.0      # after a failed model call, the same moment is not retried for this long
PAUSE_AFTER = 3     # consecutive failures before all model calls pause
PAUSE_S = 30.0      # length of that pause; Tally's phrases keep the experience going meanwhile
SCORE_QUEUE_MAX = 32  # scores and delivery notes waiting for Weave; past this they are dropped and counted

DELIVERY_FEEDBACK = "tutor_delivery"
# What a tutor_delivery note can say. None of them claims the page received the line or that it was spoken: this
# module never sees an acknowledgement for either.
DELIVERY_NOTES: dict[str, str] = {
    "handed_to_server": "Tutor.phrase returned this line to its caller for a state message; page receipt and speech "
                        "are not observed here",
    "handed_to_callback": "on_phrase received this line; page receipt and speech are not observed here",
    "late_held": "arrived after the timeout, not shown in its moment; kept for the next time the moment happens",
    "moment_ended_unserved": "the moment changed before this line was returned; kept in case the moment comes back",
    "dropped_late": "arrived after the timeout; on_phrase never received it",
    "generation_failed": "the model call raised; Tally's canonical line stayed",
    "generation_empty": "the model said nothing usable; Tally's canonical line stayed",
}


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


def _plain_hint(hint: Any) -> Optional[dict[str, Any]]:
    """The hint as a plain dict, so Weave can record it and _hint_fields and tally.phrase still read the move."""
    if hint is None:
        return None
    if isinstance(hint, dict):
        return dict(hint)
    return {name: getattr(hint, name, None) for name in ("hand", "move_from", "move_to")}


def trace_context(event: str, ctx: dict[str, Any]) -> dict[str, Any]:
    """What Weave keeps of a moment: the exercise, the expected correction, and the typed answer on an answer moment.
    Never the child's name, the detected fingers or anything else the caller put in the context."""
    out: dict[str, Any] = {"exercise": ctx.get("exercise") or None}
    hint = _plain_hint(ctx.get("hint"))
    if hint and any(hint.get(k) is not None for k in ("hand", "move_from", "move_to")):
        out["hint"] = {k: hint.get(k) for k in ("hand", "move_from", "move_to")}
    operands = ctx.get("operands")
    if isinstance(operands, (list, tuple)) and len(operands) == 2 and all(isinstance(v, int) for v in operands):
        out["operands"] = list(operands)
    if str(event).startswith("answer") and ctx.get("answer") is not None:
        out["answer"] = ctx.get("answer")
    return out


def _record_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """postprocess_inputs of tutor.call: the model still gets the full context, Weave records trace_context."""
    event = str(inputs.get("event") or "")
    context = inputs.get("context")
    return {"event": event, "context": trace_context(event, dict(context) if isinstance(context, dict) else {}),
            "model": inputs.get("model")}


class Line(str):
    """The model's words for one moment, carrying the served model, token usage and latency of the call, and the
    Weave call that produced it when it was traced."""
    model: Optional[str] = None
    usage: Optional[dict] = None
    latency_ms: Optional[int] = None
    call: Any = None


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


class ScoreQueue:
    """One daemon thread and a bounded queue between the tutor and Weave. Nothing that renders, speaks or reads the
    camera waits on it: submit never blocks, a full queue drops the job and counts it, a job that raises is counted."""

    def __init__(self, maxsize: int = SCORE_QUEUE_MAX):
        self._jobs: queue.Queue = queue.Queue(maxsize=maxsize)
        self._thread: Optional[threading.Thread] = None
        self._start = threading.Lock()
        self.stats = {"scored": 0, "notes": 0, "dropped": 0, "errors": 0}

    def submit(self, job: Callable[[], None]) -> bool:
        with self._start:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="tutor-scoring", daemon=True)
                self._thread.start()
        try:
            self._jobs.put_nowait(job)
            return True
        except queue.Full:
            self.stats["dropped"] += 1
            return False

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            try:
                job()
            except Exception:
                self.stats["errors"] += 1
            finally:
                self._jobs.task_done()

    def wait(self, timeout: float = 5.0) -> bool:
        """True once every submitted job has run, for tests and eval/tutor_live_check.py."""
        end = time.monotonic() + timeout
        while self._jobs.unfinished_tasks:
            if time.monotonic() > end:
                return False
            time.sleep(0.01)
        return True


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
        self.scoring = ScoreQueue()
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
                _WEAVE["scorer"] = weave.op(name=tutor_rules.SCORER_OP)(tutor_rules.score_output)
            except Exception:
                _WEAVE["op"], _WEAVE["scorer"] = None, None
        if self.trace and _WEAVE["op"] is not None and getattr(self, "_op", None) is None:
            # one op per instance, so a second Tutor never runs with the first one's key or model
            try:
                self._op = _WEAVE["op"](name="tutor.call", postprocess_inputs=_record_inputs)(
                    lambda event, context, model: as_record(self._call(event, context)))
            except Exception:
                self.trace = False  # a Weave that cannot build the op leaves the model untraced, never silent
        op = getattr(self, "_op", None) if self.trace else None
        if op is None:
            return self._call(event, ctx)
        # the op must hand _call the same correction as the untraced path, so the hint stays a mapping
        context = {**ctx, "hint": _plain_hint(ctx["hint"])} if "hint" in ctx else dict(ctx)
        with_call = getattr(op, "call", None)
        if with_call is None:
            out = op(event, context, self.model)
            return out["text"] if isinstance(out, dict) else out
        out, call = with_call(event, context, self.model)   # Op.call never raises: a failure is on call.exception
        if getattr(call, "exception", None) is not None or not isinstance(out, dict):
            self._note(call, "generation_failed")
            raise RuntimeError("tutor.call failed")
        line = Line(out.get("text") or "")
        line.model, line.usage, line.latency_ms, line.call = out.get("model"), out.get("usage"), out.get("latency_ms"), call
        self._score(call, event, context)
        if not line:
            self._note_line(line, "generation_empty")
        return line

    # ---------- live scoring, off the frame loop ----------
    def _score(self, call: Any, event: str, context: dict[str, Any]) -> None:
        """Attach tutor_rules to the tutor.call that produced the line, with Weave's Call.apply_scorer."""
        scorer = _WEAVE.get("scorer")
        if scorer is None or call is None or not hasattr(call, "apply_scorer"):
            return
        kwargs = {"event": event, "context": trace_context(event, context), "timeout_ms": int(self.timeout * 1000)}

        def job() -> None:
            asyncio.run(call.apply_scorer(scorer, additional_scorer_kwargs=kwargs))
            self.scoring.stats["scored"] += 1

        self.scoring.submit(job)

    def _note(self, call: Any, status: str) -> None:
        """One tutor_delivery feedback on a tutor.call."""
        if call is None or not hasattr(call, "feedback"):
            return
        payload = {"status": status, "detail": DELIVERY_NOTES[status], "source": "model",
                   "scorer_version": tutor_rules.SCORER_VERSION}

        def job() -> None:
            call.feedback.add(DELIVERY_FEEDBACK, payload)
            self.scoring.stats["notes"] += 1

        self.scoring.submit(job)

    def _note_line(self, line: Any, status: str) -> bool:
        """_note once per status for a line that came from a traced call. False when nothing was noted."""
        call = getattr(line, "call", None)
        if call is None:
            return False
        noted = line.__dict__.setdefault("noted", set())
        if status in noted:
            return False
        noted.add(status)
        self._note(call, status)
        return True

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
                ended = self.cache.get(self._episode_key) if self._episode_key is not None else None
                if ended is not None and "handed_to_server" not in getattr(ended, "__dict__", {}).get("noted", ()):
                    self._note_line(ended, "moment_ended_unserved")
                self._episode_key = key
                self._blocked.clear()
            cached = self.cache.get(key)
            if cached is not None and key not in self._blocked:
                self.stats["hits"] += 1
                self._note_line(cached, "handed_to_server")
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
                    self._note_line(text, "late_held")
                elif self._episode_key != key:
                    self._note_line(text, "moment_ended_unserved")

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
            self._note_line(cached, "handed_to_callback")
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
                self._note_line(text, "handed_to_callback")
            else:
                self.stats["late"] += 1
                self._note_line(text, "dropped_late")

        threading.Thread(target=worker, daemon=True).start()
        return fb
