"""MediaPipe hand landmarks, 21 points per hand, two hands (SPEC.md 4 and 5.1).

Frozen after the contract session: data/capture.py and app/main.py must see
exactly the same perception, so the dataset and the live app cannot drift.

This module only reports what MediaPipe returns, in image coordinates. It does
not decide which hand is the left one: SPEC.md 5.2 assigns left and right by
wrist x in the mirrored image, and app/normalize.py owns that decision. The
MediaPipe handedness label is carried through for the record and is deliberately
not used, because it is wrong by construction on a mirrored frame.

The model file is about 8 MB, so it is cached outside the repo and downloaded on
first use rather than committed.
"""

from __future__ import annotations

import os
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

# 21 landmarks per hand, as fixed by the frozen contract in classifier/schema.py.
POINTS_PER_HAND = 21

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)
DEFAULT_MODEL_PATH = (
    Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    / "tenfold"
    / "hand_landmarker.task"
)


@dataclass
class RawHand:
    """One detected hand, straight out of MediaPipe.

    points holds 21 (x, y, z) landmarks. x and y are image coordinates in 0..1
    on the frame that was passed in, so on a mirrored frame they are mirrored
    too. z is MediaPipe relative depth, roughly wrist relative, with a scale of
    its own per hand.
    """

    points: list[tuple[float, float, float]]
    handedness: str
    confidence: float


def ensure_model(path: Path = DEFAULT_MODEL_PATH, url: str = MODEL_URL) -> Path:
    """Return the local model path, downloading it once if it is missing."""
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    print(f"landmarks: downloading the hand model to {path}")
    with urllib.request.urlopen(url, timeout=120) as response, open(tmp, "wb") as out:
        while chunk := response.read(1 << 16):
            out.write(chunk)
    tmp.replace(path)
    return path


class HandDetector:
    """Stateful MediaPipe HandLandmarker in video mode, two hands.

    Video mode keeps MediaPipe's own tracking between frames, which matters when
    the two hands come close to each other during a 6-10 contact.
    """

    def __init__(
        self,
        num_hands: int = 2,
        model_path: Path | str | None = None,
        min_detection_confidence: float = 0.5,
        min_presence_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
    ) -> None:
        resolved = Path(model_path) if model_path is not None else DEFAULT_MODEL_PATH
        options = vision.HandLandmarkerOptions(
            # CPU delegate, forced. On macOS the default delegate takes the Metal
            # path and TensorsToDetectionsCalculator aborts the process with
            # "DrishtiMetalHelper ... Service is unavailable". The classifier
            # runs in a few milliseconds on CPU, so nothing is lost.
            base_options=mp_python.BaseOptions(
                model_asset_path=str(ensure_model(resolved)),
                delegate=mp_python.BaseOptions.Delegate.CPU,
            ),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=num_hands,
            min_hand_detection_confidence=min_detection_confidence,
            min_hand_presence_confidence=min_presence_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        self._landmarker = vision.HandLandmarker.create_from_options(options)
        self._last_timestamp_ms = -1

    def detect(self, frame_bgr, timestamp_ms: int | None = None) -> list[RawHand]:
        """Detect hands on one BGR frame. Pass the frame already mirrored."""
        stamp = int(time.monotonic() * 1000) if timestamp_ms is None else int(timestamp_ms)
        # detect_for_video refuses a timestamp that does not move forward.
        stamp = max(stamp, self._last_timestamp_ms + 1)
        self._last_timestamp_ms = stamp

        image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB),
        )
        result = self._landmarker.detect_for_video(image, stamp)
        return to_raw_hands(result)

    def close(self) -> None:
        self._landmarker.close()

    def __enter__(self) -> HandDetector:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def to_raw_hands(result: object) -> list[RawHand]:
    """Convert a HandLandmarkerResult into RawHand, dropping malformed hands.

    Kept apart from HandDetector so it can be exercised without the MediaPipe
    native runtime.
    """
    landmarks = getattr(result, "hand_landmarks", None) or []
    handedness = getattr(result, "handedness", None) or []
    hands: list[RawHand] = []
    for index, hand_points in enumerate(landmarks):
        if len(hand_points) != POINTS_PER_HAND:
            continue
        label, score = "unknown", 0.0
        if index < len(handedness) and handedness[index]:
            top = handedness[index][0]
            label = getattr(top, "category_name", "unknown") or "unknown"
            score = float(getattr(top, "score", 0.0))
        hands.append(
            RawHand(
                points=[(float(p.x), float(p.y), float(p.z)) for p in hand_points],
                handedness=label,
                confidence=score,
            )
        )
    return hands
