"""Dataset capture, SPEC.md 5.5 and 5.6 with amendment F4.

Records continuously and labels by construction, so no sample is ever annotated
by hand or by a model. One step of the schedule is 4.5 seconds:

    0.0 to 3.0 s   prep    frames labelled transition
    3.0 to 3.5 s   drop    frames discarded, not labelled (F4)
    3.5 to 4.5 s   hold    frames labelled with the class

The prompt always names the side explicitly, in mirror view, for example
"LEFT hand 8, RIGHT hand 7" (G9): the ordered left and right labels are then
correct by construction and the 25 ordered classes stay meaningful.

Each block is one camera angle at one distance. The script tells you when to
move, and waits. Three angles by two distances.

Windows are non overlapping, stride 5, written one JSON line per window in the
shape classifier/schema.py documents, and flushed immediately so a crash costs
at most one window. Transition windows are kept at one in five, otherwise they
would drown the real classes.

Train and held out never share a file. SPEC.md 5.5 and amendment F1 split by
person: train is Axel, all angles, in data/samples.jsonl; the held out sessions
are recorded by 3 to 5 other people and written to ../tenfold-heldout/test.jsonl
outside this repo, mode 700. The side angle stays a stress metric reported on
its own, never a split.

Usage:
    python data/capture.py --session axel_1 --person axel
    python data/capture.py --session axel_1 --person axel --resume
    python data/capture.py --session guest_3 --person guest_3 --heldout
    python data/capture.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.normalize import Normalizer  # noqa: E402
from classifier.schema import WINDOW_SIZE, Window, window_to_json  # noqa: E402

SAMPLES_PATH = REPO_ROOT / "data" / "samples.jsonl"
# Held out data lives outside the repo and outside both critic worktrees (F1).
HELDOUT_PATH = REPO_ROOT.parent / "tenfold-heldout" / "test.jsonl"
HELDOUT_DIR_MODE = 0o700
HELDOUT_FILE_MODE = 0o600

PREP_S = 3.0
DROP_S = 0.5
KEEP_S = 1.0
STEP_S = PREP_S + DROP_S + KEEP_S

ANGLES = ("front", "top", "side")
DISTANCES = ("near", "far")

# One transition window kept out of this many.
TRANSITION_KEEP_ONE_IN = 5

FINGERS = (6, 7, 8, 9, 10)
NEAR_CONTACT_PAIRS = (
    (6, 6), (6, 10), (7, 7), (7, 9), (8, 7),
    (8, 8), (9, 6), (9, 10), (10, 8), (10, 10),
)

PHASE_PREP = "prep"
PHASE_DROP = "drop"
PHASE_HOLD = "hold"
PHASE_DONE = "done"

UNKNOWN_LABEL: dict[str, Any] = {"method": "unknown"}
TRANSITION_KIND = "transition"
TRANSITION_CLASS = "transition"


@dataclass(frozen=True)
class Item:
    """One step of the schedule: what to show, and how it will be labelled.

    cls only names the hold in hold_id. The evaluation derives the class it
    scores from kind and label, through eval/scorers.class_of.
    """

    cls: str
    kind: str
    prompt: str
    label: dict[str, Any]


def build_schedule() -> list[Item]:
    """The fixed order: 25 ordered combinations, rest, near contact, partial
    hand, then hands in and out of frame."""
    items: list[Item] = []
    for left in FINGERS:
        for right in FINGERS:
            items.append(
                Item(
                    cls=f"{left}x{right}",
                    kind="positive",
                    prompt=f"LEFT hand {left}, RIGHT hand {right}, touch them tip to tip",
                    label={"method": "6-10", "left": left, "right": right, "contact": True},
                )
            )
    items.append(
        Item(cls="rest", kind="rest", prompt="REST, both hands down and relaxed",
             label=dict(UNKNOWN_LABEL))
    )
    for left, right in NEAR_CONTACT_PAIRS:
        items.append(
            Item(
                cls=f"near{left}x{right}",
                kind="near_contact",
                prompt=f"LEFT hand {left}, RIGHT hand {right}, about 2 cm apart, DO NOT touch",
                label={"method": "6-10", "left": left, "right": right, "contact": False},
            )
        )
    for finger in FINGERS:
        items.append(
            Item(
                cls=f"partial{finger}",
                kind="partial_hand",
                prompt=f"RIGHT hand only, show finger {finger}, LEFT hand out of frame",
                label=dict(UNKNOWN_LABEL),
            )
        )
    for n in range(1, 6):
        items.append(
            Item(
                cls=f"outofframe{n}",
                kind="out_of_frame",
                prompt=f"Move BOTH hands out of frame, then back in ({n} of 5)",
                label=dict(UNKNOWN_LABEL),
            )
        )
    return items


# --- the labelling rule, kept pure so it can be tested without a camera -----


def phase_at(elapsed: float) -> str:
    """Which phase of one step a time offset falls in."""
    if elapsed < PREP_S:
        return PHASE_PREP
    if elapsed < PREP_S + DROP_S:
        return PHASE_DROP
    if elapsed < STEP_S:
        return PHASE_HOLD
    return PHASE_DONE


def label_for(phase: str, item: Item) -> tuple[str, str, dict[str, Any]] | None:
    """The (class, kind, label) a frame gets in this phase, or None when it is
    thrown away. F4: the first 0.5 s of the hold is discarded, not labelled
    transition, because the hand is still settling into the pose."""
    if phase == PHASE_PREP:
        return TRANSITION_CLASS, TRANSITION_KIND, dict(UNKNOWN_LABEL)
    if phase == PHASE_HOLD:
        return item.cls, item.kind, dict(item.label)
    return None


def hold_id(session: str, cls: str, angle: str, distance: str, step: int) -> str:
    """Groups the windows of one hold, so accuracy can be scored by hold."""
    return f"{session}:{cls}:{angle}:{distance}:{step}"


def timeline(step_seconds: float = STEP_S, fps: float = 30.0) -> Iterator[tuple[float, str]]:
    """Every frame time of one step with its phase. Used by the tests and by
    --dry-run to count what a step is worth."""
    frames = int(round(step_seconds * fps))
    for index in range(frames):
        elapsed = index / fps
        yield elapsed, phase_at(elapsed)


# --- writing ----------------------------------------------------------------


def resolve_target(heldout: bool) -> tuple[Path, str]:
    """Where the rows go and what split they carry. The two never mix."""
    return (HELDOUT_PATH, "test") if heldout else (SAMPLES_PATH, "train")


class SampleWriter:
    """Appends one JSON line per window and flushes every time.

    The split is decided by the destination, never by the angle: everything in
    data/samples.jsonl is train, everything in ../tenfold-heldout/test.jsonl is
    test. angle is on every row, so the side angle can still be reported on its
    own as the stress metric SPEC.md 5.5 asks for.
    """

    def __init__(self, path: Path = SAMPLES_PATH, split: str = "train") -> None:
        self.path = path
        self.split = split
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if split == "test":
            os.chmod(self.path.parent, HELDOUT_DIR_MODE)
        self.done, self.next_index = self._read_existing()
        self._handle = open(self.path, "a", encoding="utf-8")
        if split == "test":
            os.chmod(self.path, HELDOUT_FILE_MODE)
        self.written = 0

    def _read_existing(self) -> tuple[set[str], int]:
        done: set[str] = set()
        count = 0
        if not self.path.exists():
            return done, 0
        with open(self.path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                count += 1
                if row.get("hold_id"):
                    done.add(row["hold_id"])
        return done, count

    def already_done(self, hold: str) -> bool:
        return hold in self.done

    def write(self, session: str, person: str, angle: str, distance: str, step: int,
              cls: str, kind: str, label: dict[str, Any], window: Window) -> None:
        row = {
            "id": f"s{self.next_index:06d}",
            "hold_id": hold_id(session, cls, angle, distance, step),
            "session": session,
            "person": person,
            "angle": angle,
            "distance": distance,
            "kind": kind,
            "label": label,
            "split": self.split,
            "window": window_to_json(window),
        }
        self._handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        self._handle.flush()
        self.next_index += 1
        self.written += 1

    def close(self) -> None:
        self._handle.close()


# --- capture ----------------------------------------------------------------


def run_dry(schedule: list[Item], fps: float = 30.0) -> None:
    """Rehearse the prompts and the timing with no camera and no file."""
    per_step = sum(1 for _, phase in timeline(fps=fps) if phase == PHASE_HOLD) // WINDOW_SIZE
    blocks = len(ANGLES) * len(DISTANCES)
    print(f"dry run: {len(schedule)} steps per block, {blocks} blocks, "
          f"{STEP_S:.1f} s per step")
    print(f"expected windows: about {per_step * len(schedule) * blocks} class windows "
          f"at {fps:.0f} fps, plus transitions at one in {TRANSITION_KEEP_ONE_IN}")
    for angle in ANGLES:
        for distance in DISTANCES:
            print(f"\n=== block: angle {angle}, distance {distance} ===")
            for step, item in enumerate(schedule):
                print(f"[{step + 1}/{len(schedule)}] {item.prompt}")
                for phase in (PHASE_PREP, PHASE_DROP, PHASE_HOLD):
                    seconds = {PHASE_PREP: PREP_S, PHASE_DROP: DROP_S, PHASE_HOLD: KEEP_S}[phase]
                    print(f"    {phase:<5} {seconds:.1f} s")
                    time.sleep(seconds)


def run_capture(session: str, person: str, schedule: list[Item], writer: SampleWriter,
                resume: bool, camera_index: int = 0) -> int:
    """The real capture loop. Returns a process exit code."""
    import cv2

    from app.landmarks import HandDetector

    camera = cv2.VideoCapture(camera_index)
    if not camera.isOpened():
        print(f"capture: no camera on index {camera_index}", file=sys.stderr)
        return 1

    detector = HandDetector(num_hands=2)
    normalizer = Normalizer()
    transition_seen = 0
    quit_requested = False

    try:
        for angle in ANGLES:
            if quit_requested:
                break
            for distance in DISTANCES:
                if quit_requested:
                    break
                if not _wait_for_block(camera, angle, distance):
                    quit_requested = True
                    break
                for step, item in enumerate(schedule):
                    if resume and writer.already_done(
                        hold_id(session, item.cls, angle, distance, step)
                    ):
                        continue
                    normalizer.reset()
                    outcome, transition_seen = _run_step(
                        camera, detector, normalizer, writer, session, person,
                        angle, distance, step, item, len(schedule), transition_seen,
                    )
                    if outcome == "quit":
                        quit_requested = True
                        break
    finally:
        detector.close()
        camera.release()
        cv2.destroyAllWindows()

    print(f"capture: wrote {writer.written} {writer.split} windows to {writer.path}")
    return 0


def _wait_for_block(camera: Any, angle: str, distance: str) -> bool:
    """Hold until the operator has moved the camera. False means quit."""
    import cv2

    banner = f"Set the camera: angle {angle}, distance {distance}"
    print(f"\n=== {banner} ===  space to start, q to quit")
    while True:
        ok, frame = camera.read()
        if not ok:
            continue
        frame = cv2.flip(frame, 1)
        _put(frame, banner, 40, 1.0)
        _put(frame, "space to start, q to quit", 80, 0.7)
        cv2.imshow("tenfold capture", frame)
        key = cv2.waitKey(1) & 0xFF
        if key == ord(" "):
            return True
        if key == ord("q"):
            return False


def _run_step(camera: Any, detector: Any, normalizer: Normalizer, writer: SampleWriter,
              session: str, person: str, angle: str, distance: str, step: int,
              item: Item, total_steps: int, transition_seen: int) -> tuple[str, int]:
    """Record one step of the schedule. Returns (outcome, transition counter)."""
    import cv2

    print(f"[{step + 1}/{total_steps}] {item.prompt}")
    start = time.monotonic()
    current_phase = ""
    since_emit = 0

    while True:
        elapsed = time.monotonic() - start
        phase = phase_at(elapsed)
        if phase == PHASE_DONE:
            return "done", transition_seen

        ok, frame = camera.read()
        if not ok:
            continue
        frame = cv2.flip(frame, 1)
        window = normalizer.update(detector.detect(frame))

        # A window must not straddle two phases, so the stride restarts on every
        # phase change and the next window is the 5 frames that follow it.
        if phase != current_phase:
            current_phase = phase
            since_emit = 0

        if len(window) < WINDOW_SIZE:
            since_emit = 0
        else:
            since_emit += 1

        if since_emit >= WINDOW_SIZE:
            since_emit = 0
            labelled = label_for(phase, item)
            if labelled is not None:
                cls, kind, label = labelled
                keep = True
                if kind == TRANSITION_KIND:
                    transition_seen += 1
                    keep = transition_seen % TRANSITION_KEEP_ONE_IN == 0
                if keep:
                    writer.write(session, person, angle, distance, step,
                                 cls, kind, label, window)

        _draw_step(frame, item, phase, STEP_S - elapsed, step, total_steps, writer.written)
        cv2.imshow("tenfold capture", frame)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            return "quit", transition_seen
        if key == ord("s"):
            return "skip", transition_seen


def _draw_step(frame: Any, item: Item, phase: str, remaining: float, step: int,
               total_steps: int, written: int) -> None:
    colors = {PHASE_PREP: (150, 160, 170), PHASE_DROP: (35, 166, 245),
              PHASE_HOLD: (113, 204, 46)}
    _put(frame, item.prompt, 44, 0.9, colors.get(phase, (255, 255, 255)))
    _put(frame, f"{phase}  {remaining:.1f} s", 84, 0.8, colors.get(phase, (255, 255, 255)))
    _put(frame, f"step {step + 1}/{total_steps}   windows {written}   s skip   q quit", 120, 0.6)


def _put(frame: Any, text: str, y: int, size: float,
         color: tuple[int, int, int] = (255, 255, 255)) -> None:
    import cv2

    cv2.putText(frame, text, (20, y), cv2.FONT_HERSHEY_SIMPLEX, size, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(frame, text, (20, y), cv2.FONT_HERSHEY_SIMPLEX, size, color, 1, cv2.LINE_AA)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tenfold dataset capture")
    parser.add_argument("--session", default="axel_1", help="session name stored on every row")
    parser.add_argument("--person", default="", help="who is in front of the camera")
    parser.add_argument("--heldout", action="store_true",
                        help=f"write test rows to {HELDOUT_PATH} instead of the repo dataset")
    parser.add_argument("--resume", action="store_true",
                        help="skip holds already recorded for this session")
    parser.add_argument("--dry-run", action="store_true",
                        help="rehearse the prompts and the timing without a camera")
    parser.add_argument("--camera", type=int, default=0, help="camera index")
    args = parser.parse_args(argv)

    schedule = build_schedule()
    if args.dry_run:
        run_dry(schedule)
        return 0

    # F3: the held out set is recorded by someone who is not the dataset author,
    # so who recorded it has to be stated rather than defaulted.
    if args.heldout and not args.person:
        parser.error("--heldout needs --person: the held out set is a different person's hands")

    path, split = resolve_target(args.heldout)
    writer = SampleWriter(path, split)
    try:
        return run_capture(args.session, args.person or args.session, schedule, writer,
                           args.resume, args.camera)
    finally:
        writer.close()


if __name__ == "__main__":
    sys.exit(main())
