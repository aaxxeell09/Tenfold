"""Nimble loop report: how AI agents teach the Nimble app to read children's hands, what they proved and where the
evidence stops. A marimo app over one file, data/snapshot.json, styled by dashboard/tenfold.css.

    make dashboard                               # marimo run dashboard/loop_dashboard.py
    marimo edit dashboard/loop_dashboard.py      # to change it
    TENFOLD_SNAPSHOT=/path/snapshot.json marimo run dashboard/loop_dashboard.py

Snapshot order: $TENFOLD_SNAPSHOT, then this repository's data/snapshot.json, then the working directory's, then the
one committed on GitHub main. The footer names the one loaded and the top of the page lists what it lacks.

Written for someone who opens the page cold: every section says why it exists before showing numbers, numbers read
as "out of 100", and the engineering detail sits in one folded "Technical details" block. Path: the result, 01 hands
before and after (why almost touching matters), 02 why two judges can refuse a change, 03 every change and attempt,
04 the effect across the practice set, 05 the limits of the evidence (retrospective validation, participants not
identified, with its intervals). Two scores are compared only when their data and scorers are identified and
equal; otherwise the page says "not verified". The explorables are precomputed and never change the app.
Every visible word is English, including labels that the snapshot stores in French.
"""
import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium", app_title="Nimble loop report", css_file="tenfold.css")


@app.cell
def _():
    import html
    import json
    import math
    import os
    import re
    import urllib.request
    from datetime import datetime
    from pathlib import Path

    import altair as alt
    import marimo as mo
    import pandas as pd

    return Path, alt, datetime, html, json, math, mo, os, pd, re, urllib


