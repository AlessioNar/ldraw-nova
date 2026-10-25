// Adding at once, and applying the server's difference (EditorCore.optimisticAdd / applyDelta / tweenNodes).
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const C = createRequire(import.meta.url)("../../viewer/editor-core.js");
const near = (a, b, eps = 1e-6) => a.forEach((v, i) => assert.ok(Math.abs(v - b[i]) < eps, `${a} vs ${b}`));
const IDENTITY = [1, 0, 0, 0, 1, 0, 0, 0, 1];

// The MPD the builder would write for a plan of plain entries (what the server's preview holds).
const mpdOf = plan => plan.sections.map(s => [`0 FILE ${s.name}`, ...s.steps.flatMap(step => [
  ...step.flatMap(e => {
    const m = e.matrix ? e.matrix.flat() : e.yaw ? C.rotationMatrix("y", e.yaw) : IDENTITY;
    return [`0 // ${e.id}: x`, `1 ${e.colour} ${e.at.join(" ")} ${m.map(v => Math.round(v * 1e9) / 1e9).join(" ")} ${e.ref}`];
  }), "0 STEP"])].join("\n")).join("\n");
const entry = (id, at, extra = {}) => ({ id, ref: "3001.dat", colour: 4, at, ...extra });
const copy = value => JSON.parse(JSON.stringify(value));
const START = { version: 1, sections: [
  { name: "main.ldr", steps: [[entry("e1", [0, 0, 0]), entry("e2", [40, 0, 0]), entry("rf", [0, -48, 0], { ref: "roof.ldr" })], [entry("e3", [0, -24, 0])]] },
  { name: "roof.ldr", steps: [[entry("r1", [0, 0, 0])]] }] };
const byKey = nodes => Object.fromEntries(nodes.map(n => [n.key, n]));
const sameNodes = (a, b) => {
  assert.deepEqual(Object.keys(byKey(a)).sort(), Object.keys(byKey(b)).sort());
  for (const n of a) {
    const o = byKey(b)[n.key];
    assert.equal(o.colour, n.colour); assert.equal(o.ref, n.ref); assert.equal(o.section, n.section);
    near(o.position, n.position); near(o.matrix, n.matrix); near(o.localPosition, n.localPosition);
  }
};
const placed = plan => C.flatten(mpdOf(plan), plan).nodes;

test("nextEntryId counts the parts shown but not yet confirmed", () => {
  assert.equal(C.nextEntryId(START), "e4");
  const shown = C.optimisticAdd(placed(START), START, "e4", "main.ldr", "3001", 4, [80, 0, 0]);
  assert.equal(C.nextEntryId(START, shown), "e5");
});

test("optimisticAdd shows the part where it was dropped, flagged pending, and leaves the input alone", () => {
  const before = placed(START), snapshot = JSON.stringify(before);
  const shown = C.optimisticAdd(before, START, "e4", "main.ldr", "3001", 4, [80, -24, 20]);
  assert.equal(JSON.stringify(before), snapshot);
  assert.equal(shown.length, before.length + 1);
  const added = shown.at(-1);
  assert.deepEqual([added.key, added.entryId, added.ref, added.colour, added.pending, added.leaf, added.editable],
                   ["e4", "e4", "3001.dat", "4", true, true, true]);
  assert.deepEqual(added.position, [80, -24, 20]);
  assert.equal(C.optimisticAdd(before, START, "e1", "main.ldr", "3001", 4, [0, 0, 0]), null, "an id that is taken is not predicted");
  assert.equal(C.optimisticAdd(before, START, "e4", "nowhere.ldr", "3001", 4, [0, 0, 0]), null);
});

test("a delta of an add gives the nodes (and the plan) a full reload gives", () => {
  const after = copy(START);
  after.sections[0].steps[1].push(entry("e4", [80, -24, 20], { yaw: 90 }));
  const delta = [{ section: 0, step: 1, at: 1, remove: 0, insert: [after.sections[0].steps[1][1]] }];
  const result = C.applyDelta(START, placed(START), delta);
  assert.deepEqual(result.plan, after);
  sameNodes(result.nodes, placed(after));
  assert.equal(START.sections[0].steps[1].length, 1, "the input plan is not changed");
});

test("deltas of a move, a recolour, a delete and the undo of each match a full reload", () => {
  let plan = START, nodes = placed(START);
  const steps = [
    [{ section: 0, step: 0, at: 1, remove: 1, insert: [entry("e2", [60, 0, 20], { matrix: [[0, 0, 1], [0, 1, 0], [-1, 0, 0]] })] }],
    [{ section: 1, step: 0, at: 0, remove: 1, insert: [entry("r1", [0, 0, 0], { colour: 1 })] }],
    [{ section: 0, step: 0, at: 0, remove: 1, insert: [] }],
    [{ section: 0, step: 0, at: 0, remove: 0, insert: [entry("e1", [0, 0, 0])] }],
  ];
  for (const delta of steps) {
    const result = C.applyDelta(plan, nodes, delta);
    assert.ok(result, JSON.stringify(delta));
    plan = result.plan; nodes = result.nodes;
    sameNodes(nodes, placed(plan));
  }
  assert.deepEqual(plan.sections[0].steps[0].map(e => e.id), ["e1", "e2", "rf"]);
});

