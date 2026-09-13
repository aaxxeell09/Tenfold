"""Tenfold loop dashboard (SPEC.md section 10). A marimo notebook that reads one file, data/snapshot.json.

    make dashboard                               # marimo run dashboard/loop_dashboard.py
    marimo edit dashboard/loop_dashboard.py      # to change it
    TENFOLD_SNAPSHOT=/path/snapshot.json marimo run dashboard/loop_dashboard.py

No W&B key and no repo needed: without a local snapshot it reads the one committed on GitHub, so it runs on
molab as is. The loop rewrites and pushes the snapshot after every accepted version (loop/watch.py).
"""
import marimo

__generated_with = "0.24.2"
app = marimo.App(width="full", app_title="Tenfold loop")


@app.cell
def _():
    import json
    import os
    import urllib.request
    from pathlib import Path

    import marimo as mo
    import matplotlib.pyplot as plt

    return Path, json, mo, os, plt, urllib


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
def _(informed, mo, snap, snap_source):
    def _em(v, key="train"):
        return ((v or {}).get(key) or {}).get("exact_match")

    _patches = [v for v in informed if v.get("kind") == "patch" or (v.get("version") or 0) > 0 and not v.get("kind")]
    def _basis(v):
        return ((v.get("data") or {}).get("sha"), (v.get("train") or {}).get("scorers_sha256"))

    # Only two train scores measured on the same data with the same scorers are compared.
    _scored = [v for v in informed if _em(v) is not None]
    _last = _scored[-1] if _scored else {}
    _first = next((v for v in _scored if _basis(v)[0] and _basis(v) == _basis(_last)), _last)
    _same = "same data and scorers" if _basis(_last)[1] else "same data, scorer version not recorded"
    if not _scored:
        _train = "no train evaluation yet"
    elif _first is _last:
        _train = f"train exact_match **{_em(_last):.3f}** ({_last.get('tag')}, nothing comparable before it)"
    else:
        _train = (f"train exact_match **{_em(_first):.3f} → {_em(_last):.3f}** "
                  f"({_first.get('tag')} → {_last.get('tag')}, {_same})")
    _held = [v for v in informed if _em(v, "heldout") is not None]
    _held_line = (f"held-out exact_match **{_em(_held[0], 'heldout'):.3f} → {_em(_held[-1], 'heldout'):.3f}**"
                  if _held else "held-out: **not measured yet** (needs a capture by other people)")
    _spent = sum(v.get("spent_usd") or 0 for v in informed)
    mo.md(f"""
# Tenfold: the tutor learns how to see the child

{len(_patches)} accepted patch(es) written by the critic agents, {_train}, {_held_line}.
Agent spend on accepted versions: ${_spent:.2f}. Running version `{(snap.get('best_version') or 'none')[:8]}`.

<small>Snapshot {snap.get('generated_at', '?')} from `{snap_source}`. Held-out, blind arm, kNN and the detection
ceiling appear on the chart as soon as they are measured.</small>
""")
    return


