/*
 * Tenfold, the tablet app. One page, several views: welcome, home, the learn
 * showcase, the practice path, the start check, the camera lesson, finish, profile.
 *
 * Structure and progress come from levels.js and nothing else. The only reward is
 * XP and Tally's ten levels: XP lives in the learner profile (tenfold.learner), the
 * same record lesson/scheduler.py round trips, so one profile carries everything.
 * The camera lesson is driven by app/server.py over the websocket, scoped to a
 * node's pairs by the scheduler; this file renders, counts and stores. Tally is
 * drawn from the renders in art/ through tally.js, the node icons from icons.svg.
 */
(function () {
  "use strict";
  const L = window.TenfoldLevels;
  const KEY = "tenfold.levels.v1";
  const KEY_NAME = "tenfold.name";
  const KEY_LEARNER = "tenfold.learner";
  const KEY_MUTED = "tenfold.muted";
  const KEY_CHILD = "tenfold.child";          // who is playing on this computer right now
  const KEY_CHILDREN = "tenfold.children";    // the roster, one entry per child ever named here
  const params = new URLSearchParams(location.search);
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.prototype.slice.call((root || document).querySelectorAll(sel));
  const NS = "http://www.w3.org/2000/svg";

  // ---------- what the server is allowed to slow down ----------
  // The page validates on the event. The waits it knows about are these parameters, all
  // zero unless a state message sets them, so a server that says nothing about them
  // means no wait at all: the step turns and the answer goes the moment each is true.
  // The gate's two are read from lesson/tutor_params.json as well, so the gate paces
  // itself on the policy file whatever the wire carries.
  const PARAMS = { check_step_min_ms: 0, answer_first_number_ms: 0,
    gate_step_pause_ms: 0, gate_ready_button_s: 0 };
  // One event, one thing on screen. The lesson's answer field is the export's pill and
  // nothing else: the digits typed and the number heard are shown in the pill itself,
  // the big number beside it and the "heard:" line above it are gone. That line was a
  // developer's readout, so it comes back only with ?debug=1 in the url.
  const DEBUG = params.get("debug") === "1";
  const HEARD_MS = 800;           // how long the pill holds the number before the tutor answers
  let heardTimer = null;
  function readParams(m) {
    const from = (m && m.params) || m || {};
    Object.keys(PARAMS).forEach((name) => {
      const value = Number(from[name]);
      if (Number.isFinite(value) && value >= 0) PARAMS[name] = value;
    });
  }
  // The one line allowed to cut another is the acknowledgement, and the server is what
  // marks it. tutor_line_cuts is the server's own word for it, so it is the answer
  // whenever the message carries it, true or false alike. The names below are what the
  // field was called while it was being added, and are read only when the server's word
  // is absent; a message with none of them cuts nothing, which is the rhythm the
  // dialogue has with no tutor behind it.
  const CUT_FIELD = "tutor_line_cuts";
  const INTERRUPT_FIELDS = ["interrupt", "can_interrupt", "interrupts", "interruptible",
    "tally_interrupt", "tally_interrupts", "barge_in"];
  function interrupts(m) {
    if (m && Object.prototype.hasOwnProperty.call(m, CUT_FIELD)) return Boolean(m[CUT_FIELD]);
    return INTERRUPT_FIELDS.some((name) => Boolean(m && m[name]));
  }

  // ---------- storage ----------
  function read(key, fallback) {
    try { const raw = localStorage.getItem(key); return raw ? JSON.parse(raw) : fallback; }
    catch (e) { return fallback; }
  }
  function write(key, value) {
    try { localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value)); } catch (e) { /* keep going in memory */ }
  }
  function readText(key) { try { return localStorage.getItem(key) || ""; } catch (e) { return ""; } }
  // ---------- one record per child ----------
  // Several children share one tablet and they are separate learners. A child is the
  // first name typed on the welcome screen, trimmed and lowercased, and everything
  // below (profile, XP, the tutor's factors, the path) lives in that child's own slot.
  // Switching learner moves the pointer and nothing else: no other child's record is
  // ever read, written or erased.
  function idOf(value) { return String(value || "").trim().replace(/\s+/g, " ").toLowerCase(); }
  function tidy(value) { return String(value || "").trim().replace(/\s+/g, " "); }
  function child() { return readText(KEY_CHILD); }
  // before any name is typed there is one anonymous slot, which is the plain key
  function slot(key) { const who = child(); return who ? `${key}.${who}` : key; }
  function load() { return Object.assign(L.emptyProgress(), read(slot(KEY), {})); }
  function save() { write(slot(KEY), progress); }
  // the learner record is the profile: the scheduler's memory plus display_name and xp
  function learner() { return read(slot(KEY_LEARNER), null); }
  function saveLearner(state) { write(slot(KEY_LEARNER), state); }
  function name() {
    const me = learner();
    if (me && me.display_name) return String(me.display_name);
    return readText(slot(KEY_NAME));
  }
  function setName(value) {
    const me = learner() || {};
    me.display_name = value;
    saveLearner(me);
    write(slot(KEY_NAME), value);
    write(KEY_NAME, value);   // the name of whoever is playing, for anything that reads it plain
  }
  function roster() { const list = read(KEY_CHILDREN, []); return Array.isArray(list) ? list : []; }
  function remember(key, display) {
    const list = roster().filter((c) => c && c.key !== key);
    list.push({ key, name: display });
    write(KEY_CHILDREN, list);
  }
  function setChild(value) {
    const key = idOf(value);
    if (!key) return false;
    const firstEver = roster().length === 0;
    const known = readText(`${KEY_LEARNER}.${key}`) !== "";
    write(KEY_CHILD, key);
    // whatever was played before any name existed belongs to the first child named,
    // and to no one after that: a second child always starts from zero
    if (firstEver && !known) {
      const anon = read(KEY_LEARNER, null), anonProgress = read(KEY, null);
      if (anon) saveLearner(anon);
      if (anonProgress) write(slot(KEY), anonProgress);
    }
    remember(key, tidy(value));
    setName(tidy(value));
    progress = load();
    unitAt = null;
    return true;
  }
  function xp() { const me = learner(); return me ? Math.max(0, Math.floor(Number(me.xp) || 0)) : 0; }
  function addXp(gain) {
    const me = learner() || {};
    me.xp = xp() + Math.max(0, Math.floor(gain || 0));
    saveLearner(me);
    return me.xp;
  }

  let progress = load();
  let unitAt = null;
  let muted = read(KEY_MUTED, false) === true;
  function setMuted(on) {
    muted = Boolean(on);
    write(KEY_MUTED, muted);
    cutSpeech();    // a switch flicked mid sentence is a break in the dialogue, both ways
    markMute();
  }
  function markMute() {
    const b = $("#mute");
    if (!b) return;
    b.classList.toggle("is-muted", muted);
    b.setAttribute("aria-pressed", String(muted));
    b.setAttribute("aria-label", muted ? "Unmute Tally" : "Mute Tally");
  }
  let lesson = null;
  let justUnlocked = null;
  let checkRun = null;
  let demoSeeded = false;

  // ---------- icons and Tally ----------
  const use = (id, box) => `<svg viewBox="${box || "0 0 48 48"}" aria-hidden="true"><use href="#${id}"></use></svg>`;
  function decorate(root) {
    const scope = root || document;
    const info = L.levelInfo(xp());
    $$("[data-outfit]", scope).forEach((el) => el.setAttribute("data-accessories", info.accessories));
    if (window.Icons) window.Icons.expand(scope);
    if (window.Tally) window.Tally.mount(scope);
    renderLevelBoxes(scope, info);
  }
  function levelLabel(info) { return info.next ? `${info.inLevel} / ${info.span} XP` : `${info.xp} XP`; }
  // the level box of the practice top bar, the one place a level is shown outside the profile
  function renderLevelBoxes(scope, info) {
    const box = $("#practice");
    if (!box || (scope && scope !== document && scope !== box)) return;
    $("#lvl-name", box).textContent = info.name;
    $("#lvl-xp", box).textContent = levelLabel(info);
    $("#lvl-fill", box).style.width = `${info.percent}%`;
  }
  function setTally(host, expr) {
    if (!host) return;
    if (host.getAttribute("data-tally") === expr) return;
    if (window.Tally) window.Tally.set(host, expr); else host.setAttribute("data-tally", expr);
  }

  // ---------- router ----------
  const VIEWS = ["welcome", "home", "learn", "practice", "check", "profile"];
  let view = "welcome";
  // The one door every view change goes through, the six routed views, the lesson and
  // the finish card alike. The screen that is leaving stops talking here: the queue is
  // emptied, the utterance in flight is cancelled, the tts pair around it is closed and
  // the memory of the line just said is cleared, before the new screen queues anything.
  // A line asked for by a screen that is no longer on top is never spoken late, which is
  // stronger than the staleness test and does not depend on the caller remembering.
  function enterView(name) {
    cutSpeech();
    // a screen the child opens says its lines again: the memory that keeps the map's
    // line from being repeated on every render starts fresh here
    speech.lastLine = null;
    view = name;
    document.body.dataset.view = name;
  }
  function show(name) {
    // the Back button is a way out of a running lesson too: without this the node
    // keeps running on the server and Tally talks over the next screen
    if (lesson) leaveLesson();
    enterView(name);
    VIEWS.forEach((v) => { const el = document.getElementById(v); if (el) el.hidden = v !== name; });
    $("#lesson").hidden = true;
    $("#finish").hidden = true;
    if (name !== "check") stopCheck();
    if (name === "welcome") renderWelcome();
    if (name === "home") renderHome();
    if (name === "learn") renderShowcase();
    if (name === "practice") renderCourse();
    if (name === "profile") renderProfile();
    if (name === "check") startCheck();
  }
  function route() {
    const hash = (location.hash || "").replace("#", "");
    if (VIEWS.includes(hash)) { show(hash); return; }
    show(name() ? "home" : "welcome");
  }
  function go(name) {
    if (location.hash !== `#${name}`) location.hash = name; else show(name);
  }

  // ---------- welcome ----------
  const welcome = $("#welcome");
  $("#form", welcome).addEventListener("submit", (e) => {
    e.preventDefault();
    if (!setChild($("#name", welcome).value)) return;
    go("home");
  });
  // the children this computer already knows: one tap and no spelling, and the
  // name is never put back on screen as markup
  function renderWelcome() {
    const field = $("#name", welcome);
    field.value = "";
    setTimeout(() => { try { field.focus(); } catch (e) { /* no focus, no harm */ } }, 0);
    const box = $("#known", welcome);
    const list = roster();
    box.hidden = list.length === 0;
    box.replaceChildren();
    list.forEach((c) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "known-btn";
      button.dataset.action = "child";
      button.dataset.child = c.key;
      button.textContent = c.name || c.key;
      box.appendChild(button);
    });
  }

  // ---------- home ----------
  function renderHome() {
    const who = name();
    $("#greeting").textContent = who ? line("home_greeting", { who }) : line("home_greeting_plain");
    $("#switch-child").textContent = who ? `Not ${who}?` : "Switch child";
    decorate($("#home"));
  }

  // ---------- the path screen, drawn twice ----------
  // The export wrote one path screen. The practice view is that screen wired to this
  // child's own progress; the learn view is the same markup filled with the export's
  // own showcase units and no way in, which is what the learn view was before.
  const TONES = [["var(--green)", "var(--green-lip)"], ["var(--blue)", "var(--blue-lip)"], ["var(--purple)", "#a95fd8"], ["var(--yellow)", "var(--yellow-lip)"]];
  // the export's winding path, top left down to bottom right, no sharp reversals. The
  // nodes are spread evenly down it, so a unit of four fills the panel a unit of six does.
  const SPOT_X = [22, 43, 64, 42, 23, 46];
  function spotsFor(count) {
    const top = 20, bottom = 86;
    return Array.from({ length: count }, (unused, i) => [
      SPOT_X[i % SPOT_X.length],
      count > 1 ? top + i * (bottom - top) / (count - 1) : (top + bottom) / 2,
    ]);
  }
  function iconFor(kind, state) {
    if (kind === "chest") return state === "locked" ? "icon-chest-locked" : "icon-chest";
    if (kind === "boss") return state === "done" ? "icon-boss-done" : state === "locked" ? "icon-boss-locked" : "icon-boss";
    return state === "done" ? "icon-lesson-done" : state === "locked" ? "icon-lesson-locked" : "icon-lesson";
  }
  // the path furniture is rendered art, one image per node state. A chest has no
  // render in the export, so it keeps the drawn icon of icons.svg.
  function nodeArt(kind, state) {
    if (state === "locked") return "n-lock.png";
    if (state === "done") return "n-done.png";
    return kind === "boss" ? "n-boss.png" : "start.png";
  }
  function nodeFace(kind, state) {
    return kind === "chest"
      ? `<span class="chest">${use(iconFor(kind, state), "0 0 88 80")}</span>`
      : `<span style="background-image:url(art/${nodeArt(kind, state)})"></span>`;
  }
  // one row of unit tiles, the export's three renders, the one on screen lit
  function tileRow(count, at, action) {
    return Array.from({ length: count }, (unused, i) =>
      `<button type="button" class="${i === at ? "on" : ""}" data-action="${action}" data-u="${i}" aria-label="Unit ${i + 1}">` +
      `<img src="art/tile-${i + 1}-${i === at ? "on" : "off"}.png" alt=""></button>`).join("");
  }
  // nodes: { kind, state, id, label, action }. An entry with no action is not a way in,
  // which is the whole difference between the learn showcase and the practice path.
  function pathHtml(nodes) {
    const pts = spotsFor(nodes.length);
    const d = pts.map(([x, y], i) => `${i ? "L" : "M"} ${x} ${y}`).join(" ");
    let html = `<svg class="track" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><path d="${d}"></path></svg>`;
    nodes.forEach((n, i) => {
      const [x, y] = pts[i];
      const cls = `node ${n.state === "locked" ? "locked" : n.state === "done" ? "done" : "current"}`;
      const way = n.action ? ` data-action="${n.action}" data-node="${n.id}"` : " disabled";
      html += `<button class="${cls}" type="button"${way} data-status="${n.state}" aria-label="${n.label}" ` +
              `style="left:${x}%;top:${y}%">${nodeFace(n.kind, n.state)}</button>`;
    });
    return html;
  }
  function tone(root, card, i) {
    const [c, lip] = TONES[i % TONES.length];
    [root, card].forEach((el) => { if (el) { el.style.setProperty("--unit", c); el.style.setProperty("--unit-lip", lip); } });
  }

  // ---------- learn: the export's own showcase units, read only ----------
  const SHOWCASE = [
    { kicker: "Unit 1", name: "Meet your fingers", desc: "Numbers on every fingertip", done: 2,
      say: "showcase_unit_1", mood: "happy",
      nodes: [["lesson", "done"], ["lesson", "done"], ["lesson", "current"], ["chest", "locked"], ["lesson", "locked"], ["boss", "locked"]] },
    { kicker: "Unit 2", name: "Count the tens", desc: "Fingers down make tens", done: 0,
      say: "showcase_unit_2", mood: "ready",
      nodes: [["lesson", "locked"], ["lesson", "locked"], ["lesson", "locked"], ["chest", "locked"], ["boss", "locked"]] },
    { kicker: "Unit 3", name: "Big numbers", desc: "Nine times and ten times", done: 0,
      say: "showcase_unit_3", mood: "thinking",
      nodes: [["lesson", "locked"], ["lesson", "locked"], ["lesson", "locked"], ["boss", "locked"]] },
  ];
  const SHOWCASE_SHELL = `
<div class="frame">
  <div class="top">
    <a class="back" href="#home" aria-label="Back"><img src="art/btn-back.png" alt=""></a>
    <div><p class="sub">Learn</p><h1 id="lrn-crumb"></h1></div>
  </div>
  <div class="body">
    <section class="unitcard" id="lrn-card">
      <div class="head"><div class="kicker" id="lrn-kicker"></div><h2 id="lrn-name"></h2><p id="lrn-desc"></p></div>
      <div class="mid">
        <div class="metric"><span>Lessons done</span><b id="lrn-count"></b></div>
        <div class="bar2"><i id="lrn-bar" style="width:0%"></i></div>
        <div class="tallyspot"><div class="tally-art" id="lrn-tally" data-tally="happy" data-outfit aria-hidden="true"></div><p class="line" id="lrn-say"></p></div>
      </div>
      <div class="units" id="lrn-switch"></div>
    </section>
    <section class="pathwrap" id="lrn-path"></section>
  </div>
</div>`;
  let showAt = 0;
  function renderShowcase() {
    const root = $("#learn");
    if (!$("#lrn-card", root)) root.innerHTML = SHOWCASE_SHELL;
    const u = SHOWCASE[showAt % SHOWCASE.length];
    tone($(".frame", root), $("#lrn-card", root), showAt);
    $("#lrn-crumb", root).textContent = u.name;
    $("#lrn-kicker", root).textContent = u.kicker;
    $("#lrn-name", root).textContent = u.name;
    $("#lrn-desc", root).textContent = u.desc;
    const scored = u.nodes.filter(([kind]) => kind !== "chest").length;
    $("#lrn-count", root).textContent = `${u.done} of ${scored}`;
    $("#lrn-bar", root).style.width = `${scored ? (u.done / scored) * 100 : 0}%`;
    $("#lrn-say", root).textContent = line(u.say);
    setTally($("#lrn-tally", root), u.mood);
    $("#lrn-switch", root).innerHTML = tileRow(SHOWCASE.length, showAt, "showcase");
    $("#lrn-path", root).innerHTML = pathHtml(u.nodes.map(([kind, state], i) => ({ kind, state, id: "", label: `${kind} ${i + 1}`, action: null })));
    decorate(root);
  }

  // ---------- practice: the child's own path ----------
  function unitIndex() {
    if (unitAt !== null) return unitAt;
    const cur = L.currentNode(progress);
    return cur ? cur.unitIndex : L.UNITS.length - 1;
  }
  function renderCourse() {
    const ui = unitIndex();
    const unit = L.UNITS[ui];
    const root = $("#practice");
    tone($(".frame", root), $("#card", root), ui);
    const nodes = L.allNodes().filter((n) => n.unitId === unit.id);
    const scored = nodes.filter((n) => n.kind !== "chest");
    const done = scored.filter((n) => (progress.stars[n.id] || 0) >= 1).length;
    const cur = L.currentNode(progress);
    const here = cur && cur.unitId === unit.id;
    $("#crumb", root).textContent = unit.title;
    $("#u-kicker", root).textContent = `Unit ${ui + 1}`;
    $("#u-name", root).textContent = unit.title;
    $("#u-desc", root).textContent = unit.subtitle;
    $("#u-count", root).textContent = `${done} of ${scored.length}`;
    $("#u-bar", root).style.width = `${scored.length ? (done / scored.length) * 100 : 0}%`;
    const said = here
      ? (cur.kind === "chest" ? line("path_chest") : cur.kind === "boss" ? line("path_boss") : line("path_next", { title: cur.title }))
      : done === scored.length ? line("path_all_done") : line("path_locked");
    $("#u-say", root).textContent = said;
    if (said !== speech.lastLine) { speech.lastLine = said; say(said, $("#u-say", root), $("#u-tally", root)); }
    setTally($("#u-tally", root), here ? "ready" : "happy");
    $("#switch", root).innerHTML = tileRow(L.UNITS.length, ui, "unit");
    $("#path", root).innerHTML = pathHtml(nodes.map((n) => {
      const state = L.nodeState(progress, n.id);
      return { kind: n.kind, state, id: n.id, label: n.title,
               action: state !== "current" ? "node" : n.kind === "chest" ? "chest" : "start" };
    }));
    decorate(root);
  }
  // ---------- the two files the page reads ----------
  // A line Tally says lives in lesson/tally_lines.json and a policy timing in
  // lesson/tutor_params.json, and in neither case anywhere else: the page reads them
  // rather than carrying a copy. They are asked for once, at boot, and the page works
  // without either. A line it could not read is not spoken, a timing it could not read
  // falls back to the value written next to its key here.
  const files = { lines: null, params: null };
  const asked = {};
  function loadFile(key, url) {
    if (asked[key]) return asked[key];
    asked[key] = fetch(url, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : {}))
      .catch(() => ({}))
      .then((data) => { files[key] = (data && typeof data === "object") ? data : {}; });
    return asked[key];
  }
  function loadFiles() {
    return Promise.all([loadFile("lines", "tally_lines.json"), loadFile("params", "tutor_params.json")]);
  }
  function tallyLine(key) { return (files.lines && files.lines[key]) || ""; }
  // One line of the file, with its placeholders filled the way the tutor fills its
  // own: {who}, {title}, {need}. A key the file does not carry is an empty line, which
  // is said by nobody and written nowhere, and never a sentence invented here.
  function line(key, fill) {
    const text = tallyLine(key);
    if (!text || !fill) return text;
    return text.replace(/\{(\w+)\}/g, (whole, name) => (name in fill ? String(fill[name]) : whole));
  }
  // run now when the lines are in, on the same turn as the event that asked for them,
  // otherwise as soon as they are
  function withLine(key, fn) {
    if (files.lines) { fn(tallyLine(key)); return; }
    loadFile("lines", "tally_lines.json").then(() => fn(tallyLine(key)));
  }
  // a timing from lesson/tutor_params.json, held inside the bounds that file carries
  // for it: the page never runs on a value the policy file itself calls out of range
  function timing(key, fallback) {
    const values = (files.params && files.params.global) || null;
    const raw = values ? Number(values[key]) : NaN;
    if (!isFinite(raw)) return fallback;
    const bounds = (files.params && files.params.bounds && files.params.bounds[key]) || null;
    if (!Array.isArray(bounds) || bounds.length !== 2) return raw;
    return Math.min(Math.max(raw, Number(bounds[0])), Number(bounds[1]));
  }

  // ---------- the ready gate ----------
  // It runs at the start of every activity, and it is short: show both hands, say the
  // word, in. Nothing in it is an exercise, nothing in it is scored, and there is no
  // question. The very first gate a child ever sees carries one more step, the finger
  // pose, which proves the camera can read a pose; after that it never comes back.
  //
  // The export sheet draws the steps as frames side by side, f1, f2 and f3; the screen
  // has one frame and the step it is on is its data-step, which is the export frame that
  // step belongs to. Every class the export puts on a frame for the state being shown
  // goes on that one frame here at the moment it is true: detected, banner, matched,
  // check, heard, mapin. The export's result state belonged to a step that asked for a
  // number; no step asks for one, so the frame never wears it.
  const KEY_POSE = "tenfold.posestep";        // per child: the pose step has been passed
  const KEY_GATE_OK = "tenfold.gate.ok";      // this session: camera and microphone both worked
  // how long the gate waits for a microphone that is not bringing anything back before
  // it puts a Ready button up instead. lesson/tutor_params.json owns the value.
  const READY_BUTTON_KEY = "gate_ready_button_s";
  const READY_BUTTON_S = 6;
  // How long a step of the gate stands before the next one may open, so a child sees
  // each one rather than a screen that flickers past. lesson/tutor_params.json owns the
  // value; with no value there the gate turns on the event and waits for nothing, which
  // is what the page does today.
  const GATE_PAUSE_KEY = "gate_step_pause_ms";
  const GATE_PAUSE_MS = 0;
  // the key of the one line the gate asks for. lesson/tally_lines.json is written by
  // other hands: until the key is in it, the step opens without a line rather than with
  // a sentence invented here.
  const READY_LINE_KEY = "gate_ready";
  // a timing of the gate: what the wire said if it said anything, then what
  // lesson/tutor_params.json says, then the constant written beside its key here
  function gateTiming(key, fallback) {
    const wire = Number(PARAMS[key]);
    if (isFinite(wire) && wire > 0) return wire;
    return timing(key, fallback);
  }
  function poseMet() { return readText(slot(KEY_POSE)) === "1"; }
  function markPoseMet() { write(slot(KEY_POSE), "1"); }
  // Proof, for this session only, that the camera and the microphone both work: the
  // hands were seen and the word came back in one gate. Only that lets a later gate be
  // skipped, so a child can never skip past a screen that has never worked for them.
  function gateProved() { try { return sessionStorage.getItem(KEY_GATE_OK) === "1"; } catch (e) { return false; } }
  function markGateProved() { try { sessionStorage.setItem(KEY_GATE_OK, "1"); } catch (e) { /* fine */ } }
  // the first gate for a child has the pose step in it, every later one has two steps
  function gateSteps() { return poseMet() ? ["hands", "ready"] : ["hands", "pose", "ready"]; }
  const STEP_FRAME = { hands: "1", pose: "2", ready: "3" };
  function stepNow(run) { return run ? run.steps[run.at] : null; }
  function atReady() { return Boolean(checkRun && !checkRun.done && stepNow(checkRun) === "ready"); }
  // what the child asked for, waiting behind the gate
  let gateAfter = null;
  function runGate(after) { gateAfter = after || null; go("check"); }
  function checkFrame() { return $("#check .frame"); }
  function startCheck() {
    if (checkRun) return;
    const root = $("#check");
    const frame = checkFrame();
    const run = { steps: gateSteps(), at: 0, stepAt: Date.now(), turning: false, lit: 0,
                  timers: [], done: false, ended: false, listening: false, button: false,
                  said: null, sawHands: false, heardWord: false };
    checkRun = run;
    frame.className = "frame";
    frame.dataset.step = STEP_FRAME.hands;
    checkSay(line("show_both_hands"), { kind: "instruction", key: "check_show" });
    // the last step of every gate is a microphone test: the recogniser has to be awake
    // for it, armed here the way the lesson arms it
    armVoice();
    setTally($("#check-tally"), "ready");
    setStepDots(run);
    videoOn($("#check-cam .practice-video"));
    // The node carries no exercise: the gate asks the server for the perception stream
    // and for nothing else. steps says which steps are running, so the server knows
    // whether a pose verdict is wanted at all, and the pair is the pose that gate step
    // asks for, which is the only thing a verdict could be read against. A gate without
    // that step names no pair, because nothing in it is about any pair of hands.
    const pairs = run.steps.indexOf("pose") === -1 ? [] : [[6, 6]];
    connect(() => sendLesson({ type: "start_node", tab: TAB, state: learner(),
      node: { id: "check", kind: "check", steps: run.steps, pairs: pairs } }));
    decorate(root);
  }
  function checkSay(text, opts) { return say(text, $("#check-say"), $("#check-tally"), opts); }
  function stopCheck() {
    if (!checkRun) return;
    checkRun.timers.forEach(clearTimeout);
    checkRun = null;
    gateAfter = null;
    resetPill();
    cutSpeech();
    gateVoice(false);
    videoOff($("#check-cam .practice-video"));
    link.start = null; link.waiting = [];
    sendLesson({ type: "quit" });
  }
  // The export's tile art and spacing, one tile per step of this gate: two in the usual
  // gate, three in the one that carries the pose step. There is no spare tile standing
  // there greyed out, so the row says how long the gate is.
  function setStepDots(run) {
    const steps = run ? run.steps.length : 3;
    const at = run ? run.at : 0;
    $$("#check .stepnum img").forEach((tile, i) => {
      tile.style.display = i < steps ? "" : "none";
      if (i >= steps) { tile.alt = ""; return; }
      const on = i === at;
      tile.src = `art/tile-${i + 1}-${on ? "on" : "off"}.png`;
      tile.alt = on ? `Step ${i + 1}` : "";
    });
  }
  function later(ms, fn) { if (checkRun) checkRun.timers.push(setTimeout(fn, ms)); }
  // A step turns the instant its condition is true. check_step_min_ms is the only thing
  // that can hold one back, and it is zero by default: the turn then runs here and now,
  // with no timer between the child being right and the screen saying so. The number
  // sweep keeps its own timers, it is decoration and not the step.
  function turnStep(run, fn) {
    const least = Math.max(PARAMS.check_step_min_ms, gateTiming(GATE_PAUSE_KEY, GATE_PAUSE_MS));
    const wait = least - (Date.now() - (run.stepAt || 0));
    const turn = () => { if (checkRun !== run) return; run.stepAt = Date.now(); run.turning = false; fn(); };
    if (!(wait > 0)) { turn(); return; }
    run.turning = true;
    later(wait, turn);
  }
  // the 6 and 6 pose is being held. m.pose is the gate's own field; the state names are
  // what the server says while the check node is still an exercise node.
  function posed(m) {
    if (m.pose === true) return true;
    return m.state === "correct_pose" || m.state === "waiting_answer";
  }
  function checkMessage(m) {
    const run = checkRun;
    const frame = checkFrame();
    if (!run || run.done || m.type !== "state") return;
    readParams(m);
    const cuts = interrupts(m);
    // The gate has no exercise, so it draws no exercise: the fingertips and their
    // numbers, and none of the marks a lesson message carries. While the check node
    // still serves an exercise, its green and orange belong to a question the child
    // was never asked, and the gate must not show them.
    drawFingers({ fingers: m.fingers }, $("#check .stage"));
    const hands = new Set((m.fingers || []).map((f) => f.hand)).size;
    const step = stepNow(run);
    if (step === "hands") {
      if (hands === 2 && !frame.classList.contains("detected") && !run.turning) {
        frame.classList.add("detected");
        // the camera has worked in this gate, and the child is past this step the moment
        // both hands are there, whatever Tally is still saying: the acknowledgement rides
        // the event and the next instruction waits its turn in the queue, never a clock
        run.sawHands = true;
        turnStep(run, () => openStep(run, line("gate_hands_ok"), "check_hands_seen", cuts));
      }
      if (hands === 2) $$("#check .practice-overlay [data-number]").forEach((el) => { if (run.lit && Number(el.dataset.number) >= run.lit) el.classList.add("lit"); });
      return;
    }
    // The pose step is a gate step and nothing else. A pose that is not there yet is not
    // an error and not a correction: the gate is simply not passed, so nothing is said
    // about it, nothing is scored and nothing is written anywhere.
    if (step === "pose" && posed(m) && !run.turning) {
      markPoseMet();
      turnStep(run, () => {
        frame.classList.add("matched", "banner", "check");
        $("#check .banner-top").textContent = line("gate_banner");
        setTally($("#check-tally"), "happy");
        openStep(run, line("gate_pose_ok"), "check_pose", cuts);
      });
    }
  }
  // one step is passed, the next one opens: the acknowledgement first, then the
  // instruction, and the screen moves with the instruction rather than ahead of it
  function openStep(run, ack, key, cuts) {
    const frame = checkFrame();
    run.at += 1; run.said = null;
    const step = stepNow(run);
    const cheer = { kind: "ack", key: key, interrupt: cuts,
      onStart: () => { frame.classList.add("check"); setTally($("#check-tally"), "happy"); } };
    if (step === "pose") {
      if (ack) checkSay(ack, cheer);
      // the numbers light up one by one, ten down to six. Decoration on its own timers:
      // the step never waits for it
      [10, 9, 8, 7, 6].forEach((n, i) => later(400 + i * 300, () => {
        $$("#check .practice-overlay [data-number]").forEach((el) => { if (Number(el.dataset.number) <= 10 && Number(el.dataset.number) >= n) el.classList.add("lit"); });
        run.lit = n;
      }));
      // The screen is the step, and it moves whether or not there is a line to say over
      // it: a key the line file has not got yet costs this step its sentence and nothing
      // else. The line moves the screen when there is one, so the two arrive together.
      const showPose = () => {
        frame.dataset.step = STEP_FRAME.pose; frame.classList.remove("check"); setStepDots(run);
        setTally($("#check-tally"), "thinking");
      };
      const asked = line("gate_pose");
      if (!asked) { showPose(); return; }
      checkSay(asked, {
        kind: "instruction", key: "check_touch",
        still: () => checkRun === run && stepNow(run) === "pose" && !run.done,
        onStart: showPose,
        // a line the child has moved past is dropped, and the screen still has to be the
        // step it is on: the drop runs this, the same as the start would have
        after: () => { if (checkRun === run && stepNow(run) === "pose") showPose(); },
      });
      return;
    }
    // The last step: Tally asks for one word, and the recogniser bringing it back is what
    // passes the gate. The line lives in lesson/tally_lines.json and nowhere else, so it
    // is read from there by its key, once the acknowledgement is over. The pill belongs
    // to this step and comes up with the line.
    const open = () => {
      frame.dataset.step = STEP_FRAME.ready; frame.classList.remove("check", "banner"); setStepDots(run);
      $("#check-miclabel").textContent = checkMicLabel();
      setTally($("#check-tally"), "ready");
      run.listening = true;
      armReadyButton(run);
    };
    const start = (line) => {
      if (checkRun !== run || run.done || stepNow(run) !== "ready") return;
      // a line the page could not read is not spoken and not invented: the step opens in
      // silence, and the word, the button and the keyboard pass the gate as they always do
      if (!line) { open(); return; }
      checkSay(line, { kind: "instruction", key: "check_ask",
        still: () => checkRun === run && !run.done, onStart: open });
    };
    const ask = () => withLine(READY_LINE_KEY, start);
    // the acknowledgement hands over to the last step whether it is spoken or dropped,
    // so a gate with no lines at all still reaches the word and the Ready button
    if (ack) checkSay(ack, Object.assign({}, cheer, { after: ask }));
    else ask();
  }
  // A browser with no speech recognition, or a microphone that brings nothing back: the
  // pill becomes a Ready button the child taps, and the tap passes the step. With no
  // recognition at all it is a button from the start; with a microphone that may yet
  // work it waits the policy timing out first, so the word stays the way through.
  function armReadyButton(run) {
    if (!canHear()) { showReadyButton(run); return; }
    const wait = Math.round(gateTiming(READY_BUTTON_KEY, READY_BUTTON_S) * 1000);
    later(wait, () => { if (checkRun === run && !run.done) showReadyButton(run); });
  }
  function showReadyButton(run) {
    if (!run || run.done || run.button) return;
    run.button = true;
    const pill = $("#check-mic");
    if (!pill) return;
    pill.classList.add("is-button");
    pill.setAttribute("role", "button");
    pill.setAttribute("tabindex", "0");
    pill.setAttribute("aria-label", "Ready");
    pill.style.cursor = "pointer";
    $("#check-miclabel").textContent = checkMicLabel();
  }
  function resetPill() {
    const pill = $("#check-mic");
    if (!pill) return;
    pill.classList.remove("is-button");
    pill.removeAttribute("role");
    pill.removeAttribute("tabindex");
    pill.removeAttribute("aria-label");
    pill.style.cursor = "";
  }
  // The gate is through. "heard" says the word came back through the microphone, which
  // with the hands already seen is this session's proof that both work. The page ends
  // the gate itself rather than waiting for a node the server no longer ends on an
  // answer, and a node_end for the check that lands afterwards is a no op.
  function passGate(heard) {
    const run = checkRun;
    if (!run || run.done || stepNow(run) !== "ready") return;
    run.done = true; run.listening = false;
    if (heard) run.heardWord = true;
    gateVoice(false);
    checkFrame().classList.add("heard", "check");
    $("#check-miclabel").textContent = "Ready";
    setTally($("#check-tally"), "happy");
    if (run.sawHands && run.heardWord) markGateProved();
    sendLesson({ type: "ready", node: "check" });
    endGate(true);
  }
  // A tap on Tally skips the gate, but only once the camera and the microphone have both
  // worked in an earlier gate of this session. Before that the tap does nothing at all
  // and Tally says nothing new: there is no proof yet that this screen can be skipped.
  function skipGate() {
    const run = checkRun;
    if (!run || run.done || !gateProved()) return;
    run.done = true; run.listening = false;
    gateVoice(false);
    cutSpeech();
    sendLesson({ type: "ready", node: "check" });
    endGate(false);
  }
  function endGate(spoken) {
    const run = checkRun;
    if (!run || run.ended) return;
    run.ended = true;
    // Straight into the activity, with nothing said over it. The practice path is the
    // one destination with a line of its own: the export slides the path in on it, and
    // behind it is the same path.
    if (!gateAfter && spoken) {
      checkSay(line("gate_path_open"), { kind: "instruction", key: "check_done",
        onStart: () => checkFrame().classList.add("mapin"), after: closeGate });
      return;
    }
    closeGate();
  }
  function closeGate() {
    const run = checkRun;
    if (!run) return;
    run.timers.forEach(clearTimeout);
    const after = gateAfter;
    gateAfter = null;
    // checkRun is left standing: the screen the gate hands over to stops it itself, which
    // is what turns the camera off and closes the node on the server
    if (after) after(); else go("practice");
  }

  // ---------- Tally speaks ----------
  // Tally's voice: the best English voice this browser has, in a fixed preference order,
  // then any en-US voice that reads as female, then any English one. Voices arrive late
  // in Chrome, so the pick is redone on voiceschanged.
  const VOICE_PREFERENCE = ["Ava (Premium)", "Zoe (Premium)", "Samantha", "Karen"];
  const FEMALE_HINT = /ava|zoe|samantha|karen|allison|susan|nicky|zira|aria|jenny|michelle|emma|ana|joanna|kendra|salli|ivy|female|woman/i;
  const tallyVoice = { picked: null, done: false };
  function pickVoice() {
    if (!window.speechSynthesis) return null;
    const voices = window.speechSynthesis.getVoices() || [];
    if (!voices.length) return null;
    const byName = (name) => voices.find((v) => v.name === name) || voices.find((v) => v.name.startsWith(name));
    let found = null;
    for (const name of VOICE_PREFERENCE) { found = byName(name); if (found) break; }
    const us = voices.filter((v) => /^en[-_]US/i.test(v.lang));
    if (!found) found = us.find((v) => FEMALE_HINT.test(v.name)) || null;
    if (!found) found = us[0] || voices.find((v) => /^en/i.test(v.lang)) || null;
    tallyVoice.picked = found; tallyVoice.done = true;
    logVoice(found ? found.name : "browser default");
    return found;
  }
  function logVoice(name) {
    if (tallyVoice.logged) return;
    tallyVoice.logged = true;
    console.log(`Tally voice: ${name}`);
  }
  if (window.speechSynthesis && window.speechSynthesis.addEventListener) {
    window.speechSynthesis.addEventListener("voiceschanged", () => { tallyVoice.done = false; });
  }
  // short sentences, one utterance at a time, all of them inside the same line
  function sentencesOf(text) {
    return String(text || "").replace(/\s+/g, " ").trim().split(/(?<=[.!?])\s+/).map((s) => s.trim()).filter(Boolean);
  }
  // The dialogue is paced: one line at a time, then silence while the child works.
  // A line asked for while Tally is still talking waits for the end of that line plus
  // PAUSE_MS. Nothing overlaps. The single exception is a line the server has marked as
  // able to interrupt, the acknowledgement: it cuts the line in flight and takes its
  // place at once, because the child has already moved on and a late yes is the wait
  // they can feel. Every other line keeps the beat exactly as it was.
  const PAUSE_MS = 1000;          // the beat after an instruction, the rhythm of the dialogue
  const MAX_PENDING = 1;          // one line waiting behind the one being spoken, no deeper
  const READ_MS_PER_WORD = 320;   // how long a line stands when this browser cannot speak it
  const SPEAK_GRACE_MS = 4000;    // an engine that never reports the end must not hold the queue
  const speech = { queue: [], line: null, serial: 0, timer: null, guard: null, waiting: false, lastLine: null };
  function hasVoice() { return Boolean(window.speechSynthesis); }
  // Chrome hands the voice list over asynchronously: the first line waits for
  // voiceschanged (or a short timeout for browsers that never fire it) rather than
  // going out in the wrong voice.
  function voicesReady() {
    return !hasVoice() || tallyVoice.timedOut || (window.speechSynthesis.getVoices() || []).length > 0;
  }
  function waitForVoices() {
    if (speech.waiting) return;
    speech.waiting = true;
    const ready = () => {
      if (!speech.waiting) return;
      speech.waiting = false;
      if (!(window.speechSynthesis.getVoices() || []).length) tallyVoice.timedOut = true;
      pump();
    };
    if (window.speechSynthesis.addEventListener) window.speechSynthesis.addEventListener("voiceschanged", ready, { once: true });
    setTimeout(ready, 1500);
  }
  function hush() {
    $$(".is-talking").forEach((el) => el.classList.remove("is-talking"));
    voice.spokeUntil = 0;
  }
  function readMs(text) { return Math.min(6000, 700 + String(text).split(/\s+/).length * READ_MS_PER_WORD); }
  // What the queue does with a line depends on what kind of line it is, and there are
  // three. An acknowledgement, which is the pose acknowledgement and the success line:
  // the child earned it, so it is never dropped and it cuts whatever is playing. A
  // correction, which is always spoken to the end once it has started. Everything else
  // is a nudge, and only the newest nudge is worth saying. The kind is read off the
  // state message, or given by the caller.
  // The line that opens the next exercise is not one of them any more: it used to cut
  // the success line every time, which is the one line the child had just earned. It
  // waits its turn like an instruction, behind the beat that belongs to the answer.
  const ACK_STATES = ["correct_pose", "waiting_answer", "answer_correct"];
  const CORRECTION_MOMENTS = ["wrong_left_finger", "wrong_right_finger", "hands_swapped",
    "same_hand_twice", "recount_tens", "recount_units", "no_contact"];
  function lineKind(m) {
    if (ACK_STATES.includes(m.state)) return "ack";
    if (CORRECTION_MOMENTS.includes(m.reaction) || m.state === "wrong_pose" || m.state === "answer_wrong") return "correction";
    return "nudge";
  }
  // Nothing is dropped quietly. Every line the queue lets go is reported to the server,
  // which keeps the record in data/tutor_log.jsonl: what the line was, why it went, and
  // when. The page is the only place that knows, so the page is what says so.
  function reportDrop(item, reason) {
    if (!item || item.reported) return;
    item.reported = true;
    sendLesson({ type: "line_drop", line: item.key || item.text, reason, at: Date.now() });
  }
  function dropLine(item, reason) {
    const at = speech.queue.indexOf(item);
    if (at >= 0) speech.queue.splice(at, 1);
    reportDrop(item, reason);
    closeItem(item);
  }
  // A newer line takes the place of an older unspoken one of its own kind, and the
  // queue is never deeper than one waiting line: past that, the line that goes is the
  // one the child can most afford to lose, never an acknowledgement and never a
  // correction while anything else is there to go instead.
  const REPLACEABLE = ["nudge", "correction"];
  function enqueue(item) {
    if (REPLACEABLE.includes(item.kind)) {
      speech.queue.filter((q) => q.kind === item.kind)
        .forEach((q) => dropLine(q, "replaced_by_newer_of_same_kind"));
    }
    speech.queue.push(item);
    while (speech.queue.length > MAX_PENDING) {
      const droppable = speech.queue.filter((q) => q.kind !== "ack");
      const victim = droppable.find((q) => q.kind !== "correction") || droppable[0];
      if (!victim) break;
      dropLine(victim, "queue_full");
    }
  }
  // The one door. Only Tally's bubble goes through it: a title, a label, an XP number
  // or a level name is shown and stays silent. The bubble is written when the line is
  // spoken, not when it is asked for, so what is on screen is what Tally is saying.
  // "still" is the condition that made the line true; the queue tests it again at the
  // last moment and drops the line rather than speak it late. "onStart" moves the
  // screen with the line, "after" runs once the line is over or dropped.
  function say(text, bubble, tally, opts) {
    const line = String(text == null ? "" : text).replace(/\s+/g, " ").trim();
    if (!line) return null;
    const cuts = Boolean(opts && opts.interrupt);
    const item = { text: line, bubble: bubble || null, tally: tally || null, done: false,
      kind: (opts && opts.kind) || (cuts ? "ack" : "instruction"), key: (opts && opts.key) || null,
      still: (opts && opts.still) || null, onStart: (opts && opts.onStart) || null, after: (opts && opts.after) || null };
    if (item.kind === "ack") {
      // ahead of whatever is waiting, then the line in flight is ended where it stands
      speech.queue.unshift(item);
      cutLine();
    } else {
      enqueue(item);
    }
    pump();
    return item;
  }
  function speak(text, host, opts) { return say(text, null, host || null, opts); }
  // a line is stale when its screen is gone or the child has moved past the moment it
  // belonged to: nothing behind the child's back, nothing spoken out of turn
  function stale(item) {
    if (item.bubble && (!item.bubble.isConnected || item.bubble.closest("[hidden]"))) return true;
    // the child earned this one: it reaches them however much has happened since
    if (item.kind === "ack") return false;
    return Boolean(item.still) && !item.still();
  }
  function closeItem(item) {
    if (!item || item.done) return;
    item.done = true;
    if (item.after) { try { item.after(); } catch (e) { /* the dialogue goes on */ } }
  }
  function pump() {
    if (speech.line || speech.timer) return;          // Tally is talking, or the beat is running
    if (!speech.queue.length) return;
    if (!muted && !voicesReady()) { waitForVoices(); return; }
    const dropped = [];
    let item = speech.queue.shift();
    while (item && stale(item)) { dropped.push(item); item = speech.queue.shift(); }
    if (item) startLine(item);
    dropped.forEach((gone) => { reportDrop(gone, "no_longer_true"); closeItem(gone); });
  }
  function talking(item, on) {
    if (item && item.tally) item.tally.classList.toggle("is-talking", on);
    voice.spokeUntil = on ? Infinity : Date.now() + 800;
  }
  function startLine(item) {
    speech.line = item;
    const serial = ++speech.serial;
    if (item.bubble) item.bubble.textContent = item.text;
    if (item.onStart) { try { item.onStart(); } catch (e) { /* the line still goes out */ } }
    voice.said = numbersIn(item.text);
    voice.saidText = plainSpeech(item.text);
    ttsPair(true);
    talking(item, true);
    // muted, or a browser with no speech at all: the line still takes the time it takes
    // to read, so the rhythm and the server's clock are the same either way
    if (muted || !hasVoice()) { speech.guard = setTimeout(() => endLine(serial), readMs(item.text)); return; }
    item.rest = sentencesOf(item.text);
    nextSentence(serial);
  }
  function nextSentence(serial) {
    if (serial !== speech.serial || !speech.line) return;
    clearTimeout(speech.guard); speech.guard = null;
    const sentence = speech.line.rest.shift();
    if (!sentence) { endLine(serial); return; }
    const u = new SpeechSynthesisUtterance(sentence);
    u.lang = "en-US"; u.rate = 0.92; u.pitch = 1.05;
    const chosen = tallyVoice.done ? tallyVoice.picked : pickVoice();
    if (chosen) u.voice = chosen; else if (tallyVoice.timedOut) logVoice("browser default");
    u.onend = () => nextSentence(serial);
    u.onerror = () => nextSentence(serial);
    speech.guard = setTimeout(() => endLine(serial), readMs(sentence) + SPEAK_GRACE_MS);
    try { window.speechSynthesis.speak(u); } catch (e) { endLine(serial); }
  }
  function endLine(serial) {
    if (serial !== speech.serial) return;
    clearTimeout(speech.guard); speech.guard = null;
    const item = speech.line;
    speech.line = null;
    talking(item, false);
    ttsPair(false);
    // the beat starts before anything this line was waiting on, so a line asked for
    // from inside "after" still waits its turn. There is no beat after an
    // acknowledgement: the child is already past it and the next line follows at once.
    const beat = item && item.kind === "ack" ? 0 : PAUSE_MS;
    speech.timer = setTimeout(() => { speech.timer = null; pump(); }, beat);
    closeItem(item);
  }
  // The pedagogical clock on the server stops while Tally talks, so every line is
  // bracketed by this pair, muted or not, engine or no engine: the clock must never
  // wait on a voice that is not coming.
  function ttsPair(on) { sendLesson({ type: "tts", speaking: Boolean(on) }); }
  // One line cut, not the dialogue: the line being spoken is ended where it stands, its
  // tts pair closed and the beat cleared, so the line that cut it goes out now instead
  // of one beat after a sentence the child has stopped listening to. What was queued
  // behind is kept and follows in order.
  function cutLine() {
    clearTimeout(speech.timer); speech.timer = null;
    clearTimeout(speech.guard); speech.guard = null;
    speech.serial += 1;          // the engine may still report the end of a line that is over
    const line = speech.line;
    speech.line = null;
    if (line) { talking(line, false); ttsPair(false); }
    if (hasVoice()) { try { window.speechSynthesis.cancel(); } catch (e) { /* no engine */ } }
    closeItem(line);
  }
  // the dialogue is over: mute, or a screen the child has left. Whatever is left to say
  // is dropped, and the pair around the line being spoken is closed.
  function cutSpeech() {
    clearTimeout(speech.timer); speech.timer = null;
    clearTimeout(speech.guard); speech.guard = null;
    speech.serial += 1;
    const dropped = speech.queue.splice(0);
    const line = speech.line;
    speech.line = null;
    if (line) { talking(line, false); ttsPair(false); }
    // An engine with nothing of ours in it is left alone: cancelling an idle voice is
    // not a cut. Every view change comes through here, and most of them arrive in
    // silence; only a screen that really was talking touches the engine.
    if ((line || dropped.length) && hasVoice()) { try { window.speechSynthesis.cancel(); } catch (e) { /* no engine */ } }
    hush();
    closeItem(line);
    dropped.forEach(closeItem);
  }

  // ---------- voice answers ----------
  // Chrome starts recognition only from a user gesture: the first click of the session arms
  // it, then it stays alive across screens. "gate" says whether a heard number is an answer
  // right now; "denied" means the mic was refused and the type field is the way in.
  const voice = { recognition: null, wanted: false, running: false, denied: false, gate: false, spokeUntil: 0, said: [], saidText: "", last: null, at: 0 };
  const ONES = { zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9 };
  const TEENS = { ten: 10, eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16, seventeen: 17, eighteen: 18, nineteen: 19 };
  const TENS = { twenty: 20, thirty: 30, forty: 40, fourty: 40, fifty: 50, sixty: 60, seventy: 70, eighty: 80, ninety: 90 };
  function parseNumber(text) {
    const tokens = String(text || "").toLowerCase().replace(/[^a-z0-9\s-]/g, " ").split(/[\s-]+/).filter(Boolean);
    let value = 0, started = false;
    for (const token of tokens) {
      if (/^\d+$/.test(token)) return Number(token);
      if (token in TEENS) { value += TEENS[token]; started = true; continue; }
      if (token in TENS) { value += TENS[token]; started = true; continue; }
      if (token in ONES) { value += ONES[token]; started = true; continue; }
      if (token === "hundred") { value = (value || 1) * 100; started = true; continue; }
      if (token === "and" && started) continue;
      if (started) return value;
    }
    return started ? value : null;
  }
  // every number a sentence carries, as words or digits, plus the whole read as one number
  function numbersIn(text) {
    const tokens = String(text || "").toLowerCase().replace(/[^a-z0-9\s-]/g, " ").split(/[\s-]+/).filter(Boolean);
    const found = new Set();
    tokens.forEach((t) => {
      if (/^\d+$/.test(t)) found.add(Number(t));
      else if (t in ONES) found.add(ONES[t]);
      else if (t in TEENS) found.add(TEENS[t]);
      else if (t in TENS) found.add(TENS[t]);
    });
    const whole = parseNumber(text);
    if (whole !== null) found.add(whole);
    return Array.from(found);
  }
  function setupVoice() {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition || voice.recognition) return;
    const r = new Recognition();
    r.lang = "en-US"; r.continuous = true; r.interimResults = true;
    r.onstart = () => { voice.running = true; markMic(); };
    r.onend = () => { voice.running = false; markMic(); if (voice.wanted && !voice.denied) setTimeout(startVoice, 300); };
    r.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") { voice.wanted = false; voice.denied = true; markMic(); }
    };
    r.onresult = (e) => {
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const text = e.results[i][0].transcript.trim();
        showTranscript(text);
        if (atReady() && !checkRun.button) $("#check-miclabel").textContent = text || checkMicLabel();
        // the answer goes on the first number heard, interim or not: waiting for the end
        // of the utterance is a wait the child feels. The whole sentence still goes to
        // the server on the final result, where it is what tells the tutor they are
        // working; the guards below are what keep one number from being sent twice.
        submitSpoken(text);
        // the gate's word is taken on the final result: the word is short, and an interim
        // transcript of it is the same word a moment earlier, not a wait the child feels
        if (e.results[i].isFinal) { sendSpeech(text); checkHeard(text); }
      }
    };
    voice.recognition = r;
  }
  function armVoice() {
    setupVoice();
    if (!voice.recognition || voice.denied || voice.running) return;
    voice.wanted = true;
    startVoice();
  }
  // a browser with no recognition and a refused microphone are the same thing for
  // the child: the keyboard is the way in, and the label has to say so
  function hasSpeech() { return Boolean(window.SpeechRecognition || window.webkitSpeechRecognition); }
  function canHear() { return hasSpeech() && !voice.denied; }
  function micLabel() { return canHear() ? (voice.running ? "Listening" : "Mic off") : "Type it"; }
  // the gate asks for a word, never a number: its pill says what the child can do, and
  // once it has become the Ready button it says so until the gate is over
  function checkMicLabel() {
    if (checkRun && checkRun.button) return "Ready";
    return canHear() && voice.running ? "Listening" : "Mic off";
  }
  // the answer field of the lesson: the number heard, held in the pill with the bars
  // stopped, for as long as it takes the tutor to answer. The bars are stopped the way
  // the export stops them on the check's own heard state, by taking the animation off.
  function lessonBars(still) {
    const mic = $("#lesson .mic");
    if (!mic) return;
    mic.classList.toggle("is-heard", Boolean(still));
    $$(".bars i", mic).forEach((bar) => { bar.style.animation = still ? "none" : ""; });
  }
  function showHeard(value) {
    const label = $("#lesson .mic .mic-label");
    if (!label || !lesson) return;
    clearTimeout(heardTimer);
    label.textContent = String(value);
    lessonBars(true);
    heardTimer = setTimeout(() => { heardTimer = null; lessonBars(false); setTyped(""); }, HEARD_MS);
  }
  // ---------- the success beat ----------
  // The tutor calls the beat and hands the page its numbers; the page owns the pixels
  // and reads the numbers rather than inventing them. The parts it names run for
  // success_ms, then pause_ms of silence, and the line that closes it is said when the
  // next exercise arrives, which the server holds back until the beat is over. A message
  // without a beat changes nothing, so a server that does not send one plays as before.
  let beatTimer = null;
  function playBeat(beat) {
    const s = lesson;
    if (!s || !beat || beat.kind !== "success") return;
    const root = $("#lesson"), frame = $("#lesson .frame");
    if (!root || !frame) return;
    // next_line closes the beat, and it is the tutor's line, not one the page writes
    s.beat = beat;
    const parts = beat.parts || [];
    const ms = Math.max(0, Number(beat.success_ms) || 0);
    clearTimeout(beatTimer);
    root.dataset.beat = beat.kind;
    // the parts by name: the halo on the hands, the stars of the export's badge and the
    // counter. Each one is a class the design can hang on, and the badge pops today.
    parts.forEach((part) => root.classList.add(`beat-${part}`));
    if (parts.indexOf("stars") !== -1) { void frame.offsetWidth; frame.classList.add("is-cheer"); }
    // the number heard belonged to the answer that earned the beat: it goes with it
    clearHeard();
    beatTimer = setTimeout(() => {
      beatTimer = null;
      root.dataset.beat = "";
      parts.forEach((part) => root.classList.remove(`beat-${part}`));
    }, ms);
  }
  // the new exercise has landed, so the beat is over: its closing line is said here, on
  // the screen the child is looking at, and never over the success line it follows
  function closeBeat() {
    const s = lesson;
    if (!s || !s.beat) return;
    const closing = s.beat.next_line;
    s.beat = null;
    if (!closing) return;
    const turn = s.turn;
    say(closing, $("#lesson .say"), lessonTally(),
        { kind: "instruction", key: "next_one", still: () => Boolean(lesson) && lesson.turn === turn });
  }
  // The pill carries one number for one exercise and no longer. The success beat is
  // where it goes: the moment the beat starts the pill drops the number and is listening
  // again, and the next exercise is armed with it empty whatever the last one ended
  // with, a wrong answer or a quit included.
  function clearHeard() {
    clearTimeout(heardTimer); heardTimer = null;
    lessonBars(false);
    if (lesson) lesson.typed = "";
    const el = $("#lesson .typed"); if (el) el.textContent = "";
    const label = $("#lesson .mic .mic-label");
    if (label) label.textContent = micLabel();
  }
  // the "heard:" readout is for a developer, not for the child: off unless the url asks
  function showTranscript(text) {
    const heard = $("#lesson .heard");
    if (!heard) return;
    heard.hidden = !DEBUG;
    heard.textContent = DEBUG && text && voice.gate ? `heard: ${text}` : "";
  }
  // the live listening state, on the lesson's mic and on the check's pill
  function markMic() {
    const mic = $("#lesson .mic");
    if (mic) {
      // the pill is the lesson's answer field, spoken or typed, so it is always there:
      // a browser that cannot hear says "Type it" on it and the digits land in it
      mic.hidden = false;
      mic.classList.toggle("is-on", voice.running && !voice.denied);
      mic.classList.toggle("is-off", !voice.running || voice.denied);
      // the export writes the quiet pill as .off; the app has said is-off since v1
      mic.classList.toggle("off", !voice.running || voice.denied);
      mic.classList.toggle("is-denied", voice.denied);
      const label = $(".mic-label", mic);
      if (label && !heardTimer) label.textContent = (lesson && lesson.typed) || micLabel();
    }
    const pill = $("#check-mic");
    if (pill) {
      pill.classList.toggle("is-off", !voice.running || voice.denied);
      if (atReady()) $("#check-miclabel").textContent = checkMicLabel();
    }
  }
  function startVoice() { if (!voice.recognition || voice.running || !voice.wanted || voice.denied) return; try { voice.recognition.start(); } catch (e) { /* starting */ } }
  function stopVoice() { voice.wanted = false; if (voice.recognition && voice.running) { try { voice.recognition.stop(); } catch (e) { /* done */ } } }
  function gateVoice(on) { voice.gate = Boolean(on); markMic(); }
  // answer_wrong keeps the pose latched and waits for another try: the next number said
  // out loud is that try, so the gate stays open until the server moves on
  function listenWhile(state) { gateVoice(state === "correct_pose" || state === "waiting_answer" || state === "answer_wrong"); }
  // Tally's own voice is never the child speaking: while one of his lines is in the
  // air, a transcript that is part of that line, or that carries only numbers he just
  // said, is dropped instead of being forwarded.
  function plainSpeech(text) { return String(text || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim(); }
  // a transcript that is nothing but numbers, which is what an echo of a number
  // Tally said sounds like. A sentence with a number inside it is the child talking.
  function onlyNumbers(text) {
    const tokens = plainSpeech(text).split(" ").filter(Boolean);
    return tokens.length > 0 && tokens.every((t) => /^\d+$/.test(t) || t in ONES || t in TEENS || t in TENS || t === "hundred" || t === "and");
  }
  function tallyEcho(text) {
    if (Date.now() >= voice.spokeUntil) return false;
    const heard = plainSpeech(text);
    if (!heard) return true;
    if (voice.saidText && (voice.saidText.includes(heard) || heard.includes(voice.saidText))) return true;
    const numbers = numbersIn(text);
    return onlyNumbers(text) && numbers.length > 0 && numbers.every((n) => voice.said.includes(n));
  }
  // Everything the child says goes to the server, number in it or not: it is what
  // tells the tutor the child is working, and it pauses his clocks. It never submits
  // an answer, and it is sent in addition to the check message, never instead of it.
  function sendSpeech(text) {
    const said = String(text || "").trim().slice(0, 200);
    if (!said || (!lesson && !checkRun)) return;
    if (tallyEcho(said)) return;
    sendLesson({ type: "speech", text: said });
  }
  // The one word the last step of the gate waits for, whatever the recogniser wraps it
  // in. "I'm ready", "Ready!", "yes", "ok then I am ready" all pass; case, punctuation
  // and anything said around the word are ignored. A number is not one of these words,
  // so an answer said out loud never walks the gate on.
  const READY_WORDS = ["ready", "yes", "yeah", "yep", "yup", "ok", "okay"];
  function heardReady(text) {
    return plainSpeech(text).split(" ").some((word) => READY_WORDS.indexOf(word) !== -1);
  }
  // the microphone test listens for that word and for nothing else: Tally asking for it
  // through the speakers is not the child saying it, and a number is not it either
  function checkHeard(text) {
    const run = checkRun;
    if (!run || !run.listening || run.done || stepNow(run) !== "ready") return;
    if (tallyEcho(text) || !heardReady(text)) return;
    passGate(true);
  }
  // answer_first_number_ms is the only wait between hearing a number and sending it, and
  // it is zero by default: the answer leaves on the first result that parses, interim or
  // final. The guards are unchanged, so the same number heard again, as the interim then
  // the final of one breath, is still one answer, and Tally's own voice is still not the
  // child's. The typed path does not come through here at all.
  function submitSpoken(text) {
    const value = parseNumber(text);
    if (value === null) return;
    const wait = PARAMS.answer_first_number_ms;
    if (wait > 0) { setTimeout(() => sendAnswer(value), wait); return; }
    sendAnswer(value);
  }
  function sendAnswer(value) {
    if ((!lesson && !checkRun) || !voice.gate) return;
    // Tally's own voice is not an answer: a number he just said is dropped while he says it
    if (Date.now() < voice.spokeUntil && voice.said.includes(value)) return;
    const now = Date.now();
    if (value === voice.last && now - voice.at < 1500) return;
    voice.last = value; voice.at = now;
    setTyped(String(value).slice(0, 3));
    showHeard(value);
    sendLesson({ type: "check", value });
  }

  // ---------- socket ----------
  // One socket for the page. A handshake that never completes is given up on, a
  // closed socket is reopened with a growing wait, and whatever asked for the last
  // connection is replayed once it is back. A lesson that cannot reach the server
  // says so instead of sitting on "Getting ready" for ever.
  const LINK_KEY = "link_trouble";
  const LINK_TIMEOUT_MS = 4000;
  const LINK_MAX_WAIT_MS = 8000;
  let socket = null, socketReady = false;
  // This page load, as opposed to this socket: a reconnect keeps it, so the server can
  // hand the running node back to it even before it has noticed the old socket is dead.
  // A second tab, a duplicated one included, runs its own script and gets its own.
  const TAB = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const link = { start: null, waiting: [], tries: 0, timer: null, guard: null, trouble: false };
  function connect(onOpen) {
    if (onOpen) {
      link.start = onOpen;
      // several callers can ask while one socket is opening: they all run, in order
      if (!(socket && socketReady)) link.waiting.push(onOpen);
    }
    if (socket && socketReady) { if (onOpen) onOpen(); return; }
    if (socket) return;
    openSocket();
  }
  function openSocket() {
    clearTimeout(link.timer); link.timer = null;
    try { socket = new WebSocket(`ws://${location.host}/ws`); }
    catch (e) { socket = null; retryLink(); return; }
    link.guard = setTimeout(() => { if (socket && !socketReady) { try { socket.close(); } catch (err) { /* already gone */ } } }, LINK_TIMEOUT_MS);
    socket.onopen = () => {
      socketReady = true;
      link.tries = 0;
      clearTimeout(link.guard); link.guard = null;
      linkTrouble(false);
      // the server may have restarted behind a frozen last frame
      videoRefresh();
      const waiting = link.waiting.splice(0);
      if (waiting.length) waiting.forEach((fn) => fn());
      else if (link.start) link.start();   // a reconnect: replay the last start
    };
    socket.onmessage = (e) => onServerMessage(JSON.parse(e.data));
    socket.onclose = () => {
      socketReady = false; socket = null;
      clearTimeout(link.guard); link.guard = null;
      retryLink();
    };
    socket.onerror = () => { socketReady = false; };
  }
  function retryLink() {
    if (link.timer) return;
    link.tries += 1;
    if (link.tries >= 2) linkTrouble(true);
    const wait = Math.min(LINK_MAX_WAIT_MS, 500 * Math.pow(2, link.tries - 1));
    link.timer = setTimeout(() => { link.timer = null; openSocket(); }, wait);
  }
  function linkTrouble(on) {
    if (Boolean(on) === link.trouble) return;
    link.trouble = Boolean(on);
    if (!link.trouble) return;
    if (lesson) {
      // the export's bubble is the one place a line is said: there is no sub line
      // under it any more, so the trouble is said the way everything else is
      say(line(LINK_KEY), $("#lesson .say"), lessonTally());
    } else if (checkRun) {
      checkSay(line(LINK_KEY));
    }
    toast(line(LINK_KEY));
  }
  // every message names the page load it comes from, so the server can tell this page
  // on a new socket from a genuinely second tab whatever order the messages arrive in
  function sendLesson(payload) {
    if (socket && socketReady) socket.send(JSON.stringify(Object.assign({ tab: TAB }, payload)));
  }
  function onServerMessage(m) {
    if (m.demo && !demoSeeded) seedShowcase();
    if (m.type === "node_end") {
      // an abandoned session on the server must never write over this page's record:
      // the message is filtered against what is actually running here first
      const forCheck = Boolean(checkRun && m.node_id === "check");
      const forLesson = Boolean(lesson && (!m.node_id || m.node_id === lesson.node.id));
      if (!forCheck && !forLesson) return;
      // the server hands the learner back; xp and the name are the page's, keep the higher xp
      if (m.state) {
        const mine = learner() || {};
        m.state.xp = Math.max(Number(mine.xp) || 0, Number(m.state.xp) || 0);
        if (!m.state.display_name && mine.display_name) m.state.display_name = mine.display_name;
        saveLearner(m.state);
      }
      if (forCheck) return endGate(true);
      lesson.serverCorrect = m.correct; lesson.total = m.total || lesson.total; lesson.endTally = m.tally;
      return finish(false);
    }
    if (m.type !== "state") return;
    if (checkRun && m.node === "check") return checkMessage(m);
    readParams(m);
    if (!lesson || m.node !== lesson.node.id) return;
    const fresh = m.state !== lesson.last;
    // A new exercise, not a new fact: a node can serve the same fact five times over
    // (u1-l2 is 6x7 both ways), and only the engine's rearm to exercise_shown moves
    // once per question. That is the counter and the clean first try again.
    if (fresh && m.state === "exercise_shown") lesson.done += 1;
    // the success beat between two exercises, and the exercise it hands over to: the
    // beat plays on its own numbers, the pill drops the number it was showing at the
    // start of it, and the new exercise is armed with nothing of the last one on screen
    if (m.tutor_beat) playBeat(m.tutor_beat);
    if (fresh && m.state === "exercise_shown") { clearHeard(); closeBeat(); }
    if (m.fact) lesson.fact = m.fact;
    if (fresh && m.state === "answer_wrong" && lesson.hearts !== null) {
      lesson.hearts -= 1; lesson.hit = true;
      if (lesson.hearts <= 0) { sendLesson({ type: "quit" }); return finish(true); }
    }
    // First try is the server's answer, not the page's: it holds the clock, the ladder
    // and the params, and it alone knows whether the child asked for the help given.
    // The value that counts is the one riding this answer_correct message. A message
    // without the field counts as false, so an older server simply pays no bonus.
    if (fresh && m.state === "answer_correct") { lesson.correct += 1; if (m.first_try) lesson.firstTry += 1; }
    lesson.last = m.state;
    renderPractice(m);
    listenWhile(m.state);
    speakFor(m);
  }
  // What Tally says for one state message. tutor_line is a new intervention, sent once;
  // null means nothing new, never "cancel what is waiting". Without one, the engine's
  // phrase is the line, and only when that phrase itself has changed: an unchanged
  // tally on the next refresh used to replace the tutor's line and drop it from the
  // queue. A line belongs to its moment, the exercise and the engine state it was sent
  // in, or to the exercise alone when it opens one; still waiting once that is gone,
  // it is obsolete and dropped, never spoken late. A newer tutor line supersedes a
  // waiting one, and supersedes a waiting phrase; a phrase never supersedes a tutor line.
  function speakFor(m) {
    const s = lesson;
    const exercise = `${s.done}|${m.exercise || ""}`;
    const moment = `${exercise}|${m.state}`;
    if (exercise !== s.exerciseKey) s.exerciseKey = exercise;
    if (moment !== s.momentKey) { s.momentKey = moment; s.said = null; }
    const inScope = m.state === "exercise_shown"
      ? () => Boolean(lesson) && lesson === s && s.exerciseKey === exercise
      : () => Boolean(lesson) && lesson === s && s.momentKey === moment;
    const tallyChanged = (m.tally || null) !== s.tally;
    s.tally = m.tally || null;
    const bubble = $("#lesson .say"), host = lessonTally();
    // how the queue treats the line is the server's word, whichever of the two it is:
    // an acknowledgement still goes ahead, may cut, and is never dropped
    const delivery = { kind: lineKind(m), key: m.reaction || m.state, interrupt: interrupts(m) };
    if (m.tutor_line) {
      if (m.tutor_line === s.said) return;
      s.said = m.tutor_line;
      const turn = ++s.turn;
      s.tallyTurn += 1;
      say(m.tutor_line, bubble, host, { ...delivery, still: () => inScope() && s.turn === turn });
    } else if (m.tally && tallyChanged && m.tally !== s.said) {
      s.said = m.tally;
      const tallyTurn = ++s.tallyTurn;
      say(m.tally, bubble, host, { ...delivery, still: () => inScope() && s.tallyTurn === tallyTurn });
    }
  }
  // --demo: the map opens as a showcase, first unit done, second current
  function seedShowcase() {
    demoSeeded = true;
    if (!xp()) addXp(210);
    if (!Object.keys(progress.stars).length) {
      const nodes = L.allNodes().filter((n) => n.unitId === L.UNITS[0].id);
      nodes.forEach((n) => { if (n.kind === "chest") progress.chests[n.id] = true; else progress.stars[n.id] = 3; });
      save();
    }
    unitAt = null;
    if (view === "practice") renderCourse(); else decorate(document);
  }

  // ---------- the camera lesson ----------
  const MOOD = { exercise_shown: "ready", waiting_pose: "ready", wrong_pose: "almost", correct_pose: "thinking", waiting_answer: "thinking", answer_correct: "happy", answer_wrong: "almost" };
  function startLesson(id) {
    const node = L.findNode(id);
    const total = node.kind === "boss" ? L.BOSS_QUESTIONS : L.LESSON_QUESTIONS;
    lesson = { node, total, correct: 0, firstTry: 0, serverCorrect: null, done: 0, fact: null, last: null, said: null, turn: 0,
      tally: null, tallyTurn: 0, exerciseKey: null, momentKey: null,
      beat: null, hearts: node.kind === "boss" ? 3 : null, hit: false, typed: "", paid: false, t0: Date.now() };
    stopCheck();
    VIEWS.forEach((v) => { $("#" + v).hidden = true; });
    $("#finish").hidden = true;
    $("#lesson").hidden = false;
    enterView("lesson");
    // the lesson is the screen that asks for a spoken answer, so opening one arms the
    // microphone: the tap that opened it is the gesture Chrome wants, and a lesson
    // opened by name (a test, a hash) still comes up with voice ready
    armVoice();
    shellPractice();
    connect(() => {
      // replayed after a dropped socket: the server starts the node again, so the
      // page banks what was already earned and counts this node from zero too
      restartCounters();
      sendLesson({ type: "start_node", tab: TAB, state: learner(), node: { id: node.id, kind: node.kind, pairs: node.pairs, count: total } });
    });
  }
  function restartCounters() {
    const s = lesson;
    if (!s) return;
    payLesson();
    s.paid = false;
    s.done = 0; s.serverCorrect = null; s.fact = null; s.last = null; s.said = null; s.turn = 0;
    s.tally = null; s.tallyTurn = 0; s.exerciseKey = null; s.momentKey = null;
    s.hearts = s.node.kind === "boss" ? 3 : null; s.hit = false; s.typed = "";
    clearHeard();
  }
  // XP is never lost: every answer already right is paid, whatever happens next
  function payLesson() {
    const s = lesson;
    if (!s || s.paid) return 0;
    s.paid = true;
    const gain = L.xpForNode(s.node.kind, s.correct, s.firstTry, false);
    s.correct = 0; s.firstTry = 0;
    if (gain > 0) addXp(gain);
    return gain;
  }
  // The screen is the export's exercise.html, which lives in index.html: the shell
  // does not build it, it puts it back to the state a node opens on.
  function shellPractice() {
    const root = $("#lesson");
    const frame = $(".frame", root);
    frame.className = "frame clean";
    frame.dataset.state = "waiting";
    $(".cam", root).dataset.state = "";
    root.dataset.state = "";
    root.dataset.reaction = "";
    root.dataset.tutor = "WORKING";
    root.dataset.level = "0";
    root.dataset.mode = "normal";
    const ex = $(".ex", root);
    ex.textContent = "Getting ready"; ex.dataset.ex = "";
    $(".count", root).textContent = "";
    $(".hearts", root).dataset.hearts = "";
    $(".bar i", root).style.width = "0%";
    $(".say", root).textContent = "";
    clearTimeout(heardTimer); heardTimer = null;
    // a beat belongs to the exercise that earned it: a node opening carries none
    clearTimeout(beatTimer); beatTimer = null;
    root.dataset.beat = "";
    ["line", "halo", "stars", "counter"].forEach((part) => root.classList.remove(`beat-${part}`));
    lessonBars(false);
    setTyped("");
    showTranscript("");
    $(".gh", root).replaceChildren();
    const band = $(".why", root);
    band.replaceChildren(); band.classList.remove("is-on"); band.dataset.band = "";
    clearBand();
    numbersKey = null;
    clearTimeout(numbersTimer); numbersTimer = null;
    root.classList.remove("shows-numbers");
    const tally = $(".foot > .tally-art", root);
    if (tally) tally.setAttribute("data-tally", "ready");
    decorate(root);
    renderHearts();
    // the first line of the lesson goes through the same door as every other line
    say(line("show_both_hands"), $("#lesson .say"), lessonTally(), { kind: "instruction", key: "waiting_pose" });
    videoOn($(".practice-video", root));
    markMic();
  }
  // Tally is the render, mounted by tally.js in the export's box. The box is the
  // direct child of the foot; the render tally.js builds inside it carries the
  // same class, so the selector has to say which of the two it means.
  function lessonTally() { return $("#lesson .foot > .tally-art"); }
  // a boss carries hearts, a lesson does not. The export's top strip has no place
  // for them, so they sit beside the counter, the way states.html puts its pills.
  function renderHearts() {
    const s = lesson, box = $("#lesson .hearts");
    if (!box || !s) return;
    box.hidden = s.hearts === null;
    if (s.hearts === null) return;
    if (box.dataset.hearts !== String(s.hearts)) {
      box.dataset.hearts = String(s.hearts);
      box.innerHTML = use("icon-flame") + s.hearts;
      box.setAttribute("aria-label", `${s.hearts} hearts`);
      decorate(box);
    }
    if (s.hit) { box.classList.add("is-hit"); setTimeout(() => box.classList.remove("is-hit"), 420); s.hit = false; }
  }
  // ---------- the camera stream ----------
  // /video is a response that never ends, so an <img> streams for exactly as long as
  // it has a src: it is set when the check or the lesson is on screen and cleared on
  // the way out. A stream that fails is retried, and a restarted server is picked up
  // when the socket comes back, instead of leaving a frozen last frame on screen.
  const stream = { tries: 0, timer: null };
  function videoSrc() { return `/video?t=${Date.now()}`; }
  function videoOn(img) {
    if (!img) return;
    clearTimeout(stream.timer); stream.timer = null;
    img.onload = () => { stream.tries = 0; };
    img.onerror = () => videoRetry(img);
    img.src = videoSrc();
  }
  function videoOff(img) {
    if (!img) return;
    clearTimeout(stream.timer); stream.timer = null;
    img.onload = null;
    img.onerror = null;
    img.removeAttribute("src");
  }
  function videoRetry(img) {
    if (stream.timer) return;
    stream.tries += 1;
    const wait = Math.min(4000, 500 * stream.tries);
    stream.timer = setTimeout(() => {
      stream.timer = null;
      if (img.isConnected && img.onerror) img.src = videoSrc();
    }, wait);
  }
  function videoRefresh() {
    if (checkRun) videoOn($("#check-cam .practice-video"));
    if (lesson && !$("#lesson").hidden) videoOn($(".practice-video", $("#lesson")));
  }
  // the overlay covers exactly the rendered video box: the img's own box, corrected for what
  // object-fit did with it (contain letterboxes, cover crops). One convention for both
  // screens: the overlay draws in the picture's own pixels, 1194 across and as tall as the
  // camera ratio makes it, which is the export's own box, so a marker radius means the same
  // size wherever it is drawn. A landmark is a frame fraction and is multiplied through.
  // The lesson box also takes the camera's ratio so there is no letterbox to begin with.
  function videoRatio(video) {
    return (video && video.naturalWidth && video.naturalHeight) ? video.naturalWidth / video.naturalHeight : 4 / 3;
  }
  function fitOverlay(stage) {
    const video = $(".practice-video", stage), overlay = $(".practice-overlay", stage);
    if (!stage || !video || !overlay) return 4 / 3;
    const ratio = videoRatio(video);
    const cam = video.closest(".cam");
    if (cam && !cam.classList.contains("cam-full")) cam.style.setProperty("--cam-ratio", String(ratio));
    const W = video.clientWidth, H = video.clientHeight;
    const cover = getComputedStyle(video).objectFit === "cover";
    const w = cover ? Math.max(W, H * ratio) : Math.min(W, H * ratio), h = w / ratio;
    overlay.setAttribute("viewBox", `0 0 ${VIEW_W} ${Math.round(VIEW_W / ratio)}`);
    overlay.style.cssText = `left:${video.offsetLeft + (W - w) / 2}px;top:${video.offsetTop + (H - h) / 2}px;width:${w}px;height:${h}px`;
    return ratio;
  }
  // The four looks the export draws, from the state the server is in. The server's
  // own state stays on the camera box and on the view, which is what the overlay
  // and the tests read; this is only what the picture is dressed as.
  // Visual before voice: the moment the pose is confirmed the picture goes green, on
  // the same tick as the message that confirmed it and with nothing in between. Tally
  // may still be finishing a sentence; the child has already been told yes.
  const POSED = ["correct_pose", "waiting_answer", "answer_correct"];
  function lessonLook(m) {
    if (m.reaction === "cannot_see") return "blind";
    if (POSED.includes(m.state)) return "yes";
    if (m.state === "wrong_pose" || m.state === "answer_wrong") return "almost";
    return "waiting";
  }
  // "8 x 7" as the export writes it, with the sign in its own green span. A state
  // message arrives on every camera frame, so nothing is rebuilt that has not changed.
  function writeExercise(el, exercise) {
    if (el.dataset.ex === String(exercise || "")) return;
    el.dataset.ex = String(exercise || "");
    const parts = String(exercise || "").split(/\s*x\s*/i);
    if (parts.length !== 2) { el.textContent = "Getting ready"; return; }
    const sign = document.createElement("span");
    sign.className = "eq";
    sign.textContent = "×";
    el.replaceChildren(document.createTextNode(parts[0] + " "), sign, document.createTextNode(" " + parts[1]));
  }
  function renderPractice(m) {
    const s = lesson, root = $("#lesson");
    const frame = $(".frame", root), cam = $(".cam", root);
    const bar = $(".bar i", root);
    if (bar) bar.style.width = `${Math.min(100, (s.done ? s.done - 1 : 0) / s.total * 100)}%`;
    renderHearts();
    writeExercise($(".ex", root), m.exercise);
    $(".count", root).textContent = s.done ? `${Math.min(s.done, s.total)} of ${s.total}` : "";
    setTally(lessonTally(), m.reaction === "cannot_see" ? "squint" : (MOOD[m.state] || "ready"));
    const kind = m.tutor_visual ? m.tutor_visual.kind : null;
    const look = lessonLook(m);
    frame.classList.toggle("detected", (m.fingers || []).length > 0);
    frame.classList.toggle("yes", look === "yes");
    // the export's guide, the arc and the ring drawn on the hands, is what a
    // correction and a ghost are made of
    frame.classList.toggle("coach", kind === "correction" || kind === "ghost");
    // the badge that pops is for the answer, not for the pose
    const cheer = m.state === "answer_correct";
    if (!cheer) frame.classList.remove("is-cheer");
    else if (root.dataset.state !== "answer_correct") { void frame.offsetWidth; frame.classList.add("is-cheer"); }
    frame.dataset.state = look;
    root.dataset.state = m.state;
    root.dataset.reaction = m.reaction || "";
    // the tutor's own state, its rung on the ladder and the mode are diagnostics in
    // V0: nothing is drawn from them, they are here to be read off the page
    root.dataset.tutor = m.tutor_state || "WORKING";
    root.dataset.level = String(m.intervention_level || 0);
    root.dataset.mode = m.mode || "normal";
    cam.dataset.state = m.state;
    numbersWindow(root, m);
    renderBand(m, root);
    drawFingers(m, cam);
  }
  // supportive mode opens an exercise with the numbers 6 to 10 on the fingertips and
  // fades them after three seconds. Normal mode never shows them: there, a number on
  // a fingertip is the tutor's own aid, asked for through tutor_visual.
  const NUMBERS_MS = 3000;
  let numbersTimer = null, numbersKey = null;
  function numbersWindow(root, m) {
    const supportive = (m.mode || "normal") === "supportive";
    const key = `${supportive ? "s" : "n"}|${m.exercise || ""}`;
    if (key === numbersKey) return;
    numbersKey = key;
    clearTimeout(numbersTimer);
    numbersTimer = null;
    root.classList.toggle("shows-numbers", supportive);
    if (supportive) numbersTimer = setTimeout(() => root.classList.remove("shows-numbers"), NUMBERS_MS);
  }
  // ---------- what the tutor draws ----------
  // tutor_visual is null or one of six kinds. Every one of them is drawn inside the
  // camera view, on the landmarks, in the treatment design/states.html gives it: the
  // pulsing halo, the orange outline on the hand at fault, the ghost finger sliding
  // to where it belongs, the dashed placement zones, the numbers lighting up, and
  // the rescue card walked through over the bottom of the picture.
  function rescueLines(card, exercise) {
    const tens = Number(card.tens) || 0, units = Number(card.units) || 0;
    const up = String(exercise || "").split(/x/i).map((part) => Number(part.trim()));
    const lines = [`${tens} ${tens === 1 ? "ten" : "tens"} = ${tens * 10}`];
    // the units are the fingers still up on each hand, in the order the exercise asks
    lines.push(up.length === 2 && up.every((n) => n >= 6 && n <= 10)
      ? `${10 - up[0]} x ${10 - up[1]} = ${units}`
      : `${units} units`);
    lines.push(`${tens * 10} + ${units} = ${Number(card.total) || tens * 10 + units}`);
    return lines;
  }
  // one band over the bottom of the picture, the export's .why.
  // Nothing on this screen shows the tens, the units or the result before the child
  // has answered: the whole point of the lesson is that they work it out. The band is
  // the rescue card, which is the one moment walking the method through is the point,
  // or a short recap once the answer is in, and it goes away again. Every other line
  // Tally has goes in his bubble and nowhere else. The server sends its reasoning with
  // every state message; the page is where it is held back.
  const RECAP_MS = 3000;
  let bandTimer = null;
  function clearBand() {
    clearTimeout(bandTimer);
    bandTimer = null;
  }
  function renderBand(m, root) {
    const box = $(".why", root);
    if (!box) return;
    const visual = m.tutor_visual || null;
    const card = visual && visual.kind === "rescue_card" ? visual : null;
    const recap = !card && m.state === "answer_correct";
    const lines = card ? rescueLines(card, m.exercise) : (recap ? (m.reasoning || []) : []);
    const key = (card ? "card|" : recap ? "recap|" : "off|") + (m.exercise || "") + "|" + lines.join("|");
    if (box.dataset.band === key) return;
    box.dataset.band = key;
    clearBand();
    box.classList.toggle("is-on", lines.length > 0);
    box.replaceChildren();
    lines.forEach((line) => {
      const el = document.createElement("span");
      // a rescue line is the tutor's own text; a reasoning line is the engine's
      // phrase, which carries the export's <b> around the number it is about
      if (card) el.textContent = line; else el.innerHTML = line;
      box.appendChild(el);
    });
    // a recap is read once and then gone, so the next question starts clean
    if (recap && lines.length) {
      bandTimer = setTimeout(() => {
        box.classList.remove("is-on");
        box.replaceChildren();
      }, RECAP_MS);
    }
  }
  function svgEl(tag, cls) {
    const el = document.createElementNS(NS, tag);
    if (cls) el.setAttribute("class", cls);
    return el;
  }
  function dot(cls, x, y, r) {
    const c = svgEl("circle", cls);
    c.setAttribute("cx", x); c.setAttribute("cy", y); c.setAttribute("r", r);
    return c;
  }
  // an aid carries a CSS or SMIL animation, so one already saying the same thing in
  // the same place is kept rather than rebuilt: a new element every frame would
  // restart the animation and nothing would ever move
  function keeper(overlay) {
    const kept = {};
    $$("[data-keep]", overlay).forEach((el) => { kept[el.dataset.keep] = el; });
    return (tag, build) => {
      if (kept[tag]) return kept[tag];
      const el = build();
      el.dataset.keep = tag;
      return el;
    };
  }
  // a fingertip is close enough to where it was for an aid pointing at it to stay
  const near = (p) => `${Math.round(p.x / 24)}:${Math.round(p.y / 24)}`;
  // the two hand zones of SPEC section 9, in the dashed style states.html draws them
  function placementZones(overlay, W, H) {
    [0.3, 0.7].forEach((centre) => {
      const zone = svgEl("rect", "zone-box");
      zone.setAttribute("x", (centre - 0.15) * W); zone.setAttribute("y", 0.18 * H);
      zone.setAttribute("width", 0.3 * W); zone.setAttribute("height", 0.64 * H);
      zone.setAttribute("rx", 0.055 * W);
      overlay.appendChild(zone);
    });
  }
  // the hand at fault, outlined where it is. Nothing is drawn on the hand that is right.
  function handOutline(overlay, points, W) {
    if (!points.length) return;
    const xs = points.map((p) => p.x), ys = points.map((p) => p.y), pad = 0.05 * W;
    const box = svgEl("rect", "hand-box");
    box.setAttribute("x", Math.min.apply(null, xs) - pad); box.setAttribute("y", Math.min.apply(null, ys) - pad);
    box.setAttribute("width", Math.max.apply(null, xs) - Math.min.apply(null, xs) + pad * 2);
    box.setAttribute("height", Math.max.apply(null, ys) - Math.min.apply(null, ys) + pad * 2);
    box.setAttribute("rx", 0.05 * W);
    overlay.appendChild(box);
  }
  // the export's guide: the arc crawling from the finger that is wrong to the one
  // the exercise asks for, the arrow head at the end of it, and the ring waiting there
  function guideArc(from, to) {
    const g = svgEl("g", "guide");
    const mx = (from.x + to.x) / 2, my = Math.min(from.y, to.y) - 128;
    const angle = Math.atan2((to.y - 44) - my, to.x - mx) * 180 / Math.PI;
    const arc = svgEl("path", "arc");
    arc.setAttribute("d", `M ${from.x} ${from.y - 40} Q ${mx} ${my} ${to.x} ${to.y - 44}`);
    const head = svgEl("polygon", "head");
    head.setAttribute("points", "0,-15 30,0 0,15");
    head.setAttribute("transform", `translate(${to.x},${to.y - 44}) rotate(${angle})`);
    g.append(arc, head, dot("ring", to.x, to.y, 47));
    return g;
  }
  // the ghost finger of states.html: a translucent tip that slides from where the
  // finger is to where it belongs, over and over, with the target ringed
  function ghostSlide(from, to) {
    const g = svgEl("g", "guide");
    const ghost = dot("ghost", 0, 0, 26);
    const move = svgEl("animateTransform");
    move.setAttribute("attributeName", "transform");
    move.setAttribute("type", "translate");
    move.setAttribute("values", `${from.x} ${from.y};${to.x} ${to.y};${to.x} ${to.y};${from.x} ${from.y}`);
    move.setAttribute("keyTimes", "0;0.55;0.82;1");
    move.setAttribute("dur", "2.8s");
    move.setAttribute("repeatCount", "indefinite");
    ghost.appendChild(move);
    g.append(dot("ring", to.x, to.y, 42), ghost);
    return g;
  }
  // The overlay is the export's .gh and it draws in the picture's own pixels: 1194
  // across, as tall as the camera's ratio makes it. fitOverlay has already laid it
  // over exactly the rendered video box, so a landmark, which is a frame fraction,
  // multiplies straight through.
  const VIEW_W = 1194;
  function drawFingers(m, stage) {
    const overlay = $(".practice-overlay", stage);
    if (!overlay) return;
    const ratio = fitOverlay(stage);
    const W = VIEW_W, H = Math.round(VIEW_W / ratio);
    overlay.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const keep = keeper(overlay);
    overlay.replaceChildren();
    const id = (f) => `${f.hand}:${f.number}`;
    const wrong = new Set((m.wrong || []).map(id));
    const match = new Set((m.match || []).map(id));
    const visual = m.tutor_visual || null;
    const kind = visual ? visual.kind : null;
    const fingers = m.fingers || [];
    const at = {};
    fingers.forEach((f) => { at[id(f)] = { x: f.x * W, y: f.y * H }; });
    const pulse = kind === "pulse_finger" ? `${visual.hand}:${visual.finger}` : null;
    const expect = kind === "correction" ? `${visual.wrong_hand}:${visual.expected_finger}` : null;
    const ghostTo = kind === "ghost" ? `${visual.hand}:${visual.to}` : null;
    const ghostFrom = kind === "ghost" ? `${visual.hand}:${visual.from}` : null;
    // A marker is earned, never given away. Supportive mode marks every fingertip;
    // normal mode marks only what the tutor is pointing at, and the two target tips
    // once the answer is in. The numbers 6 to 10 are supportive mode's own, plus the
    // one visual that asks for them by name.
    const supportive = (m.mode || "normal") === "supportive";
    // the two target tips are marked as soon as the pose is confirmed: that green is
    // the yes the child reads, and it does not wait for Tally or for the answer
    const posed = POSED.includes(m.state);
    const aid = new Set([pulse, expect, ghostTo, ghostFrom].filter(Boolean));
    const marked = (tag) => supportive || (posed && match.has(tag))
      || (visual !== null && (aid.has(tag) || match.has(tag) || wrong.has(tag)));
    if (kind === "placement_zones") placementZones(overlay, W, H);
    if (kind === "correction") handOutline(overlay, fingers.filter((f) => f.hand === visual.wrong_hand).map((f) => at[id(f)]), W);
    for (const finger of fingers) {
      const tag = id(finger), p = at[tag];
      const g = svgEl("g", "fin" + (marked(tag) ? " on" : "")
        + (match.has(tag) ? " want" : "") + (wrong.has(tag) ? " bad" : ""));
      g.setAttribute("transform", `translate(${p.x},${p.y})`);
      g.dataset.hand = finger.hand;
      g.dataset.number = finger.number;
      const num = svgEl("text", kind === "finger_numbers" ? "num lit" : "num");
      num.setAttribute("y", 10);
      num.dataset.number = finger.number;
      num.textContent = finger.number;
      g.append(dot("halo", 0, 0, 38), dot("tip", 0, 0, 32), num);
      overlay.appendChild(g);
    }
    // the aids go over the fingertips, so none of them is hidden behind a circle
    if (pulse && at[pulse]) {
      overlay.appendChild(keep(`pulse:${pulse}:${near(at[pulse])}`, () => dot("ring pulse", at[pulse].x, at[pulse].y, 40)));
    }
    if (expect && at[expect]) {
      const wrongOne = (m.wrong || []).map(id).find((tag) => tag.indexOf(visual.wrong_hand + ":") === 0);
      if (wrongOne && at[wrongOne] && wrongOne !== expect) {
        overlay.appendChild(keep(`arc:${wrongOne}:${expect}:${near(at[expect])}`, () => guideArc(at[wrongOne], at[expect])));
      }
      overlay.appendChild(dot("dot", at[expect].x, at[expect].y, 19));
    }
    if (ghostTo && at[ghostTo] && at[ghostFrom]) {
      overlay.appendChild(keep(`ghost:${ghostFrom}:${ghostTo}:${near(at[ghostTo])}`, () => ghostSlide(at[ghostFrom], at[ghostTo])));
    } else if (ghostTo && at[ghostTo]) {
      overlay.appendChild(dot("ghost", at[ghostTo].x, at[ghostTo].y, 26));
    }
  }
  // the digits belong to the lesson: the gate asks for a word, so nothing is ever typed
  // into it any more
  function setTyped(value) {
    if (!lesson) return;
    lesson.typed = value;
    // kept in the markup and off the screen: the pill is the one place an answer shows
    const el = $("#lesson .typed"); if (el) { el.textContent = value; el.hidden = true; }
    const caret = $("#lesson .caret"); if (caret) caret.hidden = true;
    const label = $("#lesson .mic .mic-label");
    if (label && !heardTimer) label.textContent = value || micLabel();
  }

  // ---------- finish ----------
  // The export's two panes, filled in place: the card, then the level up card behind it.
  function finish(outOfHearts) {
    const s = lesson;
    gateVoice(false);
    const root = $("#finish");
    const total = s.total;
    const correct = s.serverCorrect === null || s.serverCorrect === undefined ? s.correct : s.serverCorrect;
    const res = L.recordLesson(progress, s.node.id, correct, total);
    progress = res.progress; save();
    justUnlocked = res.unlocked;
    const gain = L.xpForNode(s.node.kind, correct, s.firstTry, !outOfHearts);
    const before = L.levelInfo(xp());
    addXp(gain);
    s.paid = true;
    const after = L.levelInfo(xp());
    // a perfect boss is 220 XP, which can cross two levels at once: every accessory
    // between the two levels is announced, not just the one at the level landed on
    const earned = L.earnedBetween(before.level, after.level);
    const fail = outOfHearts || res.stars === 0;
    const title = outOfHearts ? "Out of hearts" : fail ? "Almost there" : s.node.kind === "boss" ? "Boss defeated!" : res.stars === 3 ? "Perfect lesson!" : "Lesson complete!";
    const need = Math.ceil(total * 0.6);
    const sub = fail ? line("finish_need", { need, total })
      : s.endTally || (res.unlocked ? line("finish_unlocked") : line("finish_more_stars"));

    const p1 = $("#p1", root), p2 = $("#p2", root);
    p1.className = "pane";
    p2.className = "pane in";
    $("h1", p1).textContent = title;
    $(".sub", p1).textContent = sub;
    $("#gain", root).textContent = "0";
    $("#n1", root).textContent = before.name;
    $("#x1", root).textContent = levelLabel(before);
    const fill = $("#f1", p1);
    fill.style.transition = "none";
    fill.style.width = `${before.percent}%`;
    $("#t-done", root).setAttribute("data-tally", fail ? "almost" : "happy");
    $("#t-done", root).setAttribute("data-accessories", before.accessories);
    // the export gives the card one button. A lesson that failed needs the way back
    // as well, so the second button of the level up card's row is borrowed for it.
    $("#cont1", root).textContent = fail ? "Try again" : "Continue";
    $("#cont1", root).dataset.action = fail ? "retry" : "home";
    $("#cont1", root).dataset.node = s.node.id;
    const back = $(".btn-row .ghost", p1);
    if (fail && !back) {
      const link = document.createElement("a");
      link.className = "btn2 ghost";
      link.href = "#practice";
      link.dataset.action = "home";
      link.textContent = "Back to the path";
      $(".btn-row", p1).appendChild(link);
    } else if (!fail && back) back.remove();

    $("#n2", root).textContent = after.name;
    $("#n3", root).textContent = after.name;
    $("#x2", root).textContent = levelLabel(after);
    $("#f2", root).style.width = "0";
    $("#t-up", root).setAttribute("data-tally", "happy");
    $("#t-up", root).setAttribute("data-accessories", after.accessories);
    const earnedLine = $(".earned", p2);
    earnedLine.hidden = earned.length === 0;
    if (earned.length) $("#acc", root).textContent = earned.map((key) => L.ACCESSORY_NAMES[key]).join(" and ");
    confetti($("#confetti", root));

    videoOff($(".practice-video", $("#lesson")));
    $("#lesson").hidden = true;
    root.hidden = false;
    // the card is a view change like any other: whatever the lesson was still saying
    // stops here, so its line can never play over this screen
    enterView("finish");
    decorate(root);
    // the title, the XP count and the level name are read and stay silent. Tally's one
    // closing line is the sub, the sentence the server ended the node with, and it goes
    // through the same door and the same beat as every other line.
    say(sub, $(".sub", p1), $("#t-done", root));
    countUp(gain, before, after);
  }
  // the XP counts up over a second, the bar and the level name follow; then the level up card
  function countUp(gain, before, after) {
    const root = $("#finish");
    const el = $("#gain", root), fill = $("#f1", $("#p1", root)), nameEl = $("#n1", root), xpEl = $("#x1", root);
    const t0 = performance.now(), dur = 1100;
    function tick(now) {
      if (!el || !el.isConnected) return;
      const k = Math.min(1, (now - t0) / dur);
      const shown = Math.round(gain * (1 - Math.pow(1 - k, 3)));
      const info = L.levelInfo(before.xp + shown);
      el.textContent = `${shown}`;
      nameEl.textContent = info.name; xpEl.textContent = levelLabel(info); fill.style.width = `${info.percent}%`;
      if (k < 1) requestAnimationFrame(tick);
      else if (after.level > before.level) setTimeout(levelUp, 1000);
    }
    requestAnimationFrame(tick);
  }
  function levelUp() {
    const root = $("#finish");
    const a = $("#p1", root), b = $("#p2", root);
    if (!a || !b || root.hidden) return;
    a.classList.add("out"); b.classList.add("show");
    setTimeout(() => { const f = $("#f2", root); if (f) f.style.width = `${L.levelInfo(xp()).percent}%`; }, 500);
    // crossing a level is a Tally moment, not a label: one short line, the card's own
    // name read back. It waits its turn behind the closing line like anything else.
    const named = $("#n2", root);
    say(line("level_up_card", { level: named ? named.textContent : "" }), null, $("#t-up", root));
  }
  function confetti(host) {
    if (!host) return;
    const colors = ["#2563d9", "#36a9f5", "#2fae82", "#ef5a4f", "#f8c62c", "#8b6ee0"];
    host.replaceChildren();
    for (let i = 0; i < 46; i++) {
      const bit = document.createElement("i");
      bit.style.left = `${Math.random() * 100}%`;
      bit.style.background = colors[i % colors.length];
      bit.style.setProperty("--dx", `${Math.round(Math.random() * 220 - 110)}px`);
      bit.style.setProperty("--rot", `${Math.round(Math.random() * 900 - 450)}deg`);
      bit.style.animationDuration = `${2.4 + Math.random() * 1.8}s`;
      bit.style.animationDelay = `${Math.random() * 1.1}s`;
      host.appendChild(bit);
    }
  }
  function toast(html) {
    const t = $("#toast");
    t.innerHTML = html; decorate(t);
    t.classList.add("is-on");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => t.classList.remove("is-on"), 2600);
  }
  function leaveLesson() {
    if (lesson) {
      sendLesson({ type: "quit" });
      // SPEC: XP is never lost. Every answer already right is earned, quit or not.
      const gain = payLesson();
      if (gain > 0) toast(`${use("icon-xp")}+${gain} XP kept`);
    }
    link.start = null; link.waiting = [];
    videoOff($(".practice-video", $("#lesson")));
    gateVoice(false);
    cutSpeech();
    lesson = null;
  }
  function backToMap() {
    leaveLesson();
    // land on the unit that just opened, or wherever the learner is
    unitAt = justUnlocked ? L.findNode(justUnlocked).unitIndex : null;
    go("practice");
    setTimeout(() => { justUnlocked = null; }, 900);
  }

  // ---------- profile ----------
  // the levels that hand out something to wear, in order: 2, 4, 6, 8, 10
  function accessoryLevels() {
    return Object.keys(L.ACCESSORIES).map(Number).sort((a, b) => a - b);
  }
  // the export's own tick, drawn rather than taken from icons.svg so the row keeps
  // the stroke colour its rules paint
  const TICK = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M 5 12.5 L 9.5 17 L 19 7" fill="none" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"></path></svg>`;
  function renderProfile() {
    const root = $("#profile");
    const info = L.levelInfo(xp());
    $("#lnum", root).textContent = info.level;
    $("#lname", root).textContent = info.name;
    $("#lxp", root).textContent = info.next ? `${info.inLevel} / ${info.span}` : "Complete";
    $("#lfill", root).style.width = `${info.percent}%`;
    $("#nextline", root).textContent = info.next ? `Next: ${info.next}` : line("profile_top_level");
    // the gear row: what Tally wears now, and what is still waiting for him
    const worn = new Set(info.accessories.split(" ").filter(Boolean));
    $("#wardrobe", root).innerHTML = accessoryLevels().map((level) => {
      const acc = L.ACCESSORIES[level];
      const wear = L.ACCESSORY_NAMES[acc];
      const on = worn.has(acc);
      return `<span class="slot${on ? " on" : ""}" title="${on ? wear : `${wear} at level ${level}`}"><img src="art/acc-${acc}.png" alt="${wear}"></span>`;
    }).join("");
    $("#list", root).innerHTML = L.LEVELS.map((lv, i) => {
      const n = i + 1;
      const acc = L.earnedAt(n);
      const wear = acc ? L.ACCESSORY_NAMES[acc] : "";
      const cls = n < info.level ? " got" : n === info.level ? " now" : "";
      const item = acc ? `<img class="item" src="art/acc-${acc}.png" alt="${wear}" title="${wear}">` : "";
      const tail = n === info.level ? `<span class="tag">You are here</span><span class="mark">${TICK}</span>`
        : n < info.level ? `<span class="mark">${TICK}</span>`
        : `<span class="pending"></span>`;
      return `<div class="row${cls}"><span class="no">${n}</span><span class="nm">${lv.name}</span><span class="right-side">${item}${tail}</span></div>`;
    }).join("");
    decorate(root);
  }


  // ---------- events ----------
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-action]");
    if (!el) return;
    const action = el.dataset.action, id = el.dataset.node;
    if (action === "node") {
      if (el.dataset.status === "locked") return;
      if (L.findNode(id).kind === "chest") openChest(id); else startLesson(id);
    }
    else if (action === "start") runGate(() => startLesson(id));
    else if (action === "chest") openChest(id);
    else if (action === "unit") { unitAt = Number(el.dataset.u); renderCourse(); }
    else if (action === "showcase") { showAt = Number(el.dataset.u); renderShowcase(); }
    else if (action === "profile") { e.preventDefault(); leaveLesson(); go("profile"); }
    else if (action === "mute") setMuted(!muted);
    // switching learner: back to the welcome screen, where the name is the identity.
    // Nothing is cleared, the pointer simply moves when the next name is typed.
    else if (action === "switch") go("welcome");
    else if (action === "child") {
      const who = roster().find((c) => c.key === el.dataset.child);
      if (who && setChild(who.name || who.key)) go("home");
    }
    // help the child asked for, the only help that costs the first try bonus
    else if (action === "help") { if (lesson) sendLesson({ type: "hint" }); }
    // a refusal is not final: an explicit tap on the mic asks the browser again
    else if (action === "mic") {
      if (voice.denied) { voice.denied = false; voice.wanted = true; startVoice(); }
      else if (voice.wanted) stopVoice();
      else { voice.wanted = true; startVoice(); }
      markMic();
    }
    else if (action === "quit" || action === "home") { e.preventDefault(); backToMap(); }
    else if (action === "retry") { e.preventDefault(); runGate(() => startLesson(id)); }
    // the dev reset is this child's record and no one else's
    else if (action === "reset") {
      progress = L.emptyProgress(); unitAt = null; save();
      try { localStorage.removeItem(slot(KEY_LEARNER)); } catch (err) { /* fine */ }
      renderHome(); toast("This child is back to zero");
    }
  });
  function openChest(id) {
    const res = L.claimChest(progress, id);
    if (res.error) return;
    progress = res.progress; save(); justUnlocked = res.unlocked;
    renderCourse(); toast(`${use("icon-chest", "0 0 88 80")}Chest opened, the next lesson is yours`);
    setTimeout(() => { justUnlocked = null; }, 900);
  }
  document.addEventListener("keydown", (e) => {
    const inField = e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA");
    const answering = (lesson && !$("#lesson").hidden) || atReady();
    if (!answering) {
      // the pane on screen: the card until the level up card slides in over it (.out on #p1, .in.show on #p2)
      if (e.key === "Enter" && !$("#finish").hidden) { const btn = $("#finish .pane:not(.out):not(.in) .btn2, #finish .pane.in.show .btn2"); if (btn) btn.click(); }
      return;
    }
    if (inField) return;
    // the last step of the gate is a microphone test: no digits go into it, and the
    // keyboard way through it is the same Enter that answers everywhere else here
    if (checkRun && !lesson) {
      if (e.key === "Enter") { e.preventDefault(); passGate(false); }
      else if (e.key === "Escape") backToMap();
      return;
    }
    const typed = lesson.typed;
    if (e.key >= "0" && e.key <= "9") { if (typed.length < 3) setTyped(typed + e.key); }
    else if (e.key === "Backspace") setTyped(typed.slice(0, -1));
    else if (e.key === "Enter") { e.preventDefault(); if (!typed.length) return; sendLesson({ type: "check", value: Number(typed) }); setTyped(""); }
    else if (e.key === "n" && lesson) { setTyped(""); sendLesson({ type: "next" }); }
    else if (e.key === "Escape") backToMap();
  });
  // the first click of the session is the gesture Chrome needs to open the mic
  document.addEventListener("click", armVoice, { capture: true });
  // The Ready button of the last step: a tap on the pill is the child saying they are
  // ready, and it passes the step exactly as the word does. It is how a browser with no
  // recognition, or a microphone that was refused, gets through the gate.
  const checkPill = $("#check-mic");
  if (checkPill) checkPill.addEventListener("click", () => { if (checkRun && checkRun.listening) passGate(false); });
  // a tap on Tally skips the gate, and does nothing at all until a gate of this session
  // has proved that the camera and the microphone both work
  const checkTally = $("#check-tally");
  if (checkTally) checkTally.addEventListener("click", () => skipGate());
  window.addEventListener("hashchange", route);
  window.addEventListener("resize", () => { if (lesson) fitOverlay($("#lesson .cam")); if (checkRun) fitOverlay($("#check-cam")); });

  // ---------- boot ----------
  function watchCamera() {
    const art = $(".panel-camera-art"), copy = $(".panel-camera h3"), note = $(".panel-camera p");
    if (!art) return;
    const probe = new Image();
    probe.onload = () => {
      art.innerHTML = ""; art.appendChild(probe); art.classList.add("is-live");
      if (copy) copy.textContent = "Tally is watching";
      if (note) note.textContent = line("home_camera_live");
    };
    probe.alt = ""; probe.src = "/video";
  }
  if (params.get("dev") === "1") $$(".dev-reset").forEach((el) => { el.hidden = false; });
  // startLesson is on the surface so the camera lesson can be opened without the path
  // screen, and the child helpers so a test can switch child: the screens are wired by
  // different hands and each one's test should fail for its own reasons.
  window.Tenfold = { parseNumber, numbersIn, heardReady, tallyLine, gateTiming, interrupts, sentencesOf, pickVoice, speak, say, setMuted, voice, fitOverlay, timing: PARAMS,
    startLesson, runGate, addXp, setChild, roster,
    get muted() { return muted; }, get lesson() { return lesson; }, get xp() { return xp(); },
    get gate() { return checkRun; }, get gateProved() { return gateProved(); },
    // the dialogue as it stands, for a test that has to see why a line did not go out
    get queue() { return { line: speech.line && speech.line.text, waiting: speech.queue.map((q) => q.text) }; },
    // the gate's step as a number, the way the export numbers its frames
    get checkStep() { return checkRun ? checkRun.at + 1 : 0; },
    get child() { return child(); }, get name() { return name(); } };
  // The page holds no sentence of its own, so it opens once it has Tally's lines in
  // hand: the first screen is drawn with its line rather than with an empty bubble it
  // would have to fill again. A file it cannot read leaves the bubbles empty and every
  // screen still works.
  loadFiles().then(() => {
    route();
    markMute();
    watchCamera();
    decorate(document);
    // the demo flag arrives on the socket; connect early so the showcase seeds before
    // the map is opened. No callback: whatever the first view asked for runs on open.
    connect();
  });
})();
