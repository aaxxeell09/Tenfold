/*
 * Tests for levels.js, the single source of course structure and progress.
 * Run with: node --test web/course/
 *
 * The original test file was lost; this is rebuilt from the module's contract.
 * The camera lesson calls recordLesson with correct out of the node length, so
 * the star thresholds and the unlock chain are what the whole course rests on.
 */
const test = require("node:test");
const assert = require("node:assert");
const L = require("./levels.js");

const TODAY = "2026-09-13";

function fresh() {
  return L.emptyProgress();
}

function beat(progress, id, correct, total, day) {
  return L.recordLesson(progress, id, correct, total, day || TODAY).progress;
}

test("the course is units of nodes, each node once", () => {
  const nodes = L.allNodes();
  assert.ok(nodes.length > 0);
  assert.strictEqual(new Set(nodes.map((n) => n.id)).size, nodes.length);
  for (const node of nodes) {
    assert.ok(["lesson", "chest", "boss"].includes(node.kind), node.kind);
    if (node.kind !== "chest") {
      assert.ok(Array.isArray(node.pairs) && node.pairs.length > 0, `${node.id} has no pairs`);
      for (const [a, b] of node.pairs) {
        assert.ok(a >= 6 && a <= 10 && b >= 6 && b <= 10, `${node.id} has ${a}x${b}`);
      }
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
  const cases = [
    [5, 5, 3], [4, 5, 2], [3, 5, 1], [2, 5, 0], [0, 5, 0],
    [8, 8, 3], [7, 8, 2], [5, 8, 1], [4, 8, 0], [0, 0, 0],
  ];
  for (const [correct, total, stars] of cases) {
    assert.strictEqual(L.starsFor(correct, total), stars, `${correct}/${total}`);
  }
});

test("nodes unlock strictly in order", () => {
  const nodes = L.allNodes();
  let progress = fresh();
  assert.strictEqual(L.nodeState(progress, nodes[0].id), "current");
  assert.strictEqual(L.nodeState(progress, nodes[1].id), "locked");

  progress = beat(progress, nodes[0].id, 5, 5);
  assert.strictEqual(L.nodeState(progress, nodes[0].id), "done");
  assert.strictEqual(L.nodeState(progress, nodes[1].id), "current");
  assert.strictEqual(L.nodeState(progress, nodes[2].id), "locked");
});

test("one star is enough to unlock, zero is not", () => {
  const first = L.allNodes()[0];
  let progress = beat(fresh(), first.id, 2, 5);
  assert.strictEqual(L.nodeState(progress, first.id), "current", "no star, still to do");
  progress = beat(progress, first.id, 3, 5);
  assert.strictEqual(L.nodeState(progress, first.id), "done");
});

test("a locked node records nothing", () => {
  const second = L.allNodes()[1];
  const result = L.recordLesson(fresh(), second.id, 5, 5, TODAY);
  assert.strictEqual(result.error, "locked");
  assert.deepStrictEqual(result.progress, fresh());
});

test("a replay keeps the best star count", () => {
  const first = L.allNodes()[0];
  let progress = beat(fresh(), first.id, 5, 5);
  assert.strictEqual(progress.stars[first.id], 3);
  progress = beat(progress, first.id, 3, 5);
  assert.strictEqual(progress.stars[first.id], 3, "a weaker replay must not take stars away");
});

test("xp follows the correct answers and the daily quest tracks it", () => {
  const first = L.allNodes()[0];
  const result = L.recordLesson(fresh(), first.id, 4, 5, TODAY);
  assert.strictEqual(result.xpGained, 4 * L.XP_PER_CORRECT);
  assert.strictEqual(result.progress.xp, 4 * L.XP_PER_CORRECT);
  const quest = L.dailyQuest(result.progress, TODAY);
  assert.strictEqual(quest.goal, L.DAILY_GOAL);
  assert.strictEqual(quest.value, Math.min(L.DAILY_GOAL, 40));
  assert.strictEqual(L.dailyQuest(result.progress, "2026-09-14").value, 0);
});

test("the streak counts consecutive days and resets after a gap", () => {
  const first = L.allNodes()[0];
  let progress = beat(fresh(), first.id, 5, 5, "2026-09-11");
  assert.strictEqual(progress.streak, 1);
  progress = beat(progress, first.id, 5, 5, "2026-09-12");
  assert.strictEqual(progress.streak, 2);
  progress = beat(progress, first.id, 5, 5, "2026-09-12");
  assert.strictEqual(progress.streak, 2, "twice in one day is still one day");
  progress = beat(progress, first.id, 5, 5, "2026-09-15");
  assert.strictEqual(progress.streak, 1, "a missed day starts again");
});

test("finishing a node unlocks the next one, once", () => {
  const [first, second] = L.allNodes();
  const opened = L.recordLesson(fresh(), first.id, 5, 5, TODAY);
  assert.strictEqual(opened.unlocked, second.id);
  const again = L.recordLesson(opened.progress, first.id, 5, 5, TODAY);
  assert.strictEqual(again.unlocked, null, "already unlocked, no second announcement");
});

test("a chest opens once and only when it is current", () => {
  const chest = L.allNodes().find((n) => n.kind === "chest");
  assert.ok(chest, "the course needs a chest");
  assert.ok(L.claimChest(fresh(), chest.id, TODAY).error, "locked at the start");
  assert.throws(() => L.claimChest(fresh(), L.allNodes()[0].id, TODAY), /not a chest/);
  assert.throws(() => L.recordLesson(fresh(), chest.id, 1, 1, TODAY), /use claimChest/);

  let progress = fresh();
  for (const node of L.allNodes()) {
    if (node.id === chest.id) break;
    progress = beat(progress, node.id, 5, 5);
  }
  const opened = L.claimChest(progress, chest.id, TODAY);
  assert.strictEqual(opened.xpGained, L.CHEST_XP);
  assert.strictEqual(L.nodeState(opened.progress, chest.id), "done");
  assert.ok(L.claimChest(opened.progress, chest.id, TODAY).error, "a chest opens once");
});

test("totals ignore chests and count every scored node", () => {
  const totals = L.totals(fresh());
  const scored = L.allNodes().filter((n) => n.kind !== "chest").length;
  assert.strictEqual(totals.maxStars, scored * 3);
  assert.strictEqual(totals.stars, 0);
  assert.strictEqual(totals.done, 0);
  assert.strictEqual(totals.nodes, L.allNodes().length);
});

test("currentNode walks forward as nodes are finished", () => {
  let progress = fresh();
  const first = L.currentNode(progress);
  assert.strictEqual(first.id, L.allNodes()[0].id);
  progress = beat(progress, first.id, 5, 5);
  assert.notStrictEqual(L.currentNode(progress).id, first.id);
});

test("the boss is longer than a lesson", () => {
  const boss = L.allNodes().find((n) => n.kind === "boss");
  assert.ok(boss, "the course needs a boss");
  assert.strictEqual(L.BOSS_QUESTIONS, 8);
  assert.strictEqual(L.LESSON_QUESTIONS, 5);
  assert.ok(L.BOSS_QUESTIONS > L.LESSON_QUESTIONS);
});

test("the reasoning arithmetic matches the fingers", () => {
  for (let a = 6; a <= 10; a++) {
    for (let b = 6; b <= 10; b++) {
      assert.strictEqual(L.tensOf(a, b) * 10 + L.onesOf(a, b), a * b, `${a}x${b}`);
    }
  }
});

test("recordLesson never mutates the progress it was given", () => {
  const before = fresh();
  const snapshot = JSON.stringify(before);
  L.recordLesson(before, L.allNodes()[0].id, 5, 5, TODAY);
  assert.strictEqual(JSON.stringify(before), snapshot);
});
