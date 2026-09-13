"""app/live_samples.py: the live sample writer, driven with injected windows and no camera.

Every case here is a rule the writer has to keep whatever a lesson does: the schema of the frozen
dataset, N windows and no more, nothing at all from the demo or the mock camera, never a byte into
data/samples.jsonl, a learner id on every row, windows that belong to the learner and exercise they
were collected for, a validated pose that survives a late answer, and silence instead of an
exception when the disk says no.
"""

from __future__ import annotations

import builtins
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from app.live_samples import (
    BUFFER_MIN,
    DEDUPE_DISTANCE,
    DEFAULT_WINDOWS,
    LIVE_SAMPLES_PATH,
    LiveSampleWriter,
    Pose,
    windows_from_params,
)
from classifier.schema import GestureState, HandFrame, Window

REPO = Path(__file__).resolve().parents[1]
SAMPLES = REPO / "data" / "samples.jsonl"

EXTRA_FIELDS = {
    "source", "learner_id", "exercise", "session_ts", "pose_confidence",
    "confirmed_by_answer", "seen_pose", "target_pose",
}


# --- synthetic windows ------------------------------------------------------


def hand(offset: float) -> HandFrame:
    """One hand, 21 plausible normalized points, shifted by offset so windows can differ."""
    points = [(0.1 * index + offset, 0.05 * index + offset, 0.0) for index in range(21)]
    return HandFrame(points=points, detection_conf=0.9, wrist_xy=(0.3 + offset, 0.5), scale=0.12)


def window(offset: float = 0.0) -> Window:
    return Window(left=[hand(offset) for _ in range(5)],
                  right=[hand(offset + 1.0) for _ in range(5)])


def pose(left: int, right: int, contact: bool = True) -> GestureState:
    return GestureState(method="6-10", left=left, right=right, contact=contact, confidence=0.8)


def writer(tmp_path: Path, windows: int = DEFAULT_WINDOWS, enabled: bool = True,
           log: Any = None, learner: str = "lea", exercise: str = "8 x 7") -> LiveSampleWriter:
    """A writer with one exercise on screen, the way the server opens every exercise."""
    live = LiveSampleWriter(tmp_path / "live_samples.jsonl", windows, enabled,
                            log or logging.getLogger("test.live_samples"),
                            session_ts="2026-09-13T18:00:00.000Z")
    live.begin(learner, exercise, "s1")
    return live


def feed(live: LiveSampleWriter, count: int, state: Any, start: float = 0.0,
         step: float = 0.05) -> None:
    """Windows that differ well above the dedupe threshold, as real hands do."""
    for index in range(count):
        live.offer(window(start + index * step), state)


def rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --- schema -----------------------------------------------------------------


def test_written_line_matches_the_samples_schema_plus_the_live_fields(tmp_path: Path) -> None:
    live = writer(tmp_path)
    feed(live, 3, pose(8, 7))
    assert live.confirm_correct("Lea", "8 x 7", (8, 7)) == 3

    with open(SAMPLES, encoding="utf-8") as handle:
        reference = set(json.loads(handle.readline()).keys())

    written = rows(tmp_path / "live_samples.jsonl")
    assert written
    for row in written:
        assert set(row.keys()) == reference | EXTRA_FIELDS
        assert row["source"] == "live"
        assert row["split"] == "live"
        assert row["label"] == {"method": "6-10", "left": 8, "right": 7, "contact": True}
        assert row["kind"] == "positive"
        assert row["confirmed_by_answer"] is True
        assert row["exercise"] == "8 x 7"
        assert row["session_ts"] == "2026-09-13T18:00:00.000Z"
        assert row["pose_confidence"] == pytest.approx(0.8)
        assert len(row["window"]) == 5
        for frame in row["window"]:
            assert len(frame["left"]["points"]) == 21
            assert len(frame["right"]["points"]) == 21
            assert set(frame["left"]) == {"points", "conf", "wrist", "scale"}


def test_the_label_is_what_the_classifier_read_not_the_ordered_target(tmp_path: Path) -> None:
    """The engine accepts 7 x 8 for 8 x 7, so the pose held is the pose labelled."""
    live = writer(tmp_path)
    feed(live, 3, pose(7, 8))
    assert live.confirm_correct("lea", "8 x 7", (8, 7)) == 3

    for row in rows(tmp_path / "live_samples.jsonl"):
        assert row["label"] == {"method": "6-10", "left": 7, "right": 8, "contact": True}
        assert row["target_pose"] == {"method": "6-10", "left": 8, "right": 7, "contact": True}


