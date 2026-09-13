"""Tenfold, explained for anyone: how a finger multiplication tutor reads hands, and how AI agents make it read them
better. A marimo app over one file, data/snapshot.json.

    make dashboard                               # marimo run dashboard/loop_dashboard.py
    marimo edit dashboard/loop_dashboard.py      # to change it
    TENFOLD_SNAPSHOT=/path/snapshot.json marimo run dashboard/loop_dashboard.py

No W&B key and no repo needed: without a local snapshot it reads the one committed on GitHub, so it runs on molab
and as WebAssembly. The loop rewrites and pushes the snapshot after every accepted version (loop/watch.py).

Written for an 18 year old who is not an engineer: a four step picture, three numbers, then four questions, each
with one "try it" hint, plain words and the technical detail folded away.
1. How does the tutor know two fingers touch? Real hand landmarks, a touch limit you can move, a right or wrong.
2. Did the AI make the tutor better? Click a version to see what changed, in plain words.
3. Try being the referee: move the touch limit and see whether the automatic test would keep it.
4. Can we trust these numbers? Practice gestures, the validation rétrospective, new people (not tested yet).
Gains are only compared on the same data with the same scorers. The validation rétrospective keeps its name: the AI
had already seen those gestures and participants are not identified. Hands come from the practice side only.
"""
import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium", app_title="Tenfold, explained")


@app.cell
def _():
    import json
    import os
    import re
    import urllib.request
    from pathlib import Path

    import altair as alt
    import marimo as mo
    import pandas as pd

    return Path, alt, json, mo, os, pd, re, urllib


@app.cell
def _():
    GOOD, BAD, WARN, INK, MUTED, GRID = "#16a34a", "#dc2626", "#d97706", "#0f172a", "#64748b", "#e2e8f0"
    LEFT_HAND, RIGHT_HAND = "#2563eb", "#9333ea"
    FONT = "Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    HAND_BONES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
                  (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]

    def style(chart):
        return (chart.configure(font=FONT)
                .configure_view(strokeWidth=0)
                .configure_axis(labelColor=MUTED, titleColor=MUTED, gridColor=GRID, domainColor=GRID, tickColor=GRID,
                                labelFontSize=13, titleFontSize=13, titleFontWeight="normal", labelPadding=6))

    return BAD, GOOD, GRID, HAND_BONES, INK, LEFT_HAND, MUTED, RIGHT_HAND, WARN, style


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
    return explorer, informed, snap


@app.cell
def _(mo, re):
    def em(m):
        return None if not m else m.get("exact_match")

    def pct(x):
        return "n/a" if x is None else f"{x * 100:.1f}%"

    def basis(v):
        """Two scores are compared only when measured on the same data with the same scorers."""
        return ((v.get("data") or {}).get("sha"), (v.get("train") or {}).get("scorers_sha256"))

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
        names = {"CONTACT_THRESHOLD": "touch limit", "UNKNOWN_THRESHOLD": "confidence needed to answer"}
        m = re.search(r"([A-Z_]{4,}) from ([0-9.]+) to ([0-9.]+)", (patch or "").replace("`", ""))
        if m:
            return f"{names.get(m.group(1), m.group(1).lower())}: {m.group(2)} → {m.group(3)}"
        text = " ".join((patch or "").split())
        return text if len(text) <= 90 else text[:89] + "…"

    def plain_reason(why):
        why = why or ""
        if why.startswith("no improvement"):
            return "no more gestures read right than the rule in use"
        if why.startswith("exact_match fell"):
            return "fewer gestures read right"
        m = re.search(r"class (\S+) fell", why)
        if m:
            return f"it breaks another gesture ({plain_class(m.group(1))})"
        if why.startswith("condition"):
            return "it gets worse for one camera position"
        if "false_unknown" in why:
            return "it refuses too many real gestures"
        m = re.search(r"exact_match ([0-9.]+) -> ([0-9.]+)", why)
        if m:
            return f"more gestures read right ({float(m.group(1)) * 100:.1f}% → {float(m.group(2)) * 100:.1f}%)"
        return why

    def names_for(versions):
        out, fixes = {}, 0
        for v in versions:
            if v.get("kind") == "patch" or (v.get("version") or 0) > 0 and not v.get("kind"):
                fixes += 1
                out[v["tag"]] = f"Fix {fixes}"
            elif v.get("kind") == "data_refresh":
                out[v["tag"]] = "More data"
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

    def chapter(anchor, number, question, lede):
        return mo.Html(
            f'<div id="{anchor}" style="margin:56px 0 6px;display:flex;gap:16px;align-items:flex-start">'
            f'<div style="flex:none;width:40px;height:40px;border-radius:12px;background:#0f172a;color:#fff;display:grid;'
            f'place-items:center;font:700 18px system-ui">{number}</div><div><h2 style="margin:0 0 4px;font-size:26px;'
            f'line-height:1.2">{question}</h2><p style="margin:0;color:#64748b;font-size:16px">{lede}</p></div></div>')

    def try_it(text):
        return mo.Html(f'<div style="display:inline-block;margin:6px 0;padding:8px 14px;border-radius:999px;background:#fef3c7;'
                       f'color:#92400e;font-size:15px">👉 <b>Try it:</b> {text}</div>')

    def tile(icon, title, body, tone="#f8fafc", border="#e2e8f0"):
        return mo.Html(f'<div style="min-width:220px;flex:1;padding:14px 16px;border-radius:14px;background:{tone};'
                       f'border:1px solid {border}"><div style="font-size:22px">{icon}</div><div style="font:600 13px system-ui;'
                       f'color:#64748b;text-transform:uppercase;letter-spacing:.06em;margin:4px 0">{title}</div>'
                       f'<div style="font-size:17px;color:#0f172a">{body}</div></div>')

    return basis, chapter, em, names_for, pct, plain_change, plain_class, plain_reason, said, same_pose, tile, try_it


