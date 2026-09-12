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

# Keyed by event, or by the screen state when no event carries the moment. None
# of these carry a placeholder: a line here has to render with no context at all,
# because a phrase reading "move your finger to None" must never reach a child.
PHRASES: dict[str, str] = {
    "intro": "Hi, I am Tally. Show me your hands and let us multiply together.",
    "exercise_shown": "Here we go. Put up the two fingers and touch them.",
    "waiting_pose": "Show me both hands, palms towards me.",
    "one_hand": "Show me your other hand too.",
    "unknown_gesture": "Touch the two fingers tip to tip, gently.",
    "no_contact": "So close. Let those two fingertips touch.",
    "hands_swapped": "Almost. Swap your hands and try again.",
    "wrong_left_finger": "Almost. Move your left finger a little.",
    "wrong_right_finger": "Almost. Move your right finger a little.",
    "correct_pose": "That is it. Now count the tens, then the ones.",
    "answer_correct": "Yes. That is exactly right.",
    "answer_wrong": "Almost. Count the tens again, slowly.",

    # Onboarding, first session only.
    "count_fingers": "Thumb is six, then seven, eight, nine, ten. Say them with me.",
    "guided": "Let us do one together. I will show you every step.",

    # Why Tally picked this exercise, from lesson/scheduler.py.
    "retry": "Let us have another go at that one.",
    "review": "Here is one you have met before.",
    "confidence": "An easy one now, just because.",
    "next_new": "Something brand new. Ready?",
    "level_up": "You are quick today. Let us jump ahead.",

    # Help, in three steps.
    "hint_1": "Look at your hands. One finger needs to move.",
    "hint_2": "See the faint circle? Put that finger there.",
    "hint_3": "Watch me do it, then copy me.",
    "same_hand_twice": "Both hands show the same finger. They need different ones.",
    "recount_tens": "Almost. Count the tens again: the touched ones and below.",
    "recount_units": "Almost. The tens are good. Count the ones above.",
    "cannot_see": "I cannot see your hands. Bring them into the light.",

    # The end of a session.
    "end_success": "Lovely work today. Come back soon and we keep going.",
    "end_tired": "That is plenty for today. You did really well.",
}

FALLBACK = "Take your time, I am watching."

# Richer wording, used only when the context carries what it needs and the
# result still fits under the 20 word limit. Otherwise the plain line above wins.
DETAILED = {
    "wrong_left_finger": "Almost. Your right hand is good. Move your left finger from {move_from} to {move_to}.",
    "wrong_right_finger": "Almost. Your left hand is good. Move your right finger from {move_from} to {move_to}.",
    "answer_correct": "Yes. {answer} is exactly right.",
    "hint_1": "Look at your {hand} hand. That finger should be {move_to}.",
    "same_hand_twice": "Both hands show {move_from}. Your {hand} hand needs {move_to}.",
    "retry": "Let us try {exercise} again. You were close.",
    "next_new": "A brand new one: {exercise}. Ready?",
    "confidence": "An easy one: {exercise}, just because.",
    "review": "Here is {exercise} again.",
    "end_success": "Lovely work. Tomorrow we try {tomorrow}.",
}


def phrase(event: str, context: dict[str, Any] | None = None) -> str:
    """One short sentence for this moment. Never raises, never returns empty."""
    ctx: dict[str, Any] = dict(context or {})
    hint = ctx.get("hint") or {}
    if isinstance(hint, dict):
        for name in ("move_from", "move_to", "hand"):
            if ctx.get(name) is None and hint.get(name) is not None:
                ctx[name] = hint[name]

    detailed = _rendered(DETAILED.get(event), ctx)
    if detailed is not None:
        return detailed
    plain = _rendered(PHRASES.get(event), ctx)
    return plain if plain is not None else FALLBACK


def _rendered(template: str | None, ctx: dict[str, Any]) -> str | None:
    """Fill a template, or None when the context lacks a field or it runs long.

    A phrase with an unfilled placeholder must never reach a child, and neither
    must one over the 20 word limit, so both failures fall back rather than show.
    """
    if not template:
        return None
    try:
        text = template.format(**ctx)
    except (KeyError, IndexError, ValueError):
        return None
    if not text.strip() or len(text.split()) >= 20:
        return None
    return text


def all_events() -> tuple[str, ...]:
    return tuple(PHRASES)