# --- how many windows -------------------------------------------------------


def test_the_last_n_windows_are_written_and_no_more(tmp_path: Path) -> None:
    live = writer(tmp_path, windows=3)
    feed(live, 12, pose(8, 7))
    assert live.confirm_correct("lea", "8 x 7", (8, 7)) == 3

    written = rows(tmp_path / "live_samples.jsonl")
    assert len(written) == 3
    # The last three offered, not the first three: offsets 0.45, 0.50 and 0.55, and the thumb tip
    # of hand(offset) sits at x = 0.4 + offset.
    tips = [row["window"][-1]["left"]["points"][4][0] for row in written]
    assert tips == pytest.approx([0.85, 0.90, 0.95])


def test_the_run_stops_at_a_different_pose(tmp_path: Path) -> None:
    live = writer(tmp_path, windows=8)
    feed(live, 5, pose(8, 7), start=0.0)
    feed(live, 2, pose(9, 7), start=1.0)
    feed(live, 3, pose(8, 7), start=2.0)
    assert live.confirm_correct("lea", "8 x 7", (8, 7)) == 3


def test_n_falls_back_to_eight() -> None:
    assert windows_from_params(None) == DEFAULT_WINDOWS == 8
    assert windows_from_params({}) == 8
    assert windows_from_params({"global": {"fps": 15}}) == 8
    assert windows_from_params({"global": {"live_sample_windows": "nonsense"}}) == 8
    assert windows_from_params({"global": {"live_sample_windows": 0}}) == 8


def test_n_is_read_from_the_tutor_params_when_the_key_is_there(tmp_path: Path) -> None:
    assert windows_from_params({"global": {"live_sample_windows": 4}}) == 4
    assert windows_from_params({"live_sample_windows": 2}) == 2

    live = writer(tmp_path, windows=windows_from_params({"global": {"live_sample_windows": 2}}))
    feed(live, 6, pose(8, 7))
    assert live.confirm_correct("lea", "8 x 7", (8, 7)) == 2


# --- the validated pose and a late answer -----------------------------------


def test_a_late_answer_still_writes_the_validated_pose(tmp_path: Path) -> None:
    """Eight windows of the right pose, then the hands leave the frame for longer than the buffer
    holds: the pose validated before is what the answer corroborates."""
    unknown = GestureState.unknown(0.1)
    late = writer(tmp_path, windows=8)
    feed(late, 8, pose(8, 7))
    assert late.pose_validated("lea", "8 x 7", (8, 7)) == 8
    feed(late, BUFFER_MIN, unknown, start=5.0)
    assert late.confirm_correct("lea", "8 x 7", (8, 7)) == 8

    written = rows(tmp_path / "live_samples.jsonl")
    assert len(written) == 8
    assert all(row["label"] == {"method": "6-10", "left": 8, "right": 7, "contact": True}
               for row in written)
    assert all(row["confirmed_by_answer"] is True for row in written)

    # The same lesson without the pose kept aside: the buffer has moved past it.
    lost = LiveSampleWriter(tmp_path / "lost.jsonl", 8, True, logging.getLogger("test.live_samples"))
    lost.begin("lea", "8 x 7")
    feed(lost, 8, pose(8, 7))
    feed(lost, BUFFER_MIN, unknown, start=5.0)
    assert lost.confirm_correct("lea", "8 x 7", (8, 7)) == 0


def test_the_validated_pose_follows_the_hold_until_the_answer(tmp_path: Path) -> None:
    """Validated early, held a while longer: the last N windows of the hold are the ones kept."""
    live = writer(tmp_path, windows=3)
    feed(live, 2, pose(8, 7))
    assert live.pose_validated("lea", "8 x 7", (8, 7)) == 2
    feed(live, 4, pose(8, 7), start=1.0)
    feed(live, BUFFER_MIN, GestureState.unknown(0.1), start=5.0)
    assert live.confirm_correct("lea", "8 x 7", (8, 7)) == 3
    tips = [row["window"][-1]["left"]["points"][4][0] for row in rows(tmp_path / "live_samples.jsonl")]
    assert tips == pytest.approx([1.45, 1.50, 1.55])


