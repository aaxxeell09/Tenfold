"""Which W&B Inference model should be Tally's voice? One Weave Evaluation per model over the lesson's real moments,
and a Weave Leaderboard that ranks them.

    python eval/tutor_eval.py                          # the default models, published to Weave project tenfold
    python eval/tutor_eval.py --models Qwen/Qwen3-235B-A22B-Instruct-2507 openai/gpt-oss-120b
    python eval/tutor_eval.py --local --limit 6        # no Weave: calls the models, prints the table

Every row is one lesson moment: an event of lesson/tally.py with the context app/server.py passes. Each model gets
the exact prompt the live tutor sends (lesson.tutor.build_messages) under the live tutor's settings, and its reply
is scored in code by lesson/tutor_rules.py, the same rules the live lesson attaches to every tutor.call:
  non_empty, concise, safe_words, no_spoiler, correction_consistency, in_time   (see lesson/tutor_rules.py)
plus keeps_numbers, which needs Tally's own line for the moment and so runs offline only: every number in Tally's
line survives. Each rule is pass, fail, not_applicable or not_verifiable, and only pass and fail count in a fraction.
A judge model on W&B Inference (never one of the candidates) rates warmth and clarity to a 7 year old, 1 to 5.

Scorer version: tutor_rules.SCORER_VERSION, published as Evaluation EVALUATION with scorer tally_rules_v2 and
Leaderboard LEADERBOARD. The earlier tally-voice runs used the v1 rules (digits only spoiler check, no hand or
from/to check): their scores are not comparable with these, which is why the names changed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import httpx  # noqa: E402

from lesson import tally, tutor_rules  # noqa: E402
from lesson.tutor import DEFAULT_BASE_URL, build_messages, clean_reply  # noqa: E402
from tenfold.env import load_env  # noqa: E402

MODELS = ["Qwen/Qwen3-235B-A22B-Instruct-2507", "Qwen/Qwen3-30B-A3B-Instruct-2507", "meta-llama/Llama-3.3-70B-Instruct",
          "deepseek-ai/DeepSeek-V4-Flash", "openai/gpt-oss-120b"]
JUDGE = "deepseek-ai/DeepSeek-V3.1"
IN_TIME_MS = tutor_rules.IN_TIME_MS  # lesson.tutor.Tutor's default timeout: a later line waits for the next time the moment happens
EXERCISES = [(7, 8), (6, 9), (9, 7)]
RULES = tutor_rules.RULES + ("keeps_numbers",)
EVALUATION = "tally-voice-v2"
LEADERBOARD = "tally-voice-leaderboard-v2"
SCORER = "tally_rules_v2"
NUMBER_WORDS = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve "
                                                "thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}


# ---------- the dataset: lesson moments ----------

def moments() -> list[dict[str, Any]]:
    """One row per lesson moment, with Tally's own line for it. Pose and answer moments for every exercise."""
    rows: list[dict[str, Any]] = []

    def add(event: str, exercise: str | None, **ctx: Any) -> None:
        context = {"hint": ctx.pop("hint", None), "answer": ctx.pop("answer", None), "exercise": exercise or "", **ctx}
        rows.append({"id": f"{event}:{exercise or '-'}", "event": event, "context": context,
                     "line": tally.phrase(event, context)})

    add("intro", None)
    add("end_success", None, tomorrow="8 x 9")
    add("end_tired", None)
    add("cannot_see", None)
    for a, b in EXERCISES:
        ex, product = f"{a} x {b}", a * b
        wrong_right = b + 1 if b < 10 else b - 1
        wrong_left = a + 1 if a < 10 else a - 1
        add("exercise_shown", ex)
        add("next_new", ex)
        add("no_contact", ex)
        add("one_hand", ex)
        add("hands_swapped", ex)
        add("wrong_right_finger", ex, hint={"hand": "right", "move_from": wrong_right, "move_to": b})
        add("wrong_left_finger", ex, hint={"hand": "left", "move_from": wrong_left, "move_to": a})
        add("hint_1", ex, hint={"hand": "right", "move_from": wrong_right, "move_to": b})
        add("correct_pose", ex)
        add("recount_tens", ex, answer=product + 10)
        add("answer_wrong", ex, answer=product + 10)
        add("answer_correct", ex, answer=product)
    return rows


# ---------- scoring, in code ----------

def numbers_in(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z]+|\d+", text.lower())
    return {NUMBER_WORDS.get(w, w) for w in words if w.isdigit() or w in NUMBER_WORDS}


def check_keeps_numbers(line: str, text: str) -> bool:
    return numbers_in(line) <= numbers_in(text)


def keeps_numbers(line: str, text: str) -> tutor_rules.Check:
    if not text.strip():
        return tutor_rules.Check(tutor_rules.NOT_APPLICABLE, "nothing was said")
    missing = sorted(numbers_in(line) - numbers_in(text))
    if missing:
        return tutor_rules.Check(tutor_rules.FAIL, f"drops {', '.join(missing)} from Tally's line")
    return tutor_rules.Check(tutor_rules.PASS, "every number of Tally's line is said")