@app.cell
def _(informed, plt, snap):
    def _metric(m):
        return None if not m else m.get("exact_match")

    _xs = list(range(len(informed)))
    _labels = [v["tag"] for v in informed]
    plt.rcParams.update({"font.size": 13, "axes.titlesize": 17, "axes.labelsize": 13, "xtick.labelsize": 14,
                         "ytick.labelsize": 12, "legend.fontsize": 12})
    _fig, _ax = plt.subplots(figsize=(11, 4.6))

    def _series(key, color, style, width, label, band_alpha):
        _pts = [(x, _metric(v.get(key)), (v.get(key) or {}).get("exact_match_ci95")) for x, v in zip(_xs, informed)]
        _pts = [p for p in _pts if p[1] is not None]
        if not _pts:
            return
        _band = [p for p in _pts if p[2]]
        if _band:
            _ax.fill_between([p[0] for p in _band], [p[2][0] for p in _band], [p[2][1] for p in _band],
                             color=color, alpha=band_alpha, linewidth=0)
        _ax.plot([p[0] for p in _pts], [p[1] for p in _pts], color=color, linestyle=style, linewidth=width,
                 marker="o", label=label)
        for _x, _y, _ in _pts:
            _ax.annotate(f"{_y:.3f}", (_x, _y), textcoords="offset points", xytext=(0, 9), ha="center",
                         fontsize=12, color=color)

    _series("train", "#6b7280", "--", 2, "train (the data the critic sees)", 0.15)
    _series("heldout", "#16a34a", "-", 3, "held-out (other people's hands)", 0.18)
    def _basis(v):
        return ((v.get("data") or {}).get("sha"), (v.get("train") or {}).get("scorers_sha256"))

    # The gain is written only between two train scores with the same data fingerprint and the same scorers
    # (a data refresh or a train split starts a new basis), never simply first against last.
    _trains = [(_x, v) for _x, v in zip(_xs, informed) if _metric(v.get("train")) is not None]
    if _trains:
        _lx, _lv = _trains[-1]
        _base = next(((_x, v) for _x, v in _trains if _basis(v)[0] and _basis(v) == _basis(_lv)), None)
        if _base and _base[0] != _lx:
            _gain = (_metric(_lv.get("train")) - _metric(_base[1].get("train"))) * 100
            _ax.annotate(f"{_gain:+.1f} train points since {_base[1]['tag']}"
                         + ("" if _basis(_lv)[1] else "\n(same data, scorer version not recorded)"),
                         (_lx, _metric(_lv.get("train"))), textcoords="offset points", xytext=(-12, 34), ha="right",
                         fontsize=14, fontweight="bold", color="#111827")

    _blind = snap.get("blind") or []
    if _blind:
        _index = {}
        for _x, _v in zip(_xs, informed):
            if _v.get("kind") != "data_refresh":
                _index.setdefault(_v.get("version"), _x)
        _pts = [(_index[b.get("version")], _metric(b.get("heldout")) or _metric(b.get("train")))
                for b in _blind if b.get("version") in _index]
        _pts = [p for p in _pts if p[1] is not None]
        if _pts:
            _ax.plot([p[0] for p in _pts], [p[1] for p in _pts], color="#9aa3ad", linewidth=2, marker="s",
                     label="blind critic (no failure data)")

    _knn = _metric(snap.get("knn_heldout"))
    if _knn is not None:
        _ax.axhline(_knn, color="#3b82f6", linestyle=":", linewidth=2, label=f"kNN on landmarks, held-out ({_knn:.2f})")
    _ceiling = (snap.get("detection_ceiling") or {}).get("both_hands_contact_frames")
    if _ceiling is not None:
        _ax.axhline(_ceiling, color="#111827", linewidth=1.2, label=f"detection ceiling ({_ceiling:.2f})")

    for _x, _v in zip(_xs, informed):
        if _v.get("kind") == "data_refresh":
            _ax.axvline(_x, color="#f5a623", linestyle=":", linewidth=1.5)
            _ax.annotate(f"new captures\n{(_v.get('data') or {}).get('n_samples', '?')} samples", (_x, 0.05),
                         ha="center", fontsize=11, color="#b45309")

    _ax.set_xticks(_xs, _labels)
    _ax.set_xlim(-0.5, max(len(_xs) - 0.5, 0.5))
    _ax.set_ylim(0, 1)
    _ax.set_ylabel("exact_match, per hold")
    _ax.set_title("Perception accuracy, version by version", loc="left", fontsize=13)
    _ax.grid(axis="y", alpha=0.25)
    for _side in ("top", "right"):
        _ax.spines[_side].set_visible(False)
    _ax.legend(loc="upper left", frameon=False, fontsize=12)
    _fig.tight_layout()
    _fig
    return


@app.cell
def _(informed, mo):
    def _fmt(m):
        return "" if not m or m.get("exact_match") is None else f"{m['exact_match']:.3f}"

    _rows = [{
        "version": v["tag"],
        "kind": v.get("kind") or "",
        "train": _fmt(v.get("train")),
        "held-out": _fmt(v.get("heldout")) or "not measured",
        "samples": (v.get("data") or {}).get("n_samples", ""),
        "gate": v.get("gate") or "",
        "patch": v.get("patch") or "",
        "USD": "" if v.get("spent_usd") is None else f"{v['spent_usd']:.2f}",
    } for v in informed]
    mo.vstack([mo.md("## Every version the loop kept"), mo.ui.table(_rows, selection=None)])
    return


