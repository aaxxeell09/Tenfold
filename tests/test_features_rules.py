import json

import numpy as np
import pytest

from classifier import features, rules
from classifier.schema import GestureState, validate_state, window_from_json, window_to_json
from loop import synth


def test_image_points_reconstructs_geometry():
    w = synth.contact_window(7, 8)
    l, r = features.last_valid_frame(w)
    lp, rp = features.image_points(l), features.image_points(r)
    assert np.allclose(lp[0], synth.LEFT_WRIST) and np.allclose(rp[0], synth.RIGHT_WRIST)
    # the overridden tips meet at MEET
    assert np.allclose(lp[features.TIP_INDEX[7]], synth.MEET) and np.allclose(rp[features.TIP_INDEX[8]], synth.MEET)


def test_distance_matrix_min_is_the_touching_pair():
    for lf, rf in [(6, 9), (7, 8), (10, 10), (8, 7)]:
        w = synth.contact_window(lf, rf)
        l, r = features.last_valid_frame(w)
        assert features.nearest_pair(l, r)[:2] == (lf, rf)
        d = features.tip_distance_matrix(l, r)
        assert d.shape == (5, 5) and d.min() < 0.05


def test_extension_flags():
    h = synth.make_hand(synth.LEFT_WRIST, side="left", folded=(8,))
    assert features.extension_vector(h).tolist() == [1, 1, 0, 1, 1]


def test_json_roundtrip():
    w = synth.contact_window(9, 10, n=3)
    w2 = window_from_json(json.loads(json.dumps(window_to_json(w))))
    assert len(w2) == 3 and features.nearest_pair(*features.last_valid_frame(w2))[:2] == (9, 10)


@pytest.mark.parametrize("lf,rf", [(7, 8), (8, 7), (6, 10), (9, 9)])
def test_rules_v0_contact(lf, rf):
    s = rules.classify(synth.contact_window(lf, rf, gap=0.1))
    assert s == GestureState("6-10", lf, rf, True, None, pytest.approx(0.9))


def test_rules_v0_near_contact_is_not_contact():
    s = rules.classify(synth.contact_window(7, 8, gap=0.8))
    assert s.method == "6-10" and (s.left, s.right) == (7, 8) and s.contact is False


def test_rules_v0_unknown_cases_never_raise():
    for name, w in synth.smoke_cases().items():
        s = rules.classify(w)
        assert validate_state(s) == [], (name, s)
        if name in ("both_absent", "one_hand", "degenerate", "nan"):
            assert s.method == "unknown", name
    assert rules.classify(synth.contact_window(7, 8, conf=0.2)).method == "unknown"


def test_short_window_still_classifies():
    assert rules.classify(synth.short_window()).contact is True
