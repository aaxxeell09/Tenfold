"""Two synthetic moments through Tutor.phrase, the path app/server.py uses, to check in Weave that each tutor.call
trace carries its tutor_rules_v2 score and a tutor_delivery note. It costs two short W&B Inference calls and prints
the call links, never a key.

    python eval/tutor_live_check.py                     # reads .env from the repo
    python eval/tutor_live_check.py --env ../tenfold/.env

The moments are synthetic (exercise 7 x 8, right finger from 9 to 8, then no contact; no child name) and every call
is renamed "nimble synthetic check", so it is never mistaken for a lesson. The first moment ends before its line is
returned, so its honest note is moment_ended_unserved. Nothing here hands a line to a page or speaks it.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lesson import tally, tutor_rules  # noqa: E402
from lesson import tutor as tutor_module  # noqa: E402
from tenfold.env import load_env  # noqa: E402

MOMENTS = [("wrong_right_finger", {"exercise": "7 x 8", "hint": {"hand": "right", "move_from": 9, "move_to": 8},
                                   "answer": None}),
           ("no_contact", {"exercise": "7 x 8", "hint": None, "answer": None})]
SCORE_TYPE = f"wandb.runnable.{tutor_rules.SCORER_OP}"
DISPLAY_NAME = "nimble synthetic check"


def settle(tutor: tutor_module.Tutor, limit: float) -> None:
    end = time.monotonic() + limit
    while time.monotonic() < end:
        with tutor._lock:
            if not tutor._inflight:
                return
        time.sleep(0.05)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=None, help="a .env file to read the W&B variables from")
    ap.add_argument("--wait", type=float, default=60.0, help="seconds to wait for each step")
    a = ap.parse_args()
    load_env(Path(a.env) if a.env else None)
    if not os.environ.get("WANDB_API_KEY"):
        print("tutor_live_check: WANDB_API_KEY is needed", file=sys.stderr)
        return 2
    tutor = tutor_module.Tutor(fallback=tally.phrase)
    if not tutor.trace:
        print("tutor_live_check: tracing is off (TENFOLD_TUTOR=0 or TENFOLD_TRACE=0)", file=sys.stderr)
        return 2

    for event, context in MOMENTS:            # the second moment ends the first one
        tutor.phrase(event, dict(context))
        settle(tutor, a.wait)
    queued = tutor.scoring.wait(a.wait)

    from weave.trace.context.weave_client_context import require_weave_client
    client = require_weave_client()
    lines = [tutor.cache.get(tutor._moment_key(event, context)) for event, context in MOMENTS]
    calls = [getattr(line, "call", None) for line in lines]
    if not all(calls):
        print("tutor_live_check: a moment has no traced model line (model or Weave unavailable)", file=sys.stderr)
        return 1
    for call in calls:
        call.set_display_name(DISPLAY_NAME)
    client.flush()
    print(f"queue      {'done' if queued else 'timed out'} {tutor.scoring.stats}")

    ok = True
    for i, ((event, _), line, call) in enumerate(zip(MOMENTS, lines, calls)):
        # only the first moment has ended, so only its call gets a tutor_delivery note
        wanted = {SCORE_TYPE, tutor_module.DELIVERY_FEEDBACK} if i == 0 else {SCORE_TYPE}
        found: dict[str, dict] = {}
        end = time.monotonic() + a.wait
        while time.monotonic() < end and not wanted <= set(found):
            found = {fb.feedback_type: fb.payload or {} for fb in client.get_call(call.id).feedback}
            time.sleep(2)
        print(f"\n{event}: {str(line)!r} ({line.latency_ms} ms, {line.model})")
        score = found.get(SCORE_TYPE, {}).get("output")
        if score:
            for rule in tutor_rules.RULES:
                r = score.get(rule) or {}
                print(f"  {rule:24s} {r.get('status')}: {r.get('reason')}")
        else:
            ok = False
            print(f"  no {SCORE_TYPE} feedback")
        note = found.get(tutor_module.DELIVERY_FEEDBACK)
        print(f"  {tutor_module.DELIVERY_FEEDBACK:24s} {note.get('status') if note else 'none'}")
        ok = ok and (note is not None or i > 0)
        print(f"  link {call.ui_url}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
