"""Build data/tutor_quality.json: the tutor's lines as Weave scored them, for the dashboard's tutor quality section.

    python eval/tutor_quality.py                    (or: make tutor-quality)
    python eval/tutor_quality.py --env ../tenfold/.env

Read only. It lists the published tally-voice Evaluations and the traced tutor.call calls with their feedback, and
writes one small JSON file. It calls no model, runs no evaluation and costs nothing. The dashboard only reads the file,
so it needs no key, like data/snapshot.json. Not real time: run it again to refresh.

Formats are the scorer's own, read from the payloads rather than restated here:
  feedback wandb.runnable.tutor_rules_v2 on a tutor.call: {"output": {"scorer_version", "latency_ms",
      <rule>: {"status", "passed", "reason"}}}, status pass, fail, not_applicable or not_verifiable
  feedback tutor_delivery on a tutor.call: {"status", "detail", "source", "scorer_version"}
  Evaluation summaries: {"tally_rules_v2": {<rule>: {"passed": {"true_count", "true_fraction"}}, "latency_ms"},
      "child_judge": {"warmth": {"mean"}, "clarity": {"mean"}}}

What the file never claims: that a line was spoken (no delivery status says so), that a line came from a real lesson
when its trace carries no lesson marker, or a score for a line that has none. No key and no learner name is written:
tutor.call records the event, the exercise, the expected correction, the typed answer and the model, nothing else.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "data" / "tutor_quality.json"
PROJECT = "tenfold"
SCORE_PREFIX = "wandb.runnable.tutor_rules"
DELIVERY = "tutor_delivery"
EVALUATION_PREFIX = "tally-voice"
DATASET = "tally-moments"
TIMING_RULES = frozenset({"in_time"})       # timing, reported with speed, never inside a quality rate
SYNTHETIC_ATTRIBUTE = "nimble_check"        # an attribute some synthetic check calls carry
CANCELLED = frozenset({"moment_ended_unserved", "dropped_late", "late_held"})
PENDING_MINUTES = 10                        # an unscored line younger than this may still be in the scoring queue
MAX_ROWS = 25
MAX_CALLS = 300


# The model is given the child's name (lesson/tutor.py build_messages) and may say it back. The trace does not record
# the name, so it cannot be matched: a capitalised word that does not open a sentence, or that opens one as a call
# ("Mia, move..."), is hidden. It can hide a word it should not; it never adds one.
KEEP_CAPITALIZED = frozenset({"Tally", "Nimble", "I", "OK"})
SENTENCE_OPENERS = frozenset("""Yes No Great Good Nice Well Wow Almost Perfect Awesome Excellent Oops Hmm Okay Ok Hi Hello
    Hey Right Correct Super Brilliant Amazing Fantastic Cool Yay Sure Ready Look Now Then So Oh Alright Hooray Bravo
    Careful Remember Try Again Close Keep Go Done""".split())
_CAPITALIZED = re.compile(r"\b[A-Z][a-zà-ÿ]+(?:['’-][A-Za-zà-ÿ]+)*\b")


def redact_names(text: Any) -> tuple[Any, bool]:
    """The line with possible names replaced by [name], and whether anything was hidden."""
    if not isinstance(text, str) or not text:
        return text, False
    parts, last, changed = [], 0, False
    for m in _CAPITALIZED.finditer(text):
        word = m.group(0)
        if word in KEEP_CAPITALIZED:
            continue
        before = text[:m.start()].rstrip(" \"'“‘(")
        opens_sentence = not before or before[-1] in ".!?:"
        called = text[m.end():m.end() + 1] in (",", "!")
        if opens_sentence and (word in SENTENCE_OPENERS or not called):
            continue
        parts += [text[last:m.start()], "[name]"]
        last, changed = m.end(), True
    return "".join(parts) + text[last:], changed


def plain(value: Any) -> Any:
    """Weave objects as plain JSON values."""
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, datetime):
        return iso(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")
    return str(value)


def parse_time(value: Any) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def version_of(name: str) -> str:
    """'tally_rules_v2' or 'tutor-rules-v2' gives 'v2'; the first scorer had no suffix and is 'v1'."""
    tail = str(name).replace("-", "_").rsplit("_", 1)[-1]
    return tail if tail.startswith("v") and tail[1:].isdigit() else "v1"


# ---------- offline: the published Evaluations ----------

def evaluation_run(display_name: Any, started_at: Any, url: Optional[str], output: Any) -> Optional[dict]:
    """One published tally-voice Evaluation for one model, with the fractions Weave summarised."""
    name = str(display_name or "")
    if not name.startswith(EVALUATION_PREFIX) or not isinstance(output, dict):
        return None
    evaluation, _, model = name.partition(" ")
    scorer = next((k for k in output if str(k).startswith("tally_rules")), None)
    if scorer is None:
        return None
    summary = output.get(scorer) if isinstance(output.get(scorer), dict) else {}
    rules: dict[str, dict] = {}
    for rule, value in summary.items():
        if rule == "latency_ms" or not isinstance(value, dict):
            continue
        passed = value.get("passed", value) if isinstance(value.get("passed", value), dict) else {}
        count, fraction = passed.get("true_count"), passed.get("true_fraction")
        decided = (round(count / fraction) if isinstance(count, (int, float)) and isinstance(fraction, (int, float))
                   and fraction > 0 else None)
        rules[rule] = {"pass_fraction": fraction, "pass_count": count, "decided": decided}
    judge = output.get("child_judge") if isinstance(output.get("child_judge"), dict) else {}

    def mean(key: str) -> Optional[float]:
        entry = judge.get(key)
        return entry.get("mean") if isinstance(entry, dict) else None

    latency = summary.get("latency_ms") if isinstance(summary.get("latency_ms"), dict) else {}
    return {"evaluation": evaluation, "model": model or None, "started_at": iso(started_at), "url": url,
            "scorer": scorer, "rules_version": version_of(scorer), "rules": rules,
            "warmth": mean("warmth"), "clarity": mean("clarity"), "latency_ms_mean": latency.get("mean")}


def offline_block(runs: list[Optional[dict]], moments: Optional[int], leaderboards: dict[str, str]) -> dict:
    """The newest published Evaluation, its latest run per model, and the older ones named but not mixed in."""
    runs = [r for r in runs if r]
    if not runs:
        return {"status": "unavailable", "reason": "no published tally-voice Evaluation was found"}
    newest = max(runs, key=lambda r: r["started_at"] or "")
    current = sorted((r for r in runs if r["evaluation"] == newest["evaluation"]), key=lambda r: r["started_at"] or "")
    latest = {r["model"]: r for r in current}
    older = Counter((r["evaluation"], r["rules_version"]) for r in runs if r["evaluation"] != newest["evaluation"])
    return {"status": "ok", "evaluation": newest["evaluation"], "scorer": newest["scorer"],
            "rules_versions": sorted({r["rules_version"] for r in latest.values()}),
            "moments": moments, "published_at": max((r["started_at"] or "" for r in current), default="") or None,
            "leaderboard_url": leaderboards.get(newest["evaluation"]),
            "runs": sorted(latest.values(), key=lambda r: r["model"] or ""),
            "other_versions": [{"evaluation": e, "rules_version": v, "runs": n} for (e, v), n in sorted(older.items())]}


# ---------- live: the traced tutor.call lines ----------

def call_record(call: Any) -> dict:
    """The fields of a Weave call this file needs, as plain values; tests build these dicts directly."""
    feedback = [{"type": getattr(f, "feedback_type", None), "payload": plain(getattr(f, "payload", None) or {}),
                 "created_at": iso(getattr(f, "created_at", None))} for f in (getattr(call, "feedback", None) or [])]
    return {"id": str(call.id), "started_at": iso(call.started_at), "display_name": call.display_name,
            "attributes": plain(dict(call.attributes or {})), "inputs": plain(dict(call.inputs or {})),
            "output": plain(call.output) if isinstance(call.output, dict) else {},
            "exception": bool(call.exception), "url": getattr(call, "ui_url", None), "feedback": feedback}


def origin_of(record: dict) -> str:
    """synthetic_check when the trace says so; otherwise not_identified. tutor.call carries no lesson marker today, so
    no line is ever assumed to come from a lesson."""
    if SYNTHETIC_ATTRIBUTE in (record.get("attributes") or {}) or "synthetic" in str(record.get("display_name") or "").lower():
        return "synthetic_check"
    return "not_identified"


def _is_score(feedback: dict) -> bool:
    return str(feedback.get("type") or "").startswith(SCORE_PREFIX)


def intervention_row(record: dict, first_scored_at: Optional[datetime], now: datetime) -> dict:
    """One line: its score if Weave has one, its delivery notes, and a state that never turns a missing score into one."""
    scores = sorted((f for f in record["feedback"] if _is_score(f)), key=lambda f: f.get("created_at") or "")
    notes = sorted((f for f in record["feedback"] if f.get("type") == DELIVERY and isinstance(f.get("payload"), dict)),
                   key=lambda f: f.get("created_at") or "")
    started = parse_time(record.get("started_at"))
    if scores:
        state = "scored"
    elif first_scored_at is None or started is None or started < first_scored_at:
        state = "before_scoring"
    elif now - started <= timedelta(minutes=PENDING_MINUTES):
        state = "pending"
    else:
        state = "score_missing"
    output = (scores[-1]["payload"] or {}).get("output") if scores else None
    output = output if isinstance(output, dict) else {}
    checks = {rule: {"status": value.get("status"), "reason": value.get("reason")}
              for rule, value in output.items() if isinstance(value, dict) and "status" in value}
    inputs = record.get("inputs") or {}
    context = inputs.get("context") if isinstance(inputs.get("context"), dict) else {}
    hint = context.get("hint") if isinstance(context.get("hint"), dict) else None
    out = record.get("output") or {}
    text, redacted = redact_names(out.get("text"))
    return {"id": record["id"], "started_at": record.get("started_at"), "origin": origin_of(record), "state": state,
            "event": inputs.get("event"), "exercise": context.get("exercise"), "hint": hint,
            "answer": context.get("answer"), "model": out.get("model") or inputs.get("model"), "text": text,
            "text_redacted": redacted,
            "latency_ms": out.get("latency_ms"), "exception": bool(record.get("exception")),
            "rules_version": version_of(output.get("scorer_version") or scores[-1]["type"]) if scores else None,
            "checks": checks, "delivery": [n["payload"].get("status") for n in notes if n["payload"].get("status")],
            "url": record.get("url")}


def live_block(records: list[dict], now: datetime, limit_reached: bool) -> dict:
    """The latest lines, and a lesson summary whose denominators are stated: scored lines marked as a lesson and not
    cancelled; checks that passed or failed, timing apart."""
    scored_times = [parse_time(r.get("started_at")) for r in records if any(_is_score(f) for f in r["feedback"])]
    first = min((t for t in scored_times if t), default=None)
    rows = sorted((intervention_row(r, first, now) for r in records), key=lambda r: r["started_at"] or "", reverse=True)
    scored_lessons = [r for r in rows if r["state"] == "scored" and r["origin"] == "lesson"]
    lessons = [r for r in scored_lessons if not set(r["delivery"]) & CANCELLED]
    decided = passed = 0
    failures: Counter[str] = Counter()
    for r in lessons:
        for rule, check in r["checks"].items():
            if rule in TIMING_RULES or check["status"] not in ("pass", "fail"):
                continue
            decided += 1
            if check["status"] == "pass":
                passed += 1
            else:
                failures[f"{rule}: {check['reason']}"] += 1
    latencies = [r["latency_ms"] for r in lessons if isinstance(r["latency_ms"], (int, float))]
    return {"status": "ok", "calls_read": len(records), "read_limit_reached": limit_reached,
            "first_scored_at": iso(first), "states": dict(Counter(r["state"] for r in rows)),
            "scored_by_origin": dict(Counter(r["origin"] for r in rows if r["state"] == "scored")),
            "lesson_summary": {"lines": len(lessons), "cancelled_excluded": len(scored_lessons) - len(lessons),
                               "checks_decided": decided, "checks_passed": passed,
                               "top_failures": [{"reason": k, "count": v} for k, v in failures.most_common(3)],
                               "median_latency_ms": statistics.median(latencies) if latencies else None,
                               "rules_versions": sorted({r["rules_version"] for r in lessons if r["rules_version"]})},
            "rows": [r for r in rows if r["state"] != "before_scoring"][:MAX_ROWS]}


# ---------- the file ----------

def build(fetch: Callable[[], dict], previous: Optional[dict], now: datetime) -> dict:
    """The file to write. A Weave that cannot be read keeps the previous data and records the failed attempt."""
    try:
        raw = fetch()
    except Exception as e:  # no key, network, auth, SDK: the lesson data already written stays readable
        kept = dict(previous) if isinstance(previous, dict) else {"offline": {"status": "unavailable"},
                                                                 "live": {"status": "unavailable"}}
        kept["last_refresh_error"] = {"at": iso(now), "error": type(e).__name__}
        return kept
    calls = raw.get("calls") or []
    return {"generated_at": iso(now), "source": "Weave, read only", "weave_url": raw.get("weave_url"),
            "offline": offline_block([evaluation_run(**e) for e in raw.get("evaluations") or []], raw.get("moments"),
                                     raw.get("leaderboards") or {}),
            "live": live_block(calls, now, len(calls) >= MAX_CALLS)}


def fetch_weave() -> dict:
    """Everything the file needs, read from Weave project tenfold. Reads only: get_calls and object lookups."""
    if not os.environ.get("WANDB_API_KEY"):
        raise RuntimeError("WANDB_API_KEY is not set")
    import weave
    client = weave.init(PROJECT)
    base = f"weave:///{client.entity}/{client.project}"
    calls = [call_record(c) for c in client.get_calls(filter={"op_names": [f"{base}/op/tutor.call:*"]}, limit=MAX_CALLS,
                                                       include_feedback=True,
                                                       sort_by=[{"field": "started_at", "direction": "desc"}])]
    evaluations = [{"display_name": c.display_name, "started_at": c.started_at, "url": c.ui_url, "output": plain(c.output)}
                   for c in client.get_calls(filter={"op_names": [f"{base}/op/Evaluation.evaluate:*"]}, limit=200,
                                             columns=["id", "display_name", "started_at", "output"])]
    moments = None
    try:
        moments = len(list(client.get(weave.ref(f"{DATASET}:latest")).rows))
    except Exception:
        pass
    leaderboards: dict[str, str] = {}
    for name in {str(e["display_name"] or "").partition(" ")[0] for e in evaluations}:
        if not name.startswith(EVALUATION_PREFIX):
            continue
        board = name.replace(EVALUATION_PREFIX, f"{EVALUATION_PREFIX}-leaderboard", 1)
        try:
            client.get(weave.ref(f"{board}:latest"))
            leaderboards[name] = f"https://wandb.ai/{client.entity}/{client.project}/weave/leaderboards/{board}"
        except Exception:
            pass
    return {"weave_url": f"https://wandb.ai/{client.entity}/{client.project}/weave", "calls": calls,
            "evaluations": evaluations, "moments": moments, "leaderboards": leaderboards}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=None, help="a .env file to read the W&B variables from (default: the repo's)")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    sys.path.insert(0, str(REPO))
    from tenfold.env import load_env
    load_env(Path(a.env) if a.env else REPO / ".env")
    out = Path(a.out)
    try:
        previous = json.loads(out.read_text()) if out.exists() else None
    except (OSError, json.JSONDecodeError):
        previous = None
    now = datetime.now(timezone.utc)
    data = build(fetch_weave, previous, now)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(out)  # a reader never sees a half written file
    error = data.get("last_refresh_error") or {}
    if error.get("at") == iso(now):
        print(f"tutor_quality: Weave not read ({error.get('error')}), previous data kept in {out}", file=sys.stderr)
        return 1
    off, live = data["offline"], data["live"]
    print(f"offline  {off.get('status')} {off.get('evaluation') or ''} runs {len(off.get('runs') or [])} moments {off.get('moments')}")
    print(f"live     calls {live.get('calls_read')} states {live.get('states')} lesson lines {live['lesson_summary']['lines']}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
