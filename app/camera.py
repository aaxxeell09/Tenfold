"""Opening the webcam, SPEC.md section 4. Frames stay in memory, nothing is written.

Two macOS problems are handled here, once, for every script that needs a camera.

The backend: without an explicit one OpenCV can fall back to FFMPEG, which
cannot enumerate AVFoundation devices and reports no camera at all. On macOS the
capture is opened with cv2.CAP_AVFOUNDATION. Elsewhere the default backend is
left alone, so the capture scripts still run on Linux.

The index: index 0 is not always the real camera. A virtual camera, Continuity
Camera or a screen sharing device can sit in front of it and hand out black
frames forever. So when no index is given, the indexes are probed in order and
the first one that actually produces a lit image wins. Each candidate gets a
warmup: the first frames out of a real camera are frequently black because the
sensor has not finished exposing, and judging on a single read would reject the
good camera.
"""

from __future__ import annotations

import sys
from typing import Any, Callable, Sequence

import cv2

PROBE_INDEXES: tuple[int, ...] = (0, 1, 2, 3)
WARMUP_FRAMES = 15
MIN_BRIGHTNESS = 5.0


class CameraError(RuntimeError):
    """No usable camera. The message says what was tried."""


def backend() -> int:
    """AVFoundation on macOS, the OpenCV default everywhere else."""
    return cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY


def _open(index: int, api: int) -> Any:
    return cv2.VideoCapture(index, api)


def peak_brightness(capture: Any, warmup: int = WARMUP_FRAMES) -> float:
    """Brightest mean over the first frames, so a slow sensor is not called black."""
    best = 0.0
    for _ in range(warmup):
        ok, frame = capture.read()
        if ok and frame is not None:
            best = max(best, float(frame.mean()))
    return best


def open_camera(
    index: int | None = None,
    opener: Callable[[int, int], Any] = _open,
    indexes: Sequence[int] = PROBE_INDEXES,
    warmup: int = WARMUP_FRAMES,
    min_brightness: float = MIN_BRIGHTNESS,
) -> tuple[Any, int]:
    """Return an open capture and the index it came from.

    An explicit index is taken at face value and never probed: if the operator
    asked for camera 2, a dark room is not a reason to silently use another one.
    With no index, the first index that opens and produces a mean brightness
    above min_brightness wins.

    Raises CameraError when nothing usable is found.
    """
    api = backend()

    if index is not None:
        capture = opener(index, api)
        if not capture.isOpened():
            capture.release()
            raise CameraError(f"no camera on index {index}")
        print(f"camera: using index {index} as asked")
        return capture, index

    tried: list[str] = []
    for candidate in indexes:
        capture = opener(candidate, api)
        if not capture.isOpened():
            capture.release()
            tried.append(f"{candidate} absent")
            continue
        brightness = peak_brightness(capture, warmup)
        if brightness > min_brightness:
            print(f"camera: picked index {candidate} (brightness {brightness:.1f})")
            return capture, candidate
        capture.release()
        tried.append(f"{candidate} black ({brightness:.1f})")

    raise CameraError(
        "no camera produced a lit image, tried " + ", ".join(tried)
        + ". Pass --camera N to force one, and check the camera permission "
        "for your terminal in System Settings, Privacy and Security."
    )
