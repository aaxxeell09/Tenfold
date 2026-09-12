"""app/normalize.py: recentering, scale, carried fields, window reset, short window.

No camera and no MediaPipe here: normalize takes anything with points and
confidence, so the hands are built by hand.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from app.normalize import POINTS_PER_HAND, Normalizer, normalize_hand
from classifier.features import MIDDLE_MCP, WRIST
from classifier.schema import WINDOW_SIZE, Window


def latest(window: Window):
    """The newest frame of each hand, None when that hand is absent."""
    left = window.left[-1] if window.left and window.left[-1].present else None
    right = window.right[-1] if window.right and window.right[-1].present else None
    return left, right


def fake_hand(wrist: tuple[float, float], scale: float = 0.1,
              confidence: float = 0.9) -> SimpleNamespace:
    """A hand whose wrist is at wrist and whose middle MCP sits scale above it."""
    wx, wy = wrist
    points = [(wx + 0.01 * i, wy - 0.004 * i, 0.001 * i) for i in range(POINTS_PER_HAND)]
    points[WRIST] = (wx, wy, 0.0)
    points[MIDDLE_MCP] = (wx, wy - scale, 0.0)
    return SimpleNamespace(points=points, confidence=confidence, handedness="Left")


def test_recenter_puts_the_wrist_at_the_origin():
    frame = normalize_hand(fake_hand((0.42, 0.61)))
    assert frame is not None
    assert frame.points[WRIST] == pytest.approx((0.0, 0.0, 0.0))


def test_scale_is_the_wrist_to_middle_mcp_distance():
    frame = normalize_hand(fake_hand((0.3, 0.5), scale=0.12))
    assert frame is not None
    assert frame.scale == pytest.approx(0.12)
    # After dividing by that scale the middle MCP sits exactly one unit away.
    assert math.hypot(*frame.points[MIDDLE_MCP][:2]) == pytest.approx(1.0)


def test_wrist_xy_and_scale_are_carried_on_the_frame():
    frame = normalize_hand(fake_hand((0.33, 0.58), scale=0.09, confidence=0.77))
    assert frame is not None
    assert frame.wrist_xy == pytest.approx((0.33, 0.58))
    assert frame.scale == pytest.approx(0.09)
    assert frame.detection_conf == pytest.approx(0.77)
    assert frame.present


def test_a_degenerate_hand_is_rejected():
    """Wrist and middle MCP at the same spot would divide the hand by zero."""
    hand = fake_hand((0.3, 0.5))
    hand.points[MIDDLE_MCP] = (0.3, 0.5, 0.0)
    assert normalize_hand(hand) is None
    assert normalize_hand(SimpleNamespace(points=[(0.0, 0.0, 0.0)] * 3, confidence=1.0)) is None


def test_window_grows_from_one_to_five_frames():
    """G8: a window of 1 to 5 frames is valid, only full windows are complete."""
    normalizer = Normalizer()
    for expected in range(1, WINDOW_SIZE + 1):
        window = normalizer.update([fake_hand((0.3, 0.5)), fake_hand((0.7, 0.5))])
        assert len(window) == expected
    # It stops growing once it is full.
    window = normalizer.update([fake_hand((0.3, 0.5)), fake_hand((0.7, 0.5))])
    assert len(window) == WINDOW_SIZE


def test_window_resets_when_a_wrist_jumps():
    normalizer = Normalizer()
    for _ in range(WINDOW_SIZE):
        window = normalizer.update([fake_hand((0.3, 0.5)), fake_hand((0.7, 0.5))])
    assert len(window) == WINDOW_SIZE

    # 0.04 in image units over a scale of 0.1 is 0.4 normalized units, past 0.3.
    window = normalizer.update([fake_hand((0.26, 0.5)), fake_hand((0.7, 0.5))])
    assert len(window) == 1

    # A small move leaves the window alone.
    window = normalizer.update([fake_hand((0.265, 0.5)), fake_hand((0.7, 0.5))])
    assert len(window) == 2


def test_left_and_right_come_from_wrist_x():
    normalizer = Normalizer()
    window = normalizer.update([fake_hand((0.72, 0.5)), fake_hand((0.28, 0.5))])
    left, right = latest(window)
    assert left is not None and right is not None
    assert left.wrist_xy[0] == pytest.approx(0.28)
    assert right.wrist_xy[0] == pytest.approx(0.72)


def test_a_missing_hand_keeps_the_two_sides_aligned_in_time():
    normalizer = Normalizer()
    normalizer.update([fake_hand((0.3, 0.5)), fake_hand((0.7, 0.5))])
    window = normalizer.update([fake_hand((0.3, 0.5))])
    assert len(window.left) == len(window.right) == 2
    left, right = latest(window)
    assert left is not None
    assert right is None
    assert not window.right[-1].present


def test_swap_needs_three_frames_of_a_wide_enough_gap():
    """G10: the by-x assignment is only swapped once the x gap has exceeded 0.1
    for 3 frames. The two hands are told apart here by their y, which is what
    makes the by-x order disagree with hand continuity."""
    normalizer = Normalizer()
    low_y, high_y = 0.5, 0.9

    window = normalizer.update([fake_hand((0.45, low_y)), fake_hand((0.55, high_y))])
    left, _ = latest(window)
    assert left.wrist_xy[0] == pytest.approx(0.45)

    # The hands cross: the low-y hand goes right, the high-y hand goes left.
    crossed = [fake_hand((0.56, low_y)), fake_hand((0.44, high_y))]
    for _ in range(2):
        left, _ = latest(normalizer.update(crossed))
        assert left.wrist_xy[0] == pytest.approx(0.56), "swapped too early"

    left, _ = latest(normalizer.update(crossed))
    assert left.wrist_xy[0] == pytest.approx(0.44), "should have swapped on the third frame"


def test_a_narrow_gap_never_swaps():
    normalizer = Normalizer()
    normalizer.update([fake_hand((0.49, 0.5)), fake_hand((0.51, 0.9))])
    crossed = [fake_hand((0.505, 0.5)), fake_hand((0.495, 0.9))]
    for _ in range(10):
        left, _ = latest(normalizer.update(crossed))
    assert left.wrist_xy[0] == pytest.approx(0.505)
