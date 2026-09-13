"""Tenfold gesture classifier. V0: the honest first version a developer writes.

This is the only file the critic loop edits. Allowed imports: math, statistics, dataclasses, typing, numpy,
classifier.schema, classifier.features. No file, network or introspection access (the guard enforces it).

Hypothesis log (one line per accepted patch, newest last):
- v0: nearest fingertip pair on the newest frame with both hands; contact if closer than one threshold.
- v1: measured (not the diagnosis's pair-selection guess) that near_contact holds were the biggest failure:
  the correct pair is found but sits just under 0.35, so contact flips true when the label expects false.
  Lowered CONTACT_THRESHOLD to 0.2975 (best passing value from --sweep-all), near_contact_accuracy 0.41 -> 0.73.
- v2: measured that the diagnosed 6x7/6x8 pair-selection failure (nearest-pair picks index-index over
  thumb-index) is real but not reachable by any single constant or a recent-frame vote/average without
  regressing a small class (e.g. 8x10, near:6x10 each lose their one hold). Instead found low-confidence
  frames (~0.50-0.52, e.g. s000500, s000513, s000560, s000561) slip past UNKNOWN_THRESHOLD and produce a
  spurious pair on negative/transition samples. Raised UNKNOWN_THRESHOLD to 0.525 (best passing value from
  --sweep-all), exact_match 0.492 -> 0.497, false_unknown_rate unchanged at 0.047.
- v3: re-measured the diagnosed 6x6/6x7/6x8 pair-selection failure directly (--class 6x7): it is real
  (index-index beats thumb-index in 3 of 4 failing samples) but --sweep-all confirms again no single
  constant reaches it, and v2 already found frame-voting regresses small classes. Sweep-all's best
  passing change is instead a near-contact boundary miscalibration one step down from v1's value:
  CONTACT_THRESHOLD 0.2975 -> 0.2826 fixes near-contact samples (e.g. class 9x6) sitting just above the
  old threshold, near_contact_accuracy 0.736 -> 0.778, exact_match 0.497 -> 0.500, false_unknown_rate
  unchanged at 0.047, no class regressed.
"""
from __future__ import annotations

from classifier import features
from classifier.schema import GestureState, Window

CONTACT_THRESHOLD = 0.2826  # fingertip distance, in units of mean hand scale
UNKNOWN_THRESHOLD = 0.525  # below this detection confidence we refuse to answer


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