@app.cell
def _():
    INK, INK2, MUTED, GRID, AXIS = "#1C1917", "#57534E", "#78716C", "#F0EFED", "#D6D3D1"
    ACCENT, GOOD, BAD = "#4F46E5", "#15803D", "#B91C1C"
    LEFT_HAND, RIGHT_HAND = "#3B82F6", "#F59E0B"
    FONT = "Geist, Inter, ui-sans-serif, system-ui, sans-serif"
    WEAVE_URL = "https://wandb.ai/ilan-sainteagathe-synthetic-swarm/tenfold/weave"
    REPO_URL = "https://github.com/aaxxeell09/Tenfold"
    HAND_BONES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
                  (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]
    _PATHS = {
        "camera": '<path d="M4 8.5h3l1.8-2.5h6.4L17 8.5h3V19H4z"/><circle cx="12" cy="13.5" r="3.2"/>',
        "search": '<circle cx="11" cy="11" r="6"/><path d="m20 20-4.3-4.3"/>',
        "code": '<path d="m8 8-4 4 4 4"/><path d="m16 8 4 4-4 4"/><path d="m13.5 5.5-3 13"/>',
        "shield": '<path d="M12 3 5 6v5.5c0 4.6 3 8 7 9.5 4-1.5 7-4.9 7-9.5V6z"/><path d="m9 12 2.2 2.2L15.5 10"/>',
        "scale": '<path d="M12 4v16"/><path d="M7 20h10"/><path d="M5 7h14"/><path d="M5 7 2.5 13a2.5 2.5 0 0 0 5 0z"/>'
                 '<path d="m19 7-2.5 6a2.5 2.5 0 0 0 5 0z"/>',
        "commit": '<circle cx="12" cy="12" r="3.5"/><path d="M3 12h5.5"/><path d="M15.5 12H21"/>',
        "check": '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
        "x": '<path d="M6 6l12 12"/><path d="M18 6 6 18"/>',
        "minus": '<path d="M6 12h12"/>',
        "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 7.5h.01"/>',
        "loop": '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 5v6h-6"/>',
        "link": '<path d="M7 17 17 7"/><path d="M8 7h9v9"/>',
    }

    def icon(name, size=18, color="currentColor", width=1.75):
        return (f'<svg class="tf-icon" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{color}" '
                f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{_PATHS[name]}</svg>')

    def style(chart):
        return (chart.configure(font=FONT, background="transparent")
                .configure_view(strokeWidth=0)
                .configure_axis(labelColor=MUTED, titleColor=INK2, gridColor=GRID, domainColor=AXIS, tickColor=AXIS,
                                labelFontSize=12, titleFontSize=12, titleFontWeight=500, labelPadding=8, labelFont=FONT)
                .configure_axisY(domain=False, ticks=False))

    return (ACCENT, BAD, GOOD, HAND_BONES, INK, INK2, LEFT_HAND, MUTED, REPO_URL, RIGHT_HAND, WEAVE_URL, icon, style)


@app.cell
def _(Path, json, mo, os, urllib):
    RAW_SNAPSHOT = "https://raw.githubusercontent.com/aaxxeell09/Tenfold/main/data/snapshot.json"

    def _candidates():
        if os.environ.get("TENFOLD_SNAPSHOT"):
            yield Path(os.environ["TENFOLD_SNAPSHOT"]), "TENFOLD_SNAPSHOT"
        try:
            _dir = mo.notebook_dir()
            if _dir:
                yield Path(_dir).parent / "data" / "snapshot.json", "this repository"
        except Exception:
            pass
        yield Path.cwd() / "data" / "snapshot.json", "working directory"

    def _load():
        for _path, _kind in _candidates():
            if _path.exists():
                try:
                    return json.loads(_path.read_text()), f"{_path.name} ({_kind})"
                except (OSError, json.JSONDecodeError):
                    return {}, f"{_path.name} ({_kind}, unreadable)"
        try:
            with urllib.request.urlopen(RAW_SNAPSHOT, timeout=10) as _r:
                return json.loads(_r.read().decode()), "snapshot.json (GitHub main)"
        except Exception:
            return {}, "none: no local snapshot and GitHub unreachable"

    snap, snap_source = _load()
    informed = snap.get("informed") or []
    explorer = snap.get("explorer") if isinstance(snap.get("explorer"), dict) and not snap["explorer"].get("error") else None
    _cmp = (explorer or {}).get("compare")
    compare = _cmp if isinstance(_cmp, dict) and not _cmp.get("error") and _cmp.get("a") and _cmp.get("b") else None
    refused = snap.get("refused") or [{"stage": "gate", **_r} for _r in (snap.get("rejected") or [])]
    refused_known = "refused" in snap or bool(snap.get("rejected"))  # older snapshots never recorded refusals
    return compare, explorer, informed, refused, refused_known, snap, snap_source


@app.cell
def _(datetime, mo, re):
    RETRO_LABEL = "retrospective validation, participants not identified"  # the page is English; the snapshot keeps its own label

    def em(m):
        return None if not m else m.get("exact_match")

    def pct(x, digits=1):
        return "n/a" if x is None else f"{x * 100:.{digits}f}%"

    def pts(x):
        return "n/a" if x is None else f"{x * 100:+.1f} pts"

    def basis(v):
        """The identity of what a score was measured with: data and scorers, or None when either is unknown."""
        data = (v.get("data") or {}).get("sha")
        scorers = (v.get("scorers") or {}).get("sha256") or (v.get("train") or {}).get("scorers_sha256")
        return (data, scorers) if data and scorers else None

    def comparable(a, b):
        return a is not None and b is not None and basis(a) is not None and basis(a) == basis(b)

    def when(ts):
        try:
            return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).strftime("%d %b %H:%M")
        except ValueError:
            return str(ts or "")[:16]

    def plain_class(c):
        if c.startswith("near:") and "x" in c:
            a, b = c[5:].split("x", 1)
            return f"almost touching {a}×{b}"
        if re.fullmatch(r"\d+x\d+", c):
            a, b = c.split("x")
            return f"touching {a}×{b}"
        return {"transition": "hands moving", "partial_hand": "one hand only", "out_of_frame": "hands leaving the camera",
                "rest": "hands resting"}.get(c, c)

    def _setting(patch):
        m = re.search(r"([A-Z_]{4,}) from ([0-9.]+) to ([0-9.]+)", (patch or "").replace("`", ""))
        return (m.group(1), float(m.group(2)), float(m.group(3))) if m else None

    def plain_change(patch):
        """The setting that moved, with its values."""
        s = _setting(patch)
        if s:
            names = {"CONTACT_THRESHOLD": "touch limit", "UNKNOWN_THRESHOLD": "confidence needed"}
            return f"{names.get(s[0], s[0].lower())} {s[1]:g} → {s[2]:g}"
        text = " ".join((patch or "").split())
        return text if len(text) <= 70 else text[:69] + "…"

    def plain_effect(patch):
        """What the change means for a child in front of the camera."""
        s = _setting(patch)
        if s and s[0] == "CONTACT_THRESHOLD":
            return ("Fingers must be a little closer to count as touching" if s[2] < s[1]
                    else "Fingers can be a little further apart and still count as touching")
        if s and s[0] == "UNKNOWN_THRESHOLD":
            return ("The app answers only when it sees both hands clearly" if s[2] > s[1]
                    else "The app answers even when it sees the hands less clearly")
        return plain_change(patch)

    def plain_reason(why):
        why = why or ""
        if why.startswith("no improvement"):
            return "no better than the setting in use"
        if why.startswith("exact_match fell"):
            return "fewer gestures read right"
        m = re.search(r"class (\S+) fell ([0-9.]+) -> ([0-9.]+)", why)
        if m:
            return f"{plain_class(m.group(1))} would drop from {float(m.group(2)) * 100:.0f}% to {float(m.group(3)) * 100:.0f}%"
        if why.startswith("condition"):
            return "worse for one camera position"
        if "false_unknown" in why:
            return "refuses too many real gestures"
        m = re.search(r"exact_match ([0-9.]+) -> ([0-9.]+)", why)
        if m:
            return f"more gestures read right ({float(m.group(1)) * 100:.1f}% → {float(m.group(2)) * 100:.1f}%)"
        return why

    def names_for(versions):
        out, fixes, refreshes = {}, 0, 0
        for v in versions:
            if v.get("kind") == "patch" or (v.get("version") or 0) > 0 and not v.get("kind"):
                fixes += 1
                out[v["tag"]] = f"Fix {fixes}"
            elif v.get("kind") == "data_refresh":
                refreshes += 1
                out[v["tag"]] = "New data" if refreshes == 1 else f"New data {refreshes}"
            else:
                out[v["tag"]] = "Start"
        return out

    def said(out):
        if not out or out.get("method") == "unknown":
            return "not sure"
        return f"{out.get('left')}×{out.get('right')} {'touching' if out.get('contact') else 'not touching'}"

    def same_pose(a, b):
        if (a or {}).get("method") == "unknown" or (b or {}).get("method") == "unknown":
            return (a or {}).get("method") == (b or {}).get("method")
        return (all((a or {}).get(k) == (b or {}).get(k) for k in ("method", "left", "right"))
                and bool((a or {}).get("contact")) == bool((b or {}).get("contact")))

    def pill(text, tone="neutral", dot=False):
        return f'<span class="tf-pill tf-{tone}">{"<i class=tf-dot></i>" if dot else ""}{text}</span>'

    def section(anchor, number, eyebrow, title, lede=""):
        return mo.Html(f'<header id="{anchor}" class="tf tf-section"><div class="tf-eyebrow"><span class="tf-num">{number:02d}</span>'
                       f'<span>{eyebrow}</span></div><h2 class="tf-h2">{title}</h2>{f"<p class=tf-lede>{lede}</p>" if lede else ""}</header>')

    def steps(*items):
        return mo.Html('<div class="tf tf-steps">' + "".join(f'<span class="tf-step"><b>{k + 1}</b>{t}</span>'
                                                            for k, t in enumerate(items)) + "</div>")

    def kicker(title, value, note):
        return mo.Html(f'<div class="tf"><div class="tf-kicker"><span class="tf-kicker-title">{title}</span>'
                       f'<span class="tf-mono tf-kicker-value">{value}</span></div><div class="tf-kicker-note">{note}</div></div>')

    def limits(items):
        return mo.Html(f'<div class="tf tf-limits">{"".join(pill(i, "warn") for i in items)}</div>') if items else mo.md("")

    def signed(x):
        """A change in percentage points with two decimals and a true minus sign, from a fraction."""
        return "n/a" if x is None else f"{'+' if x >= 0 else '−'}{abs(x) * 100:.2f}"

    def retro_summary(r):
        """The retrospective result in words that follow the numbers: the measured change, then what its 95% interval
        allows. Nothing here assumes the change is positive."""
        p = (r or {}).get("paired") or {}
        d, (lo, hi) = p.get("mean_difference"), (p.get("ci95") or [None, None])
        if d is None:
            return None
        includes_zero = lo is None or hi is None or lo <= 0 <= hi
        if d > 0 and not includes_zero:
            badge, text = ("Observed gain · interval above zero",
                           "The updated rules scored higher on this retrospective evaluation, and the uncertainty "
                           "interval stays above no improvement.")
        elif d > 0:
            badge, text = ("Observed gain · uncertainty remains",
                           "The updated rules scored higher on this retrospective evaluation. The uncertainty interval "
                           "includes no improvement, so this result does not yet establish a reliable gain.")
        elif d < 0 and not includes_zero:
            badge, text = ("Observed drop · interval below zero",
                           "The updated rules scored lower on this retrospective evaluation, and the uncertainty "
                           "interval stays below no change.")
        elif d < 0:
            badge, text = ("Observed drop · uncertainty remains",
                           "The updated rules scored lower on this retrospective evaluation. The uncertainty interval "
                           "includes no change, so this result does not establish a reliable drop.")
        else:
            badge, text = ("No change observed", "The updated rules scored the same on this retrospective evaluation.")
        return {"badge": badge, "text": text, "gain": f"{signed(d)} pts",
                "ci": f"{signed(lo)} to {signed(hi)} pts" if lo is not None and hi is not None else "",
                "a": pct(((r.get("a") or {}).get("exact_match")), 2), "b": pct(((r.get("b") or {}).get("exact_match")), 2)}

    return (RETRO_LABEL, basis, comparable, em, kicker, limits, names_for, pct, pill, plain_change, plain_class,
            plain_effect, plain_reason, pts, retro_summary, said, same_pose, section, steps, when)


@app.cell
def _(compare, explorer, informed, snap):
    _best_sha = snap.get("best_version") or ""
    best_version = next((v for v in reversed(informed) if v.get("sha") and _best_sha
                         and (v["sha"].startswith(_best_sha[:7]) or _best_sha.startswith(v["sha"][:7]))), None)
    _rules = (best_version or {}).get("rules_sha256")
    _data = ((best_version or {}).get("data") or {}).get("sha")
    _scorers = (((best_version or {}).get("scorers") or {}).get("sha256")
                or ((best_version or {}).get("train") or {}).get("scorers_sha256"))
    _retro = snap.get("retrospective_validation") or {}
    # what each block of the page was computed from must be the version in use, on the same data, with the same scorers
    checks = {
        "hands": bool(explorer and _rules and explorer.get("rules_sha256") == _rules and explorer.get("data_sha") == _data),
        "compare": bool(compare and _rules and _scorers and compare.get("data_sha") == _data
                        and compare.get("scorers_sha256") == _scorers and (compare.get("b") or {}).get("rules_sha256") == _rules),
        "retro": bool(_retro and _rules and (_retro.get("b") or {}).get("rules_sha256") == _rules),
    }
    missing = [label for label, present in (("versions", informed), ("hand data and threshold sweep", explorer),
                                            ("retrospective check", _retro)) if not present]
    unverified = [label for key, label, present in (("hands", "hand data", explorer), ("compare", "before and after", compare),
                                                     ("retro", "retrospective check", _retro)) if present and not checks[key]]
    return best_version, checks, missing, unverified


