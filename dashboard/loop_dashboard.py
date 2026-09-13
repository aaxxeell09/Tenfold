"""Tenfold: an explorable explanation of a perception loop that rewrites itself. A marimo app over one file,
data/snapshot.json.

    make dashboard                               # marimo run dashboard/loop_dashboard.py
    marimo edit dashboard/loop_dashboard.py      # to change it
    TENFOLD_SNAPSHOT=/path/snapshot.json marimo run dashboard/loop_dashboard.py

No W&B key and no repo needed: without a local snapshot it reads the one committed on GitHub, so it runs on molab
and as WebAssembly. The loop rewrites and pushes the snapshot after every accepted version (loop/watch.py).

Four chapters, summary first and detail on demand:
1. The loop, version by version: click a version on the chart and the rest of the chapter follows.
2. See what the critic saw: real landmarks of a hold, a contact threshold you can drag, the rules' real answer.
3. Be the critic: sweep a constant of the running rules and watch the metric gate decide, as the patch agent does.
4. Evidence: what is measured, on which data, and what is not claimed.
A gain is only computed between scores on the same data with the same scorers. The retrospective validation keeps its
own name: the critic saw those holds and participants are not identified. Explorables use the train side only.
"""
import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium", app_title="Tenfold, the loop")


@app.cell
def _():
    import json
    import os
    import urllib.request
    from pathlib import Path

    import altair as alt
    import marimo as mo
    import pandas as pd

    return Path, alt, json, mo, os, pd, urllib


@app.cell
def _():
    INK, MUTED, GRID, SOFT = "#0f172a", "#64748b", "#e2e8f0", "#f8fafc"
    TRAIN, RETRO, HELDOUT, UP, DOWN, PASSC, FAILC = "#334155", "#d97706", "#16a34a", "#16a34a", "#dc2626", "#16a34a", "#cbd5e1"
    LEFT_HAND, RIGHT_HAND = "#2563eb", "#9333ea"
    FONT = "Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    RETRO_LABEL, RETRO_CAPTION = "Validation rétrospective", "participants non identifiés"
    HAND_BONES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
                  (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]

    def style(chart):
        return (chart.configure(font=FONT)
                .configure_view(strokeWidth=0)
                .configure_axis(labelColor=MUTED, titleColor=MUTED, gridColor=GRID, domainColor=GRID, tickColor=GRID,
                                labelFontSize=12, titleFontSize=12, titleFontWeight="normal", labelPadding=6)
                .configure_legend(labelColor=MUTED, titleColor=MUTED, orient="bottom", labelFontSize=12))

    return (DOWN, FAILC, GRID, HAND_BONES, HELDOUT, INK, LEFT_HAND, MUTED, PASSC, RETRO, RETRO_CAPTION, RETRO_LABEL,
            RIGHT_HAND, SOFT, TRAIN, UP, style)


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
    return explorer, informed, snap, snap_source


@app.cell
def _():
    def em(m):
        return None if not m else m.get("exact_match")

    def basis(v):
        """Two train scores are comparable only with the same data fingerprint and the same scorers."""
        return ((v.get("data") or {}).get("sha"), (v.get("train") or {}).get("scorers_sha256"))

    def pct(x, digits=1):
        return "n/a" if x is None else f"{x * 100:.{digits}f}%"

    def pts(d):
        return f"{d * 100:+.1f} pts"

    def direction(d):
        return "increase" if d > 1e-9 else "decrease" if d < -1e-9 else None

    def short(text, limit=150):
        text = " ".join((text or "").replace("DIAGNOSIS:", "").split())
        first = text.split(". ")[0]
        return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"

    def pose(out):
        if not out or out.get("method") == "unknown":
            return "unknown"
        return f"{out.get('left')} × {out.get('right')}, {'contact' if out.get('contact') else 'no contact'}"

    def same_pose(a, b):
        if (a or {}).get("method") == "unknown" or (b or {}).get("method") == "unknown":
            return (a or {}).get("method") == (b or {}).get("method")
        return (all((a or {}).get(k) == (b or {}).get(k) for k in ("method", "left", "right"))
                and bool((a or {}).get("contact")) == bool((b or {}).get("contact")))

    def chapter(anchor, number, title, lede):
        return mo_html(f'<div id="{anchor}" style="margin:48px 0 8px"><div style="font:600 12px/1 system-ui;letter-spacing:.14em;'
                       f'text-transform:uppercase;color:#d97706">Chapter {number}</div><h2 style="margin:6px 0 6px;font-size:28px;'
                       f'line-height:1.15">{title}</h2><p style="margin:0;color:#64748b;font-size:15px;max-width:720px">{lede}</p></div>')

    def mo_html(text):
        import marimo as _mo
        return _mo.Html(text)

    return basis, chapter, direction, em, pct, pose, pts, same_pose, short