def test_the_validated_pose_is_freed_at_the_end_of_the_exercise(tmp_path: Path) -> None:
    live = writer(tmp_path)
    feed(live, 8, pose(8, 7))
    assert live.pose_validated("lea", "8 x 7", (8, 7)) == 8
    live.end()
    assert live.owner is None
    assert live.confirm_correct("lea", "8 x 7", (8, 7)) == 0
    live.begin("lea", "8 x 7", "s1")        # the same title again is another exercise
    assert live.confirm_correct("lea", "8 x 7", (8, 7)) == 0
    assert not (tmp_path / "live_samples.jsonl").exists()


# --- whose windows ----------------------------------------------------------


def test_a_new_exercise_never_confirms_with_the_previous_exercises_windows(tmp_path: Path) -> None:
    """8 x 7 held and never answered, then 7 x 8: the engine accepts the swapped pair, so the old
    windows would match the new exercise if they were still there."""
    live = writer(tmp_path, exercise="8 x 7")
    feed(live, 8, pose(8, 7))
    assert live.pose_validated("lea", "8 x 7", (8, 7)) == 8
    live.begin("lea", "7 x 8", "s1")
    assert live.confirm_correct("lea", "7 x 8", (7, 8)) == 0
    assert live.confirm_correct("lea", "8 x 7", (8, 7)) == 0, "the old exercise is not on screen"
    assert not (tmp_path / "live_samples.jsonl").exists()

    feed(live, 3, pose(7, 8), start=3.0)
    assert live.confirm_correct("lea", "7 x 8", (7, 8)) == 3
    assert {row["exercise"] for row in rows(tmp_path / "live_samples.jsonl")} == {"7 x 8"}


def test_a_new_learner_never_gets_the_previous_learners_windows(tmp_path: Path) -> None:
    live = writer(tmp_path, learner="lea")
    feed(live, 8, pose(8, 7))
    assert live.pose_validated("lea", "8 x 7", (8, 7)) == 8
    feed(live, 4, pose(9, 7), start=2.0)
    live.begin("noe", "8 x 7", "s2")
    assert live.confirm_correct("noe", "8 x 7", (8, 7)) == 0
    assert live.record_gesture_error("noe", "8 x 7", pose(9, 7), (8, 7)) == 0
    assert live.confirm_correct("lea", "8 x 7", (8, 7)) == 0
    assert not (tmp_path / "live_samples.jsonl").exists()

    feed(live, 2, pose(8, 7), start=4.0)
    assert live.confirm_correct("noe", "8 x 7", (8, 7)) == 2
    assert {row["learner_id"] for row in rows(tmp_path / "live_samples.jsonl")} == {"noe"}


def test_an_event_for_someone_not_on_screen_writes_nothing_and_keeps_the_windows(tmp_path: Path) -> None:
    live = writer(tmp_path, learner="lea", exercise="8 x 7")
    feed(live, 4, pose(8, 7))
    assert live.confirm_correct("noe", "8 x 7", (8, 7)) == 0
    assert live.confirm_correct("lea", "9 x 9", (9, 9)) == 0
    assert live.pose_validated("noe", "8 x 7", (8, 7)) == 0
    assert live.confirm_correct("lea", "8 x 7", (8, 7)) == 4


def test_nothing_is_collected_without_an_exercise_on_screen(tmp_path: Path) -> None:
    live = LiveSampleWriter(tmp_path / "live_samples.jsonl", 4, True,
                            logging.getLogger("test.live_samples"))
    feed(live, 6, pose(8, 7))
    assert live.confirm_correct("lea", "8 x 7", (8, 7)) == 0
    live.begin("lea", "8 x 7")
    assert live.confirm_correct("lea", "8 x 7", (8, 7)) == 0, "windows before begin are not kept"
    live.end()
    feed(live, 6, pose(8, 7))
    assert live.record_gesture_error("lea", "8 x 7", pose(8, 7), (8, 7)) == 0
    assert not (tmp_path / "live_samples.jsonl").exists()


# --- the demo and the mock camera -------------------------------------------

def test_a_mock_or_demo_source_writes_nothing(tmp_path: Path) -> None:
    live = writer(tmp_path, enabled=False)
    feed(live, 10, pose(8, 7))
    assert live.pose_validated("lea", "8 x 7", (8, 7)) == 0
    assert live.confirm_correct("lea", "8 x 7", (8, 7)) == 0
    assert live.record_gesture_error("lea", "8 x 7", pose(9, 7), (8, 7)) == 0
    assert not (tmp_path / "live_samples.jsonl").exists()


def test_the_enabled_flag_is_off_by_default(tmp_path: Path) -> None:
    live = LiveSampleWriter(tmp_path / "live_samples.jsonl")
    assert live.enabled is False