@app.cell
def _(best_version, checks, comparable, compare, em, icon, informed, missing, mo, names_for, pill, pts, refused,
      refused_known, retro_summary, snap, unverified, when):
    _names = names_for(informed)
    _scored = [v for v in informed if em(v.get("train")) is not None]
    _last = _scored[-1] if _scored else None
    _in_use = best_version or _last
    _in_use_name = _names.get((_in_use or {}).get("tag"), "the rule in use")
    if compare and checks["compare"]:
        _a, _b = compare["a"], compare["b"]
        _holds, _near_holds = compare.get("holds"), compare.get("near_holds")
    else:
        _first = next((v for v in _scored if comparable(v, _last)), None) if _last else None
        _pairable = _first is not None and _first is not _last
        _a = ((_first or {}).get("train") or {}) if _pairable else {}
        _b = (_last or {}).get("train") or {}
        _holds, _near_holds = _b.get("n_holds"), None

    def _out_of_100(key):
        x, y = _a.get(key), _b.get(key)
        if y is None:
            return '<span class="tf-from">n/a</span>'
        now = f'{y * 100:.0f}<span class="tf-unit">out of 100</span>'
        return now if x is None else f'<span class="tf-from">{x * 100:.0f}</span><span class="tf-arrow">→</span>{now}'

    def _gain(key):
        x, y = _a.get(key), _b.get(key)
        if y is None:
            return ""
        if x is None:
            return pill("comparison not verified", "warn")
        return pill(pts(y - x), "good" if y > x else ("bad" if y < x else "neutral"))

    def _kpi(label, value, foot, badge=""):
        return (f'<div class="tf-kpi"><div class="tf-kpi-top"><span class="tf-kpi-label">{label}</span>{badge}</div>'
                f'<div class="tf-kpi-value">{value}</div><div class="tf-kpi-foot">{foot}</div></div>')

    _retro = snap.get("retrospective_validation") or {}
    _rs = retro_summary(_retro)
    _kept = sum(1 for v in informed if v.get("kind") == "patch")
    _spend = sum(v.get("spent_usd") or 0 for v in informed if v.get("kind") == "patch")
    _kpis = "".join([
        _kpi("Gestures the app reads right", _out_of_100("exact_match"),
             f"before the AI → now · {_holds} practice gestures" if _holds else "practice gestures", _gain("exact_match")),
        _kpi("Hands that almost touch, read right", _out_of_100("near_contact_accuracy"),
             f"the hardest case · {_near_holds} practice gestures" if _near_holds else "the hardest case", _gain("near_contact_accuracy")),
        _kpi("Retrospective evaluation", _rs["gain"] if _rs else '<span class="tf-from">not run</span>',
             (f"{_rs['a']} → {_rs['b']} on {_retro.get('holds')} gestures kept aside · not certain yet" if _rs else "gestures kept aside"),
             pill("not verified", "warn") if _retro and not checks["retro"] else ""),
        _kpi("AI changes tested",
             f'{_kept}<span class="tf-unit">kept</span> <span class="tf-from">·</span> '
             + (f'{len(refused)}<span class="tf-unit">refused</span>' if refused_known else '<span class="tf-unit">refusals not recorded</span>'),
             f"${_spend:.2f} of AI time" if _spend else ""),
    ])
    _limits = [f"missing: {m}" for m in missing] + [f"not verified: {u}" for u in unverified]
    _limits_html = (f'<div class="tf-limits"><span class="tf-label">Limits of this snapshot</span>'
                    f'{"".join(pill(l, "warn") for l in _limits)}</div>') if _limits else ""
    _status = pill(f"Rule in use: {_in_use_name} · {when(_in_use.get('ts'))}", "dark", dot=True) if _in_use else ""
    mo.Html(
        f'<div class="tf"><div class="tf-topbar"><div class="tf-brand"><span class="tf-mark">N</span><span>Nimble</span></div>'
        f'<div class="tf-stack">{pill("Traced in W&amp;B Weave")}{pill("Agents on W&amp;B Inference · CoreWeave")}'
        f'{pill("Built with marimo")}{_status}</div></div>'
        '<div class="tf-hero"><div class="tf-eyebrow">Nimble · learn multiplication with your fingers</div>'
        '<h1 class="tf-h1">An AI loop that teaches an app to <em>read</em> children\'s hands.</h1>'
        '<p class="tf-sub-hero">Kids answer a multiplication by touching two fingertips in front of the camera. '
        'Before it can check the answer, the app has to see which fingers touch.</p></div>'
        f'{_limits_html}<div class="tf-kpis">{_kpis}</div>'
        f'<div class="tf-measure">{icon("info", 16)}<span>A gesture counts as read right only if the app gets both fingers '
        'and the touch right. These are scores of the app, not grades of children.</span></div></div>'
    )
    return


@app.cell
def _(checks, compare, explorer, limits, mo, plain_class, section, steps):
    example_options = {plain_class(_x["class"]).capitalize(): _i for _i, _x in enumerate((explorer or {}).get("examples", []))}
    example_pick = mo.ui.radio(options=example_options, value=next(iter(example_options), None), inline=True) if example_options else None
    mo.vstack([
        section("see", 1, "Hands, before and after", "Almost touching is the <em>hard case</em>.",
                "To answer 8 × 7, a child touches finger 8 of one hand to finger 7 of the other. Seen from the camera, "
                "fingers 2 cm apart look almost like fingers that touch. If the app gets that wrong, it corrects a child "
                "who did nothing wrong, or checks an answer the child has not given yet."),
        limits(["not verified: this hand data does not match the rule in use"] if explorer and not checks["hands"] else []),
        # the steps come before the gesture picker, which is step 1
        (steps("Pick a gesture", "Switch between the old setting and the AI's setting", "See whether the app reads the hands right")
         if compare else steps("Pick a gesture", "Move the touch limit")) if example_pick is not None else mo.md(""),
        example_pick if example_pick is not None else mo.callout(mo.md("**No hand data in this snapshot.** This section needs a snapshot built on the practice split."), kind="warn"),
    ], gap=1)
    return (example_pick,)


@app.cell
def _(explorer, mo):
    _in_use = ((explorer or {}).get("constants") or {}).get("CONTACT_THRESHOLD")
    get_limit, set_limit = mo.state(_in_use if isinstance(_in_use, (int, float)) else 0.35)
    return get_limit, set_limit


@app.cell
def _(compare, explorer, get_limit, mo, set_limit):
    _in_use = ((explorer or {}).get("constants") or {}).get("CONTACT_THRESHOLD")
    _before = (((compare or {}).get("a") or {}).get("constants") or {}).get("CONTACT_THRESHOLD")
    _grid = [a["threshold"] for a in ((((explorer or {}).get("examples") or [{}])[0]).get("answers_by_contact_threshold") or [])]
    _steps = sorted({round(float(x), 4) for x in _grid + [v for v in (_in_use, _before) if isinstance(v, (int, float))]})
    if len(_steps) > 1:
        limit_slider = mo.ui.slider(steps=_steps, value=min(_steps, key=lambda s: abs(s - get_limit())), on_change=set_limit,
                                    label="Touch limit", show_value=False, full_width=True)
    else:
        limit_slider = mo.ui.slider(start=0.10, stop=0.50, step=0.0125, value=0.35, label="Touch limit", disabled=True, full_width=True)
    return (limit_slider,)


