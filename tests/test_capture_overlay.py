"""data/capture.py on screen banner.

The banner is drawn once, on the already mirrored frame, as one solid dark band
so the text needs no outline stroke. The old version stroked every line twice,
black at thickness 4 then colour at thickness 1, which smeared the glyphs.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from data.capture import (
    BANNER_BG,
    PHASE_DROP,
    PHASE_HOLD,
    PHASE_PREP,
    PREP_S,
    Item,
    _draw_banner,
    _fit_scale,
    build_schedule,
    overlay_lines,
    phase_caption,
)

ITEM = Item(cls="6x9", kind="positive",
            prompt="LEFT 6 and RIGHT 9, touch tip to tip",
            label={"method": "6-10", "left": 6, "right": 9, "contact": True})


def test_phase_caption_counts_down_to_the_hold():
    assert phase_caption(PHASE_PREP, 0.9) == f"get ready  {PREP_S - 0.9:.1f} s"
    assert phase_caption(PHASE_PREP, PREP_S + 1.0) == "get ready  0.0 s", "never negative"


def test_phase_caption_says_hold_once_the_pose_starts():
    assert phase_caption(PHASE_DROP, PREP_S + 0.2) == "HOLD"
    assert phase_caption(PHASE_HOLD, PREP_S + 0.8) == "HOLD  recording"


def test_three_lines_instruction_phase_counters():
    lines = overlay_lines(ITEM, PHASE_PREP, 0.9, 3, 46, 12)
    assert len(lines) == 3
    assert lines[0] == "LEFT 6 and RIGHT 9, touch tip to tip"
    assert lines[1].startswith("get ready")
    assert "step 4/46" in lines[2]
    assert "windows 12" in lines[2]
    assert "s skip" in lines[2] and "q quit" in lines[2]


@pytest.mark.parametrize("width", [640, 1280])
def test_every_instruction_fits_the_frame_width(width):
    """A prompt that runs off the edge is the other half of the legibility bug."""
    room = width - 40
    for item in build_schedule():
        scale = _fit_scale(item.prompt, room, 1.0, 2)
        (drawn, _), _ = cv2.getTextSize(item.prompt, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
        assert drawn <= room, f"{item.prompt!r} overflows at {width} px"


def test_the_banner_is_solid_and_leaves_the_video_alone():
    frame = np.full((480, 640, 3), 255, dtype=np.uint8)
    _draw_banner(frame, overlay_lines(ITEM, PHASE_HOLD, 4.0, 0, 46, 0), (113, 204, 46))

    band = max(110, min(200, int(480 * 0.24)))
    # Below the band the webcam image is untouched.
    assert (frame[band + 1:] == 255).all()
    # The band itself is the dark colour, not a translucent wash of the frame.
    corner = frame[2, 2]
    assert tuple(int(v) for v in corner) == BANNER_BG
    # And there is text on it: some pixels inside the band are neither.
    inside = frame[:band]
    painted = ~np.all(inside == np.array(BANNER_BG, dtype=np.uint8), axis=2)
    assert painted.any(), "no text was drawn"
    assert painted.mean() < 0.35, "text should sit on the band, not fill it"
