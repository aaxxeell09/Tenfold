"""Tenfold loop dashboard (SPEC.md section 10). A marimo notebook that reads one file, data/snapshot.json.

    make dashboard                               # marimo run dashboard/loop_dashboard.py
    marimo edit dashboard/loop_dashboard.py      # to change it
    TENFOLD_SNAPSHOT=/path/snapshot.json marimo run dashboard/loop_dashboard.py

No W&B key and no repo needed: without a local snapshot it reads the one committed on GitHub, so it runs on
molab as is. The loop rewrites and pushes the snapshot after every accepted version (loop/watch.py).

Layout, summary first and detail on demand: four figures, one chart with its values written on the lines, then
tabs. A gain is only ever computed between two scores measured on the same data with the same scorers, and the
retrospective validation keeps its own name: the critic saw those holds and participants are not identified.
"""
import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium", app_title="Tenfold loop")


@app.cell
def _():
    import json
    import os
    import urllib.request
    from pathlib import Path

    import marimo as mo
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mtick

    return Path, json, mo, mtick, os, plt, urllib


@app.cell
def _():
    INK, MUTED, GRID = "#111827", "#6b7280", "#e5e7eb"
    TRAIN, RETRO, HELDOUT, KNN, UP, DOWN = "#4b5563", "#d97706", "#16a34a", "#2563eb", "#16a34a", "#dc2626"
    RETRO_LABEL = "Validation rétrospective"
    RETRO_CAPTION = "participants non identifiés"
    return DOWN, GRID, HELDOUT, INK, KNN, MUTED, RETRO, RETRO_CAPTION, RETRO_LABEL, TRAIN, UP


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
    return informed, snap, snap_source


@app.cell
def _():
    def em(m):
        return None if not m else m.get("exact_match")

    def basis(v):
        """Two train scores are comparable only with the same data fingerprint and the same scorers."""
        return ((v.get("data") or {}).get("sha"), (v.get("train") or {}).get("scorers_sha256"))

    def pct(x):
        return "n/a" if x is None else f"{x * 100:.1f}%"

    def pts(d):
        return f"{d * 100:+.1f} pts"

    def direction(d):
        return "increase" if d > 1e-9 else "decrease" if d < -1e-9 else None

    def short(text, limit=150):
        text = " ".join((text or "").replace("DIAGNOSIS:", "").split())
        first = text.split(". ")[0]
        return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"

    return basis, direction, em, pct, pts, short


@app.cell
def _(RETRO_CAPTION, RETRO_LABEL, basis, direction, em, informed, mo, pct, pts, snap):
    _scored = [v for v in informed if em(v.get("train")) is not None]
    _last = _scored[-1] if _scored else None
    _base = next((v for v in _scored if basis(v)[0] and basis(v) == basis(_last)), None) if _last else None

    _commit = (snap.get("best_version") or "")[:7] or "not pinned"
    _remeasured = bool(_last) and _last.get("kind") == "data_refresh"
    _running = mo.stat(f"v{_last.get('version', 0)}" if _last else "none", label="Running version",
                       caption=(f"commit {_commit}, re-measured on {(_last.get('data') or {}).get('n_samples', '?')} samples"
                                if _remeasured else f"commit {_commit}"), bordered=True)
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
        _retro_card = mo.stat(pct(_retro["b"]["exact_match"]), label=RETRO_LABEL,
                              caption=f"{pts(_d)} vs V0, {RETRO_CAPTION}", direction=direction(_d), bordered=True)
    else:
        _retro_card = mo.stat("not run", label=RETRO_LABEL, caption=RETRO_CAPTION, bordered=True)
    _kept = sum(1 for v in informed if v.get("kind") == "patch")
    _spent = sum(v.get("spent_usd") or 0 for v in informed)
    _loop = mo.stat(f"{_kept} kept", label="Critic patches",
                    caption=f"{len(snap.get('rejected') or [])} rejected, ${_spent:.2f} spent", bordered=True)

    mo.vstack([
        mo.md("# Tenfold, the loop"),
        mo.md(f"<span style='color:#6b7280'>Agents rewrite the hand classifier; a version is kept only if the guard "
              f"and the metric gate pass it. Snapshot {str(snap.get('generated_at', ''))[:16].replace('T', ' ')} UTC.</span>"),
        mo.hstack([_running, _train, _retro_card, _loop], justify="start", wrap=True, gap=1),
    ], gap=1)
    return