@app.cell
def _(compare, explorer, mo, set_limit):
    # the buttons live in their own cell: a state set from here re-renders the slider cell, so the slider follows them
    _in_use = ((explorer or {}).get("constants") or {}).get("CONTACT_THRESHOLD")
    _before = (((compare or {}).get("a") or {}).get("constants") or {}).get("CONTACT_THRESHOLD")
    old_rule_button = mo.ui.button(label=f"Old setting · {_before:g}" if _before is not None else "Old setting · unknown",
                                   disabled=_before is None, on_click=lambda _: set_limit(_before),
                                   tooltip="The touch limit written by hand before any AI; the rest of the rule stays as in use")
    ai_rule_button = mo.ui.button(label=f"AI's setting · {_in_use:g}" if _in_use is not None else "AI's setting · unknown",
                                  kind="success", disabled=_in_use is None, on_click=lambda _: set_limit(_in_use),
                                  tooltip="The touch limit the AI found, used by the app today")
    return ai_rule_button, old_rule_button


@app.cell
def _(ACCENT, BAD, GOOD, HAND_BONES, INK2, LEFT_HAND, RIGHT_HAND, ai_rule_button, alt, compare, example_pick, explorer,
      get_limit, icon, kicker, limit_slider, mo, old_rule_button, pd, said, same_pose, steps, style):
    if example_pick is None or not explorer:
        _view = mo.md("")
    else:
        _ex = explorer["examples"][example_pick.value]
        _limit = get_limit()
        _current = (explorer.get("constants") or {}).get("CONTACT_THRESHOLD")
        _pts, _bones, _tips = [], [], []
        for _hand in ("left", "right"):
            _p = _ex[_hand]
            _pts += [{"hand": _hand, "x": _x, "y": _y} for _x, _y in _p]
            _bones += [{"hand": _hand, "x": _p[_a][0], "y": _p[_a][1], "x2": _p[_b][0], "y2": _p[_b][1]} for _a, _b in HAND_BONES]
            _tips += [{"hand": _hand, "x": _p[_ti][0], "y": _p[_ti][1], "finger": str(_f)} for _f, _ti in zip(_ex["fingers"], _ex["tip_index"])]
        _d = _ex["tip_distances"]
        _li, _rj = min(((i, j) for i in range(5) for j in range(5)), key=lambda ij: _d[ij[0]][ij[1]])
        _gap = _d[_li][_rj]
        _lt, _rt = _ex["left"][_ex["tip_index"][_li]], _ex["right"][_ex["tip_index"][_rj]]
        _grid = _ex.get("answers_by_contact_threshold") or []
        _exact = next((a for a in _grid if abs(a["threshold"] - _limit) < 1e-6), None)
        if _current is not None and abs(_limit - _current) < 1e-6:
            _answer = _ex["running"]  # the rule in use, exactly as the snapshot recorded it
        else:
            _answer = _exact or (min(_grid, key=lambda a: abs(a["threshold"] - _limit)) if _grid else _ex["running"])
        _right = same_pose(_answer, _ex["label"])
        _color = GOOD if _right else BAD
        _xs, _ys = [p["x"] for p in _pts], [p["y"] for p in _pts]
        _cx, _cy = (min(_xs) + max(_xs)) / 2, (min(_ys) + max(_ys)) / 2
        _half = max(max(_xs) - min(_xs), max(_ys) - min(_ys)) / 2 * 1.15
        _sx = alt.Scale(domain=[_cx - _half, _cx + _half], nice=False)
        _sy = alt.Scale(domain=[_cy - _half, _cy + _half], reverse=True, nice=False)
        _hand_color = alt.Color("hand:N", scale=alt.Scale(domain=["left", "right"], range=[LEFT_HAND, RIGHT_HAND]), legend=None)
        _pair = pd.DataFrame([{"x": _lt[0], "y": _lt[1], "x2": _rt[0], "y2": _rt[1]}])
        _hands = alt.layer(
            alt.Chart(pd.DataFrame(_bones)).mark_rule(strokeWidth=3.5, strokeCap="round", opacity=0.9).encode(
                x=alt.X("x:Q", scale=_sx, axis=None), y=alt.Y("y:Q", scale=_sy, axis=None), x2="x2:Q", y2="y2:Q", color=_hand_color),
            alt.Chart(_pair).mark_rule(strokeWidth=3, strokeDash=[4, 3], color=_color).encode(
                x=alt.X("x:Q", scale=_sx), y=alt.Y("y:Q", scale=_sy), x2="x2:Q", y2="y2:Q"),
            alt.Chart(pd.DataFrame(_tips)).mark_circle(size=280, opacity=1, stroke="white", strokeWidth=2).encode(
                x=alt.X("x:Q", scale=_sx), y=alt.Y("y:Q", scale=_sy), color=_hand_color),
            alt.Chart(pd.DataFrame(_tips)).mark_text(fontSize=10, fontWeight=600, color="white").encode(
                x=alt.X("x:Q", scale=_sx), y=alt.Y("y:Q", scale=_sy), text="finger:N"),
        ).properties(width=330, height=330)

        _zones = pd.DataFrame([{"from": 0, "to": _limit, "zone": "counts as touching"}, {"from": _limit, "to": 0.6, "zone": "counts as not touching"}])
        _marks = pd.DataFrame([{"x": _limit, "t": "touch limit", "c": ACCENT}, {"x": _gap, "t": "gap on this hand", "c": _color}])
        _ruler = alt.layer(
            alt.Chart(_zones).mark_bar(height=30, cornerRadius=6).encode(
                x=alt.X("from:Q", scale=alt.Scale(domain=[0, 0.6]), title=None, axis=None),
                x2="to:Q", color=alt.Color("zone:N", scale=alt.Scale(domain=["counts as touching", "counts as not touching"],
                                                                   range=["#E0E7FF", "#F5F5F4"]), legend=None)),
            alt.Chart(_zones.iloc[[0]]).mark_text(align="left", dx=10, fontSize=12, color=INK2).encode(x="from:Q", text="zone:N"),
            alt.Chart(_zones.iloc[[1]]).mark_text(align="right", dx=-10, fontSize=12, color=INK2).encode(x="to:Q", text="zone:N"),
            alt.Chart(pd.DataFrame([{"x": _limit}])).mark_rule(color=ACCENT, strokeWidth=2).encode(x="x:Q"),
            alt.Chart(pd.DataFrame([{"x": _gap}])).mark_tick(thickness=4, size=44, color=_color).encode(x="x:Q"),
            alt.Chart(_marks.iloc[[1]]).mark_text(dy=-28, fontSize=12, fontWeight=600, color=_color).encode(x="x:Q", text="t:N"),
            alt.Chart(_marks.iloc[[0]]).mark_text(dy=30, fontSize=12, fontWeight=600, color=ACCENT).encode(x="x:Q", text="t:N"),
        ).properties(width="container", height=96)

        _verdict = mo.Html(
            f'<div class="tf tf-verdict {"tf-v-good" if _right else "tf-v-bad"}"><div class="tf-verdict-head">'
            f'<span class="tf-verdict-icon">{icon("check" if _right else "x", 17, "#fff", 2.6)}</span>'
            f'{"The app reads it right" if _right else "The app reads it wrong"}</div>'
            f'<dl class="tf-rows"><dt>App sees</dt><dd>{said(_answer)}</dd><dt>Child did</dt><dd>{said(_ex["label"])}</dd></dl></div>')
        _keys = mo.Html(
            f'<div class="tf tf-keys"><span class="tf-key"><i class="tf-swatch" style="background:{LEFT_HAND}"></i>left hand</span>'
            f'<span class="tf-key"><i class="tf-swatch" style="background:{RIGHT_HAND}"></i>right hand</span>'
            '<span class="tf-key"><i class="tf-dash"></i>gap between the two closest fingertips</span></div>')
        _view = mo.vstack([
            mo.hstack([
                mo.vstack([mo.ui.altair_chart(style(_hands), chart_selection=False, legend_selection=False), _keys], gap=0.5),
                mo.vstack([_verdict,
                           kicker("Try it on this gesture", f"touch limit {_limit:.4g}",
                                  "A gap smaller than the touch limit counts as touching. Limits are in palm lengths "
                                  "(0.35 is about a third of a palm). Precomputed; this does not change the app."),
                           mo.hstack([old_rule_button, ai_rule_button], justify="start", wrap=True, gap=0.5),
                           limit_slider, mo.ui.altair_chart(style(_ruler), chart_selection=False, legend_selection=False)], gap=1),
            ], wrap=True, gap=2, align="start"),
        ], gap=1)
    _view
    return


