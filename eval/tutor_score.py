"""TutorScore: read data/tutor_log.jsonl and say how well the tutor tutored.

Read only. No loop, no network, no camera, and it imports nothing from app/ so it
runs on a laptop with the log copied onto it and nothing else.

    python eval/tutor_score.py [path/to/tutor_log.jsonl]

docs/tutor_contract.md section 6:

    TutorScore = 4.0 * autonomous_success_rate
               + 2.0 * first_try_rate
               + 2.0 * light_hint_recovery_rate
               - 2.0 * unnecessary_intervention_rate
               - 2.0 * rescue_rate
               - 1.0 * abandonment_rate

An intervention is unnecessary when the child was already moving, or moved within
half a second of being interrupted: they were already fixing it. Interrupting a
child who is solving it is the worst thing a tutor can do, so it costs as much as
a rescue.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

REPO = Path(__file__).resolve().parents[1]
DEFAULT_LOG = REPO / "data" / "tutor_log.jsonl"

ALREADY_SOLVING_S = 0.5

WEIGHTS = {
    "autonomous_success_rate": 4.0,
    "first_try_rate": 2.0,
    "light_hint_recovery_rate": 2.0,
    "unnecessary_intervention_rate": -2.0,
    "rescue_rate": -2.0,
    "abandonment_rate": -1.0,
}


@dataclass
class ParamStat:
    """One parameter, and what happened after the interventions it shaped."""

    triggered: int = 0
    delays: list[float] = field(default_factory=list)

    @property
    def mean_delay(self) -> float | None:
        return sum(self.delays) / len(self.delays) if self.delays else None


@dataclass
class Report:
    exercises: int = 0
    interventions: int = 0
    rates: dict[str, float] = field(default_factory=dict)
    score: float = 0.0
    params: dict[str, ParamStat] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "exercises": self.exercises,
            "interventions": self.interventions,
            "rates": self.rates,
            "tutor_score": self.score,
            "params": {name: {"triggered": stat.triggered,
                              "mean_reaction_s": stat.mean_delay}
                       for name, stat in sorted(self.params.items())},
        }


def read_log(path: Path | str) -> Iterator[dict[str, Any]]:
    """Every readable line. A broken line is skipped, never fatal."""
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("kind"):
                yield record


def _flag(record: dict[str, Any], key: str) -> bool:
    return record.get(key) is True


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def is_unnecessary(record: dict[str, Any]) -> bool:
    """The child was already moving, or moved before we finished being useful."""
    if _flag(record, "child_was_already_moving"):
        return True
    moved = _number(record.get("child_moved_after_s"))
    return moved is not None and moved < ALREADY_SOLVING_S


def score(records: Iterable[dict[str, Any]]) -> Report:
    # read_log is a generator, and this walks the records twice.
    lines = list(records)
    exercises = [r for r in lines if r.get("kind") == "exercise"]
    interventions = [r for r in lines if r.get("kind") == "intervention"]
    report = Report(exercises=len(exercises), interventions=len(interventions))

    total = len(exercises) or 1
    report.rates = {
        "autonomous_success_rate": sum(_flag(r, "autonomous_success") for r in exercises) / total,
        "first_try_rate": sum(_flag(r, "first_try") for r in exercises) / total,
        "light_hint_recovery_rate": sum(_flag(r, "light_hint_recovery") for r in exercises) / total,
        "rescue_rate": sum(_flag(r, "rescue_used") for r in exercises) / total,
        "abandonment_rate": sum(_flag(r, "abandoned") for r in exercises) / total,
        "unnecessary_intervention_rate": (
            sum(is_unnecessary(r) for r in interventions) / len(interventions)
            if interventions else 0.0
        ),
    }
    if not exercises:
        for key in ("autonomous_success_rate", "first_try_rate",
                    "light_hint_recovery_rate", "rescue_rate", "abandonment_rate"):
            report.rates[key] = 0.0
    report.score = sum(WEIGHTS[name] * value for name, value in report.rates.items())

    for record in interventions:
        effective = record.get("effective_params")
        if not isinstance(effective, dict):
            continue
        delay = _number(record.get("child_moved_after_s"))
        for name in effective:
            stat = report.params.setdefault(name, ParamStat())
            stat.triggered += 1
            if delay is not None:
                stat.delays.append(delay)
    return report


def render(report: Report) -> str:
    lines = [
        f"TutorScore {report.score:+.3f}"
        f"   exercises {report.exercises}   interventions {report.interventions}",
        "",
    ]
    for name in ("autonomous_success_rate", "first_try_rate",
                 "light_hint_recovery_rate", "unnecessary_intervention_rate",
                 "rescue_rate", "abandonment_rate"):
        weight = WEIGHTS[name]
        value = report.rates.get(name, 0.0)
        lines.append(f"  {name:<32} {value:6.3f}  x {weight:+.1f} = "
                     f"{weight * value:+.3f}")
    lines.append("")
    lines.append(f"  {'parameter':<32} {'triggered':>9}  {'mean reaction':>13}")
    if not report.params:
        lines.append("  no interventions logged")
    for name, stat in sorted(report.params.items()):
        delay = stat.mean_delay
        shown = f"{delay:.2f} s" if delay is not None else "no movement"
        lines.append(f"  {name:<32} {stat.triggered:>9}  {shown:>13}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score a tutor log. Read only.")
    parser.add_argument("log", nargs="?", default=str(DEFAULT_LOG),
                        help="path to tutor_log.jsonl")
    parser.add_argument("--json", action="store_true",
                        help="print the report as JSON instead of a table")
    args = parser.parse_args(argv)

    path = Path(args.log)
    if not path.exists():
        print(f"no log at {path}", file=sys.stderr)
        return 1
    report = score(read_log(path))
    if args.json:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
