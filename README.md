# Tenfold

**The child learns multiplication. The tutor learns how to see the child.**

A math tutor (Tally) that watches a child's hands on the webcam, teaches the 6-10 finger multiplication method, corrects the gesture live, and whose perception layer rewrites itself overnight from its own failures, evaluated blind on other people's hands.

CoreWeave Hacks "Agent Loops", San Francisco, 12-13 September 2026. Built this weekend; no code predates the first commit.

## Docs
- `SPEC.md`: the source of truth for the team and for Claude Code.
- `docs/review-2026-09-12.md`: the four-phase plan review and its decisions.
- `TODOS.md`: what was deferred and why.

## Quick start (target: under 5 minutes, no keys, no webcam)
```
git clone https://github.com/aaxxeell09/Tenfold.git && cd Tenfold
python3.11 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
make doctor && make demo
```
`make run` uses the webcam. `make loop N=1` runs one guarded critic iteration (needs `.env`). Sections to come: architecture, sponsor tools, the loop, demo script, roadmap.