@app.cell
def _(informed, mo):
    pick = mo.ui.slider(start=0, stop=max(len(informed) - 1, 1), value=max(len(informed) - 1, 0),
                        label="Version to inspect", show_value=True)
    pick
    return (pick,)


@app.cell
def _(informed, mo, pick, plt):
    if not informed:
        _out = mo.md("No version in this snapshot yet.")
    else:
        _i = min(pick.value, len(informed) - 1)
        _v = informed[_i]
        _prev = informed[_i - 1] if _i > 0 else None
        _now = (_v.get("train") or {}).get("per_class") or {}
        _before = ((_prev or {}).get("train") or {}).get("per_class") or {}
        _changed = [c for c in _now if _before and abs((_now[c] or 0) - (_before.get(c) or 0)) > 1e-9]
        _classes = sorted(_changed or list(_now), key=lambda c: ((_now[c] or 0) - (_before.get(c) or 0), c))
        _unchanged = len(_now) - len(_changed) if _changed else 0
        _fig, _ax = plt.subplots(figsize=(7, max(2.6, 0.42 * len(_classes) + 1)))
        _ys = list(range(len(_classes)))
        if _before:
            _ax.barh([y + 0.2 for y in _ys], [_before.get(c) or 0 for c in _classes], height=0.4, color="#cbd5e1",
                     label=_prev["tag"])
        _ax.barh([y - 0.2 for y in _ys] if _before else _ys, [_now[c] or 0 for c in _classes], height=0.4,
                 color="#16a34a", label=_v["tag"])
        _ax.set_yticks(_ys, _classes, fontsize=12)
        _ax.set_xlim(0, 1)
        _ax.set_title(f"Classes that changed ({_unchanged} unchanged), most improved at the top" if _changed
                      else "Accuracy by class", loc="left", fontsize=13)
        _ax.legend(frameon=False, fontsize=12, loc="lower right")
        for _side in ("top", "right"):
            _ax.spines[_side].set_visible(False)
        _fig.tight_layout()

        def _cond(v):
            return (v.get("train") or {}).get("per_slice") or {}
        _conditions = [{"condition": k,
                        _v["tag"]: f"{s['exact_match']:.3f}",
                        **({_prev["tag"]: f"{_cond(_prev)[k]['exact_match']:.3f}"} if _prev and k in _cond(_prev) else {}),
                        "samples": s["n_samples"]} for k, s in _cond(_v).items() if s.get("exact_match") is not None]
        _story = "\n\n".join(f"**{label}** {_v[key]}" for key, label in (
            ("diagnosis", "Diagnostic agent:"), ("patch_hypothesis", "Patch agent measured:"), ("patch", "Patch:"),
            ("expected", "Expected:"), ("gate", "Metric gate:")) if _v.get(key))
        _out = mo.vstack([
            mo.md(f"## {_v['tag']} ({_v.get('kind') or 'version'})"),
            mo.md(_story or "The baseline: nothing changed yet."),
            mo.hstack([mo.as_html(_fig),
                       mo.vstack([mo.md("**By capture condition**"),
                                  mo.ui.table(_conditions, selection=None) if _conditions else mo.md("not recorded")])],
                      widths=[3, 2]),
            mo.md(f"**Diff the critic wrote**\n\n```diff\n{_v['diff']}\n```") if _v.get("diff") else mo.md(""),
        ])
    _out
    return


@app.cell
def _(mo, snap):
    _rejected = [{"arm": r.get("arm"), "when": (r.get("ts") or "")[:16], "why the gate said no": r.get("reason"),
                  "patch": r.get("patch")} for r in snap.get("rejected") or []]
    mo.vstack([
        mo.md("## Patches the metric gate turned down"),
        mo.ui.table(_rejected, selection=None) if _rejected else mo.md(
            "None in this snapshot. Every rejection, including the guard agent's, is traced in Weave "
            "(project `tenfold`, op `critic.rejected_patch`)."),
    ])
    return


@app.cell
def _(mo):
    mo.Html(
        '<div style="background:#0B0D10;color:#FFFFFF;padding:84px 48px;border-radius:18px;text-align:center;'
        'font:800 64px/1.12 Nunito, \'Avenir Next Rounded\', system-ui, sans-serif">'
        "The child learns multiplication.<br>The tutor learns how to see the child.</div>"
    )
    return


if __name__ == "__main__":
    app.run()
