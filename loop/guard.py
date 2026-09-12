"""The guard in code. Runs before any commit; the critic never gets to bypass it.

    python loop/guard.py --check                                  # inside the critic worktree, by the patch agent
    python loop/guard.py --worktree ../tenfold-critic --transcript /tmp/patch.jsonl   # full check, by critic.py

Every failed rule prints one line:
    GUARD_REJECT <rule>: <what happened> | cause: <why> | fix: <what to change>
Exit 0 = accepted, 1 = rejected, 2 = the guard itself could not run.

Rules (SPEC.md section 7): only classifier/rules.py changed; importable; diff under 80 lines; file under 400
lines; AST import whitelist and forbidden names; smoke test in a subprocess; transcript audit: no tool input
referencing the held-out data, no executed tool call reaching outside the worktree.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

ALLOWED_IMPORTS = {"math", "statistics", "dataclasses", "typing", "numpy", "classifier", "classifier.schema",
                   "classifier.features", "__future__"}
FORBIDDEN_NAMES = {"open", "exec", "eval", "__import__", "getattr", "setattr", "globals", "locals", "vars",
                   "compile", "breakpoint", "input", "sys", "os", "inspect", "importlib", "subprocess", "socket",
                   "urllib", "pickle", "shutil", "pathlib"}
FORBIDDEN_TEXT = ("heldout", "held-out", "held_out", "test.jsonl", "tenfold-heldout")
PATH_FIELDS = ("file_path", "path", "notebook_path")
MAX_DIFF_LINES = 80
MAX_FILE_LINES = 400
RULES = Path("classifier/rules.py")


def reject(rule: str, what: str, cause: str, fix: str) -> str:
    return f"GUARD_REJECT {rule}: {what} | cause: {cause} | fix: {fix}"


def check_ast(src: str) -> list[str]:
    out: list[str] = []
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [reject("syntax", f"rules.py does not parse (line {e.lineno})", str(e.msg), "fix the syntax error")]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name not in ALLOWED_IMPORTS and a.name.split(".")[0] not in ALLOWED_IMPORTS:
                    out.append(reject("imports", f"import {a.name}", "only the standard geometry stack is allowed",
                                      f"remove it; allowed: {sorted(ALLOWED_IMPORTS)}"))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod not in ALLOWED_IMPORTS and mod.split(".")[0] not in ALLOWED_IMPORTS:
                out.append(reject("imports", f"from {mod} import ...", "only the standard geometry stack is allowed",
                                  f"remove it; allowed: {sorted(ALLOWED_IMPORTS)}"))
            if any(a.name == "*" for a in node.names):
                out.append(reject("imports", f"from {mod} import *", "star imports hide what is used", "import names explicitly"))
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            out.append(reject("forbidden_name", f"use of {node.id}", "file, process or introspection access is not allowed in the classifier",
                              "compute from the Window only; no I/O, no reflection"))
        elif isinstance(node, ast.Attribute) and node.attr in ("__dict__", "__globals__", "__code__", "__builtins__", "__subclasses__"):
            out.append(reject("forbidden_name", f"use of .{node.attr}", "reflection is not allowed", "remove it"))
    return out


def check_size(src: str) -> list[str]:
    n = src.count("\n") + 1
    if n > MAX_FILE_LINES:
        return [reject("file_size", f"rules.py is {n} lines, limit {MAX_FILE_LINES}", "the file keeps growing",
                       "prefer editing existing logic over adding; delete what the diagnosis made obsolete")]
    return []


def check_smoke(worktree: Path, timeout: float = 30.0) -> list[str]:
    try:
        p = subprocess.run([sys.executable, str(worktree / "loop" / "smoke.py"), "--rules", str(worktree / RULES)],
                           cwd=worktree, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(worktree)})
    except subprocess.TimeoutExpired:
        return [reject("smoke", f"smoke test exceeded {timeout:.0f} s", "classify() is too slow or hangs",
                       "keep classify() under a few milliseconds; no loops over frames beyond the window")]
    if p.returncode == 0:
        return []
    lines = [l[len("SMOKE_FAIL "):] for l in p.stdout.splitlines() if l.startswith("SMOKE_FAIL ")]
    if not lines:
        lines = [f"smoke test crashed | cause: {p.stderr.strip()[-300:]} | fix: make loop/smoke.py --rules pass"]
    return ["GUARD_REJECT " + l for l in lines]


def check_worktree_files(worktree: Path) -> list[str]:
    try:
        st = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=worktree,
                            capture_output=True, text=True, timeout=10, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        return [f"GUARD_ERROR git status failed in {worktree}: {e}"]
    changed = [l[3:].strip() for l in st.splitlines() if l.strip()]
    changed = [c for c in changed if "__pycache__" not in c and not c.endswith(".pyc")]
    if not changed:
        return [reject("no_change", "nothing changed", "the patch agent produced no edit", "edit classifier/rules.py with one coherent change")]
    extra = [c for c in changed if c != str(RULES)]
    if extra:
        return [reject("files", f"changed or created: {', '.join(extra)}", "only classifier/rules.py may change",
                       "revert those files; put all logic inside classifier/rules.py")]
    return []


def check_diff_size(worktree: Path) -> list[str]:
    num = subprocess.run(["git", "diff", "--numstat", "--", str(RULES)], cwd=worktree, capture_output=True, text=True).stdout
    added = deleted = 0
    for line in num.splitlines():
        a, d, *_ = line.split("\t")
        added += int(a) if a.isdigit() else 0
        deleted += int(d) if d.isdigit() else 0
    if added + deleted > MAX_DIFF_LINES:
        return [reject("diff_size", f"diff is {added + deleted} lines, limit {MAX_DIFF_LINES}",
                       "the patch rewrote more than one hypothesis", "keep the change to the single hypothesis in the diagnosis; revert unrelated edits")]
    return []


def _strings(obj) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return []


def _command_paths(cmd: str) -> list[str]:
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = cmd.split()
    parts = [p for t in tokens for p in t.split("=")]
    return [p for p in parts if len(p) > 1 and (p.startswith("/") or p.startswith("~") or p.startswith("../"))]


def _outside(p: str, worktree: str) -> bool:
    p = p.strip().rstrip(".,;:")
    if p.startswith("~"):
        return True
    full = os.path.realpath(p if p.startswith("/") else os.path.join(worktree, p))
    return not (full == worktree or full.startswith(worktree + os.sep))


def check_transcript(path: Path, worktree: Path) -> list[str]:
    """Audit a claude stream-json transcript.
    - Any tool input (executed or denied) that references the held-out data is rejected: that is intent.
    - Executed tool calls whose path fields or command arguments leave the worktree are rejected.
    Denied calls never ran and code inside Edit/Write payloads is not a path, so neither is flagged.
    Assistant prose and tool results are not audited."""
    if not path.exists():
        return []
    wt = os.path.realpath(worktree)
    events = []
    for line in path.read_text().splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    errored: dict[str, bool] = {}
    for ev in events:
        content = (ev.get("message") or {}).get("content") if ev.get("type") == "user" else None
        for block in content if isinstance(content, list) else []:
            if block.get("type") == "tool_result":
                errored[block.get("tool_use_id")] = bool(block.get("is_error"))
    out: list[str] = []
    for ev in events:
        if ev.get("type") != "assistant":
            continue
        for block in (ev.get("message") or {}).get("content") or []:
            if block.get("type") != "tool_use":
                continue
            inp = block.get("input") or {}
            blob = " ".join(_strings(inp)).lower()
            for bad in FORBIDDEN_TEXT:
                if bad in blob:
                    out.append(reject("held_out", f"a {block.get('name')} call referenced {bad!r}", "the held-out set is off limits",
                                      "work only from eval/last_train_report.json and python loop/train_eval.py"))
                    break
            if errored.get(block.get("id"), False):
                continue
            paths = [inp[k] for k in PATH_FIELDS if isinstance(inp.get(k), str)]
            if isinstance(inp.get("command"), str):
                paths += _command_paths(inp["command"])
            for p in paths:
                if _outside(p, wt):
                    out.append(reject("path", f"{block.get('name')} used {p}", "paths outside the worktree are off limits",
                                      "use paths relative to the worktree only"))
    seen: set[str] = set()
    return [o for o in out if not (o in seen or seen.add(o))]


def full(worktree: Path, transcript: Path | None) -> list[str]:
    rules = worktree / RULES
    if not rules.exists():
        return [reject("files", "classifier/rules.py is missing", "it was deleted or moved", "restore classifier/rules.py")]
    src = rules.read_text()
    problems = check_worktree_files(worktree) + check_diff_size(worktree) + check_size(src) + check_ast(src)
    if not any(p.startswith("GUARD_REJECT syntax") or p.startswith("GUARD_ERROR") for p in problems):
        problems += check_smoke(worktree)
    if transcript:
        problems += check_transcript(transcript, worktree)
    return problems


def quick(worktree: Path) -> list[str]:
    rules = worktree / RULES
    src = rules.read_text()
    problems = check_size(src) + check_ast(src)
    if not any(p.startswith("GUARD_REJECT syntax") for p in problems):
        problems += check_smoke(worktree)
    if (worktree / ".git").exists():
        problems += check_diff_size(worktree)
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="quick self-check from inside the worktree")
    ap.add_argument("--worktree", default=".")
    ap.add_argument("--transcript")
    args = ap.parse_args()
    wt = Path(args.worktree).resolve()
    problems = quick(wt) if args.check else full(wt, Path(args.transcript) if args.transcript else None)
    for p in problems:
        print(p)
    if any(p.startswith("GUARD_ERROR") for p in problems):
        return 2
    print("GUARD_OK" if not problems else f"GUARD_REJECTED {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
