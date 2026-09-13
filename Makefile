PY ?= .venv/bin/python
N ?= 1

.PHONY: install doctor test run demo eval eval-local eval-heldout tutor-eval knn split retro-validate loop dry-run nightly watch dashboard snapshot rehearse rehearse-mock

install:
	uv venv -p 3.11 .venv && uv pip install -p .venv/bin/python -r requirements.txt

doctor:
	$(PY) -m tenfold.doctor

test:
	$(PY) -m pytest -q

run:            ## live app with the webcam (Axel)
	$(PY) app/server.py

demo:           ## the fixed demo/scenario.json sequence through the real pipeline (Axel)
	$(PY) app/server.py --mock --demo

eval:           ## train evaluation, published to Weave when WANDB_API_KEY is set
	$(PY) eval/run_eval.py --split train --tag $${TAG:-v0}

eval-local:
	$(PY) eval/run_eval.py --split train --local --tag $${TAG:-v0}

eval-heldout:   ## uses WANDB_API_KEY_HELDOUT, project tenfold-heldout
	$(PY) eval/run_eval.py --split heldout --tag $${TAG:-v0}

tutor-eval:     ## which W&B Inference model speaks for Tally: one Weave Evaluation per model and a Leaderboard
	$(PY) eval/tutor_eval.py

knn:
	$(PY) eval/baseline_knn.py --split heldout

split:          ## validation retrospective, participants non identifies: whole holds, seed 42, validation outside the repo
	$(PY) eval/split.py --name $${NAME:-retro-seed42}

retro-validate: ## V0 vs the running rules on that validation set, frozen and strict scoring, local only
	$(PY) eval/retro_validate.py --name $${NAME:-retro-seed42}

loop:           ## N guarded, gated critic iterations: make loop N=3
	$(PY) loop/critic.py --iterations $(N)

dry-run:        ## diagnostic + patch + guard, prints the diff, commits nothing
	$(PY) loop/critic.py --iterations 1 --dry-run

nightly:
	bash loop/nightly.sh 20

watch:          ## continuous loop in the runner clone: pull, iterate on new data, push accepted versions
	caffeinate -i $(PY) loop/watch.py --push --max-cost $${MAX_COST:-20} --critic-args=--skip-heldout

dashboard:
	$(PY) -m marimo run dashboard/loop_dashboard.py

snapshot:       ## rebuild data/snapshot.json for the dashboard, then commit it
	$(PY) loop/snapshot.py

rehearse:       ## full loop on hard synthetic data in a throwaway clone, real claude, smoke Weave projects
	$(PY) loop/rehearse.py --iterations $${N:-2}

rehearse-mock:
	$(PY) loop/rehearse.py --iterations $${N:-2} --mock
