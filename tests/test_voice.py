"""Voice answers in the camera lesson of web/course/, driven in a real browser.

Headless Chromium ships no speech recognition, which is exactly the case the
page has to survive: the microphone stays hidden and the keyboard still works.
A fake recogniser is then injected to exercise the rest, because the parser and
the listening window are the parts that can silently submit a wrong answer.
The same browser is used for the answer paths around voice: the typed digits on
the start check, the question counter, the first try bonus and the XP a child
keeps when quitting, none of which can be exercised without a real page.

Skipped when playwright or its browser is missing, so make test stays green on a
machine that has neither.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright", reason="playwright is not installed")
from playwright.sync_api import sync_playwright  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
CHROME_CANDIDATES = (
    Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome"),
)

# A fake Web Speech API, installed before the page script runs. Chromium exposes
# both the prefixed and the unprefixed constructor, so both have to be replaced
# or the page picks the real one and nothing can be driven from a test.
FAKE_RECOGNITION = """
window.__voice = { started: 0, stopped: 0 };
function FakeRecognition() {
  window.__rec = this;
  this.start = () => { window.__voice.started += 1; if (this.onstart) this.onstart(); };
  this.stop = () => { window.__voice.stopped += 1; if (this.onend) this.onend(); };
}
window.SpeechRecognition = FakeRecognition;
window.webkitSpeechRecognition = FakeRecognition;
window.__say = function (text, isFinal) {
  const alternative = { transcript: text };
  const result = Object.assign([alternative], { isFinal: isFinal });
  window.__rec.onresult({ resultIndex: 0, results: Object.assign([result], { length: 1 }) });
};
"""

# A handle on the page's own websocket. The mock server cannot be asked for a
# given message at a given moment, so the tests that need one exact message take
# the page's handler over: __silence stops the server's own stream, __feed hands
# the page a message built here. Nothing about the page's contract changes.
CAPTURE_SOCKET = """
(() => {
  const Original = window.WebSocket;
  function Wrapped(url, protocols) {
    const ws = protocols === undefined ? new Original(url) : new Original(url, protocols);
    window.__socket = ws;
    return ws;
  }
  Wrapped.prototype = Original.prototype;
  ["CONNECTING", "OPEN", "CLOSING", "CLOSED"].forEach((name, i) => { Wrapped[name] = i; });
  window.WebSocket = Wrapped;
  window.__silence = () => { window.__page_onmessage = window.__socket.onmessage; window.__socket.onmessage = () => {}; };
  window.__feed = (message) => (window.__page_onmessage || window.__socket.onmessage)({ data: JSON.stringify(message) });
})();
"""


# The page now sends more than answers on that socket: the child speaking and the
# tts pair around every line. A test about answers reads the check messages alone.
WATCH_SENDS = """
(() => {
  window.__sent = [];
  const send = WebSocket.prototype.send;
  WebSocket.prototype.send = function (data) {
    try { window.__sent.push(JSON.parse(data)); } catch (e) { /* not ours */ }
    return send.call(this, data);
  };
})();
"""


def sent(tab, kind: str):
    """Every message of one type the page has sent since the watcher was installed."""
    return [m for m in tab.evaluate("window.__sent") if m.get("type") == kind]


def checks(tab):
    return [m["value"] for m in sent(tab, "check")]


def state_message(node: str, **fields):
    """One "state" message shaped like app/server.py build_message."""
    message = {"type": "state", "node": node, "state": "wrong_pose", "exercise": "6 x 7",
               "tally": "", "reason": None, "reaction": None, "answer": None,
               "wrong": [], "match": [], "reasoning": [], "fingers": [],
               "hint": {}, "hint_level": 0, "fact": "6x7"}
    message.update(fields)
    return message


def feed(tab, node: str, **fields):
    tab.evaluate("(m) => window.__feed(m)", state_message(node, **fields))


def running_lesson(tab):
    """Open the first node and take the message stream over."""
    tab.wait_for_function("!!Tenfold.lesson")
    tab.wait_for_function("document.querySelector('#lesson .practice-why').textContent === '1 of 5'")
    tab.evaluate("() => window.__silence()")
    return tab.evaluate("Tenfold.lesson.node.id")


ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty", 6: "sixty",
        7: "seventy", 8: "eighty", 9: "ninety"}


def open_lesson(tab, url):
    """Land on the practice path and start the first node, where voice lives."""
    tab.goto(url + "#practice", wait_until="domcontentloaded")
    # the Start bubble sits on the current node; tapping it opens the lesson
    tab.wait_for_selector('[data-action="start"]', timeout=10000)
    tab.click('[data-action="start"]', force=True)
    tab.wait_for_selector("#lesson .practice-stage", timeout=10000)


# answer_correct lives for one message: the server advances to the next exercise in
# the same breath, so a poll on the live attribute can miss it. Every state the page
# renders is recorded instead, and the transient ones are looked for in that list.
STATE_LOG = """
window.__states = [];
document.addEventListener("DOMContentLoaded", () => {
  const host = document.querySelector("#lesson");
  if (!host) return;
  new MutationObserver((records) => {
    for (const record of records) {
      const el = record.target;
      if (el.classList && el.classList.contains("practice-stage")) window.__states.push(el.dataset.state);
    }
  }).observe(host, { attributes: true, attributeFilter: ["data-state"], subtree: true });
});
"""


def saw_state(tab, state: str, timeout: int = 30000):
    """Wait for a state the page went through, live or already passed."""
    tab.wait_for_function(f"window.__states.includes('{state}')", timeout=timeout)


def stage_state(tab) -> str:
    return tab.evaluate("document.querySelector('#lesson .practice-stage')?.dataset.state || ''")


def wait_for_state(tab, state: str, timeout: int = 30000):
    tab.wait_for_function(
        f"document.querySelector('#lesson .practice-stage')?.dataset.state === '{state}'",
        timeout=timeout)


def spoken(number: int) -> str:
    """English words for the results this lesson produces, 36 to 100."""
    if number == 100:
        return "one hundred"
    tens, units = divmod(number, 10)
    return f"{TENS[tens]} {ONES[units]}".strip() if units else TENS[tens]


def chrome() -> str | None:
    """A pinned binary when this machine has one, otherwise playwright's own."""
    for path in CHROME_CANDIDATES:
        if path.exists():
            return str(path)
    return None


def launch(play):
    binary = chrome()
    try:
        return play.chromium.launch(**({"executable_path": binary} if binary else {}))
    except Exception as error:      # no browser installed: not a failure of the page
        pytest.skip(f"no chromium for playwright here: {error}")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, str(REPO / "app" / "server.py"), "--mock", "--no-open",
         "--port", str(port)],
        cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                break
        except OSError:
            time.sleep(0.2)
    else:
        process.kill()
        pytest.fail("the mock server never came up")
    yield f"http://127.0.0.1:{port}"
    process.terminate()
    process.wait(timeout=10)


