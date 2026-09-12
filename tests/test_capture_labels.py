"""data/capture.py labelling, amendment F4 on a synthetic timeline.

One step is 3.0 s of prep, then 0.5 s dropped, then 1.0 s kept. The dropped half
second is the whole point of F4: the hand is still settling into the pose, so
those frames are thrown away rather than labelled transition, which would teach
the classifier that a half formed 8 x 7 is a transition.

The last test checks the other half of the contract: a row written here has to
be readable by eval/predict.py and classifiable by eval/scorers.py.
"""

from __future__ import annotations

import json

import pytest

from classifier.schema import KINDS, HandFrame, Window, window_from_json
from data.capture import (
    ALL_BLOCKS,
    DROP_S,
    HELDOUT_PATH,
    KEEP_S,
    PHASE_DONE,
    PHASE_DROP,
    PHASE_HOLD,
    PHASE_PREP,
    PREP_S,
    SAMPLES_PATH,
    STEP_S,
    TRANSITION_KIND,
    Item,
    SampleWriter,
    block_name,
    build_schedule,
    hold_id,
    label_for,
    parse_blocks,
    phase_at,
    resolve_target,
    timeline,
)
from eval import scorers

ITEM = Item(cls="8x7", kind="positive",
            prompt="LEFT hand 8, RIGHT hand 7, touch them tip to tip",
            label={"method": "6-10", "left": 8, "right": 7, "contact": True})


def test_phase_boundaries():
    assert phase_at(0.0) == PHASE_PREP
    assert phase_at(PREP_S - 0.001) == PHASE_PREP
    assert phase_at(PREP_S) == PHASE_DROP
    assert phase_at(PREP_S + DROP_S - 0.001) == PHASE_DROP
    assert phase_at(PREP_S + DROP_S) == PHASE_HOLD
    assert phase_at(STEP_S - 0.001) == PHASE_HOLD
    assert phase_at(STEP_S) == PHASE_DONE


def test_f4_drops_the_first_half_second_of_the_hold():
    """No frame between 3.0 s and 3.5 s carries a label of any kind."""
    dropped = [t for t, phase in timeline(fps=30.0) if label_for(phase, ITEM) is None]
    assert dropped, "the drop window should not be empty"
    for t in dropped:
        assert PREP_S <= t < PREP_S + DROP_S
    assert min(dropped) == pytest.approx(PREP_S)
    assert max(dropped) < PREP_S + DROP_S


def test_dropped_frames_are_not_labelled_transition():
    """The F4 fix: discarded, not relabelled."""
    assert label_for(PHASE_DROP, ITEM) is None
    assert label_for(PHASE_DONE, ITEM) is None


def test_prep_is_transition_and_hold_carries_the_class():
    cls, kind, label = label_for(PHASE_PREP, ITEM)
    assert kind == TRANSITION_KIND
    assert label == {"method": "unknown"}

    cls, kind, label = label_for(PHASE_HOLD, ITEM)
    assert cls == "8x7"
    assert kind == "positive"
    assert label == {"method": "6-10", "left": 8, "right": 7, "contact": True}


def test_the_label_is_a_copy_the_caller_cannot_corrupt():
    _, _, label = label_for(PHASE_HOLD, ITEM)
    label["left"] = 99
    assert ITEM.label["left"] == 8


def test_frame_counts_per_phase_at_thirty_fps():
    phases = [phase for _, phase in timeline(fps=30.0)]
    assert phases.count(PHASE_PREP) == int(PREP_S * 30)
    assert phases.count(PHASE_DROP) == int(DROP_S * 30)
    assert phases.count(PHASE_HOLD) == int(KEEP_S * 30)
    assert PHASE_DONE not in phases


def test_schedule_shape():
    schedule = build_schedule()
    counts: dict[str, int] = {}
    for item in schedule:
        counts[item.kind] = counts.get(item.kind, 0) + 1
    assert counts["positive"] == 25
    assert counts["rest"] == 1
    assert counts["near_contact"] == 10
    assert counts["partial_hand"] == 5
    assert counts["out_of_frame"] == 5
    assert len(schedule) == 46
    assert len({item.cls for item in schedule}) == 46, "hold_id needs unique class names"


def test_every_kind_is_one_the_contract_knows():
    for item in build_schedule():
        assert item.kind in KINDS


def test_combo_prompts_name_both_sides():
    """G9: the ordered labels are only correct if the prompt says which hand."""
    for item in build_schedule():
        if item.kind != "positive":
            continue
        left, right = (int(n) for n in item.cls.split("x"))
        assert f"LEFT {left}" in item.prompt
        assert f"RIGHT {right}" in item.prompt
        assert item.label["left"] == left
        assert item.label["right"] == right