@app.cell
def _(compare, checks, icon, informed, mo, pill, refused, refused_known, section):
    _guard = sum(1 for r in refused if r.get("stage") == "guard")
    _gate = sum(1 for r in refused if r.get("stage") != "guard")
    _kept = sum(1 for v in informed if v.get("kind") == "patch")
    _unknown = "" if refused_known else pill("refusals not recorded")
    _stages = [
        ("camera", "", "New gestures", "Hand points, no images", "camera", ""),
        ("search", " tf-ai", "Diagnosis agent", "Finds the app's most common mistake", "W&B Inference · Qwen3", ""),
        ("code", " tf-ai", "Patch agent", "Changes a setting to fix it", "Claude Code", ""),
        ("shield", " tf-ai", "Guard agent", "Refuses changes off topic or against the rules", "W&B Inference",
         pill(f"{_guard} refused", "bad") if _guard else _unknown),
        ("scale", " tf-judge", "Referee", "Refuses a change if any gesture drops by 5 points", "metric gate",
         pill(f"{_gate} refused", "bad") if _gate else _unknown),
        ("commit", " tf-judge", "Kept", "Goes into the app", "W&B Weave",
         pill(f"{_kept} kept", "good") if informed else pill("no versions")),
    ]
    _cards = "".join(
        f'<div class="tf-stage{_kind}"><div class="tf-stage-head"><span class="tf-stage-icon">{icon(_icon, 18)}</span>'
        f'<span class="tf-stage-step">{_k + 1:02d}</span></div><div class="tf-stage-name">{_name}</div>'
        f'<div class="tf-stage-text">{_text}</div><div class="tf-stage-foot"><span class="tf-stage-tech">{_tech}</span>{_count}</div></div>'
        for _k, (_icon, _kind, _name, _text, _tech, _count) in enumerate(_stages))
    _a, _b = ((compare or {}).get("a") or {}, (compare or {}).get("b") or {}) if checks["compare"] else ({}, {})
    _impact = (f'<div class="tf-impact">Impact of the {_kept} kept changes: the app reads <b>{_a["exact_match"] * 100:.0f}</b> → '
               f'<b>{_b["exact_match"] * 100:.0f}</b> practice gestures out of 100, and <b>{_a["near_contact_accuracy"] * 100:.0f}</b> → '
               f'<b>{_b["near_contact_accuracy"] * 100:.0f}</b> almost-touching ones.</div>'
               if _a.get("exact_match") is not None and _a.get("near_contact_accuracy") is not None else "")
    mo.vstack([
        section("loop", 2, "How a change is accepted or refused", "Agents propose. <em>Two judges</em> can say no.",
                "An AI change can raise the average while breaking one gesture a child needs. So nothing reaches the app "
                "unless two independent checks accept it."),
        mo.Html(f'<div class="tf"><div class="tf-loop">{_cards}</div><div class="tf-loopback">{icon("loop", 14)}'
                f'Repeats when new gestures arrive</div>{_impact}</div>'),
    ])
    return


@app.cell
def _(informed, mo):
    _default = next((v["tag"] for v in reversed(informed) if v.get("kind") == "patch"), informed[-1]["tag"] if informed else "")
    get_selected, set_selected = mo.state(_default)
    return get_selected, set_selected


@app.cell
def _(ACCENT, INK, INK2, alt, basis, em, get_selected, informed, mo, names_for, pd, pill, refused, section, style):
    version_names = names_for(informed)
    _selected = get_selected()
    _rows, _segment, _previous, _unknown = [], 0, None, False
    for _v in informed:
        _basis = basis(_v)
        _unknown = _unknown or _basis is None
        if _previous is None or _basis is None or _basis != basis(_previous):
            _segment += 1  # a line only joins versions measured on identified, identical data and scorers
        _previous = _v
        _rows.append({"tag": _v["tag"], "name": version_names[_v["tag"]], "kind": _v.get("kind") or "patch",
                      "right": em(_v.get("train")), "segment": _segment, "label": f"{(em(_v.get('train')) or 0) * 100:.1f}%"})
    versions_df = pd.DataFrame(_rows if _rows else [{"tag": "", "name": "", "kind": "baseline", "right": None, "segment": 0, "label": ""}])
    _names = [r["name"] for r in _rows]
    _values = [r["right"] for r in _rows if r["right"] is not None]
    _lo = max(0.0, (min(_values) if _values else 0.4) - 0.05)
    _hi = min(1.0, (max(_values) if _values else 0.6) + 0.04)
    _x = alt.X("name:N", sort=_names, title=None, scale=alt.Scale(paddingOuter=0.3),
               axis=alt.Axis(labelAngle=0, labelFontSize=13, labelColor=INK, domain=False, ticks=False, labelPadding=12,
                             labelExpr="split(datum.label, ' ')"))
    _y = alt.Y("right:Q", title=None, scale=alt.Scale(domain=[_lo, _hi]), axis=alt.Axis(format=".0%", tickCount=4))
    _base = alt.Chart(versions_df)
    _refresh = _base.transform_filter(alt.datum.kind == "data_refresh")
    _layers = [
        _refresh.mark_rule(strokeDash=[3, 3], color="#A8A29E").encode(x=_x),
        _refresh.mark_text(align="left", dx=8, y=6, fontSize=11, color=INK2).encode(x=_x, text=alt.value("20% of recordings set aside here")),
        _refresh.mark_text(align="left", dx=8, y=20, fontSize=11, color=INK2).encode(x=_x, text=alt.value("so scores restart on a new scale")),
        _base.mark_line(interpolate="step-after", strokeWidth=2.5, color=ACCENT).encode(x=_x, y=_y, detail="segment:N"),
        _base.mark_circle(opacity=1, stroke="white", strokeWidth=2.5, cursor="pointer").encode(
            x=_x, y=_y, tooltip=[alt.Tooltip("name:N", title="version"), alt.Tooltip("right:Q", format=".1%", title="read right")],
            color=alt.Color("kind:N", scale=alt.Scale(domain=["baseline", "patch", "data_refresh"], range=["#A8A29E", ACCENT, "#78716C"]), legend=None),
            size=alt.condition(alt.FieldEqualPredicate(field="tag", equal=_selected), alt.value(560), alt.value(200))).properties(name="dots"),
        _base.mark_text(dy=-18, fontSize=12, fontWeight=600, color=INK).encode(x=_x, y=_y, text="label:N"),
    ]
    _layered = alt.layer(*_layers).properties(width="container", height=240)
    # the click selection sits at the top of the layered chart, on the dots only: marimo reads selections there
    _layered.params = [alt.TopLevelSelectionParameter(name="version", views=["dots"],
                                                      select=alt.PointSelectionConfig(type="point", fields=["tag"], on="click"))]
    version_chart = mo.ui.altair_chart(style(_layered), chart_selection=False, legend_selection=False)
    _kept = sum(1 for v in informed if v.get("kind") == "patch")
    mo.vstack([
        section("versions", 3, "Every change, measured",
                (f"{_kept} changes kept. {len(refused)} refused." if refused else f"{_kept} changes kept.") if _rows else "No versions yet.",
                "Each dot is the app's score after a change, on practice gestures. Click a dot, or pick it in the menu below."
                if _rows else "This snapshot has no evaluated versions."),
        mo.Html(f'<div class="tf tf-chart-head"><span>Gestures read right, per version</span>'
                f'{pill("Zoomed axis") if _lo > 0 else ""}</div>') if _rows else mo.md(""),
        version_chart if _rows else mo.md(""),
        mo.Html('<div class="tf tf-kicker-note">A version without identified data and scorers stands alone: comparison not verified.</div>')
        if _unknown else mo.md(""),
    ], gap=1)
    return version_chart, version_names, versions_df


