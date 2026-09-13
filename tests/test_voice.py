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
    tab.evaluate("""() => {
      window.__sent = 0;
      const original = WebSocket.prototype.send;
      WebSocket.prototype.send = function (data) { window.__sent += 1; return original.call(this, data); };
    }""")
    tab.evaluate("() => window.__say('thirty six', true)")
    tab.wait_for_timeout(300)
    assert tab.evaluate("window.__sent") == 0


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
    tab.evaluate("""() => {
      window.__sent = 0;
      const original = WebSocket.prototype.send;
      WebSocket.prototype.send = function (data) { window.__sent += 1; return original.call(this, data); };
      Tenfold.voice.said = [36]; Tenfold.voice.spokeUntil = Date.now() + 5000;
      window.__say('thirty six', true);
      window.__say('eleven', true);
    }""")
    tab.wait_for_timeout(300)
    assert tab.evaluate("window.__sent") == 1


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
    tab.evaluate("""() => {
      window.__sent = 0;
      const original = WebSocket.prototype.send;
      WebSocket.prototype.send = function (data) { window.__sent += 1; return original.call(this, data); };
    }""")
    tab.evaluate("() => { window.__say('eleven', true); window.__say('eleven', true); }")
    tab.wait_for_timeout(400)
    assert tab.evaluate("window.__sent") == 1


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
        feed(tab, node, state="answer_correct", answer=42)
        feed(tab, node, state="exercise_shown")
    assert tab.text_content("#lesson .practice-why") == "3 of 5"
    assert tab.evaluate("document.querySelector('#lesson .bar-fill').style.width") == "40%"
    assert tab.evaluate("Tenfold.lesson.done") == 3
    assert tab.evaluate("Tenfold.lesson.correct") == 2
    assert tab.evaluate("Tenfold.lesson.firstTry") == 2, "the same fact twice is still two first tries"
    assert errors == []


def test_a_wrong_pose_keeps_the_first_try_bonus_but_a_pose_slip_does_not(page):
    """Fixing a finger is how the input works: the server gives the star, so the page
    keeps the 15 XP. What costs it is a pose still wrong after the server's grace
    (pose_slip), a hint the child asked for, or a wrong answer. A hint the server
    raised by itself does not."""
    context, url = page
    tab = context.new_page()
    errors = []
    tab.on("pageerror", lambda e: errors.append(str(e)))
    tab.add_init_script(CAPTURE_SOCKET)
    open_lesson(tab, url)
    node = running_lesson(tab)

    # question one: a wrong pose, then the right answer, bonus kept
    feed(tab, node, state="wrong_pose")
    assert tab.evaluate("Tenfold.lesson.slipped") is False
    feed(tab, node, state="correct_pose")
    feed(tab, node, state="answer_correct", answer=42)
    assert tab.evaluate("Tenfold.lesson.firstTry") == 1

    # question two: the pose is still wrong after the grace, bonus lost
    feed(tab, node, state="exercise_shown")
    assert tab.evaluate("Tenfold.lesson.slipped") is False, "a new question starts clean"
    feed(tab, node, state="wrong_pose", pose_slip=True)
    assert tab.evaluate("Tenfold.lesson.slipped") is True
    feed(tab, node, state="correct_pose")
    feed(tab, node, state="answer_correct", answer=42)
    assert tab.evaluate("Tenfold.lesson.correct") == 2
    assert tab.evaluate("Tenfold.lesson.firstTry") == 1

    # question three: the hint the server raises by itself after ten seconds keeps the
    # bonus, and the ghost finger it carries has to draw without breaking anything
    feed(tab, node, state="exercise_shown")
    feed(tab, node, state="wrong_pose", hint_level=2, hint_auto=True,
         hint={"hand": "left", "move_to": 7},
         fingers=[{"hand": "left", "number": 6, "x": 0.4, "y": 0.5},
                  {"hand": "left", "number": 7, "x": 0.5, "y": 0.5}])
    assert tab.evaluate("Tenfold.lesson.slipped") is False, "the child never asked for it"
    # one circle per finger plus the ghost ring on the finger the hint points at
    assert tab.evaluate("document.querySelectorAll('#lesson .practice-overlay circle').length") == 3
    feed(tab, node, state="answer_correct", answer=42)
    assert tab.evaluate("Tenfold.lesson.correct") == 3
    assert tab.evaluate("Tenfold.lesson.firstTry") == 2

    # question four: a hint the child asked for costs the bonus
    feed(tab, node, state="exercise_shown")
    feed(tab, node, state="wrong_pose", hint_level=1, hint_auto=False)
    assert tab.evaluate("Tenfold.lesson.slipped") is True

    # question five: a wrong answer costs it too, exactly as before
    feed(tab, node, state="exercise_shown")
    feed(tab, node, state="answer_wrong")
    assert tab.evaluate("Tenfold.lesson.slipped") is True
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
        feed(tab, node, state="answer_correct", answer=42)
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
    feed(tab, node, state="answer_correct", answer=42)
    assert tab.evaluate("Tenfold.lesson.correct") == 1
    before = tab.get_attribute("#lesson .practice-video", "src")

    tab.evaluate("() => { window.__old = window.__socket; window.__socket.close(); }")
    tab.wait_for_function("window.__socket !== window.__old && window.__socket.readyState === 1", timeout=15000)
    tab.wait_for_function("document.querySelector('#lesson .practice-why').textContent === '1 of 5'", timeout=15000)
    assert tab.get_attribute("#lesson .practice-video", "src") != before, "the stream was never re-requested"
    assert tab.evaluate("Tenfold.xp") == 15, "the answer already right is paid before the node restarts"
    assert tab.evaluate("Tenfold.lesson.correct") == 0