def test_near_contact_labels_match_the_scorer():
    """near_contact_accuracy counts samples with contact false and a known method."""
    near = [i for i in build_schedule() if i.kind == "near_contact"]
    assert len(near) == 10
    for item in near:
        assert item.label["method"] == "6-10"
        assert item.label["contact"] is False
        assert "DO NOT touch" in item.prompt


def test_negative_classes_are_unknown():
    for item in build_schedule():
        if item.kind in {"rest", "partial_hand", "out_of_frame"}:
            assert item.label == {"method": "unknown"}


def test_split_follows_the_destination_not_the_angle():
    """SPEC.md 5.5 and F1: split by person, held out outside the repo."""
    assert resolve_target(False) == (SAMPLES_PATH, "train")
    assert resolve_target(True) == (HELDOUT_PATH, "test")
    assert "tenfold-heldout" in str(HELDOUT_PATH)
    assert SAMPLES_PATH.parent.name == "data"


def _window() -> Window:
    frame = HandFrame(points=[(0.0, 0.0, 0.0)] * 21, detection_conf=0.9,
                      wrist_xy=(0.3, 0.5), scale=0.1)
    return Window(left=[frame] * 5, right=[frame] * 5)


def test_a_written_row_is_readable_by_the_eval(tmp_path):
    path = tmp_path / "samples.jsonl"
    writer = SampleWriter(path, "train")
    writer.write("axel_1", "axel", "side", "near", 3, ITEM.cls, ITEM.kind,
                 dict(ITEM.label), _window())
    writer.close()

    row = json.loads(path.read_text().strip())
    assert row["session"] == "axel_1"
    assert row["person"] == "axel"
    assert row["angle"] == "side"
    assert row["split"] == "train", "the side angle is a stress metric, not a split"
    assert row["kind"] == "positive"
    assert row["hold_id"] == hold_id("axel_1", "8x7", "side", "near", 3)

    # eval/predict.py reads it exactly this way.
    window = window_from_json(row["window"])
    assert len(window) == 5
    assert window.left[0].present and window.right[0].present
    assert window.left[0].wrist_xy == (0.3, 0.5)
    # and eval/scorers.py names the class from kind and label.
    assert scorers.class_of(row["label"], row["kind"]) == "8x7"


def test_resume_skips_holds_already_recorded(tmp_path):
    path = tmp_path / "samples.jsonl"
    writer = SampleWriter(path, "train")
    writer.write("axel_1", "axel", "front", "near", 3, ITEM.cls, ITEM.kind,
                 dict(ITEM.label), _window())
    writer.close()

    reopened = SampleWriter(path, "train")
    assert reopened.already_done(hold_id("axel_1", "8x7", "front", "near", 3))
    assert not reopened.already_done(hold_id("axel_1", "8x7", "front", "far", 3))
    assert reopened.next_index == 1, "ids continue where the file stopped"
    reopened.close()


# --- which blocks to record --------------------------------------------------


def test_no_blocks_given_means_all_six_in_the_standard_order():
    assert parse_blocks(None) == list(ALL_BLOCKS)
    assert parse_blocks("") == list(ALL_BLOCKS)
    assert parse_blocks("   ") == list(ALL_BLOCKS)
    assert len(ALL_BLOCKS) == 6


def test_blocks_run_in_the_order_they_were_asked_for():
    """The point of the flag: pick up the two blocks that are missing, in that
    order, without walking the four already recorded."""
    asked = parse_blocks("side_near,top_near")
    assert asked == [("side", "near"), ("top", "near")]
    assert asked != [b for b in ALL_BLOCKS if b in asked], "the default order is not kept"


def test_block_names_are_forgiving_about_spacing_and_case():
    assert parse_blocks(" Side_Near , top_near ") == [("side", "near"), ("top", "near")]
    assert parse_blocks("front_far,") == [("front", "far")]


def test_a_block_named_twice_is_recorded_once():
    assert parse_blocks("top_far,top_far") == [("top", "far")]


def test_an_unknown_block_is_refused_and_says_what_exists():
    with pytest.raises(ValueError) as excinfo:
        parse_blocks("side_middle")
    message = str(excinfo.value)
    assert "side_middle" in message
    assert "side_near" in message and "front_far" in message


def test_a_list_that_names_nothing_is_refused():
    with pytest.raises(ValueError):
        parse_blocks(",,")


def test_block_names_match_the_angle_and_distance():
    assert block_name("side", "near") == "side_near"
    assert {block_name(a, d) for a, d in ALL_BLOCKS} == {
        "front_near", "front_far", "top_near", "top_far", "side_near", "side_far"}
