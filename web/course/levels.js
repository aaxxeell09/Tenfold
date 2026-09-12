/*
 * Tenfold levels: pure progression logic, no DOM.
 * Loaded as a classic script in the browser (window.TenfoldLevels) and required by node tests.
 *
 * A course is a list of units; a unit is a list of nodes. A node is a lesson, a chest or a boss.
 * Nodes unlock strictly in order: a node is "current" when every node before it is done.
 * A lesson or boss is done with at least one star; a chest is done once opened.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.TenfoldLevels = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const UNITS = [
    {
      id: "u1", title: "Meet your fingers", subtitle: "Thumbs are 6, pinkies are 10",
      color: "#58CC02", lip: "#58A700",
      nodes: [
        { id: "u1-l1", kind: "lesson", title: "Doubles", pairs: [[6, 6], [7, 7]] },
        { id: "u1-l2", kind: "lesson", title: "Six and seven", pairs: [[6, 7], [7, 6]] },
        { id: "u1-c1", kind: "chest", title: "Chest" },
        { id: "u1-l3", kind: "lesson", title: "Mix it up", pairs: [[6, 6], [6, 7], [7, 7], [7, 6]] },
      ],
    },
    {
      id: "u2", title: "Count the tens", subtitle: "Touching fingers and the ones below",
      color: "#1CB0F6", lip: "#1899D6",
      nodes: [
        { id: "u2-l1", kind: "lesson", title: "Hello eight", pairs: [[6, 8], [8, 6], [7, 8]] },
        { id: "u2-l2", kind: "lesson", title: "Eights", pairs: [[8, 7], [8, 8]] },
        { id: "u2-c1", kind: "chest", title: "Chest" },
        { id: "u2-l3", kind: "lesson", title: "Up to eight", pairs: [[6, 8], [7, 8], [8, 8], [8, 6]] },
      ],
    },
    {
      id: "u3", title: "Big numbers", subtitle: "Nines, tens and the boss",
      color: "#CE82FF", lip: "#A568CC",
      nodes: [
        { id: "u3-l1", kind: "lesson", title: "Nines", pairs: [[9, 6], [9, 7], [9, 8], [9, 9]] },
        { id: "u3-l2", kind: "lesson", title: "Tens", pairs: [[10, 6], [10, 8], [10, 9], [10, 10]] },
        { id: "u3-b1", kind: "boss", title: "Boss", pairs: [[7, 8], [8, 9], [9, 9], [6, 10], [10, 10], [8, 6]] },
      ],
    },
  ];

  // horizontal offset of each node on the winding path, in px, repeating
  const OFFSETS = [0, 44, 70, 44, 0, -44, -70, -44];
  const LESSON_QUESTIONS = 5;
  const BOSS_QUESTIONS = 8;
  const XP_PER_CORRECT = 10;
  const CHEST_XP = 25;
  const DAILY_GOAL = 30;

  function allNodes() {
    const out = [];
    UNITS.forEach((unit, unitIndex) => {
      unit.nodes.forEach((node, indexInUnit) => {
        out.push(Object.assign({}, node, { unitId: unit.id, unitIndex, indexInUnit, index: out.length }));
      });
    });
    return out;
  }

  function findNode(id) {
    const node = allNodes().find((n) => n.id === id);
    if (!node) throw new Error(`unknown node ${id}`);
    return node;
  }

  function emptyProgress() {
    return { stars: {}, chests: {}, xp: 0, streak: 0, lastDay: null, xpByDay: {} };
  }

  function clone(p) {
    return JSON.parse(JSON.stringify(Object.assign(emptyProgress(), p)));
  }

  function isDone(p, node) {
    if (node.kind === "chest") return Boolean(p.chests && p.chests[node.id]);
    return ((p.stars && p.stars[node.id]) || 0) >= 1;
  }

  function nodeState(p, id) {
    const nodes = allNodes();
    const i = nodes.findIndex((n) => n.id === id);
    if (i < 0) throw new Error(`unknown node ${id}`);
    if (isDone(p, nodes[i])) return "done";
    for (let j = 0; j < i; j++) if (!isDone(p, nodes[j])) return "locked";
    return "current";
  }

  function currentNode(p) {
    return allNodes().find((n) => !isDone(p, n)) || null;
  }

  function starsFor(correct, total) {
    if (!total) return 0;
    const ratio = correct / total;
    if (ratio >= 1) return 3;
    if (ratio >= 0.8) return 2;
    if (ratio >= 0.6) return 1;
    return 0;
  }

  function addDays(day, n) {
    const [y, m, d] = day.split("-").map(Number);
    return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
  }

  function bumpStreak(q, today) {
    if (q.lastDay === today) return;
    q.streak = q.lastDay === addDays(today, -1) ? q.streak + 1 : 1;
    q.lastDay = today;
  }

  function nextId(node) {
    const nodes = allNodes();
    return nodes[node.index + 1] ? nodes[node.index + 1].id : null;
  }

  function recordLesson(p, id, correct, total, today) {
    const node = findNode(id);
    if (node.kind === "chest") throw new Error("use claimChest for chests");
    if (nodeState(p, id) === "locked") return { progress: p, error: "locked" };
    const q = clone(p);
    const stars = starsFor(correct, total);
    const before = q.stars[id] || 0;
    q.stars[id] = Math.max(before, stars);
    const xpGained = correct * XP_PER_CORRECT;
    q.xp += xpGained;
    q.xpByDay[today] = (q.xpByDay[today] || 0) + xpGained;
    const streakBefore = q.streak;
    bumpStreak(q, today);
    const unlocked = before < 1 && q.stars[id] >= 1 ? nextId(node) : null;
    return { progress: q, stars, best: q.stars[id], xpGained, streak: q.streak, streakUp: q.streak > streakBefore, unlocked };
  }

  function claimChest(p, id, today) {
    const node = findNode(id);
    if (node.kind !== "chest") throw new Error("not a chest");
    if (nodeState(p, id) !== "current") return { progress: p, error: nodeState(p, id) };
    const q = clone(p);
    q.chests[id] = true;
    q.xp += CHEST_XP;
    q.xpByDay[today] = (q.xpByDay[today] || 0) + CHEST_XP;
    return { progress: q, xpGained: CHEST_XP, unlocked: nextId(node) };
  }

  function totals(p) {
    const scored = allNodes().filter((n) => n.kind !== "chest");
    const stars = scored.reduce((s, n) => s + ((p.stars && p.stars[n.id]) || 0), 0);
    return { stars, maxStars: scored.length * 3, done: allNodes().filter((n) => isDone(p, n)).length, nodes: allNodes().length };
  }

  function dailyQuest(p, today) {
    return { goal: DAILY_GOAL, value: Math.min(DAILY_GOAL, (p.xpByDay && p.xpByDay[today]) || 0) };
  }

  // ---------- questions ----------

  function hash(str) {
    let h = 2166136261;
    for (let i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }

  function rng(seed) {
    let t = seed >>> 0;
    return function () {
      t = (t + 0x6d2b79f5) >>> 0;
      let r = Math.imul(t ^ (t >>> 15), 1 | t);
      r ^= r + Math.imul(r ^ (r >>> 7), 61 | r);
      return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
    };
  }

  function shuffle(list, rand) {
    const a = list.slice();
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(rand() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  function tensOf(a, b) {
    return (a - 5) + (b - 5);
  }

  function onesOf(a, b) {
    return (10 - a) * (10 - b);
  }

  function productDistractors(a, b) {
    const answer = a * b;
    const pool = new Set([answer + 1, answer - 1, answer + 10, answer - 10, (a + 1) * b, a * (b + 1), (a - 1) * b, a * (b - 1), tensOf(a, b) * 10 + onesOf(a, b) + 10]);
    return Array.from(pool).filter((v) => v > 0 && v !== answer);
  }

  function tensDistractors(answer) {
    const out = [];
    for (let d = -3; d <= 3; d++) {
      const v = answer + d;
      if (d !== 0 && v >= 0 && v <= 10) out.push(v);
    }
    return out;
  }

  function questions(node, seed) {
    const n = node.kind === "boss" ? BOSS_QUESTIONS : LESSON_QUESTIONS;
    const rand = rng(hash(node.id) + (seed >>> 0));
    const out = [];
    for (let i = 0; i < n; i++) {
      const [a, b] = node.pairs[i % node.pairs.length];
      const type = i % 3 === 1 ? "tens" : "product";
      const answer = type === "product" ? a * b : tensOf(a, b);
      const pool = type === "product" ? productDistractors(a, b) : tensDistractors(answer);
      const options = shuffle([answer].concat(shuffle(pool, rand).slice(0, 3)), rand);
      out.push({
        id: `${node.id}-${i}`, type, a, b, answer, options,
        prompt: type === "product" ? `${a} × ${b} = ?` : `How many tens in ${a} × ${b}?`,
        hint: type === "product" ? `${tensOf(a, b)} tens and ${onesOf(a, b)} ones` : "Touching fingers and every finger below them",
      });
    }
    return out;
  }

  return {
    UNITS, OFFSETS, LESSON_QUESTIONS, BOSS_QUESTIONS, XP_PER_CORRECT, CHEST_XP, DAILY_GOAL,
    allNodes, findNode, emptyProgress, nodeState, currentNode, starsFor, recordLesson, claimChest,
    totals, dailyQuest, questions, tensOf, onesOf, addDays,
  };
});