@app.cell
def _(GRID, HELDOUT, INK, KNN, MUTED, RETRO, RETRO_LABEL, TRAIN, basis, em, informed, mo, mtick, plt, snap):
    plt.rcParams.update({"font.size": 12, "axes.edgecolor": GRID})
    _fig, _ax = plt.subplots(figsize=(10, 3.6))
    _xs = list(range(len(informed)))

    def _label(x, y, text, color, dy):
        _ax.annotate(text, (x, y), xytext=(12, dy), textcoords="offset points", va="center", fontsize=12,
                     fontweight="bold", color=color)

    def _series(points, color, name, style, dy):
        """points: (x, y, ci, segment). A new segment starts where the data or the scorers changed."""
        points = [p for p in points if p[1] is not None]
        if not points:
            return
        for _x, _y, _ci, _seg in points:
            if _ci:
                _ax.vlines(_x, _ci[0], _ci[1], color=color, alpha=0.25, linewidth=7)
        for _seg in dict.fromkeys(p[3] for p in points):
            _part = [p for p in points if p[3] == _seg]
            _ax.plot([p[0] for p in _part], [p[1] for p in _part], color=color, linestyle=style, linewidth=2.5,
                     marker="o", markersize=7, zorder=3)
        _label(points[-1][0], points[-1][1], f"{name}  {points[-1][1] * 100:.1f}%", color, dy)

    _segments, _seg_id, _previous = [], 0, None
    for _v in informed:
        if _previous is not None and basis(_v) != basis(_previous):
            _seg_id += 1
        _segments.append(_seg_id)
        _previous = _v
    _series([(x, em(v.get("train")), (v.get("train") or {}).get("exact_match_ci95"), s)
             for x, v, s in zip(_xs, informed, _segments)], TRAIN, "train", "-", 10)
    _series([(x, em(v.get("heldout")), (v.get("heldout") or {}).get("exact_match_ci95"), s)
             for x, v, s in zip(_xs, informed, _segments)], HELDOUT, "held-out", "-", 26)

    _retro = snap.get("retrospective_validation")
    if _retro and informed:
        _xa = next((x for x, v in zip(_xs, informed) if v.get("version") == 0), 0)
        _xb = next((x for x, v in zip(_xs, informed) if v.get("sha") == _retro["b"]["commit"]), _xs[-1])
        _series([(_xa, _retro["a"]["exact_match"], _retro["a"]["ci95"], "retro"),
                 (_xb, _retro["b"]["exact_match"], _retro["b"]["ci95"], "retro")], RETRO, RETRO_LABEL.lower(), "--", -12)

    _knn = em(snap.get("knn_heldout"))
    if _knn is not None:
        _ax.axhline(_knn, color=KNN, linestyle=":", linewidth=1.5)
        _ax.annotate("kNN", (0, _knn), xytext=(-34, 0), textcoords="offset points", va="center", color=KNN, fontsize=10)
    _ceiling = (snap.get("detection_ceiling") or {}).get("both_hands_contact_frames")
    if _ceiling is not None:
        _ax.axhline(_ceiling, color=INK, linewidth=1)
        _ax.annotate("detection ceiling", (0, _ceiling), xytext=(0, 5), textcoords="offset points", color=INK, fontsize=10)
    for _x, _v in zip(_xs, informed):
        if _v.get("kind") == "data_refresh":
            _ax.axvline(_x, color=MUTED, linestyle=":", linewidth=1)
            _ax.annotate("new data", (_x, 0.02), xytext=(4, 0), textcoords="offset points", color=MUTED, fontsize=10)

    _ax.set_xticks(_xs, [v["tag"] for v in informed])
    _ax.set_xlim(-0.35, max(len(_xs) - 1, 1) + 1.1)
    _ax.set_ylim(0, 1)
    _ax.yaxis.set_major_formatter(mtick.PercentFormatter(1.0, decimals=0))
    _ax.grid(axis="y", color=GRID, linewidth=1)
    _ax.set_axisbelow(True)
    for _side in ("top", "right", "left"):
        _ax.spines[_side].set_visible(False)
    _ax.tick_params(axis="y", length=0, colors=MUTED)
    _ax.tick_params(axis="x", length=0, labelsize=13)
    _fig.tight_layout()

    mo.vstack([
        mo.md("### Exact match, version by version"),
        mo.as_html(_fig),
        mo.md("<span style='color:#6b7280;font-size:0.9em'>Bars are 95% confidence intervals over holds. "
              "Train: the holds the critic works on. Validation rétrospective: holds set aside after the critic had "
              "seen them, participants non identifiés, not a test on new people.</span>"),
    ])
    return