@app.cell
def _(mo):
    mo.sidebar([
        mo.md("### Tenfold"),
        mo.nav_menu({"#top": "Overview", "#loop": "1 · The loop", "#see": "2 · What the critic saw",
                     "#critic": "3 · Be the critic", "#evidence": "4 · Evidence"}, orientation="vertical"),
    ], footer=mo.md("<span style='color:#64748b;font-size:12px'>Snapshot-driven, runs without the repo.</span>"))
    return


@app.cell
def _(RETRO_CAPTION, RETRO_LABEL, basis, direction, em, informed, mo, pct, pts, snap):
    _scored = [v for v in informed if em(v.get("train")) is not None]
    _last = _scored[-1] if _scored else None
    _base = next((v for v in _scored if basis(v)[0] and basis(v) == basis(_last)), None) if _last else None
    _commit = (snap.get("best_version") or "")[:7] or "not pinned"
    _running = mo.stat(f"v{_last.get('version', 0)}" if _last else "none", label="Running version",
                       caption=(f"commit {_commit}, re-measured on {(_last.get('data') or {}).get('n_samples', '?')} samples"
                                if _last and _last.get("kind") == "data_refresh" else f"commit {_commit}"), bordered=True)
    if _last and _base is not None and _base is not _last:
        _d = em(_last["train"]) - em(_base["train"])
        _train = mo.stat(pct(em(_last["train"])), label="Train accuracy", caption=f"{pts(_d)} vs {_base['tag']}, same data",
                         direction=direction(_d), bordered=True)
    else:
        _train = mo.stat(pct(em((_last or {}).get("train"))), label="Train accuracy",
                         caption="baseline on this data" if _last else "not evaluated yet", bordered=True)
    _retro = snap.get("retrospective_validation")
    if _retro:
        _d = _retro["b"]["exact_match"] - _retro["a"]["exact_match"]
        _retro_card = mo.stat(pct(_retro["b"]["exact_match"]), label=RETRO_LABEL, caption=f"{pts(_d)} vs V0, {RETRO_CAPTION}",
                              direction=direction(_d), bordered=True)
    else:
        _retro_card = mo.stat("not run", label=RETRO_LABEL, caption=RETRO_CAPTION, bordered=True)
    _kept = sum(1 for v in informed if v.get("kind") == "patch")
    _loop = mo.stat(f"{_kept} kept", label="Critic patches",
                    caption=f"{len(snap.get('rejected') or [])} rejected, ${sum(v.get('spent_usd') or 0 for v in informed):.2f} spent",
                    bordered=True)
    mo.vstack([
        mo.Html('<div id="top" style="padding:28px 0 4px"><div style="font:600 12px/1 system-ui;letter-spacing:.14em;'
                'text-transform:uppercase;color:#64748b">Tenfold · CoreWeave Hacks</div>'
                '<h1 style="margin:10px 0 8px;font-size:44px;line-height:1.05;letter-spacing:-.02em">The tutor learns how to see the child.</h1>'
                '<p style="margin:0;color:#475569;font-size:18px;max-width:760px">Three agents rewrite the hand classifier of a finger '
                'multiplication tutor. A version survives only if a code guard and a metric gate pass it. This page shows what they '
                f'changed, lets you move the same dials, and says what is proven. <span style="color:#94a3b8">Snapshot '
                f'{str(snap.get("generated_at", ""))[:16].replace("T", " ")} UTC.</span></p></div>'),
        mo.hstack([_running, _train, _retro_card, _loop], justify="start", wrap=True, gap=1),
    ], gap=1)
    return


