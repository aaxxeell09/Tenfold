"""Go/no-go check for MediaPipe on the real 6-10 gesture (SPEC.md section 13, 11:45).

Opens a mirrored webcam window, runs the frozen perception layer from app/
(landmarks then normalize), draws the 21 points per hand with the 6-10 finger
numbers, and measures for 60 seconds:

  - percent of frames where both hands are present
  - left/right swap rate
  - hands lost while the closest fingertip pair was already close

Verdict: GO if both hands on at least 90 percent of frames and swaps under
5 percent of two-hand frames, else NO GO. Press q to stop early.

Cross-hand distances use the shared image frame rebuilt from wrist_xy and
scale (SPEC.md section 5.2): image_xy = wrist_xy + normalized_xy * scale.
The distance is then divided by the mean scale of the two hands, so 0.25
means a quarter of a hand.

Exit code 0 on GO, 1 on NO GO.
"""

from __future__ import annotations

import importlib
import math
import sys
import time
from dataclasses import dataclass
from typing import Any

import cv2

DURATION_S = 60.0
NEAR_CONTACT = 0.25
TWO_HANDS_PASS = 0.90
SWAP_RATE_FAIL = 0.05

# MediaPipe fingertip indices mapped to the 6-10 numbering (SPEC.md section 5.1).
TIP_TO_FINGER = {4: 6, 8: 7, 12: 8, 16: 9, 20: 10}
TIP_INDICES = tuple(TIP_TO_FINGER)

WINDOW_NAME = "tenfold go/no-go"
COLOR_LEFT = (255, 200, 80)
COLOR_RIGHT = (140, 120, 255)
COLOR_TEXT = (255, 255, 255)
COLOR_CLOSE = (110, 220, 120)


# --- perception layer from app/ -------------------------------------------
#
# app/landmarks.py and app/normalize.py are frozen and owned by the app side;
# this script only reads them. They are not in the repo yet, so the entry point
# is resolved by name rather than pinned to one guessed signature. Once the two
# modules land, replace _load_perception with the two direct imports.

_DETECT_FUNCS = ("detect", "detect_hands", "find_hands", "process", "run")
_DETECT_CLASSES = ("HandLandmarker", "HandLandmarks", "HandDetector", "Detector", "Landmarker")
_NORM_FUNCS = ("normalize", "normalize_hands", "to_window", "build_window", "update")
_NORM_CLASSES = ("Normalizer", "WindowBuilder", "SlidingWindow")


def _resolve(module: Any, funcs: tuple[str, ...], classes: tuple[str, ...]) -> Any:
    """Return a callable taking one argument, from a module function or class."""
    for name in funcs:
        attr = getattr(module, name, None)
        if callable(attr):
            return attr
    for name in classes:
        attr = getattr(module, name, None)
        if isinstance(attr, type):
            instance = attr()
            for method in funcs + ("__call__",):
                bound = getattr(instance, method, None)
                if callable(bound):
                    return bound
    raise SystemExit(
        f"gonogo: no entry point found in {module.__name__}. "
        f"Looked for functions {funcs} or classes {classes}. "
        "Edit _load_perception in scripts/gonogo.py to match the real interface."
    )


def _load_perception() -> tuple[Any, Any]:
    landmarks = importlib.import_module("app.landmarks")
    normalize = importlib.import_module("app.normalize")
    return (
        _resolve(landmarks, _DETECT_FUNCS, _DETECT_CLASSES),
        _resolve(normalize, _NORM_FUNCS, _NORM_CLASSES),
    )


def as_hand_pair(result: Any) -> tuple[Any | None, Any | None]:
    """Reduce whatever normalize returns to the latest (left, right) HandFrame."""
    if result is None:
        return None, None
    if isinstance(result, dict):
        left, right = result.get("left"), result.get("right")
    elif isinstance(result, (tuple, list)) and len(result) == 2:
        left, right = result
    else:
        left, right = getattr(result, "left", None), getattr(result, "right", None)
    return _latest(left), _latest(right)


def _latest(side: Any) -> Any | None:
    """A Window holds a list of frames per side; take the most recent one."""
    if isinstance(side, (list, tuple)):
        side = side[-1] if side else None
    if side is None or getattr(side, "points", None) is None:
        return None
    if getattr(side, "wrist_xy", None) is None or not getattr(side, "scale", None):
        return None
    return side


# --- geometry in the shared image frame ------------------------------------


def point_image_xy(hand: Any, index: int) -> tuple[float, float]:
    """Undo the wrist recentering and the scaling to get back to image coords."""
    px, py = hand.points[index][0], hand.points[index][1]
    wx, wy = hand.wrist_xy
    return wx + px * hand.scale, wy + py * hand.scale


def closest_pair(left: Any, right: Any) -> tuple[int, int, float] | None:
    """Closest fingertip pair across hands, as (left finger, right finger, distance).

    Distance is normalized by the mean hand scale, so it is expressed in hands.
    """
    if left is None or right is None:
        return None
    mean_scale = (left.scale + right.scale) / 2.0
    if mean_scale <= 0.0:
        return None
    best: tuple[int, int, float] | None = None
    for li in TIP_INDICES:
        lx, ly = point_image_xy(left, li)
        for ri in TIP_INDICES:
            rx, ry = point_image_xy(right, ri)
            distance = math.hypot(lx - rx, ly - ry) / mean_scale
            if best is None or distance < best[2]:
                best = (TIP_TO_FINGER[li], TIP_TO_FINGER[ri], distance)
    return best


