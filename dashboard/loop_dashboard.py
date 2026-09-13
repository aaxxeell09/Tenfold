"""Tenfold loop report: how AI agents teach a finger multiplication tutor to read children's hands, what they
proved and where the evidence stops. A marimo app over one file, data/snapshot.json, styled by dashboard/tenfold.css.

    make dashboard                               # marimo run dashboard/loop_dashboard.py
    marimo edit dashboard/loop_dashboard.py      # to change it
    TENFOLD_SNAPSHOT=/path/snapshot.json marimo run dashboard/loop_dashboard.py

No W&B key and no repo needed: without a local snapshot it reads the one committed on GitHub, so it runs on molab
and as WebAssembly. The loop rewrites and pushes the snapshot after every accepted version (loop/watch.py).

Few words, each one earning its place: four measured numbers; 01 the loop as six stages with how often each said no;
02 the hard case (almost touching) on real hand landmarks with a touch limit you can move; 03 every version measured,
with kept and refused fixes; 04 be the referee on the real sweep; 05 the evidence ladder: practice gestures, the
validation rétrospective (participants non identifiés) drawn around zero, new people, kids in real use.
Gains are only compared on the same data with the same scorers. Hands come from the practice side only.
"""
import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium", app_title="Tenfold loop report", css_file="tenfold.css")


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
        "pointer": '<path d="M9 11.5V5.2a1.6 1.6 0 0 1 3.2 0V11"/><path d="M12.2 10.2a1.6 1.6 0 0 1 3.2 0V11"/>'
                   '<path d="M15.4 10.8a1.6 1.6 0 0 1 3.2 0v3.4A6.8 6.8 0 0 1 11.8 21h-.6a6.8 6.8 0 0 1-5.6-3L3.3 14.4'
                   'a1.6 1.6 0 0 1 2.6-1.8L9 15"/>',
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
            yield Path(os.environ["TENFOLD_SNAPSHOT"])
        try:
            _dir = mo.notebook_dir()
            if _dir:
                yield Path(_dir).parent / "data" / "snapshot.json"
        except Exception:
            pass
        yield Path.cwd() / "data" / "snapshot.json"

    def _load():
        for _path in _candidates():
            if _path.exists():
                return json.loads(_path.read_text()), str(_path)
        with urllib.request.urlopen(RAW_SNAPSHOT, timeout=10) as _r:
            return json.loads(_r.read().decode()), RAW_SNAPSHOT

    snap, snap_source = _load()
    informed = snap.get("informed") or []
    explorer = snap.get("explorer") if isinstance(snap.get("explorer"), dict) and not snap["explorer"].get("error") else None
    _cmp = (explorer or {}).get("compare")
    compare = _cmp if isinstance(_cmp, dict) and not _cmp.get("error") and _cmp.get("a") and _cmp.get("b") else None
    refused = snap.get("refused") or [{"stage": "gate", **_r} for _r in (snap.get("rejected") or [])]
    return compare, explorer, informed, refused, snap


@app.cell
def _(datetime, icon, mo, re):
    RETRO_LABEL = "validation rétrospective, participants non identifiés"

    def em(m):
        return None if not m else m.get("exact_match")

    def pct(x, digits=1):
        return "n/a" if x is None else f"{x * 100:.{digits}f}%"

    def pts(x):
        return "n/a" if x is None else f"{x * 100:+.1f} pts"

    def basis(v):
        """Two scores are compared only when measured on the same data with the same scorers."""
        return ((v.get("data") or {}).get("sha"), (v.get("train") or {}).get("scorers_sha256"))

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

    def plain_change(patch):
        names = {"CONTACT_THRESHOLD": "touch limit", "UNKNOWN_THRESHOLD": "confidence needed"}
        m = re.search(r"([A-Z_]{4,}) from ([0-9.]+) to ([0-9.]+)", (patch or "").replace("`", ""))
        if m:
            return f"{names.get(m.group(1), m.group(1).lower())} {m.group(2)} → {m.group(3)}"
        text = " ".join((patch or "").split())
        return text if len(text) <= 70 else text[:69] + "…"

    def plain_reason(why):
        why = why or ""
        if why.startswith("no improvement"):
            return "no better than the rule in use"
        if why.startswith("exact_match fell"):
            return "fewer gestures read right"
        m = re.search(r"class (\S+) fell ([0-9.]+) -> ([0-9.]+)", why)
        if m:
            return f"breaks {plain_class(m.group(1))} ({float(m.group(2)) * 100:.0f}% → {float(m.group(3)) * 100:.0f}%)"
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

    def hint(text):
        return mo.Html(f'<div class="tf tf-hint">{icon("pointer", 16)}<span>{text}</span></div>')

    return (RETRO_LABEL, basis, em, hint, names_for, pct, pill, plain_change, plain_class, plain_reason, pts, said,
            same_pose, section, when)


