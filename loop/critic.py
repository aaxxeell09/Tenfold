"""The self-improvement loop orchestrator. Runs from the main repo; the agents run in a stripped worktree.

    python loop/critic.py --iterations 1 --dry-run          # diagnostic, patch, guard; print the diff; no commit
    python loop/critic.py --iterations 1                    # one full guarded, gated iteration
    python loop/critic.py --iterations 20 --no-early-stop   # the nightly
    python loop/critic.py --iterations 2 --mock-claude tests/fake_claude.py --local   # no API at all
    python loop/critic.py --blind --worktree ../tenfold-blind --metrics data/metrics-blind.json --no-commit

One iteration (SPEC.md section 7): reset worktree -> diagnostic -> patch -> guard agent -> guard.py ->
train eval -> metric gate -> commit (author critic-agent) + data/BEST_VERSION -> held-out eval subprocess.
Every step is a weave op when WANDB_API_KEY is set. The critic's environment never contains the held-out key.
"""
from __future__ import annotations

import argparse
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

from tenfold.env import load_env  # noqa: E402

RULES_REL = Path("classifier/rules.py")
CRITIC_FILES = ["classifier/__init__.py", "classifier/schema.py", "classifier/features.py", "loop/__init__.py",
                "loop/smoke.py", "loop/guard.py", "loop/synth.py", "loop/prompts/diagnostic.md",
                "loop/prompts/patch.md", "loop/prompts/guard.md"]
PATCH_TOOLS = "Read,Edit,Bash(python loop/guard.py --check),Bash(python loop/smoke.py),Bash(python loop/smoke.py *),mcp__wandb__*"
DIAG_TOOLS = "Read,mcp__wandb__*"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg: str, logfile: Path | None) -> None:
    line = f"{now()} {msg}"
    print(line, flush=True)
    if logfile:
        with open(logfile, "a") as f:
            f.write(line + "\n")


def maybe_weave_op(name: str):
    """weave.op when tracing is on, identity otherwise."""
    def deco(fn):
        if os.environ.get("WANDB_API_KEY") and not os.environ.get("TENFOLD_NO_WEAVE"):
            try:
                import weave
                return weave.op(name=name)(fn)
            except Exception:
                return fn
        return fn
    return deco


class CriticAuthError(RuntimeError):
    """claude -p cannot authenticate: nothing to gain by iterating."""


