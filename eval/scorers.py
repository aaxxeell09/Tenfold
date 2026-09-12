"""The six scorers (SPEC.md 5.7). Frozen. Pure functions over dicts; `output=None` is a failure, nothing raises.

Row shape used everywhere in eval/: {"id", "hold_id", "kind", "target": label dict, "output": state dict | None}
"""
from __future__ import annotations

import random
from collections import defaultdict
from typing import Any, Optional

from classifier.schema import NEGATIVE_KINDS

Row = dict[str, Any]


def _is_unknown(output: Optional[dict]) -> bool:
    return output is None or output.get("method") == "unknown"


def class_of(target: dict, kind: str) -> str:
    if kind == "positive" and target.get("method") == "6-10":
        return f"{target.get('left')}x{target.get('right')}"
    if kind == "near_contact":
        return f"near:{target.get('left')}x{target.get('right')}"
    return kind


def exact_match(output: Optional[dict], target: dict) -> bool:
    if target.get("method") == "unknown":
        return _is_unknown(output)
    if _is_unknown(output):
        return False
    assert output is not None
    return (
        output.get("method") == target.get("method")
        and output.get("left") == target.get("left")
        and output.get("right") == target.get("right")
        and bool(output.get("contact")) == bool(target.get("contact"))
    )


def exact_match_unordered(output: Optional[dict], target: dict) -> bool:
    if target.get("method") == "unknown" or _is_unknown(output):
        return exact_match(output, target)
    assert output is not None
    return (
        output.get("method") == target.get("method")
        and {output.get("left"), output.get("right")} == {target.get("left"), target.get("right")}
        and bool(output.get("contact")) == bool(target.get("contact"))
    )


def contact_accuracy(output: Optional[dict], target: dict) -> Optional[bool]:
    """Only defined for 6-10 targets; None means 'does not apply'."""
    if target.get("method") != "6-10":
        return None
    if _is_unknown(output):
        return False
    assert output is not None
    return bool(output.get("contact")) == bool(target.get("contact"))


def false_unknown(output: Optional[dict], target: dict, kind: str) -> Optional[bool]:
    """1 when a positive sample was refused. Rate must stay under 5%."""
    if kind != "positive":
        return None
    return _is_unknown(output)


def negative_rejection(output: Optional[dict], target: dict, kind: str) -> Optional[bool]:
    if kind not in NEGATIVE_KINDS:
        return None
    return _is_unknown(output)


def near_contact_accuracy(output: Optional[dict], target: dict, kind: str) -> Optional[bool]:
    if kind != "near_contact":
        return None
    if _is_unknown(output):
        return False
    assert output is not None
    return not bool(output.get("contact"))


def score_row(row: Row) -> dict[str, Optional[bool]]:
    out, tgt, kind = row.get("output"), row["target"], row.get("kind", "positive")
    return {
        "exact_match": exact_match(out, tgt),
        "exact_match_unordered": exact_match_unordered(out, tgt),
        "contact_accuracy": contact_accuracy(out, tgt),
        "false_unknown": false_unknown(out, tgt, kind),
        "negative_rejection": negative_rejection(out, tgt, kind),
        "near_contact_accuracy": near_contact_accuracy(out, tgt, kind),
    }


METRICS = ("exact_match", "exact_match_unordered", "contact_accuracy", "false_unknown_rate",
           "negative_rejection_accuracy", "near_contact_accuracy")
_ROW_TO_METRIC = {
    "exact_match": "exact_match",
    "exact_match_unordered": "exact_match_unordered",
    "contact_accuracy": "contact_accuracy",
    "false_unknown": "false_unknown_rate",
    "negative_rejection": "negative_rejection_accuracy",
    "near_contact_accuracy": "near_contact_accuracy",
}


def _mean(vals: list[float]) -> Optional[float]:
    return sum(vals) / len(vals) if vals else None


def aggregate(rows: list[Row], bootstrap: int = 1000, seed: int = 0) -> dict[str, Any]:
    """Hold-level metrics: windows are averaged within a hold, holds are averaged across the split.
    Adds per_class exact_match, a bootstrap 95% CI on exact_match over holds, and counts."""
    per_hold: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    per_class: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        s = score_row(row)
        hold = str(row.get("hold_id") or row["id"])
        for k, v in s.items():
            if v is not None:
                per_hold[hold][_ROW_TO_METRIC[k]].append(float(v))
        per_class[class_of(row["target"], row.get("kind", "positive"))].append(float(s["exact_match"]))

    hold_means: dict[str, list[float]] = defaultdict(list)
    for hold, metrics in per_hold.items():
        for m, vals in metrics.items():
            hold_means[m].append(sum(vals) / len(vals))
    result: dict[str, Any] = {m: _mean(hold_means.get(m, [])) for m in METRICS}
    result["per_class"] = {c: _mean(v) for c, v in sorted(per_class.items())}
    result["n_samples"] = len(rows)
    result["n_holds"] = len(per_hold)

    em = hold_means.get("exact_match", [])
    if em and bootstrap > 0:
        rng = random.Random(seed)
        n = len(em)
        draws = sorted(sum(rng.choice(em) for _ in range(n)) / n for _ in range(bootstrap))
        result["exact_match_ci95"] = [draws[int(0.025 * bootstrap)], draws[min(bootstrap - 1, int(0.975 * bootstrap))]]
    else:
        result["exact_match_ci95"] = None
    return result