@pytest.fixture()
def page(server):
    with sync_playwright() as play:
        browser = launch(play)
        context = browser.new_context()
        yield context, server
        browser.close()


PARSER_CASES = [
    ("56", 56), ("fifty six", 56), ("fifty-six", 56), ("it is fifty six", 56),
    ("thirty six", 36), ("forty two", 42), ("fourty two", 42),
    ("sixteen", 16), ("ninety", 90), ("one hundred", 100), ("a hundred", 100),
    ("hundred", 100), ("one hundred and four", 104), ("seven", 7),
    ("The answer is 72 I think", 72), ("fifty six please", 56),
    ("", None), ("I do not know", None), ("um", None),
]


def test_the_number_parser(page):
    context, url = page
    tab = context.new_page()
    tab.goto(url + "#home", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold")
    for text, expected in PARSER_CASES:
        got = tab.evaluate("(t) => Tenfold.parseNumber(t)", text)
        assert got == expected, f"{text!r} parsed as {got}, expected {expected}"


NO_SPEECH_API = """
delete window.SpeechRecognition;
delete window.webkitSpeechRecognition;
"""


def test_without_the_api_the_microphone_never_appears(page):
    """Firefox and Safari have no speech recognition. Keyboard only, no error."""
    context, url = page
    tab = context.new_page()
    errors = []
    tab.on("pageerror", lambda e: errors.append(str(e)))
    tab.add_init_script(NO_SPEECH_API)
    open_lesson(tab, url)
    tab.wait_for_timeout(500)
    assert tab.is_hidden("#lesson .mic")
    assert tab.evaluate("Tenfold.voice.recognition") is None
    assert tab.is_visible("#lesson .practice-answer") and tab.is_visible("#lesson .caret")
    # the keyboard has to keep working with no speech API at all
    tab.keyboard.type("42")
    assert tab.inner_text("#lesson .typed") == "42"
    assert errors == []


def test_the_first_click_arms_the_microphone_and_it_stays_on(page):
    """Chrome opens the mic only from a user gesture: the tap on Start is that gesture,
    and recognition then stays alive across states. Only the gate moves."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(FAKE_RECOGNITION)
    open_lesson(tab, url)
    assert tab.is_visible("#lesson .mic")
    assert tab.evaluate("window.__voice.started") >= 1
    assert tab.evaluate("Tenfold.voice.wanted") is True
    assert "is-on" in tab.get_attribute("#lesson .mic", "class")
    assert tab.inner_text("#lesson .mic .mic-label").strip().lower() == "listening"

    wait_for_state(tab, "wrong_pose")
    assert tab.evaluate("Tenfold.voice.gate") is False
    assert tab.evaluate("Tenfold.voice.running") is True

    wait_for_state(tab, "correct_pose")
    assert tab.evaluate("Tenfold.voice.gate") is True
    assert tab.evaluate("window.__voice.stopped") == 0


def test_a_number_heard_outside_the_answer_window_is_not_sent(page):
    context, url = page
    tab = context.new_page()
    tab.add_init_script(FAKE_RECOGNITION)
    open_lesson(tab, url)
    wait_for_state(tab, "wrong_pose")
    tab.evaluate(WATCH_SENDS)
    tab.evaluate("() => window.__say('thirty six', true)")
    tab.wait_for_timeout(300)
    assert checks(tab) == [], "a number outside the answer window is not an answer"
    assert [m["text"] for m in sent(tab, "speech")] == ["thirty six"], "it is still the child speaking"


def test_tallys_own_numbers_are_not_answers(page):
    """The mic stays open while Tally talks through the speakers. A number he just said
    is dropped for as long as he says it; any other number still counts."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(FAKE_RECOGNITION)
    open_lesson(tab, url)
    wait_for_state(tab, "correct_pose")
    assert sorted(tab.evaluate("Tenfold.numbersIn('Six and six. What is six times six?')")) == [6, 12]
    assert sorted(tab.evaluate("Tenfold.numbersIn('Thirty six. Exactly.')")) == [6, 30, 36]
    tab.evaluate(WATCH_SENDS + """
    () => {
      Tenfold.voice.said = [36]; Tenfold.voice.saidText = "thirty six exactly";
      Tenfold.voice.spokeUntil = Date.now() + 5000;
      window.__say('thirty six', true);
      window.__say('eleven', true);
    }""")
    tab.wait_for_timeout(300)
    assert checks(tab) == [11]
    assert [m["text"] for m in sent(tab, "speech")] == ["eleven"], "his own line is not the child"


DENIED_RECOGNITION = FAKE_RECOGNITION + """
function DeniedRecognition() {
  FakeRecognition.call(this);
  this.start = () => { window.__voice.started += 1; if (this.onerror) this.onerror({ error: 'not-allowed' }); };
}
window.SpeechRecognition = DeniedRecognition;
window.webkitSpeechRecognition = DeniedRecognition;
"""


def test_a_refused_microphone_falls_back_to_typing(page):
    context, url = page
    tab = context.new_page()
    tab.add_init_script(DENIED_RECOGNITION)
    open_lesson(tab, url)
    tab.wait_for_timeout(300)
    assert tab.evaluate("Tenfold.voice.denied") is True
    assert "is-denied" in tab.get_attribute("#lesson .mic", "class")
    assert tab.inner_text("#lesson .mic .mic-label").strip().lower() == "type it"
    assert tab.is_visible("#lesson .caret")
    tab.keyboard.type("42")
    assert tab.inner_text("#lesson .typed") == "42"


def test_a_spoken_answer_is_sent_and_shown(page):
    context, url = page
    tab = context.new_page()
    tab.add_init_script(FAKE_RECOGNITION)
    tab.add_init_script(STATE_LOG)
    open_lesson(tab, url)
    wait_for_state(tab, "correct_pose")

    exercise = tab.inner_text("#lesson .practice-exercise").replace("×", "x")
    a, b = (int(part.strip()) for part in exercise.split(" x "))

    # an interim result is shown but never submitted
    tab.evaluate("() => window.__say('thirty', false)")
    tab.wait_for_timeout(150)
    assert "thirty" in tab.inner_text("#lesson .heard")
    assert stage_state(tab) == "correct_pose"

    tab.evaluate("(text) => window.__say(text, true)", spoken(a * b))
    saw_state(tab, "answer_correct", timeout=10000)


def test_a_spoken_wrong_answer_is_sent_too(page):
    context, url = page
    tab = context.new_page()
    tab.add_init_script(FAKE_RECOGNITION)
    tab.add_init_script(STATE_LOG)
    open_lesson(tab, url)
    wait_for_state(tab, "correct_pose")
    tab.evaluate("() => window.__say('eleven', true)")
    saw_state(tab, "answer_wrong", timeout=10000)


def test_the_same_number_twice_in_a_row_is_one_answer(page):
    """Recognition repeats itself. Two identical finals must not be two answers."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(FAKE_RECOGNITION)
    open_lesson(tab, url)
    wait_for_state(tab, "correct_pose")
    tab.evaluate(WATCH_SENDS)
    tab.evaluate("() => { window.__say('eleven', true); window.__say('eleven', true); }")
    tab.wait_for_timeout(400)
    assert checks(tab) == [11]


# A fake speechSynthesis: records every utterance, ends each one on the next tick so
# the queue advances the way a real engine would, and takes a voice list from the test.
FAKE_SYNTH = """
window.__spoken = []; window.__cancelled = 0;
window.__voices = (window.__voiceList || []).map((v) => Object.assign({ localService: true, default: false, voiceURI: v.name }, v));
function SpeechSynthesisUtterance(text) { this.text = text; this.voice = null; this.rate = 1; this.pitch = 1; this.lang = ""; }
window.SpeechSynthesisUtterance = SpeechSynthesisUtterance;
// the real property has no setter: a plain assignment is silently ignored
Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
  getVoices: () => window.__voices,
  cancel: () => { window.__cancelled += 1; },
  speak: (u) => {
    window.__spoken.push({ text: u.text, voice: u.voice && u.voice.name, rate: u.rate, pitch: u.pitch, lang: u.lang });
    if (u.onstart) u.onstart();
    setTimeout(() => { if (u.onend && window.__spoken.length <= (window.__endUpTo ?? Infinity)) u.onend(); }, 0);
  },
  addEventListener: (name, fn) => { (window.__onvoices = window.__onvoices || []).push(fn); },
} });
window.__loadVoices = (list) => {
  window.__voices = list.map((v) => Object.assign({ localService: true, default: false, voiceURI: v.name }, v));
  (window.__onvoices || []).splice(0).forEach((fn) => fn());
};
"""


def voices(names_and_langs):
    return "window.__voiceList = " + str([{"name": n, "lang": l} for n, l in names_and_langs]).replace("'", '"') + ";"


def test_tally_picks_the_best_english_voice(page):
    context, url = page
    tab = context.new_page()
    tab.add_init_script(voices([("Karen", "en-AU"), ("Daniel", "en-GB"), ("Samantha", "en-US"), ("Ava (Premium)", "en-US")]) + FAKE_SYNTH)
    tab.goto(url + "#home", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold")
    assert tab.evaluate("Tenfold.pickVoice().name") == "Ava (Premium)"

    tab = context.new_page()
    tab.add_init_script(voices([("Daniel", "en-GB"), ("Google US English", "en-US"), ("Microsoft Zira", "en-US")]) + FAKE_SYNTH)
    tab.goto(url + "#home", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold")
    # no preferred name: an en-US voice that reads as female, before any other en-US one
    assert tab.evaluate("Tenfold.pickVoice().name") == "Microsoft Zira"

    tab = context.new_page()
    tab.add_init_script(voices([("Daniel", "en-GB"), ("Google US English", "en-US")]) + FAKE_SYNTH)
    tab.goto(url + "#home", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold")
    assert tab.evaluate("Tenfold.pickVoice().name") == "Google US English"

    tab = context.new_page()
    tab.add_init_script(voices([]) + FAKE_SYNTH)
    tab.goto(url + "#home", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold")
    assert tab.evaluate("Tenfold.pickVoice()") is None


def test_tally_speaks_short_sentences_one_at_a_time(page):
    context, url = page
    tab = context.new_page()
    tab.add_init_script(voices([("Samantha", "en-US")]) + FAKE_SYNTH)
    tab.goto(url + "#home", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold")
    assert tab.evaluate("Tenfold.sentencesOf('Lovely work.  Tomorrow we try 7 x 7! Ready?')") == ["Lovely work.", "Tomorrow we try 7 x 7!", "Ready?"]
    tab.evaluate("() => Tenfold.speak('Lovely work. Tomorrow we try 7 x 7.')")
    tab.wait_for_timeout(100)
    spoken = tab.evaluate("window.__spoken")
    assert [s["text"] for s in spoken] == ["Lovely work.", "Tomorrow we try 7 x 7."]
    assert all(s["voice"] == "Samantha" and s["rate"] == 0.92 and s["pitch"] == 1.05 and s["lang"] == "en-US" for s in spoken)
    assert tab.evaluate("window.__cancelled") == 1


def test_a_new_sentence_cancels_the_one_still_queued(page):
    context, url = page
    tab = context.new_page()
    tab.add_init_script(voices([("Samantha", "en-US")]) + FAKE_SYNTH + "window.__endUpTo = 0;")
    tab.goto(url + "#home", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold")
    # the engine never ends the first utterance: the second sentence waits in the queue
    tab.evaluate("() => Tenfold.speak('First one. Second one.')")
    tab.wait_for_timeout(50)
    assert [s["text"] for s in tab.evaluate("window.__spoken")] == ["First one."]
    tab.evaluate("() => { window.__endUpTo = Infinity; Tenfold.speak('Something else.'); }")
    tab.wait_for_timeout(100)
    assert [s["text"] for s in tab.evaluate("window.__spoken")] == ["First one.", "Something else."]
    assert tab.evaluate("window.__cancelled") == 2


def test_the_first_sentence_waits_for_the_voices_and_the_pick_is_logged_once(page):
    context, url = page
    tab = context.new_page()
    logs = []
    tab.on("console", lambda m: logs.append(m.text))
    tab.add_init_script(voices([]) + FAKE_SYNTH)
    tab.goto(url + "#home", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold")
    tab.evaluate("() => Tenfold.speak('Show me both hands.')")
    tab.wait_for_timeout(100)
    assert tab.evaluate("window.__spoken") == []
    tab.evaluate("() => window.__loadVoices([{ name: 'Daniel', lang: 'en-GB' }, { name: 'Samantha', lang: 'en-US' }])")
    tab.wait_for_timeout(100)
    assert [(u["text"], u["voice"]) for u in tab.evaluate("window.__spoken")] == [("Show me both hands.", "Samantha")]
    tab.evaluate("() => Tenfold.speak('There they are.')")
    tab.wait_for_timeout(100)
    assert [m for m in logs if m.startswith("Tally voice:")] == ["Tally voice: Samantha"]


def test_the_mute_switch_silences_tally_and_is_remembered(page):
    context, url = page
    tab = context.new_page()
    tab.add_init_script(voices([("Samantha", "en-US")]) + FAKE_SYNTH)
    tab.goto(url + "#home", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold")
    assert tab.is_visible("#mute") and tab.evaluate("Tenfold.muted") is False
    tab.click("#mute")
    assert tab.evaluate("Tenfold.muted") is True
    assert "is-muted" in tab.get_attribute("#mute", "class")
    tab.evaluate("() => Tenfold.speak('Say the answer.')")
    tab.wait_for_timeout(100)
    assert tab.evaluate("window.__spoken") == []
    tab.reload(wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold")
    assert tab.evaluate("Tenfold.muted") is True
    tab.click("#mute")
    tab.evaluate("() => Tenfold.speak('Say the answer.')")
    tab.wait_for_timeout(100)
    assert [u["text"] for u in tab.evaluate("window.__spoken")] == ["Say the answer."]


def test_every_check_sentence_is_spoken(page):
    context, url = page
    tab = context.new_page()
    # with a working microphone step 3 asks for the answer out loud
    tab.add_init_script(FAKE_RECOGNITION + voices([("Samantha", "en-US")]) + FAKE_SYNTH)
    tab.goto(url + "#home", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold")
    tab.click("a.door[href='#check']")
    # step 3 shows "Say the answer." together with the mic pill, a moment after the match
    tab.wait_for_selector("#check-mic", state="visible", timeout=30000)
    tab.wait_for_timeout(300)
    spoken = [u["text"] for u in tab.evaluate("window.__spoken")]
    for line in ["Show me both hands.", "There they are.", "Touch your 6 with your 6.", "Six and six, touching.", "Say the answer."]:
        assert line in spoken, f"{line!r} was shown but not spoken: {spoken}"


# A microphone that says no the first time and yes the second: Chrome asks again
# when the child taps the mic, which is the only way back inside a session.
DENIED_ONCE = FAKE_RECOGNITION + """
window.__deny = true;
function DeniedOnce() {
  FakeRecognition.call(this);
  this.start = () => {
    window.__voice.started += 1;
    if (window.__deny) { window.__deny = false; if (this.onerror) this.onerror({ error: 'not-allowed' }); return; }
    if (this.onstart) this.onstart();
  };
}
window.SpeechRecognition = DeniedOnce;
window.webkitSpeechRecognition = DeniedOnce;
"""


def test_a_refused_microphone_can_be_asked_for_again(page):
    """A denial on the welcome screen used to kill voice for the whole session."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(DENIED_ONCE)
    open_lesson(tab, url)
    tab.wait_for_timeout(300)
    assert tab.evaluate("Tenfold.voice.denied") is True
    assert tab.inner_text("#lesson .mic .mic-label").strip().lower() == "type it"
    started = tab.evaluate("window.__voice.started")
    tab.click("#lesson .mic", force=True)
    tab.wait_for_timeout(300)
    assert tab.evaluate("Tenfold.voice.denied") is False
    assert tab.evaluate("window.__voice.started") > started
    assert tab.evaluate("Tenfold.voice.running") is True
    assert tab.inner_text("#lesson .mic .mic-label").strip().lower() == "listening"


