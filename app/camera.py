"""Opening the webcam, SPEC.md section 4. Frames stay in memory, nothing is written.

Three macOS problems are handled here, once, for every script that needs a camera.

The backend: without an explicit one OpenCV can fall back to FFMPEG, which
cannot enumerate AVFoundation devices and reports no camera at all. On macOS the
capture is opened with cv2.CAP_AVFOUNDATION. Elsewhere the default backend is
left alone, so the capture scripts still run on Linux.

The index: index 0 is not always the real camera. A virtual camera, Continuity
Camera or a screen sharing device can sit in front of it and hand out black
frames forever. So when no index is given, the indexes are probed in order and
the first one that actually produces a lit image wins.

The warmup: a real sensor needs a moment to expose, so its first frames are
black. A probe that reads them throws away a camera that is about to work
perfectly well, which is exactly what happened to a FaceTime HD camera on the
owner's Mac. So every candidate is warmed up first, its opening frames are
discarded, and only then is brightness measured, on the median of several
frames so that neither one late black frame nor one bright flash decides alone.
And the built in camera is never rejected for being dark: if it is there, it is
the camera the child is looking at, so it is used and the darkness is logged as
a warning instead of a refusal.

The phone: with an iPhone nearby, Continuity Camera offers it as an ordinary
camera, and its rear sensor is brighter than any laptop webcam, so brightness
alone hands the lesson to a camera pointing at the ceiling. cv2.VideoCapture
takes an index and tells us nothing about what it opened, so the names come
from the platform (device_names) and decide the probe order (rank_devices):
built in first, never the phone, brightness only between whatever is left.
"""

from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import cv2

PROBE_INDEXES: tuple[int, ...] = (0, 1, 2, 3)
MIN_BRIGHTNESS = 5.0

# Constants of the camera path, not tutor policy, so they are named here and not
# in lesson/tutor_params.json. The opening frames of a device are discarded:
# WARMUP_FRAMES of them, or WARMUP_MS milliseconds of trying, whichever ends
# first. The deadline is not a nicety, it is what stops a device that opens and
# never delivers a frame from holding the probe for ever. Brightness is then the
# median of BRIGHTNESS_SAMPLES frames, so one outlier in either direction cannot
# carry the decision on its own.
WARMUP_FRAMES = 10
WARMUP_MS = 600.0
BRIGHTNESS_SAMPLES = 5

# Matched case insensitively as substrings of the device name.
BUILT_IN_HINTS: tuple[str, ...] = ("facetime", "built-in")
BLOCKED_HINTS: tuple[str, ...] = ("iphone",)

SYSTEM_PROFILER: tuple[str, ...] = ("system_profiler", "SPCameraDataType", "-json")
PROFILER_TIMEOUT_S = 5.0
V4L_NAME = "/sys/class/video4linux/video{index}/name"


class CameraError(RuntimeError):
    """No usable camera. The message says what was tried."""


def backend() -> int:
    """AVFoundation on macOS, the OpenCV default everywhere else."""
    return cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY


def _open(index: int, api: int) -> Any:
    return cv2.VideoCapture(index, api)


def _profiler_json() -> str:
    """The macOS camera inventory, as JSON text."""
    done = subprocess.run(SYSTEM_PROFILER, capture_output=True, text=True,
                          timeout=PROFILER_TIMEOUT_S, check=True)
    return done.stdout


def _sysfs_name(index: int) -> str:
    """The Linux name of /dev/video<index>."""
    return Path(V4L_NAME.format(index=index)).read_text()


def device_names(
    indexes: Sequence[int] = PROBE_INDEXES,
    platform: str | None = None,
    profiler: Callable[[], str] = _profiler_json,
    sysfs: Callable[[int], str] = _sysfs_name,
) -> dict[int, str]:
    """Index to device name, since OpenCV hands out indexes and nothing else.

    macOS: system_profiler lists the cameras in the order AVFoundation gives
    them indexes, so position in that list is the index.
    Linux: /sys/class/video4linux/video<N>/name is the name of index N.

    Anywhere else, and on any failure at all (no such tool, a timeout, JSON we
    do not recognise, no permission), this returns nothing and every caller
    falls back to the behaviour it had before names existed. This is the part
    most likely to be wrong on a machine we have never seen, so it is never
    allowed to be the reason a camera does not open.
    """
    system = sys.platform if platform is None else platform
    try:
        if system == "darwin":
            listed = json.loads(profiler()).get("SPCameraDataType", [])
            return {
                index: str(entry["_name"]).strip()
                for index, entry in enumerate(listed)
                if isinstance(entry, dict) and str(entry.get("_name", "")).strip()
            }
        if system.startswith("linux"):
            found: dict[int, str] = {}
            for index in indexes:
                try:
                    name = sysfs(index).strip()
                except OSError:
                    continue
                if name:
                    found[index] = name
            return found
    except Exception:
        return {}
    return {}


def rank_devices(
    indexes: Sequence[int],
    names: Mapping[int, str],
    wanted: str | None = None,
) -> tuple[list[int], list[str]]:
    """The order to probe in, and a note for every device ruled out.

    Built in cameras first, then the devices we know nothing about, and never
    an iPhone. Brightness is not consulted here at all: it only decides between
    the devices this leaves, which is exactly what open_camera already does.

    With wanted set, only the devices whose name contains it (case
    insensitively) survive, in index order. An index nobody has a name for
    cannot match a name, so it is left out.
    """
    if wanted:
        needle = wanted.casefold()
        keep = [i for i in indexes if needle in names.get(i, "").casefold()]
        skipped = [f"{i} is {names.get(i, 'unnamed')}"
                   for i in indexes if i not in keep]
        return keep, skipped

    built_in: list[int] = []
    unknown: list[int] = []
    skipped: list[str] = []
    for index in indexes:
        name = names.get(index)
        if name is None:
            unknown.append(index)
            continue
        folded = name.casefold()
        if any(hint in folded for hint in BLOCKED_HINTS):
            skipped.append(f"{index} {name} is a phone, skipped")
        elif any(hint in folded for hint in BUILT_IN_HINTS):
            built_in.append(index)
        else:
            unknown.append(index)
    return built_in + unknown, skipped