@app.cell
def _(informed, mo):
    version_pick = mo.ui.dropdown(options=[v["tag"] for v in reversed(informed)] or ["none"],
                                  value=next((v["tag"] for v in reversed(informed) if v.get("kind") == "patch"),
                                             informed[-1]["tag"] if informed else "none"), label="Version")
    return (version_pick,)


@app.cell
def _(DOWN, GRID, MUTED, RETRO_CAPTION, RETRO_LABEL, UP, basis, em, informed, mo, pct, plt, short, snap, version_pick):
    def _changed_classes(v, prev):
        now = (v.get("train") or {}).get("per_class") or {}
        before = ((prev or {}).get("train") or {}).get("per_class") or {}
        deltas = {c: (now[c] or 0) - (before.get(c) or 0) for c in now
                  if prev and c in before and abs((now[c] or 0) - (before.get(c) or 0)) > 1e-9}
        if not deltas:
            return mo.md(f"<span style='color:{MUTED}'>" + ("No class changed against the previous version." if prev
                         else "Nothing to compare: no earlier version on the same data and scorers.") + "</span>")
        classes = sorted(deltas, key=lambda c: deltas[c])
        fig, ax = plt.subplots(figsize=(6.4, 0.42 * len(classes) + 0.8))
        ax.barh(classes, [deltas[c] * 100 for c in classes], color=[UP if deltas[c] > 0 else DOWN for c in classes], height=0.6)
        for i, c in enumerate(classes):
            ax.annotate(f"{deltas[c] * 100:+.0f}", (deltas[c] * 100, i), xytext=(5 if deltas[c] > 0 else -5, 0),
                        textcoords="offset points", va="center", ha="left" if deltas[c] > 0 else "right", fontsize=11)
        ax.axvline(0, color=MUTED, linewidth=1)
        ax.set_xlabel("points of accuracy vs the previous version", color=MUTED, fontsize=10)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.tick_params(length=0)
        ax.grid(axis="x", color=GRID)
        ax.set_axisbelow(True)
        lim = max(abs(d) for d in deltas.values()) * 100 * 1.35
        ax.set_xlim(-lim if min(deltas.values()) < 0 else -lim * 0.1, lim)
        fig.tight_layout()
        return mo.as_html(fig)

    if not informed:
        _what = mo.md("No version in this snapshot yet.")
    else:
        _i = next((i for i, v in enumerate(informed) if v["tag"] == version_pick.value), len(informed) - 1)
        _v, _prev = informed[_i], informed[_i - 1] if _i > 0 else None
        if _v.get("kind") == "patch":
            _cards = mo.hstack([
                mo.callout(mo.md(f"**Diagnosis**\n\n{short(_v.get('diagnosis'))}"), kind="neutral"),
                mo.callout(mo.md(f"**Fix**\n\n{short(_v.get('patch'), 140)}"), kind="info"),
                mo.callout(mo.md(f"**Gate**\n\n{_v.get('gate') or 'passed'}"), kind="success"),
            ], widths="equal", gap=1)
        elif _v.get("kind") == "data_refresh":
            _cards = mo.callout(mo.md(f"**Same rules, new data.** v{_v.get('version', 0)} re-measured on "
                                      f"{(_v.get('data') or {}).get('n_samples', '?')} samples; the next patch is judged "
                                      "against this score."), kind="neutral")
        else:
            _cards = mo.callout(mo.md("**Baseline.** The rules as first written, before any critic patch."), kind="neutral")
        if _prev is not None and basis(_prev) != basis(_v):
            _prev = None  # different data or scorers: a class by class difference would not mean anything
        _previous_slices = ((_prev or {}).get("train") or {}).get("per_slice") or {}
        _conditions = [{"condition": k, _v["tag"]: pct(s.get("exact_match")),
                        **({_prev["tag"]: pct((_previous_slices.get(k) or {}).get("exact_match"))} if _prev else {})}
                       for k, s in ((_v.get("train") or {}).get("per_slice") or {}).items()]
        _details = {}
        if _conditions:
            _details["Accuracy by capture condition"] = mo.ui.table(_conditions, selection=None)
        if _v.get("patch_hypothesis") or _v.get("expected"):
            _details["Full reasoning of the agents"] = mo.md("\n\n".join(
                f"**{label}** {_v[key]}" for key, label in (("diagnosis", "Diagnostic agent:"),
                                                            ("patch_hypothesis", "Patch agent measured:"),
                                                            ("expected", "Expected:")) if _v.get(key)))
        if _v.get("diff"):
            _details["Diff written by the critic"] = mo.md(f"```diff\n{_v['diff']}\n```")
        _what = mo.vstack([version_pick, _cards, mo.md("**Classes that changed**"), _changed_classes(_v, _prev),
                           mo.accordion(_details) if _details else mo.md("")], gap=1)

    _rows = [{"version": v["tag"], "kind": v.get("kind") or "", "train": pct(em(v.get("train"))),
              "samples": (v.get("data") or {}).get("n_samples", ""), "gate": v.get("gate") or "",
              "USD": "" if v.get("spent_usd") is None else f"{v['spent_usd']:.2f}"} for v in informed]
    _rejected = [{"when": (r.get("ts") or "")[:16].replace("T", " "), "why the gate said no": r.get("reason"),
                  "patch": short(r.get("patch"), 90)} for r in snap.get("rejected") or []]
    _retro = snap.get("retrospective_validation")
    _retro_line = (f"- **{RETRO_LABEL}** ({RETRO_CAPTION}): {_retro['holds']} holds set aside from the same capture after "
                   f"the critic had seen them, strict scoring; V0 {pct(_retro['a']['exact_match'])}, running "
                   f"{pct(_retro['b']['exact_match'])}. Not a test on new people.\n" if _retro else
                   f"- **{RETRO_LABEL}** ({RETRO_CAPTION}): not run yet.\n")
    _method = mo.md(
        "- **Train**: the holds the critic diagnoses and patches against (the 80% side once the split is active).\n"
        + _retro_line +
        "- **Held-out**: other people's hands, captured separately; appears on the chart once measured.\n"
        "- **Gains** are written only between versions measured on the same data with the same scorers.\n"
        "- Every rejection, including the guard agent's, is traced in Weave (project `tenfold`)."
    )
    mo.ui.tabs({
        "What changed": _what,
        "All versions": mo.ui.table(_rows, selection=None) if _rows else mo.md("No version yet."),
        "Rejected": mo.ui.table(_rejected, selection=None) if _rejected else mo.md(
            f"<span style='color:{MUTED}'>None in this snapshot. Every rejection is traced in Weave, op `critic.rejected_patch`.</span>"),
        "How to read this": _method,
    })
    return


@app.cell
def _(mo):
    mo.Html(
        '<div style="margin-top:12px;background:#0B0D10;color:#FFFFFF;padding:56px 40px;border-radius:16px;text-align:center;'
        'font:800 48px/1.15 Nunito, \'Avenir Next Rounded\', system-ui, sans-serif">'
        "The child learns multiplication.<br>The tutor learns how to see the child.</div>"
    )
    return


if __name__ == "__main__":
    app.run()
