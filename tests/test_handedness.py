"""The child's left hand is the one Tally calls left. app/server.py camera path.

A camera does not mirror. Facing a child, it sees them the way another person
does, so the child's LEFT hand lands on the RIGHT of the raw frame, exactly as
your left hand is on the right of a photograph of you.

app/server.py flips that frame once, and that single flip settles three things
together, which is why they cannot disagree:

  1. the picture the child sees is a mirror, left hand on the left of the screen;
  2. the detector is handed the selfie orientation MediaPipe documents, so its
     own Left and Right labels would be the child's real hands (this repo does
     not use them: app/landmarks.py carries them and app/normalize.py decides by
     wrist x instead, SPEC.md 5.2);
  3. the hand at the smaller x is the child's left hand, which is the name that
     travels through the classifier, the engine, Tally's line and the overlay.

So the test below paints two unmistakable hands into a raw frame, the child's
left one on the right of it, feeds it through the real path (flip, detect,
normalize, fingers_from_window) with only the detector faked, and asserts that
the fingers that come out labelled "left" are the ones the child actually calls
left. If anyone removes the flip, or flips twice, or mirrors again in the page,
this goes red.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from app import server
from classifier.schema import GestureState

WIDTH, HEIGHT = 64, 48

# Where each hand sits in the RAW frame, as a fraction of the width. The camera
# is not a mirror, so the child's left hand is the one on the right.
CHILD_RIGHT_X = 0.25
CHILD_LEFT_X = 0.75

BLUE = (255, 0, 0)      # BGR. The child's left hand.
RED = (0, 0, 255)       # BGR. The child's right hand.


def raw_frame() -> np.ndarray:
    """One frame straight off a webcam pointed at a child, hands unmistakable."""
    frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    for centre, colour in ((CHILD_RIGHT_X, RED), (CHILD_LEFT_X, BLUE)):
        x = int(centre * WIDTH)
        frame[16:32, x - 4:x + 4] = colour
    return frame


def blob_x(frame: np.ndarray, colour: tuple[int, int, int]) -> float:
    """Where that colour sits in this frame, as a fraction of the width."""
    hit = np.all(frame == np.array(colour, dtype=np.uint8), axis=2)
    assert hit.any(), f"{colour} is not in the frame at all"
    return float(np.nonzero(hit)[1].mean() + 0.5) / WIDTH


def hand_at(x: float) -> SimpleNamespace:
    """21 landmarks around x, in the 0..1 image coordinates MediaPipe returns.

    Only the shape matters here: a wrist, a middle MCP a tenth of the frame
    above it so the normalized scale is not zero, and fingertips fanned out.
    """
    points = [(x, 0.60, 0.0)] * 21
    points[9] = (x, 0.50, 0.0)                      # middle MCP, sets the scale
    for slot, tip in enumerate((4, 8, 12, 16, 20)):
        points[tip] = (x + (slot - 2) * 0.02, 0.40, 0.0)
    return SimpleNamespace(points=points, confidence=0.9, handedness="unknown")


class ColourDetector:
    """A stand in for MediaPipe that finds the two painted hands by colour.

    It keeps the frame it was given, which is the one assertion nothing else
    can make: whether the picture reaching the detector, and the child, is a
    mirror or the camera's own view.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.frames: list[np.ndarray] = []

    def detect(self, frame: np.ndarray) -> list[SimpleNamespace]:
        self.frames.append(frame.copy())
        return [hand_at(blob_x(frame, RED)), hand_at(blob_x(frame, BLUE))]

    def close(self) -> None:
        pass


class OneFrameCamera:
    """Delivers the raw frame once, then reports the device as gone."""

    def __init__(self, frame: np.ndarray) -> None:
        self.frame = frame
        self.reads = 0
        self.released = False

    def read(self):
        self.reads += 1
        return (True, self.frame.copy()) if self.reads == 1 else (False, None)

    def release(self) -> None:
        self.released = True


class SpyLesson:
    """Only what camera_loop touches, so the test is about the frame and nothing else."""

    def __init__(self) -> None:
        self.hub = SimpleNamespace(set_frame=self._set_frame)
        self.live = SimpleNamespace(offer=lambda window, verdict: None)
        self.shown: list[bytes] = []
        self.fingers: list[dict] = []
        self.failure: str | None = None

    def _set_frame(self, jpeg: bytes) -> None:
        self.shown.append(jpeg)

    def observe(self, verdict, fingers, hands_seen, now, palm=None) -> None:
        self.fingers = fingers

    def fail(self, message: str) -> None:
        self.failure = message


