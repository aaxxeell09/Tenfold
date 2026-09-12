#!/usr/bin/env bash
# The overnight run: informed critic on main + blind critic in its own worktree, both under caffeinate.
#   loop/nightly.sh 20
# Budget: each arm stops at TENFOLD_MAX_COST_USD (default 40 USD). Extra critic flags: TENFOLD_CRITIC_ARGS.
# Check loop/nightly.log, loop/nightly-blind.log and the heartbeat op in Weave at 23:30.
# Never commit to main while this runs. .env is loaded by loop/critic.py itself (do not source it: a header
# value contains a space).
cd "$(dirname "$0")/.."
N=${1:-20}
PY=${PY:-.venv/bin/python}
EXTRA=${TENFOLD_CRITIC_ARGS:-}
mkdir -p loop
echo "$(date -u +%FT%TZ) nightly start N=$N" >> loop/nightly.log
# shellcheck disable=SC2086
caffeinate -i "$PY" -u loop/critic.py --iterations "$N" --no-early-stop $EXTRA > loop/nightly.out 2>&1 &
P1=$!
# shellcheck disable=SC2086
caffeinate -i "$PY" -u loop/critic.py --iterations "$N" --no-early-stop --blind $EXTRA > loop/nightly-blind.out 2>&1 &
P2=$!
wait $P1; R1=$?
wait $P2; R2=$?
echo "$(date -u +%FT%TZ) nightly done informed=$R1 blind=$R2" >> loop/nightly.log
exit $(( R1 != 0 || R2 != 0 ))
