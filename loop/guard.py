"""The guard in code. Runs before any commit; the critic never gets to bypass it.

    python loop/guard.py --check                                  # inside the critic worktree, by the patch agent
    python loop/guard.py --worktree ../tenfold-critic --transcript /tmp/patch.jsonl   # full check, by critic.py

Every failed rule prints one line:
    GUARD_REJECT <rule>: <what happened> | cause: <why> | fix: <what to change>
Exit 0 with a last line GUARD_OK = accepted, 1 = rejected, 2 = the guard itself could not run. Callers accept only
exit 0 and GUARD_OK: a crash, a timeout or a truncated output is a rejection.

Rules (SPEC.md section 7): only classifier/rules.py changed; importable; diff under 80 lines; file under 400
lines; AST import whitelist, forbidden names and attributes, no module reached or passed around beyond the
whitelist; transcript audit: no tool input referencing the held-out data, no executed tool call reaching outside
the worktree. The candidate is executed only when every one of those passes: then the smoke test runs in a
subprocess with an empty environment and a runtime audit hook, and only a verdict carrying a per-run token counts.

What this is not: a sandbox. The static rules are a deny list over what the allowed modules expose, and the audit
hook (PEP 578) is a tripwire that Python itself documents as unsuitable for sandboxing. Together they stop the
known routes (file access through numpy, os reached through dataclasses.sys, eval through typing, forged verdicts)
and every route is covered by a test; real isolation needs an OS sandbox around every process that runs rules.py.
"""
from __future__ import annotations

import argparse
import ast
import importlib
import importlib.util
import json
import os
import secrets
import shlex
import subprocess
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALLOWED_IMPORTS = {"math", "statistics", "dataclasses", "typing", "numpy", "classifier", "classifier.schema",
                   "classifier.features", "__future__"}
# modules rules.py may hold a reference to: the whitelist plus numpy's linear algebra, nothing reached through them
ALLOWED_MODULES = ALLOWED_IMPORTS | {"numpy.linalg"}
FORBIDDEN_NAMES = {"open", "exec", "eval", "__import__", "getattr", "setattr", "delattr", "globals", "locals", "vars",
                   "compile", "breakpoint", "input", "help", "exit", "quit", "license", "copyright", "credits", "sys",
                   "os", "inspect", "importlib", "subprocess", "socket", "urllib", "pickle", "shutil", "pathlib",
                   "builtins", "ctypes", "gc", "operator", "types", "marshal"}
# Attributes that read or write files, load native code, evaluate strings or expose frames, whatever the receiver
# (np.load, arr.tofile, np.ctypeslib.load_library, typing.get_type_hints, tb.tb_frame). Checked on every attribute
# access, so an alias (f = np.load) is caught too. A deny list: ALLOWED_MODULES and the runtime hook back it up.
FORBIDDEN_ATTRS = {"load", "loads", "loadtxt", "genfromtxt", "fromfile", "fromregex", "tofile", "save", "savez",
                   "savez_compressed", "savetxt", "memmap", "open_memmap", "DataSource", "load_library", "dump", "dumps",
                   "info", "source", "lookfor", "show_config", "show_runtime", "get_type_hints", "ForwardRef", "mro",
                   "f_globals", "f_locals", "f_back", "f_builtins", "f_code", "tb_frame", "tb_next", "gi_frame",
                   "gi_code", "cr_frame", "ag_frame"}
FORBIDDEN_TEXT = ("heldout", "held-out", "held_out", "test.jsonl", "tenfold-heldout", "tenfold-validation",
                  "validation.jsonl")
PATH_FIELDS = ("file_path", "path", "notebook_path")
MAX_DIFF_LINES = 80
MAX_FILE_LINES = 400
SMOKE_TIMEOUT_S = 30.0
RULES = Path("classifier/rules.py")
# Runtime audit events the candidate may not raise during the smoke run. A trailing dot is a prefix. compile, exec
# and sys._getframe stay allowed: @dataclass and TypeVar raise them in legitimate code.
DENIED_EVENTS = ("open", "import", "code.__new__", "builtins.input", "sys.addaudithook", "sys.settrace",
                 "sys.setprofile", "sys._current_frames", "gc.get_objects", "gc.get_referrers", "gc.get_referents",
                 "os.", "subprocess.", "shutil.", "socket.", "ctypes.", "pty.", "glob.", "tempfile.", "mmap.", "fcntl.",
                 "resource.", "syslog.", "sqlite3.", "urllib.", "http.", "ftplib.", "smtplib.", "poplib.", "imaplib.",
                 "nntplib.", "telnetlib.", "webbrowser.", "winreg.", "msvcrt.", "_winapi.", "cpython.")


