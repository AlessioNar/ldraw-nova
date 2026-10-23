import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const C = createRequire(import.meta.url)("../../viewer/editor-core.js");
const near = (a, b, eps = 1e-6) => a.forEach((v, i) => assert.ok(Math.abs(v - b[i]) < eps, `${a} vs ${b}`));

const MPD = `0 FILE main.ldr
0 Name: main.ldr
0 // e1: base
1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat
0 STEP
0 // e2: top
1 1 0 -24 0 0 0 1 0 1 0 -1 0 0 3001.dat
0 // e3-0: repeated
1 1 40 0 0 1 0 0 0 1 0 0 0 1 3024.dat
0 FILE sub.ldr
0 // s1: other
1 1 5 5 5 1 0 0 0 1 0 0 0 1 3001.dat
`;
const BOX = { min: [-20, -24, -40], max: [20, 0, 40] };
const IDENTITY = [1, 0, 0, 0, 1, 0, 0, 0, 1];

test("parsePlacements reads the root section and keeps the plan ids", () => {
  const found = C.parsePlacements(MPD, "main.ldr");
  assert.deepEqual(found.map(p => p.id), ["e1", "e2", "e3-0"]);
  assert.deepEqual(found[1].position, [0, -24, 0]);
  assert.equal(found[1].ref, "3001.dat");
  assert.equal(found[1].section, "main.ldr");
  assert.deepEqual(C.parsePlacements(MPD, "SUB.ldr").map(p => p.id), ["s1"]);
  assert.deepEqual(C.parsePlacements(MPD).map(p => p.id), ["e1", "e2", "e3-0"]);
});

test("entryIds lists root entries, so repeated copies are not editable", () => {
  const ids = C.entryIds({ sections: [{ steps: [[{ id: "e1" }], [{ id: "e2" }, { id: "e3" }]] }, { steps: [[{ id: "s1" }]] }] });
  assert.deepEqual([...ids], ["e1", "e2", "e3"]);
});

test("sceneToLdraw undoes ldbi's flip and recentring", () => {
  near(C.sceneToLdraw([1, 2, 3], [1, 1, 1]), [0, -1, -2]);
  near(C.ldrawToScene(C.sceneToLdraw([4, 5, 6], [7, 8, 9]), [7, 8, 9]), [4, 5, 6]);
});

test("a ray picks the nearest part, and misses empty space", () => {
  const placements = [
    { id: "far", ref: "3001.dat", position: [0, 0, 0], matrix: IDENTITY },
    { id: "near", ref: "3001.dat", position: [0, 0, -200], matrix: IDENTITY },
  ];
  const boxes = { "3001.dat": BOX };
  const along = { origin: [0, -10, -500], direction: [0, 0, 1] };
  assert.equal(C.pick(along, placements, boxes).id, "near");
  const down = { origin: [0, -500, 0], direction: [0, 1, 0] };
  assert.equal(C.pick(down, placements, boxes).id, "far");
  assert.equal(C.pick({ origin: [300, -500, 0], direction: [0, 1, 0] }, placements, boxes), null);
});

test("the box turns with the part", () => {
  // Turned 90 degrees about Y, the long (z) side lies along x.
  const turned = { id: "t", ref: "3001.dat", position: [0, 0, 0], matrix: C.rotationMatrix("y", 90) };
  assert.ok(C.rayBox({ origin: [35, -500, 0], direction: [0, 1, 0] }, turned, BOX) !== null);
  assert.equal(C.rayBox({ origin: [0, -500, 35], direction: [0, 1, 0] }, turned, BOX), null);
});

test("selection: click replaces, shift toggles, empty click clears", () => {
  assert.deepEqual(C.nextSelection(["a"], "b", false), ["b"]);
  assert.deepEqual(C.nextSelection(["a"], "b", true), ["a", "b"]);
  assert.deepEqual(C.nextSelection(["a", "b"], "a", true), ["b"]);
  assert.deepEqual(C.nextSelection(["a"], null, false), []);
  assert.deepEqual(C.nextSelection(["a"], null, true), ["a"]);
});