def swapped(prev: tuple[tuple[float, float], tuple[float, float]],
            current: tuple[tuple[float, float], tuple[float, float]]) -> bool:
    """True if this frame's left/right assignment flipped against hand continuity."""
    (pl, pr), (cl, cr) = prev, current
    keep = math.dist(cl, pl) + math.dist(cr, pr)
    flip = math.dist(cl, pr) + math.dist(cr, pl)
    return flip < keep


# --- counters --------------------------------------------------------------


@dataclass
class Counters:
    frames: int = 0
    two_hands: int = 0
    swaps: int = 0
    lost_while_close: int = 0

    @property
    def two_hands_rate(self) -> float:
        return self.two_hands / self.frames if self.frames else 0.0

    @property
    def swap_rate(self) -> float:
        return self.swaps / self.two_hands if self.two_hands else 0.0

    @property
    def go(self) -> bool:
        return self.two_hands_rate >= TWO_HANDS_PASS and self.swap_rate < SWAP_RATE_FAIL


def summary(counters: Counters, elapsed: float, complete: bool) -> str:
    fps = counters.frames / elapsed if elapsed > 0 else 0.0
    verdict = "GO" if counters.go else "NO GO"
    lines = [
        "",
        "=== go/no-go, MediaPipe on the 6-10 gesture ===",
        f"duration           : {elapsed:.1f} s over {DURATION_S:.0f} s"
        + ("" if complete else " (stopped early, partial run)"),
        f"frames             : {counters.frames} ({fps:.1f} fps)",
        f"two hands          : {counters.two_hands}/{counters.frames} = "
        f"{counters.two_hands_rate * 100:.1f} % (pass >= {TWO_HANDS_PASS * 100:.0f} %)",
        f"left/right swaps   : {counters.swaps} = {counters.swap_rate * 100:.1f} % "
        f"of two-hand frames (pass < {SWAP_RATE_FAIL * 100:.0f} %)",
        f"hand lost < {NEAR_CONTACT}   : {counters.lost_while_close} "
        "(hand disappeared while fingertips were already close)",
        f"verdict            : {verdict}",
    ]
    if not counters.go:
        if counters.two_hands_rate < TWO_HANDS_PASS:
            lines.append("reason             : both hands are not visible often enough")
        if counters.swap_rate >= SWAP_RATE_FAIL:
            lines.append("reason             : left/right assignment is unstable")
        lines.append("next               : table of 9 becomes the core method today (SPEC.md section 13)")
    return "\n".join(lines)


# --- drawing ---------------------------------------------------------------


def draw_hand(canvas: Any, hand: Any, color: tuple[int, int, int], label: str) -> None:
    height, width = canvas.shape[:2]
    for index in range(len(hand.points)):
        x, y = point_image_xy(hand, index)
        cv2.circle(canvas, (int(x * width), int(y * height)), 3, color, -1)
    for index in TIP_INDICES:
        x, y = point_image_xy(hand, index)
        cv2.putText(canvas, str(TIP_TO_FINGER[index]),
                    (int(x * width) + 6, int(y * height) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, COLOR_TEXT, 3, cv2.LINE_AA)
        cv2.putText(canvas, str(TIP_TO_FINGER[index]),
                    (int(x * width) + 6, int(y * height) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 1, cv2.LINE_AA)
    wx, wy = hand.wrist_xy
    cv2.putText(canvas, label, (int(wx * width) - 20, int(wy * height) + 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2, cv2.LINE_AA)


def draw_readout(canvas: Any, pair: tuple[int, int, float] | None, remaining: float) -> None:
    if pair is None:
        text = "closest pair: no two hands"
        color = COLOR_TEXT
    else:
        finger_l, finger_r, distance = pair
        text = f"closest pair: left {finger_l} - right {finger_r}   d = {distance:.3f}"
        color = COLOR_CLOSE if distance < NEAR_CONTACT else COLOR_TEXT
    cv2.putText(canvas, text, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2, cv2.LINE_AA)
    cv2.putText(canvas, f"{remaining:.0f} s left, q to quit", (20, 74),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLOR_TEXT, 1, cv2.LINE_AA)


# --- main loop -------------------------------------------------------------


def main() -> int:
    detect, normalize = _load_perception()

    camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        raise SystemExit("gonogo: no webcam on index 0")

    counters = Counters()
    previous_wrists: tuple[tuple[float, float], tuple[float, float]] | None = None
    previous_close = False
    start = time.monotonic()
    complete = False

    try:
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= DURATION_S:
                complete = True
                break

            ok, frame = camera.read()
            if not ok:
                continue

            # Mirror first: left/right is decided on wrist x in the mirrored image
            # (SPEC.md section 5.2), so the whole pipeline sees the mirrored frame.
            frame = cv2.flip(frame, 1)
            left, right = as_hand_pair(normalize(detect(frame)))

            counters.frames += 1
            pair = closest_pair(left, right)

            if left is not None and right is not None:
                counters.two_hands += 1
                wrists = (tuple(left.wrist_xy), tuple(right.wrist_xy))
                if previous_wrists is not None and swapped(previous_wrists, wrists):
                    counters.swaps += 1
                previous_wrists = wrists
                previous_close = pair is not None and pair[2] < NEAR_CONTACT
            else:
                if previous_close:
                    counters.lost_while_close += 1
                previous_wrists = None
                previous_close = False

            if left is not None:
                draw_hand(frame, left, COLOR_LEFT, "LEFT")
            if right is not None:
                draw_hand(frame, right, COLOR_RIGHT, "RIGHT")
            draw_readout(frame, pair, DURATION_S - elapsed)

            cv2.imshow(WINDOW_NAME, frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        camera.release()
        cv2.destroyAllWindows()

    print(summary(counters, time.monotonic() - start, complete))
    return 0 if counters.go else 1


if __name__ == "__main__":
    sys.exit(main())