class Critic:
    def __init__(self, a: argparse.Namespace):
        self.a = a
        self.repo = Path(a.repo).resolve()
        self.worktree = Path(a.worktree).resolve()
        self.metrics_path = self.repo / a.metrics
        self.logfile = self.repo / "loop" / ("nightly-blind.log" if a.blind else "nightly.log") if not a.dry_run else None
        self.env = {k: v for k, v in os.environ.items() if not k.endswith("_HELDOUT")}
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        if self.env.get("ANTHROPIC_API_KEY") or self.env.get("ANTHROPIC_AUTH_TOKEN"):
            # key-based backend: run the CLI with its own config dir so the developer's claude.ai login,
            # user-scope MCP servers and skills never reach the critic
            self.env.setdefault("CLAUDE_CONFIG_DIR", str(self.repo / ".claude-critic"))
            Path(self.env["CLAUDE_CONFIG_DIR"]).mkdir(parents=True, exist_ok=True)
        if self.env.get("ANTHROPIC_API_KEY") or self.env.get("ANTHROPIC_AUTH_TOKEN"):
            # key-based backend (Anthropic API key, or DeepSeek's Anthropic-compatible endpoint): run the CLI with
            # its own config dir so the developer's claude.ai login, user-scope MCP servers and skills never leak in
            self.env.setdefault("CLAUDE_CONFIG_DIR", str(self.repo / ".claude-critic"))
            Path(self.env["CLAUDE_CONFIG_DIR"]).mkdir(parents=True, exist_ok=True)
        self.weave_ready = False
        if os.environ.get("WANDB_API_KEY") and not a.local:
            try:
                import weave
                ent = os.environ.get("WANDB_ENTITY")
                weave.init(f"{ent}/tenfold" if ent else "tenfold")
                self.weave_ready = True
            except Exception as e:
                log(f"weave init failed, continuing untraced: {e}", None)

    # ---------- worktree ----------
    def setup_worktree(self) -> None:
        wt = self.worktree
        wt.mkdir(parents=True, exist_ok=True)
        for rel in CRITIC_FILES:
            dst = wt / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(self.repo / rel, dst)
        shutil.copy(self.repo / "loop" / "CLAUDE.critic.md", wt / "CLAUDE.md")
        shutil.copy(self.repo / RULES_REL, wt / RULES_REL)
        report = self.repo / "eval" / "last_train_report.json"
        (wt / "eval").mkdir(exist_ok=True)
        if report.exists():
            shutil.copy(report, wt / "eval" / "last_train_report.json")
        if not (wt / ".git").exists():
            self.git_wt("init", "-q")
            self.git_wt("config", "user.name", "critic-base")
            self.git_wt("config", "user.email", "critic@tenfold.local")
        self.git_wt("add", "-A")
        self.git_wt("-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", f"base {now()}")

    def git_wt(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.worktree, capture_output=True, text=True, check=False).stdout

    def reset_worktree(self) -> None:
        self.git_wt("checkout", "--", ".")
        self.git_wt("clean", "-fdq")
        shutil.copy(self.repo / RULES_REL, self.worktree / RULES_REL)
        report = self.repo / "eval" / "last_train_report.json"
        (self.worktree / "eval").mkdir(exist_ok=True)
        if report.exists():
            shutil.copy(report, self.worktree / "eval" / "last_train_report.json")
        self.git_wt("add", "-A")
        self.git_wt("-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", f"iteration base {now()}")

    # ---------- agents ----------
    def run_claude(self, prompt: str, tools: str, transcript: Path, max_turns: int) -> tuple[str, int]:
        """Run claude -p (or the mock) in the worktree; returns (assistant text, returncode). Transcript = stream-json."""
        if self.a.mock_claude:
            cmd = [sys.executable, str(Path(self.a.mock_claude).resolve()), "-p", prompt, "--allowedTools", tools]
        else:
            cmd = [self.a.claude_bin, "-p", prompt, "--output-format", "stream-json", "--verbose",
                   "--allowedTools", tools, "--max-turns", str(max_turns),
                   "--mcp-config", str(self.repo / "loop" / "mcp.json"), "--strict-mcp-config"]
        try:
            proc = subprocess.run(cmd, cwd=self.worktree, env=self.env, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=self.a.timeout)
        except subprocess.TimeoutExpired:
            transcript.write_text("")
            return "", 124
        transcript.write_text(proc.stdout)
        texts: list[str] = []
        for line in proc.stdout.splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "result" and ev.get("is_error") and ev.get("api_error_status") in (401, 403):
                return str(ev.get("result", "authentication failed")), 401
            if ev.get("type") == "assistant":
                for b in (ev.get("message") or {}).get("content") or []:
                    if b.get("type") == "text":
                        texts.append(b["text"])
            elif ev.get("type") == "result" and ev.get("result"):
                texts.append(str(ev["result"]))
        if not texts and proc.stdout.strip() and not proc.stdout.lstrip().startswith("{"):
            texts.append(proc.stdout)
        return "\n".join(texts).strip(), proc.returncode

    def prompt(self, name: str, **fields: str) -> str:
        text = (self.repo / "loop" / "prompts" / f"{name}.md").read_text()
        for k, v in fields.items():
            text = text.replace("{" + k + "}", v)
        return text

    @maybe_weave_op("critic.diagnose")
    def diagnose(self, iteration: int) -> str:
        if self.a.blind:
            return "DIAGNOSIS: no failure data available (blind arm).\nHYPOTHESIS: improve the classifier however you see fit."
        text, rc = self.run_claude(self.prompt("diagnostic", train_eval_name=f"tenfold-train-{self.tag_prev}"),
                                   DIAG_TOOLS, self.tmp / f"diag-{iteration}.jsonl", 15)
        if rc == 401:
            raise CriticAuthError(text)
        return text if rc == 0 and text else f"DIAGNOSIS: unavailable (claude rc={rc}).\nHYPOTHESIS: none."

    @maybe_weave_op("critic.patch")
    def patch(self, iteration: int, diagnosis: str, feedback: str, next_version: int) -> tuple[str, int, Path]:
        transcript = self.tmp / f"patch-{iteration}.jsonl"
        text, rc = self.run_claude(
            self.prompt("patch", diagnosis=diagnosis, feedback=feedback, next_version=str(next_version)),
            PATCH_TOOLS, transcript, self.a.max_turns)
        return text, rc, transcript

    @maybe_weave_op("critic.guard_agent")
    def guard_agent(self, iteration: int, diagnosis: str, diff: str) -> str:
        if self.a.blind:
            return "VERDICT: APPROVE | blind arm has no diagnosis to check against"
        text, rc = self.run_claude(self.prompt("guard", diagnosis=diagnosis, diff=diff[:12000]), "",
                                   self.tmp / f"guard-{iteration}.jsonl", 1)
        return text if rc == 0 and text else "VERDICT: REJECT | guard agent unavailable | fix: retry"

    @maybe_weave_op("critic.guard_code")
    def guard_code(self, transcript: Path) -> list[str]:
        p = subprocess.run([sys.executable, str(self.repo / "loop" / "guard.py"), "--worktree", str(self.worktree),
                            "--transcript", str(transcript)], capture_output=True, text=True, timeout=120)
        return [l for l in p.stdout.splitlines() if l.startswith("GUARD_REJECT ") or l.startswith("GUARD_ERROR")]

    # ---------- evaluation and gate ----------
    def run_eval(self, split: str, rules: Path, tag: str) -> dict | None:
        out = self.tmp / f"{split}-{tag}.json"
        cmd = [sys.executable, str(self.repo / "eval" / "run_eval.py"), "--split", split, "--rules", str(rules),
               "--tag", tag, "--out", str(out)]
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
        if prev is None:
            return True, "no previous version"
        pe, ce = prev["exact_match"], cand["exact_match"]
        if ce is None or pe is None:
            return False, "exact_match unavailable"
        if ce < pe - 1e-9:
            return False, f"exact_match fell {pe:.3f} -> {ce:.3f}"
        drops = [(c, prev["per_class"].get(c), v) for c, v in cand["per_class"].items()
                 if prev["per_class"].get(c) is not None and v is not None and v < prev["per_class"][c] - 0.05 - 1e-9]
        if drops:
            c, a, b = drops[0]
            return False, f"class {c} fell {a:.2f} -> {b:.2f} (limit 5 points), {len(drops)} class(es) regressed"
        fu = cand.get("false_unknown_rate")
        if fu is not None and fu > 0.05 and (prev.get("false_unknown_rate") or 0) <= 0.05:
            return False, f"false_unknown_rate rose to {fu:.3f} (limit 0.05)"
        return True, f"exact_match {pe:.3f} -> {ce:.3f}"

    # ---------- persistence ----------
    def load_metrics(self) -> dict:
        if self.metrics_path.exists():
            return json.loads(self.metrics_path.read_text())
        return {"versions": [], "rejected": []}

    def save_metrics(self, m: dict) -> None:
        self.metrics_path.parent.mkdir(parents=True, exist_ok=True)
        self.metrics_path.write_text(json.dumps(m, indent=2))

    def commit(self, version: int, diagnosis: str, patch_summary: str, expected: str) -> str:
        shutil.copy(self.worktree / RULES_REL, self.repo / RULES_REL)
        if self.a.no_commit:
            return "no-commit"
        diag = diagnosis.split("\n")[0].replace("DIAGNOSIS:", "").strip()[:120]
        msg = f"critic v{version}: {diag} | patch: {patch_summary[:120]} | expected: {expected[:120]}"
        subprocess.run(["git", "add", str(RULES_REL)], cwd=self.repo, check=True)
        subprocess.run(["git", "-c", "user.name=critic-agent", "-c", "user.email=critic-agent@tenfold.local",
                        "commit", "-q", "-m", msg], cwd=self.repo, check=True)
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True).stdout.strip()
        (self.repo / "data").mkdir(exist_ok=True)
        (self.repo / "data" / "BEST_VERSION").write_text(sha + "\n")
        return sha

    @maybe_weave_op("critic.heartbeat")
    def heartbeat(self, iteration: int, status: str) -> dict:
        return {"iteration": iteration, "status": status, "ts": now(), "blind": self.a.blind}

    @maybe_weave_op("critic.rejected_patch")
    def record_rejection(self, iteration: int, reason: str, diff: str) -> dict:
        return {"iteration": iteration, "reason": reason, "diff": diff[:4000]}

    # ---------- main loop ----------
    def run(self) -> int:
        a = self.a
        self.tmp = self.repo / "loop" / "transcripts"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.setup_worktree()
        metrics = self.load_metrics()
        accepted = [v for v in metrics["versions"] if v.get("accepted")]
        if not accepted:
            log("no baseline: evaluating V0 on train", self.logfile)
            base = self.run_eval("train", self.repo / RULES_REL, "v0")
            if base is None:
                log("FATAL: V0 evaluation failed", self.logfile)
                return 2
            entry = {"tag": "v0", "sha": base["git_sha"], "train": base["metrics"], "heldout": None,
                     "accepted": True, "blind": a.blind, "ts": now(), "diagnosis": "baseline"}
            if not a.dry_run and not a.skip_heldout:
                h = self.run_eval("heldout", self.repo / RULES_REL, "v0")
                entry["heldout"] = h["metrics"] if h else None
            metrics["versions"].append(entry)
            self.save_metrics(metrics)
            accepted = [entry]
        version = int(accepted[-1]["tag"].lstrip("v")) + 1
        self.tag_prev = accepted[-1]["tag"]
        no_improve = 0
        for it in range(1, a.iterations + 1):
            log(f"iteration {it}/{a.iterations} start (next version v{version})", self.logfile)
            self.heartbeat(it, "start")
            self.reset_worktree()
            diagnosis = self.diagnose(it)
            log("diagnosis: " + diagnosis.split("\n")[0][:160], self.logfile)
            feedback = ""
            outcome = "failed"
            for attempt in range(1, 3):
                text, rc, transcript = self.patch(it, diagnosis, feedback, version)
                if rc == 124:
                    log(f"patch attempt {attempt}: claude timed out after {a.timeout}s", self.logfile)
                    feedback = ""
                    self.reset_worktree()
                    continue
                if rc == 401:
                    raise CriticAuthError(text)
                if rc != 0:
                    log(f"patch attempt {attempt}: claude exited {rc}", self.logfile)
                    break
                diff = self.git_wt("diff", "--", str(RULES_REL))
                rejects = self.guard_code(transcript)
                if not rejects:
                    verdict = self.guard_agent(it, diagnosis, diff)
                    if "APPROVE" not in verdict.upper():
                        rejects = [f"GUARD_REJECT agent: {verdict.strip()[:300]}"]
                if rejects:
                    log(f"patch attempt {attempt} rejected: " + " || ".join(r[:160] for r in rejects), self.logfile)
                    feedback = "The previous attempt was rejected by the guard:\n" + "\n".join(rejects) + \
                               "\nStart again from the original file (it has been restored) and address every line."
                    self.record_rejection(it, "guard: " + rejects[0][:200], diff)
                    self.reset_worktree()
                    continue
                patch_summary = next((l.split(":", 1)[1].strip() for l in text.splitlines() if l.startswith("PATCH:")), text[-200:])
                expected = next((l.split(":", 1)[1].strip() for l in text.splitlines() if l.startswith("EXPECTED:")), "")
                if a.dry_run:
                    print("\n----- DRY RUN: guard passed, diff below, nothing committed -----\n" + diff)
                    return 0
                cand = self.run_eval("train", self.worktree / RULES_REL, f"v{version}-candidate")
                if cand is None:
                    log("train eval failed; iteration abandoned", self.logfile)
                    break
                ok, why = self.gate(accepted[-1]["train"], cand["metrics"])
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
                entry = {"tag": f"v{version}", "sha": sha, "train": cand["metrics"], "heldout": None, "accepted": True,
                         "blind": a.blind, "ts": now(), "diagnosis": diagnosis[:300], "patch": patch_summary,
                         "expected": expected, "gate": why}
                if not a.skip_heldout:
                    h = self.run_eval("heldout", self.repo / RULES_REL, f"v{version}")
                    entry["heldout"] = h["metrics"] if h else None
                metrics["versions"].append(entry)
                self.save_metrics(metrics)
                accepted.append(entry)
                log(f"ACCEPTED v{version} ({why}) sha={sha[:8]}", self.logfile)
                self.tag_prev = f"v{version}"
                version += 1
                no_improve = 0 if "->" in why and float(why.split("->")[1]) > float(why.split("->")[0].split()[-1]) else no_improve + 1
                outcome = "accepted"
                break
            self.heartbeat(it, outcome)
            if not a.no_early_stop and no_improve >= 3:
                log("stop: 3 iterations without improvement", self.logfile)
                break
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=1)
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--worktree", default=str(REPO.parent / "tenfold-critic"))
    ap.add_argument("--metrics", default="data/metrics.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--mock-claude", help="script that stands in for `claude` (tests)")
    ap.add_argument("--claude-bin", default="claude")
    ap.add_argument("--blind", action="store_true", help="ablation: no failure data for the diagnosis")
    ap.add_argument("--no-commit", action="store_true", help="never commit to the repo (blind arm)")
    ap.add_argument("--no-early-stop", action="store_true")
    ap.add_argument("--skip-heldout", action="store_true")
    ap.add_argument("--local", action="store_true", help="no W&B anywhere")
    ap.add_argument("--train-samples")
    ap.add_argument("--heldout-samples")
    ap.add_argument("--max-turns", type=int, default=30)
    ap.add_argument("--timeout", type=int, default=600)
    a = ap.parse_args()
    load_env(Path(a.repo).resolve() / ".env")
    if a.blind:
        a.no_commit = True
        if a.metrics == "data/metrics.json":
            a.metrics = "data/metrics-blind.json"
    if a.local:
        os.environ.pop("WANDB_API_KEY", None)
    try:
        return Critic(a).run()
    except CriticAuthError as e:
        log(f"STOP: the critic cannot authenticate to Claude ({e}). Fix: run `claude login` (or set ANTHROPIC_API_KEY "
            f"with credit) on this machine, then `python loop/critic.py --dry-run`.", None)
        return 3


if __name__ == "__main__":
    sys.exit(main())