def warm_up(
    capture: Any,
    frames: int = WARMUP_FRAMES,
    budget_ms: float = WARMUP_MS,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Read and throw away the opening frames. Returns how many really arrived.

    Stops at `frames` frames or once `budget_ms` milliseconds have passed,
    whichever comes first, so a camera that never returns anything costs the
    probe a fixed fraction of a second and not the whole lesson.
    """
    deadline = clock() + budget_ms / 1000.0
    seen = 0
    while seen < frames and clock() < deadline:
        ok, frame = capture.read()
        if ok and frame is not None:
            seen += 1
    return seen


def median_brightness(capture: Any, samples: int = BRIGHTNESS_SAMPLES) -> float:
    """Median mean brightness over the next frames, 0.0 if none arrive at all.

    The median, not the maximum: a single bright flash on an otherwise black
    device must not pass for a working camera, and a single black frame from a
    working one must not fail it.
    """
    values: list[float] = []
    for _ in range(samples):
        ok, frame = capture.read()
        if ok and frame is not None:
            values.append(float(frame.mean()))
    return float(statistics.median(values)) if values else 0.0


def probe_brightness(
    capture: Any,
    frames: int = WARMUP_FRAMES,
    budget_ms: float = WARMUP_MS,
    samples: int = BRIGHTNESS_SAMPLES,
    clock: Callable[[], float] = time.monotonic,
) -> float:
    """Warm the device up, then measure it. The whole probe, in one call."""
    warm_up(capture, frames, budget_ms, clock)
    return median_brightness(capture, samples)


def _is_built_in(name: str | None) -> bool:
    """Is this the machine's own camera, by name? An unnamed device is not."""
    folded = (name or "").casefold()
    return any(hint in folded for hint in BUILT_IN_HINTS)


def _label(names: Mapping[int, str], index: int) -> str:
    name = names.get(index)
    return f" ({name})" if name else ""


def _nothing_to_probe(wanted: str | None, names: Mapping[int, str],
                      skipped: Sequence[str]) -> str:
    """Why the probe never started. The operator has to be able to act on this."""
    seen = ", ".join(f"{i}: {n}" for i, n in sorted(names.items()))
    if wanted:
        return (f'no camera name contains "{wanted}"'
                + (f", saw {seen}" if seen else ", and no device names are available")
                + ". Drop --camera-name, or pass --camera N for a raw index.")
    return (
        "the only cameras found are phones, not the built in one ("
        + ", ".join(skipped)
        + "). Move the iPhone away or turn Continuity Camera off, or pass "
        "--camera N to use the phone on purpose."
    )


def open_camera(
    index: int | None = None,
    name: str | None = None,
    opener: Callable[[int, int], Any] = _open,
    indexes: Sequence[int] = PROBE_INDEXES,
    warmup: int = WARMUP_FRAMES,
    min_brightness: float = MIN_BRIGHTNESS,
    names: Mapping[int, str] | None = None,
    warmup_ms: float = WARMUP_MS,
    samples: int = BRIGHTNESS_SAMPLES,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[Any, int]:
    """Return an open capture and the index it came from.

    An explicit index wins over everything, including name: it is taken at face
    value and never probed, because if the operator asked for camera 2, a dark
    room is not a reason to silently use another one.

    With no index, the devices are probed in the order of rank_devices, and the
    first one that opens and, once warmed up, gives a median brightness above
    min_brightness wins. So a built in camera is preferred, a phone is never
    picked, and brightness only separates the rest.

    The built in camera is never rejected for being dark. The name preference
    put it first, and a dark reading cannot undo that: it is kept, with a
    warning, because a lens the child has covered with a thumb is still the
    right camera and it is fixed by moving the thumb, not by using another one.

    Raises CameraError when nothing usable is left.
    """
    api = backend()
    known = device_names(indexes) if names is None else dict(names)

    if index is not None:
        capture = opener(index, api)
        if not capture.isOpened():
            capture.release()
            raise CameraError(f"no camera on index {index}")
        print(f"camera: using index {index} as asked{_label(known, index)}")
        return capture, index

    order, tried = rank_devices(indexes, known, name)
    if not order:
        raise CameraError(_nothing_to_probe(name, known, tried))

    tried = list(tried)
    for candidate in order:
        capture = opener(candidate, api)
        label = _label(known, candidate)
        if not capture.isOpened():
            capture.release()
            tried.append(f"{candidate} absent")
            continue
        brightness = probe_brightness(capture, warmup, warmup_ms, samples, clock)
        if brightness > min_brightness:
            print(f"camera: picked index {candidate}{label} "
                  f"(brightness {brightness:.1f})")
            return capture, candidate
        if _is_built_in(known.get(candidate)):
            print(f"camera: WARNING index {candidate}{label} still reads dark "
                  f"(brightness {brightness:.1f}) after warm up, using the "
                  f"built in camera anyway")
            return capture, candidate
        capture.release()
        tried.append(f"{candidate}{label} black ({brightness:.1f})")

    raise CameraError(
        "no camera produced a lit image, tried " + ", ".join(tried)
        + ". Pass --camera N to force one, and check the camera permission "
        "for your terminal in System Settings, Privacy and Security."
    )
