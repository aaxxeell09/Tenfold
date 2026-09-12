PY ?= .venv/bin/python
N ?= 1

.PHONY: install doctor test run demo eval eval-local eval-heldout knn loop dry-run nightly dashboard

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

dashboard:
	$(PY) -m marimo run dashboard/loop_dashboard.py