@app.cell
def _(basis, em, informed, mo, pct, snap):
    _steps = [("📷", "A camera", "watches the child's two hands"),
              ("🧮", "A rule", "decides which fingers touch"),
              ("🤖", "AI agents", "find a mistake and edit the rule"),
              ("⚖️", "A referee", "keeps the edit only if more gestures are read right")]
    _cards = "".join(
        (f'<div style="font-size:24px;color:#94a3b8;align-self:center">→</div>' if _i else "")
        + f'<div style="flex:1;min-width:150px;padding:16px;border-radius:16px;background:#fff;border:1px solid #e2e8f0;'
          f'text-align:center"><div style="font-size:34px">{_icon}</div><div style="font:700 16px system-ui;margin-top:6px">'
          f'{_title}</div><div style="color:#64748b;font-size:14px;margin-top:2px">{_text}</div></div>'
        for _i, (_icon, _title, _text) in enumerate(_steps))
    _scored = [v for v in informed if em(v.get("train")) is not None]
    _last = _scored[-1] if _scored else None
    _base = next((v for v in _scored if basis(v)[0] and basis(v) == basis(_last)), None) if _last else None
    _gain = (em(_last["train"]) - em(_base["train"])) if _last and _base is not None and _base is not _last else None
    _fixes = sum(1 for v in informed if v.get("kind") == "patch")
    mo.vstack([
        mo.Html('<div style="padding:24px 0 8px"><div style="font:600 12px system-ui;letter-spacing:.14em;text-transform:uppercase;'
                'color:#64748b">Tenfold · finger multiplication tutor</div><h1 style="margin:8px 0 6px;font-size:40px;'
                'line-height:1.1;letter-spacing:-.02em">A tutor that learns to read children\'s hands better, on its own.</h1></div>'),
        mo.Html(f'<div style="display:flex;flex-wrap:wrap;gap:10px;padding:18px;border-radius:20px;background:#f1f5f9">{_cards}'
                '<div style="flex-basis:100%;text-align:center;color:#64748b;font-size:14px;margin-top:4px">↺ and again, '
                'every time the camera records new gestures</div></div>'),
        mo.hstack([
            mo.stat(pct(em((_last or {}).get("train"))), label="Gestures read right", bordered=True,
                    caption=(f"{_gain * 100:+.1f} pts since the practice set last changed" if _gain is not None else "on the practice gestures"),
                    direction=("increase" if (_gain or 0) > 0 else None)),
            mo.stat(str(_fixes), label="AI fixes kept by the referee", caption=f"{len(snap.get('rejected') or [])} refused in this run", bordered=True),
            mo.stat("not yet", label="Tested on new people", caption="the one test still missing", bordered=True),
        ], justify="start", wrap=True, gap=1),
    ], gap=1)
    return