@app.cell
def _(RETRO, alt, chapter, em, informed, mo, pct, pd, short, snap, style, basis):
    _segment, _prev = 0, None
    _rows = []
    for _i, _v in enumerate(informed):
        if _prev is not None and basis(_v) != basis(_prev):
            _segment += 1
        _prev = _v
        _ci = (_v.get("train") or {}).get("exact_match_ci95") or [None, None]
        _rows.append({"order": _i, "tag": _v["tag"], "kind": _v.get("kind") or "version", "train": em(_v.get("train")),
                      "lo": _ci[0], "hi": _ci[1], "segment": _segment, "label": pct(em(_v.get("train"))),
                      "samples": (_v.get("data") or {}).get("n_samples"), "gate": _v.get("gate") or "",
                      "patch": short(_v.get("patch"), 90) or ("rules re-measured on new data" if _v.get("kind") == "data_refresh" else "")})
    for _k, _r in enumerate(_rows):
        _r["show"] = _k == len(_rows) - 1 or _rows[_k + 1]["segment"] != _r["segment"]
    versions_df = pd.DataFrame(_rows)
    _tags = list(versions_df["tag"]) if len(versions_df) else []
    _x = alt.X("tag:N", sort=_tags, title=None, axis=alt.Axis(labelAngle=0 if len(_tags) <= 4 else -35, labelFontSize=13, labelColor="#0f172a"))
    _y = alt.Y("train:Q", title=None, scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format="%", tickCount=5))
    _pick = alt.selection_point(name="version", fields=["tag"], on="click", empty=False)
    _kind_color = alt.Color("kind:N", scale=alt.Scale(domain=["baseline", "patch", "data_refresh"],
                                                      range=["#94a3b8", "#16a34a", "#f8fafc"]), legend=None)
    _tooltip = [alt.Tooltip("tag:N", title="version"), alt.Tooltip("kind:N"), alt.Tooltip("train:Q", format=".1%", title="train"),
                alt.Tooltip("samples:Q"), alt.Tooltip("gate:N"), alt.Tooltip("patch:N")]
    _base = alt.Chart(versions_df)
    _layers = [
        _base.mark_rule(strokeWidth=9, opacity=0.14, color="#334155").encode(x=_x, y=alt.Y("lo:Q", scale=alt.Scale(domain=[0, 1])), y2="hi:Q"),
        _base.mark_line(strokeWidth=3, color="#334155").encode(x=_x, y=_y, detail="segment:N"),
        _base.mark_circle(size=220, stroke="#334155", strokeWidth=2, opacity=1).encode(
            x=_x, y=_y, color=_kind_color, tooltip=_tooltip,
            size=alt.condition(_pick, alt.value(520), alt.value(220))).add_params(_pick),
        _base.transform_filter(alt.datum.show).mark_text(dy=-22, fontSize=13, fontWeight="bold", color="#0f172a").encode(x=_x, y=_y, text="label:N"),
    ]
    for _r in _rows:
        if _r["kind"] == "data_refresh":
            _layers.append(alt.Chart(pd.DataFrame([_r])).mark_rule(strokeDash=[4, 4], color="#94a3b8").encode(x=_x))
            _layers.append(alt.Chart(pd.DataFrame([{**_r, "y": 0.06, "note": f"new data · {_r['samples']} samples"}]))
                           .mark_text(align="left", dx=8, color="#64748b", fontSize=12).encode(x=_x, y=alt.Y("y:Q"), text="note:N"))
    _retro = snap.get("retrospective_validation")
    if _retro and _tags:
        _b_tag = next((v["tag"] for v in informed if v.get("sha") == _retro["b"]["commit"]), _tags[-1])
        _rdf = pd.DataFrame([{"tag": _tags[0], "value": _retro["a"]["exact_match"], "lo": (_retro["a"].get("ci95") or [None])[0],
                              "hi": (_retro["a"].get("ci95") or [None, None])[1], "label": ""},
                             {"tag": _b_tag, "value": _retro["b"]["exact_match"], "lo": (_retro["b"].get("ci95") or [None])[0],
                              "hi": (_retro["b"].get("ci95") or [None, None])[1], "label": f"validation rétrospective {pct(_retro['b']['exact_match'])}"}])
        _layers += [
            alt.Chart(_rdf).mark_line(strokeDash=[6, 4], strokeWidth=2.5, color=RETRO).encode(x=_x, y=alt.Y("value:Q", scale=alt.Scale(domain=[0, 1]))),
            alt.Chart(_rdf).mark_circle(size=90, color=RETRO).encode(x=_x, y="value:Q",
                                                                    tooltip=[alt.Tooltip("value:Q", format=".1%", title="validation rétrospective")]),
            alt.Chart(_rdf).mark_text(dy=22, color=RETRO, fontSize=12, fontWeight="bold").encode(x=_x, y="value:Q", text="label:N"),
        ]
    version_chart = mo.ui.altair_chart(style(alt.layer(*_layers).properties(width="container", height=300)),
                                       chart_selection=False, legend_selection=False)
    mo.vstack([
        chapter("loop", 1, "The loop, version by version",
                "Each point is a version the gate kept. Click one to open what the agents changed and why. Grey bars are 95% "
                "intervals over holds; the line breaks where the data changed, because scores on different data do not compare."),
        version_chart,
    ])
    return version_chart, versions_df


@app.cell
def _(informed, mo):
    version_menu = mo.ui.dropdown(options=[v["tag"] for v in reversed(informed)] or ["none"],
                                  value=next((v["tag"] for v in reversed(informed) if v.get("kind") == "patch"),
                                             informed[-1]["tag"] if informed else "none"), label="or pick")
    return (version_menu,)