@app.cell
def _(set_selected, version_chart, versions_df):
    try:
        _clicked = version_chart.apply_selection(versions_df)
        if 0 < len(_clicked) < len(versions_df):
            set_selected(_clicked["tag"].iloc[0])
    except Exception:
        pass
    return


@app.cell
def _(get_selected, informed, mo, set_selected, version_names):
    _options = {version_names[v["tag"]]: v["tag"] for v in reversed(informed)}
    version_menu = mo.ui.dropdown(
        options=_options or {"no versions": ""},
        value=next((name for name, tag in _options.items() if tag == get_selected()), None) if _options else "no versions",
        on_change=set_selected, label="Version", disabled=not _options)
    return (version_menu,)


@app.cell
def _(comparable, em, get_selected, html, informed, mo, pct, pill, plain_change, plain_class, plain_effect, plain_reason,
      pts, version_menu, version_names):
    _tag = get_selected()
    _i = next((i for i, v in enumerate(informed) if v["tag"] == _tag), len(informed) - 1)
    if not informed:
        _out = mo.md("")
    else:
        _v = informed[_i]
        _prev = informed[_i - 1] if _i > 0 else None
        _ok = comparable(_prev, _v)
        _now = (_v.get("train") or {}).get("per_class") or {}
        _before = (((_prev or {}).get("train") or {}).get("per_class") or {}) if _ok else {}
        _moves = sorted(((c, (_now[c] or 0) - (_before.get(c) or 0)) for c in _now if c in _before
                         and abs((_now[c] or 0) - (_before.get(c) or 0)) > 1e-9), key=lambda cm: -cm[1])
        _shown = [m for m in _moves if m[1] > 0][:2] + [m for m in _moves if m[1] < 0][-2:]
        _moves_html = "".join(
            f'<div class="tf-move"><span>{plain_class(c)}</span><span class="{"tf-up" if d > 0 else "tf-down"}">{d * 100:+.0f} pts</span></div>'
            for c, d in _shown)
        _name = version_names.get(_v["tag"], _v["tag"])
        if _v.get("kind") == "patch":
            _delta = (em(_v.get("train")) - em(_prev.get("train"))) if _ok and em(_v.get("train")) is not None and em(_prev.get("train")) is not None else None
            _cards = (
                f'<div class="tf-card"><span class="tf-label">What changed</span><div class="tf-card-title">{html.escape(plain_effect(_v.get("patch")))}</div>'
                f'<div class="tf-quiet">{html.escape(plain_change(_v.get("patch")))}</div></div>'
                f'<div class="tf-card"><span class="tf-label">Effect</span>'
                + (f'<div class="tf-big">{pts(_delta)}</div><div class="tf-quiet">gestures read right, {pct(em(_prev.get("train")))} → {pct(em(_v.get("train")))}</div>'
                   f'<div class="tf-moves">{_moves_html}</div>' if _delta is not None else '<div class="tf-card-text">comparison not verified</div>')
                + '</div>'
                f'<div class="tf-card"><span class="tf-label">Why it was kept</span><div class="tf-card-title">No gesture got worse by 5 points or more</div>'
                f'<div class="tf-quiet">Referee: {html.escape(plain_reason(_v.get("gate")))}</div></div>')
        elif _v.get("kind") == "data_refresh":
            _cards = ('<div class="tf-card"><span class="tf-label">New data</span><div class="tf-card-title">20% of the recordings were set aside</div>'
                      '<div class="tf-card-text">They are kept for the retrospective evaluation. Scores from here are measured '
                      'on the other 80%, so they are not compared with the scores before.</div></div>')
        else:
            _cards = ('<div class="tf-card"><span class="tf-label">Start</span><div class="tf-card-title">The setting a developer wrote by hand</div>'
                      f'<div class="tf-card-text">Before any AI: {pct(em(_v.get("train")))} of practice gestures read right.</div></div>')
        _tech = []
        if _v.get("diagnosis") or _v.get("patch_hypothesis"):
            _tech.append("\n\n".join(f"**{label}** {_v[key]}" for key, label in (("diagnosis", "Agent diagnosis:"),
                                     ("patch_hypothesis", "Agent measurement:"), ("expected", "Agent expectation:")) if _v.get(key)))
        if _v.get("diff"):
            _tech.append(f"```diff\n{_v['diff']}\n```")
        _tech.append(" · ".join([f"commit `{(_v.get('sha') or '')[:8]}`", f"rules `{(_v.get('rules_sha256') or 'unknown')[:8]}`",
                                 f"data `{(_v.get('data') or {}).get('sha') or 'unknown'}`",
                                 f"scorers `{(((_v.get('scorers') or {}).get('sha256')) or 'unknown')[:8]}` "
                                 f"({(_v.get('scorers') or {}).get('source', 'not identified')})"]))
        _out = mo.vstack([
            mo.hstack([mo.Html(f'<div class="tf tf-card-title" style="font-size:20px">{_name}</div>'), version_menu],
                      justify="space-between", align="center", wrap=True),
            mo.Html(f'<div class="tf tf-grid3">{_cards}</div>'),
            mo.accordion({"Technical details (for engineers)": mo.md("\n\n".join(_tech))}),
        ], gap=1)
    _out
    return


@app.cell
def _(comparable, em, html, informed, mo, pill, plain_change, plain_reason, pts, refused, version_names, when):
    _events = []
    _previous = None
    for _v in informed:
        _kind = _v.get("kind") or "patch"
        if _kind == "patch":
            _ok = comparable(_previous, _v) and em(_v.get("train")) is not None and em(_previous.get("train")) is not None
            _events.append((str(_v.get("ts") or ""), 1, pill("Kept", "good"),
                            f"{version_names.get(_v['tag'], _v['tag'])}: {html.escape(plain_change(_v.get('patch')))}",
                            pts(em(_v.get("train")) - em(_previous.get("train"))) if _ok else "not verified"))
        elif _kind == "data_refresh":
            _events.append((str(_v.get("ts") or ""), 1, pill("Data", "accent"), "20% of recordings set aside for the retrospective evaluation", ""))
        else:
            _events.append(("", 0, pill("Start"), "Setting written by hand", ""))
        _previous = _v
    for _r in refused:
        _text = ("Guard agent: the change did not fix the diagnosed mistake or broke a loop rule" if _r.get("stage") == "guard"
                 else "Referee: " + plain_reason(_r.get("reason")))
        _events.append((str(_r.get("ts") or ""), 1, pill("Refused", "bad"),
                        f'<span title="{html.escape(str(_r.get("reason") or ""))}">{html.escape(_text)}</span>', ""))
    _events.sort(key=lambda e: (e[1], e[0]))
    _kept_ts = sorted(str(v.get("ts") or "") for v in informed if v.get("kind") == "patch")
    _refused_ts = sorted(str(r.get("ts") or "") for r in refused)
    _why = ('<span style="font-weight:600">Why the first ideas were refused:</span> each one would have broken another gesture or ignored the loop\'s rules. '
            "We then let the AI test every idea against the referee before proposing it, and its next ideas passed."
            if _kept_ts and _refused_ts and _refused_ts[-1] < _kept_ts[0] else "")
    _rows = "".join(f'<div class="tf-log-row"><span class="tf-log-time">{when(ts) if ts else ""}</span><span>{tag}</span>'
                    f'<span class="tf-log-text">{text}</span><span class="tf-log-right">{right}</span></div>'
                    for ts, _o, tag, text, right in _events)
    mo.Html(f'<div class="tf" style="margin-top:12px"><div class="tf-label" style="margin-bottom:8px">Everything the AI tried, in order (UTC)</div>'
            f'<div class="tf-log">{_rows}</div>{f"<div class=tf-impact>{_why}</div>" if _why else ""}</div>') if _events else mo.md("")
    return


