/*
 * Tests for levels.js, the single source of course structure, unlocking, XP and levels.
 * Run with: node --test web/course/levels_test.js
 */
const test = require("node:test");
const assert = require("node:assert");
const L = require("./levels.js");

function fresh() { return L.emptyProgress(); }
function beat(progress, id, correct, total) { return L.recordLesson(progress, id, correct, total).progress; }

test("the course is units of nodes, each node once", () => {
  const nodes = L.allNodes();
  assert.ok(nodes.length > 0);
  assert.strictEqual(new Set(nodes.map((n) => n.id)).size, nodes.length);
  for (const node of nodes) {
    assert.ok(["lesson", "chest", "boss"].includes(node.kind), node.kind);
    if (node.kind !== "chest") {
      assert.ok(Array.isArray(node.pairs) && node.pairs.length > 0, `${node.id} has no pairs`);
      for (const [a, b] of node.pairs) assert.ok(a >= 6 && a <= 10 && b >= 6 && b <= 10, `${node.id} has ${a}x${b}`);
    }
  }
});

test("every node index is stable and findable", () => {
  L.allNodes().forEach((node, i) => {
    assert.strictEqual(node.index, i);
    assert.strictEqual(L.findNode(node.id).id, node.id);
  });
  assert.throws(() => L.findNode("nope"), /unknown node/);
});

test("stars come from the ratio, not the count", () => {
  for (const [correct, total, stars] of [[5, 5, 3], [4, 5, 2], [3, 5, 1], [2, 5, 0], [0, 5, 0], [8, 8, 3], [7, 8, 2], [5, 8, 1], [4, 8, 0], [0, 0, 0]]) {
    assert.strictEqual(L.starsFor(correct, total), stars, `${correct}/${total}`);
  }
});

test("nodes unlock strictly in order, one star is enough", () => {
  const nodes = L.allNodes();
  let progress = fresh();
  assert.strictEqual(L.nodeState(progress, nodes[0].id), "current");
  assert.strictEqual(L.nodeState(progress, nodes[1].id), "locked");
  progress = beat(progress, nodes[0].id, 2, 5);
  assert.strictEqual(L.nodeState(progress, nodes[0].id), "current", "no star, still to do");
  progress = beat(progress, nodes[0].id, 3, 5);
  assert.strictEqual(L.nodeState(progress, nodes[0].id), "done");
  assert.strictEqual(L.nodeState(progress, nodes[1].id), "current");
  assert.strictEqual(L.nodeState(progress, nodes[2].id), "locked");
});

test("a locked node records nothing and a replay keeps the best stars", () => {
  const [first, second] = L.allNodes();
  const locked = L.recordLesson(fresh(), second.id, 5, 5);
  assert.strictEqual(locked.error, "locked");
  let progress = beat(fresh(), first.id, 5, 5);
  progress = beat(progress, first.id, 3, 5);
  assert.strictEqual(progress.stars[first.id], 3, "a weaker replay must not take stars away");
});

test("finishing a node unlocks the next one, once", () => {
  const [first, second] = L.allNodes();
  const opened = L.recordLesson(fresh(), first.id, 5, 5);
  assert.strictEqual(opened.unlocked, second.id);
  assert.strictEqual(L.recordLesson(opened.progress, first.id, 5, 5).unlocked, null);
});

test("a chest opens once, only when current, and gives no XP", () => {
  const chest = L.allNodes().find((n) => n.kind === "chest");
  assert.ok(L.claimChest(fresh(), chest.id).error, "locked at the start");
  assert.throws(() => L.claimChest(fresh(), L.allNodes()[0].id), /not a chest/);
  assert.throws(() => L.recordLesson(fresh(), chest.id, 1, 1), /use claimChest/);
  let progress = fresh();
  for (const node of L.allNodes()) { if (node.id === chest.id) break; progress = beat(progress, node.id, 5, 5); }
  const opened = L.claimChest(progress, chest.id);
  assert.strictEqual(opened.xpGained, undefined);
  assert.strictEqual(L.nodeState(opened.progress, chest.id), "done");
  assert.ok(L.claimChest(opened.progress, chest.id).error, "a chest opens once");
});

test("progress carries stars and chests and nothing else", () => {
  assert.deepStrictEqual(Object.keys(fresh()).sort(), ["chests", "stars"]);
  const before = fresh();
  const snapshot = JSON.stringify(before);
  L.recordLesson(before, L.allNodes()[0].id, 5, 5);
  assert.strictEqual(JSON.stringify(before), snapshot, "never mutates the progress it was given");
});

test("totals ignore chests and count every scored node", () => {
  const totals = L.totals(fresh());
  assert.strictEqual(totals.maxStars, L.allNodes().filter((n) => n.kind !== "chest").length * 3);
  assert.strictEqual(totals.stars, 0);
});

test("the boss is longer than a lesson", () => {
  assert.strictEqual(L.BOSS_QUESTIONS, 8);
  assert.strictEqual(L.LESSON_QUESTIONS, 5);
});

// ---------- XP ----------

test("xp: 10 per correct, 15 first try, 25 per lesson, 100 per boss", () => {
  assert.strictEqual(L.xpForNode("lesson", 0, 0, false), 0);
  assert.strictEqual(L.xpForNode("lesson", 1, 0, false), 10);
  assert.strictEqual(L.xpForNode("lesson", 1, 1, false), 15);
  assert.strictEqual(L.xpForNode("lesson", 5, 3, false), 3 * 15 + 2 * 10);
  assert.strictEqual(L.xpForNode("lesson", 5, 5, true), 5 * 15 + 25);
  assert.strictEqual(L.xpForNode("boss", 8, 8, true), 8 * 15 + 100);
  assert.strictEqual(L.xpForNode("boss", 3, 1, false), 15 + 20, "out of hearts: the exercises still pay, the boss bonus does not");
});