@app.cell
def _(chapter, explorer, mo, plain_class):
    example_options = {plain_class(_x["class"]).capitalize(): _i for _i, _x in enumerate((explorer or {}).get("examples", []))}
    example_pick = mo.ui.radio(options=example_options, value=next(iter(example_options), None), inline=True) if example_options else None
    mo.vstack([
        chapter("see", 1, "How does the tutor know two fingers touch?",
                "It measures the gap between the two closest fingertips. Under a limit, it says “touching”."),
        mo.md("**Pick a gesture**"),
        example_pick if example_pick is not None else mo.callout(mo.md("No hand data in this snapshot."), kind="warn"),
    ])
    return (example_pick,)


@app.cell
def _(explorer, mo):
    _in_use = ((explorer or {}).get("constants") or {}).get("CONTACT_THRESHOLD", 0.35)
    get_limit, set_limit = mo.state(_in_use)
    return get_limit, set_limit


@app.cell
def _(explorer, get_limit, mo, set_limit):
    _in_use = ((explorer or {}).get("constants") or {}).get("CONTACT_THRESHOLD", 0.35)
    limit_slider = mo.ui.slider(start=0.10, stop=0.50, step=0.0125, value=get_limit(), on_change=set_limit,
                                label="Touch limit", show_value=True, full_width=True)
    old_rule_button = mo.ui.button(label="Old rule, before the AI", on_click=lambda _: set_limit(0.35))
    ai_rule_button = mo.ui.button(label="Rule found by the AI", kind="success", on_click=lambda _: set_limit(_in_use))
    return ai_rule_button, limit_slider, old_rule_button


@app.cell
def _(BAD, GOOD, HAND_BONES, LEFT_HAND, RIGHT_HAND, ai_rule_button, alt, example_pick, explorer, get_limit, limit_slider,
      mo, old_rule_button, pd, said, same_pose, style, try_it):
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
            alt.Chart(pd.DataFrame(_bones)).mark_rule(strokeWidth=4, strokeCap="round").encode(
                x=alt.X("x:Q", scale=_sx, axis=None), y=alt.Y("y:Q", scale=_sy, axis=None), x2="x2:Q", y2="y2:Q", color=_hand_color),
            alt.Chart(_pair).mark_rule(strokeWidth=5, strokeDash=[3, 3], color=_color).encode(
                x=alt.X("x:Q", scale=_sx), y=alt.Y("y:Q", scale=_sy), x2="x2:Q", y2="y2:Q"),
            alt.Chart(pd.DataFrame(_tips)).mark_circle(size=300, opacity=1, stroke="white", strokeWidth=2).encode(
                x=alt.X("x:Q", scale=_sx), y=alt.Y("y:Q", scale=_sy), color=_hand_color),
            alt.Chart(pd.DataFrame(_tips)).mark_text(fontSize=11, fontWeight="bold", color="white").encode(
                x=alt.X("x:Q", scale=_sx), y=alt.Y("y:Q", scale=_sy), text="finger:N"),
        ).properties(width=340, height=340)

        _zones = pd.DataFrame([{"from": 0, "to": _limit, "zone": "counts as touching"}, {"from": _limit, "to": 0.6, "zone": "not touching"}])
        _ruler = alt.layer(
            alt.Chart(_zones).mark_bar(height=34, cornerRadius=6).encode(
                x=alt.X("from:Q", scale=alt.Scale(domain=[0, 0.6]), title="gap between the two closest fingertips", axis=alt.Axis(labels=False, ticks=False)),
                x2="to:Q", color=alt.Color("zone:N", scale=alt.Scale(domain=["counts as touching", "not touching"], range=["#fde68a", "#e2e8f0"]), legend=None)),
            alt.Chart(_zones).mark_text(align="left", dx=8, fontSize=13, color="#334155").encode(x="from:Q", text="zone:N"),
            alt.Chart(pd.DataFrame([{"x": _gap, "t": "this gap"}])).mark_tick(thickness=5, size=50, color=_color).encode(x="x:Q"),
            alt.Chart(pd.DataFrame([{"x": _gap, "t": "this gap"}])).mark_text(dy=-32, fontSize=13, fontWeight="bold", color=_color).encode(x="x:Q", text="t:N"),
        ).properties(width="container", height=90)

        _verdict = mo.Html(
            f'<div style="padding:18px 20px;border-radius:16px;background:{"#f0fdf4" if _right else "#fef2f2"};'
            f'border:2px solid {_color}"><div style="font-size:34px;font-weight:800;color:{_color}">'
            f'{"✅ Right" if _right else "❌ Wrong"}</div><div style="font-size:18px;margin-top:6px">The tutor says '
            f'<b>{said(_answer)}</b></div><div style="font-size:16px;color:#475569">The truth is <b>{said(_ex["label"])}</b></div></div>')
        _view = mo.vstack([
            try_it("pick “Almost touching 6×10”, then press <b>Old rule</b>. The tutor gets it wrong."),
            mo.hstack([
                mo.vstack([mo.ui.altair_chart(style(_hands), chart_selection=False, legend_selection=False),
                           mo.md("<span style='color:#64748b;font-size:13px'>Blue: left hand · purple: right hand · 6 = thumb … 10 = little finger</span>")]),
                mo.vstack([_verdict, mo.hstack([old_rule_button, ai_rule_button], justify="start", wrap=True, gap=1), limit_slider,
                           mo.ui.altair_chart(style(_ruler), chart_selection=False, legend_selection=False)], gap=1),
            ], wrap=True, gap=2, align="start"),
        ], gap=1)
    _view
    return


