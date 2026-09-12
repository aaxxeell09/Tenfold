"""app/camera.py: AVFoundation backend, index probing, warmup.

The case that matters on this Mac: index 0 is a virtual camera that opens fine
and hands out black frames forever, index 1 is the real one.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app import camera as cam


class FakeCapture:
    """A capture whose frames go from dark to bright after lit_after reads."""

    def __init__(self, opened: bool = True, brightness: float = 0.0,
                 lit_after: int = 0, breaks: bool = False) -> None:
        self._opened = opened
        self._brightness = brightness
        self._lit_after = lit_after
        self._breaks = breaks
        self.reads = 0
        self.released = False

    def isOpened(self) -> bool:
        return self._opened

    def read(self):
        self.reads += 1
        if self._breaks:
            return False, None
        value = self._brightness if self.reads > self._lit_after else 0.0
        return True, np.full((4, 4, 3), value, dtype=np.uint8)

    def release(self) -> None:
        self.released = True


def opener_for(devices: dict[int, FakeCapture]):
    """An opener that hands out the scripted devices and records the backend."""
    seen: list[tuple[int, int]] = []

    def opener(index: int, api: int):
        seen.append((index, api))
        return devices.get(index, FakeCapture(opened=False))

    opener.seen = seen  # type: ignore[attr-defined]
    return opener


def test_backend_is_avfoundation_on_macos(monkeypatch):
    monkeypatch.setattr(cam.sys, "platform", "darwin")
    assert cam.backend() == cv2.CAP_AVFOUNDATION


def test_backend_is_left_alone_elsewhere(monkeypatch):
    monkeypatch.setattr(cam.sys, "platform", "linux")
    assert cam.backend() == cv2.CAP_ANY


def test_an_explicit_index_is_taken_at_face_value():
    """A dark room is not a reason to quietly use another camera."""
    devices = {2: FakeCapture(brightness=0.0)}
    opener = opener_for(devices)
    capture, index = cam.open_camera(2, opener=opener)
    assert index == 2
    assert capture is devices[2]
    assert [i for i, _ in opener.seen] == [2], "no other index should be touched"


def test_an_explicit_index_that_does_not_open_is_an_error():
    with pytest.raises(cam.CameraError, match="index 3"):
        cam.open_camera(3, opener=opener_for({}))


def test_auto_pick_skips_the_black_virtual_camera():
    """Index 0 opens and stays black, index 1 is the real camera."""
    devices = {0: FakeCapture(brightness=0.0), 1: FakeCapture(brightness=120.0)}
    opener = opener_for(devices)
    capture, index = cam.open_camera(None, opener=opener)
    assert index == 1
    assert capture is devices[1]
    assert devices[0].released, "the rejected camera must be released"
    assert not devices[1].released


def test_a_camera_that_is_black_at_first_is_not_rejected():
    """Real sensors need a few frames to expose. One read would reject this one."""
    devices = {0: FakeCapture(brightness=90.0, lit_after=10)}
    capture, index = cam.open_camera(None, opener=opener_for(devices))
    assert index == 0
    assert devices[0].reads == cam.WARMUP_FRAMES


def test_one_read_would_not_have_been_enough():
    """Guards the warmup itself: with a single frame the same camera looks black."""
    devices = {0: FakeCapture(brightness=90.0, lit_after=10)}
    with pytest.raises(cam.CameraError):
        cam.open_camera(None, opener=opener_for(devices), warmup=1)


def test_no_usable_camera_says_what_was_tried():
    devices = {0: FakeCapture(brightness=0.0), 2: FakeCapture(breaks=True)}
    with pytest.raises(cam.CameraError) as excinfo:
        cam.open_camera(None, opener=opener_for(devices))
    message = str(excinfo.value)
    assert "0 black" in message
    assert "1 absent" in message
    assert "--camera" in message


def test_probing_uses_the_platform_backend(monkeypatch):
    monkeypatch.setattr(cam.sys, "platform", "darwin")
    opener = opener_for({0: FakeCapture(brightness=200.0)})
    cam.open_camera(None, opener=opener)
    assert opener.seen == [(0, cv2.CAP_AVFOUNDATION)]
