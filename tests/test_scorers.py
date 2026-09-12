import pytest

from eval import scorers

POS = {"method": "6-10", "left": 7, "right": 8, "contact": True}
NEAR = {"method": "6-10", "left": 7, "right": 8, "contact": False}
UNK = {"method": "unknown"}


def out(left=7, right=8, contact=True, method="6-10"):
    return {"method": method, "left": left, "right": right, "contact": contact, "folded": None, "confidence": 0.9}


def test_exact_match_and_none():
    assert scorers.exact_match(out(), POS)
    assert not scorers.exact_match(out(contact=False), POS)
    assert not scorers.exact_match(None, POS)
    assert scorers.exact_match(None, UNK) and scorers.exact_match(out(method="unknown"), UNK)
    assert not scorers.exact_match(out(), UNK)


def test_unordered():
    assert not scorers.exact_match(out(8, 7), POS)
    assert scorers.exact_match_unordered(out(8, 7), POS)


def test_conditional_scorers_return_none_when_not_applicable():
    assert scorers.contact_accuracy(out(), UNK) is None
    assert scorers.false_unknown(out(), POS, "near_contact") is None
    assert scorers.negative_rejection(None, UNK, "positive") is None
    assert scorers.near_contact_accuracy(out(), NEAR, "positive") is None
    assert scorers.false_unknown(None, POS, "positive") is True
    assert scorers.negative_rejection(None, UNK, "transition") is True
    assert scorers.near_contact_accuracy(out(contact=False), NEAR, "near_contact") is True
    assert scorers.near_contact_accuracy(None, NEAR, "near_contact") is False


def test_aggregate_known_fixture():
    rows = [
        # hold A: 2 windows, one right one wrong -> 0.5
        {"id": "1", "hold_id": "A", "kind": "positive", "target": POS, "output": out()},
        {"id": "2", "hold_id": "A", "kind": "positive", "target": POS, "output": out(contact=False)},
        # hold B: 1 window right -> 1.0
        {"id": "3", "hold_id": "B", "kind": "positive", "target": {**POS, "left": 6}, "output": out(left=6)},
        # hold C: near contact, predicted contact -> 0
        {"id": "4", "hold_id": "C", "kind": "near_contact", "target": NEAR, "output": out()},
        # hold D: transition correctly refused -> 1
        {"id": "5", "hold_id": "D", "kind": "transition", "target": UNK, "output": None},
        # hold E: partial hand wrongly classified -> 0
        {"id": "6", "hold_id": "E", "kind": "partial_hand", "target": UNK, "output": out()},
    ]
    m = scorers.aggregate(rows, bootstrap=200)
    assert m["n_samples"] == 6 and m["n_holds"] == 5
    assert m["exact_match"] == pytest.approx((0.5 + 1 + 0 + 1 + 0) / 5)
    assert m["contact_accuracy"] == pytest.approx((0.5 + 1 + 0) / 3)
    assert m["false_unknown_rate"] == 0.0
    assert m["negative_rejection_accuracy"] == pytest.approx(0.5)
    assert m["near_contact_accuracy"] == 0.0
    assert set(m["per_class"]) == {"7x8", "6x8", "near:7x8", "transition", "partial_hand"}
    assert m["per_class"]["7x8"] == 0.5 and m["per_class"]["transition"] == 1.0
    lo, hi = m["exact_match_ci95"]
    assert 0.0 <= lo <= m["exact_match"] <= hi <= 1.0


def test_aggregate_empty_metric_is_none():
    rows = [{"id": "1", "hold_id": "A", "kind": "positive", "target": POS, "output": out()}]
    m = scorers.aggregate(rows, bootstrap=10)
    assert m["near_contact_accuracy"] is None and m["negative_rejection_accuracy"] is None