# --- the frozen dataset -----------------------------------------------------


def test_the_frozen_dataset_is_refused_by_name(tmp_path: Path) -> None:
    for name in ("samples.jsonl", "test.jsonl"):
        live = LiveSampleWriter(tmp_path / name, 8, True,
                                logging.getLogger("test.live_samples"))
        assert live.enabled is False
        live.begin("lea", "8 x 7")
        feed(live, 3, pose(8, 7))
        assert live.confirm_correct("lea", "8 x 7", (8, 7)) == 0
        assert not (tmp_path / name).exists()


def test_samples_jsonl_is_never_opened_for_writing(tmp_path: Path, monkeypatch: Any) -> None:
    opened: list[tuple[str, str]] = []
    real_open = builtins.open

    def spy(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        opened.append((str(file), mode))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy)

    live = writer(tmp_path)
    feed(live, 4, pose(8, 7))
    live.confirm_correct("lea", "8 x 7", (8, 7))
    feed(live, 4, pose(9, 7), start=1.0)
    live.record_gesture_error("lea", "8 x 7", pose(9, 7), (8, 7))

    assert opened, "the writer never opened anything, the spy is not wired"
    assert not any(Path(path).name in ("samples.jsonl", "test.jsonl")
                   for path, _ in opened)
    assert not any(Path(path) == LIVE_SAMPLES_PATH for path, _ in opened)
    assert all(Path(path).parent == tmp_path for path, _ in opened)


# --- hard negatives ---------------------------------------------------------


def test_a_hard_negative_carries_both_poses_and_is_not_confirmed(tmp_path: Path) -> None:
    live = writer(tmp_path, windows=4)
    feed(live, 6, pose(9, 7))
    assert live.record_gesture_error("lea", "8 x 7", pose(9, 7), (8, 7)) == 4

    written = rows(tmp_path / "live_samples.jsonl")
    assert len(written) == 4
    for row in written:
        assert row["confirmed_by_answer"] is False
        assert row["seen_pose"] == {"method": "6-10", "left": 9, "right": 7, "contact": True}
        assert row["target_pose"] == {"method": "6-10", "left": 8, "right": 7, "contact": True}
        assert row["label"] == row["seen_pose"]
        assert row["kind"] == "positive"


def test_a_hard_negative_on_an_unknown_reading_keeps_the_target(tmp_path: Path) -> None:
    live = writer(tmp_path, windows=2)
    feed(live, 4, GestureState.unknown(0.2))
    assert live.record_gesture_error("lea", "8 x 7", GestureState.unknown(0.2), (8, 7)) == 2

    for row in rows(tmp_path / "live_samples.jsonl"):
        assert row["label"] == {"method": "unknown"}
        assert row["seen_pose"] == {"method": "unknown"}
        assert row["target_pose"] == {"method": "6-10", "left": 8, "right": 7, "contact": True}
        assert row["kind"] == "transition"
        assert row["confirmed_by_answer"] is False


def test_a_gesture_error_on_a_pose_that_is_gone_writes_nothing(tmp_path: Path) -> None:
    live = writer(tmp_path)
    feed(live, 5, pose(8, 7))
    assert live.record_gesture_error("lea", "8 x 7", pose(10, 10), (8, 7)) == 0
    assert not (tmp_path / "live_samples.jsonl").exists()


# --- dedupe -----------------------------------------------------------------


def test_near_identical_windows_are_deduped(tmp_path: Path) -> None:
    live = writer(tmp_path, windows=8)
    state = pose(8, 7)
    for _ in range(3):
        live.offer(window(0.0), state)                       # the same measurement three times
    live.offer(window(DEDUPE_DISTANCE / 10.0), state)         # jitter well under the threshold
    assert live.confirm_correct("lea", "8 x 7", (8, 7)) == 1


def test_genuinely_different_windows_are_kept(tmp_path: Path) -> None:
    live = writer(tmp_path, windows=8)
    state = pose(8, 7)
    for offset in (0.0, DEDUPE_DISTANCE * 10.0, DEDUPE_DISTANCE * 20.0):
        live.offer(window(offset), state)
    assert live.confirm_correct("lea", "8 x 7", (8, 7)) == 3