test("axisParameter finds the closest point on the axis", () => {
  const s = C.axisParameter({ origin: [60, -100, 0], direction: [0, 1, 0] }, [0, 0, 0], [1, 0, 0]);
  assert.ok(Math.abs(s - 60) < 1e-9);
  assert.equal(C.axisParameter({ origin: [0, 0, 0], direction: [1, 0, 0] }, [0, 0, 0], [1, 0, 0]), null);
});

test("planeAngle measures a turn about an axis, right-handed", () => {
  const atX = { origin: [50, -100, 0], direction: [0, 1, 0] };
  const atZ = { origin: [0, -100, 50], direction: [0, 1, 0] };
  // From +x to +z is +90 degrees about +y (rotationMatrix("y", 90) sends +x to -z; the angle is of the pointer, see rotateEdits).
  assert.ok(Math.abs(C.planeAngle(atX, atZ, [0, 0, 0], [0, 1, 0]) - -90) < 1e-6);
  assert.ok(Math.abs(C.planeAngle(atZ, atX, [0, 0, 0], [0, 1, 0]) - 90) < 1e-6);
});

test("snapping", () => {
  assert.equal(C.snapMove("x", 27), 20);
  assert.equal(C.snapMove("y", -13), -16);
  assert.equal(C.snapMove("x", 27, false), 27);
  assert.equal(C.snapMove("x", 4), 0);
  assert.ok(Object.is(C.snapMove("x", -4), 0));
  assert.equal(C.snapRotate(52), 45);
  assert.equal(C.snapRotate(52, false), 52);
});

test("moveEdits: one move per selected part, nothing for a zero drag", () => {
  const placements = C.parsePlacements(MPD, "main.ldr");
  assert.deepEqual(C.moveEdits(placements, ["e1", "e2"], [20, 0, -40]), [
    { op: "move", id: "e1", section: "main.ldr", position: [20, 0, -40] },
    { op: "move", id: "e2", section: "main.ldr", position: [20, -24, -40] },
  ]);
  assert.deepEqual(C.moveEdits(placements, ["e1"], [0, 0, 0]), []);
  assert.deepEqual(C.moveEdits(placements, ["nope"], [20, 0, 0]), []);
});

test("rotateEdits: one part turns in place, a group turns about its middle", () => {
  const placements = C.parsePlacements(MPD, "main.ldr");
  assert.deepEqual(C.rotateEdits(placements, ["e1"], "y", 90), [{ op: "rotate", id: "e1", section: "main.ldr", axis: "y", degrees: 90 }]);
  assert.deepEqual(C.rotateEdits(placements, ["e1"], "y", 0), []);
  const group = [{ id: "a", section: "s", position: [20, 0, 0] }, { id: "b", section: "s", position: [-20, 0, 0] }];
  const edits = C.rotateEdits(group, ["a", "b"], "y", 90);
  assert.equal(edits.length, 4);
  // +90 about y sends +x to -z, the same matrix the plan's rotate uses.
  near(edits[1].position, [0, 0, -20]);
  near(edits[3].position, [0, 0, 20]);
});

test("deleteEdits and the request body", () => {
  const placements = C.parsePlacements(MPD, "main.ldr");
  assert.deepEqual(C.deleteEdits(placements, ["e2"]), [{ op: "delete", id: "e2", section: "main.ldr" }]);
  assert.deepEqual(C.editsBody([1], 7), { edits: [1], base_revision: 7 });
});

test("describeParts", () => {
  assert.equal(C.describeParts({ rows: [{ used: 2 }, { used: 1 }], short: [] }), "3 parts used");
  assert.equal(C.describeParts({ rows: [{ used: 1 }], short: [{}] }), "1 part used, 1 beyond what you own");
  assert.equal(C.describeParts(null), "");
});

