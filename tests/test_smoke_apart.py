"""Fingers held clearly apart must read as those fingers apart: the smoke case, make doctor, and the critic's notice."""
import sys
from pathlib import Path

from loop import smoke, synth
from loop.critic import smoke_requirements
from tenfold import doctor

REPO = Path(__file__).resolve().parents[1]
V0 = REPO / "tests" / "rules_v0.py"
UNKNOWN_EVERYWHERE = "from classifier.schema import GestureState\n\ndef classify(window):\n    return GestureState.unknown()\n"


def test_apart_case_is_the_rules_test_window():
    assert "apart_7x8" in synth.smoke_cases() and len(synth.smoke_cases()) == 7


def test_v0_reads_the_apart_case_and_passes_every_smoke_case():
    assert smoke.run(V0) == []


def test_refusing_the_apart_case_is_an_expectation_failure(tmp_path):
    rules = tmp_path / "rules.py"
    rules.write_text(UNKNOWN_EVERYWHERE)
    fails = smoke.run(rules)
    apart = [f for f in fails if f.startswith("smoke apart_7x8:")]
    assert len(apart) == 1 and apart[0].startswith(smoke.EXPECTATION_PREFIXES)
    assert "got method 'unknown'" in apart[0] and "| fix:" in apart[0]
    assert any(f.startswith("smoke five_valid_contact:") and not f.startswith(smoke.EXPECTATION_PREFIXES) for f in fails)


def test_doctor_passes_warns_on_a_pose_and_fails_on_a_crash(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "run", lambda path: [])
    assert doctor.check_rules_smoke() is True
    assert capsys.readouterr().out.startswith("PASS")
    monkeypatch.setattr(smoke, "run", lambda path: ["smoke apart_7x8: expected fingers 7 and 8 apart ... | fix: x"])
    assert doctor.check_rules_smoke() is True
    assert capsys.readouterr().out.startswith("WARN")
    monkeypatch.setattr(smoke, "run", lambda path: ["smoke apart_7x8: expected fingers 7 and 8 apart",
                                                   "smoke nan: classify() raised | cause: x | fix: y"])
    assert doctor.check_rules_smoke() is False
    assert "classify() raised" in capsys.readouterr().out


def test_critic_names_the_smoke_failures_of_the_running_rules(tmp_path):
    rules = tmp_path / "rules.py"
    rules.write_text(UNKNOWN_EVERYWHERE)
    required = smoke_requirements(REPO, rules)
    assert any(r.startswith("smoke apart_7x8: expected") for r in required)
    assert smoke_requirements(REPO, V0) == []


REFUSE_APART = """

_classify_v0 = classify


def classify(window):
    state = _classify_v0(window)
    if state.method == "6-10" and not state.contact:
        return GestureState.unknown()
    return state
"""


def test_loop_announces_the_requirement_and_commits_nothing_that_leaves_it_broken(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_guard_and_loop import commits_by_critic, make_repo, run_critic
    repo = make_repo(tmp_path)
    rules = repo / "classifier" / "rules.py"
    rules.write_text(rules.read_text() + REFUSE_APART)
    before = rules.read_text()
    p = run_critic(repo, "--skip-heldout")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "required before any patch is accepted: smoke apart_7x8: expected" in p.stdout
    assert commits_by_critic(repo) == [] and rules.read_text() == before