@app.cell
def _(RETRO_LABEL, basis, compare, em, informed, mo, names_for, pill, pts, refused, snap, when):
    _scored = [v for v in informed if em(v.get("train")) is not None]
    _last = _scored[-1] if _scored else None
    if compare:
        _a, _b = compare["a"], compare["b"]
    else:
        _first = next((v for v in _scored if basis(v) == basis(_last)), None) if _last else None
        _a, _b = (_first or {}).get("train") or {}, (_last or {}).get("train") or {}
    _names = names_for(informed)

    def _move(key, digits):
        x, y = _a.get(key), _b.get(key)
        if x is None or y is None:
            return '<span class="tf-from">n/a</span>'
        return f'<span class="tf-from">{x * 100:.{digits}f}%</span><span class="tf-arrow">→</span>{y * 100:.{digits}f}%'

    def _kpi(label, value, foot, badge=""):
        return (f'<div class="tf-kpi"><div class="tf-kpi-top"><span class="tf-kpi-label">{label}</span>{badge}</div>'
                f'<div class="tf-kpi-value">{value}</div><div class="tf-kpi-foot">{foot}</div></div>')

    _gain = (_b["exact_match"] - _a["exact_match"]) if _a.get("exact_match") is not None and _b.get("exact_match") is not None else None
    _paired = (snap.get("retrospective_validation") or {}).get("paired") or {}
    _ci = _paired.get("ci95") or [None, None]
    _kept = sum(1 for v in informed if v.get("kind") == "patch")
    _spend = sum(v.get("spent_usd") or 0 for v in informed if v.get("kind") == "patch")
    _kpis = "".join([
        _kpi("Almost touching, read right", _move("near_contact_accuracy", 0), "practice gestures"),
        _kpi("All gestures, read right", _move("exact_match", 1), "practice gestures",
             pill(pts(_gain), "good") if _gain and _gain > 0 else ""),
        _kpi("Retrospective check", pts(_paired.get("mean_difference")) if _paired else '<span class="tf-from">not run</span>',
             f"range {pts(_ci[0])} to {pts(_ci[1])} · {RETRO_LABEL}" if _paired else RETRO_LABEL,
             pill("not yet proven", "warn") if _paired and (_ci[0] is None or _ci[0] <= 0) else (pill("above zero", "good") if _paired else "")),
        _kpi("AI fixes",
             f'{_kept}<span class="tf-unit">kept</span> <span class="tf-from">·</span> {len(refused)}<span class="tf-unit">refused</span>',
             f"${_spend:.2f} of agent time" if _spend else ""),
    ])
    _best = next((v for v in reversed(informed) if v.get("kind") != "data_refresh"), None)
    _status = pill(f"Rule in use: {_names.get(_best['tag'], _best['tag'])} · {when(_best.get('ts'))}", "dark", dot=True) if _best else ""
    mo.Html(
        f'<div class="tf"><div class="tf-topbar"><div class="tf-brand"><span class="tf-mark">10</span><span>Tenfold</span></div>'
        f'<div class="tf-stack">{pill("Traced in W&amp;B Weave")}{pill("Agents on W&amp;B Inference · CoreWeave")}'
        f'{pill("Built with marimo")}{_status}</div></div>'
        '<div class="tf-hero"><div class="tf-eyebrow">A finger multiplication tutor for kids</div>'
        '<h1 class="tf-h1">An AI loop that teaches a tutor to <em>see</em> children\'s hands.</h1></div>'
        f'<div class="tf-kpis">{_kpis}</div></div>'
    )
    return