test("fitDistance frames a sphere to the requested fraction and snapStep reports the grid", () => {
  const d = C.fitDistance(100, 45, 2, 0.33);
  assert.ok(Math.abs(100 / Math.sin(Math.PI / 8) / 0.33 - d) < 1e-9);
  assert.ok(C.fitDistance(100, 45, 0.5, 0.33) > d, "narrow viewports sit further back");
  assert.equal(C.snapStep("x"), 20);
  assert.equal(C.snapStep("y"), 8);
});

// ---- sub-models and repeated placements ------------------------------------------------
// The root places the wing sub-model twice (the right one turned 180 degrees about y), a repeated run, and one brick.
const SHIP_MPD = `0 FILE ship.ldr
0 // wl: left wing
1 15 -80 0 0 1 0 0 0 1 0 0 0 1 wing.ldr
0 // wr: right wing
1 15 80 0 0 -1 0 0 0 1 0 0 0 -1 wing.ldr
0 // row-0: run
1 4 0 -24 100 1 0 0 0 1 0 0 0 1 3005.dat
0 // row-1: run
1 4 20 -24 100 1 0 0 0 1 0 0 0 1 3005.dat
0 // row-2: run
1 4 40 -24 100 1 0 0 0 1 0 0 0 1 3005.dat
0 // top: brick
1 1 0 -24 0 1 0 0 0 1 0 0 0 1 3001.dat
0 FILE wing.ldr
0 // w1: panel
1 1 10 0 20 1 0 0 0 1 0 0 0 1 3020.dat
0 // w2: tip
1 1 30 0 60 1 0 0 0 1 0 0 0 1 3020.dat
`;
const SHIP_PLAN = { sections: [
  { name: "ship.ldr", steps: [[{ id: "wl", ref: "wing.ldr", at: [-80, 0, 0] }, { id: "wr", ref: "wing.ldr", at: [80, 0, 0], yaw: 180 }],
    [{ id: "row", ref: "3005.dat", at: [0, -24, 100], repeat: { count: 3, step: [20, 0, 0] } }, { id: "top", ref: "3001.dat", at: [0, -24, 0] }]] },
  { name: "wing.ldr", steps: [[{ id: "w1", ref: "3020.dat", at: [10, 0, 20] }, { id: "w2", ref: "3020.dat", at: [30, 0, 60] }]] },
] };

test("flatten walks into sub-models and puts every part in the root's frame", () => {
  const { nodes, truncated } = C.flatten(SHIP_MPD, SHIP_PLAN);
  assert.equal(truncated, false);
  assert.deepEqual(nodes.map(n => n.key), ["wl", "wl/w1", "wl/w2", "wr", "wr/w1", "wr/w2", "row-0", "row-1", "row-2", "top"]);
  const byKey = Object.fromEntries(nodes.map(n => [n.key, n]));
  assert.deepEqual(byKey["wl/w1"].position, [-70, 0, 20]);
  assert.deepEqual(byKey["wr/w1"].position, [70, 0, -20]);          // the right wing is turned half a circle: x and z flip
  assert.deepEqual(byKey["wl/w1"].localPosition, [10, 0, 20]);
  assert.equal(byKey["wr/w1"].section, "wing.ldr");
  assert.equal(byKey["wl"].leaf, false);
  assert.equal(byKey["wl/w1"].leaf, true);
  assert.equal(byKey["wl/w1"].copies, 2);
  assert.equal(byKey["top"].copies, 1);
  assert.equal(byKey["top"].editable, true);
  assert.equal(byKey["row-1"].entryId, "row");
  assert.equal(byKey["row-1"].repeated, true);
  assert.deepEqual(byKey["row-1"].templateAt, [0, -24, 100]);
});

