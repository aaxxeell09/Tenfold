"""Load the classifier the live app should run: the version pinned in data/BEST_VERSION, never HEAD.

    from classifier.loader import load_classifier
    classify = load_classifier()          # -> Callable[[Window], GestureState], never raises
"""
from __future__ import annotations

import importlib.util
import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

from classifier.schema import GestureState, Window

log = logging.getLogger("tenfold.classifier")
REPO = Path(__file__).resolve().parents[1]
BEST_VERSION_FILE = REPO / "data" / "BEST_VERSION"


def _import_rules_from(path: Path):
    spec = importlib.util.spec_from_file_location(f"tenfold_rules_{abs(hash(str(path)))}", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def resolve_rules_path(pinned: bool = True) -> Path:
    """Path of the rules.py to run. With a valid BEST_VERSION sha, that commit's file is extracted to a temp dir."""
    head = REPO / "classifier" / "rules.py"
    if not pinned or not BEST_VERSION_FILE.exists():
        return head
    sha = BEST_VERSION_FILE.read_text().strip()
    if not sha:
        return head
    try:
        src = subprocess.run(
            ["git", "-C", str(REPO), "show", f"{sha}:classifier/rules.py"],
            check=True, capture_output=True, text=True, timeout=10,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.warning("BEST_VERSION %s unreadable (%s), falling back to HEAD rules.py", sha, e)
        return head
    tmp = Path(tempfile.mkdtemp(prefix="tenfold-rules-")) / "rules.py"
    tmp.write_text(src)
    return tmp


def load_classifier(pinned: bool = True) -> Callable[[Window], GestureState]:
    path = resolve_rules_path(pinned)
    mod = _import_rules_from(path)
    raw = getattr(mod, "classify")

    def safe_classify(window: Window) -> GestureState:
        try:
            state = raw(window)
        except Exception:  # a critic-written file must never crash the demo
            log.exception("classify() raised; returning unknown")
            return GestureState.unknown()
        return state if isinstance(state, GestureState) else GestureState.unknown()

    safe_classify.rules_path = path  # type: ignore[attr-defined]
    return safe_classify
