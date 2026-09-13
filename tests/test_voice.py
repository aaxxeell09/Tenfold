"""Voice answers in the camera lesson of web/course/, driven in a real browser.

Headless Chromium ships no speech recognition, which is exactly the case the
page has to survive: the microphone stays hidden and the keyboard still works.
A fake recogniser is then injected to exercise the rest, because the parser and
the listening window are the parts that can silently submit a wrong answer.
The same browser is used for the answer paths around voice: the digits typed in
the pill, the question counter, the first try bonus and the XP a child keeps
when quitting, none of which can be exercised without a real page.

One event is shown once: the lesson's answer field is the export's mic pill, and
the number heard, the digits typed and the listening state all show there. The
"heard:" readout is a developer's line and only comes back with ?debug=1.

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
  // A silenced socket never hands the server's own messages to the page: its handler is
  // kept aside for __feed and the real onmessage is left empty. A socket the page opens
  // again after a drop is silenced the same way, or a reconnect in the middle of a test
  // puts the live stream back behind the test's back.
  function hush(ws) {
    if (ws.__hushed) return;
    ws.__hushed = true;
    let stored = ws.onmessage || null;
    if (stored) window.__page_onmessage = stored;
    ws.onmessage = null;
    Object.defineProperty(ws, "onmessage", {
      configurable: true,
      get: () => stored,
      set: (fn) => { stored = fn; window.__page_onmessage = fn; },
    });
  }
  function Wrapped(url, protocols) {
    const ws = protocols === undefined ? new Original(url) : new Original(url, protocols);
    window.__socket = ws;
    if (window.__silenced) hush(ws);
    return ws;
  }
  Wrapped.prototype = Original.prototype;
  ["CONNECTING", "OPEN", "CLOSING", "CLOSED"].forEach((name, i) => { Wrapped[name] = i; });
  window.WebSocket = Wrapped;
  window.__silence = () => { window.__silenced = true; if (window.__socket) hush(window.__socket); };
  window.__feed = (message) => (window.__page_onmessage || window.__socket.onmessage)({ data: JSON.stringify(message) });
})();
"""

# The same capture, silenced before the page has opened its socket at all. Silencing
# from the test leaves a gap the width of one round trip, and the camera can walk a
# whole step of the check through it; this way no server message ever lands.
SILENT_SOCKET = CAPTURE_SOCKET + "\nwindow.__silenced = true;\n"

# The screens of the design export are wired one at a time, and app.js stops at the
# first element a view it sets up has not got yet: nothing after it runs, the check
# included. This hands back a detached stand in for those elements and only for those,
# so the screen under test boots. It answers with the real element the moment the view
# that owns it is wired, and comes out when the last one is.
MISSING_VIEWS = """
(() => {
  const stubs = { "#welcome-form": "form", "#welcome-name": "input" };
  const find = Document.prototype.querySelector;
  Document.prototype.querySelector = function (selector) {
    const found = find.call(this, selector);
    if (found || this !== document || !(selector in stubs)) return found;
    return document.createElement(stubs[selector]);
  };
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


def mine(errors):
    """The errors the check is answerable for.

    A view that has not been wired yet still carries the demo script the export wrote
    for its own page, and those scripts throw on globals that only their page had. They
    go with the script, in the pass that wires the view.
    """
    return [error for error in errors if "TF is not defined" not in error]


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
    tab.wait_for_function("document.querySelector('#lesson .count').textContent === '1 of 5'")
    tab.evaluate("() => window.__silence()")
    return tab.evaluate("Tenfold.lesson.node.id")


ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty", 6: "sixty",
        7: "seventy", 8: "eighty", 9: "ninety"}


def open_lesson(tab, url):
    """Open the first node, which is where voice lives. The camera lesson is asked for
    by name rather than tapped through the path: the two screens are wired by different
    hands, and a failure here should be this screen's own. The tap on the path is still
    walked, once, by test_every_bubble_shown_is_spoken."""
    tab.goto(url + "#practice", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold", timeout=10000)
    tab.evaluate("() => Tenfold.startLesson(TenfoldLevels.allNodes()[0].id)")
    tab.wait_for_selector("#lesson .cam", timeout=10000)


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
      if (el.classList && el.classList.contains("cam")) window.__states.push(el.dataset.state);
    }
  }).observe(host, { attributes: true, attributeFilter: ["data-state"], subtree: true });
});
"""


def saw_state(tab, state: str, timeout: int = 30000):
    """Wait for a state the page went through, live or already passed."""
    tab.wait_for_function(f"window.__states.includes('{state}')", timeout=timeout)


def stage_state(tab) -> str:
    return tab.evaluate("document.querySelector('#lesson .cam')?.dataset.state || ''")


def wait_for_state(tab, state: str, timeout: int = 30000):
    tab.wait_for_function(
        f"document.querySelector('#lesson .cam')?.dataset.state === '{state}'",
        timeout=timeout)


def pill(tab) -> str:
    """What the lesson's answer pill says: listening, the digits typed, or the number
    heard. It is the only place in the lesson an answer is shown."""
    return tab.inner_text("#lesson .mic .mic-label").strip()


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


def test_without_the_api_the_pill_is_the_typing_field(page):
    """Firefox and Safari have no speech recognition. Keyboard only, no error, and the
    pill stays on screen because it is where the digits show."""
    context, url = page
    tab = context.new_page()
    errors = []
    tab.on("pageerror", lambda e: errors.append(str(e)))
    tab.add_init_script(NO_SPEECH_API)
    open_lesson(tab, url)
    tab.wait_for_timeout(500)
    assert tab.evaluate("Tenfold.voice.recognition") is None
    assert tab.is_visible("#lesson .mic")
    assert pill(tab).lower() == "type it"
    # the keyboard has to keep working with no speech API at all
    tab.keyboard.type("42")
    assert pill(tab) == "42"
    assert tab.is_hidden("#lesson .typed"), "the big number beside the pill is gone"
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
    assert pill(tab).lower() == "type it"
    assert tab.is_hidden("#lesson .caret"), "the caret went with the big number"
    tab.keyboard.type("42")
    assert pill(tab) == "42"


def test_a_spoken_answer_is_sent_and_shown(page):
    context, url = page
    tab = context.new_page()
    tab.add_init_script(FAKE_RECOGNITION)
    tab.add_init_script(STATE_LOG)
    open_lesson(tab, url)
    wait_for_state(tab, "correct_pose")

    exercise = tab.inner_text("#lesson .ex").replace("×", "x")
    a, b = (int(part.strip()) for part in exercise.split(" x "))

    # an interim result with no number in it moves nothing: the pill keeps listening
    tab.evaluate("() => window.__say('um', false)")
    tab.wait_for_timeout(150)
    assert pill(tab).lower() == "listening"
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