def test_the_check_shows_the_typing_path_with_no_speech_api(page):
    """No recognition at all: step 3 asks for the answer typed, the pill carries the
    digits and a Backspace takes one back off."""
    context, url = page
    tab = context.new_page()
    errors = []
    tab.on("pageerror", lambda e: errors.append(str(e)))
    tab.add_init_script(NO_SPEECH_API)
    tab.goto(url + "#check", wait_until="domcontentloaded")
    tab.wait_for_selector("#check-mic", state="visible", timeout=30000)
    assert tab.text_content("#check-say").strip() == "Type the answer."
    assert tab.text_content("#check-miclabel").strip() == "Type it"
    tab.keyboard.type("36")
    assert tab.text_content("#check-miclabel").strip() == "36"
    tab.keyboard.press("Backspace")
    assert tab.text_content("#check-miclabel").strip() == "3"
    tab.keyboard.press("Backspace")
    assert tab.text_content("#check-miclabel").strip() == "Type it"
    assert errors == []


def test_the_camera_streams_only_while_it_is_on_screen(page):
    """/video never ends: an <img> with a src holds the connection open for ever."""
    context, url = page
    tab = context.new_page()
    tab.goto(url + "#home", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold")
    assert tab.get_attribute("#check-cam .practice-video", "src") is None
    open_lesson(tab, url)
    assert (tab.get_attribute("#lesson .practice-video", "src") or "").startswith("/video")
    assert tab.get_attribute("#check-cam .practice-video", "src") is None
    tab.click('#lesson [data-action="quit"]', force=True)
    tab.wait_for_timeout(300)
    assert tab.get_attribute("#lesson .practice-video", "src") is None


def test_the_counter_follows_the_exercise_not_the_fact(page):
    """u1-l2 is 6x7 and 7x6, one fact for all five questions. The counter hung on
    "1 of 5" and the bar on 0 percent for the whole lesson."""
    context, url = page
    tab = context.new_page()
    errors = []
    tab.on("pageerror", lambda e: errors.append(str(e)))
    tab.add_init_script(CAPTURE_SOCKET)
    open_lesson(tab, url)
    node = running_lesson(tab)
    # the lesson is already on its first exercise: two more questions, same fact
    for _ in range(2):
        feed(tab, node, state="correct_pose")
        feed(tab, node, state="answer_correct", answer=42, first_try=True)
        feed(tab, node, state="exercise_shown")
    assert tab.text_content("#lesson .practice-why") == "3 of 5"
    assert tab.evaluate("document.querySelector('#lesson .bar-fill').style.width") == "40%"
    assert tab.evaluate("Tenfold.lesson.done") == 3
    assert tab.evaluate("Tenfold.lesson.correct") == 2
    assert tab.evaluate("Tenfold.lesson.firstTry") == 2, "the same fact twice is still two first tries"
    assert errors == []


def test_first_try_comes_from_the_server(page):
    """The page stopped computing the bonus for itself: no pose_slip, no hint_level,
    no hint_auto, no lesson.slipped. The server holds the clock, the ladder and the
    params, and says first_try on the answer_correct message. The page adds 15 XP
    when it is there and nothing when it is not."""
    context, url = page
    tab = context.new_page()
    errors = []
    tab.on("pageerror", lambda e: errors.append(str(e)))
    tab.add_init_script(CAPTURE_SOCKET)
    open_lesson(tab, url)
    node = running_lesson(tab)
    assert tab.evaluate("Tenfold.lesson.slipped") is None, "the page keeps no slipped flag"

    # question one: the server says first try, the bonus is counted
    feed(tab, node, state="wrong_pose")
    feed(tab, node, state="correct_pose")
    feed(tab, node, state="answer_correct", answer=42, first_try=True)
    assert tab.evaluate("Tenfold.lesson.firstTry") == 1

    # question two: the server says no, whatever the old fields say
    feed(tab, node, state="exercise_shown")
    feed(tab, node, state="wrong_pose", pose_slip=True)
    feed(tab, node, state="answer_correct", answer=42, first_try=False)
    assert tab.evaluate("Tenfold.lesson.correct") == 2
    assert tab.evaluate("Tenfold.lesson.firstTry") == 1

    # question three: the old page lost the bonus on hint_level with hint_auto false.
    # Those fields are not read any more: the server's first_try is the whole answer.
    feed(tab, node, state="exercise_shown")
    feed(tab, node, state="wrong_pose", hint_level=3, hint_auto=False, pose_slip=True,
         hint={"hand": "left", "move_to": 7})
    feed(tab, node, state="answer_correct", answer=42, first_try=True)
    assert tab.evaluate("Tenfold.lesson.correct") == 3
    assert tab.evaluate("Tenfold.lesson.firstTry") == 2

    # a message from a server that does not know the field yet pays no bonus
    feed(tab, node, state="exercise_shown")
    feed(tab, node, state="answer_correct", answer=42)
    assert tab.evaluate("Tenfold.lesson.correct") == 4
    assert tab.evaluate("Tenfold.lesson.firstTry") == 2

    # a wrong answer still costs a heart on a boss, which is not first try business
    feed(tab, node, state="exercise_shown")
    feed(tab, node, state="answer_wrong")
    assert tab.evaluate("Tenfold.lesson.firstTry") == 2
    assert errors == []


def test_quitting_a_lesson_keeps_the_xp_already_earned(page):
    """SPEC: XP is never lost. Three right answers and a quit still pay."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(CAPTURE_SOCKET)
    open_lesson(tab, url)
    node = running_lesson(tab)
    before = tab.evaluate("Tenfold.xp")
    for _ in range(2):
        feed(tab, node, state="correct_pose")
        feed(tab, node, state="answer_correct", answer=42, first_try=True)
        feed(tab, node, state="exercise_shown")
    assert tab.evaluate("Tenfold.lesson.correct") == 2
    tab.click('#lesson [data-action="quit"]', force=True)
    tab.wait_for_timeout(400)
    assert tab.evaluate("Tenfold.lesson") is None
    assert tab.evaluate("Tenfold.xp") == before + 30, "two first try answers, no lesson bonus"


def test_the_back_button_leaves_the_running_lesson(page):
    """The lesson never touches the hash, so Back walks straight out of it. The node has
    to be quit, the shell hidden and Tally quiet over the next screen."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(CAPTURE_SOCKET)
    tab.goto(url + "#home", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold")
    tab.evaluate("() => { location.hash = 'practice'; }")
    tab.wait_for_selector('[data-action="start"]', timeout=10000)
    tab.click('[data-action="start"]', force=True)
    tab.wait_for_selector("#lesson .practice-stage", timeout=10000)
    tab.wait_for_function("!!Tenfold.lesson")
    tab.evaluate("""() => {
      window.__sent = [];
      const ws = window.__socket, send = ws.send.bind(ws);
      ws.send = (data) => { window.__sent.push(data); return send(data); };
    }""")
    tab.go_back()
    tab.wait_for_timeout(400)
    assert tab.evaluate("Tenfold.lesson") is None
    assert tab.is_hidden("#lesson")
    assert tab.evaluate("document.body.dataset.view") == "home"
    assert any("quit" in message for message in tab.evaluate("window.__sent")), "the node was left running"
    assert tab.get_attribute("#lesson .practice-video", "src") is None


def test_a_dropped_socket_comes_back_by_itself(page):
    """A restarted server used to leave the lesson on "Getting ready" and the camera on
    a frozen frame. The page reconnects, asks for the node again and re-requests the
    stream, and the XP already earned is banked before the counters go back to zero."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(CAPTURE_SOCKET)
    open_lesson(tab, url)
    tab.wait_for_function("!!Tenfold.lesson")
    tab.wait_for_function("document.querySelector('#lesson .practice-why').textContent === '1 of 5'")
    node = tab.evaluate("Tenfold.lesson.node.id")
    feed(tab, node, state="correct_pose")
    feed(tab, node, state="answer_correct", answer=42, first_try=True)
    assert tab.evaluate("Tenfold.lesson.correct") == 1
    before = tab.get_attribute("#lesson .practice-video", "src")

    tab.evaluate("() => { window.__old = window.__socket; window.__socket.close(); }")
    tab.wait_for_function("window.__socket !== window.__old && window.__socket.readyState === 1", timeout=15000)
    tab.wait_for_function("document.querySelector('#lesson .practice-why').textContent === '1 of 5'", timeout=15000)
    assert tab.get_attribute("#lesson .practice-video", "src") != before, "the stream was never re-requested"
    assert tab.evaluate("Tenfold.xp") == 15, "the answer already right is paid before the node restarts"
    assert tab.evaluate("Tenfold.lesson.correct") == 0


# ---------- the tutor: what the page sends, says and draws ----------

# The same watcher, installed before the page script runs.
CAPTURE_SENDS = "window.__sent = [];\n" + WATCH_SENDS

# A browser with no speech synthesis at all, which is the case the tts pair still
# has to cover: the property is removed from the window and from its prototype.
NO_SYNTH = """
try { delete window.speechSynthesis; } catch (e) { /* nothing to remove */ }
try { delete Window.prototype.speechSynthesis; } catch (e) { /* nothing to remove */ }
"""

TUTOR_FINGERS = [
    {"hand": "left", "number": 8, "x": 0.30, "y": 0.50},
    {"hand": "left", "number": 9, "x": 0.38, "y": 0.42},
    {"hand": "right", "number": 7, "x": 0.70, "y": 0.50},
    {"hand": "right", "number": 9, "x": 0.78, "y": 0.42},
]


def tutor_page(context, url, extra: str = ""):
    """A page on the first lesson, with a fake voice, its socket under control."""
    tab = context.new_page()
    tab.add_init_script(CAPTURE_SOCKET + CAPTURE_SENDS + voices([("Samantha", "en-US")])
                        + FAKE_SYNTH + extra)
    errors = []
    tab.on("pageerror", lambda e: errors.append(str(e)))
    open_lesson(tab, url)
    node = running_lesson(tab)
    tab.evaluate("() => { window.__sent.length = 0; window.__spoken.length = 0; }")
    return tab, node, errors


def test_the_tts_pair_brackets_the_whole_spoken_line(page):
    """The server's clocks start at the end of Tally's line, so the pair has to wrap
    the line, not each sentence of it."""
    context, url = page
    tab, node, errors = tutor_page(context, url)
    feed(tab, node, state="wrong_pose", tutor_line="Almost. Your right hand needs 7, not 9.")
    tab.wait_for_timeout(200)
    assert [u["text"] for u in tab.evaluate("window.__spoken")] == [
        "Almost.", "Your right hand needs 7, not 9."]
    assert [m["speaking"] for m in sent(tab, "tts")] == [True, False], "one pair, two sentences"
    # the next line is its own pair
    feed(tab, node, state="wrong_pose", tutor_line="Try your 7.")
    tab.wait_for_timeout(200)
    assert [m["speaking"] for m in sent(tab, "tts")] == [True, False, True, False]

    # a line cut off by the way out of the lesson still closes its pair: without it
    # the next line would open without one and the tutor's clock would never start
    tab.evaluate("() => { window.__endUpTo = 0; window.__sent.length = 0; }")
    feed(tab, node, state="wrong_pose", tutor_line="Hold them up for me.")
    tab.wait_for_timeout(200)
    assert [m["speaking"] for m in sent(tab, "tts")] == [True], "the engine never ended it"
    tab.click('#lesson [data-action="quit"]', force=True)
    tab.wait_for_timeout(300)
    assert [m["speaking"] for m in sent(tab, "tts")][:2] == [True, False]
    assert errors == []


def test_the_tts_pair_is_sent_when_tally_is_muted(page):
    """Muted is the child's choice, not a hole in the clock: the bubble still shows,
    the pair still goes out, back to back with nothing in between."""
    context, url = page
    tab, node, errors = tutor_page(context, url)
    tab.click("#mute")
    assert tab.evaluate("Tenfold.muted") is True
    tab.evaluate("() => { window.__sent.length = 0; }")
    feed(tab, node, state="wrong_pose", tutor_line="Your right hand needs 7.")
    tab.wait_for_timeout(200)
    assert tab.evaluate("window.__spoken") == [], "muted says nothing"
    assert tab.text_content("#lesson .tally-say") == "Your right hand needs 7."
    assert [m["speaking"] for m in sent(tab, "tts")] == [True, False]
    assert errors == []


def test_the_tts_pair_is_sent_with_no_speech_synthesis_at_all(page):
    """A browser with no engine has to keep the tutor's clock honest too."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(NO_SYNTH + CAPTURE_SOCKET + CAPTURE_SENDS)
    errors = []
    tab.on("pageerror", lambda e: errors.append(str(e)))
    open_lesson(tab, url)
    node = running_lesson(tab)
    assert tab.evaluate("'speechSynthesis' in window") is False
    tab.evaluate("() => { window.__sent.length = 0; }")
    feed(tab, node, state="wrong_pose", tutor_line="Your right hand needs 7.")
    tab.wait_for_timeout(200)
    assert tab.text_content("#lesson .tally-say") == "Your right hand needs 7."
    assert [m["speaking"] for m in sent(tab, "tts")] == [True, False]
    assert errors == []


def test_the_child_speaking_is_sent_with_or_without_a_number(page):
    """Everything the child says goes up: it is what tells the tutor she is working.
    A number still submits an answer as well, and never instead."""
    context, url = page
    tab, node, errors = tutor_page(context, url, extra=FAKE_RECOGNITION)
    # the answer window, which speech does not need but the check message does
    tab.evaluate("() => Tenfold.voice.gate = true")
    tab.evaluate("() => window.__say('is it fifty six', true)")
    tab.wait_for_timeout(150)
    assert [m["text"] for m in sent(tab, "speech")] == ["is it fifty six"]
    assert [m["value"] for m in sent(tab, "check")] == [56], "the answer is still submitted"
    # a sentence with no number in it is sent all the same
    tab.evaluate("() => window.__say('I do not know this one', true)")
    tab.wait_for_timeout(150)
    assert [m["text"] for m in sent(tab, "speech")] == ["is it fifty six", "I do not know this one"]
    assert [m["value"] for m in sent(tab, "check")] == [56], "speech never submits an answer"
    # an interim result is not the child speaking yet
    tab.evaluate("() => window.__say('maybe', false)")
    tab.wait_for_timeout(150)
    assert len(sent(tab, "speech")) == 2
    assert errors == []


def test_tallys_own_line_is_never_the_child_speaking(page):
    """The echo guard stays: what Tally just said, heard back through the microphone,
    is not forwarded and is not an answer."""
    context, url = page
    tab, node, errors = tutor_page(context, url, extra=FAKE_RECOGNITION)
    tab.evaluate("() => Tenfold.voice.gate = true")
    # the engine never ends the utterance, so Tally is still talking when it comes back
    tab.evaluate("() => { window.__endUpTo = 0; }")
    feed(tab, node, state="wrong_pose", tutor_line="Move your right finger to seven.")
    tab.wait_for_timeout(100)
    tab.evaluate("() => window.__say('move your right finger to seven', true)")
    tab.evaluate("() => window.__say('seven', true)")
    tab.wait_for_timeout(150)
    assert sent(tab, "speech") == [], "Tally heard himself"
    assert sent(tab, "check") == []
    # the child speaking over him is still the child
    tab.evaluate("() => window.__say('I am stuck', true)")
    tab.wait_for_timeout(150)
    assert [m["text"] for m in sent(tab, "speech")] == ["I am stuck"]
    assert errors == []


def test_the_tutor_line_is_spoken_once_and_shown_in_the_bubble(page):
    """tutor_line is the line Tally says and shows. The same line arriving on every
    frame is said once; the fallback is the engine's tally phrase."""
    context, url = page
    tab, node, errors = tutor_page(context, url)
    for _ in range(3):
        feed(tab, node, state="wrong_pose", tally="ignored while the tutor talks",
             tutor_line="Your right hand needs 7, not 9.")
    tab.wait_for_timeout(200)
    assert tab.text_content("#lesson .tally-say") == "Your right hand needs 7, not 9."
    assert [u["text"] for u in tab.evaluate("window.__spoken")] == ["Your right hand needs 7, not 9."]
    # no tutor_line: the engine's phrase is the line, and it is spoken too
    feed(tab, node, state="wrong_pose", tally="Move your right finger from 9 to 7.")
    tab.wait_for_timeout(200)
    assert tab.text_content("#lesson .tally-say") == "Move your right finger from 9 to 7."
    assert [u["text"] for u in tab.evaluate("window.__spoken")] == [
        "Your right hand needs 7, not 9.", "Move your right finger from 9 to 7."]
    assert errors == []


VISUALS = [
    ({"kind": "pulse_finger", "hand": "right", "finger": 7}, ".practice-overlay .pulse"),
    ({"kind": "correction", "wrong_hand": "right", "expected_finger": 7}, ".practice-overlay .hand-box"),
    ({"kind": "ghost", "hand": "right", "from": 9, "to": 7}, ".practice-overlay .ghost"),
    ({"kind": "rescue_card", "tens": 5, "units": 6, "total": 56}, ".practice-rescue span"),
    ({"kind": "placement_zones"}, ".practice-overlay .zone-box"),
    ({"kind": "finger_numbers"}, ".practice-overlay text.lit"),
]


def test_every_tutor_visual_renders(page):
    """The six kinds of tutor_visual, each drawn inside the camera view, none of them
    throwing. Null draws nothing beyond the finger circles the page already draws."""
    context, url = page
    tab, node, errors = tutor_page(context, url)
    for visual, selector in VISUALS:
        feed(tab, node, state="wrong_pose", exercise="8 x 7", fingers=TUTOR_FINGERS,
             tutor_visual=visual)
        found = tab.eval_on_selector_all(f"#lesson {selector}", "els => els.length")
        assert found >= 1, f"{visual['kind']} drew nothing"
    # the correction marks the finger the wrong hand needs, and leaves the other alone
    feed(tab, node, state="wrong_pose", exercise="8 x 7", fingers=TUTOR_FINGERS,
         tutor_visual={"kind": "correction", "wrong_hand": "right", "expected_finger": 7})
    assert tab.eval_on_selector_all("#lesson .practice-overlay .dot", "els => els.length") == 1
    assert tab.eval_on_selector_all("#lesson .practice-overlay .hand-box", "els => els.length") == 1
    # the rescue card walks the whole method through, in three lines
    feed(tab, node, state="wrong_pose", exercise="8 x 7", fingers=TUTOR_FINGERS,
         tutor_visual={"kind": "rescue_card", "tens": 5, "units": 6, "total": 56})
    assert tab.eval_on_selector_all("#lesson .practice-rescue span", "els => els.map((e) => e.textContent)") == [
        "5 tens = 50", "2 x 3 = 6", "50 + 6 = 56"]
    # null: the finger circles and nothing else, and the card is gone
    feed(tab, node, state="wrong_pose", exercise="8 x 7", fingers=TUTOR_FINGERS, tutor_visual=None)
    assert tab.eval_on_selector_all(
        "#lesson .practice-overlay .ring, #lesson .practice-overlay .zone-box, "
        "#lesson .practice-overlay .hand-box, #lesson .practice-overlay .dot",
        "els => els.length") == 0
    assert tab.is_hidden("#lesson .practice-rescue")
    assert errors == []


def test_the_ghost_follows_the_tutor_not_the_hint_level(page):
    """hint_level is no longer a rendering input: only tutor_visual draws the ghost."""
    context, url = page
    tab, node, errors = tutor_page(context, url)
    feed(tab, node, state="wrong_pose", fingers=TUTOR_FINGERS, hint_level=3,
         hint={"hand": "right", "move_from": 9, "move_to": 7})
    assert tab.eval_on_selector_all("#lesson .practice-overlay .ghost", "els => els.length") == 0
    feed(tab, node, state="wrong_pose", fingers=TUTOR_FINGERS, hint_level=0,
         tutor_visual={"kind": "ghost", "hand": "right", "from": 9, "to": 7})
    assert tab.eval_on_selector_all("#lesson .practice-overlay .ghost", "els => els.length") == 1
    assert errors == []


def test_a_tap_on_tally_asks_for_help(page):
    """No new button: Tally himself and his bubble are the ask. The server already
    accepts hint, and it is the only help that costs the first try bonus."""
    context, url = page
    tab, node, errors = tutor_page(context, url)
    tab.click("#lesson .speaker .tally", force=True)
    tab.wait_for_timeout(100)
    assert [m["type"] for m in sent(tab, "hint")] == ["hint"]
    tab.click("#lesson .speech", force=True)
    tab.wait_for_timeout(100)
    assert len(sent(tab, "hint")) == 2, "the bubble asks too"
    assert errors == []


# Every bubble that carries a line of Tally's, screen by screen. A bubble on
# screen with text in it and no voice behind it is the bug this list catches.
BUBBLES = [
    "#check-say", "#u-say", "#lesson .tally-say",
    "#fn-1 h1", "#fn-1 .sub", "#fn-2 .newlevel", "#fn-2 h1", "#fn-2 .earned", "#fn-2 .sub",
]

SILENT_BUBBLES = """
(selectors) => {
  const said = window.__spoken.map((u) => u.text).join(" ").replace(/\\s+/g, " ");
  const visible = (el) => {
    if (!el || !el.getClientRects().length) return false;
    for (let n = el; n instanceof Element; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (s.display === "none" || s.visibility === "hidden" || Number(s.opacity) === 0) return false;
    }
    return true;
  };
  const missing = [];
  selectors.forEach((sel) => {
    const el = document.querySelector(sel);
    if (!visible(el)) return;
    const text = (el.textContent || "").replace(/\\s+/g, " ").trim().replace(/[.!?]+$/, "");
    if (!text) return;
    if (!said.includes(text)) missing.push(sel + ": " + text);
  });
  return missing;
}
"""


def silent(tab):
    return tab.evaluate(SILENT_BUBBLES, BUBBLES)


def test_every_bubble_shown_is_spoken(page):
    """The rule, walked screen by screen: a bubble that appears without Tally saying
    it is a bug. Every visible line has to have gone through speech synthesis."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(FAKE_RECOGNITION + voices([("Samantha", "en-US")]) + FAKE_SYNTH + CAPTURE_SOCKET)
    errors = []
    tab.on("pageerror", lambda e: errors.append(str(e)))
    tab.goto(url + "#home", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold")

    # the start check, three steps driven by the camera
    tab.click("a.door[href='#check']")
    tab.wait_for_selector("#check-mic", state="visible", timeout=30000)
    tab.wait_for_timeout(300)
    assert silent(tab) == []

    # the practice card
    tab.evaluate("() => { location.hash = 'practice'; }")
    tab.wait_for_selector('[data-action="start"]', timeout=10000)
    tab.wait_for_timeout(300)
    assert silent(tab) == []

    # the lesson bubble: the line it opens on, then every line the tutor sends
    tab.click('[data-action="start"]', force=True)
    tab.wait_for_selector("#lesson .practice-stage", timeout=10000)
    node = running_lesson(tab)
    tab.wait_for_timeout(300)
    assert silent(tab) == []
    feed(tab, node, state="wrong_pose", tutor_line="Your right hand needs 7, not 9.")
    tab.wait_for_timeout(300)
    assert silent(tab) == []

    # the finish card, and the level up card behind it: 150 XP crosses level two
    for _ in range(5):
        feed(tab, node, state="correct_pose")
        feed(tab, node, state="answer_correct", answer=42, first_try=True)
        feed(tab, node, state="exercise_shown")
    tab.evaluate("(id) => window.__feed({ type: 'node_end', node_id: id, correct: 5, total: 5, tally: 'Lovely work.' })", node)
    tab.wait_for_selector("#finish", state="visible", timeout=10000)
    tab.wait_for_timeout(400)
    assert silent(tab) == []
    tab.wait_for_selector("#fn-2.show", timeout=10000)
    tab.wait_for_timeout(500)
    assert silent(tab) == []
    assert errors == []


# ---------------------------------------------------------------------------
# The v3 design: Tally and the course furniture are 3D renders in web/course/art/.
# These live here because this is the file with the browser harness: the mapping
# from an expression to a render, and the way an accessory is laid on top of it,
# only exist once the page has run.
# ---------------------------------------------------------------------------

EXPRESSIONS = ["ready", "thinking", "almost", "happy", "squint", "hands"]


def test_every_tally_expression_has_a_render(page):
    """Six expressions, fewer renders: each one still resolves to a file in art/,
    and every mounted Tally carries that file as its base image."""
    context, url = page
    tab = context.new_page()
    errors = []
    tab.on("pageerror", lambda e: errors.append(str(e)))
    tab.goto(url + "#home", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tally")
    faces = tab.evaluate("window.Tally.faces")
    assert sorted(faces) == sorted(EXPRESSIONS)
    for expression, render in faces.items():
        assert render.endswith(".png"), f"{expression} is not a render"
        response = tab.request.get(f"{url}/art/{render}")
        assert response.status == 200, f"{expression} points at a missing {render}"
    # one host, every expression in turn: the base image follows the table
    for expression in EXPRESSIONS:
        tab.evaluate("(e) => Tally.set(document.querySelector('#home .tally'), e)", expression)
        src = tab.get_attribute("#home .tally .t-base", "src")
        assert src == f"art/{faces[expression]}", expression
    assert errors == []


def test_the_accessories_layer_over_tally_with_the_cape_behind(page):
    """The accessories are placed on the render, not drawn into it: the cape hangs
    behind Tally and everything else sits on top of him."""
    context, url = page
    tab = context.new_page()
    errors = []
    tab.on("pageerror", lambda e: errors.append(str(e)))
    tab.goto(url + "#profile", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tally")
    tab.evaluate("() => Tally.set(document.querySelector('#pf-tally'), 'happy', 'glasses cape crown')")
    order = tab.eval_on_selector_all(
        "#pf-tally .tally-art > *",
        "els => els.map((e) => e.className)")
    assert order[0].startswith("t-acc t-acc-cape"), f"the cape is not behind Tally: {order}"
    assert order[1] == "t-base", f"Tally is not on top of his cape: {order}"
    assert [c for c in order[2:]] == ["t-acc t-acc-glasses", "t-acc t-acc-crown"]
    # each one is placed and sized against the render, in percent of its box
    placed = tab.eval_on_selector_all(
        "#pf-tally .t-acc",
        "els => els.map((e) => [e.style.left, e.style.top, e.style.width].join('|'))")
    assert all("%" in p and "||" not in p for p in placed), placed
    # and an accessory Tally has not earned is not in the drawing at all
    tab.evaluate("() => Tally.set(document.querySelector('#pf-tally'), 'happy', 'glasses')")
    assert tab.eval_on_selector_all("#pf-tally .t-acc", "els => els.length") == 1
    assert errors == []


def test_the_path_nodes_are_the_renders(page):
    """Done, locked, boss and the current node that says START: one render each,
    and the current node is the way into the lesson."""
    context, url = page
    tab = context.new_page()
    errors = []
    tab.on("pageerror", lambda e: errors.append(str(e)))
    tab.goto(url + "#practice", wait_until="domcontentloaded")
    tab.wait_for_selector('[data-action="start"]', timeout=10000)
    art = tab.eval_on_selector_all(
        "#course .node",
        "els => els.map((e) => [e.dataset.status, (e.querySelector('.art') || {}).style?.backgroundImage || 'icon'])")
    assert art, "the path drew no nodes"
    for status, image in art:
        if image == "icon":
            continue                      # the chest keeps the drawn icon of icons.svg
        expected = {"current": "start.png", "done": "n-done.png", "locked": "n-lock.png"}[status]
        assert expected in image, f"{status} node drawn with {image}"
    # the render carries the START bubble, so the node itself opens the lesson
    assert tab.get_attribute('#course .node.is-current', "data-action") == "start"
    assert errors == []


def test_the_check_steps_are_the_rendered_tiles(page):
    """The three steps of the start check are the numbered tiles, lit one at a time."""
    context, url = page
    tab = context.new_page()
    errors = []
    tab.on("pageerror", lambda e: errors.append(str(e)))
    tab.goto(url + "#check", wait_until="domcontentloaded")
    tab.wait_for_selector("#check .stepnum img", timeout=10000)
    lit = tab.eval_on_selector_all("#check .stepnum img", "els => els.map((e) => e.getAttribute('src'))")
    assert lit == ["art/tile-1-on.png", "art/tile-2-off.png", "art/tile-3-off.png"]
    for image in lit:
        assert tab.request.get(f"{url}/{image}").status == 200, image
    assert errors == []


def test_the_profile_shows_the_gear_tally_has_earned(page):
    """Five accessories, one slot each: the ones he wears are lit, the rest wait."""
    context, url = page
    tab = context.new_page()
    errors = []
    tab.on("pageerror", lambda e: errors.append(str(e)))
    tab.goto(url + "#home", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold")
    # level 4 wears the glasses of level 2 and the headband of level 4, nothing else
    tab.evaluate("() => localStorage.setItem('tenfold.learner', JSON.stringify({ xp: 500 }))")
    tab.goto(url + "#profile", wait_until="domcontentloaded")
    tab.wait_for_selector("#pf-wardrobe .slot", timeout=10000)
    slots = tab.eval_on_selector_all(
        "#pf-wardrobe .slot",
        "els => els.map((e) => [e.querySelector('img').getAttribute('src'), e.classList.contains('on')])")
    assert [s[0] for s in slots] == [
        "art/acc-glasses.png", "art/acc-headband.png", "art/acc-hat.png",
        "art/acc-cape.png", "art/acc-crown.png"]
    assert [s[1] for s in slots] == [True, True, False, False, False]
    assert tab.eval_on_selector_all("#pf-tally .t-acc", "els => els.length") == 2
    assert errors == []
