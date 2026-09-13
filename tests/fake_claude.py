"""Stand-in for `claude -p` in tests and the mock loop. Behaviour is chosen by FAKE_CLAUDE_PATCH:
  good     lowers the contact threshold a little (a harmless, accepted patch)
  bad      makes classify() refuse everything (rejected by the smoke test and the gate)
  cheat    imports os (rejected by the AST whitelist)
  outside  edits another file (rejected by the files rule)
  huge     adds 100 lines (rejected by the diff size rule)
  hang     sleeps forever (the critic's timeout must fire)
  noop     changes nothing (rejected: no_change)
  maxturns / maxturns_noedit   stops with error_max_turns after (or without) an edit
Costs: FAKE_CLAUDE_COST per call (default 0.5), FAKE_CLAUDE_PATCH_COST for the patch agent (default the same).
A call whose cost exceeds --max-budget-usd stops with error_max_budget_usd, like the real CLI, after its edits.
FAKE_CLAUDE_GUARD: need_account approves only when shown the patch agent's account; sly_reject rejects with a
sentence that contains the word "approve".
Prints stream-json like the real CLI so the transcript audit and text extraction are exercised.

With --inference it stands in for W&B Inference instead: the prompt arrives on stdin, the reply is one
OpenAI-style chat completion JSON with a usage block, and only the diagnostic and guard agents are served.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

mode = os.environ.get("FAKE_CLAUDE_PATCH", "good")
inference = "--inference" in sys.argv
prompt = sys.stdin.read() if inference else (sys.argv[sys.argv.index("-p") + 1] if "-p" in sys.argv else "")
budget = float(sys.argv[sys.argv.index("--max-budget-usd") + 1]) if "--max-budget-usd" in sys.argv else None
base_cost = float(os.environ.get("FAKE_CLAUDE_COST", "0.5"))


def text_agent_reply(p: str) -> str | None:
    """Replies of the two agents that need no tools; None for anything else."""
    if p.startswith("You are the diagnostic agent"):
        return ("DIAGNOSIS: near_contact windows are classified as contact (near:7x8 at 0.50).\n"
                "HYPOTHESIS: CONTACT_THRESHOLD is too permissive.\nEVIDENCE: synth00030 closest pair dist 0.42")
    if p.startswith("You are the guard agent"):
        account = p.split("PATCH AGENT'S ACCOUNT:", 1)[-1].split("DIFF:", 1)[0]
        guard_mode = os.environ.get("FAKE_CLAUDE_GUARD")
        if guard_mode == "need_account" and "HYPOTHESIS:" not in account:
            return "VERDICT: REJECT | Rule 1 | no account of the edit | fix: explain the measured cause"
        if guard_mode == "sly_reject":
            return "VERDICT: REJECT | Rule 2 | I would approve a general fix, not this memorised value | fix: derive it"
        return "VERDICT: APPROVE | the patch only tightens the contact threshold named in the diagnosis"
    return None


if inference:
    reply = text_agent_reply(prompt)
    if reply is None:
        print(json.dumps({"error": "the fake inference endpoint only serves the diagnostic and guard agents"}))
        sys.exit(2)
    print(json.dumps({"model": "fake/qwen", "choices": [{"message": {"role": "assistant", "content": reply}}],
                      "usage": {"prompt_tokens": len(prompt) // 4, "completion_tokens": len(reply) // 4,
                                "total_tokens": (len(prompt) + len(reply)) // 4}}))
    sys.exit(0)


def emit(text: str, tool_input: dict | None = None, cost: float = base_cost) -> None:
    content = []
    if tool_input is not None:
        content.append({"type": "tool_use", "name": "Edit", "input": tool_input})
    if budget is not None and cost > budget:
        content.append({"type": "text", "text": "Checking one more class before I explain."})
        print(json.dumps({"type": "assistant", "message": {"content": content}}))
        print(json.dumps({"type": "result", "subtype": "error_max_budget_usd", "is_error": True, "total_cost_usd": budget}))
        sys.exit(1)
    content.append({"type": "text", "text": text})
    print(json.dumps({"type": "assistant", "message": {"content": content}}))
    print(json.dumps({"type": "result", "result": text, "total_cost_usd": cost}))


rules = Path("classifier/rules.py")
if text_agent_reply(prompt) is not None:
    emit(text_agent_reply(prompt))
else:
    patch_cost = float(os.environ.get("FAKE_CLAUDE_PATCH_COST", str(base_cost)))
    if mode == "hang":
        time.sleep(3600)
    if mode in ("maxturns", "maxturns_noedit"):
        if mode == "maxturns":
            src0 = rules.read_text()
            rules.write_text(src0.replace("CONTACT_THRESHOLD = 0.35", "CONTACT_THRESHOLD = 0.30"))
        print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Let me look at more failures first."}]}}))
        print(json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True, "num_turns": 46,
                          "total_cost_usd": 0.5}))
        sys.exit(1)
    src = rules.read_text()
    if mode in ("good", "same"):
        m = re.search(r"CONTACT_THRESHOLD = ([0-9.]+)", src)
        cur = float(m.group(1))
        new = cur if mode == "same" else max(0.15, round(cur - 0.05, 2))
        src = src.replace(m.group(0), f"CONTACT_THRESHOLD = {new:.2f}")
        src = re.sub(r"(Hypothesis log[^\n]*\n)", rf"\1- vN: contact threshold {cur:.2f} -> {new:.2f} for near-contact holds\n", src, count=1)
    elif mode == "bad":
        src = src.replace("    frame = features.last_valid_frame(window)", "    return GestureState.unknown()\n    frame = features.last_valid_frame(window)")
    elif mode == "cheat":
        src = "import os\n" + src
    elif mode == "huge":
        src = src + "\n" + "\n".join(f"PAD_{i} = {i}" for i in range(100)) + "\n"
    elif mode == "outside":
        Path("classifier/features.py").write_text(Path("classifier/features.py").read_text() + "\n# tampered\n")
    if mode != "noop":
        rules.write_text(src)
    emit("HYPOTHESIS: near-contact gaps sit just under 0.35 on train\nPATCH: tightened CONTACT_THRESHOLD from 0.35 to 0.30\nEXPECTED: near_contact accuracy up, positives unchanged",
         tool_input={"file_path": "classifier/rules.py", "old_string": "0.35", "new_string": "0.30"}, cost=patch_cost)
