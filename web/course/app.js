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
  const params = new URLSearchParams(location.search);
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.prototype.slice.call((root || document).querySelectorAll(sel));
  const NS = "http://www.w3.org/2000/svg";

  // ---------- storage ----------
  function read(key, fallback) {
    try { const raw = localStorage.getItem(key); return raw ? JSON.parse(raw) : fallback; }
    catch (e) { return fallback; }
  }
  function write(key, value) {
    try { localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value)); } catch (e) { /* keep going in memory */ }
  }
  function load() { return Object.assign(L.emptyProgress(), read(KEY, {})); }
  function save() { write(KEY, progress); }
  // the learner record is the profile: the scheduler's memory plus display_name and xp
  function learner() { return read(KEY_LEARNER, null); }
  function saveLearner(state) { write(KEY_LEARNER, state); }
  function name() {
    const me = learner();
    if (me && me.display_name) return String(me.display_name);
    try { return localStorage.getItem(KEY_NAME) || ""; } catch (e) { return ""; }
  }
  function setName(value) {
    const me = learner() || {};
    me.display_name = value;
    saveLearner(me);
    write(KEY_NAME, value);
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
    if (muted) { speech.serial += 1; speech.queue = []; speech.pending = null; try { window.speechSynthesis.cancel(); } catch (e) { /* no engine */ } hush(); ttsSay(false); }
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
  function starSvg(on) { return `<svg viewBox="0 0 48 48" class="${on ? "" : "off"}" style="color:${on ? "var(--gold)" : "var(--line)"}"><use href="#icon-star"></use></svg>`; }
  function decorate(root) {
    const scope = root || document;
    const info = L.levelInfo(xp());
    $$("[data-outfit]", scope).forEach((el) => el.setAttribute("data-accessories", info.accessories));
    if (window.Icons) window.Icons.expand(scope);
    if (window.Tally) window.Tally.mount(scope);
    renderLevelBoxes(scope, info);
  }
  function levelLabel(info) { return info.next ? `${info.inLevel} / ${info.span} XP` : `${info.xp} XP`; }
  function renderLevelBoxes(scope, info) {
    $$("[data-level-name]", scope).forEach((el) => { el.textContent = info.name; });
    $$("[data-level-xp]", scope).forEach((el) => { el.textContent = levelLabel(info); });
    $$("[data-level-fill]", scope).forEach((el) => { el.style.width = `${info.percent}%`; });
    $$("[data-level-next]", scope).forEach((el) => { el.textContent = info.next ? `${info.toNext} XP to ${info.next}` : "Top level reached"; });
  }
  function setTally(host, expr) {
    if (!host) return;
    if (host.getAttribute("data-tally") === expr) return;
    if (window.Tally) window.Tally.set(host, expr); else host.setAttribute("data-tally", expr);
  }

  // ---------- router ----------
  const VIEWS = ["welcome", "home", "learn", "practice", "check", "profile"];
  let view = "welcome";
  function show(name) {
    // the Back button is a way out of a running lesson too: without this the node
    // keeps running on the server and Tally talks over the next screen
    if (lesson) leaveLesson();
    view = name;
    // a screen the child opens says its lines again: the memory that keeps a line
    // from being repeated on every state message is per bubble, and starts fresh here
    $$("[data-said]").forEach(sayAgain);
    VIEWS.forEach((v) => { const el = document.getElementById(v); if (el) el.hidden = v !== name; });
    document.body.dataset.view = name;
    $("#lesson").hidden = true;
    $("#finish").hidden = true;
    if (name !== "check") stopCheck();
    if (name === "home") renderHome();
    if (name === "learn") renderShowcase();
    if (name === "practice") renderCourse();
    if (name === "profile") renderProfile();
    if (name === "check") startCheck();
  }
  function route() {
    const hash = (location.hash || "").replace("#", "");
    if (hash === "check" && checked()) { location.hash = "practice"; return; }
    if (VIEWS.includes(hash)) { show(hash); return; }
    show(name() ? "home" : "welcome");
  }
  function go(name) {
    if (location.hash !== `#${name}`) location.hash = name; else show(name);
  }

  // ---------- welcome ----------
  $("#welcome-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const value = ($("#welcome-name").value || "").trim();
    if (!value) return;
    setName(value);
    go("home");
  });

  // ---------- home ----------
  function renderHome() {
    const who = name();
    $("#greeting").textContent = who ? `Which door today, ${who}?` : "Which door today?";
    const nodes = L.allNodes();
    const units = L.UNITS.map((u, i) => {
      const unitNodes = nodes.filter((n) => n.unitId === u.id);
      const done = unitNodes.every((n) => L.nodeState(progress, n.id) === "done");
      const current = unitNodes.some((n) => L.nodeState(progress, n.id) === "current");
      return { title: u.title, state: done ? "done" : current ? "current" : "locked" };
    }).concat([{ title: "11 to 15", state: "locked" }, { title: "16 to 20", state: "locked" }]).slice(0, 4);
    const pts = [[60, 30], [150, 92], [68, 152], [158, 212]];
    let svg = `<path class="track" d="M 60 30 Q 168 58 150 92 Q 122 124 68 152 Q 26 180 158 212"></path>`;
    units.forEach((u, i) => {
      const [x, y] = pts[i];
      const fill = u.state === "done" ? ["var(--green-lip)", "var(--green)"] : u.state === "current" ? ["var(--blue-lip)", "var(--blue)"] : ["var(--lock-lip)", "var(--lock)"];
      svg += `<circle cx="${x}" cy="${y + 4}" r="17" fill="${fill[0]}"></circle><circle cx="${x}" cy="${y}" r="17" fill="${fill[1]}"></circle>` +
             `<text x="${x + 28}" y="${y + 6}" class="${u.state === "locked" ? "lock-label" : ""}">${u.title}</text>`;
    });
    $("#minipath").innerHTML = svg;
    decorate($("#home"));
  }

  // ---------- learn: the showcase, four units, not wired to progress ----------
  const SHOWCASE = [
    { kicker: "Unit 1", name: "First steps", desc: "Meet your ten fingers", count: "15/15", tone: 0, mascot: "right",
      nodes: [{ t: "lesson", s: "done", stars: 3 }, { t: "lesson", s: "done", stars: 3 }, { t: "chest", s: "done" }, { t: "lesson", s: "done", stars: 2 }, { t: "boss", s: "done", stars: 3 }] },
    { kicker: "Unit 2", name: "6 to 10", desc: "The finger trick for 6 to 10", count: "7/15", tone: 1, mascot: "left",
      nodes: [{ t: "lesson", s: "done", stars: 3 }, { t: "lesson", s: "done", stars: 2 }, { t: "lesson", s: "current" }, { t: "chest", s: "locked" }, { t: "lesson", s: "locked" }, { t: "boss", s: "locked" }] },
    { kicker: "Unit 3", name: "11 to 15", desc: "Two hands and one carry", count: "0/15", tone: 2,
      nodes: [{ t: "lesson", s: "locked" }, { t: "lesson", s: "locked" }, { t: "lesson", s: "locked" }, { t: "chest", s: "locked" }, { t: "boss", s: "locked" }] },
    { kicker: "Unit 4", name: "16 to 20", desc: "The big ones", count: "0/15", tone: 3,
      nodes: [{ t: "lesson", s: "locked" }, { t: "lesson", s: "locked" }, { t: "lesson", s: "locked" }, { t: "boss", s: "locked" }] },
  ];
  function renderShowcase() {
    let html = "";
    SHOWCASE.forEach((u) => {
      const tone = TONES[u.tone % TONES.length];
      html += `<section class="unit" style="--unit:${tone[0]};--unit-lip:${tone[1]}"><div class="unit-banner"><div><div class="unit-kicker">${u.kicker}</div><h2>${u.name}</h2><p>${u.desc}</p></div><div class="unit-count">${use("icon-star")}${u.count}</div></div><div class="path">`;
      u.nodes.forEach((n, i) => {
        const cls = "node" + (n.t === "boss" ? " node-boss" : n.t === "chest" ? " node-chest" : "") + (n.s === "locked" ? " is-locked" : n.s === "done" ? " is-done" : " is-current");
        const face = n.t === "chest"
          ? use(iconFor(n.t, n.s), "0 0 88 80")
          : `<span class="art" style="background-image:url(art/${nodeArt(n.t, n.s)})"></span>`;
        html += `<div class="row"><div class="node-wrap" style="--x:${OFFSETS[i % OFFSETS.length]}px"><button class="${cls}" type="button" data-status="${n.s}" disabled>${face}</button>${miniStars(n.stars || 0)}</div></div>`;
      });
      if (u.mascot) html += `<div class="row"><div class="mascot mascot-${u.mascot}"><div class="tally" data-tally="${u.mascot === "right" ? "happy" : "ready"}"></div></div></div>`;
      html += `</div></section>`;
    });
    $("#showcase").innerHTML = html;
    decorate($("#learn"));
  }

  // ---------- learn map ----------
  const TONES = [["var(--green)", "var(--green-lip)"], ["var(--blue)", "var(--blue-lip)"], ["var(--purple)", "var(--purple-lip)"], ["var(--yellow)", "var(--yellow-lip)"]];
  const OFFSETS = [0, -58, -92, -58, 0, 58, 92, 58];

  function iconFor(kind, state) {
    if (kind === "chest") return state === "locked" ? "icon-chest-locked" : "icon-chest";
    if (kind === "boss") return state === "done" ? "icon-boss-done" : state === "locked" ? "icon-boss-locked" : "icon-boss";
    return state === "done" ? "icon-lesson-done" : state === "locked" ? "icon-lesson-locked" : "icon-lesson";
  }
  function miniStars(n) {
    if (!n) return `<div class="mini-stars"></div>`;
    return `<div class="mini-stars">${[0, 1, 2].map((i) => starSvg(i < n)).join("")}</div>`;
  }
  // one unit on screen at a time, the current one unless the learner switched.
  // The export's zigzag across the panel; the nodes are spread evenly down it, so a
  // unit of four fills the same panel a unit of six does.
  const SPOT_X = [22, 43, 64, 42, 23, 46];
  function spotsFor(count) {
    const top = 16, bottom = 86;
    return Array.from({ length: count }, (unused, i) => [
      SPOT_X[i % SPOT_X.length],
      count > 1 ? top + i * (bottom - top) / (count - 1) : (top + bottom) / 2,
    ]);
  }
  // the path furniture is rendered art, one image per node state. A chest has no
  // render in the export, so it keeps the drawn icon of icons.svg.
  function nodeArt(kind, state) {
    if (state === "locked") return "n-lock.png";
    if (state === "done") return "n-done.png";
    return kind === "boss" ? "n-boss.png" : "start.png";
  }
  function unitIndex() {
    if (unitAt !== null) return unitAt;
    const cur = L.currentNode(progress);
    return cur ? cur.unitIndex : L.UNITS.length - 1;
  }
  function renderCourse() {
    const ui = unitIndex();
    const unit = L.UNITS[ui];
    const tone = TONES[ui % TONES.length];
    const root = $("#practice");
    root.style.setProperty("--unit", tone[0]);
    root.style.setProperty("--unit-lip", tone[1]);
    const nodes = L.allNodes().filter((n) => n.unitId === unit.id);
    const scored = nodes.filter((n) => n.kind !== "chest");
    const done = scored.filter((n) => (progress.stars[n.id] || 0) >= 1).length;
    const cur = L.currentNode(progress);
    const here = cur && cur.unitId === unit.id;
    $("#crumb").textContent = unit.title;
    $("#u-kicker").textContent = `Unit ${ui + 1}`;
    $("#u-name").textContent = unit.title;
    $("#u-desc").textContent = unit.subtitle;
    $("#u-count").textContent = `${done} / ${scored.length}`;
    $("#u-bar").style.width = `${scored.length ? (done / scored.length) * 100 : 0}%`;
    const line = here
      ? (cur.kind === "chest" ? "A chest! Open it." : cur.kind === "boss" ? "The boss. Eight questions, three hearts." : `Next up: ${cur.title}.`)
      : done === scored.length ? "All done here. Replay for three stars." : "Finish the unit before this one first.";
    say($("#u-say"), line, $("#u-tally"));
    setTally($("#u-tally"), here ? "ready" : "happy");
    $("#unit-switch").innerHTML = L.UNITS.map((u, i) => `<button type="button" class="${i === ui ? "on" : ""}" data-action="unit" data-u="${i}" aria-label="Unit ${i + 1}"><img src="art/tile-${i + 1}-${i === ui ? "on" : "off"}.png" alt=""></button>`).join("");
    const pts = spotsFor(nodes.length);
    const d = pts.map(([x, y], i) => `${i ? "L" : "M"} ${x} ${y}`).join(" ");
    let html = `<svg class="track" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><path d="${d}"></path></svg>`;
    nodes.forEach((n, i) => {
      const [x, y] = pts[i];
      const state = L.nodeState(progress, n.id);
      const cls = "node" + (n.kind === "boss" ? " node-boss" : n.kind === "chest" ? " node-chest" : "") +
        (state === "locked" ? " is-locked" : state === "done" ? " is-done" : " is-current");
      const pos = `left:${x}%;top:${y}%`;
      // the current node is the way in: the render already says START, so the node
      // itself carries the action and no second button is stacked on top of it
      const action = state !== "current" ? "node" : n.kind === "chest" ? "chest" : "start";
      const face = n.kind === "chest"
        ? use(iconFor(n.kind, state), "0 0 88 80")
        : `<span class="art" style="background-image:url(art/${nodeArt(n.kind, state)})"></span>`;
      if (state === "current" && n.kind === "chest") {
        html += `<button class="bubble" type="button" data-action="chest" data-node="${n.id}" style="left:${x}%;top:calc(${y}% - 66px)">Open</button>`;
      }
      html += `<button class="${cls}" type="button" data-action="${action}" data-node="${n.id}" data-status="${state}" aria-label="${n.title}" style="${pos}">${face}</button>`;
      if (n.kind !== "chest" && progress.stars[n.id]) html += `<div class="mini-stars" style="${pos}">${[0, 1, 2].map((k) => starSvg(k < progress.stars[n.id])).join("")}</div>`;
    });
    $("#course").innerHTML = html;
    decorate(root);
  }
  // the Practice door: the start check once per visit, then the path
  function checked() { try { return sessionStorage.getItem("tenfold.checked") === "1"; } catch (e) { return false; } }

  // ---------- start check: three steps, the camera advances them ----------
  function startCheck() {
    if (checkRun) return;
    const root = $("#check");
    checkRun = { step: 1, lit: 0, timers: [], done: false, typed: "" };
    root.className = "checkview view";
    root.dataset.step = "1";
    $("#check-mic").hidden = true;
    checkSay("Show me both hands.");
    setTally($("#check-tally"), "ready");
    setStepDots(1);
    videoOn($("#check-cam .practice-video"));
    connect(() => sendLesson({ type: "start_node", tab: TAB, state: learner(), node: { id: "check", kind: "check", pairs: [[6, 6]], count: 1 } }));
    decorate(root);
  }
  function checkSay(text) { say($("#check-say"), text, $("#check-tally")); }
  function stopCheck() {
    if (!checkRun) return;
    checkRun.timers.forEach(clearTimeout);
    checkRun = null;
    gateVoice(false);
    videoOff($("#check-cam .practice-video"));
    link.start = null; link.waiting = [];
    sendLesson({ type: "quit" });
  }
  function setStepDots(n) {
    $$("#check .stepnum img").forEach((tile, i) => {
      const on = i === n - 1;
      tile.src = `art/tile-${i + 1}-${on ? "on" : "off"}.png`;
      tile.alt = on ? `Step ${i + 1}` : "";
    });
  }
  function later(ms, fn) { if (checkRun) checkRun.timers.push(setTimeout(fn, ms)); }
  function checkMessage(m) {
    const run = checkRun;
    const root = $("#check");
    if (!run || m.type !== "state") return;
    drawFingers(m, root);
    const hands = new Set((m.fingers || []).map((f) => f.hand)).size;
    if (run.step === 1) {
      if (hands === 2 && !root.classList.contains("detected")) {
        root.classList.add("detected");
        checkSay("There they are.");
        // the numbers light up one by one, ten down to six
        [10, 9, 8, 7, 6].forEach((n, i) => later(400 + i * 300, () => {
          $$("#check .practice-overlay [data-number]").forEach((el) => { if (Number(el.dataset.number) <= 10 && Number(el.dataset.number) >= n) el.classList.add("lit"); });
          run.lit = n;
        }));
        later(2300, () => { root.classList.add("check"); setTally($("#check-tally"), "happy"); });
        later(3300, () => {
          run.step = 2; root.dataset.step = "2"; root.classList.remove("check"); setStepDots(2);
          checkSay("Touch your 6 with your 6.");
          setTally($("#check-tally"), "thinking");
        });
      }
      if (hands === 2) $$("#check .practice-overlay [data-number]").forEach((el) => { if (run.lit && Number(el.dataset.number) >= run.lit) el.classList.add("lit"); });
      return;
    }
    if (run.step === 2) {
      if (m.state === "correct_pose" || m.state === "waiting_answer") {
        run.step = 3; root.dataset.step = "3"; root.classList.add("matched", "banner", "check");
        $("#check-banner").textContent = "That is a 6 and a 6.";
        checkSay("Six and six, touching.");
        setTally($("#check-tally"), "happy");
        later(1600, () => {
          root.classList.remove("check", "banner"); setStepDots(3);
          // no microphone in this browser, or one that was refused: say so and
          // show the digits in the pill, which is the only answer field here
          checkSay(canHear() ? "Say the answer." : "Type the answer.");
          $("#check-mic").hidden = false;
          $("#check-miclabel").textContent = micLabel();
          setTally($("#check-tally"), "ready");
          listenWhile("correct_pose");
        });
      } else if (m.state === "wrong_pose" && m.tally) {
        checkSay(m.tally);
        setTally($("#check-tally"), "almost");
      }
      return;
    }
    if (run.step === 3 && m.state === "answer_correct") {
      root.classList.add("heard", "result", "check");
      $("#check-result").textContent = m.answer;
      $("#check-miclabel").textContent = "Thirty six";
      checkSay("Thirty six. Exactly.");
      setTally($("#check-tally"), "happy");
      run.done = true;
      gateVoice(false);
    } else if (run.step === 3 && m.state === "answer_wrong" && m.tally) {
      checkSay(m.tally);
      setTally($("#check-tally"), "almost");
    }
  }
  function checkEnd() {
    const run = checkRun;
    if (!run) return;
    checkSay("Your path is open.");
    later(1400, () => {
      checkRun.timers.forEach(clearTimeout);
      checkRun = null;
      try { sessionStorage.setItem("tenfold.checked", "1"); } catch (e) { /* fine */ }
      go("practice");
    });
  }

  // ---------- Tally speaks ----------
  // Tally's voice: the best English voice this browser has, in a fixed preference order,
  // then any en-US voice that reads as female, then any English one. Voices arrive late
  // in Chrome, so the pick is redone on voiceschanged.
  const VOICE_PREFERENCE = ["Ava (Premium)", "Zoe (Premium)", "Samantha", "Karen"];
  const FEMALE_HINT = /ava|zoe|samantha|karen|allison|susan|nicky|zira|aria|jenny|michelle|emma|ana|joanna|kendra|salli|ivy|female|woman/i;
  const tallyVoice = { picked: null, done: false };
  function pickVoice() {
    if (!("speechSynthesis" in window)) return null;
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
  if ("speechSynthesis" in window && window.speechSynthesis.addEventListener) {
    window.speechSynthesis.addEventListener("voiceschanged", () => { tallyVoice.done = false; });
  }
  // short sentences, one utterance at a time; a new call drops whatever was still queued
  function sentencesOf(text) {
    return String(text || "").replace(/\s+/g, " ").trim().split(/(?<=[.!?])\s+/).map((s) => s.trim()).filter(Boolean);
  }
  const speech = { queue: [], host: null, serial: 0, pending: null, waiting: false };
  // Chrome hands the voice list over asynchronously: the first sentence waits for
  // voiceschanged (or a short timeout for browsers that never fire it) rather than
  // going out in the wrong voice.
  function voicesReady() {
    return tallyVoice.timedOut || (window.speechSynthesis.getVoices() || []).length > 0;
  }
  function waitForVoices() {
    if (speech.waiting) return;
    speech.waiting = true;
    const go = () => {
      if (!(window.speechSynthesis.getVoices() || []).length) tallyVoice.timedOut = true;
      const p = speech.pending; speech.pending = null;
      if (p) speak(p.text, p.host);
    };
    if (window.speechSynthesis.addEventListener) window.speechSynthesis.addEventListener("voiceschanged", go, { once: true });
    setTimeout(go, 1500);
  }
  function hush() {
    $$(".is-talking").forEach((el) => el.classList.remove("is-talking"));
    voice.spokeUntil = 0;
  }
  // ---------- one door for every visible Tally line ----------
  // Anything Tally shows, Tally says. Writing a bubble is the only way into speak(),
  // so a line cannot reach the screen without a voice: a new sentence anywhere in
  // this file goes through say() and is spoken by construction. Lines written in the
  // same breath are spoken as one queue, in the order they were written, and a bubble
  // never says again the line it is already showing.
  const saying = { lines: [], host: null, timer: null };
  function say(el, text, host) {
    if (!el) return "";
    if (text !== undefined) {
      const line = String(text === null ? "" : text);
      if (el.textContent !== line) el.textContent = line;
    }
    const shown = (el.textContent || "").replace(/\s+/g, " ").trim();
    if (!shown) { sayAgain(el); return ""; }
    if (el.dataset.said === shown) return shown;
    el.dataset.said = shown;
    // one utterance per bubble: a line with no full stop of its own gets one
    saying.lines.push(/[.!?]$/.test(shown) ? shown : `${shown}.`);
    if (host) saying.host = host;
    if (!saying.timer) saying.timer = setTimeout(flushSay, 0);
    return shown;
  }
  function flushSay() {
    saying.timer = null;
    const lines = saying.lines.splice(0), host = saying.host;
    saying.host = null;
    if (lines.length) speak(lines.join(" "), host);
  }
  // a bubble forgets what it said: the same line on a screen opened again is new
  function sayAgain(el) { if (el) delete el.dataset.said; }
  // The server's pedagogical clocks run on these: true before Tally's line starts,
  // false when it ends, errors, or is cancelled. The pair brackets the whole line,
  // not each sentence of it. A muted page, and a browser with no speech synthesis at
  // all, send the pair back to back, so the clock that starts at the end of the line
  // starts there too. Two false in a row are the same as one, so nothing is repeated.
  const tts = { speaking: false };
  function ttsSay(on) {
    const speaking = Boolean(on);
    if (speaking === tts.speaking) return;
    tts.speaking = speaking;
    sendLesson({ type: "tts", speaking });
  }
  function speak(text, host) {
    if (!text) return;
    if (!("speechSynthesis" in window)) { ttsSay(true); ttsSay(false); return; }
    try {
      window.speechSynthesis.cancel();
      speech.serial += 1;
      speech.queue = [];
      hush();
      ttsSay(false);
      if (muted) { ttsSay(true); ttsSay(false); return; }
      voice.said = numbersIn(text);
      voice.saidText = plainSpeech(text);
      if (!voicesReady()) { speech.pending = { text, host: host || null }; waitForVoices(); return; }
      speech.queue = sentencesOf(text);
      speech.host = host || null;
      speakNext(speech.serial);
    } catch (e) { /* the sentence is on screen anyway */ }
  }
  function speakNext(serial) {
    if (serial !== speech.serial) return;
    const talk = (on) => {
      if (speech.host) speech.host.classList.toggle("is-talking", on);
      voice.spokeUntil = on ? Infinity : Date.now() + 800;
    };
    const sentence = speech.queue.shift();
    if (!sentence) { talk(false); ttsSay(false); return; }
    const u = new SpeechSynthesisUtterance(sentence);
    u.lang = "en-US"; u.rate = 0.92; u.pitch = 1.05;
    const chosen = tallyVoice.done ? tallyVoice.picked : pickVoice();
    if (chosen) u.voice = chosen; else if (tallyVoice.timedOut) logVoice("browser default");
    u.onstart = () => { if (serial === speech.serial) talk(true); };
    u.onend = () => speakNext(serial);
    u.onerror = () => speakNext(serial);
    ttsSay(true);
    window.speechSynthesis.speak(u);
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
        const heard = $(".heard");
        if (heard) heard.textContent = text && voice.gate ? `heard: ${text}` : "";
        if (checkRun && checkRun.step === 3 && !checkRun.done) $("#check-miclabel").textContent = text || "Listening";
        if (e.results[i].isFinal) { sendSpeech(text); submitSpoken(text); }
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
  // the live listening state, on the lesson's mic and on the check's pill
  function markMic() {
    const mic = $(".mic");
    if (mic) {
      mic.hidden = !voice.recognition;
      mic.classList.toggle("is-on", voice.running && !voice.denied);
      mic.classList.toggle("is-off", !voice.running || voice.denied);
      mic.classList.toggle("is-denied", voice.denied);
      const label = $(".mic-label", mic);
      if (label) label.textContent = micLabel();
    }
    const pill = $("#check-mic");
    if (pill) {
      pill.classList.toggle("is-off", !voice.running || voice.denied);
      if (checkRun && checkRun.step === 3 && !checkRun.done && !(checkRun.typed || "")) $("#check-miclabel").textContent = micLabel();
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
  function submitSpoken(text) {
    const value = parseNumber(text);
    if (value === null || (!lesson && !checkRun) || !voice.gate) return;
    // Tally's own voice is not an answer: a number he just said is dropped while he says it
    if (Date.now() < voice.spokeUntil && voice.said.includes(value)) return;
    const now = Date.now();
    if (value === voice.last && now - voice.at < 1500) return;
    voice.last = value; voice.at = now;
    setTyped(String(value).slice(0, 3));
    sendLesson({ type: "check", value });
  }

  // ---------- socket ----------
  // One socket for the page. A handshake that never completes is given up on, a
  // closed socket is reopened with a growing wait, and whatever asked for the last
  // connection is replayed once it is back. A lesson that cannot reach the server
  // says so instead of sitting on "Getting ready" for ever.
  const LINK_SAY = "I cannot reach the camera. Trying again.";
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
      const sub = $("#lesson .speech-sub");
      if (sub) sub.textContent = "Trying again";
      say($("#lesson .tally-say"), LINK_SAY, $("#lesson .tally"));
    } else if (checkRun) {
      checkSay(LINK_SAY);
    }
    toast(LINK_SAY);
  }
  function sendLesson(payload) { if (socket && socketReady) socket.send(JSON.stringify(payload)); }
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
      if (forCheck) return checkEnd();
      lesson.serverCorrect = m.correct; lesson.total = m.total || lesson.total; lesson.endTally = m.tally;
      return finish(false);
    }
    if (m.type !== "state") return;
    if (checkRun && m.node === "check") return checkMessage(m);
    if (!lesson || m.node !== lesson.node.id) return;
    const fresh = m.state !== lesson.last;
    // A new exercise, not a new fact: a node can serve the same fact five times over
    // (u1-l2 is 6x7 both ways), and only the engine's rearm to exercise_shown moves
    // once per question. That is the counter and the clean first try again.
    if (fresh && m.state === "exercise_shown") lesson.done += 1;
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
    lesson = { node, total, correct: 0, firstTry: 0, serverCorrect: null, done: 0, fact: null, last: null,
      hearts: node.kind === "boss" ? 3 : null, hit: false, typed: "", paid: false, t0: Date.now() };
    stopCheck();
    VIEWS.forEach((v) => { $("#" + v).hidden = true; });
    $("#finish").hidden = true;
    $("#lesson").hidden = false;
    document.body.dataset.view = "lesson";
    setupVoice();
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
    s.done = 0; s.serverCorrect = null; s.fact = null; s.last = null;
    s.hearts = s.node.kind === "boss" ? 3 : null; s.hit = false; s.typed = "";
    // the node starts again on the same shell: its first line is a new line
    sayAgain($("#lesson .tally-say"));
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
  function shellPractice() {
    const s = lesson;
    const hearts = s.hearts === null ? "" : `<div class="hearts" aria-label="${s.hearts} hearts">${use("icon-flame")}${s.hearts}</div>`;
    $("#lesson").innerHTML = `
      <div class="lesson-top">
        <button class="backbtn2 lesson-back" data-action="quit" aria-label="Quit lesson"><img src="art/btn-back.png" alt=""></button>
        <div class="bar lesson-bar"><i class="bar-fill" style="width:0%"></i></div>
        ${hearts}<div class="lesson-count practice-why"></div>
      </div>
      <div class="cl-body">
        <p class="cl-exercise practice-exercise">Getting ready</p>
        <div class="cam cl-cam">
          <div class="practice-stage" data-state="">
            <img class="practice-video" alt="">
            <svg class="practice-overlay" viewBox="0 0 1.333 1" preserveAspectRatio="none"></svg>
          </div>
          <span class="cam-tag">${use("icon-camera")}camera</span>
          <div class="cam-veil">I cannot quite see your fingers<small>Hold them up, palms toward me</small></div>
          <div class="reasons practice-reasoning"></div>
          <div class="rescue practice-rescue" hidden></div>
        </div>
        <div class="cl-foot">
          <!-- Tally and his bubble are the ask for help: a tap on either sends hint,
               which is the only help that costs the first try bonus. No new button. -->
          <div class="speaker" data-action="help" role="button" aria-label="Ask Tally for help">
            <div class="tally" data-tally="ready"></div>
            <div class="speech"><span class="tally-say"></span><small class="speech-sub">I am watching your fingers</small></div>
          </div>
          <div class="practice-answer">
            <button class="mic" type="button" data-action="mic" hidden aria-label="say the answer">${use("icon-mic")}<span class="bars"><i></i><i></i><i></i></span><span class="mic-label">Listening</span></button>
            <span class="typed"></span><span class="caret"></span>
            <div class="heard"></div>
          </div>
        </div>
      </div>`;
    decorate($("#lesson"));
    // the first line of the lesson is shown and said like every other line
    say($("#lesson .tally-say"), "Show me both hands.", $("#lesson .tally"));
    videoOn($(".practice-video", $("#lesson")));
    markMic();
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
  // object-fit did with it (contain letterboxes, cover crops). Landmarks are frame fractions,
  // so a viewBox of ratio x 1 maps them straight through. The lesson box also takes the
  // camera's ratio so there is no letterbox to begin with.
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
    overlay.setAttribute("viewBox", `0 0 ${ratio} 1`);
    overlay.style.cssText = `left:${video.offsetLeft + (W - w) / 2}px;top:${video.offsetTop + (H - h) / 2}px;width:${w}px;height:${h}px`;
    return ratio;
  }
  function renderPractice(m) {
    const s = lesson, root = $("#lesson");
    const bar = $(".bar-fill", root);
    if (bar) bar.style.width = `${Math.min(100, (s.done ? s.done - 1 : 0) / s.total * 100)}%`;
    const hearts = $(".hearts", root);
    if (hearts && s.hearts !== null) {
      hearts.innerHTML = use("icon-flame") + s.hearts; decorate(hearts);
      if (s.hit) { hearts.classList.add("is-hit"); setTimeout(() => hearts.classList.remove("is-hit"), 420); s.hit = false; }
    }
    $(".practice-exercise", root).textContent = (m.exercise || "").replace(" x ", " × ") || "Getting ready";
    $(".practice-why", root).textContent = s.done ? `${Math.min(s.done, s.total)} of ${s.total}` : "";
    // tutor_line is the line Tally says and shows; tally is the fallback when the
    // tutor has nothing new. Shown and spoken in one move, once per line.
    say($(".tally-say", root), m.tutor_line || m.tally || "", $(".tally", root));
    $(".speech-sub", root).textContent = m.reaction === "cannot_see" ? "Palms toward the camera" : m.reason ? ({ retry: "One more try", review: "You have met this one", confidence: "An easy one", next_new: "Brand new", level_up: "Jumping ahead" }[m.reason] || "") : "I am watching your fingers";
    setTally($(".tally", root), m.reaction === "cannot_see" ? "squint" : (MOOD[m.state] || "ready"));
    root.dataset.state = m.state;
    root.dataset.reaction = m.reaction || "";
    $(".practice-stage", root).dataset.state = m.state;
    const band = $(".reasons", root);
    const lines = m.reasoning || [];
    root.classList.toggle("has-reasons", lines.length > 0);
    band.innerHTML = lines.map((line) => `<span>${line}</span>`).join("");
    renderRescue(m, root);
    drawFingers(m, $(".practice-stage", root));
  }
  // ---------- what the tutor draws ----------
  // tutor_visual is null or one of six kinds. Everything here is an addition inside
  // the camera view, in the language the page already draws in: the same fingertip
  // circles, the same gold ghost, the same dashed zones as the start check.
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
  function renderRescue(m, root) {
    const box = $(".practice-rescue", root);
    if (!box) return;
    const visual = m.tutor_visual || null;
    const card = visual && visual.kind === "rescue_card" ? visual : null;
    box.hidden = !card;
    if (!card) { box.dataset.card = ""; box.replaceChildren(); return; }
    const lines = rescueLines(card, m.exercise);
    if (box.dataset.card === lines.join("|")) return;
    box.dataset.card = lines.join("|");
    box.replaceChildren();
    lines.forEach((line) => { const el = document.createElement("span"); el.textContent = line; box.appendChild(el); });
  }
  // a ring carries a CSS animation, so one already pointing at the same finger is
  // kept rather than rebuilt: a new element every frame would restart the animation
  function ringAt(kept, kind, id, x, y) {
    const tag = `${kind}:${id}`;
    const ring = kept[tag] || document.createElementNS(NS, "circle");
    ring.setAttribute("class", `ring ${kind}`);
    ring.dataset.ring = tag;
    ring.setAttribute("cx", x); ring.setAttribute("cy", y); ring.setAttribute("r", 0.055);
    return ring;
  }
  // the two hand zones of SPEC section 9, in the dashed style of the start check
  function placementZones(overlay, span) {
    [0.3, 0.7].forEach((centre) => {
      const zone = document.createElementNS(NS, "rect");
      zone.setAttribute("class", "zone-box");
      zone.setAttribute("x", (centre - 0.15) * span); zone.setAttribute("y", 0.275);
      zone.setAttribute("width", 0.3 * span); zone.setAttribute("height", 0.55);
      zone.setAttribute("rx", 0.06);
      overlay.appendChild(zone);
    });
  }
  // the wrong hand, outlined where it is. Nothing is drawn on the hand that is right.
  function handOutline(overlay, fingers) {
    if (!fingers.length) return;
    const xs = fingers.map((f) => f.x), ys = fingers.map((f) => f.y), pad = 0.06;
    const box = document.createElementNS(NS, "rect");
    box.setAttribute("class", "hand-box");
    box.setAttribute("x", Math.min.apply(null, xs) - pad); box.setAttribute("y", Math.min.apply(null, ys) - pad);
    box.setAttribute("width", Math.max.apply(null, xs) - Math.min.apply(null, xs) + pad * 2);
    box.setAttribute("height", Math.max.apply(null, ys) - Math.min.apply(null, ys) + pad * 2);
    box.setAttribute("rx", 0.06);
    overlay.appendChild(box);
  }
  function drawFingers(m, stage) {
    const overlay = $(".practice-overlay", stage);
    if (!overlay) return;
    const span = fitOverlay(stage);
    const kept = {};
    $$(".ring", overlay).forEach((el) => { kept[el.dataset.ring] = el; });
    overlay.replaceChildren();
    const key = (f) => `${f.hand}:${f.number}`;
    const wrong = new Set((m.wrong || []).map(key));
    const match = new Set((m.match || []).map(key));
    const visual = m.tutor_visual || null;
    const kind = visual ? visual.kind : null;
    const ghost = kind === "ghost" && visual.hand && visual.to ? `${visual.hand}:${visual.to}` : null;
    const pulse = kind === "pulse_finger" && visual.hand && visual.finger ? `${visual.hand}:${visual.finger}` : null;
    const expect = kind === "correction" && visual.wrong_hand && visual.expected_finger ? `${visual.wrong_hand}:${visual.expected_finger}` : null;
    const fingers = m.fingers || [];
    if (kind === "placement_zones") placementZones(overlay, span);
    if (kind === "correction") {
      handOutline(overlay, fingers.filter((f) => f.hand === visual.wrong_hand).map((f) => ({ x: f.x * span, y: f.y })));
    }
    for (const finger of fingers) {
      const id = key(finger);
      const colour = wrong.has(id) ? "#f79433" : match.has(id) ? "#2fae82" : "#f4f6fd";
      if (id === ghost) overlay.appendChild(ringAt(kept, "ghost", id, finger.x * span, finger.y));
      if (id === pulse) overlay.appendChild(ringAt(kept, "pulse", id, finger.x * span, finger.y));
      const c = document.createElementNS(NS, "circle");
      c.setAttribute("cx", finger.x * span); c.setAttribute("cy", finger.y); c.setAttribute("r", 0.036);
      c.setAttribute("fill", "rgba(42,48,80,0.78)"); c.setAttribute("stroke", colour); c.setAttribute("stroke-width", 0.006);
      c.dataset.number = finger.number;
      overlay.appendChild(c);
      if (id === expect) {
        const dot = document.createElementNS(NS, "circle");
        dot.setAttribute("class", "dot");
        dot.setAttribute("cx", finger.x * span); dot.setAttribute("cy", finger.y - 0.052); dot.setAttribute("r", 0.014);
        overlay.appendChild(dot);
      }
      const t = document.createElementNS(NS, "text");
      t.setAttribute("x", finger.x * span); t.setAttribute("y", finger.y + 0.019); t.setAttribute("fill", colour);
      if (kind === "finger_numbers") t.setAttribute("class", "lit");
      t.dataset.number = finger.number; t.textContent = finger.number;
      overlay.appendChild(t);
    }
  }
  function setTyped(value) {
    if (!lesson && !checkRun) return;
    if (lesson) lesson.typed = value;
    const el = $(".typed"); if (el) el.textContent = value;
    if (checkRun && !lesson) {
      // the check has no answer field of its own: the pill carries the digits, and
      // it has to follow a Backspace back down to the label
      checkRun.typed = value;
      const label = $("#check-miclabel");
      if (label && !checkRun.done) label.textContent = value || micLabel();
    }
  }

  // ---------- finish ----------
  function finish(outOfHearts) {
    const s = lesson;
    gateVoice(false);
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
    const sub = fail ? `Get ${need} of ${total} right to earn a star and open the next lesson.` : s.endTally || (res.unlocked ? "The next lesson is open." : "Come back for all three stars.");
    $("#finish").className = `finish${fail ? " is-fail" : ""}`;
    $("#finish").innerHTML = `
      <div class="fn-pane" id="fn-1">
        <div class="tally" data-tally="${fail ? "almost" : "happy"}" data-accessories="${before.accessories}"></div>
        <h1>${title}</h1>
        <p class="sub">${sub}</p>
        <div class="fn-stars">${[0, 1, 2].map((i) => starSvg(i < res.stars)).join("")}</div>
        <div class="fn-gain">${use("icon-xp")}<span id="fn-gain">+0</span></div>
        <div class="fn-lvl">
          <div class="fn-lvlhead"><b id="fn-n1">${before.name}</b><span id="fn-x1">${levelLabel(before)}</span></div>
          <div class="fn-track"><i id="fn-f1" style="width:${before.percent}%;transition:none"></i></div>
        </div>
        <div class="btn-row">${fail
          ? `<button class="btn2 red" data-action="retry" data-node="${s.node.id}">Try again</button><button class="btn2 ghost" data-action="home">Back to the path</button>`
          : `<button class="btn2" data-action="home">Continue</button>`}</div>
      </div>
      <div class="fn-pane in" id="fn-2">
        ${confetti()}
        <div class="tally" data-tally="happy" data-accessories="${after.accessories}"></div>
        <div class="newlevel">Level ${after.level}</div>
        <h1>${after.name}</h1>
        ${earned.length ? `<p class="earned">Tally got ${earned.map(accessoryText).join(" and ")}</p>` : `<p class="sub">Tally is proud of you.</p>`}
        <div class="fn-lvl">
          <div class="fn-lvlhead"><b>${after.name}</b><span>${levelLabel(after)}</span></div>
          <div class="fn-track"><i id="fn-f2" style="width:0%"></i></div>
        </div>
        <div class="btn-row"><button class="btn2" data-action="home">Continue</button><button class="btn2 ghost" data-action="profile">See Tally</button></div>
      </div>`;
    videoOff($(".practice-video", $("#lesson")));
    $("#lesson").hidden = true;
    $("#finish").hidden = false;
    document.body.dataset.view = "finish";
    decorate($("#finish"));
    // the card is written by the template above: Tally says exactly what it shows
    say($("#fn-1 h1"), undefined, $("#fn-1 .tally"));
    say($("#fn-1 .sub"), undefined, $("#fn-1 .tally"));
    countUp(gain, before, after);
  }
  // the XP counts up over a second, the bar and the level name follow; then the level up card
  function countUp(gain, before, after) {
    const el = $("#fn-gain"), fill = $("#fn-f1"), nameEl = $("#fn-n1"), xpEl = $("#fn-x1");
    const t0 = performance.now(), dur = 1100;
    function tick(now) {
      if (!el || !el.isConnected) return;
      const k = Math.min(1, (now - t0) / dur);
      const shown = Math.round(gain * (1 - Math.pow(1 - k, 3)));
      const info = L.levelInfo(before.xp + shown);
      el.textContent = `+${shown}`;
      nameEl.textContent = info.name; xpEl.textContent = levelLabel(info); fill.style.width = `${info.percent}%`;
      if (k < 1) requestAnimationFrame(tick);
      else if (after.level > before.level) setTimeout(levelUp, 1000);
    }
    requestAnimationFrame(tick);
  }
  function levelUp() {
    const a = $("#fn-1"), b = $("#fn-2");
    if (!a || !b || $("#finish").hidden) return;
    a.classList.add("out"); b.classList.add("show");
    setTimeout(() => { const f = $("#fn-f2"); if (f) f.style.width = `${L.levelInfo(xp()).percent}%`; }, 500);
    // the level up card, line by line as it is shown: the level, the name, the reward
    const host = $("#fn-2 .tally");
    say($("#fn-2 .newlevel"), undefined, host);
    say($("#fn-2 h1"), undefined, host);
    say($("#fn-2 .earned") || $("#fn-2 .sub"), undefined, host);
  }
  // glasses are the one plural: "Tally got glasses", "Tally got a wizard hat"
  function accessoryText(key) {
    const label = L.ACCESSORY_NAMES[key];
    return key === "glasses" ? `<b>${label}</b>` : `a <b>${label}</b>`;
  }
  function confetti() {
    const colors = ["#2563d9", "#36a9f5", "#2fae82", "#ef5a4f", "#f8c62c", "#8b6ee0"];
    let out = `<div class="confetti" aria-hidden="true">`;
    for (let i = 0; i < 46; i++) {
      out += `<i style="left:${Math.random() * 100}%;background:${colors[i % colors.length]};animation-duration:${1.8 + Math.random() * 1.6}s;animation-delay:${Math.random() * 0.7}s;--dx:${Math.round(Math.random() * 160 - 80)}px;--rot:${Math.round(Math.random() * 720 - 360)}deg"></i>`;
    }
    return out + "</div>";
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
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    // a line cut off on the way out still closes its pair, or the next one would
    // open without one and the tutor's clock would never start
    ttsSay(false);
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
  function renderProfile() {
    const info = L.levelInfo(xp());
    const who = name();
    $("#pf-who").textContent = who ? `${who} and Tally` : "You and Tally";
    $("#pf-level").textContent = info.level;
    $("#pf-name").textContent = info.name;
    $("#pf-xp").textContent = info.next ? `${info.inLevel} / ${info.span}` : `${info.xp} XP`;
    $("#pf-fill").style.width = `${info.percent}%`;
    $("#pf-next").textContent = info.next ? `${info.toNext} XP to ${info.next}` : "Top level. Tally has everything.";
    // the gear row: what Tally wears now, and what is still waiting for him
    const worn = new Set(info.accessories.split(" ").filter(Boolean));
    $("#pf-wardrobe").innerHTML = accessoryLevels().map((level) => {
      const acc = L.ACCESSORIES[level];
      const wear = L.ACCESSORY_NAMES[acc];
      const on = worn.has(acc);
      return `<span class="slot${on ? " on" : ""}" title="${on ? wear : `${wear} at level ${level}`}"><img src="art/acc-${acc}.png" alt="${wear}"></span>`;
    }).join("");
    $("#pf-list").innerHTML = L.LEVELS.map((lv, i) => {
      const n = i + 1;
      const acc = L.earnedAt(n);
      const wear = acc ? L.ACCESSORY_NAMES[acc] : "";
      const cls = n < info.level ? " got" : n === info.level ? " now" : "";
      const item = acc ? `<img class="item" src="art/acc-${acc}.png" alt="${wear}" title="${wear}">` : "";
      const tail = n <= info.level
        ? `${n === info.level ? `<span class="tag">You are here</span>` : ""}<span class="mark">${use("icon-check")}</span>`
        : `<span class="tag">${lv.at} XP</span>`;
      return `<div class="pf-row${cls}"><span class="no">${n}</span><span class="nm">${lv.name}</span><span class="pf-side">${item}${tail}</span></div>`;
    }).join("");
    decorate($("#profile"));
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
    else if (action === "start") startLesson(id);
    else if (action === "chest") openChest(id);
    else if (action === "unit") { unitAt = Number(el.dataset.u); renderCourse(); }
    else if (action === "profile") { leaveLesson(); go("profile"); }
    else if (action === "mute") setMuted(!muted);
    // help the child asked for, the only help that costs the first try bonus
    else if (action === "help") { if (lesson) sendLesson({ type: "hint" }); }
    // a refusal is not final: an explicit tap on the mic asks the browser again
    else if (action === "mic") {
      if (voice.denied) { voice.denied = false; voice.wanted = true; startVoice(); }
      else if (voice.wanted) stopVoice();
      else { voice.wanted = true; startVoice(); }
      markMic();
    }
    else if (action === "quit" || action === "home") backToMap();
    else if (action === "retry") startLesson(id);
    else if (action === "reset") { progress = L.emptyProgress(); unitAt = null; save(); try { localStorage.removeItem(KEY_LEARNER); } catch (err) { /* fine */ } renderCourse(); toast("Progress reset"); }
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
    const answering = (lesson && !$("#lesson").hidden) || (checkRun && checkRun.step === 3);
    if (!answering) {
      if (e.key === "Enter" && !$("#finish").hidden) { const btn = $("#finish .fn-pane:not(.out) .btn2"); if (btn) btn.click(); }
      return;
    }
    if (inField) return;
    const typed = lesson ? lesson.typed : (checkRun.typed || "");
    if (e.key >= "0" && e.key <= "9") { if (typed.length < 3) setTyped(typed + e.key); }
    else if (e.key === "Backspace") setTyped(typed.slice(0, -1));
    else if (e.key === "Enter") { e.preventDefault(); if (!typed.length) return; sendLesson({ type: "check", value: Number(typed) }); setTyped(""); }
    else if (e.key === "n" && lesson) { setTyped(""); sendLesson({ type: "next" }); }
    else if (e.key === "Escape") backToMap();
  });
  // the first click of the session is the gesture Chrome needs to open the mic
  document.addEventListener("click", armVoice, { capture: true });
  window.addEventListener("hashchange", route);
  window.addEventListener("resize", () => { if (lesson) fitOverlay($("#lesson .practice-stage")); if (checkRun) fitOverlay($("#check-cam")); });

  // ---------- boot ----------
  function watchCamera() {
    const art = $(".panel-camera-art"), copy = $(".panel-camera h3"), note = $(".panel-camera p");
    if (!art) return;
    const probe = new Image();
    probe.onload = () => {
      art.innerHTML = ""; art.appendChild(probe); art.classList.add("is-live");
      if (copy) copy.textContent = "Tally is watching";
      if (note) note.textContent = "Your hands are on camera. Start a lesson and count with them.";
    };
    probe.alt = ""; probe.src = "/video";
  }
  if (params.get("dev") === "1") $$(".dev-reset").forEach((el) => { el.hidden = false; });
  window.Tenfold = { parseNumber, numbersIn, sentencesOf, pickVoice, speak, setMuted, voice, fitOverlay, get muted() { return muted; }, get lesson() { return lesson; }, get xp() { return xp(); } };
  route();
  markMute();
  watchCamera();
  decorate(document);
  // the demo flag arrives on the socket; connect early so the showcase seeds before the
  // map is opened. No callback: whatever the first view asked for is what runs on open.
  connect();
})();
