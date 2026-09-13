"""data/capture.py stays alive when the camera dies and exits cleanly on Ctrl-C.

Two findings from the area 4 audit:

G2, a camera that opens and then stops delivering frames used to leave
_wait_for_block in a silent busy loop: no window update, so no key event, so the
q key was unreachable and only Ctrl-C got out. It now sleeps between failed
reads, still reads the keyboard, and gives up after a bounded number of failures.

G3, Ctrl-C ended a 40 minute session in a traceback. It now releases the camera,
closes the window, says how many windows were written and returns 130. The dry
run, which sleeps through the whole schedule, is interruptible the same way.

No camera and no MediaPipe here: the capture object, the detector and the cv2
calls that need a window are all injected.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import cv2
import numpy as np
import pytest

from data.capture import (
    EXIT_INTERRUPTED,
    MAX_READ_FAILURES,
    SampleWriter,
    _wait_for_block,
    build_schedule,
    main,
    run_capture,
)
import data.capture as capture


def _frame() -> np.ndarray:
    return np.zeros((240, 320, 3), dtype=np.uint8)


class FakeCamera:
    """A capture object that replays a scripted list of read() results."""

    def __init__(self, reads: list[bool] | Iterator[bool], loop_value: bool = False) -> None:
        self.reads = list(reads)
        self.loop_value = loop_value
        self.read_count = 0
        self.released = False

    def read(self) -> tuple[bool, Any]:
        ok = self.reads[self.read_count] if self.read_count < len(self.reads) else self.loop_value
        self.read_count += 1
        return ok, (_frame() if ok else None)

    def release(self) -> None:
        self.released = True


class InterruptingCamera:
    """A capture object where the operator presses Ctrl-C on the first read."""

    def __init__(self) -> None:
        self.released = False

    def read(self) -> tuple[bool, Any]:
        raise KeyboardInterrupt

    def release(self) -> None:
        self.released = True


class FakeDetector:
    def __init__(self, num_hands: int = 2) -> None:
        self.num_hands = num_hands
        self.closed = False

    def detect(self, frame: Any) -> list[Any]:
        return []

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def headless(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """cv2 without a window, and time.sleep without the wait."""
    calls: dict[str, list[Any]] = {"sleep": [], "imshow": [], "destroy": [], "keys": []}

    monkeypatch.setattr(capture.time, "sleep", lambda seconds: calls["sleep"].append(seconds))
    monkeypatch.setattr(cv2, "imshow", lambda name, frame: calls["imshow"].append(name))
    monkeypatch.setattr(cv2, "destroyAllWindows", lambda: calls["destroy"].append(True))
    return calls


def _keys(monkeypatch: pytest.MonkeyPatch, calls: dict[str, list[Any]],
          sequence: list[int]) -> None:
    """Scripted keyboard: 255 is the no key value cv2.waitKey returns on a timeout."""
    pending = list(sequence)

    def waitKey(delay: int) -> int:
        key = pending.pop(0) if pending else 255
        calls["keys"].append(key)
        return key

    monkeypatch.setattr(cv2, "waitKey", waitKey)


# --- G2, the camera stops delivering -----------------------------------------


def test_a_silent_camera_gives_up_instead_of_spinning(headless, monkeypatch, capsys):
    _keys(monkeypatch, headless, [])
    camera = FakeCamera([], loop_value=False)

    assert _wait_for_block(camera, "front", "near") is False
    assert camera.read_count == MAX_READ_FAILURES, "bounded, not forever"
    # One sleep per failed read except the last, so the loop never spins hot.
    assert len(headless["sleep"]) == MAX_READ_FAILURES - 1
    assert all(seconds == capture.READ_FAIL_SLEEP_S for seconds in headless["sleep"])

    err = capsys.readouterr().err
    assert "stopped delivering frames" in err
    assert "giving up" in err


def test_q_still_quits_while_the_camera_is_silent(headless, monkeypatch, capsys):
    """The whole point of G2: the key has to be read on the failing path too."""
    _keys(monkeypatch, headless, [255, 255, ord("q")])
    camera = FakeCamera([], loop_value=False)

    assert _wait_for_block(camera, "front", "near") is False
    assert camera.read_count == 3, "quit as soon as q is pressed"
    assert "giving up" not in capsys.readouterr().err


def test_the_failure_count_resets_when_frames_come_back(headless, monkeypatch):
    """A camera that drops a few frames and recovers must not end the block."""
    almost = MAX_READ_FAILURES - 1
    camera = FakeCamera([False] * almost + [True] + [False] * almost + [True])
    # waitKey runs on both paths, so the space is pressed on the very last frame.
    last = 2 * almost + 2
    monkeypatch.setattr(
        cv2, "waitKey", lambda delay: ord(" ") if camera.read_count >= last else 255
    )

    assert _wait_for_block(camera, "side", "far") is True
    assert camera.read_count == last, "two near misses did not end the block"


def test_a_healthy_camera_is_untouched(headless, monkeypatch):
    _keys(monkeypatch, headless, [255, ord(" ")])
    camera = FakeCamera([], loop_value=True)
    assert _wait_for_block(camera, "front", "near") is True
    assert headless["sleep"] == [], "no sleeping when frames arrive"
    assert headless["imshow"], "the banner is still drawn"

    _keys(monkeypatch, headless, [ord("q")])
    assert _wait_for_block(FakeCamera([], loop_value=True), "front", "near") is False


# --- G3, Ctrl-C ---------------------------------------------------------------


def test_ctrl_c_during_capture_releases_everything_and_reports(
    headless, monkeypatch, tmp_path: Path, capsys
):
    _keys(monkeypatch, headless, [])
    monkeypatch.setattr("app.landmarks.HandDetector", FakeDetector)

    seen: dict[str, Any] = {}

    def open_camera(index: int | None) -> tuple[Any, int]:
        camera = InterruptingCamera()
        seen["camera"] = camera
        return camera, 0

    monkeypatch.setattr(capture, "open_camera", open_camera)

    writer = SampleWriter(tmp_path / "samples.jsonl", "train")
    try:
        code = run_capture("s1", "axel", build_schedule(), writer, resume=False,
                           blocks=[("front", "near")], camera_index=None)
    finally:
        writer.close()

    assert code == EXIT_INTERRUPTED
    assert seen["camera"].released, "the camera is released"
    assert headless["destroy"], "the window is closed"

    out = capsys.readouterr().out
    assert "interrupted" in out
    assert "wrote 0 train windows" in out, "says what was captured, not a traceback"


def test_the_dry_run_is_interruptible(monkeypatch, capsys):
    """Ctrl-C during the 21 minutes of --dry-run sleeps exits 130, not a traceback."""

    def interrupt(seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(capture.time, "sleep", interrupt)

    assert main(["--dry-run"]) == EXIT_INTERRUPTED
    assert "dry run interrupted" in capsys.readouterr().out