test("a part inside a turned sub-model moves by the drag as seen from inside it", () => {
  const { nodes } = C.flatten(SHIP_MPD, SHIP_PLAN);
  // Dragging the right wing's panel +40 in world x is -40 in the wing's own x.
  assert.deepEqual(C.moveEdits(nodes, ["wr/w1"], [40, 0, 0]), [{ op: "move", id: "w1", section: "wing.ldr", position: [-30, 0, 20] }]);
  assert.deepEqual(C.moveEdits(nodes, ["wl/w1"], [40, 0, 0]), [{ op: "move", id: "w1", section: "wing.ldr", position: [50, 0, 20] }]);
  // Both copies of the same part are one edit.
  assert.equal(C.moveEdits(nodes, ["wl/w1", "wr/w1"], [40, 0, 0]).length, 1);
  // A sub-model instance moves in the root's own frame.
  assert.deepEqual(C.moveEdits(nodes, ["wr"], [0, 0, 20]), [{ op: "move", id: "wr", section: "ship.ldr", position: [80, 0, 20] }]);
});

test("a repeated run moves by its template and is deleted whole, but cannot be rotated", () => {
  const { nodes } = C.flatten(SHIP_MPD, SHIP_PLAN);
  assert.deepEqual(C.moveEdits(nodes, ["row-2"], [0, -8, 0]), [{ op: "move", id: "row", section: "ship.ldr", position: [0, -32, 100] }]);
  assert.deepEqual(C.deleteEdits(nodes, ["row-0", "row-2"]), [{ op: "delete", id: "row", section: "ship.ldr" }]);
  assert.throws(() => C.rotateEdits(nodes, ["row-1"], "y", 90), /repeated placement .* cannot be rotated/);
});

test("rotating a part inside a turned sub-model uses the sub-model's own axis", () => {
  const { nodes } = C.flatten(SHIP_MPD, SHIP_PLAN);
  // The right wing is turned about y, so world y is its own y; world x is its own -x.
  assert.deepEqual(C.rotateEdits(nodes, ["wr/w1"], "y", 90), [{ op: "rotate", id: "w1", section: "wing.ldr", axis: "y", degrees: 90 }]);
  assert.deepEqual(C.rotateEdits(nodes, ["wr/w1"], "x", 90), [{ op: "rotate", id: "w1", section: "wing.ldr", axis: "x", degrees: -90 }]);
  const tilted = SHIP_MPD.replace("1 15 80 0 0 -1 0 0 0 1 0 0 0 -1 wing.ldr", "1 15 80 0 0 1 0 0 0 0.7071067811865476 -0.7071067811865476 0 0.7071067811865476 0.7071067811865476 wing.ldr");
  const tiltedNodes = C.flatten(tilted, SHIP_PLAN).nodes;
  assert.throws(() => C.rotateEdits(tiltedNodes, ["wr/w1"], "y", 90), /tilted sub-model/);
  assert.equal(C.rotateEdits(tiltedNodes, ["wr"], "y", 90).length, 1);        // the instance itself can still turn
});

test("widenSelection goes up one level and describeSelection says what an edit touches", () => {
  const { nodes } = C.flatten(SHIP_MPD, SHIP_PLAN);
  assert.deepEqual(C.widenSelection(nodes, ["wl/w1", "wl/w2", "top"]), ["wl", "top"]);
  assert.deepEqual(C.widenSelection(nodes, ["wl"]), ["wl"]);
  assert.match(C.describeSelection(nodes, ["wl/w1"]), /3020\.dat in sub-model wing\.ldr.*drawn 2 times/);
  assert.match(C.describeSelection(nodes, ["wl"]), /sub-model wing\.ldr \(the whole group\)/);
  assert.match(C.describeSelection(nodes, ["row-1"]), /one of 3 from a repeated placement/);
  assert.equal(C.describeSelection(nodes, ["top"]), "");
});

test("framingFill: a one-brick model keeps its third of the view, a big one fills more, capped", () => {
  assert.equal(C.framingFill(19), 0.33);
  assert.ok(C.framingFill(150) > 0.5);
  assert.equal(C.framingFill(100000), 0.6);
});

