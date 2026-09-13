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
- v4: diagnosis's "no frame with both hands present" claim for 6x6/6x8/6x9 (s000001, s000002, s000010,
  s000011, s000012) does not hold: both_present_frames on those windows is empty regardless of the finite
  check, i.e. no rule can recover them (tracker hand loss). Measured instead that the biggest reachable
  failure is last-frame pair noise (e.g. s000421, s000426: 3-4 of 5 frames agree on one pair, the last
  frame flips to a neighbour by a hair). A flat vote/average over the window was already tried (v2) and
  regresses a small class every time (this run: near:10x8 or 8x10 depending on window size), because early
  frames of a settling hand outvote a decisive last frame. Instead only fall back to the window average
  when the last frame's own top two pairs are within AMBIGUITY_MARGIN=0.025 of each other (a coin flip);
  otherwise keep trusting the last frame as before. exact_match 0.500 -> 0.505, false_unknown_rate
  unchanged at 0.047, no class regressed beyond the gate's limit.
- v5: measured (not the diagnosis's wrist-motion guess) that wrist_motion already sits under MOTION_THRESHOLD
  for failing transition samples (e.g. s000004, s000005): the wrist is still while fingers keep reconfiguring,
  which wrist-only motion cannot see. Added tip_motion (mean per-frame fingertip displacement) gated to fire
  only above TIP_MOTION_CONF_GATE=0.75, since low-confidence tracking jitter (e.g. class 8x9, conf ~0.56-0.74)
  looks like motion but is noise, not travel. TIP_MOTION_THRESHOLD=0.2 (best passing value from --sweep):
  exact_match 0.510 -> 0.516, false_unknown_rate unchanged at 0.047, no class regressed.
- v6: measured (--class transition) that most reachable transition failures (e.g. s000004, s000018,
  s000026, s000067) keep the same or a drifting closest fingertip pair steadily separating across the
  window even though whole-hand tip_motion stays under threshold, since only two fingers move while the
  rest of both hands hold still. An unsigned drift (max-min) gate caught these but also flagged real
  holds captured mid-approach (s000007, s000008 converge into contact within the window) and blew up
  false_unknown_rate to 0.193 with 10 classes regressed, so it was replaced with a signed mean per-step
  trend that only fires when the pair is separating, not converging. Added pair_separation_trend, gated
  above TIP_MOTION_CONF_GATE like tip_motion, PAIR_DRIFT_THRESHOLD=0.05 (best passing value from --sweep):
  exact_match 0.516 -> 0.534, transition 0.27 -> 0.31, false_unknown_rate 0.047 -> 0.064, no class
  regressed beyond the gate's limit.
- v7: re-measured the diagnosis's pair_separation_trend leniency claim directly (--sweep-all): the
  mechanism is right but the threshold sits one notch too high, letting samples like s000004 (trend
  just under 0.05) through as false holds. Lowered PAIR_DRIFT_THRESHOLD 0.05 -> 0.0475 (best passing
  value from --sweep-all, the only constant change that passes the gate this round): exact_match
  0.534 -> 0.541, transition 0.31 -> 0.32, false_unknown_rate unchanged at 0.064, no class regressed.
- v8: re-measured (--sweep-all, --class transition): remaining transition failures (e.g. s000004,
  s000030) sit near-flat or barely separating, just under 0.0475, and every other constant's sweep
  either regresses a class or gives no improvement, so this is the same leniency v7 named, one notch
  further. Lowered PAIR_DRIFT_THRESHOLD 0.0475 -> 0.0451 (best passing value from --sweep-all):
  exact_match 0.541 -> 0.543, false_unknown_rate unchanged at 0.064, no class regressed.
"""
from __future__ import annotations

import numpy as np

from classifier import features
from classifier.schema import FINGER_NUMBERS, GestureState, Window

CONTACT_THRESHOLD = 0.2826  # fingertip distance, in units of mean hand scale
UNKNOWN_THRESHOLD = 0.525  # below this detection confidence we refuse to answer
AMBIGUITY_MARGIN = 0.025  # distance gap, in mean-scale units, below which the last frame's own top pair is a coin flip
MOTION_THRESHOLD = 0.1  # mean per-frame wrist displacement, in mean-scale units, above which hands are still moving
TIP_MOTION_THRESHOLD = 0.2  # mean per-frame fingertip displacement, in mean-scale units, above which fingers are still moving into shape
TIP_MOTION_CONF_GATE = 0.75  # only trust tip_motion above this confidence: low-confidence tracking jitter looks like motion but isn't
PAIR_DRIFT_THRESHOLD = 0.0451  # mean per-step increase of the closest-pair distance, in mean-scale units, above which the hands are still separating


def wrist_motion(window: Window) -> float:
    """Mean per-step wrist displacement of both hands, in mean-scale units, over both-present frames.

    A held gesture keeps the wrist still while the fingertips make contact; a transition is the wrist
    still travelling between two holds. Averaged over the whole window (not just the last step) so one
    noisy frame pair cannot hide steady travel.
    """
    frames = features.both_present_frames(window)
    if len(frames) < 2:
        return 0.0
    total = 0.0
    steps = 0
    for (l0, r0), (l1, r1) in zip(frames, frames[1:]):
        s = features.mean_scale(l0, r0)
        if not s > 0:
            continue
        lw0, lw1 = np.asarray(l0.wrist_xy, dtype=float), np.asarray(l1.wrist_xy, dtype=float)
        rw0, rw1 = np.asarray(r0.wrist_xy, dtype=float), np.asarray(r1.wrist_xy, dtype=float)
        step = (float(np.linalg.norm(lw1 - lw0)) + float(np.linalg.norm(rw1 - rw0))) / (2.0 * s)
        if np.isfinite(step):
            total += step
            steps += 1
    return total / steps if steps else 0.0


def tip_motion(window: Window) -> float:
    """Mean per-step fingertip displacement of both hands, in mean-scale units, over both-present frames.

    A held gesture keeps the fingers still once contact is made; a hand still curling or reaching into
    shape moves its tips even while the wrist itself is nearly stationary (e.g. rotating at the elbow),
    which wrist_motion alone cannot see.
    """
    frames = features.both_present_frames(window)
    if len(frames) < 2:
        return 0.0
    total = 0.0
    steps = 0
    for (l0, r0), (l1, r1) in zip(frames, frames[1:]):
        s = features.mean_scale(l0, r0)
        if not s > 0:
            continue
        try:
            lp0, rp0 = features.image_points(l0), features.image_points(r0)
            lp1, rp1 = features.image_points(l1), features.image_points(r1)
        except (ValueError, TypeError):
            continue
        tips = [features.TIP_INDEX[f] for f in FINGER_NUMBERS]
        l_step = float(np.mean(np.linalg.norm(lp1[tips] - lp0[tips], axis=1)))
        r_step = float(np.mean(np.linalg.norm(rp1[tips] - rp0[tips], axis=1)))
        step = (l_step + r_step) / (2.0 * s)
        if np.isfinite(step):
            total += step
            steps += 1
    return total / steps if steps else 0.0


def pair_separation_trend(window: Window) -> float:
    """Mean signed per-step change of each frame's own closest fingertip-pair distance, in mean-scale
    units; positive means the hands are steadily moving apart.

    A held gesture is either steady or still closing into contact (converging, negative trend), even
    when it is captured mid-approach: whole-hand wrist_motion and tip_motion cannot see this because
    only two fingers travel while the rest of both hands stay put. A transition that never reaches a
    hold instead keeps separating: signed averaging (not the unsigned range tried and rejected, which
    also flagged converging holds like s000007/s000008 and blew up false_unknown_rate) catches only the
    diverging case.
    """
    dists = []
    for l, r in features.both_present_frames(window):
        try:
            d = features.tip_distance_matrix(l, r)
        except (ValueError, TypeError):
            continue
        if np.isfinite(d).all():
            dists.append(float(np.min(d)))
    if len(dists) < 2:
        return 0.0
    diffs = [b - a for a, b in zip(dists, dists[1:])]
    return sum(diffs) / len(diffs)


def averaged_pair(window: Window, last: tuple) -> tuple[int, int, float]:
    """Nearest pair, falling back to the window average only when the last frame's own choice is ambiguous.

    The last frame alone is what classify() has always used, and it is right the great majority of the
    time: trusting it by default avoids the regressions seen when every frame is blended (a hand still
    settling into position early in the window then outvotes a decisive final frame). But when the last
    frame's closest pair and the next-closest pair are almost tied, a single noisy frame can flip the
    argmin to the wrong finger. In that narrow case only, average the distance matrix over all finite
    both-present frames and let the steadier consensus break the tie.
    """
    d = features.tip_distance_matrix(*last)
    flat = np.sort(d.reshape(-1))
    if flat[1] - flat[0] >= AMBIGUITY_MARGIN:
        return features.nearest_pair(*last)
    mats = [m for m in (features.tip_distance_matrix(l, r) for l, r in features.both_present_frames(window))
            if np.isfinite(m).all()]
    if len(mats) < 2:
        return features.nearest_pair(*last)
    mean_d = np.mean(mats, axis=0)
    i, j = np.unravel_index(int(np.argmin(mean_d)), mean_d.shape)
    return FINGER_NUMBERS[i], FINGER_NUMBERS[j], float(d[i, j])


def classify(window: Window) -> GestureState:
    frame = features.last_valid_frame(window)
    if frame is None:
        return GestureState.unknown()
    left, right = frame
    confidence = min(float(left.detection_conf), float(right.detection_conf))
    if confidence < UNKNOWN_THRESHOLD:
        return GestureState.unknown(confidence=max(0.0, min(1.0, confidence)))
    if wrist_motion(window) > MOTION_THRESHOLD:
        return GestureState.unknown(confidence=max(0.0, min(1.0, confidence)))
    if confidence > TIP_MOTION_CONF_GATE and tip_motion(window) > TIP_MOTION_THRESHOLD:
        return GestureState.unknown(confidence=max(0.0, min(1.0, confidence)))
    if confidence > TIP_MOTION_CONF_GATE and pair_separation_trend(window) > PAIR_DRIFT_THRESHOLD:
        return GestureState.unknown(confidence=max(0.0, min(1.0, confidence)))
    lf, rf, dist = averaged_pair(window, frame)
    return GestureState(
        method="6-10",
        left=lf,
        right=rf,
        contact=bool(dist < CONTACT_THRESHOLD),
        folded=None,
        confidence=max(0.0, min(1.0, confidence)),
    )
