"""Synthetic hands for the guard's smoke test, the tests, and the mock critic run. No MediaPipe needed.

Geometry: a hand is a wrist plus five fingers fanned out; each finger has MCP, PIP, DIP, TIP along a direction.
Points follow the MediaPipe index layout so classifier/features.py works on them unchanged.
"""
from __future__ import annotations

import dataclasses
import json
import math
import random
from typing import Optional

from classifier.schema import FINGER_NUMBERS, HandFrame, Window, window_to_json

# landmark indices per finger in MediaPipe order: (MCP, PIP, DIP, TIP); thumb: (CMC, MCP, IP, TIP)
FINGER_CHAIN = {6: (1, 2, 3, 4), 7: (5, 6, 7, 8), 8: (9, 10, 11, 12), 9: (13, 14, 15, 16), 10: (17, 18, 19, 20)}
# finger direction angles in degrees, 0 = up (toward smaller y); mirrored per hand
BASE_ANGLES = {6: 35.0, 7: 15.0, 8: 0.0, 9: -15.0, 10: -35.0}
JOINT_RADII = (1.0, 1.7, 2.1, 2.5)  # in hand-scale units, TIP extended at 2.5


def make_hand(
    wrist_xy: tuple[float, float],
    scale: float = 0.12,
    side: str = "left",
    tip_override: Optional[dict[int, tuple[float, float]]] = None,
    folded: tuple[int, ...] = (),
    conf: float = 0.9,
    noise: float = 0.0,
    rng: Optional[random.Random] = None,
) -> HandFrame:
    """Hand in image coords, then normalized as the contract requires. `side` decides the fan direction
    (thumb toward the other hand). tip_override moves a fingertip to an absolute image position."""
    rng = rng or random.Random(0)
    sign = 1.0 if side == "left" else -1.0  # left hand's thumb points to the right (screen), toward center
    img = [(0.0, 0.0)] * 21
    img[0] = wrist_xy
    for f in FINGER_NUMBERS:
        ang = math.radians(BASE_ANGLES[f]) * sign
        dx, dy = math.sin(ang), -math.cos(ang)
        radii = list(JOINT_RADII)
        if f in folded:
            radii[3] = radii[1] - 0.2  # tip pulled back below the PIP
        for idx, r in zip(FINGER_CHAIN[f], radii):
            img[idx] = (wrist_xy[0] + dx * r * scale, wrist_xy[1] + dy * r * scale)
        if tip_override and f in tip_override:
            img[FINGER_CHAIN[f][3]] = tip_override[f]
    if noise:
        img = [(x + rng.gauss(0, noise * scale), y + rng.gauss(0, noise * scale)) for x, y in img]
    pts = [((x - wrist_xy[0]) / scale, (y - wrist_xy[1]) / scale, 0.0) for x, y in img]
    return HandFrame(points=pts, detection_conf=conf, wrist_xy=wrist_xy, scale=scale)


ABSENT = HandFrame(points=None, detection_conf=0.0, wrist_xy=None, scale=None)
LEFT_WRIST, RIGHT_WRIST, SCALE, MEET = (0.25, 0.75), (0.75, 0.75), 0.12, (0.50, 0.45)


def contact_window(left_finger: int, right_finger: int, gap: float = 0.0, n: int = 5, conf: float = 0.9,
                   noise: float = 0.0, seed: int = 0) -> Window:
    """Both hands, the two chosen fingertips meeting at MEET (gap in hand-scale units along x)."""
    rng = random.Random(seed)
    lt = (MEET[0] - gap * SCALE / 2, MEET[1])
    rt = (MEET[0] + gap * SCALE / 2, MEET[1])
    left = [make_hand(LEFT_WRIST, SCALE, "left", {left_finger: lt}, conf=conf, noise=noise, rng=rng) for _ in range(n)]
    right = [make_hand(RIGHT_WRIST, SCALE, "right", {right_finger: rt}, conf=conf, noise=noise, rng=rng) for _ in range(n)]
    return Window(left=left, right=right)


def rest_window(n: int = 5) -> Window:
    return Window(left=[make_hand(LEFT_WRIST, SCALE, "left") for _ in range(n)],
                  right=[make_hand(RIGHT_WRIST, SCALE, "right") for _ in range(n)])


def one_hand_window(n: int = 5) -> Window:
    return Window(left=[make_hand(LEFT_WRIST, SCALE, "left") for _ in range(n)], right=[ABSENT] * n)


def empty_window(n: int = 5) -> Window:
    return Window(left=[ABSENT] * n, right=[ABSENT] * n)


def short_window() -> Window:
    return contact_window(7, 8, n=3)


