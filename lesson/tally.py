"""Tally, the character. SPEC.md section 8.

These are the phrases that show instantly, before any model has answered, and
that stay if the model is slow or unreachable. They are the floor of the
experience, so they have to be good on their own.

Rules, from SPEC.md: warm, short, English for the demo, under 20 words. Tally
never says wrong, or no, or incorrect. When the pose is off she says Almost and
names the finger to move and where to move it, because a 7 year old cannot act
on "that is not right".

phrase(event, context) matches the fallback signature lesson/tutor.py expects,
so Tutor(fallback=tally.phrase) wires the model on top without changing this.
"""

from __future__ import annotations

from typing import Any

NAME = "Tally"

# Keyed by event, or by the screen state when no event carries the moment.
PHRASES: dict[str, str] = {
    "intro": "Hi, I am Tally. Show me your hands and let us multiply together.",
    "exercise_shown": "Here we go. Put up the two fingers and touch them.",
    "waiting_pose": "Show me both hands, palms towards me.",
    "one_hand": "Show me your other hand too.",
    "unknown_gesture": "Touch the two fingers tip to tip, gently.",
    "no_contact": "So close. Let those two fingertips touch.",
    "hands_swapped": "Almost. Swap your hands and try again.",
    "wrong_left_finger": "Almost. Move your left finger to {move_to}.",
    "wrong_right_finger": "Almost. Move your right finger to {move_to}.",
    "correct_pose": "That is it. Now count the tens, then the ones.",
    "answer_correct": "Yes. {answer} is exactly right.",
    "answer_wrong": "Almost. Count the tens again, slowly.",
}

FALLBACK = "Take your time, I am watching."

# Used when the event names a finger to move and the engine gave us the numbers.
DETAILED = {
    "wrong_left_finger": "Almost. Your right hand is good. Move your left finger from {move_from} to {move_to}.",
    "wrong_right_finger": "Almost. Your left hand is good. Move your right finger from {move_from} to {move_to}.",
}


def phrase(event: str, context: dict[str, Any] | None = None) -> str:
    """One short sentence for this moment. Never raises, never returns empty."""
    ctx: dict[str, Any] = dict(context or {})
    hint = ctx.get("hint") or {}
    if isinstance(hint, dict):
        ctx.setdefault("move_from", hint.get("move_from"))
        ctx.setdefault("move_to", hint.get("move_to"))

    template = PHRASES.get(event, FALLBACK)
    detailed = DETAILED.get(event)
    both_fingers = ctx.get("move_from") is not None and ctx.get("move_to") is not None
    if detailed is not None and both_fingers and _fits(detailed, ctx):
        template = detailed

    try:
        text = template.format(**ctx)
    except (KeyError, IndexError, ValueError):
        return FALLBACK
    return text if text.strip() else FALLBACK


def _fits(template: str, ctx: dict[str, Any]) -> bool:
    """The longer phrasing is only used while it stays under the 20 word limit."""
    try:
        return len(template.format(**ctx).split()) < 20
    except (KeyError, IndexError, ValueError):
        return False


def all_events() -> tuple[str, ...]:
    return tuple(PHRASES)
