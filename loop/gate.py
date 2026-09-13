"""The metric gate. loop/critic.py decides with it; loop/train_eval.py --sweep shows its verdict per value, so the
patch agent can pick a change that passes before it edits. A candidate is accepted only if, on train, against the
last accepted version: exact_match strictly improves, no class and no capture condition loses more than 5 points,
and false_unknown_rate rises by at most 2 points.
"""
from __future__ import annotations

CLASS_DROP = 0.05
CONDITION_DROP = 0.05
FALSE_UNKNOWN_RISE = 0.02
EPS = 1e-9


def check(prev: dict | None, cand: dict) -> tuple[bool, str]:
    if prev is None:
        return True, "no previous version"
    pe, ce = prev["exact_match"], cand["exact_match"]
    if ce is None or pe is None:
        return False, "exact_match unavailable"
    if ce < pe - EPS:
        return False, f"exact_match fell {pe:.3f} -> {ce:.3f}"
    if ce <= pe + EPS:
        return False, f"no improvement: exact_match stayed {pe:.3f} (strict improvement required)"
    pc, cc = prev.get("per_class") or {}, cand.get("per_class") or {}
    drops = [(c, pc[c], v) for c, v in cc.items()
             if pc.get(c) is not None and v is not None and v < pc[c] - CLASS_DROP - EPS]
    if drops:
        c, a, b = drops[0]
        return False, f"class {c} fell {a:.2f} -> {b:.2f} (limit 5 points), {len(drops)} class(es) regressed"
    ps, cs = prev.get("per_slice") or {}, cand.get("per_slice") or {}
    falls = [(s, ps[s]["exact_match"], v["exact_match"]) for s, v in cs.items()
             if s in ps and ps[s].get("exact_match") is not None and v.get("exact_match") is not None
             and v["exact_match"] < ps[s]["exact_match"] - CONDITION_DROP - EPS]
    if falls:
        s, a, b = falls[0]
        return False, f"condition {s} fell {a:.2f} -> {b:.2f} (limit 5 points), {len(falls)} condition(s) regressed"
    fu, pfu = cand.get("false_unknown_rate"), prev.get("false_unknown_rate")
    if fu is not None and pfu is not None and fu > pfu + FALSE_UNKNOWN_RISE + EPS:
        return False, f"false_unknown_rate rose {pfu:.3f} -> {fu:.3f} (limit +0.02)"
    return True, f"exact_match {pe:.3f} -> {ce:.3f}"