test("xp: first try can never exceed correct and nothing is ever negative", () => {
  assert.strictEqual(L.xpForNode("lesson", 2, 5, false), 30);
  assert.strictEqual(L.xpForNode("lesson", -3, -3, false), 0);
});

test("ten levels with the agreed cumulative thresholds", () => {
  assert.deepStrictEqual(L.LEVELS.map((l) => l.name), [
    "Thumb Buddy", "Finger Counter", "Tip Toucher", "Ten Maker", "Sixes Master",
    "Sevens Tamer", "Eights Rider", "Nines Wizard", "Tens Champion", "Tenfold Hero",
  ]);
  assert.deepStrictEqual(L.LEVELS.map((l) => l.at), [0, 100, 250, 450, 700, 1000, 1400, 1900, 2500, 3200]);
});

test("the level is the last threshold passed", () => {
  for (const [xp, level] of [[0, 1], [99, 1], [100, 2], [249, 2], [250, 3], [450, 4], [700, 5], [1000, 6], [1400, 7], [1900, 8], [2500, 9], [3199, 9], [3200, 10], [9999, 10]]) {
    assert.strictEqual(L.levelFor(xp), level, `${xp} XP`);
  }
});

test("level info describes the bar inside the level", () => {
  const i = L.levelInfo(160);
  assert.strictEqual(i.level, 2);
  assert.strictEqual(i.name, "Finger Counter");
  assert.strictEqual(i.floor, 100);
  assert.strictEqual(i.ceiling, 250);
  assert.strictEqual(i.span, 150);
  assert.strictEqual(i.inLevel, 60);
  assert.strictEqual(i.percent, 40);
  assert.strictEqual(i.next, "Tip Toucher");
  assert.strictEqual(i.toNext, 90);
  const top = L.levelInfo(4000);
  assert.strictEqual(top.level, 10);
  assert.strictEqual(top.next, null);
  assert.strictEqual(top.percent, 100);
  assert.strictEqual(L.levelInfo(-20).xp, 0);
});

test("accessories arrive at levels 2, 4, 6, 8 and 10 and stack up", () => {
  assert.deepStrictEqual(L.ACCESSORIES, { 2: "glasses", 4: "headband", 6: "hat", 8: "cape", 10: "crown" });
  assert.strictEqual(L.accessoriesFor(1), "");
  assert.strictEqual(L.accessoriesFor(2), "glasses");
  assert.strictEqual(L.accessoriesFor(5), "glasses headband");
  assert.strictEqual(L.accessoriesFor(10), "glasses headband hat cape crown");
  assert.strictEqual(L.earnedAt(6), "hat");
  assert.strictEqual(L.earnedAt(3), null);
  assert.strictEqual(L.ACCESSORY_NAMES.hat, "wizard hat");
});

test("every accessory crossed is named, not only the one on the level landed on", () => {
  assert.deepStrictEqual(L.earnedBetween(1, 1), [], "no level up, nothing new");
  assert.deepStrictEqual(L.earnedBetween(1, 2), ["glasses"]);
  assert.deepStrictEqual(L.earnedBetween(1, 3), ["glasses"], "level 3 wears nothing of its own");
  assert.deepStrictEqual(L.earnedBetween(1, 4), ["glasses", "headband"]);
  assert.deepStrictEqual(L.earnedBetween(3, 6), ["headband", "hat"]);
  assert.deepStrictEqual(L.earnedBetween(1, 10), ["glasses", "headband", "hat", "cape", "crown"]);
  assert.deepStrictEqual(L.earnedBetween(6, 4), [], "a level is never lost");
  assert.deepStrictEqual(L.earnedBetween(), []);
});

test("a perfect boss can cross two levels at once", () => {
  const before = L.levelInfo(30);
  const after = L.levelInfo(30 + L.xpForNode("boss", 8, 8, true));
  assert.strictEqual(before.level, 1);
  assert.strictEqual(after.level, 3, "220 XP from 30 goes past 100 and 250");
  assert.strictEqual(L.earnedAt(after.level), null, "the level landed on has no accessory");
  assert.deepStrictEqual(L.earnedBetween(before.level, after.level), ["glasses"],
    "the glasses of the level passed through still have to be announced");
});

test("the reasoning arithmetic matches the fingers", () => {
  for (let a = 6; a <= 10; a++) for (let b = 6; b <= 10; b++) assert.strictEqual(L.tensOf(a, b) * 10 + L.onesOf(a, b), a * b, `${a}x${b}`);
});

test("the level ladder the profile draws is complete and its gear sits inside it", () => {
  assert.strictEqual(L.LEVELS.length, 10);
  for (const level of L.LEVELS) assert.ok(level.name && Number.isFinite(level.at), JSON.stringify(level));
  const gear = Object.keys(L.ACCESSORIES).map(Number);
  assert.strictEqual(gear.length, 5);
  for (const level of gear) {
    assert.ok(level >= 1 && level <= L.LEVELS.length, `gear at level ${level}`);
    assert.ok(L.ACCESSORY_NAMES[L.ACCESSORIES[level]], `no name for ${L.ACCESSORIES[level]}`);
  }
  // the bar under every level name has something to fill, top level included
  for (let xp = 0; xp <= 3600; xp += 60) {
    const info = L.levelInfo(xp);
    assert.ok(info.span > 0 && info.percent >= 0 && info.percent <= 100, `${xp} XP`);
  }
});