def test_after_a_wrong_spoken_answer_the_next_spoken_answer_is_heard(page):
    """Right pose, wrong answer out loud, right answer out loud: the second try has to
    reach the server with no keyboard. answer_wrong keeps the pose latched and waits,
    so the listening window has to stay open on it."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(FAKE_RECOGNITION)
    tab.add_init_script(CAPTURE_SOCKET)
    open_lesson(tab, url)
    node = running_lesson(tab)
    tab.evaluate("""() => {
      window.__checks = [];
      const ws = window.__socket, send = ws.send.bind(ws);
      ws.send = (data) => { const m = JSON.parse(data); if (m.type === 'check') window.__checks.push(m.value); return send(data); };
    }""")

    feed(tab, node, state="correct_pose", exercise="6 x 7")
    assert tab.evaluate("Tenfold.voice.gate") is True
    tab.evaluate("() => window.__say('fifty two', true)")
    feed(tab, node, state="answer_wrong", exercise="6 x 7",
         reaction="recount_units", tally="Almost. Count the units again.")
    assert stage_state(tab) == "answer_wrong"
    assert tab.evaluate("Tenfold.voice.gate") is True, "the microphone closed on the wrong answer"

    tab.evaluate("() => window.__say('forty two', true)")
    assert tab.evaluate("window.__checks") == [52, 42]
    assert pill(tab) == "42", "the number heard is shown in the pill"

    # once the server moves on, a number heard is no longer an answer
    feed(tab, node, state="answer_correct", exercise="6 x 7", answer=42)
    feed(tab, node, state="exercise_shown", exercise="7 x 7")
    assert tab.evaluate("Tenfold.voice.gate") is False
    tab.evaluate("() => window.__say('forty nine', true)")
    assert tab.evaluate("window.__checks") == [52, 42]


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


# A fake speechSynthesis: records every utterance with the moment it went out, ends each
# one on the next tick so the queue advances the way a real engine would, and takes a
# voice list from the test. __endUpTo holds an utterance open (the engine never reports
# the end) and __finish() then ends the oldest one by hand, which is how a line that is
# still playing is kept playing for as long as a test needs it.
FAKE_SYNTH = """
window.__spoken = []; window.__cancelled = 0; window.__pending = [];
window.__voices = (window.__voiceList || []).map((v) => Object.assign({ localService: true, default: false, voiceURI: v.name }, v));
function SpeechSynthesisUtterance(text) { this.text = text; this.voice = null; this.rate = 1; this.pitch = 1; this.lang = ""; }
window.SpeechSynthesisUtterance = SpeechSynthesisUtterance;
// the real property has no setter: a plain assignment is silently ignored
Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
  getVoices: () => window.__voices,
  cancel: () => { window.__cancelled += 1; },
  speak: (u) => {
    window.__spoken.push({ text: u.text, voice: u.voice && u.voice.name, rate: u.rate, pitch: u.pitch, lang: u.lang, at: Date.now() });
    window.__pending.push(u);
    if (u.onstart) u.onstart();
    setTimeout(() => {
      if (u.onend && window.__spoken.length <= (window.__endUpTo ?? Infinity)) {
        window.__pending = window.__pending.filter((p) => p !== u);
        u.onend();
      }
    }, 0);
  },
  addEventListener: (name, fn) => { (window.__onvoices = window.__onvoices || []).push(fn); },
} });
window.__finish = () => { const u = window.__pending.shift(); if (u && u.onend) u.onend(); };
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
    # the two sentences belong to one line: they go out back to back, with no beat
    assert [s["text"] for s in spoken] == ["Lovely work.", "Tomorrow we try 7 x 7."]
    assert all(s["voice"] == "Samantha" and s["rate"] == 0.92 and s["pitch"] == 1.05 and s["lang"] == "en-US" for s in spoken)
    assert spoken[1]["at"] - spoken[0]["at"] < 500
    assert tab.evaluate("window.__cancelled") == 0, "a line is never cancelled to make room"


