"""Tally's rules as one deterministic scorer, shared by the offline evaluation and the live lesson.

    score_intervention(event, context, text, latency_ms, timeout_ms) -> dict

eval/tutor_eval.py runs it on the 40 lesson moments, and lesson/tutor.py attaches it to every live tutor.call in
Weave (score_output is the op body). No network, no model, no judge.

Every rule returns {"status", "passed", "reason"}:
  pass            the rule holds on this reply
  fail            the rule is broken, and reason says how
  not_applicable  the rule has nothing to check in this moment
  not_verifiable  the rule applies, but this code cannot read the reply reliably
passed is True, False, or None for the last two, so a Weave summary (true_fraction) never counts them as a success.

The rules, version SCORER_VERSION. Scores from the v1 tally_rules scorer are not comparable with these.
  non_empty               the reply says something
  concise                 MAX_WORDS words or fewer, the limit of lesson/tutor.py SYSTEM and lesson/tally.py
  safe_words              never the words wrong, no or incorrect
  no_spoiler              before the answer is validated (REVEAL_EVENTS), the product is not said: digits, English
                          number words ("fifty six", "fifty-six", "fiftysix"), or as "5 tens and 6 units"
  correction_consistency  on a correction moment with a hint, the reply moves the hint's hand, and its from and to
                          numbers are the hint's, not swapped and not another finger
  in_time                 the reply arrived inside the tutor's timeout. Timing, not content; latency_ms sits next to it

Limits, said once here and in docs/tutor_scoring.md:
  English only, because the page speaks en-US and the model is told to answer in English. A French or Spanish
  number word is not read.
  Patterns, not understanding. A finger number counts as the start only after "from", "not" or "instead of", and as
  the target only after "to", "onto", "into", "be", "need(s)", "find" or "use". Numbers without those words, a
  reply that names no hand, or both hands in the move, give not_verifiable, never pass.
  It checks what the reply says against the context it was given. It says nothing about warmth, about whether the
  child understood, or about whether the camera read the pose right.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional

SCORER_VERSION = "tutor-rules-v2"
SCORER_OP = "tutor_rules_v2"    # the Weave op name of the live scorer, so its feedback is wandb.runnable.tutor_rules_v2
MAX_WORDS = 20
IN_TIME_MS = 1500               # lesson.tutor.Tutor's default timeout
RULES = ("non_empty", "concise", "safe_words", "no_spoiler", "correction_consistency", "in_time")

PASS, FAIL, NOT_APPLICABLE, NOT_VERIFIABLE = "pass", "fail", "not_applicable", "not_verifiable"

# The moments where the result may be said: the child's answer has been validated.
REVEAL_EVENTS = frozenset({"answer_correct"})
# The moments whose line names a finger to move, lesson/tally.py DETAILED_KEYS.
CORRECTION_EVENTS = frozenset({"wrong_left_finger", "wrong_right_finger", "hint_1", "hint_2", "same_hand_twice"})

FORBIDDEN = re.compile(r"\b(wrong|no|incorrect)\b", re.IGNORECASE)
_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                     "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), start=2)}
_START_WORDS = frozenset({"from", "not"})
_TARGET_WORDS = frozenset({"to", "onto", "into", "be", "needs", "need", "find", "use"})
_CLAUSES = re.compile(r"[.!?;:,]+|\bbut\b|\bthen\b")
_HANDS = re.compile(r"\b(left|right)\s+(?:hand|hands|finger|fingers|thumb|side|one)\b|\b(?:on|to)\s+(?:the|your)\s+(left|right)\b")
_MOVE = re.compile(r"\b(move|slide|put|find|switch|change|bring|use|needs?|should|try|go|pick|shift|lift|touch)\b")
_KEEP = re.compile(r"\b(good|fine|perfect|great|ok|okay|correct|done|stays?|keep|already)\b|\bis\s+right\b")
_EXERCISE = re.compile(r"^\s*(\d+)\s*(?:x|\*|×|times)\s*(\d+)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Check:
    status: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        passed = True if self.status == PASS else False if self.status == FAIL else None
        return {"status": self.status, "passed": passed, "reason": self.reason[:120]}


# ---------- reading a reply ----------

def tokens(text: str) -> list[str]:
    return re.findall(r"\d+|[a-z]+", str(text or "").lower())


def number_mentions(words: list[str]) -> list[tuple[int, int, int]]:
    """(value, first token, token after the number) for every number said in digits or English words."""
    out: list[tuple[int, int, int]] = []
    i = 0
    while i < len(words):
        w = words[i]
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if w.isdigit():
            out.append((int(w), i, i + 1))
        elif w in _TENS:
            if nxt in _UNITS and 1 <= _UNITS[nxt] <= 9:
                out.append((_TENS[w] + _UNITS[nxt], i, i + 2))
                i += 2
                continue
            out.append((_TENS[w], i, i + 1))
        elif w in _UNITS:
            if nxt == "hundred" and _UNITS[w] == 1:
                out.append((100, i, i + 2))
                i += 2
                continue
            out.append((_UNITS[w], i, i + 1))
        elif w == "hundred" and i > 0 and words[i - 1] == "a":
            out.append((100, i - 1, i + 1))
        else:
            glued = next((_TENS[t] + _UNITS[w[len(t):]] for t in _TENS
                          if w.startswith(t) and w[len(t):] in _UNITS and 1 <= _UNITS[w[len(t):]] <= 9), None)
            if glued is not None:
                out.append((glued, i, i + 1))
        i += 1
    return out


def operands_of(context: Mapping[str, Any]) -> Optional[tuple[int, int]]:
    """The two factors of the exercise, from operands or from a title like '7 x 8'."""
    listed = context.get("operands")
    if isinstance(listed, (list, tuple)) and len(listed) == 2 and all(
            isinstance(v, int) and not isinstance(v, bool) for v in listed):
        return int(listed[0]), int(listed[1])
    m = _EXERCISE.match(str(context.get("exercise") or ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def _hint(context: Mapping[str, Any]) -> dict[str, Any]:
    hint = context.get("hint")
    if isinstance(hint, Mapping):
        return dict(hint)
    if hint is not None:
        return {name: getattr(hint, name, None) for name in ("hand", "move_from", "move_to")}
    return {}


def _int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _combine(checks: list[Check]) -> Check:
    for status in (FAIL, NOT_VERIFIABLE):
        hit = [c.reason for c in checks if c.status == status]
        if hit:
            return Check(status, "; ".join(hit))
    return Check(PASS, "; ".join(c.reason for c in checks))


# ---------- the rules ----------

def non_empty(text: str) -> Check:
    return Check(PASS, "says something") if text.strip() else Check(FAIL, "the reply is empty")


def concise(text: str) -> Check:
    n = len(text.split())
    if n == 0:
        return Check(NOT_APPLICABLE, "nothing was said")
    return Check(PASS, f"{n} words") if n <= MAX_WORDS else Check(FAIL, f"{n} words, the limit is {MAX_WORDS}")


def safe_words(text: str) -> Check:
    if not text.strip():
        return Check(NOT_APPLICABLE, "nothing was said")
    m = FORBIDDEN.search(text)
    return Check(FAIL, f"says '{m.group(0)}'") if m else Check(PASS, "no wrong, no or incorrect")


def no_spoiler(event: str, context: Mapping[str, Any], text: str) -> Check:
    operands = operands_of(context)
    if operands is None:
        if context.get("exercise"):
            return Check(NOT_VERIFIABLE, f"cannot read the exercise '{context.get('exercise')}'")
        return Check(NOT_APPLICABLE, "no exercise in this moment")
    product = operands[0] * operands[1]
    if event in REVEAL_EVENTS:
        return Check(NOT_APPLICABLE, f"the answer is validated, {product} may be said")
    if not text.strip():
        return Check(NOT_APPLICABLE, "nothing was said")
    words = tokens(text)
    mentions = number_mentions(words)
    if any(value == product for value, _, _ in mentions):
        return Check(FAIL, f"says the result {product} before the answer is validated")
    tens = [v for v, _, end in mentions if end < len(words) and words[end] == "tens"]
    units = [v for v, _, end in mentions if end < len(words) and words[end] in ("units", "unit", "ones")]
    for t in tens:
        for u in units:
            if 10 * t + u == product:
                return Check(FAIL, f"gives the result as {t} tens and {u} units")
    return Check(PASS, f"{product} is not said in digits or English words")


def _hand_check(clauses: list[str], hand: Optional[str]) -> Check:
    if hand is None:
        return Check(NOT_VERIFIABLE, "the context names no hand")
    other = "left" if hand == "right" else "right"
    move: set[str] = set()
    keep: set[str] = set()
    anywhere: set[str] = set()
    for clause in clauses:
        hands = {a or b for a, b in _HANDS.findall(clause)}
        anywhere |= hands
        if number_mentions(tokens(clause)) or _MOVE.search(clause):
            move |= hands
        elif _KEEP.search(clause):
            keep |= hands
    if move:
        if move == {hand}:
            return Check(PASS, f"moves the {hand} hand")
        if move == {other}:
            return Check(FAIL, f"asks to move the {other} hand, the {hand} one is off")
        return Check(NOT_VERIFIABLE, "names both hands in the move")
    if keep == {hand}:
        return Check(FAIL, f"calls the {hand} hand fine, it is the one to move")
    if keep == {other}:
        return Check(PASS, f"keeps the {other} hand, so the {hand} one moves")
    if anywhere == {hand}:
        return Check(PASS, f"names the {hand} hand")
    if anywhere == {other}:
        return Check(FAIL, f"names only the {other} hand, the {hand} one is off")
    return Check(NOT_VERIFIABLE, "does not say which hand moves")


def _number_check(text: str, start: Optional[int], target: Optional[int]) -> Check:
    if target is None:
        return Check(NOT_VERIFIABLE, "the context has no target finger")
    words = tokens(text)
    mentions = number_mentions(words)
    if not mentions:
        return Check(NOT_VERIFIABLE, "names no finger number")
    starts: set[int] = set()
    targets: set[int] = set()
    for value, first, _ in mentions:
        prev = words[first - 1] if first > 0 else ""
        prev2 = words[first - 2] if first > 1 else ""
        if prev in _START_WORDS or (prev == "of" and prev2 == "instead"):
            starts.add(value)
        elif prev in _TARGET_WORDS:
            targets.add(value)
    if start is not None and target in starts and start in targets:
        return Check(FAIL, f"start and target swapped: says from {target} to {start}, expected from {start} to {target}")
    if targets - {target}:
        return Check(FAIL, f"target {sorted(targets - {target})[0]}, expected {target}")
    if target in starts:
        return Check(FAIL, f"names {target} as the start, it is the target")
    if start is not None and starts - {start}:
        return Check(FAIL, f"start {sorted(starts - {start})[0]}, the child is on {start}")
    if target in targets:
        return Check(PASS, f"from {start} to {target}" if start in starts else f"target {target}, start not named")
    return Check(NOT_VERIFIABLE, "finger numbers without from or to")


def correction_consistency(event: str, context: Mapping[str, Any], text: str) -> Check:
    if event not in CORRECTION_EVENTS:
        return Check(NOT_APPLICABLE, "not a correction moment")
    if not text.strip():
        return Check(NOT_APPLICABLE, "nothing was said")
    hint = _hint(context)
    event_hand = "left" if "left" in event else "right" if "right" in event else None
    hint_hand = hint.get("hand") if hint.get("hand") in ("left", "right") else None
    if hint_hand and event_hand and hint_hand != event_hand:
        return Check(NOT_VERIFIABLE, f"the context disagrees: {event} with a {hint_hand} hand hint")
    hand = hint_hand or event_hand
    start, target = _int(hint.get("move_from")), _int(hint.get("move_to"))
    if hand is None and target is None:
        return Check(NOT_VERIFIABLE, "the context carries no expected hand or target finger")
    clauses = [c.strip() for c in _CLAUSES.split(text.lower()) if c.strip()]
    return _combine([_hand_check(clauses, hand), _number_check(text, start, target)])


def in_time(latency_ms: Any, timeout_ms: int = IN_TIME_MS) -> Check:
    if not isinstance(latency_ms, (int, float)) or isinstance(latency_ms, bool):
        return Check(NOT_VERIFIABLE, "no latency was recorded")
    if latency_ms <= timeout_ms:
        return Check(PASS, f"{int(latency_ms)} ms, inside {timeout_ms} ms")
    return Check(FAIL, f"{int(latency_ms)} ms, over {timeout_ms} ms")


def score_intervention(event: str, context: Optional[Mapping[str, Any]], text: Optional[str],
                       latency_ms: Any = None, timeout_ms: int = IN_TIME_MS) -> dict[str, Any]:
    """Every rule on one reply, as plain JSON: small enough for a Weave feedback payload."""
    ctx: Mapping[str, Any] = context if isinstance(context, Mapping) else {}
    reply = str(text or "")
    checks = {
        "non_empty": non_empty(reply),
        "concise": concise(reply),
        "safe_words": safe_words(reply),
        "no_spoiler": no_spoiler(event, ctx, reply),
        "correction_consistency": correction_consistency(event, ctx, reply),
        "in_time": in_time(latency_ms, timeout_ms),
    }
    latency = int(latency_ms) if isinstance(latency_ms, (int, float)) and not isinstance(latency_ms, bool) else None
    return {"scorer_version": SCORER_VERSION, "latency_ms": latency, **{k: c.as_dict() for k, c in checks.items()}}


def score_output(event: str, context: dict, output: dict, timeout_ms: int = IN_TIME_MS) -> dict[str, Any]:
    """The live scorer's body: tutor.call returns {"text", "model", "usage", "latency_ms"} (lesson.tutor.as_record)."""
    out = output if isinstance(output, Mapping) else {}
    return score_intervention(event, context, out.get("text"), out.get("latency_ms"), timeout_ms)
