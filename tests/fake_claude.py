"""Stand-in for `claude -p` in tests and the mock loop. Behaviour is chosen by FAKE_CLAUDE_PATCH:
  good     lowers the contact threshold a little (a harmless, accepted patch)
  bad      makes classify() refuse everything (rejected by the smoke test and the gate)
  cheat    imports os (rejected by the AST whitelist)
  outside  edits another file (rejected by the files rule)
  huge     adds 100 lines (rejected by the diff size rule)
  hang     sleeps forever (the critic's timeout must fire)
  noop     changes nothing (rejected: no_change)
Prints stream-json like the real CLI so the transcript audit and text extraction are exercised.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

mode = os.environ.get("FAKE_CLAUDE_PATCH", "good")
prompt = sys.argv[sys.argv.index("-p") + 1] if "-p" in sys.argv else ""


def emit(text: str, tool_input: dict | None = None) -> None:
    content = []
    if tool_input is not None:
        content.append({"type": "tool_use", "name": "Edit", "input": tool_input})
    content.append({"type": "text", "text": text})
    print(json.dumps({"type": "assistant", "message": {"content": content}}))
    print(json.dumps({"type": "result", "result": text, "total_cost_usd": float(os.environ.get("FAKE_CLAUDE_COST", "0.5"))}))


rules = Path("classifier/rules.py")
if prompt.startswith("You are the diagnostic agent"):
    emit("DIAGNOSIS: near_contact windows are classified as contact (near:7x8 at 0.50).\n"
         "HYPOTHESIS: CONTACT_THRESHOLD is too permissive.\nEVIDENCE: synth00030 closest pair dist 0.42")
elif prompt.startswith("You are the guard agent"):
    emit("VERDICT: APPROVE | the patch only tightens the contact threshold named in the diagnosis")
else:
    if mode == "hang":
        time.sleep(3600)
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
         tool_input={"file_path": "classifier/rules.py", "old_string": "0.35", "new_string": "0.30"})
