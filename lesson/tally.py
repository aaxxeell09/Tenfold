"""Tally, the character. SPEC.md section 8.

This module holds no sentence. Every word Tally says lives in
lesson/tally_lines.json, the twenty canonical lines the owner wrote plus the
few the older lesson path still needs, and this file only decides which key
fits the moment and fills its numbers in. One voice, one home: a phrase that is
not in that file is a phrase the child must never hear.

These are the phrases that show instantly, before any model has answered, and
that stay if the model is slow or unreachable. They are the floor of the
experience, so they have to be good on their own.

Rules, from SPEC.md: warm, short, English for the demo, under 20 words. Tally
never says wrong, or no, or incorrect. When the pose is off she says Almost and
names the finger to move and where to move it, because a 7 year old cannot act
on "that is not right".

phrase(event, context) matches the fallback signature lesson/tutor.py expects,
so Tutor(fallback=tally.phrase) wires the model on top without changing this.

One thing the context has to say is whether the child has met this fact before,
under the key "seen", which lesson/scheduler.py puts on every Pick. A lesson
widens past its node to fill its count, and the widening can land on a fact the
child has never seen, so a phrase calling it a review would be wrong. Never met
takes the neutral launch line and no subtitle. A missing key reads as met, which
is what every caller that says nothing means.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

NAME = "Tally"

LINES_PATH = Path(__file__).with_name("tally_lines.json")


def _load_lines() -> dict[str, str]:
    """The line file, or nothing at all.

    An unreadable file leaves Tally silent rather than speaking words that were
    written here instead of there. Silence is recoverable; invented text is not.
    """
    try:
        raw = json.loads(LINES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {key: value for key, value in raw.items()
            if isinstance(value, str) and value.strip()}


LINES: dict[str, str] = _load_lines()

# The moment, as the engine and the page name it, to the line that answers it.
# Where a canonical line already says the thing, the moment points straight at
# it: the pose being right is "pose_ready" and nothing else, which is how the
# second wording of that moment stopped existing.
KEYS: dict[str, str] = {
    "intro": "intro",
    "exercise_shown": "exercise_shown",
    "waiting_pose": "visibility_none",
    "one_hand": "visibility_one",
    "unknown_gesture": "unknown_gesture",
    "no_contact": "not_touching",
    "hands_swapped": "hands_swapped",
    "wrong_left_finger": "wrong_left_finger",
    "wrong_right_finger": "wrong_right_finger",
    "correct_pose": "pose_ready",
    "answer_correct": "answer_correct",
    "answer_wrong": "wrong_answer_1",

    # Onboarding, first session only.
    "count_fingers": "count_fingers",
    "guided": "guided",

    # Why Tally picked this exercise, from lesson/scheduler.py.
    "retry": "retry",
    "review": "review",
    "confidence": "confidence",
    "next_new": "next_new",
    "level_up": "level_up",

    # Help, in three steps.
    "hint_1": "hint_1",
    "hint_2": "show",
    "hint_3": "hint_3",
    "same_hand_twice": "same_hand_twice",
    "recount_tens": "count_tens",
    "recount_units": "wrong_answer_3",
    "cannot_see": "visibility_none",

    # The end of a session.
    "end_success": "end_success",
    "end_tired": "end_tired",
}

# The line said when the moment is not one Tally knows.
FALLBACK_KEY = "hesitation_1"

# Richer wording, used only when the context carries what it needs and the
# result still fits under the 20 word limit. Otherwise the plain line wins.
DETAILED_KEYS: dict[str, str] = {
    "wrong_left_finger": "wrong_left_finger_detailed",
    "wrong_right_finger": "wrong_right_finger_detailed",
    "answer_correct": "success",
    "hint_1": "hint_1_detailed",
    "same_hand_twice": "same_hand_twice_detailed",
    "retry": "retry_detailed",
    "next_new": "next_new_detailed",
    "confidence": "confidence_detailed",
    "review": "review_detailed",
    "end_success": "end_success_detailed",
}

# The tables as text, for callers that want to read a moment without rendering
# it. They are views on the file, built at import, never a second copy of it.
PHRASES: dict[str, str] = {event: LINES[key] for event, key in KEYS.items()
                           if key in LINES}
DETAILED: dict[str, str] = {event: LINES[key] for event, key in DETAILED_KEYS.items()
                            if key in LINES}
FALLBACK: str = LINES.get(FALLBACK_KEY, "")
LAUNCH: str | None = LINES.get("launch")

# Why Tally picked this exercise. The page shows these under her sentence, as
# the subtitle, so this tuple is also the list of moments that can have one.
PICK_EVENTS = ("retry", "review", "confidence", "next_new", "level_up")

# Of those, the ones that tell the child they have been here before. A fact the
# child has never met takes none of them: a lesson that widens past its node can
# serve a fact for the first time, and calling that a review would be a lie the
# child can catch. It is announced with the neutral launch line instead, and
# next_new and level_up are untouched, because a fact the node is teaching is
# announced as new already.
MET_BEFORE_EVENTS = ("retry", "review")

# The neutral announcement when a never met fact has no numbers to announce.
NEUTRAL_KEY = "exercise_shown"


def phrase(event: str, context: dict[str, Any] | None = None) -> str:
    """One short sentence for this moment, always from the line file."""
    ctx = _context(context)

    if event in MET_BEFORE_EVENTS and not met_before(ctx):
        return _launch(ctx)

    detailed = _rendered(DETAILED_KEYS.get(event), ctx)
    if detailed is not None:
        return detailed
    plain = _rendered(KEYS.get(event), ctx)
    return plain if plain is not None else FALLBACK


def _context(context: dict[str, Any] | None) -> dict[str, Any]:
    """The caller's context, plus the numbers a canonical line asks for by name.

    The hint carries the finger to move, the exercise carries the two factors,
    and the canonical lines name them {a}, {b} and {total}. Filling them here is
    the only arithmetic in this module, and it is arithmetic, not wording.
    """
    ctx: dict[str, Any] = dict(context or {})
    hint = ctx.get("hint") or {}
    if isinstance(hint, dict):
        for name in ("move_from", "move_to", "hand"):
            if ctx.get(name) is None and hint.get(name) is not None:
                ctx[name] = hint[name]
    operands = _operands(ctx)
    if operands is not None:
        a, b = operands
        ctx.setdefault("a", a)
        ctx.setdefault("b", b)
        ctx.setdefault("total", a * b)
    return ctx


def _rendered(key: str | None, ctx: dict[str, Any]) -> str | None:
    """Fill a line, or None when the context lacks a field or it runs long.

    A phrase with an unfilled placeholder must never reach a child, and neither
    must one over the 20 word limit, so both failures fall back rather than show.
    """
    template = LINES.get(key) if key else None
    if not template:
        return None
    try:
        text = template.format(**ctx)
    except (KeyError, IndexError, ValueError):
        return None
    if not text.strip() or len(text.split()) >= 20:
        return None
    return text


def subtitle(event: str, context: dict[str, Any] | None = None) -> str | None:
    """The little line under Tally's sentence, or None when there is none.

    Only a fact the child has actually met before gets one. A fact met for the
    first time is announced and nothing more, so nothing on the screen tells the
    child they have been here before when they have not.
    """
    ctx: dict[str, Any] = dict(context or {})
    if event not in PICK_EVENTS:
        return None
    if event in MET_BEFORE_EVENTS and not met_before(ctx):
        return None
    return phrase(event, ctx)


def met_before(context: dict[str, Any] | None = None) -> bool:
    """Whether the child has met this fact, as the context says.

    Only an explicit False means never met: a caller that says nothing about it
    gets the wording it got before this key existed.
    """
    return (context or {}).get("seen") is not False


def _launch(ctx: dict[str, Any]) -> str:
    """The neutral announcement: the question, and no claim about the past."""
    neutral = _rendered("launch", ctx)
    if neutral is not None:
        return neutral
    plain = _rendered(NEUTRAL_KEY, ctx)
    return plain if plain is not None else FALLBACK


def _operands(ctx: dict[str, Any]) -> tuple[int, int] | None:
    """The two factors, however the caller happens to name them."""
    for first, second in (("a", "b"), ("left", "right")):
        pair = (ctx.get(first), ctx.get(second))
        if all(isinstance(value, int) and not isinstance(value, bool)
               for value in pair):
            return int(pair[0]), int(pair[1])   # type: ignore[arg-type]
    listed = ctx.get("operands")
    if isinstance(listed, (list, tuple)) and len(listed) == 2:
        try:
            return int(listed[0]), int(listed[1])
        except (TypeError, ValueError):
            return None
    title = ctx.get("exercise")
    if isinstance(title, str):
        parts = title.lower().replace("x", " ").split()
        if len(parts) == 2:
            try:
                return int(parts[0]), int(parts[1])
            except ValueError:
                return None
    return None


def all_events() -> tuple[str, ...]:
    return tuple(KEYS)
