#!/usr/bin/env bash
# The overnight run: informed critic on main + blind critic in its own worktree, both under caffeinate.
#   loop/nightly.sh 20
# Check loop/nightly.log and the heartbeat op in Weave at 23:30. Never commit to main while this runs.
cd "$(dirname "$0")/.."
N=${1:-20}
PY=${PY:-.venv/bin/python}
[ -f .env ] && set -a && . ./.env && set +a
mkdir -p loop
echo "$(date -u +%FT%TZ) nightly start N=$N" >> loop/nightly.log
caffeinate -i "$PY" loop/critic.py --iterations "$N" --no-early-stop >> loop/nightly.log 2>&1 &
P1=$!
caffeinate -i "$PY" loop/critic.py --iterations "$N" --no-early-stop --blind --worktree ../tenfold-blind >> loop/nightly-blind.log 2>&1 &
P2=$!
wait $P1; wait $P2
echo "$(date -u +%FT%TZ) nightly done" >> loop/nightly.log
