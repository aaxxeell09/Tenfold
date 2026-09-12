"""FROZEN CONTRACT (SPEC.md 5.3, 5.4, 5.5). Both team members sign off; nobody edits it alone.

Landmark indices (MediaPipe): wrist 0; fingertips 4 (thumb) 8 12 16 20; PIP 6 10 14 18; MCP 5 9 13 17.
6-10 numbering: thumb = 6, index = 7, middle = 8, ring = 9, pinky = 10.

One sample line in data/samples.jsonl (and ../tenfold-heldout/test.jsonl):

{
  "id": "s000123", "hold_id": "axel_1:8x7:front:near:0", "session": "axel_1",
  "angle": "front|top|side", "distance": "near|far",
  "kind": "positive|near_contact|partial_hand|out_of_frame|transition|rest",
  "label": {"method": "6-10", "left": 8, "right": 7, "contact": true},      # or {"method": "unknown"}
  "split": "train|test",
  "window": [ {"left": HAND|null, "right": HAND|null}, ... ]                  # 1 to 5 frames, oldest first
}
HAND = {"points": [[x, y, z] * 21], "conf": 0.93, "wrist": [x, y], "scale": 0.12}
  points: wrist-origin, divided by scale (SPEC 5.2); wrist: mirrored image coords in 0..1; scale: wrist -> middle MCP
  distance in image coords. `wrist` and `scale` let classifier/features.py rebuild one shared frame for both hands.
"left"/"right" are decided by wrist x in the mirrored image (child's left = screen left), not by MediaPipe handedness.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

FINGER_NUMBERS: tuple[int, ...] = (6, 7, 8, 9, 10)
METHODS: tuple[str, ...] = ("6-10", "table9", "unknown")
KINDS: tuple[str, ...] = ("positive", "near_contact", "partial_hand", "out_of_frame", "transition", "rest")
NEGATIVE_KINDS: tuple[str, ...] = ("partial_hand", "out_of_frame", "transition", "rest")
WINDOW_SIZE = 5


@dataclass(frozen=True)
class HandFrame:
    points: Optional[list[tuple[float, float, float]]]  # 21 normalized points, None if the hand is absent
    detection_conf: float
    wrist_xy: Optional[tuple[float, float]]  # mirrored image coords 0..1
    scale: Optional[float]  # wrist -> middle MCP distance, image coords

    @property
    def present(self) -> bool:
        return self.points is not None and self.wrist_xy is not None and bool(self.scale) and len(self.points) == 21


@dataclass(frozen=True)
class Window:
    left: list[HandFrame] = field(default_factory=list)
    right: list[HandFrame] = field(default_factory=list)

    def __len__(self) -> int:
        return min(len(self.left), len(self.right))


@dataclass(frozen=True)
class GestureState:
    method: str  # "6-10" | "table9" | "unknown"
    left: Optional[int] = None  # 6..10 for 6-10
    right: Optional[int] = None
    contact: bool = False  # 6-10 only
    folded: Optional[int] = None  # 1..10 for table9
    confidence: float = 0.0  # 0..1

    @staticmethod
    def unknown(confidence: float = 0.0) -> "GestureState":
        return GestureState(method="unknown", confidence=confidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "left": self.left,
            "right": self.right,
            "contact": self.contact,
            "folded": self.folded,
            "confidence": self.confidence,
        }


# ---------- JSON <-> dataclasses (used by capture.py, eval/, tests) ----------

def hand_from_json(obj: Optional[dict[str, Any]]) -> HandFrame:
    if obj is None or obj.get("points") is None:
        return HandFrame(points=None, detection_conf=0.0, wrist_xy=None, scale=None)
    pts = [tuple(float(v) for v in p) for p in obj["points"]]
    wrist = obj.get("wrist")
    return HandFrame(
        points=pts,  # type: ignore[arg-type]
        detection_conf=float(obj.get("conf", 0.0)),
        wrist_xy=(float(wrist[0]), float(wrist[1])) if wrist is not None else None,
        scale=float(obj["scale"]) if obj.get("scale") is not None else None,
    )


def hand_to_json(h: HandFrame) -> Optional[dict[str, Any]]:
    if not h.present:
        return None
    return {
        "points": [list(p) for p in h.points],  # type: ignore[union-attr]
        "conf": h.detection_conf,
        "wrist": list(h.wrist_xy),  # type: ignore[arg-type]
        "scale": h.scale,
    }


def window_from_json(frames: list[dict[str, Any]]) -> Window:
    return Window(
        left=[hand_from_json(f.get("left")) for f in frames],
        right=[hand_from_json(f.get("right")) for f in frames],
    )


def window_to_json(w: Window) -> list[dict[str, Any]]:
    return [{"left": hand_to_json(l), "right": hand_to_json(r)} for l, r in zip(w.left, w.right)]


def validate_state(s: Any) -> list[str]:
    """Domain check used by the guard's smoke test. Returns a list of problems, empty if valid."""
    problems: list[str] = []
    if not isinstance(s, GestureState):
        return [f"classify() returned {type(s).__name__}, expected GestureState"]
    if s.method not in METHODS:
        problems.append(f"method {s.method!r} not in {METHODS}")
    for name, v in (("left", s.left), ("right", s.right)):
        if v is not None and (not isinstance(v, int) or v not in FINGER_NUMBERS):
            problems.append(f"{name}={v!r} must be None or one of {FINGER_NUMBERS}")
    if not isinstance(s.contact, bool):
        problems.append(f"contact={s.contact!r} must be bool")
    if s.folded is not None and (not isinstance(s.folded, int) or not 1 <= s.folded <= 10):
        problems.append(f"folded={s.folded!r} must be None or 1..10")
    try:
        c = float(s.confidence)
        if not 0.0 <= c <= 1.0 or c != c:
            problems.append(f"confidence={s.confidence!r} must be in [0, 1]")
    except (TypeError, ValueError):
        problems.append(f"confidence={s.confidence!r} is not a number")
    return problems
