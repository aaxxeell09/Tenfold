"""FROZEN CONTRACT. Shared geometry for rules.py, capture.py, the kNN baseline and the eval report.

Everything here is deterministic numpy over the frozen schema. rules.py may import this module and nothing
else outside the standard library and numpy.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from classifier.schema import FINGER_NUMBERS, HandFrame, Window

TIP_INDEX: dict[int, int] = {6: 4, 7: 8, 8: 12, 9: 16, 10: 20}
PIP_INDEX: dict[int, int] = {6: 3, 7: 6, 8: 10, 9: 14, 10: 18}  # thumb uses its IP joint
WRIST = 0
INDEX_MCP = 5
MIDDLE_MCP = 9


def image_points(hand: HandFrame) -> np.ndarray:
    """(21, 2) points in mirrored image coordinates, shared by both hands."""
    if not hand.present:
        raise ValueError("hand not present")
    pts = np.asarray(hand.points, dtype=float)[:, :2]
    return pts * float(hand.scale) + np.asarray(hand.wrist_xy, dtype=float)


def both_present_frames(window: Window) -> list[tuple[HandFrame, HandFrame]]:
    return [(l, r) for l, r in zip(window.left, window.right) if l.present and r.present]


def last_valid_frame(window: Window) -> Optional[tuple[HandFrame, HandFrame]]:
    """Newest frame with both hands present and finite geometry, else None."""
    for l, r in reversed(both_present_frames(window)):
        try:
            if np.isfinite(image_points(l)).all() and np.isfinite(image_points(r)).all():
                return l, r
        except (ValueError, TypeError):
            continue
    return None


def mean_scale(left: HandFrame, right: HandFrame) -> float:
    return (float(left.scale) + float(right.scale)) / 2.0


def tip_distance_matrix(left: HandFrame, right: HandFrame) -> np.ndarray:
    """(5, 5) distances between left fingertip i and right fingertip j, rows/cols in FINGER_NUMBERS order,
    in units of the mean hand scale (1.0 = one wrist-to-middle-MCP length)."""
    lp, rp = image_points(left), image_points(right)
    s = mean_scale(left, right)
    if not s > 0:
        return np.full((5, 5), np.inf)
    lt = np.stack([lp[TIP_INDEX[f]] for f in FINGER_NUMBERS])
    rt = np.stack([rp[TIP_INDEX[f]] for f in FINGER_NUMBERS])
    return np.linalg.norm(lt[:, None, :] - rt[None, :, :], axis=2) / s


def extended(hand: HandFrame, finger: int) -> bool:
    """A finger is extended when its tip is farther from the wrist than its PIP (thumb: IP) joint."""
    pts = np.asarray(hand.points, dtype=float)[:, :2]  # wrist-origin normalized coords
    return float(np.linalg.norm(pts[TIP_INDEX[finger]])) > float(np.linalg.norm(pts[PIP_INDEX[finger]]))


def extension_vector(hand: HandFrame) -> np.ndarray:
    return np.array([1.0 if extended(hand, f) else 0.0 for f in FINGER_NUMBERS])


def feature_vector(window: Window) -> np.ndarray:
    """35 floats for the kNN baseline: 25 tip distances + 5 + 5 extension flags. Missing geometry -> 9.0 / 0."""
    frame = last_valid_frame(window)
    if frame is None:
        return np.concatenate([np.full(25, 9.0), np.zeros(10)])
    l, r = frame
    d = np.minimum(tip_distance_matrix(l, r), 9.0).reshape(-1)
    return np.concatenate([d, extension_vector(l), extension_vector(r)])


def nearest_pair(left: HandFrame, right: HandFrame) -> tuple[int, int, float]:
    """(left finger number, right finger number, distance) of the closest fingertip pair."""
    d = tip_distance_matrix(left, right)
    i, j = np.unravel_index(int(np.argmin(d)), d.shape)
    return FINGER_NUMBERS[i], FINGER_NUMBERS[j], float(d[i, j])