def test_two_lines_wait_for_each_other_and_never_overlap(page):
    """The rhythm: a line asked for while Tally is still talking waits for the end of
    that line plus one beat. It never cuts it off and never talks over it."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(voices([("Samantha", "en-US")]) + FAKE_SYNTH)
    tab.goto(url + "#home", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold")
    # the engine never reports the end by itself: the first line stays on the air
    tab.evaluate("() => { window.__endUpTo = 0; window.__spoken = []; }")
    tab.evaluate("() => { Tenfold.speak('First line.'); Tenfold.speak('Second line.'); }")
    tab.wait_for_timeout(300)
    assert [s["text"] for s in tab.evaluate("window.__spoken")] == ["First line."], "the second line talked over the first"
    assert tab.evaluate("window.__cancelled") == 0, "the first line was cut off instead of finished"

    tab.evaluate("() => { window.__t0 = Date.now(); window.__finish(); }")
    tab.wait_for_function("window.__spoken.length === 2", timeout=5000)
    spoken = tab.evaluate("window.__spoken")
    assert spoken[1]["text"] == "Second line."
    beat = spoken[1]["at"] - tab.evaluate("window.__t0")
    assert beat >= 900, f"the second line followed {beat} ms after the first, with no beat between them"


def test_a_line_the_child_has_moved_past_is_dropped(page):
    """A line waiting its turn is spoken only if it is still true when its turn comes.
    One that a newer state has superseded is dropped, never spoken late."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(voices([("Samantha", "en-US")]) + FAKE_SYNTH)
    tab.goto(url + "#home", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold")
    tab.evaluate("() => { window.__endUpTo = 0; window.__spoken = []; window.__moved = false; }")
    tab.evaluate("""() => {
      Tenfold.speak('First line.');
      Tenfold.say('Old news.', null, null, { still: () => !window.__moved });
      Tenfold.say('Still true.', null, null, {});
    }""")
    tab.wait_for_timeout(200)
    assert [s["text"] for s in tab.evaluate("window.__spoken")] == ["First line."]
    # the child moves on while the line is still waiting its turn
    tab.evaluate("() => { window.__moved = true; window.__finish(); }")
    tab.wait_for_function("window.__spoken.length === 2", timeout=5000)
    tab.wait_for_timeout(400)
    assert [s["text"] for s in tab.evaluate("window.__spoken")] == ["First line.", "Still true."]


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


BOTH_HANDS = [{"hand": hand, "number": 6, "x": 0.3 if hand == "left" else 0.7, "y": 0.5}
              for hand in ("left", "right")]


def queued_lesson(tab, url):
    """A lesson with its own socket watched and its own speech engine held open.

    The engine never reports the end of a line by itself, so a line stays on the air
    for as long as the test needs, which is how the queue can be looked at while it
    has something in it.
    """
    open_lesson(tab, url)
    node = running_lesson(tab)
    tab.evaluate(WATCH_SENDS)
    tab.evaluate("() => { window.__endUpTo = 0; window.__spoken = []; }")
    tab.evaluate("() => Tenfold.speak('A long instruction still playing.')")
    tab.wait_for_function("window.__spoken.length === 1", timeout=5000)
    return node


def drops(tab):
    return [(m["line"], m["reason"]) for m in sent(tab, "line_drop")]


def test_an_acknowledgement_is_never_dropped_and_cuts_what_is_playing(page):
    """The measurement that started this: the queue moved slower than the dialogue, so
    every acknowledgement went stale before its turn and the child was never told yes.
    An acknowledgement now cuts the line in flight, and staleness cannot touch it."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(CAPTURE_SOCKET + voices([("Samantha", "en-US")]) + FAKE_SYNTH)
    queued_lesson(tab, url)

    tab.evaluate("""() => {
      window.__moved = false;
      Tenfold.say('Take your time.', null, null,
        { kind: 'nudge', key: 'hint_1', still: () => !window.__moved });
      Tenfold.say('Yes, that is it.', null, null,
        { kind: 'ack', key: 'correct_pose', still: () => !window.__moved });
      window.__moved = true;
    }""")
    tab.wait_for_function("window.__spoken.length === 2", timeout=5000)
    assert said(tab)[1] == "Yes, that is it.", "the acknowledgement waited its turn"
    assert tab.evaluate("window.__cancelled") >= 1, "the line in flight was not cut"

    # the nudge behind it is no longer true, so it goes, and it goes on the record
    tab.evaluate("() => { window.__endUpTo = 99; window.__finish(); window.__finish(); }")
    tab.wait_for_timeout(600)
    assert said(tab) == ["A long instruction still playing.", "Yes, that is it."]
    assert drops(tab) == [("hint_1", "no_longer_true")]
    assert all(m["at"] > 0 for m in sent(tab, "line_drop")), "a drop is stamped with its moment"


def test_a_newer_nudge_replaces_the_one_still_waiting(page):
    """Only the newest nudge is worth saying, and the one it replaces is reported."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(CAPTURE_SOCKET + voices([("Samantha", "en-US")]) + FAKE_SYNTH)
    queued_lesson(tab, url)
    tab.evaluate("""() => {
      Tenfold.say('Look at your hands.', null, null, { kind: 'nudge', key: 'hint_1' });
      Tenfold.say('See the faint circle.', null, null, { kind: 'nudge', key: 'hint_2' });
    }""")
    tab.wait_for_timeout(200)
    assert drops(tab) == [("hint_1", "replaced_by_newer_of_same_kind")]
    tab.evaluate("() => { window.__endUpTo = 99; window.__finish(); }")
    tab.wait_for_function("window.__spoken.length === 2", timeout=5000)
    tab.wait_for_timeout(400)
    assert said(tab) == ["A long instruction still playing.", "See the faint circle."]


def test_a_correction_is_kept_and_spoken_to_the_end(page):
    """The queue holds one line behind the one being spoken. When it is full the line
    that goes is never the correction, and a correction already on the air is never cut
    by the one that follows it."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(CAPTURE_SOCKET + voices([("Samantha", "en-US")]) + FAKE_SYNTH)
    queued_lesson(tab, url)
    tab.evaluate("""() => {
      Tenfold.say('Move your left finger to seven.', null, null,
        { kind: 'correction', key: 'wrong_left_finger' });
      Tenfold.say('Take your time.', null, null, { kind: 'nudge', key: 'hint_1' });
    }""")
    tab.wait_for_timeout(200)
    assert drops(tab) == [("hint_1", "queue_full")], "the correction was dropped for a nudge"

    # the correction takes the air and keeps it: a nudge behind it does not cut it
    tab.evaluate("() => { window.__endUpTo = 2; window.__finish(); }")
    tab.wait_for_function("window.__spoken.length === 2", timeout=5000)
    assert said(tab)[1] == "Move your left finger to seven."
    cancelled = tab.evaluate("window.__cancelled")
    tab.evaluate("() => Tenfold.say('Almost there.', null, null, { kind: 'nudge', key: 'hint_2' })")
    tab.wait_for_timeout(300)
    assert said(tab)[-1] == "Move your left finger to seven.", "the correction was cut off"
    assert tab.evaluate("window.__cancelled") == cancelled


def test_the_pose_turns_green_before_tally_has_finished_talking(page):
    """Visual before voice. The pose is confirmed, so the picture says yes on that tick
    and the two touching fingertips are marked, whatever Tally is still saying."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(CAPTURE_SOCKET + voices([("Samantha", "en-US")]) + FAKE_SYNTH)
    node = queued_lesson(tab, url)
    fingers = [{"hand": "left", "number": 6, "x": 0.3, "y": 0.5},
               {"hand": "right", "number": 7, "x": 0.7, "y": 0.5}]
    feed(tab, node, state="correct_pose", exercise="6 x 7", fingers=fingers,
         match=[{"hand": "left", "number": 6}, {"hand": "right", "number": 7}])
    # no wait at all: the same tick as the message
    assert tab.get_attribute("#lesson .frame", "data-state") == "yes"
    assert "yes" in tab.get_attribute("#lesson .frame", "class")
    assert tab.eval_on_selector_all("#lesson .gh g.fin.on.want", "els => els.length") == 2
    # and Tally is demonstrably still talking while the screen already says yes
    assert tab.evaluate("window.__spoken.length") == 1


def test_the_pill_holds_the_number_heard_and_nothing_else_shows_it(page):
    """One event, shown once. The number heard goes in the pill, with the bars stopped,
    for the time it takes the tutor to answer. The big number and the "heard:" line that
    used to show the same event alongside it are off the screen."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(FAKE_RECOGNITION)
    open_lesson(tab, url)
    wait_for_state(tab, "correct_pose")
    assert pill(tab).lower() == "listening"
    bars = ("() => Array.from(document.querySelectorAll('#lesson .mic .bars i'))"
            ".map((b) => getComputedStyle(b).animationName)")
    assert tab.evaluate(bars) and all(name == "mic" for name in tab.evaluate(bars)), \
        "the bars are running while Tally listens"

    tab.evaluate("() => window.__say('eleven', false)")
    assert pill(tab) == "11"
    assert all(name == "none" for name in tab.evaluate(bars)), "the bars kept moving on a number"
    assert tab.is_hidden("#lesson .typed") and tab.is_hidden("#lesson .caret")
    assert tab.is_hidden("#lesson .heard")

    # the pill goes back to listening once the number has been shown
    tab.wait_for_function("() => document.querySelector('#lesson .mic .mic-label')"
                          ".textContent.trim().toLowerCase() === 'listening'", timeout=5000)
    assert all(name == "mic" for name in tab.evaluate(bars))


def test_the_heard_line_is_debug_only(page):
    """The "heard:" readout is for a developer. It is off the screen unless the url asks
    for it with ?debug=1."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(FAKE_RECOGNITION)
    open_lesson(tab, url)
    wait_for_state(tab, "correct_pose")
    tab.evaluate("() => window.__say('eleven', false)")
    tab.wait_for_timeout(200)
    assert tab.is_hidden("#lesson .heard")
    assert tab.inner_text("#lesson .heard").strip() == ""

    debug = context.new_page()
    debug.add_init_script(FAKE_RECOGNITION)
    open_lesson(debug, url + "?debug=1")
    wait_for_state(debug, "correct_pose")
    debug.evaluate("() => window.__say('eleven', false)")
    debug.wait_for_timeout(200)
    assert debug.is_visible("#lesson .heard")
    assert "heard: eleven" in debug.inner_text("#lesson .heard")


def test_the_answer_goes_on_the_first_number_heard(page):
    """Speech recognition hands over interim results long before the end of the
    utterance. The answer leaves on the first one that parses to a number: waiting for
    the final result is a wait the child sees on the screen."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(FAKE_RECOGNITION)
    open_lesson(tab, url)
    wait_for_state(tab, "correct_pose")
    tab.evaluate(WATCH_SENDS)
    # no final result at all, only what the recogniser thinks so far
    tab.evaluate("() => window.__say('eleven', false)")
    tab.wait_for_timeout(300)
    assert checks(tab) == [11], "the answer waited for the end of the utterance"
    assert pill(tab) == "11", "the number heard is held in the pill"
    # the sentence itself still goes to the server on the final result, not before
    assert sent(tab, "speech") == []
    tab.evaluate("() => window.__say('eleven', true)")
    tab.wait_for_timeout(300)
    assert [m["text"] for m in sent(tab, "speech")] == ["eleven"]
    # settle time is a parameter, and it is off unless the server asks for it
    assert tab.evaluate("Tenfold.timing.answer_first_number_ms") == 0


def test_an_interim_number_and_its_final_are_one_answer(page):
    """One breath reaches the page twice, as an interim result and then as the final
    one. That is one answer, not two."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(FAKE_RECOGNITION)
    open_lesson(tab, url)
    wait_for_state(tab, "correct_pose")
    tab.evaluate(WATCH_SENDS)
    tab.evaluate("() => { window.__say('eleven', false); window.__say('eleven', true); }")
    tab.wait_for_timeout(400)
    assert checks(tab) == [11], "the same number was sent twice"


def test_the_check_steps_turn_with_no_wait_at_all(page):
    """The step is not on a clock. The moment the camera says the condition is true the
    step is turned, in the same breath as the message that carried it: check_step_min_ms
    is zero unless the server sets it, and zero means no wait at all."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(FAKE_RECOGNITION + SILENT_SOCKET + voices([("Samantha", "en-US")]) + FAKE_SYNTH)
    open_check(tab, url)
    tab.wait_for_function("window.__spoken.length === 1", timeout=10000)
    assert tab.evaluate("Tenfold.timing.check_step_min_ms") == 0
    assert tab.evaluate("Tenfold.checkStep") == 1

    # both hands seen: the step is already turned when the message handler returns
    step = tab.evaluate("(m) => { window.__feed(m); return Tenfold.checkStep; }",
                        state_message("check", state="waiting_pose", fingers=BOTH_HANDS))
    assert step == 2, "the step sat on a clock after the hands were seen"

    # the pose is right: the same, with no minimum display time on step 2 either
    step = tab.evaluate("(m) => { window.__feed(m); return Tenfold.checkStep; }",
                        state_message("check", state="correct_pose", fingers=BOTH_HANDS))
    assert step == 3, "the step sat on a clock after the pose was right"


def test_the_line_marked_by_the_server_cuts_the_one_in_flight(page):
    """The acknowledgement is the one line allowed to cut. A line marked able to
    interrupt ends the line being spoken and takes its place; an ordinary line asked for
    at the same moment still waits its turn and its beat."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(voices([("Samantha", "en-US")]) + FAKE_SYNTH)
    tab.goto(url + "#home", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold")
    # the engine never reports the end by itself: the first line stays on the air
    tab.evaluate("() => { window.__endUpTo = 0; window.__spoken = []; }")
    tab.evaluate("() => Tenfold.speak('A long instruction.')")
    tab.wait_for_function("window.__spoken.length === 1", timeout=5000)

    tab.evaluate("""() => {
      Tenfold.say('Ordinary line.', null, null, {});
      Tenfold.say('Yes, that is it.', null, null, { interrupt: true });
    }""")
    tab.wait_for_function("window.__spoken.length === 2", timeout=5000)
    tab.wait_for_timeout(300)
    assert said(tab) == ["A long instruction.", "Yes, that is it."], "the marked line waited its turn"
    assert tab.evaluate("window.__cancelled") >= 1, "the line in flight was left playing"

    # the ordinary line was not dropped: it follows the acknowledgement, and with no
    # beat behind it, because the beat belongs after an instruction
    tab.evaluate("() => { window.__endUpTo = 99; window.__t0 = Date.now(); window.__finish(); window.__finish(); }")
    tab.wait_for_function("window.__spoken.length === 3", timeout=5000)
    spoken = tab.evaluate("window.__spoken")
    assert spoken[2]["text"] == "Ordinary line."
    follow = spoken[2]["at"] - tab.evaluate("window.__t0")
    assert follow < 900, f"the ordinary line waited {follow} ms behind the acknowledgement"


def open_check(tab, url):
    """Land on the start check, with SILENT_SOCKET holding the camera stream back.

    The page opens its socket and starts the node as usual; nothing the server pushes
    reaches it, so the check only ever moves on the messages the test feeds it.
    """
    tab.add_init_script(MISSING_VIEWS)
    tab.goto(url + "#check", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.__socket && window.__socket.readyState === 1", timeout=15000)
    tab.wait_for_function("!!window.__page_onmessage", timeout=15000)


def feed_check(tab, **fields):
    fields.setdefault("fingers", BOTH_HANDS)
    tab.evaluate("(m) => window.__feed(m)", state_message("check", **fields))


def said(tab):
    return [u["text"] for u in tab.evaluate("window.__spoken")]


def quiet(tab, timeout: int = 25000):
    """Wait until Tally has stopped talking.

    The dialogue is paced: the lines the server sent before the test took the stream
    over are still in the queue, each with its beat. A test that counts lines waits
    for that to run out first, which is one poll longer than the beat with nothing new
    spoken.
    """
    tab.evaluate("() => { window.__quiet = -1; }")
    tab.wait_for_function(
        "() => { const n = window.__spoken.length;"
        " if (n === window.__quiet) return true; window.__quiet = n; return false; }",
        polling=1300, timeout=timeout)


def quiet_tts(tab, timeout: int = 30000):
    """The same wait for a page with no speech synthesis: there is nothing to record
    but the pair, and a line with no engine stands for as long as it takes to read."""
    tab.evaluate("() => { window.__quiet = -1; }")
    tab.wait_for_function(
        "() => { const t = window.__sent.filter((m) => m.type === 'tts');"
        " if (t.length === window.__quiet && (!t.length || t[t.length - 1].speaking === false)) return true;"
        " window.__quiet = t.length; return false; }",
        polling=1500, timeout=timeout)


def test_every_check_sentence_is_spoken_and_nothing_else_is(page):
    """The rhythm of the check, end to end: an instruction, silence while the child
    works, a short acknowledgement when they succeed, a beat, the next instruction.
    The step dots, the banner and the result are shown and never spoken."""
    context, url = page
    tab = context.new_page()
    # with a working microphone step 3 asks for the answer out loud
    tab.add_init_script(FAKE_RECOGNITION + SILENT_SOCKET + voices([("Samantha", "en-US")]) + FAKE_SYNTH)
    open_check(tab, url)
    tab.wait_for_function("window.__spoken.length === 1", timeout=10000)
    feed_check(tab, state="waiting_pose")
    tab.wait_for_function("window.__spoken.length === 3", timeout=10000)
    feed_check(tab, state="correct_pose")
    tab.wait_for_function("window.__spoken.length === 5", timeout=10000)
    feed_check(tab, state="answer_correct", answer=36)
    # "Thirty six. Exactly." is one line and two utterances
    tab.wait_for_function("window.__spoken.length === 7", timeout=10000)
    assert said(tab) == ["Show me both hands.", "Perfect.", "Touch your 6 with your 6.",
                         "Yes, that's it.", "Say the answer.", "Thirty six.", "Exactly."]
    assert tab.text_content("#check .banner-top").strip() == "That is a 6 and a 6."
    assert tab.text_content("#result").strip() == "36"

    # the node ends: the path opens once Tally has finished saying so
    tab.evaluate("(m) => window.__feed(m)", {"type": "node_end", "node_id": "check", "correct": 1, "total": 1})
    tab.wait_for_function("window.__spoken.some((u) => u.text === 'Your path is open.')", timeout=10000)
    tab.wait_for_function("document.body.dataset.view === 'practice'", timeout=10000)


def test_the_check_acknowledges_on_the_event_not_on_a_timer(page):
    """The old check ran its steps off fixed timers, which could leave Tally ahead of
    the child. Each acknowledgement now lands on the event that earned it, and the next
    instruction one beat later."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(FAKE_RECOGNITION + SILENT_SOCKET + voices([("Samantha", "en-US")]) + FAKE_SYNTH)
    open_check(tab, url)
    tab.wait_for_function("window.__spoken.length === 1", timeout=10000)
    assert said(tab) == ["Show me both hands."]
    # the child does nothing: no clock walks the check on without them
    tab.wait_for_timeout(1500)
    assert said(tab) == ["Show me both hands."]
    assert tab.get_attribute("#check .frame", "data-step") == "1"

    tab.evaluate("() => { window.__t0 = Date.now(); }")
    feed_check(tab, state="waiting_pose")
    tab.wait_for_function("window.__spoken.length >= 2", timeout=5000)
    spoken = tab.evaluate("window.__spoken")
    assert spoken[1]["text"] == "Perfect."
    delay = spoken[1]["at"] - tab.evaluate("window.__t0")
    assert delay < 500, f"the acknowledgement waited {delay} ms, that is a clock and not the event"

    tab.wait_for_function("window.__spoken.length >= 3", timeout=5000)
    spoken = tab.evaluate("window.__spoken")
    assert spoken[2]["text"] == "Touch your 6 with your 6."
    # no beat after an acknowledgement: the child is past it, the instruction follows
    follow = spoken[2]["at"] - spoken[1]["at"]
    assert follow < 900, f"the instruction waited {follow} ms behind the acknowledgement"
    # the screen moves with the instruction, not before it
    tab.wait_for_function("document.querySelector('#check .frame').dataset.step === '2'", timeout=5000)

    # the pose is right: the same rhythm again, and the mic opens with the question
    tab.wait_for_timeout(1200)
    tab.evaluate("() => { window.__t0 = Date.now(); }")
    feed_check(tab, state="correct_pose")
    tab.wait_for_function("window.__spoken.length >= 4", timeout=5000)
    spoken = tab.evaluate("window.__spoken")
    assert spoken[3]["text"] == "Yes, that's it."
    delay = spoken[3]["at"] - tab.evaluate("window.__t0")
    assert delay < 500, f"the acknowledgement waited {delay} ms, that is a clock and not the event"
    # the pill belongs to step 3 and comes up with the question; that it is never up
    # before it is what test_the_check_walks_its_three_steps_on_one_frame holds, and
    # there is no beat between the two any more for a test to look through

    tab.wait_for_function("window.__spoken.length >= 5", timeout=5000)
    spoken = tab.evaluate("window.__spoken")
    assert spoken[4]["text"] == "Say the answer."
    follow = spoken[4]["at"] - spoken[3]["at"]
    assert follow < 900, f"the question waited {follow} ms behind the acknowledgement"
    tab.wait_for_selector("#check-mic", state="visible", timeout=5000)
    assert tab.evaluate("Tenfold.voice.gate") is True


def test_a_check_correction_the_child_has_fixed_is_not_spoken(page):
    """A correction is only worth saying while the pose is still wrong. One that the
    child has already fixed by the time its turn comes is dropped."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(FAKE_RECOGNITION + SILENT_SOCKET + voices([("Samantha", "en-US")]) + FAKE_SYNTH)
    open_check(tab, url)
    tab.wait_for_function("window.__spoken.length === 1", timeout=10000)
    feed_check(tab, state="waiting_pose")
    tab.wait_for_function("document.querySelector('#check .frame').dataset.step === '2'", timeout=10000)
    # the engine holds the line that is playing, so the correction has to queue behind it
    tab.evaluate("() => { window.__endUpTo = 0; window.__spoken = []; }")
    tab.evaluate("() => Tenfold.say('Almost, keep them touching.', null, null, {})")
    tab.wait_for_function("window.__spoken.length === 1", timeout=5000)
    feed_check(tab, state="wrong_pose", tally="Move your left thumb down.")
    feed_check(tab, state="wrong_pose", tally="Move your left thumb down.")
    tab.wait_for_timeout(200)
    assert said(tab) == ["Almost, keep them touching."], "the same correction was queued twice"
    # the child fixes the pose before the correction is ever spoken
    feed_check(tab, state="correct_pose")
    tab.evaluate("() => window.__finish()")
    tab.wait_for_function("window.__spoken.length === 2", timeout=5000)
    tab.wait_for_timeout(400)
    assert said(tab) == ["Almost, keep them touching.", "Yes, that's it."]


# Five fingers up on each hand, so the numbers ten down to six have something to light.
BOTH_HANDS_OPEN = [{"hand": hand, "number": number, "y": 0.5,
                    "x": (0.16 if hand == "left" else 0.56) + 0.06 * (number - 6)}
                   for hand in ("left", "right") for number in range(6, 11)]


def tiles(tab):
    return tab.eval_on_selector_all("#check .stepnum img", "els => els.map((e) => e.getAttribute('src'))")


def frame_class(tab):
    return tab.get_attribute("#check .frame", "class")


def test_the_check_walks_its_three_steps_on_one_frame(page):
    """The export sheet draws the three steps as three frames, f1, f2 and f3; the screen
    is one frame and the step it is on is its data-step. The three steps are walked here
    on that one frame: the tiles, the numbers lighting ten down to six, the green banner,
    the pill that asks for the answer and the result behind it."""
    context, url = page
    tab = context.new_page()
    errors = []
    tab.on("pageerror", lambda e: errors.append(str(e)))
    tab.add_init_script(FAKE_RECOGNITION + SILENT_SOCKET + voices([("Samantha", "en-US")]) + FAKE_SYNTH)
    open_check(tab, url)

    # step one: the camera streams, the first tile is lit, nothing is asked for yet
    tab.wait_for_function("window.__spoken.length === 1", timeout=10000)
    assert tab.get_attribute("#check .frame", "data-step") == "1"
    assert (tab.get_attribute("#check-cam .practice-video", "src") or "").startswith("/video")
    assert tiles(tab) == ["art/tile-1-on.png", "art/tile-2-off.png", "art/tile-3-off.png"]
    assert tab.is_hidden("#check-mic"), "the answer was asked for before the question"
    assert tab.text_content("#check-say").strip() == "Show me both hands."

    # both hands seen: the frame says so, and the numbers light ten down to six
    feed_check(tab, state="waiting_pose", fingers=BOTH_HANDS_OPEN)
    tab.wait_for_function("document.querySelector('#check .frame').dataset.step === '2'", timeout=10000)
    assert "detected" in frame_class(tab)
    assert tiles(tab) == ["art/tile-1-off.png", "art/tile-2-on.png", "art/tile-3-off.png"]
    tab.wait_for_function(
        "() => { const all = document.querySelectorAll('#check .practice-overlay text[data-number]');"
        " return all.length === 10 && Array.prototype.every.call(all, (t) => t.classList.contains('lit')); }",
        timeout=10000)

    # step two ends on the pose: the export's banner drops and the pill comes up with
    # the question, carrying the third tile with it
    feed_check(tab, state="correct_pose", fingers=BOTH_HANDS_OPEN)
    tab.wait_for_function("document.querySelector('#check .frame').dataset.step === '3'", timeout=10000)
    assert tab.text_content("#check .banner-top").strip() == "That is a 6 and a 6."
    assert "matched" in frame_class(tab)
    assert tiles(tab) == ["art/tile-1-off.png", "art/tile-2-off.png", "art/tile-3-on.png"]
    tab.wait_for_selector("#check-mic", state="visible", timeout=5000)

    # the answer: the result behind the pill, then the path opens on Tally's last line
    feed_check(tab, state="answer_correct", answer=36, fingers=BOTH_HANDS_OPEN)
    tab.wait_for_function("document.querySelector('#result').textContent === '36'", timeout=10000)
    assert "result" in frame_class(tab) and "heard" in frame_class(tab)
    # the three steps are done and the screen has not thrown once; what the practice
    # view does with the child after this is that screen's own business
    assert mine(errors) == []
    tab.evaluate("(m) => window.__feed(m)", {"type": "node_end", "node_id": "check", "correct": 1, "total": 1})
    tab.wait_for_function("document.querySelector('#check .frame').classList.contains('mapin')", timeout=10000)
    tab.wait_for_function("document.body.dataset.view === 'practice'", timeout=15000)
    assert tab.evaluate("() => sessionStorage.getItem('tenfold.checked')") == "1"


# The page's own socket, with every message it sends recorded as parsed JSON.
SEND_SPY = """() => {
  window.__sent = [];
  const ws = window.__socket, send = ws.send.bind(ws);
  ws.send = (data) => {
    try { window.__sent.push(JSON.parse(data)); } catch (e) { window.__sent.push({}); }
    return send(data);
  };
}"""

def tts_pairs(tab):
    return [m["speaking"] for m in tab.evaluate("window.__sent.filter((m) => m.type === 'tts')")]


def test_the_tts_pair_brackets_every_line(page):
    """The server's pedagogical clock stops while Tally talks. The pair goes out around
    every line, whether the line is heard or not, or the clock waits for a voice that is
    never coming."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(CAPTURE_SOCKET + voices([("Samantha", "en-US")]) + FAKE_SYNTH)
    tab.goto(url + "#home", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold && !!window.__socket && window.__socket.readyState === 1")
    tab.evaluate(SEND_SPY)
    tab.evaluate("() => Tenfold.speak('Ready when you are.')")
    tab.wait_for_function("window.__sent.filter((m) => m.type === 'tts').length === 2", timeout=5000)
    assert tts_pairs(tab) == [True, False]

    # muted: nothing is heard, the pair is sent all the same
    tab.click("#mute")
    tab.evaluate("() => { window.__sent = []; window.__spoken = []; }")
    tab.evaluate("() => Tenfold.speak('Say the answer.')")
    tab.wait_for_function("window.__sent.filter((m) => m.type === 'tts').length === 2", timeout=8000)
    assert tts_pairs(tab) == [True, False]
    assert tab.evaluate("window.__spoken") == []
    tab.click("#mute")      # the tabs of one context share the switch, leave it off

    # no speech synthesis in this browser at all: same pair, same order
    tab = context.new_page()
    tab.add_init_script(CAPTURE_SOCKET + NO_SYNTH)
    errors = []
    tab.on("pageerror", lambda e: errors.append(str(e)))
    tab.goto(url + "#home", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold && !!window.__socket && window.__socket.readyState === 1")
    tab.evaluate(SEND_SPY)
    tab.evaluate("() => { Tenfold.speak('One line.'); Tenfold.speak('And another.'); }")
    tab.wait_for_function("window.__sent.filter((m) => m.type === 'tts').length === 4", timeout=10000)
    assert tts_pairs(tab) == [True, False, True, False], "the pairs overlapped"
    assert errors == []


def test_only_tallys_bubble_is_spoken(page):
    """Titles, labels, the XP count and the level names are shown and stay silent.
    The line in Tally's bubble is the one thing that is ever spoken."""
    context, url = page
    tab = context.new_page()
    tab.add_init_script(CAPTURE_SOCKET + voices([("Samantha", "en-US")]) + FAKE_SYNTH)
    open_lesson(tab, url)
    node = running_lesson(tab)
    quiet(tab)
    tab.evaluate("() => { window.__spoken = []; }")

    # Tally's bubble: spoken, and the words on screen are the words being said
    feed(tab, node, state="wrong_pose", tally="Move your left thumb down.")
    tab.wait_for_function("window.__spoken.length === 1", timeout=5000)
    assert said(tab) == ["Move your left thumb down."]
    assert tab.text_content("#lesson .say").strip() == "Move your left thumb down."

    # the finish card: Tally keeps one closing line there, and one more if a level was
    # crossed. The title, the stars, the XP count and the level name label stay silent.
    tab.evaluate("() => { window.__spoken = []; }")
    tab.evaluate("(m) => window.__feed(m)",
                 {"type": "node_end", "node_id": node, "correct": 5, "total": 5, "tally": "Lovely work."})
    tab.wait_for_selector("#finish", state="visible", timeout=10000)
    tab.wait_for_timeout(3600)      # past the XP count up and the level up card behind it
    title = tab.inner_text("#p1 h1").strip()
    assert title != ""
    assert tab.inner_text("#n1").strip() != ""
    spoken = said(tab)
    assert "Lovely work." in spoken, "Tally's closing line was not spoken"
    assert title not in spoken, "the finish title was spoken"
    assert not any("XP" in line for line in spoken), "the XP count was spoken"
    # nothing else: the closing line, and the two sentences of the level up line
    extra = [line for line in spoken if line != "Lovely work."]
    assert extra in ([], ["Level up.", tab.inner_text("#p2 h1").strip() + "."]), spoken

    # the profile: the level ladder, every name on screen and not one of them spoken
    tab.evaluate("() => { window.__spoken = []; location.hash = 'profile'; }")
    tab.wait_for_function("document.body.dataset.view === 'profile'", timeout=5000)
    tab.wait_for_timeout(800)
    assert tab.inner_text("#lname").strip() != ""
    assert said(tab) == [], "a level name was spoken"


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
    tab.add_init_script(NO_SPEECH_API + MISSING_VIEWS)
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
    assert mine(errors) == []


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
    assert tab.text_content("#lesson .count") == "3 of 5"
    assert tab.evaluate("document.querySelector('#lesson .bar i').style.width") == "40%"
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
    tab.wait_for_function("document.body.dataset.view === 'practice'", timeout=10000)
    tab.evaluate("() => Tenfold.startLesson(TenfoldLevels.allNodes()[0].id)")
    tab.wait_for_selector("#lesson .cam", timeout=10000)
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
    tab.wait_for_function("document.querySelector('#lesson .count').textContent === '1 of 5'")
    node = tab.evaluate("Tenfold.lesson.node.id")
    feed(tab, node, state="correct_pose")
    feed(tab, node, state="answer_correct", answer=42, first_try=True)
    assert tab.evaluate("Tenfold.lesson.correct") == 1
    before = tab.get_attribute("#lesson .practice-video", "src")

    tab.evaluate("() => { window.__old = window.__socket; window.__socket.close(); }")
    tab.wait_for_function("window.__socket !== window.__old && window.__socket.readyState === 1", timeout=15000)
    tab.wait_for_function("document.querySelector('#lesson .count').textContent === '1 of 5'", timeout=15000)
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
    quiet(tab)
    tab.evaluate("() => { window.__sent.length = 0; window.__spoken.length = 0; }")
    return tab, node, errors


def test_the_tts_pair_brackets_the_whole_spoken_line(page):
    """The server's clocks start at the end of Tally's line, so the pair has to wrap
    the line, not each sentence of it."""
    context, url = page
    tab, node, errors = tutor_page(context, url)
    feed(tab, node, state="wrong_pose", tutor_line="Almost. Your right hand needs 7, not 9.")
    tab.wait_for_function("window.__sent.filter((m) => m.type === 'tts').length === 2", timeout=10000)
    assert [u["text"] for u in tab.evaluate("window.__spoken")] == [
        "Almost.", "Your right hand needs 7, not 9."]
    assert [m["speaking"] for m in sent(tab, "tts")] == [True, False], "one pair, two sentences"
    # the next line is its own pair, one beat later
    feed(tab, node, state="wrong_pose", tutor_line="Try your 7.")
    tab.wait_for_function("window.__sent.filter((m) => m.type === 'tts').length === 4", timeout=10000)
    assert [m["speaking"] for m in sent(tab, "tts")] == [True, False, True, False]

    # a line cut off by the way out of the lesson still closes its pair: without it
    # the next line would open without one and the tutor's clock would never start
    tab.evaluate("() => { window.__endUpTo = 0; window.__sent.length = 0; }")
    feed(tab, node, state="wrong_pose", tutor_line="Hold them up for me.")
    tab.wait_for_function("window.__sent.filter((m) => m.type === 'tts').length === 1", timeout=10000)
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
    tab.wait_for_function("window.__sent.filter((m) => m.type === 'tts').length === 2", timeout=10000)
    assert tab.evaluate("window.__spoken") == [], "muted says nothing"
    assert tab.text_content("#lesson .say") == "Your right hand needs 7."
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
    # with no engine a line still stands for as long as it takes to read: the lines
    # already in the queue have to be over before this one is asked for
    quiet_tts(tab)
    tab.evaluate("() => { window.__sent.length = 0; }")
    feed(tab, node, state="wrong_pose", tutor_line="Your right hand needs 7.")
    # one line at a time: this one waits for the beat after whatever was still playing
    tab.wait_for_function(
        "document.querySelector('#lesson .say').textContent === 'Your right hand needs 7.'",
        timeout=15000)
    # with no engine a line still stands for as long as it takes to read, so its pair
    # closes a beat later: wait for the pair this line opened to close
    tab.wait_for_function("""() => {
      const pairs = window.__sent.filter((m) => m.type === 'tts').map((m) => m.speaking);
      return pairs.length >= 2 && pairs[pairs.length - 2] === true && pairs[pairs.length - 1] === false;
    }""", timeout=20000)
    assert [m["speaking"] for m in sent(tab, "tts")][-2:] == [True, False]
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
    tab.evaluate("() => window.__say('I really do not know this', true)")
    tab.wait_for_timeout(150)
    assert [m["text"] for m in sent(tab, "speech")] == ["is it fifty six", "I really do not know this"]
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
    tab.wait_for_function("window.__spoken.length === 1", timeout=10000)
    tab.wait_for_timeout(200)
    assert tab.text_content("#lesson .say") == "Your right hand needs 7, not 9."
    assert [u["text"] for u in tab.evaluate("window.__spoken")] == ["Your right hand needs 7, not 9."]
    # no tutor_line: the engine's phrase is the line, and it is spoken too, one beat later
    feed(tab, node, state="wrong_pose", tally="Move your right finger from 9 to 7.")
    tab.wait_for_function("window.__spoken.length === 2", timeout=10000)
    assert tab.text_content("#lesson .say") == "Move your right finger from 9 to 7."
    assert [u["text"] for u in tab.evaluate("window.__spoken")] == [
        "Your right hand needs 7, not 9.", "Move your right finger from 9 to 7."]
    assert errors == []


VISUALS = [
    ({"kind": "pulse_finger", "hand": "right", "finger": 7}, ".practice-overlay .pulse"),
    ({"kind": "correction", "wrong_hand": "right", "expected_finger": 7}, ".practice-overlay .hand-box"),
    ({"kind": "ghost", "hand": "right", "from": 9, "to": 7}, ".practice-overlay .ghost"),
    ({"kind": "rescue_card", "tens": 5, "units": 6, "total": 56}, ".why span"),
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
    assert tab.eval_on_selector_all("#lesson .why span", "els => els.map((e) => e.textContent)") == [
        "5 tens = 50", "2 x 3 = 6", "50 + 6 = 56"]
    # null: the finger circles and nothing else, and the card is gone
    feed(tab, node, state="wrong_pose", exercise="8 x 7", fingers=TUTOR_FINGERS, tutor_visual=None)
    assert tab.eval_on_selector_all(
        "#lesson .practice-overlay .ring, #lesson .practice-overlay .zone-box, "
        "#lesson .practice-overlay .hand-box, #lesson .practice-overlay .dot",
        "els => els.length") == 0
    assert tab.is_hidden("#lesson .why")
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


# Where each visual has to land on the export's own overlay: the element that has to
# exist inside #gh, and the landmark its centre has to sit on, if it sits on one.
EXPORT_VISUALS = [
    ({"kind": "pulse_finger", "hand": "right", "finger": 7}, "circle.ring.pulse", (0.70, 0.50)),
    ({"kind": "correction", "wrong_hand": "right", "expected_finger": 7}, "circle.dot", (0.70, 0.50)),
    ({"kind": "ghost", "hand": "right", "from": 9, "to": 7}, "circle.ghost", None),
    ({"kind": "rescue_card", "tens": 5, "units": 6, "total": 56}, None, None),
    ({"kind": "placement_zones"}, "rect.zone-box", None),
    # finger_numbers is the supportive mode reminder: every fingertip, not one target
    ({"kind": "finger_numbers"}, "text.num.lit", None),
]

CENTRE = r"""
(sel) => {
  const gh = document.querySelector("#lesson #gh");
  const el = gh.querySelector(sel);
  if (!el) return null;
  const box = gh.viewBox.baseVal;
  const point = el.tagName === "text"
    ? { x: Number(el.parentNode.getAttribute("transform").match(/-?[\d.]+/g)[0]),
        y: Number(el.parentNode.getAttribute("transform").match(/-?[\d.]+/g)[1]) }
    : { x: Number(el.getAttribute("cx")), y: Number(el.getAttribute("cy")) };
  return { x: point.x / box.width, y: point.y / box.height };
}
"""


def test_every_tutor_visual_is_drawn_on_the_export_markup(page):
    """The six kinds again, this time on the export's own camera: each one is drawn
    inside #gh, the svg exercise.html put over the picture, on the landmark it is
    about, and none of them throws. The fingertips keep the export's structure, a
    halo, a tip and a number inside one .fin group."""
    context, url = page
    tab, node, errors = tutor_page(context, url)
    for visual, selector, landmark in EXPORT_VISUALS:
        feed(tab, node, state="wrong_pose", exercise="8 x 7", fingers=TUTOR_FINGERS,
             wrong=[{"hand": "right", "number": 9}], tutor_visual=visual)
        # every fingertip is the export's group, drawn in the picture's own pixels
        shape = tab.eval_on_selector_all(
            "#lesson #gh g.fin",
            "els => els.map((g) => [g.querySelectorAll('circle.halo').length,"
            " g.querySelectorAll('circle.tip').length, g.querySelectorAll('text.num').length].join(''))")
        assert shape == ["111"] * len(TUTOR_FINGERS), f"{visual['kind']} lost the export fingertips"
        if selector is None:
            # the rescue card is the one visual with no shape on the hands: it is the
            # export's band of lines over the bottom of the picture
            assert tab.eval_on_selector_all("#lesson .cam .why span", "els => els.length") == 3
            continue
        assert tab.eval_on_selector_all(f"#lesson #gh {selector}", "els => els.length") >= 1, \
            f"{visual['kind']} drew nothing inside the export overlay"
        if landmark:
            at = tab.evaluate(CENTRE, selector)
            assert at is not None and abs(at["x"] - landmark[0]) < 0.01 and abs(at["y"] - landmark[1]) < 0.01, \
                f"{visual['kind']} is not on the landmark: {at}"
    assert errors == []


def test_a_tap_on_tally_asks_for_help(page):
    """No new button: Tally himself and his bubble are the ask. The server already
    accepts hint, and it is the only help that costs the first try bonus."""
    context, url = page
    tab, node, errors = tutor_page(context, url)
    tab.click("#lesson .foot > .tally-art", force=True)
    tab.wait_for_timeout(100)
    assert [m["type"] for m in sent(tab, "hint")] == ["hint"]
    tab.click("#lesson .say", force=True)
    tab.wait_for_timeout(100)
    assert len(sent(tab, "hint")) == 2, "the bubble asks too"
    assert errors == []


# Every bubble that carries a line of Tally's, screen by screen. A bubble on
# screen with text in it and no voice behind it is the bug this list catches.
# Tally's three bubbles, plus the one closing line he keeps on the finish card. The
# titles, the XP count and the level names are read on screen and never spoken.
BUBBLES = ["#check-say", "#u-say", "#lesson .say", "#p1 .sub"]

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
    """The rule, walked screen by screen: one of Tally's bubbles that appears without
    him saying it is a bug. Every visible line has gone through speech synthesis, one
    line at a time, so the waits here are as long as the rhythm needs."""
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
    tab.wait_for_selector("#lesson .cam", timeout=10000)
    node = running_lesson(tab)
    tab.wait_for_timeout(300)
    assert silent(tab) == []
    feed(tab, node, state="wrong_pose", tutor_line="Your right hand needs 7, not 9.")
    tab.wait_for_function("window.__spoken.some((u) => u.text.includes('not 9'))", timeout=10000)
    assert silent(tab) == []

    # the finish card, and the level up card behind it: 150 XP crosses level two
    for _ in range(5):
        feed(tab, node, state="correct_pose")
        feed(tab, node, state="answer_correct", answer=42, first_try=True)
        feed(tab, node, state="exercise_shown")
    tab.evaluate("(id) => window.__feed({ type: 'node_end', node_id: id, correct: 5, total: 5, tally: 'Lovely work.' })", node)
    tab.wait_for_selector("#finish", state="visible", timeout=10000)
    tab.wait_for_function("window.__spoken.some((u) => u.text.includes('Lovely work'))", timeout=10000)
    assert silent(tab) == []
    tab.wait_for_selector("#p2.show", timeout=10000)
    tab.wait_for_timeout(1600)
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
        tab.evaluate("(e) => Tally.set(document.querySelector('#t-done'), e)", expression)
        src = tab.get_attribute("#t-done .t-base", "src")
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
        "#path .node",
        "els => els.map((e) => [e.dataset.status, e.querySelector('span').style.backgroundImage || 'icon'])")
    assert art, "the path drew no nodes"
    for status, image in art:
        if image == "icon":
            continue                      # the chest keeps the drawn icon of icons.svg
        expected = {"current": "start.png", "done": "n-done.png", "locked": "n-lock.png"}[status]
        assert expected in image, f"{status} node drawn with {image}"
    # the render carries the START bubble, so the node itself opens the lesson
    assert tab.get_attribute('#path .node.current', "data-action") == "start"
    assert errors == []


def test_the_check_steps_are_the_rendered_tiles(page):
    """The three steps of the start check are the numbered tiles, lit one at a time."""
    context, url = page
    tab = context.new_page()
    errors = []
    tab.on("pageerror", lambda e: errors.append(str(e)))
    tab.add_init_script(MISSING_VIEWS)
    tab.goto(url + "#check", wait_until="domcontentloaded")
    tab.wait_for_selector("#check .stepnum img", timeout=10000)
    lit = tab.eval_on_selector_all("#check .stepnum img", "els => els.map((e) => e.getAttribute('src'))")
    assert lit == ["art/tile-1-on.png", "art/tile-2-off.png", "art/tile-3-off.png"]
    for image in lit:
        assert tab.request.get(f"{url}/{image}").status == 200, image
    assert mine(errors) == []


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
    tab.wait_for_selector("#wardrobe .slot", timeout=10000)
    slots = tab.eval_on_selector_all(
        "#wardrobe .slot",
        "els => els.map((e) => [e.querySelector('img').getAttribute('src'), e.classList.contains('on')])")
    assert [s[0] for s in slots] == [
        "art/acc-glasses.png", "art/acc-headband.png", "art/acc-hat.png",
        "art/acc-cape.png", "art/acc-crown.png"]
    assert [s[1] for s in slots] == [True, True, False, False, False]
    assert tab.eval_on_selector_all("#pf-tally .t-acc", "els => els.length") == 2
    assert errors == []


# ---------------------------------------------------------------------------
# Several children on one tablet. A child is the first name typed on the
# welcome screen, trimmed and lowercased, and every record hangs off that key:
# switching learner moves a pointer and erases nothing.
# ---------------------------------------------------------------------------


def name_child(tab, typed: str):
    """Type a first name on the welcome screen and land on the hub."""
    tab.evaluate("() => { location.hash = 'welcome'; }")
    tab.wait_for_function("document.body.dataset.view === 'welcome'")
    tab.fill("#welcome #name", typed)
    tab.click("#welcome #form button[type=submit]")
    tab.wait_for_function("document.body.dataset.view === 'home'")


def path_states(tab):
    tab.evaluate("() => { location.hash = 'practice'; }")
    tab.wait_for_selector("#path .node", timeout=10000)
    return tab.eval_on_selector_all("#path .node", "els => els.map((e) => e.dataset.status)")


def test_two_children_keep_separate_xp_and_progress(page):
    """Two children on one computer are two learners. Neither ever reads or
    overwrites the other, and switching between them loses nothing."""
    context, url = page
    tab = context.new_page()
    errors = []
    tab.on("pageerror", lambda e: errors.append(str(e)))
    tab.goto(url + "#welcome", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold")

    # Ilan plays: 120 XP and the first lesson behind him
    name_child(tab, "Ilan")
    assert tab.evaluate("Tenfold.child") == "ilan"
    tab.evaluate("() => Tenfold.addXp(120)")
    tab.evaluate("() => localStorage.setItem('tenfold.levels.v1.ilan',"
                 " JSON.stringify({ stars: { 'u1-l1': 3 }, chests: {} }))")
    tab.reload()
    tab.wait_for_function("!!window.Tenfold")
    assert tab.evaluate("Tenfold.xp") == 120
    assert path_states(tab)[:2] == ["done", "current"]

    # Axel sits down: a second child starts from zero, with none of Ilan's path
    name_child(tab, "Axel")
    assert tab.evaluate("Tenfold.child") == "axel"
    assert tab.evaluate("Tenfold.xp") == 0
    assert path_states(tab)[:2] == ["current", "locked"]
    tab.evaluate("() => Tenfold.addXp(30)")

    # Ilan comes back, spelled the way a child spells it: nothing of his was lost
    name_child(tab, "  ILAN ")
    assert tab.evaluate("Tenfold.child") == "ilan"
    assert tab.evaluate("Tenfold.xp") == 120
    assert path_states(tab)[:2] == ["done", "current"]

    # and neither record was erased along the way
    assert tab.evaluate("() => JSON.parse(localStorage.getItem('tenfold.learner.axel')).xp") == 30
    assert tab.evaluate("() => JSON.parse(localStorage.getItem('tenfold.learner.ilan')).xp") == 120
    assert tab.evaluate("() => JSON.parse(localStorage.getItem('tenfold.levels.v1.axel') || '{}').stars || {}") == {}
    assert errors == []


def test_the_play_before_a_name_belongs_to_the_first_child_only(page):
    """A child who starts before typing a name keeps that XP once the name is
    typed. The next child inherits nothing."""
    context, url = page
    tab = context.new_page()
    tab.goto(url + "#home", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold")
    tab.evaluate("() => Tenfold.addXp(40)")
    name_child(tab, "Ilan")
    assert tab.evaluate("Tenfold.xp") == 40
    name_child(tab, "Axel")
    assert tab.evaluate("Tenfold.xp") == 0


def test_the_hub_names_the_child_and_is_the_way_back_to_the_welcome_screen(page):
    """The hub says who is playing and carries the way out to switch child, and
    the welcome screen offers the children this computer already knows."""
    context, url = page
    tab = context.new_page()
    errors = []
    tab.on("pageerror", lambda e: errors.append(str(e)))
    tab.goto(url + "#welcome", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold")
    name_child(tab, "Ilan")
    assert "Ilan" in tab.inner_text("#greeting")
    assert tab.is_visible("#switch-child")

    tab.click("#switch-child")
    tab.wait_for_function("document.body.dataset.view === 'welcome'")
    assert tab.is_visible("#known")
    assert tab.inner_text('#known [data-child="ilan"]').strip() == "Ilan"
    # the name typed is never put back on screen as markup
    assert tab.eval_on_selector('#known [data-child="ilan"]', "e => e.innerHTML") == "Ilan"

    tab.click('#known [data-child="ilan"]')
    tab.wait_for_function("document.body.dataset.view === 'home'")
    assert tab.evaluate("Tenfold.child") == "ilan"
    assert errors == []