test("pickAll lists every part under the ray, nearest first, and nextHit cycles through them", () => {
  const big = { key: "wing", id: "wing", ref: "big.dat", position: [0, 0, 0], matrix: IDENTITY };
  const small = { key: "canopy", id: "canopy", ref: "small.dat", position: [0, -10, 0], matrix: IDENTITY };
  const boxes = { "big.dat": { min: [-50, -4, -50], max: [50, 4, 50] }, "small.dat": { min: [-10, -10, -10], max: [10, 10, 10] } };
  const ray = { origin: [0, -100, 0], direction: [0, 1, 0] };
  const hits = C.pickAll(ray, [big, small], boxes);
  assert.deepEqual(hits.map(h => h.key), ["canopy", "wing"]);
  assert.equal(C.pick(ray, [big, small], boxes).key, "canopy");
  assert.equal(C.nextHit(hits, undefined).key, "canopy");
  assert.equal(C.nextHit(hits, "canopy").key, "wing");
  assert.equal(C.nextHit(hits, "wing").key, "canopy");
  assert.equal(C.nextHit([], "wing"), null);
  assert.deepEqual(C.pickAll({ origin: [200, -100, 0], direction: [0, 1, 0] }, [big, small], boxes), []);
});

test("deleteTargets: the only part of a sub-model goes with its instance", () => {
  const plan = { sections: [
    { name: "ship.ldr", steps: [[{ id: "wl" }, { id: "solo" }, { id: "top" }]] },
    { name: "wing.ldr", steps: [[{ id: "w1" }, { id: "w2" }]] },
    { name: "pod.ldr", steps: [[{ id: "p1" }]] },
    { name: "outer.ldr", steps: [[{ id: "inner" }]] },
  ] };
  const node = (key, section, parentKey) => ({ key, id: key.split("/").pop(), section, parentKey: parentKey || "" });
  const nodes = [node("wl", "ship.ldr"), node("wl/w1", "wing.ldr", "wl"), node("solo", "ship.ldr"), node("solo/p1", "pod.ldr", "solo"),
                 node("top", "ship.ldr"), node("outer", "ship.ldr"), node("outer/inner", "outer.ldr", "outer"), node("outer/inner/p1", "pod.ldr", "outer/inner")];
  assert.deepEqual(C.deleteTargets(nodes, ["wl/w1", "top"], plan), ["wl/w1", "top"]);        // wing.ldr has another part
  assert.deepEqual(C.deleteTargets(nodes, ["solo/p1"], plan), ["solo"]);
  assert.deepEqual(C.deleteTargets(nodes, ["outer/inner/p1"], plan), ["outer"]);                // pod's only part, then outer's only part
  assert.deepEqual(C.deleteTargets(nodes, ["solo/p1", "solo"], plan), ["solo"]);
});

test("nudgeEdits: arrows move a stud, Page Up/Down a plate, Shift+arrow turns a quarter", () => {
  const { nodes } = C.flatten(SHIP_MPD, SHIP_PLAN);
  assert.deepEqual(C.nudgeEdits(nodes, ["top"], "ArrowRight", false), [{ op: "move", id: "top", section: "ship.ldr", position: [20, -24, 0] }]);
  assert.deepEqual(C.nudgeEdits(nodes, ["wr/w1"], "ArrowRight", false)[0].position, [-10, 0, 20]);       // east in the world is west inside the turned wing
  assert.deepEqual(C.nudgeEdits(nodes, ["top"], "PageUp", false)[0].position, [0, -32, 0]);
  assert.deepEqual(C.nudgeEdits(nodes, ["top"], "ArrowUp", false)[0].position, [0, -24, -20]);
  assert.deepEqual(C.nudgeEdits(nodes, ["top"], "ArrowRight", true), [{ op: "rotate", id: "top", section: "ship.ldr", axis: "y", degrees: 90 }]);
  assert.deepEqual(C.nudgeEdits(nodes, ["top"], "ArrowUp", true), []);
  assert.deepEqual(C.nudgeEdits(nodes, ["top"], "a", false), []);
});