def reject(rule: str, what: str, cause: str, fix: str) -> str:
    return f"GUARD_REJECT {rule}: {what} | cause: {cause} | fix: {fix}"


def _banned_attr(name: str) -> bool:
    return name in FORBIDDEN_NAMES or name in FORBIDDEN_ATTRS or name.startswith("_")


_MODULE_VALUES: dict[tuple[str, str], str | None] = {}


def _module_value(module: str, attr: str) -> str | None:
    """Name of the module `module.attr` is, None when it is not a module (a function, a class, a constant, missing).
    Imports the trusted copies next to this file; an import failure propagates, so the guard fails closed."""
    key = (module, attr)
    if key not in _MODULE_VALUES:
        if str(HERE.parent) not in sys.path:
            sys.path.insert(0, str(HERE.parent))
        parent = importlib.import_module(module)
        value = getattr(parent, attr, None)
        if isinstance(value, types.ModuleType):
            _MODULE_VALUES[key] = value.__name__
        elif value is None and hasattr(parent, "__path__") and importlib.util.find_spec(f"{module}.{attr}") is not None:
            # a submodule nobody imported yet (classifier.features in a fresh process, numpy.distutils): still a module
            _MODULE_VALUES[key] = f"{module}.{attr}"
        else:
            _MODULE_VALUES[key] = None
    return _MODULE_VALUES[key]


def check_ast(src: str) -> list[str]:
    out: list[str] = []
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [reject("syntax", f"rules.py does not parse (line {e.lineno})", str(e.msg), "fix the syntax error")]
    no_io = "compute from the Window only; no I/O, no reflection"
    aliases: dict[str, str] = {}  # local name -> the module it is bound to
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name not in ALLOWED_MODULES:
                    out.append(reject("imports", f"import {a.name}", "only the standard geometry stack is allowed",
                                      f"remove it; allowed: {sorted(ALLOWED_MODULES)}"))
                else:  # import numpy.linalg binds numpy; import numpy.linalg as la binds la to numpy.linalg
                    bound = a.asname or a.name.split(".")[0]
                    aliases[bound] = a.name if a.asname else bound
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level or mod not in ALLOWED_MODULES:
                out.append(reject("imports", f"from {'.' * node.level}{mod} import ...", "only the standard geometry stack is allowed",
                                  f"remove it; allowed: {sorted(ALLOWED_MODULES)}"))
                continue
            for a in node.names:
                if a.name == "*":
                    out.append(reject("imports", f"from {mod} import *", "star imports hide what is used", "import names explicitly"))
                elif _banned_attr(a.name):
                    out.append(reject("forbidden_name", f"from {mod} import {a.name}", "file, process or introspection access is not allowed in the classifier", no_io))
                elif (target := _module_value(mod, a.name)) is not None:
                    if target in ALLOWED_MODULES:
                        aliases[a.asname or a.name] = target
                    else:
                        out.append(reject("module_escape", f"from {mod} import {a.name} (module {target})",
                                          "a module reached through an allowed one is not allowed", no_io))
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            out.append(reject("forbidden_name", f"use of {node.id}", "file, process or introspection access is not allowed in the classifier", no_io))
        elif isinstance(node, ast.Attribute) and _banned_attr(node.attr):
            out.append(reject("forbidden_name", f"use of .{node.attr}", "file access, native code, string evaluation or reflection is not allowed", no_io))

    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

    def module_of(expr: ast.AST) -> str | None:
        if isinstance(expr, ast.Name):
            return aliases.get(expr.id)
        if isinstance(expr, ast.Attribute) and not _banned_attr(expr.attr):
            base = module_of(expr.value)
            return _module_value(base, expr.attr) if base else None
        return None

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Name, ast.Attribute)) or (mod := module_of(node)) is None:
            continue
        where = ast.unparse(node)
        parent = parents.get(node)
        if mod not in ALLOWED_MODULES:
            out.append(reject("module_escape", f"{where} is the module {mod}", "a module reached through an allowed one is not allowed", no_io))
        elif not (isinstance(parent, ast.Attribute) and parent.value is node):
            out.append(reject("module_value", f"{where} is used as a value", "a module may only be read as mod.name",
                              "call the function directly, do not store, pass or rebind a module"))
        elif not isinstance(parent.ctx, ast.Load):
            out.append(reject("module_write", f"{ast.unparse(parent)} is assigned or deleted", "rules.py may not patch a shared module",
                              "keep changes inside rules.py"))
    return out


