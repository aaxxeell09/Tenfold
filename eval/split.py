"""Retrospective validation split: about 20 % of whole holds set aside, the rest for the next iterations.

    python eval/split.py                         # seed 42, name retro-seed42, default locations below
    python eval/split.py --check                 # recompute and compare with the files on disk, write nothing

Validation rétrospective, participants non identifiés. The critic had already read every one of these windows
when the split was made, so the validation side is a retrospective check on holds kept out from now on: never
data the loop has not seen, never a test on new people. data/samples.jsonl records session and person labels,
but they do not identify participants reliably, so the split is by hold, not by person.

Outputs
    data/splits/<name>/train.jsonl                   about 80 % of holds (gitignored); set TENFOLD_TRAIN_SAMPLES to it
    data/splits/<name>/source.json                   what the train side was cut from, no validation ids
    ../tenfold-validation/<name>/validation.jsonl    about 20 % of holds, outside the repo and the critic worktree
    ../tenfold-validation/<name>/manifest.json       seed, source hash, groups on each side, counts per class

Rules, fixed before any score was computed
- A group is session::hold_id. Every window of a hold stays on one side.
- Byte-identical windows glue their holds into one unit. A unit spanning several holds, or a hold carrying more
  than one class, stays in train (on the first dataset: windows where no hand was detected at all).
- Strata are classes (finger pair, near-contact pair or negative kind). Validation holds per stratum follow the
  overall fraction by largest remainder, ties broken by the seed; holds within a stratum are drawn by the seed.
- Lines are copied byte for byte in source order. The source file is only read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from eval.scorers import class_of  # noqa: E402

LABEL = "validation rétrospective, participants non identifiés"
DEFAULT_NAME = "retro-seed42"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def group_key(row: dict) -> str:
    """session::hold_id. data/capture.py already starts hold_id with the session; joining it again means a hold_id
    reused by another session can never merge two holds."""
    return f"{row.get('session', '')}::{row.get('hold_id') or row['id']}"


def class_name(row: dict) -> str:
    return class_of(row["label"], row.get("kind", "positive"))


def window_hash(row: dict) -> str:
    return sha256(json.dumps(row["window"], sort_keys=True, separators=(",", ":")).encode())


def read_source(path: Path) -> tuple[bytes, list[str], list[dict]]:
    raw = path.read_bytes()
    lines = [line for line in raw.decode("utf-8").splitlines() if line.strip()]
    rows = [json.loads(line) for line in lines]
    repeated = sorted(i for i, c in Counter(r["id"] for r in rows).items() if c > 1)
    if repeated:
        raise SystemExit(f"split: {len(repeated)} ids appear more than once (first: {repeated[:3]}), so no split "
                         "could guarantee that an example sits on one side only")
    return raw, lines, rows


def plan(rows: list[dict], seed: int = 42, fraction: float = 0.2) -> dict:
    groups: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        groups[group_key(r)].append(i)
    classes = {g: sorted({class_name(rows[i]) for i in idx}) for g, idx in groups.items()}
    mixed = sorted(g for g, c in classes.items() if len(c) > 1)

    parent = {g: g for g in groups}

    def find(g: str) -> str:
        while parent[g] != g:
            parent[g] = parent[parent[g]]
            g = parent[g]
        return g

    by_window: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        by_window[window_hash(r)].append(i)
    duplicates = []
    for h in sorted(by_window):
        idx = by_window[h]
        if len(idx) < 2:
            continue
        holds = sorted({group_key(rows[i]) for i in idx})
        duplicates.append({"window_sha256": h, "windows": len(idx), "holds": len(holds),
                           "classes": sorted({class_name(rows[i]) for i in idx})})
        for g in holds[1:]:
            a, b = find(holds[0]), find(g)
            if a != b:
                parent[max(a, b)] = min(a, b)

    units: dict[str, list[str]] = defaultdict(list)
    for g in sorted(groups):
        units[find(g)].append(g)
    pinned = sorted(u for u, gs in units.items() if len(gs) > 1 or any(g in mixed for g in gs))
    pool = {u: units[u][0] for u in sorted(units) if u not in pinned}  # every unit left is a single hold
    strata: dict[str, list[str]] = defaultdict(list)
    for u, g in pool.items():
        strata[classes[g][0]].append(u)

    target = round(fraction * len(groups))
    share = min(1.0, target / len(pool)) if pool else 0.0
    quota = {s: share * len(us) for s, us in strata.items()}
    alloc = {s: int(q + 1e-9) for s, q in quota.items()}
    order = sorted(strata)
    rng = random.Random(seed)
    tiebreak = {s: rng.random() for s in order}
    left = target - sum(alloc.values())
    for s in sorted(order, key=lambda s: (-(quota[s] - alloc[s]), tiebreak[s])):
        if left <= 0:
            break
        if alloc[s] < len(strata[s]):
            alloc[s] += 1
            left -= 1

    validation: set[str] = set()
    for s in order:
        drawn = sorted(strata[s])
        random.Random(f"{seed}:{s}").shuffle(drawn)
        validation.update(drawn[:alloc[s]])
    side = {g: ("validation" if g in validation else "train") for g in groups}

    holds_per_class: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        holds_per_class[class_name(r)].add(group_key(r))

    def counts(which: str) -> dict:
        gs = [g for g in sorted(groups) if side[g] == which]
        idx = [i for g in gs for i in groups[g]]
        hold_classes: dict[str, set[str]] = defaultdict(set)
        for i in idx:
            hold_classes[class_name(rows[i])].add(group_key(rows[i]))
        windows = Counter(class_name(rows[i]) for i in idx)
        return {
            "holds": len(gs), "windows": len(idx),
            "per_class": {c: {"holds": len(hold_classes[c]), "windows": windows[c]} for c in sorted(windows)},
            "per_kind": dict(sorted(Counter(str(rows[i].get("kind", "positive")) for i in idx).items())),
            "per_angle": dict(sorted(Counter(str(rows[i].get("angle")) for i in idx).items())),
            "per_distance": dict(sorted(Counter(str(rows[i].get("distance")) for i in idx).items())),
        }

    train, val = counts("train"), counts("validation")
    all_classes = sorted(holds_per_class)
    window_sides: dict[str, set[str]] = defaultdict(set)
    for i, r in enumerate(rows):
        window_sides[window_hash(r)].add(side[group_key(r)])
    return {
        "side": side, "groups": dict(groups), "target_validation_holds": target,
        "train": train, "validation": val, "mixed_class_holds": mixed, "duplicate_windows": duplicates,
        "kept_in_train": [{"holds": units[u], "reason": ("byte-identical windows shared by several holds"
                                                         if len(units[u]) > 1 else "the hold carries more than one class")}
                          for u in pinned],
        "rare_classes": [c for c in all_classes if len(holds_per_class[c]) < 2],
        "classes_only_in_train": [c for c in all_classes if c in train["per_class"] and c not in val["per_class"]],
        "classes_only_in_validation": [c for c in all_classes if c in val["per_class"] and c not in train["per_class"]],
        "windows_on_both_sides": sum(1 for s in window_sides.values() if len(s) > 1),
    }


def split_bytes(p: dict, lines: list[str], rows: list[dict]) -> tuple[bytes, bytes]:
    train = [line for line, r in zip(lines, rows) if p["side"][group_key(r)] == "train"]
    val = [line for line, r in zip(lines, rows) if p["side"][group_key(r)] == "validation"]
    return ("\n".join(train) + "\n").encode(), ("\n".join(val) + "\n").encode()


def shown(path: Path) -> str:
    return str(path.relative_to(REPO)) if REPO in path.parents else str(path)


def build_manifest(p: dict, rows: list[dict], raw: bytes, source: Path, train_file: Path, val_file: Path,
                   train_bytes: bytes, val_bytes: bytes, name: str, seed: int, fraction: float) -> dict:
    sessions = sorted({str(r.get("session")) for r in rows})
    persons = sorted({str(r.get("person")) for r in rows})
    return {
        "label": LABEL,
        "name": name, "seed": seed, "fraction": fraction,
        "method": ("whole holds (session::hold_id); byte-identical windows glue their holds and a unit spanning "
                   "several holds stays in train; strata = classes; validation holds per stratum by largest "
                   "remainder, ties and draws from the seed"),
        "script_sha256": sha256(Path(__file__).read_bytes()),
        "source": {"path": shown(source), "sha256": sha256(raw), "windows": len(rows), "holds": len(p["groups"])},
        "files": {"train": {"path": shown(train_file), "sha256": sha256(train_bytes), "windows": p["train"]["windows"]},
                  "validation": {"path": shown(val_file), "sha256": sha256(val_bytes), "windows": p["validation"]["windows"]}},
        "proportions": {"validation_holds": p["validation"]["holds"] / len(p["groups"]),
                        "validation_windows": p["validation"]["windows"] / len(rows)},
        "target_validation_holds": p["target_validation_holds"],
        "counts": {"train": p["train"], "validation": p["validation"]},
        "assignment": {side: sorted(g for g, s in p["side"].items() if s == side) for side in ("train", "validation")},
        "rare_classes": p["rare_classes"],
        "classes_only_in_train": p["classes_only_in_train"],
        "classes_only_in_validation": p["classes_only_in_validation"],
        "duplicate_windows": p["duplicate_windows"],
        "kept_in_train": p["kept_in_train"],
        "mixed_class_holds": p["mixed_class_holds"],
        "windows_on_both_sides": p["windows_on_both_sides"],
        "limits": [
            "The critic read every window of this dataset before the split: the validation side is a retrospective "
            "check on holds kept out from now on, not unseen data.",
            f"Recorded sessions {sessions} and person labels {persons} are not treated as identities: participants "
            "are not identified, so nothing here measures other people.",
            "Train and validation holds come from the same capture: same hands, light and camera blocks, so they "
            "are strongly correlated and the validation score is an optimistic reference.",
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=f"{LABEL}: deterministic split of whole holds")
    ap.add_argument("--source", default=str(REPO / "data" / "samples.jsonl"))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--fraction", type=float, default=0.2)
    ap.add_argument("--name", default=DEFAULT_NAME)
    ap.add_argument("--train-dir", help="default data/splits/<name>")
    ap.add_argument("--validation-dir", help="default ../tenfold-validation/<name>, must be outside the repo")
    ap.add_argument("--check", action="store_true", help="recompute and compare with the files on disk")
    a = ap.parse_args()

    source = Path(a.source).resolve()
    train_dir = Path(a.train_dir).resolve() if a.train_dir else REPO / "data" / "splits" / a.name
    val_dir = Path(a.validation_dir).resolve() if a.validation_dir else REPO.parent / "tenfold-validation" / a.name
    if val_dir == REPO or REPO in val_dir.parents:
        raise SystemExit(f"split: {val_dir} is inside the repository; the validation side must live outside it")
    train_file, val_file, manifest_file = train_dir / "train.jsonl", val_dir / "validation.jsonl", val_dir / "manifest.json"

    raw, lines, rows = read_source(source)
    p = plan(rows, a.seed, a.fraction)
    train_bytes, val_bytes = split_bytes(p, lines, rows)
    manifest = build_manifest(p, rows, raw, source, train_file, val_file, train_bytes, val_bytes, a.name, a.seed, a.fraction)
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    source_meta = (json.dumps({"label": LABEL, "name": a.name, "seed": a.seed, "fraction": a.fraction,
                               "source_path": shown(source), "source_sha256": sha256(raw),
                               "train_sha256": sha256(train_bytes), "train_windows": p["train"]["windows"],
                               "train_holds": p["train"]["holds"]}, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()

    if a.check:
        expected = {train_file: train_bytes, train_dir / "source.json": source_meta, val_file: val_bytes,
                    manifest_file: manifest_bytes}
        bad = [str(f) for f, b in expected.items() if not f.exists() or f.read_bytes() != b]
        print(f"{LABEL}: " + ("the files on disk match a fresh split" if not bad else f"MISMATCH in {bad}"))
        return 1 if bad else 0

    if manifest_file.exists():
        old = json.loads(manifest_file.read_text())
        if (old.get("source", {}).get("sha256"), old.get("seed"), old.get("fraction")) != (sha256(raw), a.seed, a.fraction):
            raise SystemExit(f"split: {manifest_file} was made from another source or seed; rewriting it would move "
                             "holds between the two sides. Use a new --name.")

    train_dir.mkdir(parents=True, exist_ok=True)
    train_file.write_bytes(train_bytes)
    (train_dir / "source.json").write_bytes(source_meta)
    val_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(val_dir, 0o700)
    for f, b in ((val_file, val_bytes), (manifest_file, manifest_bytes)):
        f.write_bytes(b)
        os.chmod(f, 0o600)

    t, v = p["train"], p["validation"]
    print(f"{LABEL}\nsource {shown(source)} sha256 {sha256(raw)[:16]}: {len(rows)} windows, {len(p['groups'])} holds, seed {a.seed}")
    print(f"train      {t['holds']:4d} holds ({t['holds'] / len(p['groups']):.1%})  {t['windows']:4d} windows ({t['windows'] / len(rows):.1%})  -> {shown(train_file)}")
    print(f"validation {v['holds']:4d} holds ({v['holds'] / len(p['groups']):.1%})  {v['windows']:4d} windows ({v['windows'] / len(rows):.1%})  -> {val_file}")
    print(f"kept in train as one unit: {sum(len(k['holds']) for k in p['kept_in_train'])} holds "
          f"({len(p['duplicate_windows'])} groups of byte-identical windows); windows on both sides: {p['windows_on_both_sides']}")
    print(f"classes too rare for both sides: {p['rare_classes'] or 'none'}")
    print(f"classes only in train: {p['classes_only_in_train'] or 'none'}")
    print(f"classes only in validation: {p['classes_only_in_validation'] or 'none'}")
    print(f"manifest {manifest_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