@app.cell
def _(DOWN, UP, alt, basis, informed, mo, pd, short, style, version_chart, version_menu, versions_df):
    try:
        _clicked = version_chart.apply_selection(versions_df)
        _clicked_tag = _clicked["tag"].iloc[0] if len(_clicked) and len(_clicked) < len(versions_df) else None
    except Exception:
        _clicked_tag = None
    _tag = _clicked_tag or version_menu.value
    _i = next((i for i, v in enumerate(informed) if v["tag"] == _tag), len(informed) - 1)
    if not informed:
        _out = mo.md("No version in this snapshot yet.")
    else:
        _v = informed[_i]
        _prev = informed[_i - 1] if _i > 0 else None
        if _prev is not None and basis(_prev) != basis(_v):
            _prev = None
        if _v.get("kind") == "patch":
            _cards = mo.hstack([
                mo.callout(mo.md(f"**What the diagnostic agent saw**\n\n{short(_v.get('diagnosis'))}"), kind="neutral"),
                mo.callout(mo.md(f"**What the patch agent changed**\n\n{short(_v.get('patch'), 150)}"), kind="info"),
                mo.callout(mo.md(f"**What the metric gate said**\n\n{_v.get('gate') or 'passed'}"), kind="success"),
            ], justify="start", wrap=True, gap=1)
        elif _v.get("kind") == "data_refresh":
            _cards = mo.callout(mo.md(f"**Same rules, new data.** v{_v.get('version', 0)} re-measured on "
                                      f"{(_v.get('data') or {}).get('n_samples', '?')} samples; the next patch is judged against this score."),
                                kind="neutral")
        else:
            _cards = mo.callout(mo.md("**Baseline.** The rules as a developer first wrote them, before any agent touched them."), kind="neutral")

        _now = (_v.get("train") or {}).get("per_class") or {}
        _before = ((_prev or {}).get("train") or {}).get("per_class") or {}
        _deltas = [{"class": c, "change": (_now[c] or 0) - (_before.get(c) or 0)} for c in _now
                   if _prev and c in _before and abs((_now[c] or 0) - (_before.get(c) or 0)) > 1e-9]
        if _deltas:
            _ddf = pd.DataFrame(_deltas).sort_values("change", ascending=False)
            _bars = alt.Chart(_ddf).mark_bar(cornerRadiusEnd=4, height=18).encode(
                y=alt.Y("class:N", sort=list(_ddf["class"]), title=None, axis=alt.Axis(labelFontSize=13, labelColor="#0f172a")),
                x=alt.X("change:Q", title=f"accuracy change vs {_prev['tag']}", axis=alt.Axis(format="+%")),
                color=alt.condition(alt.datum.change > 0, alt.value(UP), alt.value(DOWN)),
                tooltip=[alt.Tooltip("class:N"), alt.Tooltip("change:Q", format="+.0%")])
            _labels = alt.Chart(_ddf).mark_text(align="left", dx=6, fontSize=12, color="#334155").encode(
                y=alt.Y("class:N", sort=list(_ddf["class"])), x="change:Q", text=alt.Text("change:Q", format="+.0%"))
            _change_view = mo.ui.altair_chart(style((_bars + _labels).properties(width="container", height=26 * len(_ddf) + 30)),
                                              chart_selection=False, legend_selection=False)
        else:
            _change_view = mo.md("<span style='color:#64748b'>" + ("No class changed against the previous version." if _prev
                                 else "Nothing to compare: no earlier version on the same data and scorers.") + "</span>")
        _details = {}
        _conditions = [{"condition": k, _v["tag"]: f"{s['exact_match'] * 100:.1f}%"}
                       for k, s in ((_v.get("train") or {}).get("per_slice") or {}).items() if s.get("exact_match") is not None]
        if _conditions:
            _details["Accuracy by capture condition"] = mo.ui.table(_conditions, selection=None)
        if _v.get("patch_hypothesis") or _v.get("expected"):
            _details["Full reasoning of the agents"] = mo.md("\n\n".join(
                f"**{label}** {_v[key]}" for key, label in (("diagnosis", "Diagnostic agent:"), ("patch_hypothesis", "Patch agent measured:"),
                                                            ("expected", "Expected:")) if _v.get(key)))
        if _v.get("diff"):
            _details["The diff the critic committed"] = mo.md(f"```diff\n{_v['diff']}\n```")
        _out = mo.vstack([
            mo.hstack([mo.md(f"### {_v['tag']}" + (" · clicked on the chart" if _clicked_tag else "")), version_menu],
                      justify="space-between", align="center"),
            _cards, mo.md("**Classes that moved**"), _change_view,
            mo.accordion(_details) if _details else mo.md(""),
        ], gap=1)
    _out
    return


@app.cell
def _(chapter, explorer, mo):
    _names = {"near": "Near miss", "pos": "Contact"}
    example_options = {}
    for _i, _x in enumerate((explorer or {}).get("examples", [])):
        _c = _x["class"]
        _kind, _pair = ("near", _c.split(":", 1)[1]) if _c.startswith("near:") else ("pos", _c)
        example_options[f"{_names[_kind]} {_pair.replace('x', ' × ')}"] = _i
    example_pick = mo.ui.radio(options=example_options, value=next(iter(example_options), None), inline=True,
                               label="") if example_options else None
    mo.vstack([
        chapter("see", 2, "See what the critic saw",
                "These are real landmarks from the train side, drawn the way the classifier reads them. The classifier "
                "joins the two closest fingertips and calls it a contact if they are closer than a threshold, in units of "
                "hand size. Near misses are fingers two centimetres apart: V0 called many of them contact. Drag the threshold."),
        mo.vstack([mo.md("**Pick a real hold**"), example_pick]) if example_pick is not None else mo.callout(mo.md("Run `make snapshot` on a repo with data to fill this chapter."), kind="warn"),
    ])
    return (example_pick,)


@app.cell
def _(explorer, mo):
    _running = ((explorer or {}).get("constants") or {}).get("CONTACT_THRESHOLD", 0.35)
    get_threshold, set_threshold = mo.state(_running)
    return get_threshold, set_threshold


