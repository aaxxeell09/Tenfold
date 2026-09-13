"""What the course screens draw, measured in the browser rather than looked up in the DOM.

The check's fingertips are drawn by drawFingers in the export's own pixels, a viewBox 1194
across: a number styled in the old unit frame (font-size 0.05px) is still in the DOM and still
has the right text, and nobody can read it. These tests measure the rendered numbers against
their tips. The finish screen's Enter shortcut is checked on both panes the child can see.

Skipped with test_voice.py when playwright or its browser is missing.
"""
from __future__ import annotations

from test_voice import (  # noqa: F401  page and server are the browser fixtures
    BOTH_HANDS_OPEN, CAPTURE_SOCKET, FAKE_RECOGNITION, FAKE_SYNTH, SILENT_SOCKET, feed, feed_check, mine,
    open_check, open_lesson, page, running_lesson, server, voices,
)

# Every drawn fingertip: its number's rendered box against its tip's, in CSS pixels and in the
# overlay's own frame, with the computed style that decides whether it can be read.
MEASURE_TIPS = """() => {
  const svg = document.querySelector('#check .practice-overlay');
  const box = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal;
  const scale = box.width / vb.width;
  const lum = (color) => {
    const [r, g, b] = color.match(/[\\d.]+/g).slice(0, 3).map((v) => {
      v = Number(v) / 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  return Array.from(svg.querySelectorAll('g.fin')).map((g) => {
    const text = g.querySelector('text.num'), tip = g.querySelector('circle.tip');
    const t = text.getBoundingClientRect(), c = tip.getBoundingClientRect();
    const ts = getComputedStyle(text), cs = getComputedStyle(tip);
    const a = lum(ts.fill), b = lum(cs.fill);
    return {
      number: text.textContent, viewBoxWidth: vb.width, fontSize: ts.fontSize,
      heightPx: t.height, heightFrame: t.height / scale, tipFrame: c.width / scale,
      inside: t.left >= c.left - 0.5 && t.right <= c.right + 0.5 && t.top >= c.top - 0.5 && t.bottom <= c.bottom + 0.5,
      opacity: [getComputedStyle(g).opacity, ts.opacity, cs.opacity],
      contrast: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05),
    };
  });
}"""


def test_the_check_numbers_are_drawn_readable_in_the_export_frame(page):
    context, url = page
    tab = context.new_page()
    errors = []
    tab.on("pageerror", lambda e: errors.append(str(e)))
    tab.add_init_script(FAKE_RECOGNITION + SILENT_SOCKET + voices([("Samantha", "en-US")]) + FAKE_SYNTH)
    open_check(tab, url)
    feed_check(tab, state="waiting_pose", fingers=BOTH_HANDS_OPEN)
    tab.wait_for_function(
        "() => { const all = document.querySelectorAll('#check .practice-overlay text[data-number]');"
        " return all.length === 10 && Array.prototype.every.call(all, (t) => t.classList.contains('lit')); }",
        timeout=10000)
    tab.wait_for_timeout(500)  # past the .3s fill transition of a lit tip

    tips = tab.evaluate(MEASURE_TIPS)
    assert sorted(int(t["number"]) for t in tips) == sorted([6, 7, 8, 9, 10] * 2)
    for t in tips:
        assert t["viewBoxWidth"] == 1194, t
        assert t["fontSize"] == "23px", t  # the export's size, in the frame drawFingers draws in
        assert t["opacity"] == ["1", "1", "1"], t
        # a number is a real glyph in the 1194 frame, never a speck, on a tip big enough to hold it
        assert 18 <= t["heightFrame"] <= 45 and t["tipFrame"] >= 48, t
        assert t["heightPx"] >= 12, t  # readable at the size the frame is actually shown
        assert t["inside"], t  # the number sits on its tip, not beside it
        assert t["contrast"] >= 4.5, t  # dark number on a lit tip
    assert mine(errors) == []


# Records which finish button a click lands on and stops it, so both panes can be tried on one page.
CLICK_SPY = """
window.__clicked = [];
window.addEventListener("click", (e) => {
  const button = e.target && e.target.closest && e.target.closest("#finish .btn2");
  if (!button) return;
  window.__clicked.push(button.closest(".pane").id + ":" + button.textContent.trim());
  e.preventDefault(); e.stopImmediatePropagation();
}, true);
"""


def test_enter_on_finish_presses_the_button_of_the_pane_on_screen(page):
    context, url = page
    tab = context.new_page()
    errors = []
    tab.on("pageerror", lambda e: errors.append(str(e)))
    tab.add_init_script(CAPTURE_SOCKET + voices([("Samantha", "en-US")]) + FAKE_SYNTH + CLICK_SPY)
    open_lesson(tab, url)
    node = running_lesson(tab)
    # five first-try answers: 150 XP, which crosses level two, so the level up card comes in
    for _ in range(5):
        feed(tab, node, state="correct_pose")
        feed(tab, node, state="answer_correct", answer=42, first_try=True)
        feed(tab, node, state="exercise_shown")
    tab.evaluate("(m) => window.__feed(m)",
                 {"type": "node_end", "node_id": node, "correct": 5, "total": 5, "tally": "Lovely work."})
    tab.wait_for_selector("#finish", state="visible", timeout=10000)

    # the card is on screen: Enter is its Continue
    assert "out" not in (tab.get_attribute("#p1", "class") or "")
    tab.keyboard.press("Enter")
    tab.wait_for_function("window.__clicked.length === 1", timeout=5000)
    assert tab.evaluate("window.__clicked") == ["p1:Continue"]

    # the level up card has slid over it: Enter is the level up card's Continue, not the hidden card's
    tab.wait_for_selector("#p2.show", timeout=10000)
    assert "out" in tab.get_attribute("#p1", "class")
    tab.keyboard.press("Enter")
    tab.wait_for_function("window.__clicked.length === 2", timeout=5000)
    assert tab.evaluate("window.__clicked")[1] == "p2:Continue"
    assert mine(errors) == []