def check_size(src: str) -> list[str]:
    n = src.count("\n") + 1
    if n > MAX_FILE_LINES:
        return [reject("file_size", f"rules.py is {n} lines, limit {MAX_FILE_LINES}", "the file keeps growing",
                       "prefer editing existing logic over adding; delete what the diagnosis made obsolete")]
    return []


def check_smoke(worktree: Path, timeout: float = SMOKE_TIMEOUT_S) -> list[str]:
    """Run the smoke test on the worktree's rules.py in a sandboxed subprocess (see sandbox_smoke). Accepted only on
    exit 0 with SMOKE_OK and this run's token as the last line, so a candidate that prints a verdict and exits cannot
    pass. The environment is empty: no API key, held-out key or path reaches the candidate."""
    token = secrets.token_hex(16)
    env = {"PATH": os.defpath}
    if os.environ.get("TENFOLD_CLASSIFY_P95_MS"):
        env["TENFOLD_CLASSIFY_P95_MS"] = os.environ["TENFOLD_CLASSIFY_P95_MS"]
    cmd = [sys.executable, "-I", "-B", str(HERE / "guard.py"), "--sandbox-smoke", str((worktree / RULES).resolve())]
    try:
        p = subprocess.run(cmd, cwd=worktree, input=token + "\n", capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return [reject("smoke", f"smoke test exceeded {timeout:.0f} s", "classify() is too slow or hangs",
                       "keep classify() under a few milliseconds; no loops over frames beyond the window")]
    except OSError as e:
        return [reject("smoke", "the smoke subprocess could not start", f"{type(e).__name__}: {e}", "rerun the guard")]
    out = p.stdout.splitlines()
    fails = ["GUARD_REJECT " + l[len("SMOKE_FAIL "):] for l in out if l.startswith("SMOKE_FAIL ")]
    last = next((l.strip() for l in reversed(out) if l.strip()), "")
    if p.returncode == 0 and last == f"SMOKE_OK {token}" and not fails:
        return []
    if fails:
        return fails
    cause = (p.stderr.strip()[-300:] or f"exit {p.returncode}, last line {last[:80]!r}").replace("\n", " ")
    return [reject("smoke", f"smoke test ended without its verdict (exit {p.returncode})", cause,
                   "classify() and the module body must return normally: no exit, no crash, no printed verdict")]


def _denied(event: str) -> bool:
    return any(event.startswith(e) if e.endswith(".") else event == e for e in DENIED_EVENTS)


def sandbox_smoke(rules: Path) -> int:
    """Child side of check_smoke: import the trusted stack, arm an audit hook, then load and smoke-test the candidate.
    Any denied event raises PermissionError inside the candidate and is recorded, so catching the error does not
    help. Only rules.py itself may be opened, read-only; its cached bytecode never loads, so what runs is the source
    the AST check read."""
    token = sys.stdin.readline().strip()
    sys.path.insert(0, str(HERE.parent))
    import warnings
    warnings.simplefilter("ignore")  # a warning would open source files to show the offending line
    import dataclasses, math, statistics, typing  # noqa: E401,F401  loaded now, so the candidate triggers no import
    import numpy, numpy.linalg  # noqa: E401,F401
    import classifier.features, classifier.schema  # noqa: E401,F401
    import importlib.util
    from loop import smoke

    source = os.path.realpath(rules)
    cached = os.path.realpath(importlib.util.cache_from_source(source))
    blocked: list[str] = []

    def hook(event: str, args: tuple) -> None:
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            path = os.path.realpath(os.fsdecode(args[0]))
            mode = args[1] if len(args) > 1 else None
            if path == cached:
                raise PermissionError("sandbox: cached bytecode is not loaded")
            if path == source and isinstance(mode, str) and not set(mode) & set("wax+"):
                return
        if _denied(event):
            blocked.append(f"{event} {str(args)[:120]}")
            raise PermissionError(f"sandbox: {event} is not allowed in the classifier")

    sys.addaudithook(hook)
    try:
        failures = smoke.run(Path(source))
    except BaseException as e:  # SystemExit or KeyboardInterrupt raised by the candidate ends its run, not the verdict
        failures = [f"smoke: the candidate stopped the run | cause: {type(e).__name__}: {e} | fix: return a GestureState, never exit"]
    if blocked:
        failures.insert(0, f"sandbox: {len(blocked)} blocked operation(s), first: {blocked[0]} | cause: file, process, "
                           "network or import access at runtime | fix: compute from the Window only")
    for f in failures:
        print("SMOKE_FAIL " + f.replace("\n", " "))
    print(f"SMOKE_FAILED {len(failures)}" if failures else f"SMOKE_OK {token}", flush=True)
    return 1 if failures else 0


def check_worktree_files(worktree: Path) -> list[str]:
    try:
        st = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=worktree,
                            capture_output=True, text=True, timeout=10, check=True).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
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
    try:
        num = subprocess.run(["git", "diff", "--no-ext-diff", "--no-textconv", "--numstat", "--", str(RULES)], cwd=worktree,
                             capture_output=True, text=True, timeout=10, check=True).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        return [f"GUARD_ERROR git diff failed in {worktree}: {e}"]
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
                    out.append(reject("held_out", f"a {block.get('name')} call referenced {bad!r}", "the held-out and validation sets are off limits",
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


def static_then_smoke(worktree: Path, static: list[str]) -> list[str]:
    """The candidate runs only when every static rule passed; otherwise it is never executed."""
    if static:
        return static + ["GUARD_NOTE smoke not run: rules.py is never executed while a rule above fails"]
    return check_smoke(worktree)


def full(worktree: Path, transcript: Path | None) -> list[str]:
    rules = worktree / RULES
    if not rules.exists():
        return [reject("files", "classifier/rules.py is missing", "it was deleted or moved", "restore classifier/rules.py")]
    src = rules.read_text()
    static = check_worktree_files(worktree) + check_diff_size(worktree) + check_size(src) + check_ast(src)
    if transcript:
        static += check_transcript(transcript, worktree)
    return static_then_smoke(worktree, static)


def quick(worktree: Path) -> list[str]:
    rules = worktree / RULES
    if not rules.exists():
        return [reject("files", "classifier/rules.py is missing", "it was deleted or moved", "restore classifier/rules.py")]
    src = rules.read_text()
    static = check_size(src) + check_ast(src)
    if (worktree / ".git").exists():
        static += check_diff_size(worktree)
    return static_then_smoke(worktree, static)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="quick self-check from inside the worktree")
    ap.add_argument("--worktree", default=".")
    ap.add_argument("--transcript")
    ap.add_argument("--sandbox-smoke", metavar="RULES", help=argparse.SUPPRESS)  # internal: the child of check_smoke
    args = ap.parse_args()
    if args.sandbox_smoke:
        return sandbox_smoke(Path(args.sandbox_smoke))
    wt = Path(args.worktree).resolve()
    try:
        problems = quick(wt) if args.check else full(wt, Path(args.transcript) if args.transcript else None)
    except Exception as e:  # fail closed: no GUARD_OK line, exit 2
        print(f"GUARD_ERROR the guard itself failed: {type(e).__name__}: {e}")
        return 2
    for p in problems:
        print(p)
    if any(p.startswith("GUARD_ERROR") for p in problems):
        return 2
    rejects = [p for p in problems if p.startswith("GUARD_REJECT")]
    print("GUARD_OK" if not problems else f"GUARD_REJECTED {len(rejects)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