def run_camera_loop(monkeypatch, mirror: bool = True) -> tuple[SpyLesson, ColourDetector]:
    """One frame through the real camera_loop, with only the detector faked."""
    import app.camera as camera_module
    import app.landmarks as landmarks
    import classifier.loader as loader

    camera = OneFrameCamera(raw_frame())
    detector = ColourDetector()
    monkeypatch.setattr(camera_module, "open_camera",
                        lambda index=None, **kwargs: (camera, 0))
    monkeypatch.setattr(landmarks, "HandDetector", lambda *a, **k: detector)
    monkeypatch.setattr(loader, "load_classifier",
                        lambda: lambda window: GestureState(method="unknown",
                                                            confidence=0.0))
    monkeypatch.setattr(server, "READ_FAILURES_ALLOWED", 1)
    monkeypatch.setattr(server, "READ_RETRY_S", 0.0)

    lesson = SpyLesson()
    server.camera_loop(lesson, threading.Event(), None, None, mirror)
    assert camera.released, "the webcam is always given back"
    return lesson, detector


def hand_x(fingers: list[dict], hand: str) -> float:
    xs = [f["x"] for f in fingers if f["hand"] == hand]
    assert xs, f"no fingers came out on the {hand} hand"
    return sum(xs) / len(xs)


def test_the_hand_tally_calls_left_is_the_child_s_own_left_hand(monkeypatch):
    """The whole point. The blue hand is the child's left by construction."""
    lesson, detector = run_camera_loop(monkeypatch)

    assert len(lesson.fingers) == 10, "five fingers on each of the two hands"
    seen = detector.frames[-1]
    # The child's left hand, blue, now on the left of the picture: a mirror.
    assert blob_x(seen, BLUE) == pytest.approx(1 - CHILD_LEFT_X, abs=0.02)
    assert blob_x(seen, RED) == pytest.approx(1 - CHILD_RIGHT_X, abs=0.02)

    left = hand_x(lesson.fingers, "left")
    right = hand_x(lesson.fingers, "right")
    assert left == pytest.approx(blob_x(seen, BLUE), abs=0.05), \
        "left must be the blue hand, which is the child's left, not the red one"
    assert right == pytest.approx(blob_x(seen, RED), abs=0.05)
    assert left < 0.5 < right, "and it is drawn on the left of the screen"


def bluer_half(jpeg: bytes) -> str:
    """Which half of an encoded frame holds the blue hand, JPEG noise and all."""
    frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    blueness = frame[:, :, 0].astype(int) - frame[:, :, 2].astype(int)
    half = frame.shape[1] // 2
    return "left" if blueness[:, :half].sum() > blueness[:, half:].sum() else "right"


def test_the_child_is_shown_the_very_frame_that_was_read(monkeypatch):
    """One flip serves the page and the perception, so they cannot disagree.

    The picture on screen has to be a mirror too: a child who raises their left
    hand and watches the right of the screen move stops trusting the lesson
    before Tally has said anything.
    """
    lesson, _ = run_camera_loop(monkeypatch)
    assert len(lesson.shown) == 1, "one frame read, one frame published"
    assert bluer_half(lesson.shown[0]) == "left", \
        "the child's left hand belongs on the left of the screen"

    unmirrored, _ = run_camera_loop(monkeypatch, mirror=False)
    assert bluer_half(unmirrored.shown[0]) == "right"


def test_without_the_flip_everything_inverts_together(monkeypatch):
    """What a camera that mirrors on its own does: the picture is not a mirror
    and the child is told the wrong hand. --no-mirror exists for the opposite
    case, a camera whose picture is already mirrored."""
    lesson, detector = run_camera_loop(monkeypatch, mirror=False)

    seen = detector.frames[-1]
    assert blob_x(seen, BLUE) == pytest.approx(CHILD_LEFT_X, abs=0.02)
    # The child's left hand is now on the right of the picture, and the name
    # follows it: this is exactly the report we are guarding against.
    assert hand_x(lesson.fingers, "left") < 0.5 < hand_x(lesson.fingers, "right")
    assert hand_x(lesson.fingers, "right") == pytest.approx(CHILD_LEFT_X, abs=0.05), \
        "unmirrored, the hand called right is the child's left hand"


def test_the_no_mirror_flag_reaches_the_app(monkeypatch):
    seen: dict[str, object] = {}

    def fake_create_app(**kwargs):
        seen.update(kwargs)
        return {server.STOP_KEY: threading.Event()}

    monkeypatch.setattr(server, "create_app", fake_create_app)
    monkeypatch.setattr(server, "load_env", lambda: None)
    monkeypatch.setattr(server, "enable_tutor", lambda: None)
    monkeypatch.setattr(server.web, "run_app", lambda *a, **k: None)

    assert server.main(["--no-open"]) == 0
    assert seen["mirror"] is True, "mirroring is what a webcam needs, by default"

    assert server.main(["--no-open", "--no-mirror"]) == 0
    assert seen["mirror"] is False