@app.cell
def _(explorer, mo, section):
    _sweep = (explorer or {}).get("sweep") or {}
    sweep_name = "CONTACT_THRESHOLD" if "CONTACT_THRESHOLD" in _sweep else next(iter(_sweep), None)
    section("referee", 4, "The effect across the practice set", "Be the <em>referee</em>.",
            "A touch limit that reads more gestures overall can still break one gesture. The referee keeps a new limit only "
            "if the total goes up and no gesture drops by 5 points. Try it." if sweep_name else "")
    return (sweep_name,)


@app.cell
def _(explorer, mo, sweep_name):
    _s = ((explorer or {}).get("sweep") or {}).get(sweep_name or "", {})
    _rows = _s.get("rows", [])
    _start = min(range(len(_rows)), key=lambda i: abs(_rows[i]["value"] - _s.get("current", 0))) if _rows else 0
    sweep_slider = mo.ui.slider(start=0, stop=max(len(_rows) - 1, 1), step=1, value=_start, show_value=False,
                                label="Touch limit", full_width=True) if _rows else None
    return (sweep_slider,)


@app.cell
def _(ACCENT, BAD, GOOD, INK, INK2, alt, checks, explorer, icon, kicker, limits, mo, pct, pd, pill, plain_reason, pts,
      style, sweep_name, sweep_slider):
    if sweep_slider is None:
        _view = mo.callout(mo.md("**No threshold sweep in this snapshot.** This section needs a snapshot built on the practice split."), kind="warn")
    else:
        _s = explorer["sweep"][sweep_name]
        _rows = _s["rows"]
        _row = _rows[min(sweep_slider.value, len(_rows) - 1)]
        _in_use = min(_rows, key=lambda r: abs(r["value"] - _s["current"]))
        _any_kept = any(r["gate"] == "PASS" for r in _rows)
        _holds = (explorer.get("compare") or {}).get("holds")
        _df = pd.DataFrame([{"limit": r["value"], "right": r["exact_match"], "referee": "keeps" if r["gate"] == "PASS" else "refuses"} for r in _rows])
        _lo, _hi = float(_df["right"].min()), float(_df["right"].max())
        _pad = max(0.01, (_hi - _lo) * 0.2)
        _dom = [max(0, _lo - _pad), min(1, _hi + _pad)]
        _x = alt.X("limit:Q", title="touch limit (palm lengths)", scale=alt.Scale(zero=False), axis=alt.Axis(tickCount=6, labelOverlap=True))
        _y = alt.Y("right:Q", title="gestures read right", scale=alt.Scale(domain=_dom), axis=alt.Axis(format=".0%", tickCount=4))
        _chart = alt.layer(
            alt.Chart(_df).mark_line(color="#D6D3D1", strokeWidth=1.5).encode(x=_x, y=_y),
            alt.Chart(_df).mark_point(size=70, filled=True, opacity=1).encode(
                x=_x, y=_y, color=alt.Color("referee:N", scale=alt.Scale(domain=["keeps", "refuses"], range=[GOOD, "#D6D3D1"]), legend=None),
                tooltip=[alt.Tooltip("limit:Q", title="touch limit"), alt.Tooltip("right:Q", format=".1%", title="read right"), alt.Tooltip("referee:N")]),
            alt.Chart(pd.DataFrame([{"limit": _s["current"]}])).mark_rule(strokeDash=[4, 4], color=INK2).encode(x="limit:Q"),
            alt.Chart(pd.DataFrame([{"limit": _s["current"], "right": _dom[1], "t": "used by the app"}])).mark_text(
                align="left", dx=6, dy=8, color=INK2, fontSize=11).encode(x="limit:Q", y=alt.Y("right:Q", scale=alt.Scale(domain=_dom)), text="t:N"),
            alt.Chart(pd.DataFrame([{"limit": _row["value"], "right": _row["exact_match"]}])).mark_circle(
                size=380, color=ACCENT, stroke="white", strokeWidth=3, opacity=1).encode(x=_x, y=_y),
        ).properties(width="container", height=240)
        _kept = _row["gate"] == "PASS"
        _is_current = abs(_row["value"] - _s["current"]) < 1e-9
        _delta = _row["exact_match"] - _in_use["exact_match"]
        _head = "The referee would keep it" if _kept else ("The limit the app uses today" if _is_current else "The referee would refuse it")
        _note = ("" if _any_kept else "None of the limits tried beats it.") if _is_current else "Because " + plain_reason(_row["why"]) + "."
        _verdict = mo.Html(
            f'<div class="tf tf-verdict {"tf-v-good" if _kept else ""}" style="display:flex;flex-wrap:wrap;gap:12px 32px;align-items:center">'
            f'<div class="tf-verdict-head" style="color:{GOOD if _kept else (INK if _is_current else BAD)}">'
            f'<span class="tf-verdict-icon" style="background:{GOOD if _kept else ("#A8A29E" if _is_current else BAD)}">'
            f'{icon("check" if _kept else ("minus" if _is_current else "x"), 17, "#fff", 2.6)}</span>{_head}</div>'
            f'<dl class="tf-rows" style="margin:0"><dt>Touch limit</dt><dd class="tf-mono">{_row["value"]:g}</dd>'
            f'<dt>Read right</dt><dd class="tf-mono">{pct(_row["exact_match"])} <span class="tf-muted">{"" if _is_current else pts(_delta)}</span></dd></dl>'
            f'<div class="tf-note">{_note}</div></div>')
        _legend = mo.Html(
            f'<div class="tf tf-legend-row"><span><i class="tf-legend-dot" style="background:{ACCENT}"></i>your pick</span>'
            f'<span><i class="tf-legend-dot" style="background:{GOOD}"></i>the referee would keep it</span>'
            '<span><i class="tf-legend-dot" style="background:#D6D3D1"></i>the referee would refuse it</span>'
            f'{pill("Zoomed axis") if _dom[0] > 0 else ""}</div>')
        _view = mo.vstack([
            limits(["not verified: this sweep does not match the rule in use"] if not checks["hands"] else []),
            kicker("See the effect across the practice set", f"touch limit {_row['value']:g}",
                   f"Each dot is the app's score on all {_holds or explorer.get('windows')} practice gestures with that touch limit. "
                   "Precomputed; this does not change the app."),
            sweep_slider, _verdict, _legend,
            mo.ui.altair_chart(style(_chart), chart_selection=False, legend_selection=False),
        ], gap=1)
    _view
    return