def score_row(row: dict, output: dict) -> dict[str, Any]:
    """The shared rules on one reply, plus keeps_numbers. Each rule is {"status", "passed", "reason"}."""
    text = output.get("text") or ""
    scores = tutor_rules.score_intervention(row["event"], row["context"], text, output.get("latency_ms"), IN_TIME_MS)
    scores["keeps_numbers"] = keeps_numbers(row["line"], text).as_dict()
    return scores


def parse_judge(content: str | None) -> dict[str, int | None]:
    """{"warmth": 1-5, "clarity": 1-5} from the judge's reply; None for what it did not give."""
    m = re.search(r"\{.*\}", str(content or "").split("</think>")[-1], re.DOTALL)
    out: dict[str, int | None] = {"warmth": None, "clarity": None}
    try:
        data = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        data = {}
    for k in out:
        try:
            v = int(data.get(k))
            out[k] = v if 1 <= v <= 5 else None
        except (TypeError, ValueError):
            pass
    return out


# ---------- W&B Inference ----------

def chat(model: str, messages: list[dict], max_tokens: int, temperature: float) -> dict:
    headers = {"Authorization": f"Bearer {os.environ['WANDB_API_KEY']}"}
    if os.environ.get("WANDB_INFERENCE_PROJECT"):
        headers["OpenAI-Project"] = os.environ["WANDB_INFERENCE_PROJECT"]
    base = (os.environ.get("WANDB_INFERENCE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    last: Exception | None = None
    for attempt in range(3):
        try:
            r = httpx.post(f"{base}/chat/completions", headers=headers, timeout=60, json={
                "model": model, "max_tokens": max_tokens, "temperature": temperature, "messages": messages})
            if r.status_code == 429 or r.status_code >= 500:
                raise httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"W&B Inference failed for {model}: {last}")


def speak(model: str, event: str, context: dict) -> dict:
    """What the live tutor would hear from `model` for this moment: same prompt, same max_tokens and temperature."""
    t0 = time.monotonic()
    try:
        body = chat(model, build_messages(event, context, tally.phrase(event, context)), 60, 0.7)
    except RuntimeError as e:
        return {"text": "", "latency_ms": None, "usage": None, "model": model, "error": str(e)[:200]}
    return {"text": clean_reply(body["choices"][0]["message"].get("content")),
            "latency_ms": int((time.monotonic() - t0) * 1000), "usage": body.get("usage"),
            "model": body.get("model") or model}


def judge(event: str, line: str, text: str, judge_model: str) -> dict[str, int | None]:
    if not text:
        return {"warmth": None, "clarity": None}
    prompt = (f"A tutor for a 7 year old learning finger multiplication had to say, for the moment '{event}': "
              f'"{line}". It said instead: "{text}". Rate the reply for a 7 year old. warmth: 1 cold to 5 warm and '
              "encouraging. clarity: 1 confusing to 5 a child knows exactly what to do. Reply with JSON only: "
              '{"warmth": <1-5>, "clarity": <1-5>}')
    try:
        body = chat(judge_model, [{"role": "user", "content": prompt}], 40, 0.0)
    except RuntimeError:
        return {"warmth": None, "clarity": None}
    return parse_judge(body["choices"][0]["message"].get("content"))


# ---------- runs ----------

def summarize(model: str, rows: list[dict], outputs: list[dict], scores: list[dict], judged: list[dict]) -> dict:
    """Per rule, the fraction of pass among the rows where the rule gave pass or fail; not_verifiable is counted apart."""
    n = len(rows)
    lat = [o["latency_ms"] for o in outputs if o.get("latency_ms") is not None]
    out: dict[str, Any] = {"model": model, "n": n, "errors": sum(1 for o in outputs if o.get("error")),
                           "scorer_version": tutor_rules.SCORER_VERSION}
    for k in RULES:
        decided = [s[k]["passed"] for s in scores if s[k]["passed"] is not None]
        out[k] = round(sum(decided) / len(decided), 3) if decided else None
    out["not_verifiable"] = sum(1 for s in scores for k in RULES if s[k]["status"] == tutor_rules.NOT_VERIFIABLE)
    for k in ("warmth", "clarity"):
        vals = [j[k] for j in judged if j.get(k) is not None]
        out[k] = round(statistics.mean(vals), 2) if vals else None
    out["p50_ms"] = int(statistics.median(lat)) if lat else None
    out["tokens"] = sum(((o.get("usage") or {}).get("total_tokens") or 0) for o in outputs)
    return out


def run_local(models: list[str], rows: list[dict], judge_model: str) -> list[dict]:
    results = []
    for model in models:
        outputs = [speak(model, r["event"], r["context"]) for r in rows]
        scores = [score_row(r, o) for r, o in zip(rows, outputs)]
        judged = [judge(r["event"], r["line"], o["text"], judge_model) for r, o in zip(rows, outputs)]
        results.append(summarize(model, rows, outputs, scores, judged))
        for r, o in list(zip(rows, outputs))[:3]:
            print(f"  {model.split('/')[-1]:32s} {r['id']:24s} {o['text']!r}")
    return results


def run_weave(models: list[str], rows: list[dict], judge_model: str) -> list[dict]:
    import weave
    from weave.flow import leaderboard
    from weave.trace.ref_util import get_ref

    ent = os.environ.get("WANDB_ENTITY")
    project = "tenfold" + os.environ.get("TENFOLD_PROJECT_SUFFIX", "")
    client = weave.init(f"{ent}/{project}" if ent else project)

    class TallyVoice(weave.Model):
        """Tally's voice on one W&B Inference model, prompted exactly like the live tutor."""
        model: str

        @weave.op
        def predict(self, event: str, context: dict) -> dict:
            return speak(self.model, event, context)

    @weave.op(name=SCORER)
    def tally_rules_v2(event: str, context: dict, line: str, output: dict) -> dict:
        return score_row({"event": event, "context": context, "line": line}, output)

    @weave.op
    def child_judge(event: str, line: str, output: dict) -> dict:
        return {**judge(event, line, output.get("text") or "", judge_model), "judge": judge_model}

    evaluation = weave.Evaluation(name=EVALUATION, dataset=weave.Dataset(name="tally-moments", rows=rows),
                                  scorers=[tally_rules_v2, child_judge])
    results = []
    for model in models:
        summary = asyncio.run(evaluation.evaluate(TallyVoice(model=model),
                                                  __weave={"display_name": f"{EVALUATION} {model.split('/')[-1]}"}))
        results.append({"model": model, **flatten(summary)})
    try:  # the evaluations are already published; a leaderboard failure must not lose the table
        ref = get_ref(evaluation).uri()
        columns = [leaderboard.LeaderboardColumn(evaluation_object_ref=ref, scorer_name=SCORER,
                                                 summary_metric_path=f"{k}.passed.true_fraction") for k in RULES]
        columns += [leaderboard.LeaderboardColumn(evaluation_object_ref=ref, scorer_name="child_judge",
                                                  summary_metric_path=f"{k}.mean") for k in ("warmth", "clarity")]
        weave.publish(leaderboard.Leaderboard(
            name=LEADERBOARD, description=("Which W&B Inference model speaks for Tally. Rows are lesson moments from "
                                           f"lesson/tally.py; scores are {tutor_rules.SCORER_VERSION} in code "
                                           "(pass among pass or fail) plus a judge model."),
            columns=columns))
    except Exception as e:
        print(f"tutor_eval: leaderboard not published ({type(e).__name__}: {e})", file=sys.stderr)
    try:
        client.finish()
    except Exception:
        pass
    return results


def flatten(summary: dict) -> dict:
    """The Evaluation summary as one flat row for the printed table."""
    rules, judged = summary.get(SCORER) or {}, summary.get("child_judge") or {}
    row: dict[str, Any] = {k: ((rules.get(k) or {}).get("passed") or {}).get("true_fraction") for k in RULES}
    row.update({k: (judged.get(k) or {}).get("mean") for k in ("warmth", "clarity")})
    lat = summary.get("model_latency") or {}
    row["mean_s"] = round(lat["mean"], 2) if isinstance(lat.get("mean"), (int, float)) else None
    return row


def print_table(results: list[dict]) -> None:
    keys = [k for k in (*RULES, "not_verifiable", "warmth", "clarity", "p50_ms", "mean_s", "tokens", "errors")
            if any(k in r for r in results)]
    print("\n| model | " + " | ".join(keys) + " |\n|---|" + "---|" * len(keys))
    for r in results:
        print(f"| {r['model']} | " + " | ".join("" if r.get(k) is None else str(r[k]) for k in keys) + " |")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=None)
    ap.add_argument("--judge", default=os.environ.get("TENFOLD_TUTOR_JUDGE", JUDGE))
    ap.add_argument("--limit", type=int, default=None, help="only the first N moments")
    ap.add_argument("--local", action="store_true", help="no Weave: call the models and print the table")
    ap.add_argument("--out", default=str(REPO / "eval" / "results" / "tutor-eval.json"))
    a = ap.parse_args()
    load_env()
    if not os.environ.get("WANDB_API_KEY"):
        print("tutor_eval: WANDB_API_KEY is needed, every model runs on W&B Inference", file=sys.stderr)
        return 2
    models = a.models or MODELS
    if a.judge in models:
        print(f"tutor_eval: the judge {a.judge} cannot also be a candidate", file=sys.stderr)
        return 2
    rows = moments()[: a.limit] if a.limit else moments()
    results = run_local(models, rows, a.judge) if a.local else run_weave(models, rows, a.judge)
    print_table(results)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"judge": a.judge, "scorer_version": tutor_rules.SCORER_VERSION,
                               "n_moments": len(rows), "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
