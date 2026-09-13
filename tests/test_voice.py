"""Voice answers in the camera lesson of web/course/, driven in a real browser.

Headless Chromium ships no speech recognition, which is exactly the case the
page has to survive: the microphone stays hidden and the keyboard still works.
A fake recogniser is then injected to exercise the rest, because the parser and
the listening window are the parts that can silently submit a wrong answer.

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
    wait_for_state(tab, "answer_correct", timeout=10000)


def test_a_spoken_wrong_answer_is_sent_too(page):
    context, url = page
    tab = context.new_page()
    tab.add_init_script(FAKE_RECOGNITION)
    open_lesson(tab, url)
    wait_for_state(tab, "correct_pose")
    tab.evaluate("() => window.__say('eleven', true)")
    wait_for_state(tab, "answer_wrong", timeout=10000)


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
    tab.add_init_script(voices([("Samantha", "en-US")]) + FAKE_SYNTH)
    tab.goto(url + "#home", wait_until="domcontentloaded")
    tab.wait_for_function("!!window.Tenfold")
    tab.click("a.door[href='#check']")
    # step 3 shows "Say the answer." together with the mic pill, a moment after the match
    tab.wait_for_selector("#check-mic", state="visible", timeout=30000)
    tab.wait_for_timeout(300)
    spoken = [u["text"] for u in tab.evaluate("window.__spoken")]
    for line in ["Show me both hands.", "There they are.", "Touch your 6 with your 6.", "Six and six, touching.", "Say the answer."]:
        assert line in spoken, f"{line!r} was shown but not spoken: {spoken}"
