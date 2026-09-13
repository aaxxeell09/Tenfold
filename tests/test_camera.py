"""app/camera.py: AVFoundation backend, device names, index probing, warmup.

Three cases that matter on this Mac. Index 0 is a virtual camera that opens fine
and hands out black frames forever, index 1 is the real one. With an iPhone in
the room, Continuity Camera offers the phone as a camera whose rear sensor
outshines the laptop, so the brightest camera is the wrong camera. And the built
in FaceTime camera is black for its first frames while the sensor exposes, so a
probe that reads those frames throws away the one camera the child is in front
of.

There is no camera and no macOS here, so the selection is driven through
injected device lists: names= for open_camera, and a fake system_profiler or a
fake sysfs for device_names itself. Time is injected too, through FakeClock: no
test may wait on the real clock. What no test here can prove is that the order
system_profiler prints is the order AVFoundation numbers.
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


class ScriptedCapture:
    """A capture that replays a list of frame brightnesses, then holds the last."""

    def __init__(self, values: list[float], opened: bool = True) -> None:
        self._values = list(values)
        self._opened = opened
        self.reads = 0
        self.released = False

    def isOpened(self) -> bool:
        return self._opened

    def read(self):
        value = self._values[min(self.reads, len(self._values) - 1)]
        self.reads += 1
        return True, np.full((4, 4, 3), value, dtype=np.uint8)

    def release(self) -> None:
        self.released = True


class FakeClock:
    """A clock the test drives: every reading of it moves on by step_ms."""

    def __init__(self, step_ms: float = 10.0) -> None:
        self._step_s = step_ms / 1000.0
        self.readings = 0

    def __call__(self) -> float:
        now = self.readings * self._step_s
        self.readings += 1
        return now


def open_camera(index: int | None = None, **kwargs):
    """cam.open_camera on a driven clock, so no test ever waits on a real one."""
    kwargs.setdefault("clock", FakeClock())
    return cam.open_camera(index, **kwargs)


def opener_for(devices: dict[int, object]):
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
    capture, index = open_camera(2, opener=opener, names={})
    assert index == 2
    assert capture is devices[2]
    assert [i for i, _ in opener.seen] == [2], "no other index should be touched"


def test_an_explicit_index_that_does_not_open_is_an_error():
    with pytest.raises(cam.CameraError, match="index 3"):
        open_camera(3, opener=opener_for({}), names={})


def test_auto_pick_skips_the_black_virtual_camera():
    """Index 0 opens and stays black, index 1 is the real camera."""
    devices = {0: FakeCapture(brightness=0.0), 1: FakeCapture(brightness=120.0)}
    opener = opener_for(devices)
    capture, index = open_camera(None, opener=opener, names={})
    assert index == 1
    assert capture is devices[1]
    assert devices[0].released, "the rejected camera must be released"
    assert not devices[1].released


def test_a_camera_that_is_black_at_first_is_not_rejected():
    """Real sensors need a few frames to expose. One read would reject this one."""
    devices = {0: FakeCapture(brightness=90.0, lit_after=10)}
    capture, index = open_camera(None, opener=opener_for(devices), names={})
    assert index == 0
    assert devices[0].reads == cam.WARMUP_FRAMES + cam.BRIGHTNESS_SAMPLES


def test_one_read_would_not_have_been_enough():
    """Guards the warmup itself: with a single frame the same camera looks black."""
    devices = {0: FakeCapture(brightness=90.0, lit_after=10)}
    with pytest.raises(cam.CameraError):
        open_camera(None, opener=opener_for(devices), warmup=1, names={})


def test_no_usable_camera_says_what_was_tried():
    devices = {0: FakeCapture(brightness=0.0), 2: FakeCapture(breaks=True)}
    with pytest.raises(cam.CameraError) as excinfo:
        open_camera(None, opener=opener_for(devices), names={})
    message = str(excinfo.value)
    assert "0 black" in message
    assert "1 absent" in message
    assert "--camera" in message


def test_probing_uses_the_platform_backend(monkeypatch):
    monkeypatch.setattr(cam.sys, "platform", "darwin")
    opener = opener_for({0: FakeCapture(brightness=200.0)})
    open_camera(None, opener=opener, names={})
    assert opener.seen == [(0, cv2.CAP_AVFOUNDATION)]


# --- device names: the platform is the only place they exist ----------------

MAC_PROFILER = """
{"SPCameraDataType": [
  {"_name": "FaceTime HD Camera", "spcamera_model-id": "Built-in"},
  {"_name": "Ilan's iPhone"}
]}
"""


def test_macos_names_come_from_system_profiler_in_listed_order():
    """Position in the list is the AVFoundation index. That is the whole mapping."""
    names = cam.device_names(platform="darwin", profiler=lambda: MAC_PROFILER)
    assert names == {0: "FaceTime HD Camera", 1: "Ilan's iPhone"}


def test_macos_names_survive_system_profiler_failing():
    """A timeout or a missing tool must cost names, never the camera."""

    def explode() -> str:
        raise OSError("system_profiler is not here")

    assert cam.device_names(platform="darwin", profiler=explode) == {}


def test_macos_names_survive_output_we_do_not_recognise():
    assert cam.device_names(platform="darwin", profiler=lambda: "not json") == {}
    assert cam.device_names(platform="darwin",
                            profiler=lambda: '{"SPCameraDataType": [{}, 7]}') == {}


def test_linux_names_come_from_sysfs_one_index_at_a_time():
    """Index N is video<N>, and an index with no file is simply not named."""
    files = {0: "Integrated Camera: Integrated C\n", 2: "Logitech StreamCam\n"}

    def sysfs(index: int) -> str:
        if index not in files:
            raise FileNotFoundError(index)
        return files[index]

    names = cam.device_names(indexes=(0, 1, 2), platform="linux", sysfs=sysfs)
    assert names == {0: "Integrated Camera: Integrated C", 2: "Logitech StreamCam"}


def test_an_unknown_platform_simply_has_no_names():
    assert cam.device_names(platform="win32") == {}


# --- the ranking, which is the rule the owner asked for ---------------------


def test_the_built_in_camera_is_ranked_before_everything_else():
    names = {0: "Studio Display Camera", 1: "FaceTime HD Camera"}
    order, skipped = cam.rank_devices((0, 1), names)
    assert order == [1, 0]
    assert skipped == []


def test_built_in_is_matched_case_insensitively_on_either_word():
    names = {0: "Some Capture Card", 1: "BUILT-IN iSight"}
    order, _ = cam.rank_devices((0, 1), names)
    assert order == [1, 0]


def test_an_iphone_is_never_ranked_at_all():
    names = {0: "Ilan's iPhone", 1: "FaceTime HD Camera"}
    order, skipped = cam.rank_devices((0, 1), names)
    assert order == [1]
    assert any("iPhone" in note for note in skipped)


def test_without_names_the_order_is_exactly_the_probe_order():
    """No platform, no names, and the code behaves as it did before all this."""
    assert cam.rank_devices((0, 1, 2, 3), {}) == ([0, 1, 2, 3], [])


# --- opening, with the devices injected -------------------------------------


def test_the_iphone_loses_even_though_it_is_first_and_brighter(capsys):
    """The bug: index 0 is the phone, it is the brightest, and it must not win."""
    devices = {0: FakeCapture(brightness=250.0), 1: FakeCapture(brightness=60.0)}
    opener = opener_for(devices)
    capture, index = open_camera(
        None, opener=opener, indexes=(0, 1),
        names={0: "Ilan's iPhone", 1: "FaceTime HD Camera"})
    assert index == 1
    assert capture is devices[1]
    assert [i for i, _ in opener.seen] == [1], "the phone is never even opened"
    assert "FaceTime HD Camera" in capsys.readouterr().out


def test_an_iphone_on_its_own_opens_nothing_and_says_why():
    """Better no lesson than a lesson filmed by a phone on the table, as long as
    the operator is told what happened and how to override it."""
    devices = {0: FakeCapture(brightness=250.0)}
    with pytest.raises(cam.CameraError) as excinfo:
        open_camera(None, opener=opener_for(devices), indexes=(0,),
                        names={0: "Ilan's iPhone"})
    message = str(excinfo.value)
    assert "iPhone" in message
    assert "--camera" in message, "the operator must be able to force it anyway"
    assert devices[0].reads == 0, "the phone is never even read from"


def test_brightness_still_decides_between_two_ordinary_cameras():
    """Neither name is built in and neither is a phone, so the probe rules."""
    devices = {0: FakeCapture(brightness=0.0), 1: FakeCapture(brightness=120.0)}
    capture, index = open_camera(
        None, opener=opener_for(devices), indexes=(0, 1),
        names={0: "OBS Virtual Camera", 1: "Logitech StreamCam"})
    assert index == 1
    assert capture is devices[1]


def test_with_no_names_at_all_the_old_behaviour_is_untouched():
    devices = {0: FakeCapture(brightness=0.0), 1: FakeCapture(brightness=120.0)}
    _, index = open_camera(None, opener=opener_for(devices), names={})
    assert index == 1


# --- --camera-name -----------------------------------------------------------


def test_camera_name_matches_a_substring_case_insensitively():
    devices = {0: FakeCapture(brightness=200.0), 1: FakeCapture(brightness=200.0)}
    opener = opener_for(devices)
    _, index = open_camera(
        None, name="streamcam", opener=opener, indexes=(0, 1),
        names={0: "FaceTime HD Camera", 1: "Logitech StreamCam"})
    assert index == 1, "the name overrides the built in preference"
    assert [i for i, _ in opener.seen] == [1]


def test_camera_name_can_even_ask_for_the_phone():
    """An override that cannot reach the phone would not be an override."""
    devices = {0: FakeCapture(brightness=200.0)}
    _, index = open_camera(None, name="iphone", opener=opener_for(devices),
                               indexes=(0,), names={0: "Ilan's iPhone"})
    assert index == 0


def test_camera_name_that_matches_nothing_is_an_error_that_lists_what_was_seen():
    with pytest.raises(cam.CameraError) as excinfo:
        open_camera(None, name="Dell", opener=opener_for({0: FakeCapture()}),
                        indexes=(0,), names={0: "FaceTime HD Camera"})
    message = str(excinfo.value)
    assert "Dell" in message
    assert "FaceTime HD Camera" in message


def test_camera_name_with_no_names_available_says_so_instead_of_guessing():
    with pytest.raises(cam.CameraError, match="no device names"):
        open_camera(None, name="FaceTime", opener=opener_for({0: FakeCapture()}),
                        names={})


def test_an_explicit_index_wins_over_a_name(capsys):
    """--camera is the blunter instrument and takes precedence over --camera-name."""
    devices = {0: FakeCapture(brightness=200.0), 1: FakeCapture(brightness=200.0)}
    opener = opener_for(devices)
    _, index = open_camera(0, name="StreamCam", opener=opener,
                               names={0: "FaceTime HD Camera",
                                      1: "Logitech StreamCam"})
    assert index == 0
    assert [i for i, _ in opener.seen] == [0]
    assert "FaceTime HD Camera" in capsys.readouterr().out


# --- the warm up, the bug the owner hit on his own Mac ------------------------


def test_a_camera_black_for_its_first_reads_is_warmed_up_and_then_measured(capsys):
    """The bug: the FaceTime camera is dark for a tenth of a second and was
    thrown away for it. The opening frames are discarded, the measurement lands
    on the frames that come after them."""
    devices = {0: FakeCapture(brightness=90.0, lit_after=8)}
    capture, index = open_camera(None, opener=opener_for(devices), names={})
    assert index == 0
    assert capture is devices[0]
    assert devices[0].reads == cam.WARMUP_FRAMES + cam.BRIGHTNESS_SAMPLES
    assert "brightness 90.0" in capsys.readouterr().out


def test_the_measurement_is_the_median_so_one_bright_frame_decides_nothing():
    """Four black frames and one flash is a black camera, not a working one."""
    dark = [0.0] * cam.WARMUP_FRAMES + [0.0, 0.0, 250.0, 0.0, 0.0]
    devices = {0: ScriptedCapture(dark)}
    with pytest.raises(cam.CameraError):
        open_camera(None, opener=opener_for(devices), indexes=(0,),
                    names={0: "OBS Virtual Camera"})
    assert devices[0].reads == cam.WARMUP_FRAMES + cam.BRIGHTNESS_SAMPLES


def test_the_measurement_is_the_median_so_one_black_frame_fails_nothing():
    """The same five frames the other way round: three lit out of five is lit."""
    lit = [0.0] * cam.WARMUP_FRAMES + [0.0, 250.0, 250.0, 250.0, 0.0]
    devices = {0: ScriptedCapture(lit)}
    _, index = open_camera(None, opener=opener_for(devices), indexes=(0,),
                           names={0: "OBS Virtual Camera"})
    assert index == 0


def test_median_brightness_reads_exactly_the_samples_it_is_asked_for():
    capture = ScriptedCapture([10.0, 20.0, 30.0, 200.0, 200.0])
    assert cam.median_brightness(capture) == 30.0
    assert capture.reads == cam.BRIGHTNESS_SAMPLES


def test_a_built_in_camera_that_stays_dark_is_still_the_one(capsys):
    """Point 3: the name preference cannot be undone by a black reading. A lens
    under a thumb is fixed by moving the thumb, not by using the other camera."""
    devices = {0: FakeCapture(brightness=0.0), 1: FakeCapture(brightness=200.0)}
    capture, index = open_camera(
        None, opener=opener_for(devices), indexes=(0, 1),
        names={0: "FaceTime HD Camera", 1: "Logitech StreamCam"})
    assert index == 0
    assert capture is devices[0]
    assert not devices[0].released, "the camera we are using stays open"
    assert devices[1].reads == 0, "no other camera is even considered"
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "still reads dark" in out
    assert "FaceTime HD Camera" in out


def test_a_camera_that_is_not_built_in_and_stays_dark_is_still_rejected():
    """The exception is the built in camera and nothing else."""
    devices = {0: FakeCapture(brightness=0.0)}
    with pytest.raises(cam.CameraError) as excinfo:
        open_camera(None, opener=opener_for(devices), indexes=(0,),
                    names={0: "OBS Virtual Camera"})
    assert "OBS Virtual Camera" in str(excinfo.value)
    assert devices[0].released


def test_the_warm_up_gives_up_on_the_clock_not_on_the_frame_count():
    """A device that opens and never delivers must cost 600 ms, not the lesson."""
    capture = FakeCapture(breaks=True)
    clock = FakeClock(step_ms=100.0)
    assert cam.warm_up(capture, clock=clock) == 0
    assert capture.reads == 5, "600 ms of 100 ms steps, then it gives up"
    assert capture.reads < cam.WARMUP_FRAMES, "the frame count was never reached"


def test_the_warm_up_stops_at_the_frame_count_when_frames_do_arrive():
    """The other half of whichever comes first: a live camera is not delayed."""
    capture = FakeCapture(brightness=90.0)
    clock = FakeClock(step_ms=1.0)
    assert cam.warm_up(capture, clock=clock) == cam.WARMUP_FRAMES
    assert capture.reads == cam.WARMUP_FRAMES


def test_a_camera_that_never_delivers_a_frame_is_an_error_and_not_a_hang():
    """Straight through open_camera, on a clock that is driven and not waited on."""
    devices = {0: FakeCapture(breaks=True)}
    with pytest.raises(cam.CameraError):
        open_camera(None, opener=opener_for(devices), indexes=(0,),
                    names={0: "Logitech StreamCam"}, clock=FakeClock(step_ms=100.0))
    assert devices[0].reads == 5 + cam.BRIGHTNESS_SAMPLES


# --- the server wiring -------------------------------------------------------


def test_the_camera_name_flag_reaches_the_app(monkeypatch):
    """--camera-name on the command line, all the way to create_app."""
    import threading

    from app import server

    seen: dict[str, object] = {}

    def fake_create_app(**kwargs):
        seen.update(kwargs)
        return {server.STOP_KEY: threading.Event()}

    monkeypatch.setattr(server, "create_app", fake_create_app)
    monkeypatch.setattr(server, "load_env", lambda: None)
    monkeypatch.setattr(server, "enable_tutor", lambda: None)
    monkeypatch.setattr(server.web, "run_app", lambda *a, **k: None)

    assert server.main(["--camera-name", "FaceTime", "--camera", "2",
                        "--no-open"]) == 0
    assert seen["camera_name"] == "FaceTime"
    assert seen["camera"] == 2, "both flags travel, open_camera decides"


def test_the_camera_thread_hands_the_name_to_open_camera(monkeypatch):
    """The thread is what actually opens the device, so the name has to get there."""
    import threading

    import app.camera as camera_module
    from app import server
    from lesson.engine import Engine

    asked: dict[str, object] = {}

    def fake_open(index=None, **kwargs):
        asked["index"] = index
        asked.update(kwargs)
        raise camera_module.CameraError("no camera here")

    monkeypatch.setattr(camera_module, "open_camera", fake_open)

    lesson = server.Lesson(Engine(), server.Hub(), server.make_scheduler)
    stop = threading.Event()
    stop.set()                      # the fallback loop returns straight away
    server.camera_loop(lesson, stop, None, "FaceTime")

    assert asked == {"index": None, "name": "FaceTime"}


def test_the_camera_thread_without_a_name_calls_open_camera_as_before(monkeypatch):
    import threading

    import app.camera as camera_module
    from app import server
    from lesson.engine import Engine

    asked: dict[str, object] = {}

    def fake_open(index=None, **kwargs):
        asked["index"] = index
        asked.update(kwargs)
        raise camera_module.CameraError("no camera here")

    monkeypatch.setattr(camera_module, "open_camera", fake_open)

    lesson = server.Lesson(Engine(), server.Hub(), server.make_scheduler)
    stop = threading.Event()
    stop.set()
    server.camera_loop(lesson, stop, 3)

    assert asked == {"index": 3}, "no name means the call it has always made"
