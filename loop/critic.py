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
TOOLS_NOTE = ("Start with `python loop/train_eval.py --sweep-all`: one call tries every numeric constant of rules.py "
              "around its value, each with the metric gate's verdict, and names the best passing change. If one "
              "passes, that single edit is usually the patch; build new logic only when none passes or the failure "
              "you measured is out of every constant's reach. "
              "To test your hypothesis before and after the edit, run `python loop/train_eval.py` (train set only: "
              "metrics, worst classes, failing samples with per-frame nearest pairs, and the metric gate's verdict "
              "against the last accepted version; `--class 7x8` to focus). To try several values of a constant, "
              "sweep them in one call instead of editing and re-running per value: "
              "`python loop/train_eval.py --sweep CONTACT_THRESHOLD=0.25,0.3,0.35` (each line ends with the gate's "
              "verdict; `--set NAME=VALUE` overrides one value for a full report); neither touches rules.py. Finish "
              "only when `python loop/train_eval.py` on your edited file prints `gate vs the last accepted version: "
              "PASS`: a patch that fails the gate is thrown away. Read files with the Read tool. Do not run sed, cat, "
              "git or any other command, and do not create scratch files: they are denied and waste your turns.")
TOOLS_NOTE_BLIND = ("You have no data and no evaluation tool here: reason from the code. Do not create scratch files "
                    "or run any other command: they are denied and waste your turns.")
TRACE = {"on": False}
# Money (USD). Every claude call gets --max-budget-usd; these floors decide whether a call is worth starting.
GUARD_RESERVE_USD = 0.5  # held back from the patch agent so the edit it leaves can still be judged
MIN_PATCH_USD = 1.0  # below this a patch session cannot measure and edit
DIAG_MIN_USD = 0.5
ITERATION_MIN_USD = DIAG_MIN_USD + MIN_PATCH_USD + GUARD_RESERVE_USD
# The diagnostic and guard agents read text and write text, so they can run on W&B Inference (served on CoreWeave,
# billed to W&B credits, traced with token usage in Weave). The patch agent needs tools and stays on Claude Code.
WANDB_INFERENCE_URL = "https://api.inference.wandb.ai/v1"
TEXT_AGENT_MODEL = "Qwen/Qwen3-235B-A22B-Instruct-2507"
# Hidden sets (loop/gate.py check_hidden): scored locally after the train gate, never shown to the agents, never
# published to Weave. Their variables are kept out of the agents' environment.
HIDDEN_ENV = ("TENFOLD_VALIDATION_SAMPLES", "TENFOLD_LIVE_SAMPLES")
HIDDEN_KEEP = ("exact_match", "n_holds", "n_samples", "contact_accuracy", "false_unknown_rate",
               "negative_rejection_accuracy", "near_contact_accuracy", "per_class")


GUARD_TIMEOUT_S = 120.0


def guard_verdict(returncode: int, stdout: str, stderr: str = "") -> list[str]:
    """What loop/guard.py decided. Accepted ([]) only on exit 0 with GUARD_OK as the last line and no rejection line;
    a crash, a non-zero exit, a missing verdict or a truncated output all come back as a rejection."""
    lines = [l.strip() for l in stdout.splitlines() if l.strip()]
    rejects = [l for l in lines if l.startswith("GUARD_REJECT ") or l.startswith("GUARD_ERROR")]
    if returncode == 0 and lines and lines[-1] == "GUARD_OK" and not rejects:
        return []
    if rejects:
        return rejects
    tail = (stderr.strip()[-300:] or (lines[-1][:120] if lines else "no output")).replace("\n", " ")
    return [f"GUARD_REJECT guard_crash: loop/guard.py exited {returncode} without GUARD_OK | cause: {tail} | "
            "fix: run python loop/guard.py --check and make it print GUARD_OK"]


def approved(verdict: str) -> bool:
    """Only an explicit "VERDICT: APPROVE" line counts; a rejection that mentions the word approve does not."""
    for line in verdict.splitlines():
        line = line.strip().strip("*` ").upper()
        if line.startswith("VERDICT:"):
            head = line[len("VERDICT:"):].split("|", 1)[0].strip().strip("*` ")
            return head == "APPROVE"
    return False


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