@app.cell
def _(explorer, get_threshold, mo, set_threshold):
    _running = ((explorer or {}).get("constants") or {}).get("CONTACT_THRESHOLD", 0.35)
    threshold_slider = mo.ui.slider(start=0.10, stop=0.50, step=0.0125, value=get_threshold(), on_change=set_threshold,
                                    label="Contact threshold", show_value=True, full_width=True)
    v0_button = mo.ui.button(label="V0 rule · 0.35", on_click=lambda _: set_threshold(0.35))
    running_button = mo.ui.button(label=f"running rule · {_running:g}", kind="success", on_click=lambda _: set_threshold(_running))
    return running_button, threshold_slider, v0_button


@app.cell
def _(DOWN, GRID, HAND_BONES, LEFT_HAND, RIGHT_HAND, UP, alt, example_pick, explorer, get_threshold, mo, pd, pose,
      running_button, same_pose, style, threshold_slider, v0_button):
    if example_pick is None or not explorer:
        _view = mo.md("")
    else:
        _ex = explorer["examples"][example_pick.value]
        _t = get_threshold()
        _pts, _bones, _tips = [], [], []
        for _hand, _color in (("left", LEFT_HAND), ("right", RIGHT_HAND)):
            _p = _ex[_hand]
            _pts += [{"hand": _hand, "x": _x, "y": _y} for _x, _y in _p]
            _bones += [{"hand": _hand, "x": _p[_a][0], "y": _p[_a][1], "x2": _p[_b][0], "y2": _p[_b][1]} for _a, _b in HAND_BONES]
            _tips += [{"hand": _hand, "x": _p[_ti][0], "y": _p[_ti][1], "finger": str(_f)} for _f, _ti in zip(_ex["fingers"], _ex["tip_index"])]
        _d = _ex["tip_distances"]
        _li, _rj = min(((i, j) for i in range(5) for j in range(5)), key=lambda ij: _d[ij[0]][ij[1]])
        _dist = _d[_li][_rj]
        _lt, _rt = _ex["left"][_ex["tip_index"][_li]], _ex["right"][_ex["tip_index"][_rj]]
        _answer = min(_ex["answers_by_contact_threshold"], key=lambda a: abs(a["threshold"] - _t)) if _ex["answers_by_contact_threshold"] else _ex["running"]
        _right = same_pose(_answer, _ex["label"])
        _verdict_color = UP if _right else DOWN
        _xs = [p["x"] for p in _pts]
        _ys = [p["y"] for p in _pts]
        _cx, _cy = (min(_xs) + max(_xs)) / 2, (min(_ys) + max(_ys)) / 2
        _half = max(max(_xs) - min(_xs), max(_ys) - min(_ys)) / 2 * 1.18
        _sx = alt.Scale(domain=[_cx - _half, _cx + _half], nice=False)
        _sy = alt.Scale(domain=[_cy - _half, _cy + _half], reverse=True, nice=False)
        _hand_color = alt.Color("hand:N", scale=alt.Scale(domain=["left", "right"], range=[LEFT_HAND, RIGHT_HAND]), legend=None)
        _pair = pd.DataFrame([{"x": _lt[0], "y": _lt[1], "x2": _rt[0], "y2": _rt[1],
                               "mx": (_lt[0] + _rt[0]) / 2, "my": (_lt[1] + _rt[1]) / 2, "note": f"{_dist:.3f}"}])
        _hands = alt.layer(
            alt.Chart(pd.DataFrame(_bones)).mark_rule(strokeWidth=3.5, strokeCap="round", opacity=0.85).encode(
                x=alt.X("x:Q", scale=_sx, axis=None), y=alt.Y("y:Q", scale=_sy, axis=None), x2="x2:Q", y2="y2:Q", color=_hand_color),
            alt.Chart(pd.DataFrame(_pts)).mark_circle(size=34, opacity=1).encode(x=alt.X("x:Q", scale=_sx), y=alt.Y("y:Q", scale=_sy), color=_hand_color),
            alt.Chart(_pair).mark_rule(strokeWidth=4, strokeDash=[2, 3], color=_verdict_color).encode(
                x=alt.X("x:Q", scale=_sx), y=alt.Y("y:Q", scale=_sy), x2="x2:Q", y2="y2:Q"),
            alt.Chart(pd.DataFrame(_tips)).mark_circle(size=260, opacity=1, stroke="white", strokeWidth=2).encode(
                x=alt.X("x:Q", scale=_sx), y=alt.Y("y:Q", scale=_sy), color=_hand_color),
            alt.Chart(pd.DataFrame(_tips)).mark_text(fontSize=10, fontWeight="bold", color="white").encode(
                x=alt.X("x:Q", scale=_sx), y=alt.Y("y:Q", scale=_sy), text="finger:N"),
            alt.Chart(_pair).mark_text(dy=-14, fontSize=13, fontWeight="bold", color=_verdict_color).encode(
                x=alt.X("mx:Q", scale=_sx), y=alt.Y("my:Q", scale=_sy), text="note:N"),
        ).properties(width=360, height=360)

        _scale_max = 0.6
        _zones = pd.DataFrame([{"from": 0, "to": _t, "zone": "contact"}, {"from": _t, "to": _scale_max, "zone": "no contact"}])
        _gauge = alt.layer(
            alt.Chart(_zones).mark_bar(height=26, cornerRadius=4).encode(
                x=alt.X("from:Q", scale=alt.Scale(domain=[0, _scale_max]), title="closest fingertips, in hand sizes"), x2="to:Q",
                color=alt.Color("zone:N", scale=alt.Scale(domain=["contact", "no contact"], range=["#fde68a", "#e2e8f0"]), legend=None)),
            alt.Chart(_zones).mark_text(align="left", dx=6, fontSize=11, color="#475569").encode(x="from:Q", text="zone:N"),
            alt.Chart(pd.DataFrame([{"x": _dist}])).mark_tick(thickness=4, size=40, color=_verdict_color).encode(x="x:Q"),
            alt.Chart(pd.DataFrame([{"x": 0.35, "n": "V0"}])).mark_text(dy=-24, fontSize=11, color="#64748b").encode(x="x:Q", text="n:N"),
            alt.Chart(pd.DataFrame([{"x": 0.35}])).mark_rule(strokeDash=[3, 3], color="#64748b").encode(x="x:Q"),
        ).properties(width="container", height=70)

        _verdict = mo.Html(
            f'<div style="border-radius:14px;padding:18px 20px;background:{"#f0fdf4" if _right else "#fef2f2"};'
            f'border:1px solid {"#bbf7d0" if _right else "#fecaca"}">'
            f'<div style="font:600 12px/1 system-ui;letter-spacing:.12em;text-transform:uppercase;color:#64748b">The rules answer</div>'
            f'<div style="font-size:30px;font-weight:700;margin:8px 0 4px;color:{_verdict_color}">{pose(_answer)} {"✓" if _right else "✗"}</div>'
            f'<div style="color:#475569">Label: <b>{pose(_ex["label"])}</b> · closest tips {_dist:.3f} vs threshold {_t:.4g}</div>'
            f'<div style="color:#94a3b8;font-size:12px;margin-top:6px">Answer from the real rules.py at this threshold, not a re-implementation. '
            f'Hold {_ex["hold_id"]}, {_ex["angle"]} / {_ex["distance"]}.</div></div>')
        _view = mo.hstack([
            mo.ui.altair_chart(style(_hands), chart_selection=False, legend_selection=False),
            mo.vstack([_verdict, threshold_slider, mo.hstack([v0_button, running_button], justify="start", gap=1),
                       mo.ui.altair_chart(style(_gauge), chart_selection=False, legend_selection=False),
                       mo.md("<span style='color:#64748b;font-size:14px'>Blue is the left hand, purple the right, numbered 6 to 10 "
                             "from thumb to little finger. Try a near miss at 0.35, then at the running rule.</span>")], gap=1),
        ], wrap=True, gap=2, align="start")
    _view
    return