def nan_window() -> Window:
    """Every frame carries NaN geometry: there is nothing valid to fall back to, so the answer must be unknown."""
    bad_l = HandFrame(points=[(float("nan"), float("nan"), 0.0)] * 21, detection_conf=0.9, wrist_xy=LEFT_WRIST, scale=SCALE)
    bad_r = HandFrame(points=[(float("nan"), float("nan"), 0.0)] * 21, detection_conf=0.9, wrist_xy=RIGHT_WRIST, scale=SCALE)
    return Window(left=[bad_l] * 5, right=[bad_r] * 5)


def degenerate_window() -> Window:
    same = HandFrame(points=[(0.0, 0.0, 0.0)] * 21, detection_conf=0.9, wrist_xy=LEFT_WRIST, scale=0.0)
    return Window(left=[same] * 5, right=[same] * 5)


def _adjacent(finger: int) -> int:
    return finger + 1 if finger < 10 else finger - 1


def jitter_newest(window: Window, left_finger: int) -> Window:
    """Real-camera failure: in the newest frame the tracker snaps the neighbouring left fingertip onto the contact
    point. A classifier that reads only the newest frame names the wrong finger; one that votes across frames
    does not."""
    bad = make_hand(LEFT_WRIST, SCALE, "left", {_adjacent(left_finger): MEET})
    return Window(left=window.left[:-1] + [bad], right=window.right)


def low_conf_newest(window: Window) -> Window:
    """Real-camera failure: the newest frame has low detection confidence (motion blur, occlusion)."""
    return Window(left=window.left[:-1] + [dataclasses.replace(window.left[-1], detection_conf=0.35)], right=window.right)


SMOKE_CASES: dict[str, Window] = {}


def smoke_cases() -> dict[str, Window]:
    return {
        "both_absent": empty_window(),
        "one_hand": one_hand_window(),
        "three_frames": short_window(),
        "five_valid_contact": contact_window(7, 8),
        "degenerate": degenerate_window(),
        "nan": nan_window(),
    }


def synthetic_dataset(seed: int = 0, holds_per_class: int = 3, windows_per_hold: int = 4, split: str = "train",
                      session: str = "synth", hard: bool = False) -> list[dict]:
    """A small labelled dataset in the samples.jsonl format, for tests and the mock loop.

    hard=True injects the failure modes V0 gets wrong on a real camera, so a rehearsal of the critic loop has
    something to fix: a jittered newest frame (35% of positives), a low-confidence newest frame (20%), and
    near-contact gaps just above the contact distance (0.30 to 0.60 hand-scales, V0's threshold is 0.35)."""
    rng = random.Random(seed)
    rows: list[dict] = []
    classes = [(7, 8), (8, 7), (6, 9), (9, 10), (10, 6), (8, 8)]
    n = 0

    def add(kind, label, window, hold):
        nonlocal n
        n += 1
        rows.append({"id": f"{session}{n:05d}", "hold_id": hold, "session": session,
                     "angle": rng.choice(["front", "top", "side"]), "distance": rng.choice(["near", "far"]),
                     "kind": kind, "label": label, "split": split, "window": window_to_json(window)})

    for lf, rf in classes:
        for h in range(holds_per_class):
            hold = f"{session}:{lf}x{rf}:{h}"
            for w in range(windows_per_hold):
                gap = rng.uniform(0.0, 0.10) if hard else rng.uniform(0.0, 0.15)
                win = contact_window(lf, rf, gap=gap, noise=0.03, seed=rng.randrange(10**6))
                if hard:
                    roll = rng.random()
                    if roll < 0.35:
                        win = jitter_newest(win, lf)
                    elif roll < 0.55:
                        win = low_conf_newest(win)
                add("positive", {"method": "6-10", "left": lf, "right": rf, "contact": True}, win, hold)
        hold = f"{session}:near:{lf}x{rf}"
        for w in range(windows_per_hold):
            gap = rng.uniform(0.30, 0.60) if hard else rng.uniform(0.6, 1.0)
            add("near_contact", {"method": "6-10", "left": lf, "right": rf, "contact": False},
                contact_window(lf, rf, gap=gap, noise=0.03, seed=rng.randrange(10**6)), hold)
    for h in range(holds_per_class):
        for w in range(windows_per_hold):
            add("partial_hand", {"method": "unknown"}, one_hand_window(), f"{session}:partial:{h}")
            add("transition", {"method": "unknown"}, empty_window(), f"{session}:transition:{h}")
    return rows


def write_jsonl(rows: list[dict], path) -> None:
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="write a synthetic samples.jsonl (rehearsals only, never data/samples.jsonl)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--split", default="train")
    ap.add_argument("--session", default="synth")
    ap.add_argument("--holds", type=int, default=3)
    ap.add_argument("--hard", action="store_true")
    a = ap.parse_args()
    rows = synthetic_dataset(seed=a.seed, holds_per_class=a.holds, split=a.split, session=a.session, hard=a.hard)
    write_jsonl(rows, a.out)
    print(f"wrote {len(rows)} samples to {a.out}")