@app.cell
def _(icon, informed, mo, pill, refused, section):
    _guard = sum(1 for r in refused if r.get("stage") == "guard")
    _gate = sum(1 for r in refused if r.get("stage") != "guard")
    _kept = sum(1 for v in informed if v.get("kind") == "patch")
    _stages = [
        ("camera", "", "New gestures", "Hand points, no images", "camera", ""),
        ("search", " tf-ai", "Diagnosis agent", "Finds the top mistake", "W&B Inference · Qwen3", ""),
        ("code", " tf-ai", "Patch agent", "Edits the rule", "Claude Code", ""),
        ("shield", " tf-ai", "Guard agent", "Checks the loop's rules", "W&B Inference", pill(f"{_guard} refused", "bad") if _guard else ""),
        ("scale", " tf-judge", "Referee", "No gesture may drop 5 pts", "metric gate", pill(f"{_gate} refused", "bad") if _gate else ""),
        ("commit", " tf-judge", "Kept", "Commit and evaluation", "W&B Weave", pill(f"{_kept} kept", "good")),
    ]
    _cards = "".join(
        f'<div class="tf-stage{_kind}"><div class="tf-stage-head"><span class="tf-stage-icon">{icon(_icon, 18)}</span>'
        f'<span class="tf-stage-step">{_k + 1:02d}</span></div><div class="tf-stage-name">{_name}</div>'
        f'<div class="tf-stage-text">{_text}</div><div class="tf-stage-foot"><span class="tf-stage-tech">{_tech}</span>{_count}</div></div>'
        for _k, (_icon, _kind, _name, _text, _tech, _count) in enumerate(_stages))
    mo.vstack([
        section("loop", 1, "How the loop works", "Agents propose. <em>Two judges</em> can say no."),
        mo.Html(f'<div class="tf"><div class="tf-loop">{_cards}</div><div class="tf-loopback">{icon("loop", 14)}'
                'Repeats when new gestures arrive</div></div>'),
    ])
    return


@app.cell
def _(explorer, mo, plain_class, section):
    example_options = {plain_class(_x["class"]).capitalize(): _i for _i, _x in enumerate((explorer or {}).get("examples", []))}
    example_pick = mo.ui.radio(options=example_options, value=next(iter(example_options), None), inline=True) if example_options else None
    mo.vstack([
        section("see", 2, "See what the tutor sees", "Almost touching is the <em>hard case</em>.",
                "Fingertips closer than the touch limit count as touching."),
        example_pick if example_pick is not None else mo.callout(mo.md("No hand data in this snapshot yet."), kind="neutral"),
    ], gap=1)
    return (example_pick,)


@app.cell
def _(explorer, mo):
    _in_use = ((explorer or {}).get("constants") or {}).get("CONTACT_THRESHOLD", 0.35)
    get_limit, set_limit = mo.state(_in_use)
    return get_limit, set_limit


@app.cell
def _(compare, explorer, get_limit, mo, set_limit):
    _in_use = ((explorer or {}).get("constants") or {}).get("CONTACT_THRESHOLD", 0.35)
    _before = (((compare or {}).get("a") or {}).get("constants") or {}).get("CONTACT_THRESHOLD", 0.35)
    limit_slider = mo.ui.slider(start=0.10, stop=0.50, step=0.0125, value=get_limit(), on_change=set_limit,
                                label="Touch limit", show_value=False, full_width=True)
    old_rule_button = mo.ui.button(label=f"Before the AI · {_before:g}", on_click=lambda _: set_limit(_before))
    ai_rule_button = mo.ui.button(label=f"Found by the AI · {_in_use:g}", kind="success", on_click=lambda _: set_limit(_in_use))
    return ai_rule_button, limit_slider, old_rule_button