@app.cell
def _(chapter, explorer, mo):
    _pretty = {"CONTACT_THRESHOLD": "Contact threshold", "UNKNOWN_THRESHOLD": "Confidence floor (unknown below)"}
    _sweep = (explorer or {}).get("sweep") or {}
    constant_pick = mo.ui.radio(options={_pretty.get(k, k): k for k in _sweep}, value=next((_pretty.get(k, k) for k in _sweep), None),
                                inline=True, label="Constant") if _sweep else None
    _best = []
    for _name, _s in _sweep.items():
        _passing = [r for r in _s["rows"] if r["gate"] == "PASS"]
        if _passing:
            _top = max(_passing, key=lambda r: r["exact_match"])
            _best.append(f"`{_name} = {_top['value']:g}` passes the gate ({_top['why']})")
    mo.vstack([
        chapter("critic", 3, "Be the critic",
                "The patch agent does not guess values: it sweeps them and asks the metric gate. Here is that sweep, "
                f"precomputed on the train side for the running rules ({(explorer or {}).get('windows', '?')} windows). "
                "The gate passes a value only if exact match strictly rises and no class or capture condition falls by more "
                "than 5 points."),
        constant_pick if constant_pick is not None else mo.callout(mo.md("Run `make snapshot` on a repo with data to fill this chapter."), kind="warn"),
        mo.callout(mo.md("**Open lead for the next iteration:** " + "; ".join(_best)), kind="info") if _best else mo.md(""),
    ])
    return (constant_pick,)


@app.cell
def _(constant_pick, explorer, mo):
    _rows = ((explorer or {}).get("sweep") or {}).get(constant_pick.value, {}).get("rows", []) if constant_pick is not None else []
    _current = ((explorer or {}).get("sweep") or {}).get(constant_pick.value, {}).get("current") if constant_pick is not None else None
    _start = min(range(len(_rows)), key=lambda i: abs(_rows[i]["value"] - (_current or 0))) if _rows else 0
    value_slider = mo.ui.slider(start=0, stop=max(len(_rows) - 1, 1), step=1, value=_start, show_value=False,
                                label="Drag the value", full_width=True) if _rows else None
    return (value_slider,)


