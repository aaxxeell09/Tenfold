"""Compare two fixed versions of classifier/rules.py on the retrospective validation set. Local, no W&B.

    python eval/retro_validate.py                        # V0 (first commit of rules.py) vs data/BEST_VERSION, else the last rules.py commit
    python eval/retro_validate.py --a fdea731 --b 3adc450

Validation rétrospective, participants non identifiés. The critic had already seen these windows when it wrote the
versions compared, so this measures holds kept out from now on, not generalisation to new people. Both versions are
scored twice, identically: with the frozen eval/scorers.py and with eval/strict.py, where output=None is a failure.
Nothing is selected from the results: every window of the validation file is scored, and both versions are commits
fixed before the run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from eval import scorers, strict  # noqa: E402
from eval.run_eval import build_rows, load_samples, predict  # noqa: E402
from eval.split import DEFAULT_NAME, LABEL  # noqa: E402

RULES = "classifier/rules.py"


def git(*args: str) -> bytes:
    p = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True)
    if p.returncode != 0:
        raise SystemExit(f"retro_validate: git {' '.join(args)} failed: {p.stderr.decode().strip()}")
    return p.stdout


def version(rev: str) -> dict:
    commit = git("rev-parse", f"{rev}^{{commit}}").decode().strip()
    content = git("show", f"{commit}:{RULES}")
    return {"rev": rev, "commit": commit, "blob": git("rev-parse", f"{commit}:{RULES}").decode().strip(),
            "sha256": hashlib.sha256(content).hexdigest(), "subject": git("log", "-1", "--format=%s", commit).decode().strip(),
            "content": content}


def default_revisions() -> tuple[str, str]:
    history = git("log", "--format=%H", "--", RULES).decode().split()
    if not history:
        raise SystemExit(f"retro_validate: no commit touches {RULES}")
    best = REPO / "data" / "BEST_VERSION"
    running = best.read_text().strip() if best.exists() and best.read_text().strip() else history[0]
    return history[-1], running


def evaluate(v: dict, validation: Path) -> tuple[list[dict], dict, dict]:
    with tempfile.TemporaryDirectory(prefix="tenfold-retro-") as tmp:
        rules = Path(tmp) / "rules.py"
        rules.write_bytes(v["content"])
        rows = build_rows(load_samples(validation), predict(rules, validation))
    return rows, scorers.aggregate(rows), strict.aggregate_with(rows, strict.score_row_strict)


def fmt(x) -> str:
    return "n/a" if x is None else f"{x:.3f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=f"{LABEL}: two fixed rules versions, same holds")
    ap.add_argument("--name", default=DEFAULT_NAME)
    ap.add_argument("--validation", help="default ../tenfold-validation/<name>/validation.jsonl")
    ap.add_argument("--a", help="baseline revision (default: the first commit of classifier/rules.py, V0)")
    ap.add_argument("--b", help="compared revision (default: data/BEST_VERSION, else the last commit of rules.py)")
    ap.add_argument("--out", help="report path (default next to the validation file)")
    args = ap.parse_args()

    validation = Path(args.validation) if args.validation else REPO.parent / "tenfold-validation" / args.name / "validation.jsonl"
    if not validation.exists():
        raise SystemExit(f"retro_validate: {validation} not found; run eval/split.py first")
    manifest_path = validation.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    val_sha = hashlib.sha256(validation.read_bytes()).hexdigest()
    expected_sha = manifest.get("files", {}).get("validation", {}).get("sha256")
    if expected_sha and expected_sha != val_sha:
        raise SystemExit(f"retro_validate: {validation} does not match its manifest (sha256 {val_sha[:12]} vs {expected_sha[:12]})")

    rev_a, rev_b = default_revisions()
    a, b = version(args.a or rev_a), version(args.b or rev_b)
    rows_a, frozen_a, strict_a = evaluate(a, validation)
    rows_b, frozen_b, strict_b = evaluate(b, validation)

    missing = {"a": sum(1 for r in rows_a if r.get("output") is None), "b": sum(1 for r in rows_b if r.get("output") is None)}
    negatives_missing = {k: sum(1 for r in rows if r.get("output") is None and r["target"].get("method") == "unknown")
                         for k, rows in (("a", rows_a), ("b", rows_b))}
    paired_strict = strict.paired_bootstrap(strict.hold_scores(rows_a, strict.score_row_strict),
                                            strict.hold_scores(rows_b, strict.score_row_strict))
    paired_frozen = strict.paired_bootstrap(strict.hold_scores(rows_a, scorers.score_row),
                                            strict.hold_scores(rows_b, scorers.score_row))
    holds_per_class: dict[str, set] = defaultdict(set)
    windows_per_class: dict[str, int] = defaultdict(int)
    for r in rows_a:
        c = scorers.class_of(r["target"], r.get("kind", "positive"))
        holds_per_class[c].add(r.get("hold_id") or r["id"])
        windows_per_class[c] += 1
    per_class = {c: {"holds": len(holds_per_class[c]), "windows": windows_per_class[c],
                     "a_strict": strict_a["per_class"].get(c), "b_strict": strict_b["per_class"].get(c),
                     "a_frozen": frozen_a["per_class"].get(c), "b_frozen": frozen_b["per_class"].get(c)}
                 for c in sorted(holds_per_class)}

    strip = lambda v: {k: x for k, x in v.items() if k != "content"}  # noqa: E731
    report = {
        "label": LABEL,
        "caveat": "the critic saw these windows before the split; participants are not identified; not a test on other people",
        "validation": {"path": str(validation), "sha256": val_sha, "manifest": manifest.get("name"),
                       "source_sha256": manifest.get("source", {}).get("sha256"),
                       "windows": strict_a["n_samples"], "holds": strict_a["n_holds"]},
        "a": {**strip(a), "role": "baseline (V0 unless --a)"}, "b": {**strip(b), "role": "compared (running version unless --b)"},
        "scoring": {"frozen": "eval/scorers.py as committed (output=None counts as unknown)",
                    "strict": "eval/strict.py (output=None fails every metric that applies), same rows for a and b"},
        "missing_predictions": missing, "missing_predictions_on_unknown_targets": negatives_missing,
        "metrics": {"a_frozen": frozen_a, "a_strict": strict_a, "b_frozen": frozen_b, "b_strict": strict_b},
        "paired_exact_match_per_hold": {"strict": paired_strict, "frozen": paired_frozen},
        "per_class": per_class,
    }
    out = Path(args.out) if args.out else validation.parent / f"report-{a['commit'][:8]}-{b['commit'][:8]}.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    os.chmod(out, 0o600)

    print(LABEL)
    print(f"{validation} ({strict_a['n_samples']} windows, {strict_a['n_holds']} holds), manifest {manifest.get('name')}, "
          f"sha256 {val_sha[:12]}{' matches the manifest' if expected_sha else ' (no manifest check)'}")
    for key, v in (("A", a), ("B", b)):
        print(f"{key} {v['commit'][:10]} blob {v['blob'][:10]} sha256 {v['sha256'][:12]}  {v['subject'][:70]}")
    print(f"{'':26s}{'A frozen':>10s}{'A strict':>10s}{'B frozen':>10s}{'B strict':>10s}")
    for m in scorers.METRICS:
        print(f"{m:26s}" + "".join(f"{fmt(x[m]):>10s}" for x in (frozen_a, strict_a, frozen_b, strict_b)))
    for label, x in (("exact_match 95% CI A", strict_a), ("exact_match 95% CI B", strict_b)):
        ci = x["exact_match_ci95"]
        print(f"{label:26s}  strict [{fmt(ci[0])}, {fmt(ci[1])}]" if ci else f"{label:26s}  n/a")
    print(f"missing predictions: A {missing['a']}, B {missing['b']} (on unknown targets: A {negatives_missing['a']}, B {negatives_missing['b']})")
    ps = paired_strict
    if ps["mean_difference"] is not None:
        print(f"B - A exact_match per hold (strict): {ps['mean_difference']:+.3f}, 95% CI [{ps['ci95'][0]:+.3f}, "
              f"{ps['ci95'][1]:+.3f}], {ps['better']} holds better, {ps['worse']} worse, {ps['unchanged']} unchanged")
    print(f"{'class':14s}{'holds':>6s}{'windows':>8s}{'A strict':>10s}{'B strict':>10s}")
    for c, row in per_class.items():
        print(f"{c:14s}{row['holds']:6d}{row['windows']:8d}{fmt(row['a_strict']):>10s}{fmt(row['b_strict']):>10s}")
    print(f"report {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
