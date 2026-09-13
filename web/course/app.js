/*
 * Tenfold levels page. Renders the course map, runs a lesson, shows the result.
 * Not wired to the camera lesson yet: answers are tapped or typed here.
 * Progress lives in localStorage; every read and write tolerates a blocked storage.
 */
(function () {
  "use strict";
  const L = window.TenfoldLevels;
  const KEY = "tenfold.levels.v1";
  const params = new URLSearchParams(location.search);
  const $ = (sel) => document.querySelector(sel);

  // ---------- icons (inline, flat, sized by CSS) ----------
  const ICON = {
    star: (fill) => `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="${fill}" d="M12 1.8l3.1 6.3 6.9 1-5 4.9 1.2 6.9L12 17.6l-6.2 3.3L7 14 2 9.1l6.9-1z"/></svg>`,
    check: `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" d="M5 12.5l4.5 4.5L19 7.5"/></svg>`,
    lock: `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="10" width="14" height="11" rx="3" fill="#afafaf"/><path d="M8 10V7.5a4 4 0 018 0V10" fill="none" stroke="#afafaf" stroke-width="3"/></svg>`,
    trophy: `<svg viewBox="0 0 48 48" aria-hidden="true"><path fill="#fff" d="M14 6h20v12c0 6.1-4.5 11.2-10 11.9V34h6v6H18v-6h6v-4.1C18.5 29.2 14 24.1 14 18z"/><path fill="none" stroke="#fff" stroke-width="3.5" stroke-linecap="round" d="M14 10H7c0 6 3 9 7 9.5M34 10h7c0 6-3 9-7 9.5"/></svg>`,
    flame: (on) => `<svg viewBox="0 0 32 32" aria-hidden="true"><path fill="${on ? "#ff9600" : "#e5e5e5"}" d="M16 2c1 5.5 9 9 9 17a9 9 0 01-18 0c0-4 2-6.5 4-8.5.3 3 1.6 4.8 3.3 5.5C13.5 11 14.5 6 16 2z"/><path fill="${on ? "#ffc800" : "#f7f7f7"}" d="M16 16c.6 2.6 4.5 4 4.5 7.5a4.5 4.5 0 01-9 0c0-2.2 1.6-3.8 2.8-4.8.2 1.2.8 2 1.7 2.3-.7-1.8-.6-3.4 0-5z"/></svg>`,
    bolt: `<svg viewBox="0 0 32 32" aria-hidden="true"><path fill="#ffc800" d="M18 2L6 18h8l-2 12 14-18h-8z"/><path fill="#e5b400" d="M18 2l-4 14h-8z"/></svg>`,
    heart: `<svg viewBox="0 0 32 32" aria-hidden="true"><path fill="#ff4b4b" d="M16 28S3 20.5 3 11.5A6.5 6.5 0 0116 8a6.5 6.5 0 0113 3.5C29 20.5 16 28 16 28z"/><path fill="#fff" opacity=".35" d="M9 8.5c-2 .5-3.4 2.2-3.4 4.3 0 .8.6 1 1 .5.8-1.8 1.9-3 3.5-3.6.7-.3.3-1.5-1.1-1.2z"/></svg>`,
    close: `<svg viewBox="0 0 24 24" aria-hidden="true"><path stroke="currentColor" stroke-width="3" stroke-linecap="round" d="M5 5l14 14M19 5L5 19"/></svg>`,
    home: `<svg viewBox="0 0 32 32" aria-hidden="true"><path fill="#ff9600" d="M16 3l13 11h-4v14H7V14H3z"/><rect x="12.5" y="18" width="7" height="10" rx="2" fill="#fff"/></svg>`,
    grid: `<svg viewBox="0 0 32 32" aria-hidden="true"><rect x="3" y="3" width="12" height="12" rx="3" fill="#58cc02"/><rect x="17" y="3" width="12" height="12" rx="3" fill="#1cb0f6"/><rect x="3" y="17" width="12" height="12" rx="3" fill="#ffc800"/><rect x="17" y="17" width="12" height="12" rx="3" fill="#ce82ff"/></svg>`,
    user: `<svg viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="11" r="7" fill="#ce82ff"/><path fill="#ce82ff" d="M3 29c1-7 6-10 13-10s12 3 13 10z"/></svg>`,
    spark: `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 2l2.2 6.8L21 11l-6.8 2.2L12 20l-2.2-6.8L3 11l6.8-2.2z"/></svg>`,
    clock: `<svg viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="12" fill="none" stroke="currentColor" stroke-width="4"/><path d="M16 9v7l5 3" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round"/></svg>`,
    target: `<svg viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="12" fill="none" stroke="currentColor" stroke-width="4"/><circle cx="16" cy="16" r="4" fill="currentColor"/></svg>`,
    camera: `<svg viewBox="0 0 64 64" aria-hidden="true"><rect width="64" height="64" rx="16" fill="#ddf4ff"/><rect x="9" y="19" width="34" height="26" rx="7" fill="#1cb0f6"/><path fill="#1899d6" d="M43 29l12-7v20l-12-7z"/><circle cx="26" cy="32" r="6.5" fill="#fff"/><circle cx="26" cy="32" r="2.5" fill="#1899d6"/></svg>`,
    chest: (state) => {
      const open = state === "done";
      const body = state === "locked" ? "#e5e5e5" : "#ff9600";
      const lip = state === "locked" ? "#b7b7b7" : "#cc7700";
      const band = state === "locked" ? "#afafaf" : "#ffc800";
      const lid = open
        ? `<path fill="${body}" d="M10 22 L78 10 L80 26 L12 38z"/><path fill="${band}" d="M40 17l10-2 2 16-10 2z"/>`
        : `<path fill="${body}" d="M8 30c0-10 8-16 18-16h36c10 0 18 6 18 16v6H8z"/><rect x="38" y="14" width="12" height="22" fill="${band}"/>`;
      const glow = open ? `<path fill="#ffc800" opacity=".9" d="M30 40 L44 14 L58 40z"/>` : "";
      return `<svg viewBox="0 0 88 80" aria-hidden="true">${glow}<rect x="8" y="36" width="72" height="38" rx="8" fill="${body}"/><rect x="8" y="64" width="72" height="10" rx="5" fill="${lip}"/>${lid}<rect x="38" y="36" width="12" height="30" fill="${band}"/><rect x="36" y="42" width="16" height="14" rx="4" fill="${state === "locked" ? "#d0d0d0" : "#fff5cc"}"/></svg>`;
    },
  };

  // ---------- storage ----------
  function today() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }
  function load() {
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) return Object.assign(L.emptyProgress(), JSON.parse(raw));
    } catch (e) { /* storage blocked or corrupt: start fresh */ }
    return L.emptyProgress();
  }
  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(progress)); } catch (e) { /* keep playing in memory */ }
  }

  let progress = load();
  let openNode = null;
  let justUnlocked = null;
  let lesson = null;

  // ---------- map ----------
  function starRow(n) {
    return `<div class="mini-stars">${[0, 1, 2].map((i) => ICON.star(i < n ? "#ffc800" : "#e5e5e5")).join("")}</div>`;
  }

  function popover(node, state) {
    if (state === "locked") {
      return `<div class="popover is-locked"><h4>${node.kind === "boss" ? "Boss" : node.title}</h4><p>Finish everything above to unlock this.</p></div>`;
    }
    if (node.kind === "chest") {
      return `<div class="popover"><h4>Treasure chest</h4><p>A reward for getting this far.</p><button class="btn btn-white btn-wide" data-action="chest" data-node="${node.id}">Open +${L.CHEST_XP} XP</button></div>`;
    }
    const count = node.kind === "boss" ? L.BOSS_QUESTIONS : L.LESSON_QUESTIONS;
    const facts = node.pairs.slice(0, 4).map(([a, b]) => `${a} × ${b}`).join(" · ");
    const best = progress.stars[node.id] || 0;
    const label = state === "done" ? (best < 3 ? "Practice +XP" : "Replay") : `Start +${count * L.XP_PER_CORRECT} XP`;
    return `<div class="popover"><h4>${node.kind === "boss" ? "Boss fight" : node.title}</h4><p>${node.kind === "boss" ? `${count} questions, no second chances` : facts}</p><button class="btn btn-white btn-wide" data-action="start" data-node="${node.id}">${label}</button></div>`;
  }

  function nodeHtml(node, state) {
    const current = state === "current";
    const icon = node.kind === "chest" ? ICON.chest(state)
      : node.kind === "boss" ? (state === "locked" ? ICON.lock : ICON.trophy)
        : state === "locked" ? ICON.lock : state === "done" ? ICON.check : ICON.star("#fff");
    const ring = current && node.kind !== "chest"
      ? `<svg class="ring${node.kind === "boss" ? " ring-boss" : ""}" viewBox="0 0 100 100" aria-hidden="true"><circle cx="50" cy="50" r="44"/><circle class="ring-arc" cx="50" cy="50" r="44" stroke-dasharray="${(progress.stars[node.id] || 0) / 3 * 276.5} 276.5"/></svg>`
      : "";
    const bubble = current ? `<div class="bubble">${node.kind === "chest" ? "Open" : node.kind === "boss" ? "Fight" : "Start"}</div>` : "";
    const stars = node.kind === "chest" ? "" : starRow(progress.stars[node.id] || 0);
    const label = `${node.kind === "chest" ? "Chest" : node.title}, ${state}`;
    return `<div class="node-wrap${justUnlocked === node.id ? " pop" : ""}">
      ${bubble}${ring}
      <button class="node node-${node.kind} is-${state}" data-action="node" data-node="${node.id}" aria-label="${label}" aria-expanded="${openNode === node.id}">${icon}</button>
      ${stars}
    </div>`;
  }

  function renderCourse() {
    let html = "";
    L.UNITS.forEach((unit, ui) => {
      const scored = unit.nodes.filter((n) => n.kind !== "chest");
      const got = scored.reduce((s, n) => s + (progress.stars[n.id] || 0), 0);
      html += `<section class="unit" style="--unit:${unit.color};--unit-lip:${unit.lip}">
        <header class="unit-banner">
          <div><div class="unit-kicker">Unit ${ui + 1}</div><h2>${unit.title}</h2><p>${unit.subtitle}</p></div>
          <div class="unit-count" aria-label="${got} of ${scored.length * 3} stars">${ICON.star("#fff")}${got}/${scored.length * 3}</div>
        </header>
        <div class="path">`;
      unit.nodes.forEach((node, ni) => {
        const state = L.nodeState(progress, node.id);
        const open = openNode === node.id;
        // The popover hangs off the row, not the shifted node, so it stays inside the column on narrow screens.
        html += `<div class="row${open ? " is-open" : ""}" style="--x:${L.OFFSETS[ni % L.OFFSETS.length]}px">${nodeHtml(node, state)}${open ? popover(node, state) : ""}</div>`;
      });
      html += `</div></section>`;
    });
    $("#course").innerHTML = html;
  }

  function toggleNode(id) {
    openNode = openNode === id ? null : id;
    renderCourse();
    const pop = $(".popover");
    if (!pop) return;
    // Keep the whole popover on screen, above the mobile tab bar when it shows.
    const bar = $(".tabbar");
    const barH = bar && getComputedStyle(bar).display !== "none" ? bar.offsetHeight : 0;
    const overflow = pop.getBoundingClientRect().bottom - (window.innerHeight - barH - 16);
    if (overflow > 0) window.scrollBy({ top: overflow, behavior: "smooth" });
  }

  function renderStats() {
    const t = L.totals(progress);
    const streakOn = progress.lastDay === today();
    const stats = `
      <div class="stat stat-streak${progress.streak ? "" : " is-empty"}" title="Day streak">${ICON.flame(streakOn)}${progress.streak}</div>
      <div class="stat stat-stars${t.stars ? "" : " is-empty"}" title="Stars">${ICON.star(t.stars ? "#ffc800" : "#e5e5e5")}${t.stars}</div>
      <div class="stat stat-xp${progress.xp ? "" : " is-empty"}" title="Total XP">${ICON.bolt}${progress.xp}</div>`;
    $("#stats").innerHTML = stats;
    $("#topstats").innerHTML = stats;
    const q = L.dailyQuest(progress, today());
    const pct = Math.round((q.value / q.goal) * 100);
    $("#quest").innerHTML = `
      <div class="quest-head"><h3>Daily quest</h3></div>
      <div class="quest-row">${ICON.bolt}<div class="quest-main">
        <div class="quest-title">Earn ${q.goal} XP</div>
        <div class="bar"><div class="bar-fill" style="width:${pct}%"></div><div class="bar-label">${q.value} / ${q.goal}</div></div>
      </div></div>`;
  }

  function renderAll() {
    renderStats();
    renderCourse();
  }

  // ---------- lesson ----------
  function show(view) {
    $("#lesson").hidden = view !== "lesson";
    $("#finish").hidden = view !== "finish";
    document.body.classList.toggle("is-locked", view !== "map");
  }

  // ---------- the camera lesson ----------
  // The multiple choice lesson is gone. A node now opens the practice view: the
  // mirrored camera, the finger numbers drawn over it, Tally bottom left, and an
  // answer given out loud or typed. app/server.py drives it through
  // lesson/scheduler.py, scoped to node.pairs; this file only renders and counts.

  const MOOD = {
    exercise_shown: "ready", waiting_pose: "ready", wrong_pose: "almost",
    correct_pose: "thinking", waiting_answer: "thinking",
    answer_correct: "happy", answer_wrong: "almost",
  };

  function tallySvg() {
    return `<svg class="tally-art" viewBox="0 0 120 120" aria-hidden="true">
      <g class="tally-fingers">
        <rect x="28" y="16" width="13" height="42" rx="6.5" fill="#ffd7a8"/>
        <rect x="45" y="10" width="13" height="48" rx="6.5" fill="#ffd7a8"/>
        <rect x="62" y="13" width="13" height="45" rx="6.5" fill="#ffd7a8"/>
        <rect x="79" y="22" width="13" height="36" rx="6.5" fill="#ffd7a8"/>
      </g>
      <rect x="6" y="52" width="26" height="13" rx="6.5" fill="#ffc894"
            transform="rotate(-24 19 58)"/>
      <rect x="24" y="50" width="72" height="60" rx="22" fill="#ffc894"/>
      <g class="tally-face">
        <g class="tally-eyes">
          <circle class="eye" cx="48" cy="74" r="5.5" fill="#3c2a1e"/>
          <circle class="eye" cx="72" cy="74" r="5.5" fill="#3c2a1e"/>
          <path class="eye-shut" d="M42 75q6-7 12 0M66 75q6-7 12 0" fill="none"
                stroke="#3c2a1e" stroke-width="4" stroke-linecap="round"/>
          <path class="eye-slit" d="M42 74h12M66 74h12" fill="none"
                stroke="#3c2a1e" stroke-width="4" stroke-linecap="round"/>
        </g>
        <path class="mouth" fill="none" stroke="#3c2a1e" stroke-width="4"
              stroke-linecap="round" d="M50 91q10 8 20 0"/>
      </g>
    </svg>`;
  }

  function setMood(mood) {
    const el = $(".tally");
    if (el) el.dataset.mood = mood;
  }

  // ---------- speech ----------
  function speak(text) {
    if (!("speechSynthesis" in window) || !text) return;
    try {
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = "en-US";
      utterance.rate = 0.95;
      utterance.pitch = 1.15;
      const talk = (on) => { const t = $(".tally"); if (t) t.classList.toggle("is-talking", on); };
      utterance.onstart = () => talk(true);
      utterance.onend = () => talk(false);
      utterance.onerror = () => talk(false);
      window.speechSynthesis.speak(utterance);
    } catch (e) { /* a browser without speech still shows the sentence */ }
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
    const recognition = new Recognition();
    recognition.lang = "en-US";
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.onstart = () => { voice.running = true; markMic(); };
    recognition.onend = () => { voice.running = false; markMic(); if (voice.wanted) setTimeout(startVoice, 300); };
    recognition.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") { voice.wanted = false; markMic("denied"); }
    };
    recognition.onresult = (e) => {
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const text = e.results[i][0].transcript.trim();
        const heard = $(".heard");
        if (heard) heard.textContent = text ? `heard: ${text}` : "";
        if (e.results[i].isFinal) submitSpoken(text);
      }
    };
    voice.recognition = recognition;
  }

  function markMic(forced) {
    const mic = $(".mic");
    if (!mic) return;
    mic.hidden = !voice.recognition;
    mic.classList.toggle("is-on", voice.running && !forced);
    mic.classList.toggle("is-denied", forced === "denied");
  }

  function startVoice() {
    if (!voice.recognition || voice.running || !voice.wanted) return;
    try { voice.recognition.start(); } catch (e) { /* already starting */ }
  }

  function stopVoice() {
    voice.wanted = false;
    if (voice.recognition && voice.running) { try { voice.recognition.stop(); } catch (e) { /* done */ } }
  }

  function listenWhile(state) {
    if (!voice.recognition) return;
    const should = state === "correct_pose" || state === "waiting_answer";
    if (should === voice.wanted) return;
    voice.wanted = should;
    if (should) startVoice(); else stopVoice();
  }

  function submitSpoken(text) {
    const value = parseNumber(text);
    if (value === null || !lesson) return;
    const now = Date.now();
    if (value === voice.last && now - voice.at < 1500) return;
    voice.last = value; voice.at = now;
    setTyped(String(value).slice(0, 3));
    sendLesson({ type: "check", value });
  }

  // ---------- socket ----------
  let socket = null;
  let socketReady = false;

  function connect(onOpen) {
    if (socket && socketReady) { onOpen(); return; }
    socket = new WebSocket(`ws://${location.host}/ws`);
    socket.onopen = () => { socketReady = true; onOpen(); };
    socket.onmessage = (e) => onServerMessage(JSON.parse(e.data));
    socket.onclose = () => { socketReady = false; socket = null; };
    socket.onerror = () => { socketReady = false; };
  }

  function sendLesson(payload) {
    if (socket && socketReady) socket.send(JSON.stringify(payload));
  }

  function onServerMessage(m) {
    if (!lesson) return;
    if (m.type === "node_end") {
      if (m.node_id && m.node_id !== lesson.node.id) return;
      lesson.correct = m.correct;
      lesson.total = m.total || lesson.total;
      lesson.endTally = m.tally;
      return finish(false);
    }
    if (m.type !== "state" || m.node !== lesson.node.id) return;
    const fresh = m.state !== lesson.last;
    if (fresh && m.state === "answer_wrong" && lesson.hearts !== null) {
      lesson.hearts -= 1;
      lesson.hit = true;
      if (lesson.hearts <= 0) { sendLesson({ type: "quit" }); return finish(true); }
    }
    if (fresh && m.fact && m.fact !== lesson.fact) { lesson.fact = m.fact; lesson.done += 1; }
    lesson.last = m.state;
    renderPractice(m);
    listenWhile(m.state);
    if (m.tally && m.tally !== lesson.said) { lesson.said = m.tally; speak(m.tally); }
  }

  // ---------- the view ----------
  function startLesson(id) {
    const node = L.findNode(id);
    const total = node.kind === "boss" ? L.BOSS_QUESTIONS : L.LESSON_QUESTIONS;
    lesson = {
      node, total, correct: 0, done: 0, fact: null, last: null, said: null,
      hearts: node.kind === "boss" ? 3 : null, hit: false, typed: "", t0: Date.now(),
    };
    openNode = null;
    show("lesson");
    setupVoice();
    shellPractice();
    connect(() => sendLesson({
      type: "start_node",
      state: null,
      node: { id: node.id, kind: node.kind, pairs: node.pairs, count: total },
    }));
  }

  function shellPractice() {
    const s = lesson;
    const hearts = s.hearts === null ? "" :
      `<div class="hearts" aria-label="${s.hearts} hearts">${ICON.heart}${s.hearts}</div>`;
    $("#lesson").innerHTML = `
      <div class="lesson-top">
        <button class="icon-btn" data-action="quit" aria-label="Quit lesson">${ICON.close}</button>
        <div class="bar lesson-bar"><div class="bar-fill" style="width:0%"></div></div>
        ${hearts}
      </div>
      <div class="practice">
        <div class="practice-stage">
          <img class="practice-video" src="/video" alt="">
          <svg class="practice-overlay" viewBox="0 0 1.333 1" preserveAspectRatio="none"></svg>
          <div class="practice-head"><span class="practice-exercise">Getting ready</span>
            <span class="practice-why"></span></div>
          <div class="practice-reasoning" hidden></div>
          <div class="tally" data-mood="ready">${tallySvg()}<p class="tally-say">Show me both hands.</p></div>
          <div class="practice-answer">
            <button class="mic" type="button" data-action="mic" hidden aria-label="say the answer">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                   stroke-linecap="round" aria-hidden="true">
                <rect x="9" y="2" width="6" height="11" rx="3"></rect>
                <path d="M5 11a7 7 0 0 0 14 0"></path><path d="M12 18v4"></path>
              </svg>
            </button>
            <span class="typed"></span><span class="caret"></span>
            <div class="heard"></div>
          </div>
        </div>
      </div>`;
    markMic();
  }

  function fitOverlay() {
    const stage = $(".practice-stage"), video = $(".practice-video"), overlay = $(".practice-overlay");
    if (!stage || !video || !overlay) return 4 / 3;
    const W = stage.clientWidth, H = stage.clientHeight;
    const ratio = (video.naturalWidth && video.naturalHeight) ? video.naturalWidth / video.naturalHeight : 4 / 3;
    const w = Math.min(W, H * ratio), h = w / ratio;
    overlay.setAttribute("viewBox", `0 0 ${ratio} 1`);
    overlay.style.cssText = `left:${(W - w) / 2}px;top:${(H - h) / 2}px;width:${w}px;height:${h}px`;
    return ratio;
  }

  function renderPractice(m) {
    const s = lesson;
    const bar = $("#lesson .bar-fill");
    if (bar) bar.style.width = `${Math.min(100, (s.done ? s.done - 1 : 0) / s.total * 100)}%`;
    const hearts = $("#lesson .hearts");
    if (hearts && s.hearts !== null) {
      hearts.innerHTML = ICON.heart + s.hearts;
      if (s.hit) { hearts.classList.add("is-hit"); setTimeout(() => hearts.classList.remove("is-hit"), 420); s.hit = false; }
    }
    const title = $(".practice-exercise");
    if (title) title.textContent = (m.exercise || "").replace(" x ", " × ") || "Getting ready";
    const why = $(".practice-why");
    if (why) why.textContent = s.done ? `${Math.min(s.done, s.total)} of ${s.total}` : "";
    const say = $(".tally-say");
    if (say) say.textContent = m.tally || "";
    setMood(m.reaction === "cannot_see" ? "squint" : (MOOD[m.state] || "ready"));
    $(".practice-stage").dataset.state = m.state;

    const band = $(".practice-reasoning");
    if (band) {
      const lines = m.reasoning || [];
      band.hidden = lines.length === 0;
      band.innerHTML = lines.map((line, i) => `<div style="animation-delay:${i * 300}ms">${line}</div>`).join("");
    }
    drawFingers(m);
  }

  function drawFingers(m) {
    const overlay = $(".practice-overlay");
    if (!overlay) return;
    const span = fitOverlay();
    overlay.replaceChildren();
    const NS = "http://www.w3.org/2000/svg";
    const key = (f) => `${f.hand}:${f.number}`;
    const wrong = new Set((m.wrong || []).map(key));
    const match = new Set((m.match || []).map(key));
    // The ghost is the finger the child should be using: hint level 2 and up
    // rings it, because naming a number is not enough when you are seven.
    const hint = m.hint || {};
    const ghost = (m.hint_level >= 2 && hint.hand && hint.move_to) ? `${hint.hand}:${hint.move_to}` : null;

    for (const finger of m.fingers || []) {
      const id = key(finger);
      const colour = wrong.has(id) ? "#ff9600" : match.has(id) ? "#58cc02" : "#ffffff";
      if (id === ghost) {
        const ring = document.createElementNS(NS, "circle");
        ring.setAttribute("class", "ghost");
        ring.setAttribute("cx", finger.x * span); ring.setAttribute("cy", finger.y);
        ring.setAttribute("r", 0.055);
        overlay.appendChild(ring);
      }
      const circle = document.createElementNS(NS, "circle");
      circle.setAttribute("cx", finger.x * span); circle.setAttribute("cy", finger.y);
      circle.setAttribute("r", 0.036);
      circle.setAttribute("fill", "rgba(11,13,16,0.72)");
      circle.setAttribute("stroke", colour);
      circle.setAttribute("stroke-width", 0.006);
      overlay.appendChild(circle);
      const text = document.createElementNS(NS, "text");
      text.setAttribute("x", finger.x * span); text.setAttribute("y", finger.y + 0.019);
      text.setAttribute("fill", colour);
      text.textContent = finger.number;
      overlay.appendChild(text);
    }
  }

  function setTyped(value) {
    if (!lesson) return;
    lesson.typed = value;
    const el = $(".typed");
    if (el) el.textContent = value;
  }

  function pick(list) {
    return list[Math.floor(Math.random() * list.length)];
  }

  function finish(outOfHearts) {
    const s = lesson;
    stopVoice();
    const total = s.total;
    const res = L.recordLesson(progress, s.node.id, s.correct, total, today());
    progress = res.progress;
    save();
    justUnlocked = res.unlocked;
    const secs = Math.round((Date.now() - s.t0) / 1000);
    const time = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
    const acc = Math.round((s.correct / total) * 100);
    const fail = outOfHearts || res.stars === 0;
    const title = outOfHearts ? "Out of hearts" : fail ? "Almost there" : s.node.kind === "boss" ? "Boss defeated!" : res.stars === 3 ? "Perfect lesson!" : "Lesson complete!";
    const need = Math.ceil(total * 0.6);
    const sub = fail ? `Get ${need} of ${total} right to earn a star and unlock the next level.`
      : res.unlocked ? "A new level is waiting for you." : "Keep going for all three stars.";
    $("#finish").className = `finish${fail ? " is-fail" : ""}`;
    $("#finish").innerHTML = `
      ${fail ? "" : confetti()}
      <div class="finish-inner">
        <div class="big-stars">${[0, 1, 2].map((i) => ICON.star(i < res.stars ? "#ffc800" : "#e5e5e5")).join("")}</div>
        <h1>${title}</h1>
        <p class="finish-sub">${sub}</p>
        <div class="cards">
          <div class="card"><div class="card-label">Total XP</div><div class="card-value">${ICON.bolt}${res.xpGained}</div></div>
          <div class="card card-green"><div class="card-label">${acc >= 80 ? "Amazing" : "Accuracy"}</div><div class="card-value">${ICON.target}${acc}%</div></div>
          <div class="card card-blue"><div class="card-label">Speedy</div><div class="card-value">${ICON.clock}${time}</div></div>
        </div>
        <button class="btn" data-action="${fail ? "retry" : "home"}" data-node="${s.node.id}">${fail ? "Try again" : "Continue"}</button>
        ${fail ? `<button class="finish-alt" data-action="home">Back to the map</button>` : ""}
      </div>`;
    show("finish");
    $("#finish").scrollTop = 0;
    if (res.streakUp) setTimeout(() => toast(`${ICON.flame(true)}${res.streak} day streak`), 1500);
  }

  function confetti() {
    const colors = ["#58cc02", "#1cb0f6", "#ffc800", "#ff4b4b", "#ce82ff", "#ff9600"];
    let out = `<div class="confetti" aria-hidden="true">`;
    for (let i = 0; i < 46; i++) {
      const left = Math.random() * 100;
      const dur = 1.8 + Math.random() * 1.6;
      const delay = Math.random() * 0.7;
      const dx = `${Math.round(Math.random() * 160 - 80)}px`;
      const rot = `${Math.round(Math.random() * 720 - 360)}deg`;
      out += `<i style="left:${left}%;background:${colors[i % colors.length]};animation-duration:${dur}s;animation-delay:${delay}s;--dx:${dx};--rot:${rot}"></i>`;
    }
    return out + "</div>";
  }

  function toast(html) {
    const t = $("#toast");
    t.innerHTML = html;
    t.classList.add("is-on");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => t.classList.remove("is-on"), 2600);
  }

  function backToMap() {
    if (lesson) sendLesson({ type: "quit" });
    stopVoice();
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    lesson = null;
    show("map");
    renderAll();
    const target = justUnlocked && document.querySelector(`[data-node="${justUnlocked}"]`);
    if (target) target.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => { justUnlocked = null; }, 900);
  }

  // ---------- events ----------
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-action]");
    if (!el) {
      if (openNode && !e.target.closest(".popover")) { openNode = null; renderCourse(); }
      return;
    }
    const action = el.dataset.action;
    const id = el.dataset.node;
    if (action === "node") {
      toggleNode(id);
    } else if (action === "start") {
      startLesson(id);
    } else if (action === "chest") {
      const res = L.claimChest(progress, id, today());
      if (res.error) return;
      progress = res.progress;
      save();
      openNode = null;
      justUnlocked = res.unlocked;
      renderAll();
      toast(`${ICON.chest("done")}+${res.xpGained} XP from the chest`);
      setTimeout(() => { justUnlocked = null; }, 900);
    } else if (action === "mic") {
      voice.wanted = !voice.wanted;
      if (voice.wanted) startVoice(); else stopVoice();
    } else if (action === "quit" || action === "home") {
      backToMap();
    } else if (action === "retry") {
      startLesson(id);
    } else if (action === "reset") {
      progress = L.emptyProgress();
      openNode = null;
      save();
      renderAll();
      toast("Progress reset");
    }
  });

  document.addEventListener("keydown", (e) => {
    if (!lesson || $("#lesson").hidden) {
      if (e.key === "Escape" && openNode) { openNode = null; renderCourse(); }
      if (e.key === "Enter" && !$("#finish").hidden) {
        const btn = $("#finish .btn");
        if (btn) btn.click();
      }
      return;
    }
    // The keyboard is the fallback for the voice: type the answer, Enter sends.
    if (e.key >= "0" && e.key <= "9") {
      if (lesson.typed.length < 3) setTyped(lesson.typed + e.key);
    } else if (e.key === "Backspace") {
      setTyped(lesson.typed.slice(0, -1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (lesson.typed.length === 0) return;
      sendLesson({ type: "check", value: Number(lesson.typed) });
      setTyped("");
    } else if (e.key === "n") {
      setTyped("");
      sendLesson({ type: "next" });
    } else if (e.key === "Escape") {
      backToMap();
    }
  });

  // ---------- boot ----------
  // The right panel card becomes a live thumbnail once the camera answers, and
  // stays a drawing when the page is opened without the server behind it.
  function watchCamera() {
    const art = $(".panel-camera-art");
    const copy = $(".panel-camera h3");
    const note = $(".panel-camera p");
    if (!art) return;
    const probe = new Image();
    probe.onload = () => {
      art.innerHTML = "";
      art.appendChild(probe);
      art.classList.add("is-live");
      if (copy) copy.textContent = "Tally is watching";
      if (note) note.textContent = "Your hands are on camera. Start a lesson and count with them.";
    };
    probe.alt = "";
    probe.src = "/video";
  }

  document.querySelectorAll("[data-icon]").forEach((el) => {
    const icon = ICON[el.dataset.icon];
    if (typeof icon === "string") el.innerHTML = icon;
  });
  if (params.get("dev") === "1") document.querySelectorAll(".dev-reset").forEach((el) => { el.hidden = false; });
  // A small seam for the browser tests: the number parser and the voice state
  // are the two pieces worth driving from outside.
  window.Tenfold = { parseNumber, voice };

  show("map");
  renderAll();
  watchCamera();
  window.addEventListener("resize", () => { if (lesson) fitOverlay(); });
  const current = L.currentNode(progress);
  if (current) {
    const el = document.querySelector(`[data-node="${current.id}"]`);
    if (el && current.index > 2) el.scrollIntoView({ block: "center" });
  }
})();