@app.cell
def _(DOWN, FAILC, INK, PASSC, RETRO, UP, alt, constant_pick, direction, explorer, mo, pct, pd, pts, style, value_slider):
    if constant_pick is None or value_slider is None:
        _view = mo.md("")
    else:
        _s = explorer["sweep"][constant_pick.value]
        _rows = _s["rows"]
        _row = _rows[min(value_slider.value, len(_rows) - 1)]
        _base = explorer["gate_base"]
        _df = pd.DataFrame([{"value": r["value"], "exact_match": r["exact_match"], "gate": r["gate"], "why": r["why"]} for r in _rows])
        _lo, _hi = float(_df["exact_match"].min()), float(_df["exact_match"].max())
        _pad = max(0.01, (_hi - _lo) * 0.25)
        _y = alt.Y("exact_match:Q", title="exact match (train)", scale=alt.Scale(domain=[max(0, _lo - _pad), min(1, _hi + _pad)]),
                   axis=alt.Axis(format=".0%"))
        _x = alt.X("value:Q", title=constant_pick.value, scale=alt.Scale(zero=False))
        _chart = alt.layer(
            alt.Chart(_df).mark_line(color="#94a3b8", strokeWidth=2).encode(x=_x, y=_y),
            alt.Chart(_df).mark_circle(size=90, opacity=1).encode(
                x=_x, y=_y, color=alt.Color("gate:N", scale=alt.Scale(domain=["PASS", "FAIL"], range=[PASSC, FAILC]), legend=None),
                tooltip=[alt.Tooltip("value:Q"), alt.Tooltip("exact_match:Q", format=".2%"), alt.Tooltip("gate:N"), alt.Tooltip("why:N")]),
            alt.Chart(pd.DataFrame([{"value": _s["current"], "note": "running rules"}])).mark_rule(strokeDash=[4, 4], color=INK).encode(x="value:Q"),
            alt.Chart(pd.DataFrame([{"value": _s["current"], "note": "running rules", "y": _hi + _pad * 0.6}])).mark_text(
                align="left", dx=6, color=INK, fontSize=12).encode(x="value:Q", y=alt.Y("y:Q", scale=alt.Scale(domain=[max(0, _lo - _pad), min(1, _hi + _pad)])), text="note:N"),
            alt.Chart(pd.DataFrame([_row])).mark_rule(color=RETRO, strokeWidth=3).encode(x="value:Q"),
            alt.Chart(pd.DataFrame([_row])).mark_circle(size=260, color=RETRO, stroke="white", strokeWidth=2).encode(x=_x, y=_y),
        ).properties(width="container", height=280)

        _d = _row["exact_match"] - (_base.get("exact_match") or 0)
        _verdict = mo.callout(mo.md(f"### Gate: {_row['gate']}\n\n`{constant_pick.value} = {_row['value']:g}` · {_row['why']}"),
                              kind="success" if _row["gate"] == "PASS" else "warn")
        _stats = mo.hstack([
            mo.stat(pct(_row["exact_match"]), label="Exact match", caption=f"{pts(_d)} vs {_base.get('tag')}", direction=direction(_d), bordered=True),
            mo.stat(pct(_row.get("near_contact_accuracy")), label="Near misses read right",
                    caption=f"{pts((_row.get('near_contact_accuracy') or 0) - (_base.get('near_contact_accuracy') or 0))}", bordered=True),
            mo.stat(pct(_row.get("false_unknown_rate")), label="Refused positives",
                    caption=f"{pts((_row.get('false_unknown_rate') or 0) - (_base.get('false_unknown_rate') or 0))}, gate limit +2", bordered=True),
        ], justify="start", wrap=True, gap=1)
        _moved = [{"class": c, "change": (v or 0) - ((_base.get("per_class") or {}).get(c) or 0)}
                  for c, v in (_row.get("per_class") or {}).items() if abs((v or 0) - ((_base.get("per_class") or {}).get(c) or 0)) > 1e-9]
        if _moved:
            _mdf = pd.DataFrame(_moved).sort_values("change", ascending=False)
            _bars = alt.Chart(_mdf).mark_bar(cornerRadiusEnd=4, height=16).encode(
                y=alt.Y("class:N", sort=list(_mdf["class"]), title=None), x=alt.X("change:Q", title=f"class accuracy vs {_base.get('tag')}", axis=alt.Axis(format="+%")),
                color=alt.condition(alt.datum.change > 0, alt.value(UP), alt.value(DOWN)),
                tooltip=[alt.Tooltip("class:N"), alt.Tooltip("change:Q", format="+.0%")])
            _moved_view = mo.ui.altair_chart(style(_bars.properties(width="container", height=22 * len(_mdf) + 30)),
                                             chart_selection=False, legend_selection=False)
        else:
            _moved_view = mo.md("<span style='color:#64748b'>No class moves at this value.</span>")
        _view = mo.vstack([
            value_slider,
            mo.ui.altair_chart(style(_chart), chart_selection=False, legend_selection=False),
            mo.hstack([_verdict, _stats], justify="start", wrap=True, gap=2, align="start"),
            mo.md("**Classes that would move**"), _moved_view,
        ], gap=1)
    _view
    return


