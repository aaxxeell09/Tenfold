/*
 * Tenfold levels: pure progression logic, no DOM.
 * Loaded as a classic script in the browser (window.TenfoldLevels) and required by node tests.
 *
 * A course is a list of units; a unit is a list of nodes. A node is a lesson, a chest or a boss.
 * Nodes unlock strictly in order: a node is "current" when every node before it is done.
 * A lesson or boss is done with at least one star; a chest is done once opened.
 *
 * The only gamification is XP and Tally's ten levels. XP is never lost: 10 per correct
 * exercise, 15 when it was right first try, 25 for finishing a lesson, 100 for a boss.
 * XP lives in the learner profile, not here; this module only computes it.
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
      nodes: [
        { id: "u1-l1", kind: "lesson", title: "Doubles", pairs: [[6, 6], [7, 7]] },
        { id: "u1-l2", kind: "lesson", title: "Six and seven", pairs: [[6, 7], [7, 6]] },
        { id: "u1-c1", kind: "chest", title: "Chest" },
        { id: "u1-l3", kind: "lesson", title: "Mix it up", pairs: [[6, 6], [6, 7], [7, 7], [7, 6]] },
      ],
    },
    {
      id: "u2", title: "Count the tens", subtitle: "Touching fingers and the ones below",
      nodes: [
        { id: "u2-l1", kind: "lesson", title: "Hello eight", pairs: [[6, 8], [8, 6], [7, 8]] },
        { id: "u2-l2", kind: "lesson", title: "Eights", pairs: [[8, 7], [8, 8]] },
        { id: "u2-c1", kind: "chest", title: "Chest" },
        { id: "u2-l3", kind: "lesson", title: "Up to eight", pairs: [[6, 8], [7, 8], [8, 8], [8, 6]] },
      ],
    },
    {
      id: "u3", title: "Big numbers", subtitle: "Nines, tens and the boss",
      nodes: [
        { id: "u3-l1", kind: "lesson", title: "Nines", pairs: [[9, 6], [9, 7], [9, 8], [9, 9]] },
        { id: "u3-l2", kind: "lesson", title: "Tens", pairs: [[10, 6], [10, 8], [10, 9], [10, 10]] },
        { id: "u3-b1", kind: "boss", title: "Boss", pairs: [[7, 8], [8, 9], [9, 9], [6, 10], [10, 10], [8, 6]] },
      ],
    },
  ];

  const LESSON_QUESTIONS = 5;
  const BOSS_QUESTIONS = 8;

  // ---------- XP and levels ----------
  const XP_CORRECT = 10;
  const XP_FIRST_TRY = 15;
  const XP_LESSON = 25;
  const XP_BOSS = 100;

  // cumulative thresholds: a level is reached once total XP passes its "at"
  const LEVELS = [
    { name: "Thumb Buddy", at: 0 },
    { name: "Finger Counter", at: 100 },
    { name: "Tip Toucher", at: 250 },
    { name: "Ten Maker", at: 450 },
    { name: "Sixes Master", at: 700 },
    { name: "Sevens Tamer", at: 1000 },
    { name: "Eights Rider", at: 1400 },
    { name: "Nines Wizard", at: 1900 },
    { name: "Tens Champion", at: 2500 },
    { name: "Tenfold Hero", at: 3200 },
  ];
  const ACCESSORIES = { 2: "glasses", 4: "headband", 6: "hat", 8: "cape", 10: "crown" };
  const ACCESSORY_NAMES = { glasses: "glasses", headband: "headband", hat: "wizard hat", cape: "cape", crown: "crown" };

  function xpForNode(kind, correct, firstTry, finished) {
    const first = Math.max(0, Math.min(correct || 0, firstTry || 0));
    const rest = Math.max(0, (correct || 0) - first);
    let xp = first * XP_FIRST_TRY + rest * XP_CORRECT;
    if (finished) xp += kind === "boss" ? XP_BOSS : XP_LESSON;
    return xp;
  }

  function levelFor(xp) {
    let level = 1;
    for (let i = 0; i < LEVELS.length; i++) if ((xp || 0) >= LEVELS[i].at) level = i + 1;
    return level;
  }

  function levelInfo(xp) {
    const total = Math.max(0, Math.floor(xp || 0));
    const level = levelFor(total);
    const floor = LEVELS[level - 1].at;
    const top = level >= LEVELS.length;
    const ceiling = top ? null : LEVELS[level].at;
    const span = top ? LEVELS[level - 1].at - LEVELS[level - 2].at : ceiling - floor;
    const inLevel = top ? span : total - floor;
    return {
      xp: total, level, name: LEVELS[level - 1].name, floor, ceiling, span, inLevel,
      percent: Math.round(inLevel / span * 100),
      next: top ? null : LEVELS[level].name,
      toNext: top ? 0 : ceiling - total,
      accessories: accessoriesFor(level),
    };
  }

  function accessoriesFor(level) {
    return Object.keys(ACCESSORIES).map(Number).filter((k) => k <= level).sort((a, b) => a - b)
      .map((k) => ACCESSORIES[k]).join(" ");
  }

  function earnedAt(level) {
    return ACCESSORIES[level] || null;
  }

  // Every accessory won between two levels, the level left behind excluded. A single
  // node can cross two levels at once (a perfect boss is 220 XP), and the accessory
  // of the level passed through has to be announced as well.
  function earnedBetween(before, after) {
    const from = Math.floor(Number(before) || 0), to = Math.floor(Number(after) || 0);
    const out = [];
    for (let level = from + 1; level <= to; level++) {
      const accessory = earnedAt(level);
      if (accessory) out.push(accessory);
    }
    return out;
  }

  // ---------- nodes and progress ----------
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
    return { stars: {}, chests: {} };
  }

  function clone(p) {
    const q = Object.assign(emptyProgress(), p || {});
    return { stars: Object.assign({}, q.stars), chests: Object.assign({}, q.chests) };
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

  function nextId(node) {
    const nodes = allNodes();
    return nodes[node.index + 1] ? nodes[node.index + 1].id : null;
  }

  function recordLesson(p, id, correct, total) {
    const node = findNode(id);
    if (node.kind === "chest") throw new Error("use claimChest for chests");
    if (nodeState(p, id) === "locked") return { progress: p, error: "locked" };
    const q = clone(p);
    const stars = starsFor(correct, total);
    const before = q.stars[id] || 0;
    q.stars[id] = Math.max(before, stars);
    const unlocked = before < 1 && q.stars[id] >= 1 ? nextId(node) : null;
    return { progress: q, stars, best: q.stars[id], unlocked };
  }

  function claimChest(p, id) {
    const node = findNode(id);
    if (node.kind !== "chest") throw new Error("not a chest");
    if (nodeState(p, id) !== "current") return { progress: p, error: nodeState(p, id) };
    const q = clone(p);
    q.chests[id] = true;
    return { progress: q, unlocked: nextId(node) };
  }

  function totals(p) {
    const scored = allNodes().filter((n) => n.kind !== "chest");
    const stars = scored.reduce((s, n) => s + ((p.stars && p.stars[n.id]) || 0), 0);
    return { stars, maxStars: scored.length * 3, done: allNodes().filter((n) => isDone(p, n)).length, nodes: allNodes().length };
  }

  function tensOf(a, b) {
    return (a - 5) + (b - 5);
  }

  function onesOf(a, b) {
    return (10 - a) * (10 - b);
  }

  return {
    UNITS, LESSON_QUESTIONS, BOSS_QUESTIONS,
    XP_CORRECT, XP_FIRST_TRY, XP_LESSON, XP_BOSS, LEVELS, ACCESSORIES, ACCESSORY_NAMES,
    xpForNode, levelFor, levelInfo, accessoriesFor, earnedAt, earnedBetween,
    allNodes, findNode, emptyProgress, nodeState, currentNode, starsFor, recordLesson, claimChest,
    totals, tensOf, onesOf,
  };
});
