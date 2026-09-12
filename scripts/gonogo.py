"""Go/no-go check for MediaPipe on the real 6-10 gesture (SPEC.md section 13, 11:45).

Opens a mirrored webcam window, runs the frozen perception layer from app/
(landmarks then normalize), draws the 21 points per hand with the 6-10 finger
numbers, and measures for 60 seconds:

  - percent of frames where both hands are present
  - left/right swap rate
  - hands lost while the closest fingertip pair was already close

Verdict: GO if both hands on at least 90 percent of frames and swaps under
5 percent of two-hand frames, else NO GO. Press q to stop early.

The camera is opened through app/camera.py: AVFoundation on macOS, and with no
--camera the indexes are probed so a black virtual camera on index 0 does not
pass for the real one.

Cross-hand distances come from classifier/features.py, which rebuilds the shared
image frame from wrist_xy and scale and divides by the mean hand scale, so 0.25
means a quarter of a hand. The same helpers feed classifier/rules.py, so the
go/no-go measures the numbers the classifier will actually see.

Exit code 0 on GO, 1 on NO GO.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.camera import CameraError, open_camera  # noqa: E402
from app.landmarks import HandDetector  # noqa: E402
from app.normalize import Normalizer  # noqa: E402
from classifier import features  # noqa: E402
from classifier.features import TIP_INDEX  # noqa: E402
from classifier.schema import FINGER_NUMBERS, HandFrame  # noqa: E402

DURATION_S = 60.0
NEAR_CONTACT = 0.25
TWO_HANDS_PASS = 0.90
SWAP_RATE_FAIL = 0.05

WINDOW_NAME = "tenfold go/no-go"
COLOR_LEFT = (255, 200, 80)
COLOR_RIGHT = (140, 120, 255)
COLOR_TEXT = (255, 255, 255)
COLOR_CLOSE = (110, 220, 120)


def swapped(prev: tuple[tuple[float, float], tuple[float, float]],
            current: tuple[tuple[float, float], tuple[float, float]]) -> bool:
    """True if this frame's left/right assignment flipped against hand continuity."""
    (pl, pr), (cl, cr) = prev, current
    keep = math.dist(cl, pl) + math.dist(cr, pr)
    flip = math.dist(cl, pr) + math.dist(cr, pl)
    return flip < keep


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


def draw_hand(canvas: Any, hand: HandFrame, color: tuple[int, int, int], label: str) -> None:
    height, width = canvas.shape[:2]
    points = features.image_points(hand)
    for x, y in points:
        cv2.circle(canvas, (int(x * width), int(y * height)), 3, color, -1)
    for finger in FINGER_NUMBERS:
        x, y = points[TIP_INDEX[finger]]
        position = (int(x * width) + 6, int(y * height) - 6)
        cv2.putText(canvas, str(finger), position,
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, COLOR_TEXT, 3, cv2.LINE_AA)
        cv2.putText(canvas, str(finger), position,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tenfold MediaPipe go/no-go")
    parser.add_argument("--camera", type=int, default=None,
                        help="camera index, probed over 0 to 3 when not given")
    args = parser.parse_args(argv)

    try:
        camera, _ = open_camera(args.camera)
    except CameraError as error:
        raise SystemExit(f"gonogo: {error}")

    detector = HandDetector(num_hands=2)
    normalizer = Normalizer()
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
            window = normalizer.update(detector.detect(frame))
            left = window.left[-1] if window.left and window.left[-1].present else None
            right = window.right[-1] if window.right and window.right[-1].present else None

            counters.frames += 1
            # last_valid_frame also rejects non finite geometry, so a NaN hand
            # never turns into a distance.
            valid = features.last_valid_frame(window)
            pair = features.nearest_pair(*valid) if valid is not None else None

            if left is not None and right is not None:
                counters.two_hands += 1
                wrists = (left.wrist_xy, right.wrist_xy)
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
        detector.close()
        camera.release()
        cv2.destroyAllWindows()

    print(summary(counters, time.monotonic() - start, complete))
    return 0 if counters.go else 1


if __name__ == "__main__":
    sys.exit(main())