test("a part added to a sub-model appears in every copy of it", () => {
  const plan = { version: 1, sections: [
    { name: "main.ldr", steps: [[entry("a", [0, 0, 0], { ref: "roof.ldr" }), entry("b", [100, 0, 0], { ref: "roof.ldr", yaw: 90 })]] },
    { name: "roof.ldr", steps: [[entry("r1", [0, 0, 0])]] }] };
  const after = copy(plan);
  after.sections[1].steps[0].push(entry("r2", [0, -24, 0]));
  const result = C.applyDelta(plan, placed(plan), [{ section: 1, step: 0, at: 1, remove: 0, insert: [after.sections[1].steps[0][1]] }]);
  sameNodes(result.nodes, placed(after));
  assert.equal(result.nodes.filter(n => n.entryId === "r2").length, 2);
  const shown = C.optimisticAdd(placed(plan), plan, "r2", "roof.ldr", "3001", 4, [0, -24, 0]);
  assert.equal(shown.filter(n => n.pending).length, 2);
});

test("a delta the picture cannot be worked out from asks for the whole model", () => {
  const nodes = placed(START);
  const relative = { id: "e4", ref: "3001.dat", colour: 4, on: { id: "e1" } };
  assert.equal(C.applyDelta(START, nodes, [{ section: 0, step: 1, at: 1, remove: 0, insert: [relative] }]), null);
  assert.equal(C.applyDelta(START, nodes, [{ section: 0, step: 1, at: 1, remove: 0, insert: [entry("e4", [0, 0, 0], { ref: "roof.ldr" })] }]), null);
  assert.equal(C.applyDelta(START, nodes, [{ section: 0, step: 1, at: 1, remove: 0, insert: [entry("e4", [0, 0, 0], { colour: "@colours.red" })] }]), null);
  assert.equal(C.applyDelta(START, nodes, [{ section: 5, step: 0, at: 0, remove: 0, insert: [] }]), null);
  assert.equal(C.applyDelta(START, nodes, [{ section: 0, step: 0, at: 1, remove: 9, insert: [] }]), null);
  const dependent = copy(START);
  dependent.sections[0].steps[1][0] = { id: "e3", ref: "3001.dat", colour: 4, snap: { to: "e1" } };
  assert.equal(C.applyDelta(dependent, nodes, [{ section: 0, step: 0, at: 0, remove: 1, insert: [] }]), null, "something placed relative to others");
});

test("a queue of adds: each shows at once, the answers confirm them in order, a refusal falls back to the last answer", () => {
  // The picture is the confirmed nodes plus every add still queued; the confirmed side advances one delta at a time.
  let plan = START, confirmed = placed(START), shown = confirmed;
  const queued = [];
  for (const at of [[80, 0, 0], [120, 0, 0], [160, 0, 0]]) {
    const id = C.nextEntryId(plan, shown);
    shown = C.optimisticAdd(shown, plan, id, "main.ldr", "3001", 4, at);
    queued.push({ id, at });
  }
  assert.deepEqual(queued.map(q => q.id), ["e4", "e5", "e6"]);
  assert.equal(shown.filter(n => n.pending).length, 3);
  const confirm = ({ id, at }) => {
    const at1 = plan.sections[0].steps[1].length;
    const result = C.applyDelta(plan, confirmed, [{ section: 0, step: 1, at: at1, remove: 0, insert: [entry(id, at)] }]);
    plan = result.plan; confirmed = result.nodes;
    sameNodes(confirmed, placed(plan));
  };
  confirm(queued[0]); confirm(queued[1]);
  assert.equal(confirmed.filter(n => n.pending).length, 0);
  assert.deepEqual(shown.filter(n => n.pending).map(n => n.key), ["e4", "e5", "e6"], "the picture still shows its own copies");
  // The third is refused: the picture goes back to the last confirmed nodes and nothing of the add stays.
  shown = confirmed;
  assert.equal(shown.some(n => n.key === "e6"), false);
  assert.equal(C.nextEntryId(plan, shown), "e6");
});

test("tweenNodes slides what moved and leaves the rest", () => {
  const from = [{ key: "a", position: [0, 0, 0] }, { key: "b", position: [5, 5, 5] }, { key: "gone", position: [1, 1, 1] }];
  const to = [{ key: "a", position: [10, -20, 30] }, { key: "b", position: [5, 5, 5] }, { key: "new", position: [9, 9, 9] }];
  const half = C.tweenNodes(from, to, 0.5);
  assert.deepEqual(half[0].position, [5, -10, 15]);
  assert.equal(half[1], to[1]);
  assert.equal(half[2], to[2]);
  assert.equal(C.tweenNodes(from, to, 1)[0], to[0]);
});
