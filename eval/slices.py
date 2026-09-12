"""Accuracy by capture condition (camera angle, distance, lighting, person), so a patch that helps the front camera
while breaking the side view is visible to the critic and stopped by the metric gate.

Only tags the capture recorded are used. A tag with a single value says nothing and is skipped, and a slice
smaller than MIN_SLICE samples is too noisy to gate on.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from eval import scorers

SLICE_FIELDS = ("angle", "distance", "lighting", "person")
MIN_SLICE = 20


def per_slice(samples: list[dict], rows: list[dict]) -> dict[str, dict[str, Any]]:
    """{"angle=side": {"exact_match", "false_unknown_rate", "n_samples", "n_holds"}, ...}, hold-level like aggregate."""
    by_id = {s["id"]: s for s in samples}
    out: dict[str, dict[str, Any]] = {}
    for field in SLICE_FIELDS:
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            value = by_id.get(r["id"], {}).get(field)
            if value is not None:
                groups[str(value)].append(r)
        if len(groups) < 2:
            continue
        for value, members in sorted(groups.items()):
            if len(members) < MIN_SLICE:
                continue
            m = scorers.aggregate(members, bootstrap=0)
            out[f"{field}={value}"] = {"exact_match": m["exact_match"], "false_unknown_rate": m["false_unknown_rate"],
                                       "n_samples": m["n_samples"], "n_holds": m["n_holds"]}
    return out