@app.cell
def _(GOOD, WARN, alt, basis, chapter, em, informed, mo, names_for, pd, try_it, style):
    version_names = names_for(informed)
    _rows, _segment, _previous = [], 0, None
    for _v in informed:
        if _previous is not None and basis(_v) != basis(_previous):
            _segment += 1
        _previous = _v
        _rows.append({"tag": _v["tag"], "name": version_names[_v["tag"]], "kind": _v.get("kind") or "patch",
                      "right": em(_v.get("train")), "segment": _segment, "label": f"{(em(_v.get('train')) or 0) * 100:.1f}%"})
    for _k, _r in enumerate(_rows):
        _r["show"] = _k == len(_rows) - 1 or _rows[_k + 1]["segment"] != _r["segment"]
    versions_df = pd.DataFrame(_rows)
    _names = list(versions_df["name"]) if len(versions_df) else []
    _values = [r["right"] for r in _rows if r["right"] is not None]
    _lo = max(0.0, (min(_values) if _values else 0.4) - 0.08)
    _hi = min(1.0, (max(_values) if _values else 0.6) + 0.06)
    _x = alt.X("name:N", sort=_names, title=None, axis=alt.Axis(labelAngle=0 if len(_names) <= 4 else -30, labelFontSize=14, labelColor="#0f172a"))
    _y = alt.Y("right:Q", title="gestures read right", scale=alt.Scale(domain=[_lo, _hi]), axis=alt.Axis(format=".0%", tickCount=4))
    _pick = alt.selection_point(name="version", fields=["tag"], on="click", empty=False)
    _base = alt.Chart(versions_df)
    _layers = [
        _base.mark_line(strokeWidth=3, color="#94a3b8").encode(x=_x, y=_y, detail="segment:N"),
        _base.mark_circle(size=260, opacity=1, stroke="white", strokeWidth=3).encode(
            x=_x, y=_y, tooltip=[alt.Tooltip("name:N", title="version"), alt.Tooltip("right:Q", format=".1%", title="read right")],
            color=alt.Color("kind:N", scale=alt.Scale(domain=["baseline", "patch", "data_refresh"], range=["#94a3b8", GOOD, WARN]), legend=None),
            size=alt.condition(_pick, alt.value(620), alt.value(260))).add_params(_pick),
        _base.transform_filter(alt.datum.show).mark_text(dy=-24, fontSize=14, fontWeight="bold", color="#0f172a").encode(x=_x, y=_y, text="label:N"),
    ]
    version_chart = mo.ui.altair_chart(style(alt.layer(*_layers).properties(width="container", height=280)),
                                       chart_selection=False, legend_selection=False)
    mo.vstack([
        chapter("loop", 2, "Did the AI make the tutor better?",
                "Each dot is a version of the rule. Green dots are fixes made by the AI; orange means the practice gestures changed."),
        try_it("click a green dot to see what the AI changed."),
        version_chart,
    ])
    return version_chart, version_names, versions_df


@app.cell
def _(informed, mo, version_names):
    version_menu = mo.ui.dropdown(options={version_names[v["tag"]]: v["tag"] for v in reversed(informed)} or {"none": "none"},
                                  value=next((version_names[v["tag"]] for v in reversed(informed) if v.get("kind") == "patch"),
                                             next(iter(version_names.values()), "none")), label="or choose")
    return (version_menu,)