@app.cell
def _(ACCENT, BAD, GOOD, HAND_BONES, INK2, LEFT_HAND, RIGHT_HAND, ai_rule_button, alt, example_pick, explorer, get_limit,
      hint, icon, limit_slider, mo, old_rule_button, pd, said, same_pose, style):
    if example_pick is None or not explorer:
        _view = mo.md("")
    else:
        _ex = explorer["examples"][example_pick.value]
        _limit = get_limit()
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
        _answer = min(_grid, key=lambda a: abs(a["threshold"] - _limit)) if _grid else _ex["running"]
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

        _zones = pd.DataFrame([{"from": 0, "to": _limit, "zone": "touching"}, {"from": _limit, "to": 0.6, "zone": "not touching"}])
        _ruler = alt.layer(
            alt.Chart(_zones).mark_bar(height=30, cornerRadius=6).encode(
                x=alt.X("from:Q", scale=alt.Scale(domain=[0, 0.6]), title=None, axis=None),
                x2="to:Q", color=alt.Color("zone:N", scale=alt.Scale(domain=["touching", "not touching"], range=["#E0E7FF", "#F5F5F4"]), legend=None)),
            alt.Chart(_zones.iloc[[0]]).mark_text(align="left", dx=10, fontSize=12, color=INK2).encode(x="from:Q", text="zone:N"),
            alt.Chart(_zones.iloc[[1]]).mark_text(align="right", dx=-10, fontSize=12, color=INK2).encode(x="to:Q", text="zone:N"),
            alt.Chart(pd.DataFrame([{"x": _limit}])).mark_rule(color=ACCENT, strokeWidth=2).encode(x="x:Q"),
            alt.Chart(pd.DataFrame([{"x": _gap}])).mark_tick(thickness=4, size=44, color=_color).encode(x="x:Q"),
            alt.Chart(pd.DataFrame([{"x": _gap, "t": "this gap"}])).mark_text(dy=-28, fontSize=12, fontWeight=600, color=_color).encode(x="x:Q", text="t:N"),
        ).properties(width="container", height=70)

        _verdict = mo.Html(
            f'<div class="tf tf-verdict {"tf-v-good" if _right else "tf-v-bad"}"><div class="tf-verdict-head">'
            f'<span class="tf-verdict-icon">{icon("check" if _right else "x", 17, "#fff", 2.6)}</span>'
            f'{"Read right" if _right else "Read wrong"}</div>'
            f'<dl class="tf-rows"><dt>Tutor says</dt><dd>{said(_answer)}</dd><dt>Truth</dt><dd>{said(_ex["label"])}</dd></dl></div>')
        _keys = mo.Html(
            f'<div class="tf tf-keys"><span class="tf-key"><i class="tf-swatch" style="background:{LEFT_HAND}"></i>left hand</span>'
            f'<span class="tf-key"><i class="tf-swatch" style="background:{RIGHT_HAND}"></i>right hand</span>'
            '<span class="tf-key"><i class="tf-dash"></i>closest fingertips</span></div>')
        _view = mo.vstack([
            hint("Pick “Almost touching 6×10”, then press <b>Before the AI</b>."),
            mo.hstack([
                mo.vstack([mo.ui.altair_chart(style(_hands), chart_selection=False, legend_selection=False), _keys], gap=0.5),
                mo.vstack([_verdict, mo.hstack([old_rule_button, ai_rule_button], justify="start", wrap=True, gap=0.5),
                           limit_slider, mo.ui.altair_chart(style(_ruler), chart_selection=False, legend_selection=False)], gap=1),
            ], wrap=True, gap=2, align="start"),
        ], gap=1)
    _view
    return


