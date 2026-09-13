"""The self-improvement loop orchestrator. Runs from the main repo; the agents run in a stripped worktree.

    python loop/critic.py --iterations 1 --dry-run          # diagnostic, patch, guard; print the diff; no commit
    python loop/critic.py --iterations 1                    # one full guarded, gated iteration
    python loop/critic.py --iterations 20 --no-early-stop   # the nightly
    python loop/critic.py --iterations 2 --mock-claude tests/fake_claude.py --local   # no API at all
    python loop/critic.py --blind                           # ablation arm, worktree ../tenfold-blind

One iteration (SPEC.md section 7): reset worktree -> diagnostic -> patch -> guard agent -> guard.py ->
train eval -> metric gate -> commit (author critic-agent) + data/BEST_VERSION -> held-out eval subprocess.
Every step is a weave op when WANDB_API_KEY is set. The critic's environment never contains the held-out key.

The informed worktree gets a train copy (data/train.jsonl), the train report and loop/train_eval.py so the patch
agent can test its hypothesis. The blind arm gets none of them and never touches the repo's rules.py, report,
metrics or git history: it keeps its accepted rules in loop/blind-rules.py and its metrics in
data/metrics-blind.json, with every tag suffixed -blind.
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tenfold.env import load_env  # noqa: E402

RULES_REL = Path("classifier/rules.py")
REPORT_REL = Path("eval/last_train_report.json")
CRITIC_FILES = ["classifier/__init__.py", "classifier/schema.py", "classifier/features.py", "eval/__init__.py",
                "eval/scorers.py", "eval/slices.py", "loop/__init__.py", "loop/gate.py", "loop/smoke.py", "loop/guard.py", "loop/synth.py",
                "loop/train_eval.py", "loop/prompts/diagnostic.md", "loop/prompts/patch.md", "loop/prompts/guard.md"]
BLIND_EXCLUDE = {"loop/train_eval.py"}
PATCH_TOOLS_BLIND = "Read,Edit,Bash(python loop/guard.py --check),Bash(python loop/smoke.py),Bash(python loop/smoke.py *)"
PATCH_TOOLS = PATCH_TOOLS_BLIND + ",Bash(python loop/train_eval.py),Bash(python loop/train_eval.py *),mcp__wandb__*"
DIAG_TOOLS = "Read,mcp__wandb__*"
# never useful to the critic; disallowing them stops the agent from burning turns on denied attempts
DISALLOWED = "Write,Task,WebSearch,WebFetch,NotebookEdit,Skill,EnterPlanMode,Agent,Workflow"
TOOLS_NOTE = ("To test your hypothesis before and after the edit, run `python loop/train_eval.py` (train set only: "
              "metrics, worst classes, failing samples with per-frame nearest pairs; `--class 7x8` to focus). To try "
              "several values of a constant, sweep them in one call instead of editing and re-running per value: "
              "`python loop/train_eval.py --sweep CONTACT_THRESHOLD=0.25,0.3,0.35` (`--set NAME=VALUE` overrides one "
              "value for a full report); neither touches rules.py. Do not create scratch files or run any other "
              "command: they are denied and waste your turns.")
TOOLS_NOTE_BLIND = ("You have no data and no evaluation tool here: reason from the code. Do not create scratch files "
                    "or run any other command: they are denied and waste your turns.")
TRACE = {"on": False}
# Money (USD). Every claude call gets --max-budget-usd; these floors decide whether a call is worth starting.
GUARD_RESERVE_USD = 0.5  # held back from the patch agent so the edit it leaves can still be judged
MIN_PATCH_USD = 1.0  # below this a patch session cannot measure and edit
DIAG_MIN_USD = 0.5
ITERATION_MIN_USD = DIAG_MIN_USD + MIN_PATCH_USD + GUARD_RESERVE_USD


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg: str, logfile: Path | None) -> None:
    line = f"{now()} {msg}"
    print(line, flush=True)
    if logfile:
        with open(logfile, "a") as f:
            f.write(line + "\n")


def maybe_weave_op(name: str):
    """A weave op when tracing was switched on at runtime (after weave.init), a plain call otherwise."""
    def deco(fn):
        cache: dict = {}

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not TRACE["on"]:
                return fn(*args, **kwargs)
            if "op" not in cache:
                import weave
                try:
                    cache["op"] = weave.op(name=name, postprocess_inputs=lambda i: {k: v for k, v in i.items() if k != "self"})(fn)
                except TypeError:
                    cache["op"] = weave.op(name=name)(fn)
            return cache["op"](*args, **kwargs)
        return wrapper
    return deco


class CriticAuthError(RuntimeError):
    """claude -p cannot authenticate: nothing to gain by iterating."""


class BudgetExhausted(RuntimeError):
    """What is left under --max-cost cannot pay for the next agent call: the arm stops cleanly."""


class Critic:
    def __init__(self, a: argparse.Namespace):
        self.a = a
        self.repo = Path(a.repo).resolve()
        self.worktree = Path(a.worktree).resolve()
        self.metrics_path = self.repo / a.metrics
        self.suffix = "-blind" if a.blind else ""
        self.tmp = self.repo / "loop" / "transcripts"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.rules_src = self.repo / ("loop/blind-rules.py" if a.blind else RULES_REL)
        self.report_src = (self.tmp / "blind-report.json") if a.blind else self.repo / REPORT_REL
        self.train_path = Path(a.train_samples).resolve() if a.train_samples else self.repo / "data" / "samples.jsonl"
        self.logfile = None if a.dry_run else self.repo / "loop" / ("nightly-blind.log" if a.blind else "nightly.log")
        self.spent = 0.0  # USD reported by the agents' result events
        self.env = {k: v for k, v in os.environ.items() if not k.endswith("_HELDOUT")}
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        if self.env.get("ANTHROPIC_API_KEY") or self.env.get("ANTHROPIC_AUTH_TOKEN"):
            # key-based backend: run the CLI with its own config dir so the developer's claude.ai login,
            # user-scope MCP servers and skills never reach the critic
            self.env.setdefault("CLAUDE_CONFIG_DIR", str(self.repo / ".claude-critic"))
            Path(self.env["CLAUDE_CONFIG_DIR"]).mkdir(parents=True, exist_ok=True)
        if os.environ.get("WANDB_API_KEY") and not a.local:
            try:
                import weave
                ent = os.environ.get("WANDB_ENTITY")
                project = "tenfold" + os.environ.get("TENFOLD_PROJECT_SUFFIX", "")
                weave.init(f"{ent}/{project}" if ent else project)
                TRACE["on"] = True
            except Exception as e:
                log(f"weave init failed, continuing untraced: {e}", None)
        if a.blind and not self.rules_src.exists():
            shutil.copy(self.repo / RULES_REL, self.rules_src)

    # ---------- worktree ----------
    def files(self) -> list[str]:
        return [f for f in CRITIC_FILES if not (self.a.blind and f in BLIND_EXCLUDE)]

    def git_wt(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.worktree, capture_output=True, text=True, check=False).stdout

    def commit_base(self, label: str) -> None:
        self.git_wt("add", "-A")
        self.git_wt("-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", f"{label} {now()}")

    def sync_inputs(self) -> None:
        wt = self.worktree
        shutil.copy(self.rules_src, wt / RULES_REL)
        (wt / "eval").mkdir(parents=True, exist_ok=True)
        (wt / "data").mkdir(parents=True, exist_ok=True)
        if not self.a.blind:
            if self.report_src.exists():
                shutil.copy(self.report_src, wt / REPORT_REL)
            if self.train_path.exists():
                shutil.copy(self.train_path, wt / "data" / "train.jsonl")

    def setup_worktree(self) -> None:
        wt = self.worktree
        wt.mkdir(parents=True, exist_ok=True)
        for rel in self.files():
            dst = wt / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(self.repo / rel, dst)
        shutil.copy(self.repo / "loop" / "CLAUDE.critic.md", wt / "CLAUDE.md")
        self.sync_inputs()
        if not (wt / ".git").exists():
            self.git_wt("init", "-q")
            self.git_wt("config", "user.name", "critic-base")
            self.git_wt("config", "user.email", "critic@tenfold.local")
        self.commit_base("base")

    def reset_worktree(self) -> None:
        self.git_wt("checkout", "--", ".")
        self.git_wt("clean", "-fdq")
        self.sync_inputs()
        self.commit_base("iteration base")

    # ---------- agents ----------
    def remaining(self) -> float | None:
        """USD left under --max-cost, None when uncapped."""
        return None if not self.a.max_cost else self.a.max_cost - self.spent

    def run_claude(self, prompt: str, tools: str, transcript: Path, max_turns: int,
                   budget: float | None = None) -> tuple[str, int]:
        """Run claude -p (or the mock) in the worktree. Returns (final answer text, returncode):
        401 auth failure, 124 timeout, 125 out of turns, 126 stopped at its own --max-budget-usd cap."""
        cap = [] if budget is None else ["--max-budget-usd", f"{max(budget, 0.01):.2f}"]
        if self.a.mock_claude:
            cmd = [sys.executable, str(Path(self.a.mock_claude).resolve()), "-p", prompt, "--allowedTools", tools, *cap]
        else:
            cmd = [self.a.claude_bin, "-p", prompt, "--output-format", "stream-json", "--verbose",
                   "--allowedTools", tools, "--disallowedTools", DISALLOWED, "--max-turns", str(max_turns),
                   "--model", self.a.model, "--mcp-config", str(self.repo / "loop" / "mcp.json"), "--strict-mcp-config", *cap]
        try:
            proc = subprocess.run(cmd, cwd=self.worktree, env=self.env, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=self.a.timeout)
        except subprocess.TimeoutExpired:
            transcript.write_text("")
            return "", 124
        transcript.write_text(proc.stdout)
        texts: list[str] = []
        final: str | None = None
        stopped = 0
        for line in proc.stdout.splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "result":
                try:
                    self.spent += float(ev.get("total_cost_usd") or 0.0)
                except (TypeError, ValueError):
                    pass
                if ev.get("is_error") and ev.get("api_error_status") in (401, 403):
                    return str(ev.get("result") or "authentication failed"), 401
                stopped = {"error_max_turns": 125, "error_max_budget_usd": 126}.get(ev.get("subtype"), stopped)
                if ev.get("result"):
                    final = str(ev["result"])
            elif ev.get("type") == "assistant":
                for b in (ev.get("message") or {}).get("content") or []:
                    if b.get("type") == "text":
                        texts.append(b["text"])
        if final is None and not texts and proc.stdout.strip() and not proc.stdout.lstrip().startswith("{"):
            texts.append(proc.stdout)
        text = (final if final is not None else "\n".join(texts)).strip()
        if stopped:
            return "\n".join(texts[-3:]).strip(), stopped  # out of turns or budget: keep its last words, not the error string
        return text, proc.returncode

    def prompt(self, name: str, **fields: str) -> str:
        text = (self.repo / "loop" / "prompts" / f"{name}.md").read_text()
        for k, v in fields.items():
            text = text.replace("{" + k + "}", v)
        return text

    @maybe_weave_op("critic.diagnose")
    def diagnose(self, iteration: int) -> str:
        if self.a.blind:
            return "DIAGNOSIS: no failure data available (blind arm).\nHYPOTHESIS: improve the classifier however you see fit."
        rem = self.remaining()
        budget = None if rem is None else rem - MIN_PATCH_USD - GUARD_RESERVE_USD  # never eat the patch agent's share
        if budget is not None and budget < DIAG_MIN_USD:
            raise BudgetExhausted("before the diagnostic agent")
        report_path = self.worktree / REPORT_REL
        report = report_path.read_text()[:20000] if report_path.exists() else "{}"
        text, rc = self.run_claude(self.prompt("diagnostic", train_eval_name=f"tenfold-train-{self.tag_prev}", report=report),
                                   DIAG_TOOLS, self.tmp / f"diag-{iteration}{self.suffix}.jsonl", 8, budget)
        if rc == 401:
            raise CriticAuthError(text)
        if rc == 126:
            raise BudgetExhausted("inside the diagnostic agent")
        return text if rc == 0 and text else f"DIAGNOSIS: unavailable (claude rc={rc}).\nHYPOTHESIS: none."

    @maybe_weave_op("critic.patch")
    def patch(self, iteration: int, attempt: int, diagnosis: str, feedback: str, next_version: int) -> tuple[str, int, Path]:
        rem = self.remaining()
        budget = None if rem is None else rem - GUARD_RESERVE_USD  # keep enough to judge whatever it leaves
        if budget is not None and budget < MIN_PATCH_USD:
            raise BudgetExhausted(f"before patch attempt {attempt}")
        transcript = self.tmp / f"patch-{iteration}-{attempt}{self.suffix}.jsonl"
        text, rc = self.run_claude(
            self.prompt("patch", diagnosis=diagnosis, feedback=feedback, next_version=str(next_version),
                        tools_note=TOOLS_NOTE_BLIND if self.a.blind else TOOLS_NOTE,
                        max_turns=str(self.a.max_turns), finalize_by=str(max(5, self.a.max_turns - 10))),
            PATCH_TOOLS_BLIND if self.a.blind else PATCH_TOOLS, transcript, self.a.max_turns, budget)
        return text, rc, transcript

    @maybe_weave_op("critic.guard_agent")
    def guard_agent(self, iteration: int, diagnosis: str, diff: str, account: str) -> str:
        if self.a.blind:
            return "VERDICT: APPROVE | blind arm has no diagnosis to check against"
        text, rc = self.run_claude(self.prompt("guard", diagnosis=diagnosis, account=account, diff=diff[:12000]), "",
                                   self.tmp / f"guard-{iteration}{self.suffix}.jsonl", 2, self.remaining())
        if rc == 401:
            raise CriticAuthError(text)
        return text if rc == 0 and text else f"VERDICT: REJECT | guard agent unavailable (rc={rc}) | fix: retry"

    @maybe_weave_op("critic.guard_code")
    def guard_code(self, transcript: Path) -> list[str]:
        p = subprocess.run([sys.executable, str(self.repo / "loop" / "guard.py"), "--worktree", str(self.worktree),
                            "--transcript", str(transcript)], capture_output=True, text=True, timeout=120)
        return [l for l in p.stdout.splitlines() if l.startswith("GUARD_REJECT ") or l.startswith("GUARD_ERROR")]

    # ---------- evaluation and gate ----------
    def run_eval(self, split: str, rules: Path, tag: str, report: Path | None = None) -> dict | None:
        out = self.tmp / f"{split}-{tag}.json"
        cmd = [sys.executable, str(self.repo / "eval" / "run_eval.py"), "--split", split, "--rules", str(rules),
               "--tag", tag, "--out", str(out)]
        if split == "train":
            cmd += ["--report", str(report or self.tmp / f"report-{tag}.json")]
        if self.a.local:
            cmd.append("--local")
        env = dict(self.env)
        if split == "heldout":
            env = dict(os.environ)  # the held-out key lives only in this subprocess
            if not env.get("WANDB_API_KEY_HELDOUT") and not self.a.local:
                log("heldout: WANDB_API_KEY_HELDOUT not set, evaluating locally", self.logfile)
                cmd.append("--local")
            if self.a.heldout_samples:
                cmd += ["--samples", self.a.heldout_samples]
        elif self.a.train_samples:
            cmd += ["--samples", self.a.train_samples]
        for attempt in range(3):
            p = subprocess.run(cmd, cwd=self.repo, env=env, capture_output=True, text=True, timeout=1200)
            if p.returncode == 0 and out.exists():
                return json.loads(out.read_text())
            log(f"{split} eval attempt {attempt + 1} failed: {p.stderr.strip()[-400:]}", self.logfile)
            time.sleep(5 * (attempt + 1))
        return None

    @maybe_weave_op("critic.metric_gate")
    def gate(self, prev: dict | None, cand: dict) -> tuple[bool, str]:
        from loop import gate  # shared with train_eval.py --sweep, so the patch agent sees the same verdict
        return gate.check(prev, cand)

    # ---------- persistence ----------
    def load_metrics(self) -> dict:
        if self.metrics_path.exists():
            return json.loads(self.metrics_path.read_text())
        return {"versions": [], "rejected": []}

    def save_metrics(self, m: dict) -> None:
        self.metrics_path.parent.mkdir(parents=True, exist_ok=True)
        self.metrics_path.write_text(json.dumps(m, indent=2))

    def commit(self, version: int, diagnosis: str, patch_summary: str, expected: str) -> str:
        shutil.copy(self.worktree / RULES_REL, self.rules_src)
        if self.a.no_commit:
            return "no-commit"
        diag = diagnosis.split("\n")[0].replace("DIAGNOSIS:", "").strip()[:120]
        msg = f"critic v{version}: {diag} | patch: {patch_summary[:120]} | expected: {expected[:120]}"
        subprocess.run(["git", "add", str(RULES_REL)], cwd=self.repo, check=True)
        # pathspec commit: files a human staged in the main repo never ride along under critic-agent
        subprocess.run(["git", "-c", "user.name=critic-agent", "-c", "user.email=critic-agent@tenfold.local",
                        "commit", "-q", "-m", msg, "--", str(RULES_REL)], cwd=self.repo, check=True)
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True).stdout.strip()
        (self.repo / "data").mkdir(exist_ok=True)
        (self.repo / "data" / "BEST_VERSION").write_text(sha + "\n")
        return sha

    @maybe_weave_op("critic.heartbeat")
    def heartbeat(self, iteration: int, status: str) -> dict:
        return {"iteration": iteration, "status": status, "ts": now(), "blind": self.a.blind, "spent_usd": round(self.spent, 3)}

    @maybe_weave_op("critic.rejected_patch")
    def record_rejection(self, iteration: int, reason: str, diff: str) -> dict:
        return {"iteration": iteration, "reason": reason, "diff": diff[:4000], "blind": self.a.blind}

    # ---------- main loop ----------
    def run(self) -> int:
        try:
            return self.loop()
        except BudgetExhausted as e:
            log(f"STOP: budget reached {e} (${self.spent:.2f} spent of ${self.a.max_cost:.2f})", self.logfile)
            return 0

    def loop(self) -> int:
        a = self.a
        self.setup_worktree()
        metrics = self.load_metrics()
        accepted = [v for v in metrics["versions"] if v.get("accepted")]
        if not accepted:
            log("no baseline: evaluating V0 on train", self.logfile)
            base = self.run_eval("train", self.rules_src, f"v0{self.suffix}", report=self.report_src)
            if base is None:
                log("FATAL: V0 evaluation failed", self.logfile)
                return 2
            entry = {"tag": f"v0{self.suffix}", "version": 0, "sha": base["git_sha"], "train": base["metrics"],
                     "heldout": None, "accepted": True, "blind": a.blind, "ts": now(), "diagnosis": "baseline"}
            if not a.dry_run and not a.skip_heldout:
                h = self.run_eval("heldout", self.rules_src, f"v0{self.suffix}")
                entry["heldout"] = h["metrics"] if h else None
            if not a.dry_run:
                metrics["versions"].append(entry)
                self.save_metrics(metrics)
            accepted = [entry]
        last = accepted[-1]
        version = int(last.get("version", str(last["tag"]).lstrip("v").split("-")[0])) + 1
        self.tag_prev = last["tag"]
        no_improve = 0
        for it in range(1, a.iterations + 1):
            rem = self.remaining()
            if rem is not None and rem < ITERATION_MIN_USD:
                log(f"STOP: ${self.spent:.2f} spent of ${a.max_cost:.2f}, not enough left for another iteration "
                    f"(one needs ${ITERATION_MIN_USD:.2f})", self.logfile)
                break
            log(f"iteration {it}/{a.iterations} start (next version v{version}{self.suffix})", self.logfile)
            self.heartbeat(it, "start")
            self.reset_worktree()
            diagnosis = self.diagnose(it)
            log("diagnosis: " + diagnosis.split("\n")[0][:200], self.logfile)
            feedback = ""
            outcome = "failed"
            for attempt in range(1, 3):
                text, rc, transcript = self.patch(it, attempt, diagnosis, feedback, version)
                if rc == 401:
                    raise CriticAuthError(text)
                if rc == 124:
                    log(f"patch attempt {attempt}: claude timed out after {a.timeout}s", self.logfile)
                    feedback = ""
                    self.reset_worktree()
                    continue
                unfinished = ""
                if rc in (125, 126):
                    why = "ran out of turns" if rc == 125 else "stopped at its budget"
                    if not self.git_wt("diff", "--", str(RULES_REL)).strip():
                        log(f"patch attempt {attempt}: agent {why} with no edit", self.logfile)
                        feedback = (f"Your previous session {why} before editing rules.py. Measure once (sweep "
                                    "constants in one call), make one focused edit early, then verify.")
                        self.reset_worktree()
                        continue
                    log(f"patch attempt {attempt}: agent {why}; the edit it left goes through guard and gate", self.logfile)
                    unfinished, rc = why, 0
                if rc != 0:
                    log(f"patch attempt {attempt}: claude exited {rc}: {text[-200:]}", self.logfile)
                    break
                diff = self.git_wt("diff", "--", str(RULES_REL))
                closing = {k: next((l.split(":", 1)[1].strip() for l in text.splitlines() if l.startswith(k + ":")), "")
                           for k in ("HYPOTHESIS", "PATCH", "EXPECTED")}
                account = ("\n".join(f"{k}: {v}" for k, v in closing.items() if v) if any(closing.values())
                           else f"none: the session {unfinished or 'ended'} without explaining its edit")
                rejects = self.guard_code(transcript)
                if not rejects:
                    verdict = self.guard_agent(it, diagnosis, diff, account)
                    if "APPROVE" not in verdict.upper():
                        rejects = [f"GUARD_REJECT agent: {verdict.strip()[:300]}"]
                if rejects:
                    log(f"patch attempt {attempt} rejected: " + " || ".join(r[:160] for r in rejects), self.logfile)
                    feedback = ("The previous attempt was rejected by the guard:\n" + "\n".join(rejects) +
                                "\nStart again from the original file (it has been restored) and address every line.")
                    self.record_rejection(it, "guard: " + rejects[0][:200], diff)
                    self.reset_worktree()
                    continue
                patch_summary = closing["PATCH"] or (f"edit left by a session that {unfinished}" if unfinished else text[-200:])
                patch_hypothesis = closing["HYPOTHESIS"]
                expected = closing["EXPECTED"]
                if a.dry_run:
                    print("\n----- DRY RUN: guard passed, diff below, nothing committed -----\n" + diff)
                    print(f"PATCH: {patch_summary}\nEXPECTED: {expected}")
                    return 0
                cand_tag = f"v{version}{self.suffix}-candidate"
                cand_report = self.tmp / f"report-{cand_tag}.json"
                cand = self.run_eval("train", self.worktree / RULES_REL, cand_tag, report=cand_report)
                if cand is None:
                    log("train eval failed; iteration abandoned", self.logfile)
                    break
                ok, why = self.gate(last["train"], cand["metrics"])
                if not ok:
                    log(f"metric gate rejected: {why}", self.logfile)
                    metrics["rejected"].append({"iteration": it, "ts": now(), "reason": why, "train": cand["metrics"],
                                                "diagnosis": diagnosis[:300], "patch": patch_summary})
                    self.save_metrics(metrics)
                    self.record_rejection(it, "gate: " + why, diff)
                    no_improve += 1
                    outcome = "rejected"
                    break
                sha = self.commit(version, diagnosis, patch_summary, expected)
                if cand_report.exists():
                    shutil.copy(cand_report, self.report_src)
                entry = {"tag": f"v{version}{self.suffix}", "version": version, "sha": sha, "train": cand["metrics"],
                         "heldout": None, "accepted": True, "blind": a.blind, "ts": now(), "diagnosis": diagnosis[:300],
                         "patch": patch_summary, "patch_hypothesis": patch_hypothesis, "expected": expected, "gate": why,
                         "spent_usd": round(self.spent, 3)}
                if not a.skip_heldout:
                    h = self.run_eval("heldout", self.rules_src, f"v{version}{self.suffix}")
                    entry["heldout"] = h["metrics"] if h else None
                metrics["versions"].append(entry)
                self.save_metrics(metrics)
                no_improve = 0  # the gate only accepts strict improvements
                log(f"ACCEPTED v{version}{self.suffix} ({why}) sha={sha[:8]}", self.logfile)
                last = entry
                self.tag_prev = entry["tag"]
                version += 1
                outcome = "accepted"
                break
            self.heartbeat(it, outcome)
            log(f"iteration {it} {outcome}, spent ${self.spent:.2f} so far", self.logfile)
            if not a.no_early_stop and no_improve >= 3:
                log("stop: 3 iterations without improvement", self.logfile)
                break
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=1)
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--worktree")
    ap.add_argument("--metrics")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--mock-claude", help="script that stands in for `claude` (tests)")
    ap.add_argument("--claude-bin", default="claude")
    ap.add_argument("--model", default=None, help="model for the three agents (default claude-sonnet-5, or TENFOLD_CRITIC_MODEL)")
    ap.add_argument("--blind", action="store_true", help="ablation: no failure data, no train copy, never touches the repo")
    ap.add_argument("--no-commit", action="store_true", help="never commit to the repo")
    ap.add_argument("--no-early-stop", action="store_true")
    ap.add_argument("--skip-heldout", action="store_true")
    ap.add_argument("--local", action="store_true", help="no W&B anywhere")
    ap.add_argument("--train-samples")
    ap.add_argument("--heldout-samples")
    ap.add_argument("--max-turns", type=int, default=45)
    ap.add_argument("--timeout", type=int, default=1500)
    ap.add_argument("--max-cost", type=float, default=None,
                    help="stop this arm once the agents have spent this many USD (default TENFOLD_MAX_COST_USD or 40)")
    a = ap.parse_args()
    repo = Path(a.repo).resolve()
    load_env(repo / ".env")
    a.model = a.model or os.environ.get("TENFOLD_CRITIC_MODEL", "claude-sonnet-5")
    if a.max_cost is None:
        a.max_cost = float(os.environ.get("TENFOLD_MAX_COST_USD", "40"))
    if a.blind:
        a.no_commit = True
    a.worktree = a.worktree or str(repo.parent / ("tenfold-blind" if a.blind else "tenfold-critic"))
    a.metrics = a.metrics or ("data/metrics-blind.json" if a.blind else "data/metrics.json")
    if a.local:
        os.environ.pop("WANDB_API_KEY", None)
    try:
        return Critic(a).run()
    except CriticAuthError as e:
        log(f"STOP: the critic cannot authenticate to Claude ({e}). Fix: `claude login`, or ANTHROPIC_API_KEY in .env "
            f"(org-level keys also need ANTHROPIC_CUSTOM_HEADERS), then `python loop/critic.py --dry-run`.", None)
        return 3


if __name__ == "__main__":
    sys.exit(main())
