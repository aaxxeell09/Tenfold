"""eval/tutor_score.py on a synthetic log with a hand computed score.

The expected value below is worked out in the docstring of the first test, so a
change in the weights fails here with an arithmetic argument rather than a
number nobody can check.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from eval.tutor_score import (
    ALREADY_SOLVING_S,
    WEIGHTS,
    is_unnecessary,
    main,
    read_log,
    render,
    score,
)

REPO = Path(__file__).resolve().parents[1]


def exercise(**fields: object) -> dict[str, object]:
    line = {"kind": "exercise", "ts": "2026-09-13T18:04:19.007Z",
            "learner_id": "9f31c0a7bd42", "exercise": "8 x 7",
            "autonomous_success": False, "first_try": False,
            "light_hint_recovery": False, "rescue_used": False,
            "scored_gesture_errors": 0, "scored_math_errors": 0,
            "unsolicited_interventions": 0, "abandoned": False,
            "duration_s": 12.91, "mode": "normal"}
    line.update(fields)
    return line


def intervention(level: int = 1, moving: bool = False,
                 moved: float | None = None,
                 params: dict[str, float] | None = None) -> dict[str, object]:
    return {"kind": "intervention", "ts": "2026-09-13T18:04:11.420Z",
            "learner_id": "9f31c0a7bd42", "exercise": "8 x 7",
            "intervention": level, "trigger_after_s": 6.84,
            "state_before": "WRONG_POSE", "child_was_already_moving": moving,
            "child_moved_after_s": moved, "correct_pose_within_5s": True,
            "next_hint_needed": False,
            "effective_params": dict(params or {}, motion_threshold=0.042),
            "learner_factors": {"pace_factor": 1.0, "help_factor": 1.0,
                                "tutor_observations": 23, "mode_factor": 1.0}}


SYNTHETIC = [
    {"kind": "decision", "ts": "2026-09-13T18:04:11.420Z", "state": "WRONG_POSE",
     "learner_id": "9f31c0a7bd42", "exercise": "8 x 7", "stable_for": 2.13,
     "intervention": 2, "reason": "wrong_pose_held", "scored_error": None,
     "mode": "normal", "params_version": "4c1d9ab0f772"},
    exercise(autonomous_success=True, first_try=True),
    exercise(autonomous_success=True, first_try=True),
    exercise(first_try=True, light_hint_recovery=True),
    exercise(light_hint_recovery=True),
    exercise(rescue_used=True),
    exercise(abandoned=True),
    intervention(moving=True, moved=1.0,
                 params={"wrong_pose_prompt": 2.0, "min_verbal_gap": 4.0}),
    intervention(moved=0.3, params={"wrong_pose_prompt": 2.0}),
    intervention(moved=1.2, params={"no_hands_voice": 3.0}),
    intervention(moved=None, params={"rescue_delay": 14.0}),
]


def write_log(path: Path, records: list[dict[str, object]]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n",
                    encoding="utf-8")
    return path


def test_the_weights_are_the_ones_in_the_contract() -> None:
    assert WEIGHTS == {"autonomous_success_rate": 4.0, "first_try_rate": 2.0,
                       "light_hint_recovery_rate": 2.0,
                       "unnecessary_intervention_rate": -2.0,
                       "rescue_rate": -2.0, "abandonment_rate": -1.0}
    assert ALREADY_SOLVING_S == 0.5


def test_tutor_score_on_the_synthetic_log() -> None:
    """Six exercises, four interventions, worked out by hand.

    autonomous 2/6, first try 3/6, light recovery 2/6, rescue 1/6, abandoned 1/6.
    Two of the four interventions are unnecessary: one on a child already moving,
    one the child answered in 0.3 s. So

        4 * 1/3 + 2 * 1/2 + 2 * 1/3 - 2 * 1/2 - 2 * 1/6 - 1 * 1/6
      = 1.333333 + 1 + 0.666667 - 1 - 0.333333 - 0.166667
      = 1.5
    """
    report = score(SYNTHETIC)
    assert report.exercises == 6
    assert report.interventions == 4
    assert report.rates["autonomous_success_rate"] == pytest.approx(1 / 3)
    assert report.rates["first_try_rate"] == pytest.approx(0.5)
    assert report.rates["light_hint_recovery_rate"] == pytest.approx(1 / 3)
    assert report.rates["unnecessary_intervention_rate"] == pytest.approx(0.5)
    assert report.rates["rescue_rate"] == pytest.approx(1 / 6)
    assert report.rates["abandonment_rate"] == pytest.approx(1 / 6)
    assert report.score == pytest.approx(1.5)


def test_the_per_parameter_table_counts_triggers_and_reaction_delays() -> None:
    report = score(SYNTHETIC)
    assert report.params["wrong_pose_prompt"].triggered == 2
    assert report.params["wrong_pose_prompt"].mean_delay == pytest.approx(0.65)
    assert report.params["min_verbal_gap"].triggered == 1
    assert report.params["min_verbal_gap"].mean_delay == pytest.approx(1.0)
    assert report.params["rescue_delay"].triggered == 1
    assert report.params["rescue_delay"].mean_delay is None
    assert report.params["motion_threshold"].triggered == 4
    assert report.params["motion_threshold"].mean_delay == pytest.approx(2.5 / 3)


def test_an_intervention_is_unnecessary_on_either_condition() -> None:
    assert is_unnecessary(intervention(moving=True, moved=None)) is True
    assert is_unnecessary(intervention(moving=False, moved=0.49)) is True
    assert is_unnecessary(intervention(moving=False, moved=0.5)) is False
    assert is_unnecessary(intervention(moving=False, moved=None)) is False


def test_decision_lines_do_not_move_the_score() -> None:
    only_decisions = [r for r in SYNTHETIC if r["kind"] == "decision"]
    report = score(only_decisions)
    assert report.exercises == 0
    assert report.score == 0.0


def test_an_empty_log_scores_zero_and_never_divides_by_zero() -> None:
    report = score([])
    assert report.score == 0.0
    assert report.rates["unnecessary_intervention_rate"] == 0.0
    assert "no interventions logged" in render(report)


def test_a_broken_line_is_skipped_not_fatal(tmp_path: Path) -> None:
    path = tmp_path / "tutor_log.jsonl"
    path.write_text("\n".join([
        json.dumps(exercise(first_try=True)),
        "{not json at all",
        "",
        json.dumps({"no": "kind"}),
        json.dumps(exercise()),
    ]) + "\n", encoding="utf-8")
    report = score(read_log(path))
    assert report.exercises == 2
    assert report.rates["first_try_rate"] == pytest.approx(0.5)


def test_the_table_names_every_rate_and_every_parameter() -> None:
    text = render(score(SYNTHETIC))
    assert "TutorScore +1.500" in text
    for name in WEIGHTS:
        assert name in text
    assert "wrong_pose_prompt" in text
    assert "motion_threshold" in text


def test_the_command_line_prints_json_and_reports_a_missing_file(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_log(tmp_path / "tutor_log.jsonl", SYNTHETIC)
    assert main([str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tutor_score"] == pytest.approx(1.5)
    assert payload["params"]["rescue_delay"] == {"triggered": 1,
                                                 "mean_reaction_s": None}
    assert main([str(tmp_path / "absent.jsonl")]) == 1


def test_it_reads_a_real_tutor_log_end_to_end(tmp_path: Path) -> None:
    """A log the tutor itself wrote, scored without touching the tutor."""
    from app.tutor import Observation, Tutor, jsonl_sink, load_lines, load_params
    from classifier.schema import GestureState

    target = tmp_path / "tutor_log.jsonl"
    moment = {"t": 0.0}
    tutor = Tutor(load_params(), load_lines(), clock=lambda: moment["t"],
                  log=jsonl_sink(target), learner_id="9f31c0a7bd42")
    tutor.new_exercise(8, 7, node="n", now=0.0)
    good = GestureState(method="6-10", left=8, right=7, contact=True, confidence=0.9)
    for _ in range(30):
        moment["t"] += 1 / 15
        tutor.observe(Observation(gesture=good, hands_seen=2,
                                  fingers=[{"hand": "left", "number": 6,
                                            "x": 0.3, "y": 0.5}]), moment["t"])
    tutor.answer(56, correct=True, now=moment["t"])
    tutor.end_exercise(reason="answered", now=moment["t"])

    report = score(read_log(target))
    assert report.exercises == 1
    assert report.rates["first_try_rate"] == 1.0
    assert report.score == pytest.approx(4.0 + 2.0)


def test_it_imports_without_the_app_or_a_camera() -> None:
    code = ("import sys; import eval.tutor_score as t; "
            "print(any(name.startswith('app.') or name in ('cv2', 'mediapipe') "
            "for name in sys.modules))")
    done = subprocess.run([sys.executable, "-c", code], cwd=REPO,
                          capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "False"
