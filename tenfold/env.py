"""Load .env from the repo root into os.environ without overriding what is already set. No dependency."""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load_env(path: Path | None = None) -> dict[str, str]:
    p = path or REPO / ".env"
    loaded: dict[str, str] = {}
    if not p.exists():
        return loaded
    for raw in p.read_text().splitlines():
        if raw.strip().startswith("#") or "=" not in raw:
            continue
        k, v = raw.split("=", 1)
        k, v = k.strip(), v.split(" #", 1)[0].strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v
            loaded[k] = v
    return loaded
