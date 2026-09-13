# Area 4 audit: loop/, data/, eval/, scripts/, tests/, Makefile, requirements, root

Read only audit, 2026-09-13. Nothing was modified except this file.
Reference: SPEC.md (source of truth), CLAUDE.md section "Ownership" and "Frozen".

Ownership markers used below:
- **OWNER: Ilan, do not edit** for `loop/`, `eval/`, `Makefile`, `tenfold/doctor.py`, `tenfold/env.py`.
- **FROZEN** for `data/samples.jsonl` and `eval/scorers.py` (never edit without both owners).
- Everything else in this area (`data/capture.py`, `scripts/`, `tests/`, `requirements.txt`, root files) is ours.

Evidence base: full suite run (`python3 -m pytest -q`) **284 passed in 375 s**, exit 0, on this machine,
with another agent's pytest running at the same time.

---

## BLOCKER

### B1. `make demo` and `make run` call a file that has never existed
`Makefile:15-19`
```
run:    $(PY) app/main.py
demo:   $(PY) app/main.py --demo
```
`app/main.py` does not exist and `git log --all -- app/main.py` is empty: it was never committed. The live app
is `app/server.py`, which already has `--demo`, `--mock`, `--no-open`, `--port`, `--camera`
(`app/server.py:792-802`). So `make demo` fails instantly with "can't open file app/main.py".
This is done item 14 of SPEC.md section 15 ("`make doctor` then `make demo` on a clean machine in under 5
minutes") and it is the second line of the README quick start. Fix is one line per target
(`$(PY) app/server.py --demo --no-open`, `$(PY) app/server.py` for run).
**OWNER: Ilan, do not edit** (Makefile). The missing `app/main.py` itself is Axel's side.

### B2. `make dashboard` points at a dashboard that does not exist
`Makefile:45-46` runs `marimo run dashboard/loop_dashboard.py`. There is no `dashboard/` directory in the
repo at all and it was never committed. SPEC.md section 10 and the demo script (section 14, 1:30 to 2:10)
both hang on that notebook. `marimo` is still pulled in by `requirements.txt:10`.
Either the notebook is missing work or the target is dead. Someone has to decide before the demo.
**OWNER: Ilan, do not edit** (Makefile); `dashboard/` is Axel's deliverable.

### B3. The committed dashboard snapshot has no held-out curve, no blind curve, no kNN line, no ceiling
`data/snapshot.json` (committed, and per `docs/loop.md` the only file the dashboard reads):
```
informed: v0 heldout=None, v1 heldout=None      train 0.458 -> 0.487
blind: []        knn_heldout: null        detection_ceiling: null
```
Done items 10 ("the held-out score really goes up") and 13 ("the blind critic does worse, the headline
chart") have no data in the repo as committed, and the four extra lines SPEC.md section 10 asks for
(blind, kNN, detection ceiling, bootstrap bands) are all absent. Regenerating needs, in order:
a held-out capture at `../tenfold-heldout/test.jsonl`, `make eval-heldout`, `make knn`, a blind arm run,
then `make snapshot`. Nothing here is broken code, but as it stands the demo chart is one grey train line.

---

## BUG

### G1. The loop tests copy untracked local files into their fixture repo, so `make eval` breaks `make test`
`tests/test_guard_and_loop.py:21-23`
```python
for rel in ["classifier", "eval", "loop", "lesson", "tests", "tenfold"]:
    shutil.copytree(REPO / rel, dst / rel, ignore=shutil.ignore_patterns("__pycache__", "results", "transcripts"))
```
The ignore list covers `__pycache__`, `eval/results` and `loop/transcripts`, but not the other generated,
gitignored files that live in those trees: `eval/last_train_report.json` (written by every `eval/run_eval.py
--split train`), `loop/blind-rules.py` (written by every blind arm run), `loop/watch-state.json`,
`loop/nightly*.log`. They are copied into the throwaway repo and become fixture state.
Two tests then fail depending on what the developer ran last:
- `tests/test_guard_and_loop.py:233` asserts `not (repo / "eval" / "last_train_report.json").exists()`
  after a blind run. Run `make eval` once and that file exists in `REPO/eval`, gets copied, and the
  assertion fails even though the blind arm behaved correctly.
- `tests/test_guard_and_loop.py:231` asserts `CONTACT_THRESHOLD = 0.30` in `loop/blind-rules.py`. A stale
  `loop/blind-rules.py` from an earlier blind run is copied in, `loop/critic.py:~160` keeps it
  (`if a.blind and not self.rules_src.exists()` only seeds it when missing), and the patch starts from the
  stale value, so the assertion fails.
Right now both files happen to be absent, which is why the suite is green. Fix: add them to the ignore
patterns, or copy only tracked files (`git ls-files`).

### G2. `data/capture.py` spins forever when the camera stops delivering, with no way to quit
`data/capture.py:404-407`
```python
while True:
    ok, frame = camera.read()
    if not ok:
        continue
```
In `_wait_for_block` there is no timeout and no `cv2.waitKey` on that path, so a camera that opens and then
stops returning frames (unplugged, grabbed by Zoom, a virtual camera that dies) leaves the capture in a
silent busy loop with no window update and no way to press `q`. Only Ctrl-C gets out.
The same pattern in `_run_step:435-437` is bounded by the step clock, so it only costs that step.
Fix: count consecutive failed reads, print a message and abort after a second or two.

### G3. `Ctrl-C` during a capture ends in a traceback
`data/capture.py:351-395, 510-549`. There is no `KeyboardInterrupt` handler anywhere. The `finally` blocks do
close the detector, the camera and the writer, so no data is lost, but the operator gets a traceback in the
middle of a 40 minute session, which reads like a crash. `--dry-run` is worse: `run_dry:341-348` sleeps
through the whole schedule (46 steps x 4.5 s x 6 blocks, about 21 minutes) and Ctrl-C is the only exit.
Fix: catch `KeyboardInterrupt` in `main()` and return 130 with one line.

### G4. The detection ceiling the dashboard draws is never produced by anything
`loop/snapshot.py:68` reads `data/detection_ceiling.json`; `docs/interfaces.md:52,82,93` documents it as a
committed file and as a line on the headline chart. No code in the repo ever writes it.
`scripts/gonogo.py:90-113` measures exactly those numbers (two hand rate, swap rate, lost while close) and
only prints them to stdout. So `detection_ceiling` is permanently `null` in the snapshot and the
"MediaPipe ceiling" line of SPEC.md section 6 cannot appear.
Fix: have `scripts/gonogo.py` also write the JSON, or drop the key from the snapshot and the doc.
`loop/snapshot.py` is **OWNER: Ilan, do not edit**; `scripts/gonogo.py` is ours.

### G5. `eval/baseline_knn.py` reads the held-out set outside `eval/run_eval.py`
`eval/baseline_knn.py:31,57-58` imports `samples_path` and `load_samples` from `run_eval` and then opens
`../tenfold-heldout/test.jsonl` itself in its own process (`--split heldout`, the default, and the only mode
`Makefile:30-31` uses). CLAUDE.md says: "Never read samples with split == "test" outside eval/run_eval.py.".
There is no leak to the critic (this runs from the main repo, the result goes to `eval/results/knn-heldout.json`
which is gitignored, and the critic worktree never receives it), but it is the rule the whole test blindness
story rests on, and a judge reading the code will find the exception.
Fix: route the kNN line through `run_eval.py` (a `--model knn` flag), or write the exemption into CLAUDE.md.
**OWNER: Ilan, do not edit.**

### G6. `make doctor` fails on exactly the machine done item 14 describes
`tenfold/doctor.py:82-104`. If the `claude` CLI is on PATH but not authenticated, the "claude auth" line is a
**FAIL**, so `make doctor` exits 1 and prints "DOCTOR FAILED". Done item 14 of SPEC.md section 15 asks for
`make doctor` then `make demo` to work "on a clean machine with no key and no webcam". A laptop with Claude
Code installed but no Tenfold `.env` is the normal judge machine. Everything else optional in doctor is a
WARN (`weave`, `mediapipe`, `cv2`, camera, both W&B keys, held-out data, BEST_VERSION); only python 3.11,
numpy, httpx and the rules smoke test are hard requirements, which is right.
Fix: WARN when the CLI cannot authenticate, FAIL only when the loop is actually being run.
**OWNER: Ilan, do not edit.**

### G7. `loop/nightly.sh` and `make watch` are macOS only and fail silently elsewhere
`loop/nightly.sh:15,18` and `Makefile:43` prefix the run with `caffeinate -i`. On Linux `caffeinate` does not
exist, so bash prints "command not found" into `loop/nightly.out` and `loop/nightly-blind.out`, both arms exit
127, the only thing in `loop/nightly.log` is "nightly done informed=127 blind=127", and the overnight run does
nothing at all. If the runner clone (`../tenfold-run`, docs/loop.md) is ever a Linux box or a container, the
20 iteration night is lost with no visible error.
Fix: `command -v caffeinate >/dev/null && CAF="caffeinate -i" || CAF=""`.
**OWNER: Ilan, do not edit.**

### G8. The early stop counter only counts metric gate rejections
`loop/critic.py:423,497,512,521`. `no_improve` is incremented only in the metric gate branch (line 497). An
iteration that dies in the guard (two rejected attempts), in a claude timeout, or in a failed train eval,
leaves `no_improve` untouched, so `if not a.no_early_stop and no_improve >= 3` never fires and the arm keeps
paying for iterations that produce nothing until `--max-cost` stops it. The nightly passes `--no-early-stop`
so the night is unaffected; a daytime `make loop N=10` with a broken agent is not.
**OWNER: Ilan, do not edit.**

---

## CLEANUP

### C1. A whole test file silently skips itself on a machine set up by `make install`
`tests/test_voice.py:22` (`pytest.importorskip("playwright")`) and `:94` (`pytest.skip` when no chromium).
`playwright` is not in `requirements.txt`, so after `make install` those 15 tests never run and `make test`
is still green. They are also 4 minutes of the 6 minute suite here, where playwright happens to be installed.
Same shape, less serious, in `tests/test_levels_js.py:22-25`: skipped when `node` is missing.
Fix: add playwright to requirements (or a `test-requirements.txt`) so the skip means something, and say in
the README that node is needed for the course rules test.

### C2. Hardcoded browser path
`tests/test_voice.py:26-29`
```python
CHROME_CANDIDATES = (Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome"),)
```
One machine specific, version pinned path. It degrades gracefully (falls back to playwright's own browser,
then skips), so no impact beyond confusion.

### C3. A test caps how good the critic is allowed to get
`tests/test_features_rules.py:87-92`
```python
v0 = _score(rules.classify, rows)["exact_match"]
assert v0 < 0.85, v0
```
`rules.classify` here is the live `classifier/rules.py`, the file the critic rewrites, so this asserts an
upper bound on the classifier's quality on the hard synthetic set. Measured now: 0.642 against the 0.85
ceiling, with `CONTACT_THRESHOLD = 0.2975` after critic v1, so there is room, but a good enough night turns
`make test` red for the wrong reason. `test_rules_v0_contact` and `test_rules_v0_near_contact_is_not_contact`
(`:40-48`) have the same shape: they name V0 but they test whatever the critic last accepted.
Fix: score `tests/rules_v0.py` (the pinned V0 copy the loop tests already use) instead of `classifier.rules`.

### C4. Timing based tests
`tests/test_tutor.py:15,32,45,64,79,104,149` synchronise with the tutor's background thread by sleeping
0.08 s to 0.4 s and then asserting the thread finished, and `:110,133` assert on "late" and "paused"
classifications that are wall clock decisions. They passed here while a second pytest was running, so the
margins are not tight, but this is the file that will flake on a loaded laptop at 2 a.m.
`tests/test_engine.py` and `tests/test_scheduler.py` do this correctly: time is injected.

### C5. Duplicated blocks
- `eval/baseline_knn.py:20-26`: `from tenfold.env import load_env` plus `load_env()` written twice in a row.
  **OWNER: Ilan, do not edit.**
- `.env.example:9-18` and `:19-23`: the "critic model backend (pick one)" block appears twice, with different
  wording. No secrets in either, only placeholder names, which is what SPEC.md section 11 asks for.
  **OWNER: Ilan, do not edit.**
- `.gitignore:9` and `:14` both `.venv/`; `:19` and `:20` both `.claude-critic/`.

### C6. `scripts/gonogo.py` has no caller
221 lines, referenced by nothing: not the Makefile, not the README, not `docs/`, only by SPEC.md section 13
as the 11:45 go/no-go step, which is long past. It needs a camera to run at all. It is also the only place
that computes the detection ceiling numbers of G4. Either wire it into a `make gonogo` target that writes
`data/detection_ceiling.json`, or delete it after copying the measured numbers into the README.

### C7. Stale references
- `tenfold/doctor.py:81`: the fix hint for missing train data says `python app/capture.py (Axel)`. The script
  is `data/capture.py`. **OWNER: Ilan, do not edit.**
- `docs/loop.md` ("Read the evidence in 60 seconds") tells the reader that `data/metrics.json` holds train and
  held-out metrics per version, but `.gitignore:17` ignores `data/metrics*.json`, so a cloned repo never has
  it. The committed artefact is `data/snapshot.json`.
- `TODOS.md` still lists as deferred two things that were built since: "Browser app (MediaPipe JS)" (now
  `web/course/` plus `app/server.py`, SPEC.md amendment F7 bis) and "TTS and wrong-finger arrows" (TTS shipped
  in commit 7b27584 with `tests/test_voice.py` covering it). The arrows are still open.

### C8. `tenfold/doctor.py` opens the camera its own way
`tenfold/doctor.py:43-48` does `cv2.VideoCapture(0)` directly, while every other entry point
(`data/capture.py:360`, `scripts/gonogo.py:153`, `app/server.py`) goes through `app.camera.open_camera`, which
probes indexes 0 to 3 and forces `CAP_AVFOUNDATION` on macOS precisely because index 0 is often a black
virtual camera (`app/camera.py:10-17,26`). So doctor can report "camera 0 not readable" on a Mac where
`make run` works, and vice versa. It is a WARN, so nothing breaks; it just tells the truth less often.
**OWNER: Ilan, do not edit.**

### C9. Small dead or loose ends in the eval and loop code (**OWNER: Ilan, do not edit**)
- `eval/run_eval.py:97` `def git_sha(path: Path)` never uses `path`; it always resolves `REPO`.
- `eval/run_eval.py:46-62` `load_samples` accepts any row regardless of its `split` field. Today
  `data/samples.jsonl` is 608 rows, all `split: "train"` (verified), so nothing is wrong, but one stray test
  row appended to that file would be scored as train by `run_eval.py` and copied into the critic worktree as
  `data/train.jsonl` by `loop/critic.py:sync_inputs`. A two line filter would make CLAUDE.md's rule
  mechanical instead of conventional.
- `loop/guard.py:77` runs the smoke subprocess with `timeout=30.0`; SPEC.md section 7 specifies a 5 second
  subprocess. The smoke test itself already enforces a 5 ms p95 per call, so the effect is only that a
  pathological hang costs 30 s instead of 5 s.

### C10. Dataset coverage is thinner than SPEC.md 5.6 (FROZEN, reported for the record)
`data/samples.jsonl`: 608 windows (SPEC target 800 to 1000), all `person: axel`, all `split: train`, and the
six angle by distance blocks are not covered:
```
front_near 203   front_far 206   side_near 198   top_near 1   top_far 0   side_far 0
```
`eval/slices.py:311` skips any slice under 20 samples, so `angle=top` never reaches the metric gate and the
gate's "no capture condition may fall" rule effectively watches front and side only. The file is frozen by
CLAUDE.md, so this is a capture decision (Axel), not an edit.

---

## Checks that came back clean

- **Secrets**: no `.env` in the working tree, none in the history (`git log --all --diff-filter=A` over
  `.env`, key and token patterns: nothing). `.env.example` has names only. `loop/mcp.json` uses
  `${WANDB_API_KEY}` interpolation, not a literal token, and is registered at user scope per SPEC.md 11.
  `.gitignore` covers `.env`, `.mcp.json`, `*.log`, `loop/transcripts/`, `loop/*.out`, `eval/results/`,
  `eval/last_train_report.json`, `data/metrics*.json`, `data/BEST_VERSION`, `.claude-critic/`,
  `loop/blind-rules.py`, `loop/watch-state.json`.
- **Held-out blindness**: `data/samples.jsonl` contains zero rows with `split == "test"`. The only readers of
  `../tenfold-heldout/test.jsonl` are `eval/run_eval.py --split heldout` and, as noted in G5,
  `eval/baseline_knn.py`. `loop/critic.py` strips every `*_HELDOUT` variable from the critic's environment
  (`self.env`), runs the held-out eval in a subprocess with a fresh `os.environ`, never copies metrics,
  snapshots or the held-out report into the worktree (`CRITIC_FILES` is 15 explicit files), and the
  diagnostic prompt only ever receives `eval/last_train_report.json`. `loop/guard.py:31,150-195` audits tool
  inputs for `heldout`, `test.jsonl` and paths outside the worktree, and the tests for that audit are real.
  Nothing writes held-out data inside the repo: `data/capture.py:61` targets `../tenfold-heldout/test.jsonl`
  with dir mode 700 and file mode 600, and `loop/rehearse.py:40-43` writes its synthetic held-out set beside
  the throwaway clone, not inside it.
- **Tests that test nothing**: no assertion that cannot fail, no test whose subject is fully mocked away, no
  test swallowing its own exception, no always-true skip other than C1. The mock critic
  (`tests/fake_claude.py`) is a stand-in for the `claude` binary only: the guard, the gate, the eval, the
  commits and the worktree are all real in those tests.
- **Missing directories and parents**: every writer creates its parent
  (`SampleWriter.__init__:275`, `run_eval.py:224`, `snapshot.py:289`, `critic.py:self.tmp`), so no
  first run fails on a missing directory.
- **Camera indexes**: `data/capture.py:524` and `scripts/gonogo.py:148` both default to `--camera None` and
  delegate to `app.camera.open_camera`, which probes and warms up. Only doctor hardcodes 0 (C8).
- **Suite health**: 284 tests, 375 s wall time with a competing pytest on the box. No test needs the network
  or a real camera. `tests/test_voice.py` starts a local `app/server.py --mock` on a free port and drives
  headless chromium, which is 4 of the 6 minutes; the loop tests spawn real critic and eval subprocesses in
  `tmp_path` and cost about 25 s in total. Nothing depends on test ordering except the environment leakage in
  G1 and the global `os.environ.update` in `tests/test_guard_and_loop.py:308-315`, which is restored in a
  `finally`.