@app.cell
def _(ACCENT, INK, INK2, alt, basis, em, informed, mo, names_for, pd, refused, section, style):
    version_names = names_for(informed)
    _rows, _segment, _previous = [], 0, None
    for _v in informed:
        if _previous is not None and basis(_v) != basis(_previous):
            _segment += 1
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
    _pick = alt.selection_point(name="version", fields=["tag"], on="click", empty=False)
    _base = alt.Chart(versions_df)
    _refresh = _base.transform_filter(alt.datum.kind == "data_refresh")
    _layers = [
        _refresh.mark_rule(strokeDash=[3, 3], color="#A8A29E").encode(x=_x),
        _refresh.mark_text(align="left", dx=8, y=6, fontSize=11, color=INK2).encode(x=_x, text=alt.value("new data")),
        _base.mark_line(interpolate="step-after", strokeWidth=2.5, color=ACCENT).encode(x=_x, y=_y, detail="segment:N"),
        _base.mark_circle(opacity=1, stroke="white", strokeWidth=2.5).encode(
            x=_x, y=_y, tooltip=[alt.Tooltip("name:N", title="version"), alt.Tooltip("right:Q", format=".1%", title="read right")],
            color=alt.Color("kind:N", scale=alt.Scale(domain=["baseline", "patch", "data_refresh"], range=["#A8A29E", ACCENT, "#78716C"]), legend=None),
            size=alt.condition(_pick, alt.value(520), alt.value(200))).add_params(_pick),
        _base.mark_text(dy=-18, fontSize=12, fontWeight=600, color=INK).encode(x=_x, y=_y, text="label:N"),
    ]
    version_chart = mo.ui.altair_chart(style(alt.layer(*_layers).properties(width="container", height=240)),
                                       chart_selection=False, legend_selection=False)
    _kept = sum(1 for v in informed if v.get("kind") == "patch")
    mo.vstack([
        section("versions", 3, "Every version, measured",
                f"{_kept} fixes kept. {len(refused)} refused." if refused else f"{_kept} fixes kept.",
                "Each dot is a version of the rule. Click one."),
        version_chart,
    ], gap=1)
    return version_chart, version_names, versions_df


@app.cell
def _(informed, mo, version_names):
    version_menu = mo.ui.dropdown(options={version_names[v["tag"]]: v["tag"] for v in reversed(informed)} or {"none": "none"},
                                  value=next((version_names[v["tag"]] for v in reversed(informed) if v.get("kind") == "patch"),
                                             next(iter(version_names.values()), "none")))
    return (version_menu,)


