"""Normalization and the sliding window, SPEC.md 5.2.

Frozen after the contract session.

Per hand: origin on the wrist, scale is the wrist to middle MCP distance in
image units, x and y divided by that scale. The MediaPipe z is left untouched:
it is a relative depth with a scale of its own per hand and is never compared
between two hands. wrist_xy and scale ride along on the HandFrame so that
classifier/features.py can rebuild the frame shared by both hands (G3).

Left and right come from the wrist x in the mirrored image, never from the
MediaPipe handedness label, with hysteresis: the by-x assignment is only swapped
once the x gap has exceeded 0.1 for 3 consecutive frames (G10).

The window keeps up to 5 frames per hand and is reset when a wrist moves by more
than 0.3 normalized units between two frames. Both sides are reset together: the
features that matter are cross-hand, so a teleporting right hand invalidates the
history of the pair, not just its own.

This module deliberately imports neither MediaPipe nor OpenCV. It takes anything
carrying points and confidence, which is what app/landmarks.RawHand provides.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Any, Protocol, Sequence

from classifier.features import MIDDLE_MCP, WRIST
from classifier.schema import WINDOW_SIZE, HandFrame, Window

POINTS_PER_HAND = 21
JUMP_RESET = 0.3
SWAP_X_DELTA = 0.1
SWAP_FRAMES = 3

# HandFrame is frozen, so one shared absent frame is enough for every gap.
ABSENT = HandFrame(points=None, detection_conf=0.0, wrist_xy=None, scale=None)


class DetectedHand(Protocol):
    points: Sequence[tuple[float, float, float]]
    confidence: float


def normalize_hand(hand: Any) -> HandFrame | None:
    """Put one detected hand in its wrist origin frame.

    Returns None when the hand is unusable: wrong point count, or a wrist to
    middle MCP distance of zero, which would divide the whole hand by zero.
    """
    points = getattr(hand, "points", None)
    if points is None or len(points) != POINTS_PER_HAND:
        return None

    wx, wy = float(points[WRIST][0]), float(points[WRIST][1])
    mx, my = float(points[MIDDLE_MCP][0]), float(points[MIDDLE_MCP][1])
    scale = math.hypot(mx - wx, my - wy)
    if scale <= 0.0:
        return None

    return HandFrame(
        points=[
            ((float(p[0]) - wx) / scale, (float(p[1]) - wy) / scale, float(p[2]))
            for p in points
        ],
        detection_conf=float(getattr(hand, "confidence", 0.0)),
        wrist_xy=(wx, wy),
        scale=scale,
    )


class Normalizer:
    """Turns a stream of detected hands into a Window of normalized frames."""

    def __init__(
        self,
        window_size: int = WINDOW_SIZE,
        jump_reset: float = JUMP_RESET,
        swap_x_delta: float = SWAP_X_DELTA,
        swap_frames: int = SWAP_FRAMES,
    ) -> None:
        self.window_size = window_size
        self.jump_reset = jump_reset
        self.swap_x_delta = swap_x_delta
        self.swap_frames = swap_frames
        self._left: deque[HandFrame] = deque(maxlen=window_size)
        self._right: deque[HandFrame] = deque(maxlen=window_size)
        self._prev_left_xy: tuple[float, float] | None = None
        self._prev_right_xy: tuple[float, float] | None = None
        self._cross_frames = 0

    def reset(self) -> None:
        """Start from scratch: window, remembered wrists and swap counter."""
        self._reset_window()
        self._prev_left_xy = None
        self._prev_right_xy = None
        self._cross_frames = 0

    def _reset_window(self) -> None:
        """Drop the window only.

        The remembered wrist positions and the swap counter survive on purpose.
        A wrist jump is exactly when the hands are most likely to be crossing,
        so clearing the counter here would make the G10 hysteresis unreachable
        in the one case it exists for.
        """
        self._left.clear()
        self._right.clear()

    def update(self, hands: Sequence[Any]) -> Window:
        """Consume one frame of detected hands and return the current window."""
        frames = [f for f in (normalize_hand(h) for h in hands) if f is not None]
        left, right = self._assign(frames)

        if self._jumped(left, self._prev_left_xy) or self._jumped(right, self._prev_right_xy):
            self._reset_window()

        self._left.append(left if left is not None else ABSENT)
        self._right.append(right if right is not None else ABSENT)
        if left is not None:
            self._prev_left_xy = left.wrist_xy
        if right is not None:
            self._prev_right_xy = right.wrist_xy

        return Window(left=list(self._left), right=list(self._right))

    def _jumped(self, frame: HandFrame | None, previous: tuple[float, float] | None) -> bool:
        """True when this wrist moved more than jump_reset hand widths."""
        if frame is None or previous is None or not frame.scale:
            return False
        return math.dist(frame.wrist_xy, previous) / frame.scale > self.jump_reset

    def _assign(self, frames: list[HandFrame]) -> tuple[HandFrame | None, HandFrame | None]:
        """Decide which frame is the left hand and which is the right one."""
        if not frames:
            self._cross_frames = 0
            return None, None

        if len(frames) == 1:
            self._cross_frames = 0
            return self._assign_single(frames[0])

        # More than two hands should not happen with num_hands=2, but if it does
        # keep the two widest apart rather than guessing.
        frames = sorted(frames, key=lambda f: f.wrist_xy[0])
        low, high = frames[0], frames[-1]

        by_x = (low, high)
        if self._prev_left_xy is None or self._prev_right_xy is None:
            self._cross_frames = 0
            return by_x

        keep = math.dist(low.wrist_xy, self._prev_left_xy) + math.dist(
            high.wrist_xy, self._prev_right_xy
        )
        flip = math.dist(low.wrist_xy, self._prev_right_xy) + math.dist(
            high.wrist_xy, self._prev_left_xy
        )
        continuity = by_x if keep <= flip else (high, low)

        if continuity == by_x:
            self._cross_frames = 0
            return by_x

        # The hands look crossed against their own history. Only believe it once
        # the x gap has been convincing for swap_frames frames in a row (G10).
        gap = high.wrist_xy[0] - low.wrist_xy[0]
        self._cross_frames = self._cross_frames + 1 if gap > self.swap_x_delta else 0
        if self._cross_frames >= self.swap_frames:
            self._cross_frames = 0
            return by_x
        return continuity

    def _assign_single(self, frame: HandFrame) -> tuple[HandFrame | None, HandFrame | None]:
        """One hand on screen: keep the side it was on, otherwise split at 0.5."""
        x = frame.wrist_xy[0]
        if self._prev_left_xy is not None and self._prev_right_xy is not None:
            to_left = abs(x - self._prev_left_xy[0])
            to_right = abs(x - self._prev_right_xy[0])
            return (frame, None) if to_left <= to_right else (None, frame)
        return (frame, None) if x < 0.5 else (None, frame)