def smoke_requirements(repo: Path, rules: Path) -> list[str]:
    """The SMOKE_FAIL lines of loop/smoke.py on the running rules, in a subprocess with an empty environment. The guard
    runs the same test on every candidate, so each line is something the next patch has to fix before any version
    can be accepted. Empty when the rules pass, or when the smoke test itself could not run (the guard still checks)."""
    try:
        p = subprocess.run([sys.executable, str(repo / "loop" / "smoke.py"), "--rules", str(rules)], cwd=repo,
                           env={"PATH": os.defpath}, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError):
        return []
    return [l[len("SMOKE_FAIL "):] for l in p.stdout.splitlines() if l.startswith("SMOKE_FAIL ")]


def data_fingerprint(path: Path) -> dict:
    """Which dataset a version was measured on: short sha256 of the samples file and its sample count."""
    import hashlib
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {"sha": None, "n_samples": 0}
    return {"sha": hashlib.sha256(raw).hexdigest()[:12], "n_samples": sum(1 for l in raw.splitlines() if l.strip())}


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
        self.spent = 0.0  # USD reported by the agents' result events (Claude Code only; W&B Inference is not counted)
        self.run_id = datetime.now(timezone.utc).strftime("%m%d-%H%M")  # labels this run's evaluations in Weave
        self.env = {k: v for k, v in os.environ.items() if not k.endswith("_HELDOUT") and k not in HIDDEN_ENV}
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
        self.hidden = {name: Path(p) for name, p in (("validation", a.validation_samples), ("live", a.live_samples)) if p}
        self.hidden_dir = Path(a.hidden_dir).resolve()
        if self.hidden:
            self.hidden_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

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
        prompt = self.prompt("diagnostic", train_eval_name=f"tenfold-train-{self.tag_prev}", report=report)
        if self.a.text_agents == "wandb":
            rules = (self.worktree / RULES_REL).read_text()
            prompt += f"\n\nclassifier/rules.py (no tools on this backend, so the file is here):\n```python\n{rules}\n```\n"
            text = self.run_text_agent(prompt, self.tmp / f"diag-{iteration}{self.suffix}.json", max_tokens=700)
            return text or "DIAGNOSIS: unavailable (W&B Inference call failed).\nHYPOTHESIS: none."
        text, rc = self.run_claude(prompt, DIAG_TOOLS, self.tmp / f"diag-{iteration}{self.suffix}.jsonl", 8, budget)
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
        prompt = self.prompt("guard", diagnosis=diagnosis, account=account, diff=diff[:12000])
        if self.a.text_agents == "wandb":
            text = self.run_text_agent(prompt, self.tmp / f"guard-{iteration}{self.suffix}.json", max_tokens=300)
            return text or "VERDICT: REJECT | guard agent unavailable (W&B Inference call failed) | fix: retry"
        text, rc = self.run_claude(prompt, "", self.tmp / f"guard-{iteration}{self.suffix}.jsonl", 2, self.remaining())
        if rc == 401:
            raise CriticAuthError(text)
        return text if rc == 0 and text else f"VERDICT: REJECT | guard agent unavailable (rc={rc}) | fix: retry"

    def run_text_agent(self, prompt: str, transcript: Path, max_tokens: int) -> str:
        """One chat completion on W&B Inference (or the mock), kept with its token usage in `transcript`.
        Returns "" after three failed tries; the caller turns that into an unavailable diagnosis or a rejection."""
        record: dict = {"backend": "wandb", "model": self.a.text_model, "prompt": prompt}
        for attempt in range(3):
            try:
                resp = self.inference(prompt, max_tokens)
                text = str(resp["choices"][0]["message"]["content"] or "").split("</think>")[-1].strip()
                record.update(response=text, usage=resp.get("usage"), served_model=resp.get("model"))
                record.pop("error", None)
                transcript.write_text(json.dumps(record, indent=2))
                return text
            except Exception as e:  # network, rate limit, malformed reply: retry, never crash the loop
                record["error"] = f"{type(e).__name__}: {e}"[:400]
                if not self.a.mock_inference:
                    time.sleep(5 * (attempt + 1))
        transcript.write_text(json.dumps(record, indent=2))
        log(f"W&B Inference unavailable after 3 tries: {record['error']}", self.logfile)
        return ""

    @maybe_weave_op("critic.inference")
    def inference(self, prompt: str, max_tokens: int) -> dict:
        """OpenAI-style chat completion (model, choices, usage) from W&B Inference, the same endpoint as Tally."""
        if self.a.mock_inference:
            p = subprocess.run([sys.executable, str(Path(self.a.mock_inference).resolve()), "--inference"],
                               input=prompt, capture_output=True, text=True, timeout=60)
            if p.returncode != 0:
                raise RuntimeError((p.stdout or p.stderr)[-300:])
            return json.loads(p.stdout)
        import httpx
        key = os.environ.get("WANDB_API_KEY")
        if not key:
            raise RuntimeError("WANDB_API_KEY is not set (and --local disables W&B Inference)")
        headers = {"Authorization": f"Bearer {key}"}
        if os.environ.get("WANDB_INFERENCE_PROJECT"):
            headers["OpenAI-Project"] = os.environ["WANDB_INFERENCE_PROJECT"]
        base = (os.environ.get("WANDB_INFERENCE_BASE_URL") or WANDB_INFERENCE_URL).rstrip("/")
        r = httpx.post(f"{base}/chat/completions", headers=headers, timeout=120, json={
            "model": self.a.text_model, "max_tokens": max_tokens, "temperature": 0.2,
            "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        return r.json()

    @maybe_weave_op("critic.guard_code")
    def guard_code(self, transcript: Path) -> list[str]:
        """Rejection lines from loop/guard.py; empty only for an explicit GUARD_OK (see guard_verdict).
        Runs with the critic's environment, so the held-out key never reaches the guard or the candidate."""
        try:
            p = subprocess.run([sys.executable, str(self.repo / "loop" / "guard.py"), "--worktree", str(self.worktree),
                                "--transcript", str(transcript)], cwd=self.repo, env=self.env, stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=GUARD_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return [f"GUARD_REJECT guard_timeout: loop/guard.py gave no verdict within {GUARD_TIMEOUT_S:.0f} s | "
                    "cause: the guard or the smoke test hung | fix: keep classify() and the module body fast"]
        except OSError as e:
            return [f"GUARD_REJECT guard_crash: loop/guard.py could not start | cause: {type(e).__name__}: {e} | fix: rerun"]
        return guard_verdict(p.returncode, p.stdout, p.stderr)

    # ---------- evaluation and gate ----------
    def run_eval(self, split: str, rules: Path, tag: str, report: Path | None = None, verdict: str | None = None,
                 gate: str | None = None) -> dict | None:
        out = self.tmp / f"{split}-{tag}.json"
        cmd = [sys.executable, str(self.repo / "eval" / "run_eval.py"), "--split", split, "--rules", str(rules),
               "--tag", tag, "--out", str(out), "--run", self.run_id]
        if verdict:
            cmd += ["--verdict", verdict]
        if gate:
            cmd += ["--gate", gate]
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

    def hidden_eval(self, name: str, rules: Path, tag: str) -> dict | None:
        """Metrics of `rules` on one hidden set, or None. Local only; results and reports go to the hidden directory,
        outside the repo and the critic worktree, and nothing is published."""
        path = self.hidden[name]
        out = self.hidden_dir / f"{name}-{tag}.json"
        cmd = [sys.executable, str(self.repo / "eval" / "run_eval.py"), "--split", "train", "--local",
               "--rules", str(rules), "--samples", str(path), "--tag", f"{name}-{tag}", "--out", str(out),
               "--report", str(self.hidden_dir / f"report-{name}-{tag}.json")]
        env = {k: v for k, v in self.env.items() if k not in ("WANDB_API_KEY", "TENFOLD_TRAIN_SAMPLES")}
        for attempt in range(2):
            p = subprocess.run(cmd, cwd=self.repo, env=env, capture_output=True, text=True, timeout=1200)
            if p.returncode == 0 and out.exists():
                m = json.loads(out.read_text())["metrics"]
                return {k: m.get(k) for k in HIDDEN_KEEP} | {"data_sha": data_fingerprint(path)["sha"]}
            log(f"hidden {name} eval attempt {attempt + 1} failed: {p.stderr.strip()[-300:]}", self.logfile)
        return None

    def hidden_gate(self, prev: dict, rules: Path, tag: str) -> tuple[bool, str, dict]:
        """check_hidden on every hidden set, stopping at the first refusal. Returns (ok, reason, measured)."""
        from loop import gate
        measured: dict = {}
        reasons = []
        for name in self.hidden:
            measured[name] = self.hidden_eval(name, rules, tag)
            ok, why = gate.check_hidden(name, prev.get(name), measured[name])
            if not ok:
                return False, why, measured
            reasons.append(why)
        return True, ", ".join(reasons), measured

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
        self.data = data_fingerprint(self.train_path)
        accepted = [v for v in metrics["versions"] if v.get("accepted")]
        if not accepted:
            log("no baseline: evaluating V0 on train", self.logfile)
            base = self.run_eval("train", self.rules_src, f"v0{self.suffix}", report=self.report_src, verdict="baseline")
            if base is None:
                log("FATAL: V0 evaluation failed", self.logfile)
                return 2
            entry = {"tag": f"v0{self.suffix}", "version": 0, "sha": base["git_sha"], "train": base["metrics"],
                     "heldout": None, "accepted": True, "blind": a.blind, "ts": now(), "diagnosis": "baseline",
                     "kind": "baseline", "data": self.data}
            if not a.dry_run and not a.skip_heldout:
                h = self.run_eval("heldout", self.rules_src, f"v0{self.suffix}")
                entry["heldout"] = h["metrics"] if h else None
            if not a.dry_run:
                metrics["versions"].append(entry)
                self.save_metrics(metrics)
            accepted = [entry]
        elif (accepted[-1].get("data") or {}).get("sha") != self.data["sha"]:
            # New captures landed: the running version's train score is stale, so the gate would compare a candidate
            # on today's data with a baseline on yesterday's. Re-measure the running rules first, then iterate.
            last, old = accepted[-1], accepted[-1].get("data") or {}
            k = 1 + sum(1 for v in metrics["versions"] if v.get("kind") == "data_refresh")
            tag = f"v{last.get('version', 0)}{self.suffix}-data{k}"
            log(f"data refresh: dataset changed ({old.get('n_samples', '?')} -> {self.data['n_samples']} samples, "
                f"{self.data['sha']}); re-evaluating the running version as {tag}", self.logfile)
            res = self.run_eval("train", self.rules_src, tag, report=self.report_src, verdict="data-refresh")
            if res is None:
                log("FATAL: re-evaluation on the new data failed", self.logfile)
                return 2
            entry = {"tag": tag, "version": last.get("version", 0), "sha": res["git_sha"], "train": res["metrics"],
                     "heldout": None, "accepted": True, "blind": a.blind, "ts": now(), "kind": "data_refresh",
                     "data": self.data, "previous": last["tag"],
                     "diagnosis": f"data refresh: {old.get('n_samples', '?')} -> {self.data['n_samples']} samples"}
            if not a.dry_run and not a.skip_heldout:
                h = self.run_eval("heldout", self.rules_src, tag)
                entry["heldout"] = h["metrics"] if h else None
            if not a.dry_run:
                metrics["versions"].append(entry)
                self.save_metrics(metrics)
            accepted = [entry]
        last = accepted[-1]
        if self.hidden and not a.dry_run:
            # the running version's score on each hidden set, measured again whenever that set's file changed
            known = last.setdefault("hidden", {})
            stale = [n for n, p in self.hidden.items()
                     if not known.get(n) or known[n].get("data_sha") != data_fingerprint(p)["sha"]]
            for name in stale:
                known[name] = self.hidden_eval(name, self.rules_src, last["tag"])
                if known[name] is None:
                    log(f"FATAL: the running version could not be scored on the hidden {name} set", self.logfile)
                    return 2
            if stale:
                self.save_metrics(metrics)
                log(f"hidden sets scored on the running version {last['tag']}: {', '.join(stale)}", self.logfile)
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
            required = smoke_requirements(self.repo, self.rules_src)
            if required:
                # on the diagnosis, so the patch agent aims at it and the guard agent accepts a patch that fixes it
                diagnosis += ("\nREQUIRED: the running rules.py already fails the guard's smoke test, so no patch is "
                              "accepted until it passes: " + " || ".join(required))
                log("required before any patch is accepted: " + " || ".join(r[:160] for r in required), self.logfile)
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
                    if not approved(verdict):
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
                cand = self.run_eval("train", self.worktree / RULES_REL, cand_tag, report=cand_report, verdict="candidate")
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
                measured: dict = {}
                if self.hidden:
                    hidden_ok, hidden_why, measured = self.hidden_gate(last.get("hidden") or {}, self.worktree / RULES_REL, cand_tag)
                    if not hidden_ok:
                        name = hidden_why.split(" ")[0]
                        log(f"hidden gate rejected: {hidden_why}", self.logfile)
                        # the set's name only in metrics.json and Weave: hidden numbers stay in the local log
                        metrics["rejected"].append({"iteration": it, "ts": now(), "reason": f"hidden gate: {name}",
                                                    "train": cand["metrics"], "hidden": measured,
                                                    "diagnosis": diagnosis[:300], "patch": patch_summary})
                        self.save_metrics(metrics)
                        self.record_rejection(it, f"hidden gate: {name}", diff)
                        no_improve += 1
                        outcome = "rejected"
                        break
                    log(f"hidden gate passed: {hidden_why}", self.logfile)
                sha = self.commit(version, diagnosis, patch_summary, expected)
                if cand_report.exists():
                    shutil.copy(cand_report, self.report_src)
                # publish the accepted version under its own label, so Weave tells it apart from rejected candidates
                self.run_eval("train", self.rules_src, f"v{version}{self.suffix}",
                              report=self.tmp / f"report-v{version}{self.suffix}.json", verdict="accepted", gate=why)
                entry = {"tag": f"v{version}{self.suffix}", "version": version, "sha": sha, "train": cand["metrics"],
                         "heldout": None, "accepted": True, "blind": a.blind, "ts": now(), "diagnosis": diagnosis[:300],
                         "patch": patch_summary, "patch_hypothesis": patch_hypothesis, "expected": expected, "gate": why,
                         "spent_usd": round(self.spent, 3), "kind": "patch", "data": self.data,
                         "hidden": measured}
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
    ap.add_argument("--text-agents", choices=["claude", "wandb"], default=None,
                    help="backend of the diagnostic and guard agents (default TENFOLD_TEXT_AGENTS, else claude)")
    ap.add_argument("--text-model", default=None,
                    help="W&B Inference model for those agents (default TENFOLD_TEXT_AGENT_MODEL, else Qwen3 235B)")
    ap.add_argument("--mock-inference", help="script that stands in for W&B Inference (tests)")
    ap.add_argument("--validation-samples", default=None,
                    help="hidden validation holds, scored after the train gate (default TENFOLD_VALIDATION_SAMPLES)")
    ap.add_argument("--live-samples", default=None,
                    help="real-lesson windows, scored after the train gate (default TENFOLD_LIVE_SAMPLES)")
    ap.add_argument("--hidden-dir", default=None,
                    help="where hidden evaluations are written (default ../tenfold-validation/loop-runs)")
    ap.add_argument("--max-cost", type=float, default=None,
                    help="stop this arm once the agents have spent this many USD (default TENFOLD_MAX_COST_USD or 40)")
    a = ap.parse_args()
    repo = Path(a.repo).resolve()
    load_env(repo / ".env")
    a.model = a.model or os.environ.get("TENFOLD_CRITIC_MODEL", "claude-sonnet-5")
    # TENFOLD_TRAIN_SAMPLES points the loop at a train split from eval/split.py; unset, it reads the whole dataset
    a.train_samples = a.train_samples or os.environ.get("TENFOLD_TRAIN_SAMPLES") or None
    if a.train_samples:
        chosen = Path(a.train_samples)
        a.train_samples = str(chosen if chosen.is_absolute() else repo / chosen)
        if not Path(a.train_samples).exists():
            raise SystemExit(f"train samples {a.train_samples} not found: run make split, or unset TENFOLD_TRAIN_SAMPLES")
    a.text_agents = a.text_agents or os.environ.get("TENFOLD_TEXT_AGENTS") or "claude"
    if a.text_agents not in ("claude", "wandb"):
        raise SystemExit(f"TENFOLD_TEXT_AGENTS must be claude or wandb, got {a.text_agents!r}")
    a.text_model = a.text_model or os.environ.get("TENFOLD_TEXT_AGENT_MODEL") or TEXT_AGENT_MODEL
    for attr, env_name in (("validation_samples", "TENFOLD_VALIDATION_SAMPLES"), ("live_samples", "TENFOLD_LIVE_SAMPLES")):
        chosen = getattr(a, attr) or os.environ.get(env_name) or None
        if chosen:
            path = Path(chosen) if Path(chosen).is_absolute() else repo / chosen
            if not path.exists():
                raise SystemExit(f"{env_name} points at {path}, which does not exist")
            chosen = str(path.resolve())
        setattr(a, attr, chosen)
    a.hidden_dir = a.hidden_dir or str(repo.parent / "tenfold-validation" / "loop-runs")
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
