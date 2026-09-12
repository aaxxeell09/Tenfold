"""Tenfold gesture classifier. V0: the honest first version a developer writes.

This is the only file the critic loop edits. Allowed imports: math, statistics, dataclasses, typing, numpy,
classifier.schema, classifier.features. No file, network or introspection access (the guard enforces it).

Hypothesis log (one line per accepted patch, newest last):
- v0: nearest fingertip pair on the newest frame with both hands; contact if closer than one threshold.
"""
from __future__ import annotations

from classifier import features
from classifier.schema import GestureState, Window

CONTACT_THRESHOLD = 0.35  # fingertip distance, in units of mean hand scale
UNKNOWN_THRESHOLD = 0.5  # below this detection confidence we refuse to answer


def classify(window: Window) -> GestureState:
    frame = features.last_valid_frame(window)
    if frame is None:
        return GestureState.unknown()
    left, right = frame
    confidence = min(float(left.detection_conf), float(right.detection_conf))
    if confidence < UNKNOWN_THRESHOLD:
        return GestureState.unknown(confidence=max(0.0, min(1.0, confidence)))
    lf, rf, dist = features.nearest_pair(left, right)
    return GestureState(
        method="6-10",
        left=lf,
        right=rf,
        contact=bool(dist < CONTACT_THRESHOLD),
        folded=None,
        confidence=max(0.0, min(1.0, confidence)),
    )
