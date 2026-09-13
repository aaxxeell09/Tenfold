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

# The neutral launch line lives in lesson/tally_lines.json, where the tutor
# reads it too. It is read from there rather than copied here, so the sentence
# the child hears has one home and one wording.
LINES_PATH = Path(__file__).with_name("tally_lines.json")


def _launch_line() -> str | None:
    try:
        raw = json.loads(LINES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    line = raw.get("launch") if isinstance(raw, dict) else None
    return line if isinstance(line, str) and line.strip() else None


LAUNCH = _launch_line()

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

    if event in MET_BEFORE_EVENTS and not met_before(ctx):
        return _launch(ctx)

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
    operands = _operands(ctx)
    if LAUNCH is not None and operands is not None:
        a, b = operands
        neutral = _rendered(LAUNCH, {**ctx, "a": a, "b": b})
        if neutral is not None:
            return neutral
    return PHRASES["exercise_shown"]


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
    return tuple(PHRASES)