@app.cell
def _(ACCENT, INK, MUTED, RETRO_LABEL, checks, compare, icon, informed, math, mo, names_for, pct, pill, pts, retro_summary,
      section, snap):
    _retro = snap.get("retrospective_validation") or {}
    _versions = snap.get("retrospective_versions") or ([_retro] if _retro else [])
    _names = names_for(informed)
    _best = snap.get("best_version") or ""

    def _name_of(commit):
        for v in informed:
            sha = v.get("sha") or ""
            if sha and commit and (sha.startswith(commit[:7]) or commit.startswith(sha[:7])):
                return _names[v["tag"]]
        return (commit or "")[:7]

    def _ci_svg(rows):
        items = [r for r in rows if ((r.get("paired") or {}).get("ci95") or [None])[0] is not None]
        items.sort(key=lambda r: (r["b"]["commit"] != _best, r["b"]["commit"]))
        if not items:
            return ""
        lows = [r["paired"]["ci95"][0] * 100 for r in items]
        highs = [r["paired"]["ci95"][1] * 100 for r in items]
        lo, hi = min(-4, math.floor(min(lows)) - 1), max(10, math.ceil(max(highs)) + 1)
        w, left, right, row_h, top = 470, 70, 96, 40, 34
        h = top + row_h * len(items) + 30

        def sx(v):
            return left + (v - lo) / (hi - lo) * (w - left - right)

        parts = []
        for t in range(int(lo), int(hi) + 1):
            if t % 2 == 0:
                parts.append(f'<line x1="{sx(t):.1f}" x2="{sx(t):.1f}" y1="{top - 10}" y2="{h - 26}" stroke="#F0EFED"/>'
                             f'<text x="{sx(t):.1f}" y="{h - 8}" text-anchor="middle" font-size="11" fill="{MUTED}">{"0" if t == 0 else f"{t:+d}"}</text>')
        parts.append(f'<line x1="{sx(0):.1f}" x2="{sx(0):.1f}" y1="{top - 14}" y2="{h - 26}" stroke="{INK}" stroke-width="1.5"/>'
                     f'<text x="{sx(0):.1f}" y="{top - 20}" text-anchor="middle" font-size="11" fill="{INK}">no change</text>'
                     f'<text x="0" y="{top - 20}" font-size="11" fill="{MUTED}">95% CI</text>'
                     f'<text x="0" y="{h - 8}" font-size="11" fill="{MUTED}">points</text>')
        for k, r in enumerate(items):
            y = top + k * row_h + row_h / 2
            a, b = r["paired"]["ci95"][0] * 100, r["paired"]["ci95"][1] * 100
            est = r["paired"]["mean_difference"] * 100
            in_use = r["b"]["commit"] == _best
            color = ACCENT if in_use else "#A8A29E"
            stop = (est - a) / (b - a) * 100 if b > a else 50
            parts.append(
                f'<defs><linearGradient id="tf-ci-{k}" x1="0" x2="1" y1="0" y2="0"><stop offset="0%" stop-color="{color}" stop-opacity="0.12"/>'
                f'<stop offset="{stop:.1f}%" stop-color="{color}" stop-opacity="0.75"/><stop offset="100%" stop-color="{color}" stop-opacity="0.12"/></linearGradient></defs>'
                f'<rect x="{sx(a):.1f}" y="{y - 7:.1f}" width="{max(sx(b) - sx(a), 2):.1f}" height="14" rx="7" fill="url(#tf-ci-{k})"/>'
                f'<circle cx="{sx(est):.1f}" cy="{y:.1f}" r="6.5" fill="{color}" stroke="#fff" stroke-width="2"/>'
                f'<text x="0" y="{y + 4:.1f}" font-size="13" fill="{ACCENT if in_use else INK}" font-weight="{600 if in_use else 400}">{_name_of(r["b"]["commit"])}</text>'
                f'<text x="{w - right + 8:.1f}" y="{y + 4:.1f}" font-size="13" fill="{INK}">{"+" if est >= 0 else "−"}{abs(est):.2f} pts</text>')
        return (f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Nimble: 95 percent range of the gain over the '
                f'hand-written rule, per version, on the {RETRO_LABEL}">{"".join(parts)}</svg>')

    _rs = retro_summary(_retro)
    _verified = bool(compare and checks["compare"])
    _a, _b = ((compare or {}).get("a") or {}, (compare or {}).get("b") or {}) if _verified else ({}, {})

    def _rung(state, title, text, value="", extra=""):
        mark = icon("check", 14, "#fff", 2.8) if state == "done" else ""
        return (f'<div class="tf-rung tf-r-{state}"><span class="tf-rung-mark">{mark}</span><div><div class="tf-rung-title">{title}</div>'
                f'<div class="tf-rung-text">{text}</div>{extra}</div><div class="tf-rung-value">{value}</div></div>')

    _practice_gain = (_b["exact_match"] - _a["exact_match"]) if _a.get("exact_match") is not None and _b.get("exact_match") is not None else None
    _retro_pills = ((pill(_rs["badge"]) if _rs else pill("not run"))
                    + (pill("not verified", "warn") if _retro and not checks["retro"] else ""))
    _pair = f"Original → {_name_of((_retro.get('b') or {}).get('commit'))}" if _retro else ""
    _method = ((f'<div class="tf-quiet" style="margin-top:10px">Method: these {_retro.get("holds")} gesture holds were set aside after '
                'earlier development had used the full dataset. Participants are not reliably identified. Evaluation on new users '
                f'is still needed. ({RETRO_LABEL})</div>') if _retro else "")
    _rungs = [
        _rung("done" if _verified else "next", f"Training gains {pill('done', 'good') if _verified else pill('not verified', 'warn')}",
              (f"All {pct(_a.get('exact_match'))} → {pct(_b.get('exact_match'))} · almost touching "
               f"{pct(_a.get('near_contact_accuracy'), 0)} → {pct(_b.get('near_contact_accuracy'), 0)} · {compare.get('holds')} gestures, same data and scorers")
              if _verified else "No verified before and after comparison in this snapshot.",
              pts(_practice_gain) if _practice_gain is not None else ""),
        _rung("partial" if _rs else "next", f"Retrospective evaluation {_retro_pills}",
              (f"{_pair}: {_rs['a']} → {_rs['b']}, {_rs['gain']} · 95% CI {_rs['ci']}<br>{_rs['text']}"
               if _rs else "Not in this snapshot."),
              _rs["gain"] if _rs else "",
              (f'<div class="tf-ci">{_ci_svg(_versions)}</div>' if _versions else "") + _method),
        _rung("next", f"Independent evaluation {pill('next')}", "New users the loop has never seen"),
        _rung("later", f"Children's learning {pill('not measured')}",
              "Every check above evaluates gesture recognition, not children's learning outcomes."),
    ]
    mo.vstack([
        section("evidence", 5, "Limits of the evidence", "Where we stand, <em>honestly</em>.",
                "Training gains, the retrospective evaluation and independent evaluation are kept apart."),
        mo.Html(f'<div class="tf"><div class="tf-ladder">{"".join(_rungs)}</div></div>'),
    ], gap=1)
    return


@app.cell
def _(REPO_URL, WEAVE_URL, checks, explorer, icon, informed, mo, snap, snap_source):
    _train = next((v.get("train") for v in reversed(informed) if v.get("train")), None) or {}
    _retro = snap.get("retrospective_validation") or {}
    _all_checked = all(checks.values())
    _items = [
        ("loaded", snap_source),
        ("checked", "rules, data and scorers match the rule in use" if _all_checked else "see the limits at the top"),
        ("rule", (snap.get("best_version") or "")[:8]),
        ("practice data", (explorer or {}).get("data_sha", "")),
        ("scorers", (_train.get("scorers_sha256") or "")[:8]),
        ("validation", (_retro.get("validation_sha256") or "")[:8]),
        ("snapshot", (snap.get("generated_at") or "")[:16].replace("T", " ")),
    ]
    _prov = "".join(f"<span>{k} <b>{v}</b></span>" for k, v in _items if v)
    mo.Html(
        f'<div class="tf"><div class="tf-prov">{_prov}<a href="{WEAVE_URL}" target="_blank" rel="noopener">Nimble traces in Weave {icon("link", 12)}</a>'
        f'<a href="{REPO_URL}" target="_blank" rel="noopener">Nimble code {icon("link", 12)}</a></div>'
        '<div class="tf-closing"><p>The child learns multiplication.<br><em>The app learns to see the child.</em></p></div></div>'
    )
    return


if __name__ == "__main__":
    app.run()