@app.cell
def _(HELDOUT, RETRO, RETRO_CAPTION, RETRO_LABEL, alt, chapter, mo, pct, pd, short, snap, style):
    _retro = snap.get("retrospective_validation")
    if _retro:
        _p = _retro.get("paired") or {}
        _rdf = pd.DataFrame([
            {"version": "V0", "value": _retro["a"]["exact_match"], "lo": (_retro["a"].get("ci95") or [None, None])[0], "hi": (_retro["a"].get("ci95") or [None, None])[1]},
            {"version": "running", "value": _retro["b"]["exact_match"], "lo": (_retro["b"].get("ci95") or [None, None])[0], "hi": (_retro["b"].get("ci95") or [None, None])[1]},
        ])
        _dots = alt.layer(
            alt.Chart(_rdf).mark_rule(strokeWidth=10, opacity=0.25, color=RETRO).encode(
                y=alt.Y("version:N", title=None, sort=["V0", "running"]), x=alt.X("lo:Q", scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format="%"), title=None), x2="hi:Q"),
            alt.Chart(_rdf).mark_circle(size=200, color=RETRO).encode(y=alt.Y("version:N", sort=["V0", "running"]), x="value:Q",
                                                                      tooltip=[alt.Tooltip("value:Q", format=".1%")]),
            alt.Chart(_rdf).mark_text(dy=-16, fontSize=12, fontWeight="bold", color=RETRO).encode(
                y=alt.Y("version:N", sort=["V0", "running"]), x="value:Q", text=alt.Text("value:Q", format=".1%")),
        ).properties(width="container", height=120)
        _retro_view = mo.vstack([
            mo.md(f"**{RETRO_LABEL}** · {RETRO_CAPTION} · {_retro['holds']} holds set aside after the fact, strict scoring"),
            mo.ui.altair_chart(style(_dots), chart_selection=False, legend_selection=False),
            mo.md(f"<span style='color:#475569'>Running minus V0 per hold: <b>{(_p.get('mean_difference') or 0) * 100:+.1f} pts</b>, "
                  f"95% CI [{(_p.get('ci95') or [0, 0])[0] * 100:+.1f}, {(_p.get('ci95') or [0, 0])[1] * 100:+.1f}], "
                  f"{_p.get('better', 0)} holds better, {_p.get('worse', 0)} worse. The critic had seen these holds and they come from "
                  "the same capture: a retrospective check, not a test on new people.</span>"),
        ])
    else:
        _retro_view = mo.callout(mo.md(f"**{RETRO_LABEL}** ({RETRO_CAPTION}) has not been run for the running version."), kind="neutral")
    _heldout = mo.callout(mo.md("**Held-out, other people's hands: not measured yet.** It needs a capture by three to five people "
                                "the loop has never seen. It is the only number that would show generalisation, and it appears on "
                                "the chapter 1 chart as soon as it exists."), kind="warn")
    _rejected = [{"when": (r.get("ts") or "")[:16].replace("T", " "), "why the gate said no": r.get("reason"), "patch": short(r.get("patch"), 90)}
                 for r in snap.get("rejected") or []]
    _how = mo.md(
        "- **Train**: the holds the agents diagnose and patch against (the 80% side of a seeded split of whole holds).\n"
        f"- **{RETRO_LABEL}**: the other 20%, set aside after the critic had read them, participants non identifiés.\n"
        "- **Held-out**: new people, captured separately. Not measured yet.\n"
        "- **Gains** are computed only between versions on the same data with the same scorers.\n"
        "- **Every agent call and every rejection** is traced in Weave (project `tenfold`); every kept version is a git "
        "commit by `critic-agent` with its diagnosis, patch and expected effect in the message.")
    mo.vstack([
        chapter("evidence", 4, "Evidence, and what is not claimed",
                "What the numbers are measured on, and the one measurement still missing."),
        mo.vstack([_retro_view, _heldout], gap=2),
        mo.accordion({"How to read this page": _how,
                      f"Patches the gate turned down ({len(_rejected)})": mo.ui.table(_rejected, selection=None) if _rejected
                      else mo.md("None in this snapshot. Earlier rejections are traced in Weave, op `critic.rejected_patch`.")}),
    ], gap=1)
    return


@app.cell
def _(mo):
    mo.Html(
        '<div style="margin:56px 0 12px;background:#0B0D10;color:#FFFFFF;padding:64px 40px;border-radius:20px;text-align:center;'
        'font:800 46px/1.15 Nunito, \'Avenir Next Rounded\', system-ui, sans-serif;letter-spacing:-.01em">'
        "The child learns multiplication.<br>The tutor learns how to see the child.</div>"
    )
    return


if __name__ == "__main__":
    app.run()
