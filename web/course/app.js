/*
 * Tenfold, the tablet app. One page, several views: welcome, home, the learn
 * showcase, the practice path, the start check, the camera lesson, finish,
 * castle, profile.
 *
 * Structure and progress come from levels.js and nothing else. The camera lesson
 * is driven by app/server.py over the websocket, scoped to a node's pairs by
 * lesson/scheduler.py; this file renders, counts and stores. Tally is drawn from
 * tally.svg through tally.js, the node icons from icons.svg. No em dashes anywhere.
 */
(function () {
  "use strict";
  const L = window.TenfoldLevels;
  const KEY = "tenfold.levels.v1";
  const KEY_NAME = "tenfold.name";
  const KEY_LEARNER = "tenfold.learner";
  const params = new URLSearchParams(location.search);
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.prototype.slice.call((root || document).querySelectorAll(sel));
  const NS = "http://www.w3.org/2000/svg";

  // ---------- storage ----------
  function today() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }
  function read(key, fallback) {
    try { const raw = localStorage.getItem(key); return raw ? JSON.parse(raw) : fallback; }
    catch (e) { return fallback; }
  }
  function write(key, value) {
    try { localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value)); } catch (e) { /* keep going in memory */ }
  }
  function load() { return Object.assign(L.emptyProgress(), read(KEY, {})); }
  function save() { write(KEY, progress); }
  function name() { try { return localStorage.getItem(KEY_NAME) || ""; } catch (e) { return ""; } }
  function learner() { return read(KEY_LEARNER, null); }

  let progress = load();
  let openNode = null;
  let lesson = null;
  let justUnlocked = null;
  let checkRun = null;
  let demoSeeded = false;

  // ---------- icons and Tally ----------
  const use = (id, box) => `<svg viewBox="${box || "0 0 48 48"}" aria-hidden="true"><use href="#${id}"></use></svg>`;
  function starSvg(on) { return `<svg viewBox="0 0 48 48" class="${on ? "" : "off"}" style="color:${on ? "var(--gold)" : "var(--line)"}"><use href="#icon-star"></use></svg>`; }
  function decorate(root) {
    if (window.Icons) window.Icons.expand(root || document);
    if (window.Tally) window.Tally.mount(root || document);
  }
  function setTally(host, expr) {
    if (!host) return;
    if (host.getAttribute("data-tally") === expr) return;
    if (window.Tally) window.Tally.set(host, expr); else host.setAttribute("data-tally", expr);
  }

  // ---------- router ----------
  const VIEWS = ["welcome", "home", "learn", "practice", "check", "board", "profile"];
  let view = "welcome";
  function show(name) {
    view = name;
    VIEWS.forEach((v) => { const el = document.getElementById(v); if (el) el.hidden = v !== name; });
    document.body.dataset.view = name;
    $("#lesson").hidden = true;
    $("#finish").hidden = true;
    if (name !== "check") stopCheck();
    if (name === "home") renderHome();
    if (name === "learn") renderShowcase();
    if (name === "practice") renderAll();
    if (name === "board") renderBoard();
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
    write(KEY_NAME, value);
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
      const fill = u.state === "done" ? ["#255a56", "#2f6f6a"] : u.state === "current" ? ["#2f5e1e", "#3f7d2a"] : ["#c9c0ae", "#e2dbcd"];
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
        const ring = n.s === "current" ? `<svg class="ring" viewBox="0 0 102 102"><circle cx="51" cy="51" r="44"></circle><circle class="ring-arc" cx="51" cy="51" r="44" stroke-dasharray="276" stroke-dashoffset="186"></circle></svg>` : "";
        const bubble = n.s === "current" ? `<div class="bubble">Start</div>` : "";
        html += `<div class="row"><div class="node-wrap" style="--x:${OFFSETS[i % OFFSETS.length]}px">${bubble}<button class="${cls}" type="button" data-status="${n.s}" disabled>${use(iconFor(n.t, n.s), n.t === "chest" ? "0 0 88 80" : "0 0 48 48")}</button>${ring}${miniStars(n.stars || 0)}</div></div>`;
      });
      if (u.mascot) html += `<div class="row"><div class="mascot mascot-${u.mascot}"><div class="tally" data-tally="${u.mascot === "right" ? "happy" : "ready"}"></div></div></div>`;
      html += `</div></section>`;
    });
    $("#showcase").innerHTML = html;
    $("#showcase-stats").innerHTML = `<div class="stat stat-streak">${use("icon-flame")}6</div><div class="stat stat-xp">${use("icon-xp")}240</div><div class="stat stat-stars">${use("icon-star")}22</div>`;
    $("#showcase-quest").innerHTML = `<div class="quest-head"><h3>Daily quest</h3><span>1 of 3</span></div><div class="quest-row">${use("icon-xp")}<div style="flex:1"><div class="quest-title">Earn 30 XP</div><div class="bar"><i class="bar-fill" style="width:80%"></i><span class="bar-label">24 / 30</span></div></div></div>`;
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
  function popover(node, state) {
    if (state === "locked") return `<div class="popover is-locked"><h4>${node.kind === "boss" ? "Boss" : node.title}</h4><p>Finish everything above to unlock this.</p></div>`;
    if (node.kind === "chest") return `<div class="popover"><h4>Treasure chest</h4><p>A reward for getting this far.</p><button class="btn btn-primary btn-wide" data-action="chest" data-node="${node.id}">Open +${L.CHEST_XP} XP</button></div>`;
    const count = node.kind === "boss" ? L.BOSS_QUESTIONS : L.LESSON_QUESTIONS;
    const facts = node.pairs.slice(0, 4).map(([a, b]) => `${a} × ${b}`).join(" · ");
    const best = progress.stars[node.id] || 0;
    const label = state === "done" ? (best < 3 ? "Practice +XP" : "Replay") : `Start +${count * L.XP_PER_CORRECT} XP`;
    return `<div class="popover"><h4>${node.kind === "boss" ? "Boss fight" : node.title}</h4><p>${node.kind === "boss" ? `${count} exercises, three hearts` : facts}</p><button class="btn btn-primary btn-wide" data-action="start" data-node="${node.id}">${label}</button></div>`;
  }
  function nodeHtml(node, state, i, roadmap) {
    const box = node.kind === "chest" ? "0 0 88 80" : "0 0 48 48";
    const cls = "node" + (node.kind === "boss" ? " node-boss" : node.kind === "chest" ? " node-chest" : "") +
      (state === "locked" ? " is-locked" : state === "done" ? " is-done" : " is-current");
    const ring = state === "current" ? `<svg class="ring" viewBox="0 0 102 102"><circle cx="51" cy="51" r="44"></circle><circle class="ring-arc" cx="51" cy="51" r="44" stroke-dasharray="276" stroke-dashoffset="186"></circle></svg>` : "";
    const open = !roadmap && openNode === node.id;
    const bubble = state === "current" && !open ? `<div class="bubble">Start</div>` : "";
    const stars = node.kind === "chest" ? `<div class="mini-stars"></div>` : miniStars(progress.stars[node.id] || 0);
    const button = roadmap
      ? `<button class="${cls}" type="button" data-status="locked" disabled>${use(iconFor(node.kind, "locked"), box)}</button>`
      : `<button class="${cls}" type="button" data-action="node" data-node="${node.id}" data-status="${state}" aria-label="${node.title}">${use(iconFor(node.kind, state), box)}</button>`;
    return `<div class="row${open ? " is-open" : ""}"><div class="node-wrap" style="--x:${OFFSETS[i % OFFSETS.length]}px">${bubble}${button}${ring}${stars}${open ? popover(node, state) : ""}</div></div>`;
  }
  function renderCourse() {
    const nodes = L.allNodes();
    let html = "";
    L.UNITS.forEach((unit, ui) => {
      const unitNodes = nodes.filter((n) => n.unitId === unit.id);
      const scored = unitNodes.filter((n) => n.kind !== "chest");
      const got = scored.reduce((s, n) => s + (progress.stars[n.id] || 0), 0);
      const tone = TONES[ui % TONES.length];
      html += `<section class="unit" style="--unit:${tone[0]};--unit-lip:${tone[1]}"><div class="unit-banner"><div><div class="unit-kicker">Unit ${ui + 1}</div><h2>${unit.title}</h2><p>${unit.subtitle}</p></div><div class="unit-count">${use("icon-star")}${got}/${scored.length * 3}</div></div><div class="path">`;
      unitNodes.forEach((n, i) => { html += nodeHtml(n, L.nodeState(progress, n.id), i, false); });
      if (ui < 2) html += `<div class="row"><div class="mascot mascot-${ui ? "left" : "right"}"><div class="tally" data-tally="${ui ? "ready" : "happy"}"></div></div></div>`;
      html += `</div></section>`;
    });
    $("#course").innerHTML = html;
    decorate($("#course"));
  }
  function toggleNode(id) {
    openNode = openNode === id ? null : id;
    renderCourse();
  }
  function renderStats() {
    const t = L.totals(progress);
    const q = L.dailyQuest(progress, today());
    $("#stats").innerHTML = `
      <div class="stat stat-streak">${use("icon-flame")}${progress.streak}</div>
      <div class="stat stat-xp">${use("icon-xp")}${progress.xp}</div>
      <div class="stat stat-stars">${use("icon-star")}${t.stars}</div>`;
    $("#quest").innerHTML = `
      <div class="quest-head"><h3>Daily quest</h3><span>${q.value >= q.goal ? "done" : "today"}</span></div>
      <div class="quest-row">${use("icon-xp")}<div style="flex:1"><div class="quest-title">Earn ${q.goal} XP</div>
        <div class="bar"><i class="bar-fill" style="width:${(q.value / q.goal) * 100}%"></i><span class="bar-label">${q.value} / ${q.goal}</span></div></div></div>`;
    decorate($("#practice .right"));
  }
  function renderAll() { renderStats(); renderCourse(); }
  // the Practice door: the start check once per visit, then the path
  function checked() { try { return sessionStorage.getItem("tenfold.checked") === "1"; } catch (e) { return false; } }

  // ---------- start check: three steps, the camera advances them ----------
  function startCheck() {
    if (checkRun) return;
    const root = $("#check");
    checkRun = { step: 1, lit: 0, timers: [], done: false };
    root.className = "checkview view";
    root.dataset.step = "1";
    $("#check-mic").hidden = true;
    $("#check-say").textContent = "Show me both hands.";
    setTally($("#check-tally"), "ready");
    setStepDots(1);
    connect(() => sendLesson({ type: "start_node", state: learner(), node: { id: "check", kind: "check", pairs: [[6, 6]], count: 1 } }));
    decorate(root);
  }
  function stopCheck() {
    if (!checkRun) return;
    checkRun.timers.forEach(clearTimeout);
    checkRun = null;
    stopVoice();
    sendLesson({ type: "quit" });
  }
  function setStepDots(n) {
    $$("#check .stepnum i").forEach((dot, i) => dot.classList.toggle("on", i === n - 1));
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
        $("#check-say").textContent = "There they are.";
        // the numbers light up one by one, ten down to six
        [10, 9, 8, 7, 6].forEach((n, i) => later(400 + i * 300, () => {
          $$("#check .practice-overlay [data-number]").forEach((el) => { if (Number(el.dataset.number) <= 10 && Number(el.dataset.number) >= n) el.classList.add("lit"); });
          run.lit = n;
        }));
        later(2300, () => { root.classList.add("check"); setTally($("#check-tally"), "happy"); });
        later(3300, () => {
          run.step = 2; root.dataset.step = "2"; root.classList.remove("check"); setStepDots(2);
          $("#check-say").textContent = "Touch your 6 with your 6.";
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
        $("#check-say").textContent = "Six and six, touching.";
        setTally($("#check-tally"), "happy");
        later(1600, () => {
          root.classList.remove("check", "banner"); setStepDots(3);
          $("#check-say").textContent = "Say the answer.";
          $("#check-mic").hidden = false;
          $("#check-miclabel").textContent = voice.recognition ? "Listening" : "Type it";
          setTally($("#check-tally"), "ready");
          listenWhile("correct_pose");
        });
      } else if (m.state === "wrong_pose" && m.tally) {
        $("#check-say").textContent = m.tally;
        setTally($("#check-tally"), "almost");
      }
      return;
    }
    if (run.step === 3 && m.state === "answer_correct") {
      root.classList.add("heard", "result", "check");
      $("#check-result").textContent = m.answer;
      $("#check-miclabel").textContent = "Thirty six";
      $("#check-say").textContent = "Thirty six. Exactly.";
      setTally($("#check-tally"), "happy");
      run.done = true;
    } else if (run.step === 3 && m.state === "answer_wrong" && m.tally) {
      $("#check-say").textContent = m.tally;
      setTally($("#check-tally"), "almost");
    }
  }
  function checkEnd() {
    const run = checkRun;
    if (!run) return;
    $("#check-say").textContent = "Your path is open.";
    later(1400, () => {
      checkRun.timers.forEach(clearTimeout);
      checkRun = null;
      try { sessionStorage.setItem("tenfold.checked", "1"); } catch (e) { /* fine */ }
      go("practice");
    });
  }

  // ---------- Tally speaks ----------
  function speak(text, host) {
    if (!("speechSynthesis" in window) || !text) return;
    try {
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.lang = "en-US"; u.rate = 0.95; u.pitch = 1.15;
      const talk = (on) => { if (host) host.classList.toggle("is-talking", on); };
      u.onstart = () => talk(true); u.onend = () => talk(false); u.onerror = () => talk(false);
      window.speechSynthesis.speak(u);
    } catch (e) { /* the sentence is on screen anyway */ }
  }

  // ---------- voice answers ----------
  const voice = { recognition: null, wanted: false, running: false, last: null, at: 0 };
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
  function setupVoice() {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition || voice.recognition) return;
    const r = new Recognition();
    r.lang = "en-US"; r.continuous = true; r.interimResults = true;
    r.onstart = () => { voice.running = true; markMic(); };
    r.onend = () => { voice.running = false; markMic(); if (voice.wanted) setTimeout(startVoice, 300); };
    r.onerror = (e) => { if (e.error === "not-allowed" || e.error === "service-not-allowed") { voice.wanted = false; markMic("denied"); } };
    r.onresult = (e) => {
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const text = e.results[i][0].transcript.trim();
        const heard = $(".heard");
        if (heard) heard.textContent = text ? `heard: ${text}` : "";
        if (checkRun) $("#check-miclabel").textContent = text || "Listening";
        if (e.results[i].isFinal) submitSpoken(text);
      }
    };
    voice.recognition = r;
  }
  function markMic(forced) {
    const mic = $(".mic");
    if (!mic) return;
    mic.hidden = !voice.recognition;
    mic.classList.toggle("is-on", voice.running && !forced);
    mic.classList.toggle("is-denied", forced === "denied");
  }
  function startVoice() { if (!voice.recognition || voice.running || !voice.wanted) return; try { voice.recognition.start(); } catch (e) { /* starting */ } }
  function stopVoice() { voice.wanted = false; if (voice.recognition && voice.running) { try { voice.recognition.stop(); } catch (e) { /* done */ } } }
  function listenWhile(state) {
    if (!voice.recognition) return;
    const should = state === "correct_pose" || state === "waiting_answer";
    if (should === voice.wanted) return;
    voice.wanted = should;
    if (should) startVoice(); else stopVoice();
  }
  function submitSpoken(text) {
    const value = parseNumber(text);
    if (value === null || (!lesson && !checkRun)) return;
    const now = Date.now();
    if (value === voice.last && now - voice.at < 1500) return;
    voice.last = value; voice.at = now;
    setTyped(String(value).slice(0, 3));
    sendLesson({ type: "check", value });
  }

  // ---------- socket ----------
  let socket = null, socketReady = false;
  function connect(onOpen) {
    if (socket && socketReady) { onOpen(); return; }
    if (socket) { socket.addEventListener("open", onOpen, { once: true }); return; }
    socket = new WebSocket(`ws://${location.host}/ws`);
    socket.onopen = () => { socketReady = true; onOpen(); };
    socket.onmessage = (e) => onServerMessage(JSON.parse(e.data));
    socket.onclose = () => { socketReady = false; socket = null; };
    socket.onerror = () => { socketReady = false; };
  }
  function sendLesson(payload) { if (socket && socketReady) socket.send(JSON.stringify(payload)); }
  function onServerMessage(m) {
    if (m.demo && !demoSeeded) seedShowcase();
    if (m.type === "node_end") {
      if (m.state) write(KEY_LEARNER, m.state);
      if (checkRun && m.node_id === "check") return checkEnd();
      if (!lesson || (m.node_id && m.node_id !== lesson.node.id)) return;
      lesson.correct = m.correct; lesson.total = m.total || lesson.total; lesson.endTally = m.tally;
      return finish(false);
    }
    if (m.type !== "state") return;
    if (checkRun && m.node === "check") return checkMessage(m);
    if (!lesson || m.node !== lesson.node.id) return;
    const fresh = m.state !== lesson.last;
    if (fresh && m.state === "answer_wrong" && lesson.hearts !== null) {
      lesson.hearts -= 1; lesson.hit = true;
      if (lesson.hearts <= 0) { sendLesson({ type: "quit" }); return finish(true); }
    }
    if (fresh && m.fact && m.fact !== lesson.fact) { lesson.fact = m.fact; lesson.done += 1; }
    lesson.last = m.state;
    renderPractice(m);
    listenWhile(m.state);
    if (m.tally && m.tally !== lesson.said) { lesson.said = m.tally; speak(m.tally, $("#lesson .tally")); }
  }
  // --demo: the map opens as a showcase, first unit done, second current
  function seedShowcase() {
    demoSeeded = true;
    if (Object.keys(progress.stars).length) return;
    const nodes = L.allNodes().filter((n) => n.unitId === L.UNITS[0].id);
    nodes.forEach((n) => { if (n.kind === "chest") progress.chests[n.id] = true; else progress.stars[n.id] = 3; });
    progress.xp = 150; progress.streak = 5; progress.lastDay = today();
    save();
    if (view === "practice") renderAll();
  }

  // ---------- the camera lesson ----------
  const MOOD = { exercise_shown: "ready", waiting_pose: "ready", wrong_pose: "almost", correct_pose: "thinking", waiting_answer: "thinking", answer_correct: "happy", answer_wrong: "almost" };
  function startLesson(id) {
    const node = L.findNode(id);
    const total = node.kind === "boss" ? L.BOSS_QUESTIONS : L.LESSON_QUESTIONS;
    lesson = { node, total, correct: 0, done: 0, fact: null, last: null, said: null, hearts: node.kind === "boss" ? 3 : null, hit: false, typed: "", t0: Date.now() };
    openNode = null;
    stopCheck();
    VIEWS.forEach((v) => { $("#" + v).hidden = true; });
    $("#finish").hidden = true;
    $("#lesson").hidden = false;
    document.body.dataset.view = "lesson";
    setupVoice();
    shellPractice();
    connect(() => sendLesson({ type: "start_node", state: learner(), node: { id: node.id, kind: node.kind, pairs: node.pairs, count: total } }));
  }
  function shellPractice() {
    const s = lesson;
    const hearts = s.hearts === null ? "" : `<div class="hearts" aria-label="${s.hearts} hearts">${use("icon-flame")}${s.hearts}</div>`;
    $("#lesson").innerHTML = `
      <div class="lesson-top">
        <button class="icon-btn" data-action="quit" aria-label="Quit lesson">${use("icon-close")}</button>
        <div class="bar lesson-bar"><i class="bar-fill" style="width:0%"></i></div>
        ${hearts}<div class="lesson-count practice-why"></div>
      </div>
      <div class="cl-body">
        <p class="cl-exercise practice-exercise">Getting ready</p>
        <div class="cam cl-cam">
          <div class="practice-stage" data-state="">
            <img class="practice-video" src="/video" alt="">
            <svg class="practice-overlay" viewBox="0 0 1.333 1" preserveAspectRatio="none"></svg>
          </div>
          <span class="cam-tag">${use("icon-camera")}camera</span>
          <div class="cam-veil">I cannot quite see your fingers<small>Hold them up, palms toward me</small></div>
          <div class="reasons practice-reasoning"></div>
        </div>
        <div class="cl-foot">
          <div class="speaker">
            <div class="tally" data-tally="ready"></div>
            <div class="speech"><span class="tally-say">Show me both hands.</span><small class="speech-sub">I am watching your fingers</small></div>
          </div>
          <div class="practice-answer">
            <button class="mic" type="button" data-action="mic" hidden aria-label="say the answer">${use("icon-mic")}</button>
            <span class="typed"></span><span class="caret"></span>
            <div class="heard"></div>
          </div>
        </div>
      </div>`;
    decorate($("#lesson"));
    markMic();
  }
  function fitOverlay(stage) {
    const video = $(".practice-video", stage), overlay = $(".practice-overlay", stage);
    if (!stage || !video || !overlay) return 4 / 3;
    const W = stage.clientWidth, H = stage.clientHeight;
    const ratio = (video.naturalWidth && video.naturalHeight) ? video.naturalWidth / video.naturalHeight : 4 / 3;
    const cover = stage.classList.contains("cam-full");
    const w = cover ? Math.max(W, H * ratio) : Math.min(W, H * ratio), h = w / ratio;
    overlay.setAttribute("viewBox", `0 0 ${ratio} 1`);
    overlay.style.cssText = `left:${(W - w) / 2}px;top:${(H - h) / 2}px;width:${w}px;height:${h}px`;
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
    $(".tally-say", root).textContent = m.tally || "";
    $(".speech-sub", root).textContent = m.reaction === "cannot_see" ? "Palms toward the camera" : m.reason ? ({ retry: "One more try", review: "You have met this one", confidence: "An easy one", next_new: "Brand new", level_up: "Jumping ahead" }[m.reason] || "") : "I am watching your fingers";
    setTally($(".tally", root), m.reaction === "cannot_see" ? "squint" : (MOOD[m.state] || "ready"));
    root.dataset.state = m.state;
    root.dataset.reaction = m.reaction || "";
    $(".practice-stage", root).dataset.state = m.state;
    const band = $(".reasons", root);
    const lines = m.reasoning || [];
    root.classList.toggle("has-reasons", lines.length > 0);
    band.innerHTML = lines.map((line) => `<span>${line}</span>`).join("");
    drawFingers(m, $(".practice-stage", root));
  }
  function drawFingers(m, stage) {
    const overlay = $(".practice-overlay", stage);
    if (!overlay) return;
    const span = fitOverlay(stage);
    overlay.replaceChildren();
    const key = (f) => `${f.hand}:${f.number}`;
    const wrong = new Set((m.wrong || []).map(key));
    const match = new Set((m.match || []).map(key));
    const hint = m.hint || {};
    const ghost = (m.hint_level >= 2 && hint.hand && hint.move_to) ? `${hint.hand}:${hint.move_to}` : null;
    for (const finger of m.fingers || []) {
      const id = key(finger);
      const colour = wrong.has(id) ? "#c06214" : match.has(id) ? "#3f7d2a" : "#f4f0e8";
      if (id === ghost) {
        const ring = document.createElementNS(NS, "circle");
        ring.setAttribute("class", "ghost"); ring.setAttribute("cx", finger.x * span); ring.setAttribute("cy", finger.y); ring.setAttribute("r", 0.055);
        overlay.appendChild(ring);
      }
      const c = document.createElementNS(NS, "circle");
      c.setAttribute("cx", finger.x * span); c.setAttribute("cy", finger.y); c.setAttribute("r", 0.036);
      c.setAttribute("fill", "rgba(43,39,36,0.78)"); c.setAttribute("stroke", colour); c.setAttribute("stroke-width", 0.006);
      c.dataset.number = finger.number;
      overlay.appendChild(c);
      const t = document.createElementNS(NS, "text");
      t.setAttribute("x", finger.x * span); t.setAttribute("y", finger.y + 0.019); t.setAttribute("fill", colour);
      t.dataset.number = finger.number; t.textContent = finger.number;
      overlay.appendChild(t);
    }
  }
  function setTyped(value) {
    if (!lesson && !checkRun) return;
    if (lesson) lesson.typed = value;
    const el = $(".typed"); if (el) el.textContent = value;
  }

  // ---------- finish ----------
  function finish(outOfHearts) {
    const s = lesson;
    stopVoice();
    const total = s.total;
    const res = L.recordLesson(progress, s.node.id, s.correct, total, today());
    progress = res.progress; save();
    justUnlocked = res.unlocked;
    const secs = Math.round((Date.now() - s.t0) / 1000);
    const time = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
    const acc = Math.round((s.correct / total) * 100);
    const fail = outOfHearts || res.stars === 0;
    const title = outOfHearts ? "Out of hearts" : fail ? "Almost there" : s.node.kind === "boss" ? "Boss defeated!" : res.stars === 3 ? "Perfect lesson!" : "Lesson complete!";
    const need = Math.ceil(total * 0.6);
    const sub = fail ? `Get ${need} of ${total} right to earn a star and unlock the next level.` : s.endTally || (res.unlocked ? "A new level is waiting for you." : "Keep going for all three stars.");
    $("#finish").className = `finish${fail ? " is-fail" : ""}`;
    $("#finish").innerHTML = `
      ${fail ? "" : confetti()}
      <div class="finish-inner">
        <div class="tally tally-cheer" data-tally="${fail ? "almost" : "happy"}" data-accessories="${fail ? "" : "headband"}"></div>
        <h1>${title}</h1>
        <p class="finish-sub">${sub}</p>
        <div class="big-stars">${[0, 1, 2].map((i) => starSvg(i < res.stars)).join("")}</div>
        <div class="cards">
          <div class="card"><div class="card-label">Total XP</div><div class="card-value">${use("icon-xp")}${res.xpGained}</div></div>
          <div class="card card-green"><div class="card-label">${acc >= 80 ? "Amazing" : "Accuracy"}</div><div class="card-value">${use("icon-check")}${acc}%</div></div>
          <div class="card card-blue"><div class="card-label">Speedy</div><div class="card-value">${use("icon-flame")}${time}</div></div>
        </div>
        <button class="btn btn-primary btn-xl" data-action="${fail ? "retry" : "home"}" data-node="${s.node.id}">${fail ? "Try again" : "Continue"}</button>
        ${fail ? `<button class="finish-alt" data-action="home">Back to the map</button>` : ""}
      </div>`;
    $("#lesson").hidden = true;
    $("#finish").hidden = false;
    document.body.dataset.view = "finish";
    $("#finish").scrollTop = 0;
    decorate($("#finish"));
    speak(sub, $("#finish .tally"));
    if (res.streakUp) setTimeout(() => toast(`${use("icon-flame")}${res.streak} day streak`), 1500);
  }
  function confetti() {
    const colors = ["#3f7d2a", "#2f6f6a", "#d9a227", "#c06214", "#8a6fb3"];
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
  function backToMap() {
    if (lesson) sendLesson({ type: "quit" });
    stopVoice();
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    lesson = null;
    go("practice");
    const target = justUnlocked && $(`[data-node="${justUnlocked}"]`);
    if (target) target.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => { justUnlocked = null; }, 900);
  }

  // ---------- castle, the board ----------
  function masteryStars(m) { return m >= 5 ? 3 : m >= 3 ? 2 : m >= 1 ? 1 : 0; }
  function renderBoard() {
    const state = learner() || { math: {} };
    const nums = [6, 7, 8, 9, 10];
    const map = $("#castle-map");
    map.innerHTML = "";
    const cell = (cls, text) => { const d = document.createElement("div"); d.className = cls; d.textContent = text; return d; };
    map.appendChild(cell("axis", ""));
    nums.forEach((n) => map.appendChild(cell("axis", String(n))));
    const session = state.sessions || 0;
    nums.forEach((a) => {
      map.appendChild(cell("axis", String(a)));
      nums.forEach((b) => {
        const key = `${Math.min(a, b)}x${Math.max(a, b)}`;
        const rec = (state.math || {})[key];
        const seen = rec && rec.attempts && rec.attempts.length;
        const room = document.createElement("div");
        room.className = "room" + (seen ? "" : " locked");
        if (!seen) { room.innerHTML = `<span>${a} × ${b}</span><i class="lock"></i>`; }
        else {
          const stars = masteryStars(rec.mastery || 0);
          const due = (rec.due_session !== null && rec.due_session !== undefined && rec.due_session <= session + 1) ||
                      (rec.due_at && new Date(rec.due_at) <= new Date());
          room.innerHTML = `<span>${a} × ${b}</span><div class="pips">${[0, 1, 2].map((i) => `<i class="star${i < stars ? "" : " off"}"></i>`).join("")}</div>${due ? `<i class="clock"></i>` : ""}`;
        }
        map.appendChild(room);
      });
    });
    $("#board-streak").textContent = `${progress.streak} day${progress.streak === 1 ? "" : "s"}`;
    renderWardrobe($("#wardrobe"));
    renderShelf($("#shelf"));
    decorate($("#board"));
  }
  function unlocks() {
    const t = L.totals(progress);
    const state = learner() || { math: {} };
    const mastered = Object.values(state.math || {}).filter((r) => (r.mastery || 0) >= 4).length;
    return {
      glasses: { on: true, req: "Unlocked" },
      headband: { on: t.stars >= 3, req: "Earn 3 stars" },
      hat: { on: mastered >= 5, req: "Master 5 facts" },
      cape: { on: progress.streak >= 10, req: "Streak of 10 days" },
      stars: t.stars, mastered,
    };
  }
  function renderWardrobe(host) {
    const u = unlocks();
    const items = [["glasses", "Glasses", "ready"], ["headband", "Headband", "happy"], ["hat", "Wizard hat", "ready"], ["cape", "Cape", "ready"]];
    host.innerHTML = items.map(([id, label, face]) => `
      <div class="item"><div class="tally${u[id].on ? "" : " is-locked"}" style="width:88px" data-tally="${face}" data-accessories="${id}"></div>
      <span class="name">${label}</span><span class="req">${u[id].on ? "Unlocked" : `<i class="lock"></i>${u[id].req}`}</span></div>`).join("");
  }
  function renderShelf(host) {
    const u = unlocks();
    const state = learner() || { math: {} };
    const anyPerfect = Object.values(progress.stars).some((s) => s === 3);
    const badges = [
      ["First lesson", Object.keys(progress.stars).length > 0], ["Perfect lesson", anyPerfect],
      ["Streak 5", progress.streak >= 5], ["Both hands", !!(state.sessions)],
      ["Five stars", u.stars >= 5], ["Ten stars", u.stars >= 10], ["Boss", !!progress.stars["u3-b1"]], ["Ten Master", u.mastered >= 10],
    ];
    host.innerHTML = badges.map(([label, on]) => `<div class="badge${on ? " on" : ""}"><div class="medal"><i class="star"></i></div><span>${label}</span></div>`).join("");
  }

  // ---------- profile ----------
  function renderProfile() {
    const who = name();
    const t = L.totals(progress);
    const state = learner();
    $("#profile-title").textContent = who ? `Hi ${who}.` : "Your fingers can multiply.";
    $("#profile-lead").textContent = t.stars ? `${t.stars} stars so far. ${progress.streak} day streak.` : "Start a lesson and Tally will remember your stars.";
    $("#profile-name").value = who;
    $("#profile-summary").innerHTML = `
      <div class="stat"><span class="k">${t.stars}</span><span class="v">stars of ${t.maxStars}</span></div>
      <div class="stat"><span class="k">${progress.xp}</span><span class="v">xp</span></div>
      <div class="stat"><span class="k">${progress.streak}</span><span class="v">day streak</span></div>
      <div class="stat"><span class="k">${state ? state.sessions || 0 : 0}</span><span class="v">sessions</span></div>`;
    const u = unlocks();
    const host = $("#profile-tally");
    host.setAttribute("data-accessories", u.cape.on ? "cape" : u.hat.on ? "hat" : u.headband.on ? "headband" : "");
    decorate($("#profile"));
  }
  $("#profile-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const value = ($("#profile-name").value || "").trim();
    if (value) write(KEY_NAME, value);
    renderProfile();
    toast("Saved");
  });

  // ---------- events ----------
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-action]");
    if (!el) { if (openNode && !e.target.closest(".popover")) { openNode = null; renderCourse(); } return; }
    const action = el.dataset.action, id = el.dataset.node;
    if (action === "node") toggleNode(id);
    else if (action === "start") startLesson(id);
    else if (action === "chest") {
      const res = L.claimChest(progress, id, today());
      if (res.error) return;
      progress = res.progress; save(); openNode = null; justUnlocked = res.unlocked;
      renderAll(); toast(`${use("icon-chest", "0 0 88 80")}+${res.xpGained} XP from the chest`);
      setTimeout(() => { justUnlocked = null; }, 900);
    }
    else if (action === "mic") { voice.wanted = !voice.wanted; if (voice.wanted) startVoice(); else stopVoice(); }
    else if (action === "quit" || action === "home") backToMap();
    else if (action === "retry") startLesson(id);
    else if (action === "reset") { progress = L.emptyProgress(); openNode = null; save(); try { localStorage.removeItem(KEY_LEARNER); } catch (err) { /* fine */ } renderAll(); toast("Progress reset"); }
  });
  document.addEventListener("keydown", (e) => {
    const inField = e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA");
    const answering = (lesson && !$("#lesson").hidden) || (checkRun && checkRun.step === 3);
    if (!answering) {
      if (e.key === "Escape" && openNode) { openNode = null; renderCourse(); }
      if (e.key === "Enter" && !$("#finish").hidden) { const btn = $("#finish .btn"); if (btn) btn.click(); }
      return;
    }
    if (inField) return;
    const typed = lesson ? lesson.typed : (checkRun.typed || "");
    if (e.key >= "0" && e.key <= "9") { if (typed.length < 3) { const v = typed + e.key; if (checkRun && !lesson) checkRun.typed = v; setTyped(v); if (checkRun && !lesson) $("#check-miclabel").textContent = v; } }
    else if (e.key === "Backspace") { const v = typed.slice(0, -1); if (checkRun && !lesson) checkRun.typed = v; setTyped(v); }
    else if (e.key === "Enter") { e.preventDefault(); if (!typed.length) return; sendLesson({ type: "check", value: Number(typed) }); if (checkRun && !lesson) checkRun.typed = ""; setTyped(""); }
    else if (e.key === "n" && lesson) { setTyped(""); sendLesson({ type: "next" }); }
    else if (e.key === "Escape") backToMap();
  });
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
  window.Tenfold = { parseNumber, voice, get lesson() { return lesson; } };
  route();
  watchCamera();
  decorate(document);
  // the demo flag arrives on the socket; connect early so the showcase seeds before the map is opened
  connect(() => {});
})();
