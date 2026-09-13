"""Scoring where a missing prediction is a failure everywhere, as SPEC.md 5.7 says.

eval/scorers.py is frozen and does not do that: its _is_unknown(None) is True, so output=None (classify() raised
or returned something other than a GestureState) counts as a correct rejection on negatives and as an exact match
on unknown targets. Changing scorers.py needs both team members. Until then, any comparison that must not reward
a crashing rules file scores rows with score_row_strict, applied identically to every version compared.
"""
from __future__ import annotations

import random
from collections import defaultdict
from typing import Any, Callable, Optional

from eval import scorers

ScoreFn = Callable[[dict], dict[str, Optional[bool]]]


def score_row_strict(row: dict) -> dict[str, Optional[bool]]:
    """scorers.score_row, except that a missing output fails every metric that applies to the row.
    false_unknown keeps the frozen value: on a positive, a missing answer is still counted as no answer."""
    frozen = scorers.score_row(row)
    if row.get("output") is not None:
        return frozen
    strict = {k: (None if v is None else False) for k, v in frozen.items()}
    strict["false_unknown"] = frozen["false_unknown"]
    return strict


def aggregate_with(rows: list[dict], score_fn: ScoreFn = scorers.score_row, bootstrap: int = 1000,
                   seed: int = 0) -> dict[str, Any]:
    """scorers.aggregate with the per-row scorer passed in. With scorers.score_row it returns exactly what
    scorers.aggregate returns (tested), so the only difference between frozen and strict is how a row is scored."""
    per_hold: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    per_class: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        s = score_fn(row)
        hold = str(row.get("hold_id") or row["id"])
        for k, v in s.items():
            if v is not None:
                per_hold[hold][scorers._ROW_TO_METRIC[k]].append(float(v))
        per_class[scorers.class_of(row["target"], row.get("kind", "positive"))].append(float(s["exact_match"]))

    hold_means: dict[str, list[float]] = defaultdict(list)
    for metrics in per_hold.values():
        for m, vals in metrics.items():
            hold_means[m].append(sum(vals) / len(vals))
    result: dict[str, Any] = {m: scorers._mean(hold_means.get(m, [])) for m in scorers.METRICS}
    result["per_class"] = {c: scorers._mean(v) for c, v in sorted(per_class.items())}
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


def hold_scores(rows: list[dict], score_fn: ScoreFn = score_row_strict, metric: str = "exact_match") -> dict[str, float]:
    """Per hold mean of one row metric, the unit every headline number in eval/ is computed on."""
    per: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        v = score_fn(row)[metric]
        if v is not None:
            per[str(row.get("hold_id") or row["id"])].append(float(v))
    return {h: sum(v) / len(v) for h, v in per.items()}


def paired_bootstrap(a: dict[str, float], b: dict[str, float], n: int = 2000, seed: int = 0) -> dict[str, Any]:
    """b minus a on the holds both versions were scored on, with a bootstrap 95% CI over holds."""
    holds = sorted(set(a) & set(b))
    if not holds:
        return {"holds": 0, "mean_difference": None, "ci95": None, "better": 0, "worse": 0, "unchanged": 0}
    diffs = [b[h] - a[h] for h in holds]
    rng = random.Random(seed)
    draws = sorted(sum(rng.choice(diffs) for _ in diffs) / len(diffs) for _ in range(n))
    return {"holds": len(holds), "mean_difference": sum(diffs) / len(diffs),
            "ci95": [draws[int(0.025 * n)], draws[min(n - 1, int(0.975 * n))]],
            "better": sum(1 for d in diffs if d > 1e-12), "worse": sum(1 for d in diffs if d < -1e-12),
            "unchanged": sum(1 for d in diffs if abs(d) <= 1e-12)}
