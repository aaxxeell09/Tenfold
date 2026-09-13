"""Runs web/course/levels_test.js, so the course progression is covered by make test.

levels.js is the single source of course structure and progress, and the camera
lesson scores against it, so its rules belong in the same suite as the Python.
Skipped where node is not installed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SUITE = REPO / "web" / "course" / "levels_test.js"


def test_the_course_progression_rules():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed here")
    if not SUITE.exists():
        pytest.skip(f"{SUITE} is missing")
    result = subprocess.run([node, "--test", str(SUITE)], cwd=REPO,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    assert "# fail 0" in result.stdout