@app.cell
def _(basis, informed, mo, plain_change, plain_class, plain_reason, tile, version_chart, version_menu, version_names, versions_df):
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
        _better = ", ".join(f"{plain_class(c)} <b style='color:#16a34a'>+{d * 100:.0f}</b>" for c, d in [m for m in _moves if m[1] > 0][:3])
        _worse = ", ".join(f"{plain_class(c)} <b style='color:#dc2626'>{d * 100:.0f}</b>" for c, d in [m for m in _moves if m[1] < 0][-3:])
        if _v.get("kind") == "patch":
            _tiles = mo.hstack([
                tile("🔧", "What the AI changed", plain_change(_v.get("patch"))),
                tile("⚖️", "Referee's verdict", "kept: " + plain_reason(_v.get("gate")), tone="#f0fdf4", border="#bbf7d0"),
                tile("📈", "Gestures that improved (points)", _better or "none of them by itself"),
            ] + ([tile("📉", "Gestures that got worse", _worse, tone="#fef2f2", border="#fecaca")] if _worse else []),
                justify="start", wrap=True, gap=1)
        elif _v.get("kind") == "data_refresh":
            _tiles = tile("📦", "More data", "Same rule, now measured on the practice gestures only (80% of the recordings).")
        else:
            _tiles = tile("🏁", "Start", "The rule a developer wrote first, before any AI touched it.")
        _details = {}
        if _v.get("diagnosis") or _v.get("patch_hypothesis"):
            _details["For the curious: the agents' own words"] = mo.md("\n\n".join(
                f"**{label}** {_v[key]}" for key, label in (("diagnosis", "Looked at the mistakes:"), ("patch_hypothesis", "Measured:"),
                                                            ("expected", "Expected:")) if _v.get(key)))
        if _v.get("diff"):
            _details["For developers: the code change"] = mo.md(f"```diff\n{_v['diff']}\n```")
        _out = mo.vstack([
            mo.hstack([mo.md(f"### {version_names.get(_v['tag'], _v['tag'])}"), version_menu], justify="space-between", align="center"),
            _tiles, mo.accordion(_details) if _details else mo.md(""),
        ], gap=1)
    _out
    return


@app.cell
def _(chapter, explorer, mo, plain_change, try_it):
    _sweep = (explorer or {}).get("sweep") or {}
    sweep_name = "CONTACT_THRESHOLD" if "CONTACT_THRESHOLD" in _sweep else next(iter(_sweep), None)
    _rows = _sweep.get(sweep_name, {}).get("rows", []) if sweep_name else []
    _kept = [r for r in _rows if r["gate"] == "PASS"]
    _lead = max(_kept, key=lambda r: r["exact_match"]) if _kept else None
    mo.vstack([
        chapter("critic", 3, "Try being the referee",
                "The AI tries many touch limits. The referee keeps one only if more gestures are read right and nothing else breaks."),
        try_it("drag the slider. <b style='color:#16a34a'>Green dots</b> are limits the referee would keep."),
        mo.callout(mo.md(f"💡 **The AI's next move:** a touch limit of **{_lead['value']:g}** would also be kept."), kind="info") if _lead else mo.md(""),
    ] if sweep_name else [chapter("critic", 3, "Try being the referee", "No sweep in this snapshot.")])
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
def _(BAD, GOOD, INK, WARN, alt, explorer, mo, pct, pd, plain_reason, style, sweep_name, sweep_slider):
    if sweep_slider is None:
        _view = mo.md("")
    else:
        _s = explorer["sweep"][sweep_name]
        _rows = _s["rows"]
        _row = _rows[min(sweep_slider.value, len(_rows) - 1)]
        _df = pd.DataFrame([{"limit": r["value"], "right": r["exact_match"], "referee": "keeps" if r["gate"] == "PASS" else "refuses"} for r in _rows])
        _lo, _hi = float(_df["right"].min()), float(_df["right"].max())
        _pad = max(0.01, (_hi - _lo) * 0.2)
        _dom = [max(0, _lo - _pad), min(1, _hi + _pad)]
        _x = alt.X("limit:Q", title="touch limit", scale=alt.Scale(zero=False))
        _y = alt.Y("right:Q", title="gestures read right", scale=alt.Scale(domain=_dom), axis=alt.Axis(format=".0%", tickCount=4))
        _chart = alt.layer(
            alt.Chart(_df).mark_line(color="#cbd5e1", strokeWidth=2).encode(x=_x, y=_y),
            alt.Chart(_df).mark_circle(size=110, opacity=1).encode(
                x=_x, y=_y, color=alt.Color("referee:N", scale=alt.Scale(domain=["keeps", "refuses"], range=[GOOD, "#cbd5e1"]), legend=None),
                tooltip=[alt.Tooltip("limit:Q"), alt.Tooltip("right:Q", format=".1%"), alt.Tooltip("referee:N")]),
            alt.Chart(pd.DataFrame([{"limit": _s["current"]}])).mark_rule(strokeDash=[5, 4], color=INK).encode(x="limit:Q"),
            alt.Chart(pd.DataFrame([{"limit": _s["current"], "right": _dom[1], "t": "rule in use"}])).mark_text(
                align="left", dx=6, dy=10, color=INK, fontSize=13).encode(x="limit:Q", y=alt.Y("right:Q", scale=alt.Scale(domain=_dom)), text="t:N"),
            alt.Chart(pd.DataFrame([{"limit": _row["value"], "right": _row["exact_match"]}])).mark_circle(size=420, color=WARN, stroke="white", strokeWidth=3).encode(x=_x, y=_y),
        ).properties(width="container", height=260)
        _kept = _row["gate"] == "PASS"
        _verdict = mo.Html(
            f'<div style="padding:16px 20px;border-radius:16px;background:{"#f0fdf4" if _kept else "#f8fafc"};border:2px solid {GOOD if _kept else "#cbd5e1"}">'
            f'<div style="font-size:15px;color:#64748b">Touch limit <b style="color:#0f172a">{_row["value"]:g}</b> · gestures read right '
            f'<b style="color:#0f172a">{pct(_row["exact_match"])}</b></div><div style="font-size:26px;font-weight:800;margin-top:4px;'
            f'color:{GOOD if _kept else BAD}">{"✅ The referee keeps it" if _kept else "❌ The referee refuses it"}</div>'
            f'<div style="font-size:16px;color:#475569">{"because " + plain_reason(_row["why"])}</div></div>')
        _view = mo.vstack([sweep_slider, _verdict, mo.ui.altair_chart(style(_chart), chart_selection=False, legend_selection=False)], gap=1)
    _view
    return