def test_the_same_hold_confirmed_twice_is_written_once(tmp_path: Path) -> None:
    live = writer(tmp_path, windows=8)
    feed(live, 3, pose(8, 7))
    assert live.confirm_correct("lea", "8 x 7", (8, 7)) == 3
    feed(live, 3, pose(8, 7))       # the very same three windows again
    assert live.confirm_correct("lea", "8 x 7", (8, 7)) == 0
    assert len(rows(tmp_path / "live_samples.jsonl")) == 3


# --- the learner ------------------------------------------------------------


def test_a_record_always_carries_a_non_empty_learner_id(tmp_path: Path) -> None:
    live = writer(tmp_path)
    feed(live, 4, pose(8, 7))
    assert live.confirm_correct("   ", "8 x 7", (8, 7)) == 0
    assert live.confirm_correct(None, "8 x 7", (8, 7)) == 0  # type: ignore[arg-type]
    assert not (tmp_path / "live_samples.jsonl").exists()

    assert live.confirm_correct("  Lea  ", "8 x 7", (8, 7)) == 4
    written = rows(tmp_path / "live_samples.jsonl")
    assert written
    for row in written:
        assert row["learner_id"] == "lea"
        assert row["learner_id"].strip()
        assert row["person"] == "lea"

    empty = writer(tmp_path, learner="   ")
    feed(empty, 4, pose(8, 7), start=3.0)
    assert empty.confirm_correct("   ", "8 x 7", (8, 7)) == 0, "no learner, no owner, no row"


def test_two_learners_stay_separate_in_the_file(tmp_path: Path) -> None:
    live = writer(tmp_path, windows=2)
    feed(live, 2, pose(8, 7))
    assert live.confirm_correct("Lea", "8 x 7", (8, 7)) == 2
    # The same pose and the same windows, another child: nothing is deduped across learners.
    live.begin("Noe", "8 x 7", "s2")
    feed(live, 2, pose(8, 7))
    assert live.confirm_correct("Noe", "8 x 7", (8, 7)) == 2

    written = rows(tmp_path / "live_samples.jsonl")
    assert [row["learner_id"] for row in written] == ["lea", "lea", "noe", "noe"]
    assert len({row["id"] for row in written}) == 4
    assert len({row["session"] for row in written}) == 2
    assert len({row["hold_id"] for row in written}) == 2


# --- failures ---------------------------------------------------------------


def test_an_unwritable_path_does_not_raise(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    log = logging.getLogger("test.live_samples.unwritable")
    live = LiveSampleWriter(blocker / "live_samples.jsonl", 4, True, log,
                            session_ts="2026-09-13T18:00:00.000Z")
    live.begin("lea", "8 x 7")
    feed(live, 4, pose(8, 7))
    assert live.confirm_correct("lea", "8 x 7", (8, 7)) == 0
    assert live.record_gesture_error("lea", "8 x 7", pose(9, 7), (8, 7)) == 0


def test_a_malformed_window_does_not_raise(tmp_path: Path) -> None:
    live = writer(tmp_path, windows=2)
    live.offer("not a window", pose(8, 7))       # type: ignore[arg-type]
    feed(live, 2, pose(8, 7))
    assert live.confirm_correct("lea", "8 x 7", (8, 7)) == 2


def test_ids_continue_from_the_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "live_samples.jsonl"
    path.write_text('{"id":"l000000"}\n{"id":"l000001"}\n', encoding="utf-8")
    live = LiveSampleWriter(path, 2, True, logging.getLogger("test.live_samples"),
                            session_ts="2026-09-13T18:00:00.000Z")
    live.begin("lea", "8 x 7")
    feed(live, 2, pose(8, 7))
    assert live.confirm_correct("lea", "8 x 7", (8, 7)) == 2
    assert [row["id"] for row in rows(path)[2:]] == ["l000002", "l000003"]


# --- the pose helper --------------------------------------------------------


def test_pose_reads_every_shape_the_server_has_at_hand() -> None:
    expected = Pose(method="6-10", left=8, right=7, contact=True)
    assert Pose.of(pose(8, 7)) == expected
    assert Pose.of((8, 7)) == expected
    assert Pose.of({"method": "6-10", "left": 8, "right": 7, "contact": True}) == expected
    assert Pose.of(expected) == expected
    assert Pose.of(None) == Pose()
    assert Pose.of(GestureState.unknown()) == Pose()
    assert Pose.of({"method": "6-10", "left": 42, "right": 7, "contact": True}).known is False
    assert Pose.of((8, 7)).matches(Pose.of((7, 8)), unordered=True)
    assert not Pose.of((8, 7)).matches(Pose.of((7, 8)))
