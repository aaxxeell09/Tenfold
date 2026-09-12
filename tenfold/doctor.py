"""Preflight for a fresh machine: one PASS/WARN/FAIL line per check, each with its fix.

    python -m tenfold.doctor            (or: make doctor)
Exit 0 when nothing FAILs. WARN means the live app or the loop degrades but still runs.
"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def line(status: str, name: str, detail: str, fix: str = "") -> bool:
    print(f"{status:5s} {name:26s} {detail}" + (f"  ->  {fix}" if fix and status != "PASS" else ""))
    return status != "FAIL"


def load_env() -> None:
    p = REPO / ".env"
    if p.exists():
        for l in p.read_text().splitlines():
            l = l.split("#", 1)[0].strip()
            if "=" in l:
                k, v = l.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def check_import(mod: str, required: bool) -> bool:
    try:
        importlib.import_module(mod)
        return line("PASS", mod, "importable")
    except Exception as e:
        return line("FAIL" if required else "WARN", mod, f"not importable ({type(e).__name__})",
                    "pip install -r requirements.txt (Python 3.11: mediapipe has no wheel for 3.13)")


def check_camera() -> bool:
    try:
        import cv2  # type: ignore
    except Exception:
        return line("WARN", "camera", "skipped (no cv2)", "make demo works without a camera")
    result: dict = {}

    def probe():
        cap = cv2.VideoCapture(0)
        ok, _ = cap.read() if cap.isOpened() else (False, None)
        cap.release()
        result["ok"] = ok

    t = threading.Thread(target=probe, daemon=True)
    t.start()
    t.join(4.0)
    if t.is_alive() or not result.get("ok"):
        return line("WARN", "camera", "camera 0 not readable",
                    "macOS: System Settings > Privacy > Camera, allow your terminal; Zoom may hold it; or `make demo`")
    return line("PASS", "camera", "camera 0 delivers frames")


def main() -> int:
    load_env()
    ok = True
    v = sys.version_info
    ok &= line("PASS" if (v.major, v.minor) == (3, 11) else "FAIL", "python", f"{v.major}.{v.minor}.{v.micro}",
               "use Python 3.11 (.python-version): `uv venv -p 3.11 .venv && source .venv/bin/activate`")
    ok &= check_import("numpy", True)
    ok &= check_import("httpx", True)
    ok &= check_import("weave", False)
    ok &= check_import("mediapipe", False)
    ok &= check_import("cv2", False)
    ok &= check_camera()
    ok &= line("PASS" if os.environ.get("WANDB_API_KEY") else "WARN", "WANDB_API_KEY",
               "set" if os.environ.get("WANDB_API_KEY") else "not set: Weave tracing off, tutor uses fallback phrases",
               "put it in .env (wandb.ai/authorize)")
    ok &= line("PASS" if os.environ.get("WANDB_API_KEY_HELDOUT") else "WARN", "WANDB_API_KEY_HELDOUT",
               "set" if os.environ.get("WANDB_API_KEY_HELDOUT") else "not set: held-out eval runs locally only",
               "a second W&B account's key, never the critic's")
    hd = Path(os.environ.get("TENFOLD_HELDOUT", REPO.parent / "tenfold-heldout" / "test.jsonl"))
    ok &= line("PASS" if hd.exists() else "WARN", "held-out data", str(hd) + ("" if hd.exists() else " missing"),
               "capture with other people's hands; keep it outside the repo")
    tr = REPO / "data" / "samples.jsonl"
    ok &= line("PASS" if tr.exists() else "WARN", "train data", str(tr) + ("" if tr.exists() else " missing"),
               "python app/capture.py (Axel)")
    cl = shutil.which("claude")
    ok &= line("PASS" if cl else "WARN", "claude CLI", cl or "not on PATH: the critic loop cannot run",
               "npm install -g @anthropic-ai/claude-code && claude login")
    if cl:
        try:
            out = subprocess.run([cl, "mcp", "list"], capture_output=True, text=True, timeout=20).stdout
            has = "wandb" in out
        except Exception:
            has = False
        ok &= line("PASS" if has else "WARN", "W&B MCP", "registered (user scope)" if has else "not registered: critic uses loop/mcp.json only",
                   'claude mcp add -s user --transport http wandb https://mcp.withwandb.com/mcp --header "Authorization: Bearer $WANDB_API_KEY"')
    bv = REPO / "data" / "BEST_VERSION"
    ok &= line("PASS" if bv.exists() else "WARN", "BEST_VERSION", bv.read_text().strip()[:8] if bv.exists() else "none yet: the app runs HEAD rules.py",
               "created by the first accepted critic iteration")
    try:
        sys.path.insert(0, str(REPO))
        from loop.smoke import run as smoke_run
        fails = smoke_run(REPO / "classifier" / "rules.py")
        ok &= line("PASS" if not fails else "FAIL", "rules.py smoke", "6/6 synthetic windows ok" if not fails else fails[0][:100],
                   "python loop/smoke.py")
    except Exception as e:
        ok &= line("FAIL", "rules.py smoke", f"{type(e).__name__}: {e}", "python loop/smoke.py")
    print("\nDOCTOR OK" if ok else "\nDOCTOR FAILED: fix the FAIL lines above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