@app.cell
def _(basis, em, html, informed, mo, pill, plain_change, plain_class, plain_reason, pts, re, version_chart, version_menu,
      version_names, versions_df):
    try:
        _clicked = version_chart.apply_selection(versions_df)
        _clicked_tag = _clicked["tag"].iloc[0] if 0 < len(_clicked) < len(versions_df) else None
    except Exception:
        _clicked_tag = None
    _tag = _clicked_tag or version_menu.value
    _i = next((i for i, v in enumerate(informed) if v["tag"] == _tag), len(informed) - 1)
    if not informed:
        _out = mo.md("")
    else:
        _v = informed[_i]
        _prev = informed[_i - 1] if _i > 0 and basis(informed[_i - 1]) == basis(_v) else None
        _now = (_v.get("train") or {}).get("per_class") or {}
        _before = ((_prev or {}).get("train") or {}).get("per_class") or {}
        _moves = sorted(((c, (_now[c] or 0) - (_before.get(c) or 0)) for c in _now if _prev and c in _before
                         and abs((_now[c] or 0) - (_before.get(c) or 0)) > 1e-9), key=lambda cm: -cm[1])
        _shown = [m for m in _moves if m[1] > 0][:3] + [m for m in _moves if m[1] < 0][-2:]
        _moves_html = "".join(
            f'<div class="tf-move"><span>{plain_class(c)}</span><span class="{"tf-up" if d > 0 else "tf-down"}">{d * 100:+.0f} pts</span></div>'
            for c, d in _shown) or '<div class="tf-card-text">none by much</div>'
        _name = version_names.get(_v["tag"], _v["tag"])
        if _v.get("kind") == "patch":
            _changed = [l for l in (_v.get("diff") or "").splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---")) and l[1:].strip()]
            _code = [l for l in _changed if re.match(r"\s*(?:[A-Za-z_][A-Za-z0-9_.]*\s*=|def |return |if |elif |else\b|for |while )", l[1:])]
            _lines = sorted((_code or _changed)[:4], key=lambda l: l[0] == "+")
            _diff = "".join(f'<div class="tf-diff-row {"tf-add" if l[0] == "+" else "tf-del"}"><span class="tf-diff-sign">{l[0]}</span>'
                            f'<span>{html.escape(l[1:].split("#")[0].strip()[:90])}</span></div>' for l in _lines)
            _delta = (em(_v.get("train")) - em(_prev.get("train"))) if _prev and em(_v.get("train")) is not None and em(_prev.get("train")) is not None else None
            _cards = (
                f'<div class="tf-card"><span class="tf-label">The AI changed</span><div class="tf-card-title">{html.escape(plain_change(_v.get("patch")))}</div>'
                f'{f"<div class=tf-diff>{_diff}</div>" if _diff else ""}</div>'
                f'<div class="tf-card"><span class="tf-label">Referee kept it</span><div class="tf-card-title">{html.escape(plain_reason(_v.get("gate")))}</div>'
                f'<div>{pill(pts(_delta), "good") if _delta is not None and _delta > 0 else ""}</div></div>'
                f'<div class="tf-card"><span class="tf-label">Gestures that moved</span><div class="tf-moves">{_moves_html}</div></div>')
        elif _v.get("kind") == "data_refresh":
            _cards = ('<div class="tf-card"><span class="tf-label">New data</span><div class="tf-card-title">Same rule, scored on the 80% practice set</div></div>')
        else:
            _cards = '<div class="tf-card"><span class="tf-label">Start</span><div class="tf-card-title">Written by hand, before any AI</div></div>'
        _details = {}
        if _v.get("diagnosis") or _v.get("patch_hypothesis"):
            _details["The agents' own words"] = mo.md("\n\n".join(
                f"**{label}** {_v[key]}" for key, label in (("diagnosis", "Diagnosis:"), ("patch_hypothesis", "Measured:"),
                                                            ("expected", "Expected:")) if _v.get(key)))
        if _v.get("diff"):
            _details["Full code change"] = mo.md(f"```diff\n{_v['diff']}\n```")
        _out = mo.vstack([
            mo.hstack([mo.Html(f'<div class="tf tf-card-title" style="font-size:20px">{_name}</div>'), version_menu],
                      justify="space-between", align="center"),
            mo.Html(f'<div class="tf tf-grid3">{_cards}</div>'),
            mo.accordion(_details) if _details else mo.md(""),
        ], gap=1)
    _out
    return


@app.cell
def _(basis, em, html, informed, mo, pill, plain_change, plain_reason, pts, refused, version_names, when):
    _events = []
    _previous = None
    for _v in informed:
        _kind = _v.get("kind") or "patch"
        if _kind == "patch":
            _delta = (em(_v.get("train")) - em(_previous.get("train"))
                      if _previous and basis(_previous) == basis(_v) and em(_v.get("train")) is not None and em(_previous.get("train")) is not None else None)
            _events.append((str(_v.get("ts") or ""), 1, pill("Kept", "good"),
                            f"{version_names.get(_v['tag'], _v['tag'])}: {html.escape(plain_change(_v.get('patch')))}", pts(_delta) if _delta is not None else ""))
        elif _kind == "data_refresh":
            _events.append((str(_v.get("ts") or ""), 1, pill("Data", "accent"), "New data: 80% practice set", ""))
        else:
            _events.append(("", 0, pill("Start"), "Hand-written rule", ""))
        _previous = _v
    for _r in refused:
        _text = "Guard agent: breaks a loop rule" if _r.get("stage") == "guard" else "Referee: " + plain_reason(_r.get("reason"))
        _events.append((str(_r.get("ts") or ""), 1, pill("Refused", "bad"),
                        f'<span title="{html.escape(str(_r.get("reason") or ""))}">{html.escape(_text)}</span>', ""))
    _events.sort(key=lambda e: (e[1], e[0]))
    _rows = "".join(f'<div class="tf-log-row"><span class="tf-log-time">{when(ts) if ts else ""}</span><span>{tag}</span>'
                    f'<span class="tf-log-text">{text}</span><span class="tf-log-right">{right}</span></div>'
                    for ts, _o, tag, text, right in _events)
    mo.Html(f'<div class="tf" style="margin-top:12px"><div class="tf-log">{_rows}</div></div>') if _events else mo.md("")
    return


@app.cell
def _(explorer, mo, section):
    _sweep = (explorer or {}).get("sweep") or {}
    sweep_name = "CONTACT_THRESHOLD" if "CONTACT_THRESHOLD" in _sweep else next(iter(_sweep), None)
    section("referee", 4, "Try it yourself", "Be the <em>referee</em>.",
            "Drag the slider: would you keep this touch limit?" if sweep_name else "No sweep in this snapshot yet.")
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
def _(ACCENT, BAD, GOOD, INK, INK2, alt, explorer, icon, mo, pct, pd, plain_reason, pts, style, sweep_name, sweep_slider):
    if sweep_slider is None:
        _view = mo.md("")
    else:
        _s = explorer["sweep"][sweep_name]
        _rows = _s["rows"]
        _row = _rows[min(sweep_slider.value, len(_rows) - 1)]
        _in_use = min(_rows, key=lambda r: abs(r["value"] - _s["current"]))
        _any_kept = any(r["gate"] == "PASS" for r in _rows)
        _df = pd.DataFrame([{"limit": r["value"], "right": r["exact_match"], "referee": "keeps" if r["gate"] == "PASS" else "refuses"} for r in _rows])
        _lo, _hi = float(_df["right"].min()), float(_df["right"].max())
        _pad = max(0.01, (_hi - _lo) * 0.2)
        _dom = [max(0, _lo - _pad), min(1, _hi + _pad)]
        _x = alt.X("limit:Q", title="touch limit", scale=alt.Scale(zero=False), axis=alt.Axis(tickCount=6, labelOverlap=True))
        _y = alt.Y("right:Q", title=None, scale=alt.Scale(domain=_dom), axis=alt.Axis(format=".0%", tickCount=4))
        _chart = alt.layer(
            alt.Chart(_df).mark_line(color="#D6D3D1", strokeWidth=1.5).encode(x=_x, y=_y),
            alt.Chart(_df).mark_point(size=70, filled=True, opacity=1).encode(
                x=_x, y=_y, color=alt.Color("referee:N", scale=alt.Scale(domain=["keeps", "refuses"], range=[GOOD, "#D6D3D1"]), legend=None),
                tooltip=[alt.Tooltip("limit:Q", title="touch limit"), alt.Tooltip("right:Q", format=".1%", title="read right"), alt.Tooltip("referee:N")]),
            alt.Chart(pd.DataFrame([{"limit": _s["current"]}])).mark_rule(strokeDash=[4, 4], color=INK2).encode(x="limit:Q"),
            alt.Chart(pd.DataFrame([{"limit": _s["current"], "right": _dom[1], "t": "in use"}])).mark_text(
                align="left", dx=6, dy=8, color=INK2, fontSize=11).encode(x="limit:Q", y=alt.Y("right:Q", scale=alt.Scale(domain=_dom)), text="t:N"),
            alt.Chart(pd.DataFrame([{"limit": _row["value"], "right": _row["exact_match"]}])).mark_circle(
                size=380, color=ACCENT, stroke="white", strokeWidth=3, opacity=1).encode(x=_x, y=_y),
        ).properties(width="container", height=240)
        _kept = _row["gate"] == "PASS"
        _is_current = abs(_row["value"] - _s["current"]) < 1e-9
        _delta = _row["exact_match"] - _in_use["exact_match"]
        _head = "Kept" if _kept else ("The rule in use" if _is_current else "Refused")
        _note = ("" if _any_kept else "No tested limit beats it.") if _is_current else plain_reason(_row["why"])
        _verdict = mo.Html(
            f'<div class="tf tf-verdict {"tf-v-good" if _kept else ""}" style="display:flex;flex-wrap:wrap;gap:12px 32px;align-items:center">'
            f'<div class="tf-verdict-head" style="color:{GOOD if _kept else (INK if _is_current else BAD)}">'
            f'<span class="tf-verdict-icon" style="background:{GOOD if _kept else ("#A8A29E" if _is_current else BAD)}">'
            f'{icon("check" if _kept else ("minus" if _is_current else "x"), 17, "#fff", 2.6)}</span>{_head}</div>'
            f'<dl class="tf-rows" style="margin:0"><dt>Touch limit</dt><dd class="tf-mono">{_row["value"]:g}</dd>'
            f'<dt>Read right</dt><dd class="tf-mono">{pct(_row["exact_match"])} <span class="tf-muted">{"" if _is_current else pts(_delta)}</span></dd></dl>'
            f'<div class="tf-note">{_note}</div></div>')
        _view = mo.vstack([sweep_slider, _verdict, mo.ui.altair_chart(style(_chart), chart_selection=False, legend_selection=False)], gap=1)
    _view
    return


@app.cell
def _(ACCENT, INK, MUTED, RETRO_LABEL, compare, icon, informed, math, mo, names_for, pct, pill, pts, section, snap):
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
        w, left, right, row_h, top = 440, 70, 72, 40, 34
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
                     f'<text x="0" y="{top - 20}" font-size="11" fill="{MUTED}">likely gain</text>'
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
                f'<text x="{w - right + 8:.1f}" y="{y + 4:.1f}" font-size="13" fill="{INK}">{est:+.1f} pts</text>')
        return (f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Likely range of the gain over the hand-written rule, '
                f'per version, on the {RETRO_LABEL}">{"".join(parts)}</svg>')

    _paired = _retro.get("paired") or {}
    _ci = _paired.get("ci95") or [None, None]
    _proven = _ci[0] is not None and _ci[0] > 0
    _a, _b = (compare or {}).get("a") or {}, (compare or {}).get("b") or {}

    def _rung(state, title, text, value="", extra=""):
        mark = icon("check", 14, "#fff", 2.8) if state == "done" else ""
        return (f'<div class="tf-rung tf-r-{state}"><span class="tf-rung-mark">{mark}</span><div><div class="tf-rung-title">{title}</div>'
                f'<div class="tf-rung-text">{text}</div>{extra}</div><div class="tf-rung-value">{value}</div></div>')

    _practice_gain = (_b["exact_match"] - _a["exact_match"]) if _a.get("exact_match") is not None and _b.get("exact_match") is not None else None
    _rungs = [
        _rung("done", f"Practice gestures {pill('done', 'good')}",
              (f"All {pct(_a.get('exact_match'))} → {pct(_b.get('exact_match'))} · almost touching "
               f"{pct(_a.get('near_contact_accuracy'), 0)} → {pct(_b.get('near_contact_accuracy'), 0)}") if compare else "",
              pts(_practice_gain) if _practice_gain is not None else ""),
        _rung("partial" if _retro else "next",
              f"Retrospective check {pill('not yet proven', 'warn') if _retro and not _proven else (pill('above zero', 'good') if _retro else pill('not run'))}",
              (f'<span class="tf-tag">{_retro.get("label", RETRO_LABEL)}</span> 20% held back · {_retro.get("holds")} holds'
               if _retro else f'<span class="tf-tag">{RETRO_LABEL}</span>'),
              pts(_paired.get("mean_difference")) if _paired else "",
              f'<div class="tf-ci">{_ci_svg(_versions)}</div>' if _versions else ""),
        _rung("next", f"New people {pill('next')}", "People the loop has never seen"),
        _rung("later", f"Kids using the tutor {pill('later')}", "Do they learn faster?"),
    ]
    mo.vstack([
        section("evidence", 5, "How far the evidence goes", "Where we stand, <em>honestly</em>."),
        mo.Html(f'<div class="tf"><div class="tf-ladder">{"".join(_rungs)}</div></div>'),
    ], gap=1)
    return


@app.cell
def _(REPO_URL, WEAVE_URL, explorer, icon, informed, mo, snap):
    _train = next((v.get("train") for v in reversed(informed) if v.get("train")), None) or {}
    _retro = snap.get("retrospective_validation") or {}
    _items = [
        ("rule", (snap.get("best_version") or "")[:8]),
        ("practice data", (explorer or {}).get("data_sha", "")),
        ("scorers", (_train.get("scorers_sha256") or "")[:8]),
        ("validation", (_retro.get("validation_sha256") or "")[:8]),
        ("snapshot", (snap.get("generated_at") or "")[:16].replace("T", " ")),
    ]
    _prov = "".join(f"<span>{k} <b>{v}</b></span>" for k, v in _items if v)
    mo.Html(
        f'<div class="tf"><div class="tf-prov">{_prov}<a href="{WEAVE_URL}" target="_blank" rel="noopener">Weave traces {icon("link", 12)}</a>'
        f'<a href="{REPO_URL}" target="_blank" rel="noopener">Code {icon("link", 12)}</a></div>'
        '<div class="tf-closing"><p>The child learns multiplication.<br><em>The tutor learns to see the child.</em></p></div></div>'
    )
    return


if __name__ == "__main__":
    app.run()