@app.cell
def _(chapter, em, informed, mo, pct, snap):
    _scored = [v for v in informed if em(v.get("train")) is not None]
    _retro = snap.get("retrospective_validation")

    def _badge(icon, title, value, caption, tone, border):
        return mo.Html(f'<div style="flex:1;min-width:220px;padding:18px;border-radius:16px;background:{tone};border:1px solid {border}">'
                       f'<div style="font-size:28px">{icon}</div><div style="font:600 13px system-ui;text-transform:uppercase;'
                       f'letter-spacing:.06em;color:#64748b;margin-top:6px">{title}</div><div style="font-size:30px;font-weight:800;'
                       f'margin:2px 0">{value}</div><div style="color:#475569;font-size:14px">{caption}</div></div>')

    mo.vstack([
        chapter("evidence", 4, "Can we trust these numbers?", "Three checks, from easiest to hardest to pass."),
        mo.hstack([
            _badge("✅", "Practice gestures", pct(em(_scored[-1]["train"])) if _scored else "n/a",
                   "the gestures the AI learned from", "#f0fdf4", "#bbf7d0"),
            _badge("🟠", "Validation rétrospective", pct(_retro["b"]["exact_match"]) if _retro else "not run",
                   (f"20% of gestures set aside, same person, participants non identifiés · {pct(_retro['a']['exact_match'])} "
                    "before the AI" if _retro else "20% of gestures set aside, participants non identifiés"), "#fffbeb", "#fde68a"),
            _badge("⏳", "New people", "not tested", "needs 3 to 5 new people in front of the camera", "#f8fafc", "#e2e8f0"),
        ], justify="start", wrap=True, gap=1),
        mo.md("<span style='color:#64748b;font-size:14px'>The AI had already seen the set-aside gestures, so that check is a "
              "sanity check, not proof. Every AI decision is logged in Weave and every kept fix is a git commit.</span>"),
    ], gap=1)
    return


@app.cell
def _(mo):
    mo.Html(
        '<div style="margin:56px 0 12px;background:#0B0D10;color:#FFFFFF;padding:60px 36px;border-radius:20px;text-align:center;'
        'font:800 clamp(24px, 5vw, 42px)/1.15 Nunito, \'Avenir Next Rounded\', system-ui, sans-serif;overflow-wrap:anywhere">'
        "The child learns multiplication.<br>The tutor learns how to see the child.</div>"
    )
    return


if __name__ == "__main__":
    app.run()
