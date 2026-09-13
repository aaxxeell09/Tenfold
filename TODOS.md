# TODOS (deferred by /autoplan, 2026-09-12)

- [ ] **W&B Inference model as a second overnight critic arm** (P2, human L / CC M). Why: stronger sponsor usage and a Claude-vs-open-model comparison in Weave. Cons: a second Anthropic-free prompt path to debug. Context: same `critic.py`, `--critic-backend wandb`; compare `-blind`, `-claude`, `-wandb` curves. Depends on: the loop running unattended (Saturday 21:00).
- [ ] **Browser app (MediaPipe JS)** (P3, human XL / CC L). Why: shareable URL for the social demo and "production-ready". Cons: conflicts with the Python `rules.py` contract. Context: roadmap item; would need rules.py transpiled or served. Depends on: nothing this weekend.
- [ ] **Offline replay of a stored critic patch through the guard** (P3, human S / CC S). Why: demo the guard and eval path with no Anthropic key. Context: `critic.py --replay loop/transcripts/v3.json`. Depends on: guard.py.
- [ ] **Brand mark for Tally** (P3, human S / CC S). Why: litmus check 1 fails; a hand glyph and one recurring accent. Context: Sunday polish, ui.py only.
- [ ] **TTS and wrong-finger arrows** (P3). Why: nicer child experience. Cons: robotic voice risk, arrow geometry. Context: only after the loop and the replay path are done; TTS only if tested on the demo speaker.
