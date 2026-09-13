PY ?= .venv/bin/python
N ?= 1

.PHONY: install doctor test run demo eval eval-local eval-heldout knn loop dry-run nightly watch dashboard snapshot rehearse rehearse-mock

install:
	uv venv -p 3.11 .venv && uv pip install -p .venv/bin/python -r requirements.txt

doctor:
	$(PY) -m tenfold.doctor

test:
	$(PY) -m pytest -q

run:            ## live app with the webcam (Axel)
	$(PY) app/main.py

demo:           ## replay a recorded window through the real pipeline, no camera, no keys (Axel)
	$(PY) app/main.py --demo

eval:           ## train evaluation, published to Weave when WANDB_API_KEY is set
	$(PY) eval/run_eval.py --split train --tag $${TAG:-v0}

eval-local:
	$(PY) eval/run_eval.py --split train --local --tag $${TAG:-v0}

eval-heldout:   ## uses WANDB_API_KEY_HELDOUT, project tenfold-heldout
	$(PY) eval/run_eval.py --split heldout --tag $${TAG:-v0}

knn:
	$(PY) eval/baseline_knn.py --split heldout

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
