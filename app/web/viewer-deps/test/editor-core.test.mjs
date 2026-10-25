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

// ---- the build workspace canvas ----------------------------------------------------------------

const SUB_MPD = `0 FILE main.ldr
0 // e1: base
1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat
0 // e2: roof
1 16 100 -24 0 1 0 0 0 1 0 0 0 1 roof.ldr
0 FILE roof.ldr
0 // r1: tile
1 16 0 0 0 1 0 0 0 1 0 0 0 1 3068b.dat
0 // r2: tile
1 24 20 0 0 1 0 0 0 1 0 0 0 1 3068b.dat
`;
const SUB_PLAN = { sections: [
  { name: "main.ldr", steps: [[{ id: "e1" }, { id: "e2" }]] },
  { name: "roof.ldr", steps: [[{ id: "r1" }, { id: "r2" }]] },
] };

test("effectiveColours: 16 takes the parent's colour, 24 its edge colour", () => {
  const nodes = C.flatten(SUB_MPD.replace("1 16 100", "1 7 100"), SUB_PLAN).nodes;
  const eff = C.effectiveColours(nodes);
  assert.equal(eff.get("e1"), 4);
  assert.equal(eff.get("e2/r1"), 7);
  assert.equal(eff.get("e2/r2"), -8);                      // edge of colour 7
  assert.equal(C.effectiveColours(C.flatten(SUB_MPD, SUB_PLAN).nodes).get("e2/r1"), 16);
});

test("sceneMatrix flips y and z the way ldbi does", () => {
  const n = { matrix: [1, 0, 0, 0, 1, 0, 0, 0, 1], position: [10, -24, 5] };
  assert.deepEqual(C.sceneMatrix(n).map(v => v + 0), [1, 0, 0, 10, 0, -1, 0, 24, 0, 0, -1, -5, 0, 0, 0, 1]);
});

test("diffLeaves tells moved, recoloured, added and removed parts apart", () => {
  const before = C.flatten(SUB_MPD, SUB_PLAN).nodes;
  const known = new Map();
  const eff = C.effectiveColours(before);
  for (const n of before.filter(n => n.leaf)) known.set(n.key, { ref: n.ref.toLowerCase(), colour: eff.get(n.key), tf: C.sceneMatrix(n).join(",") });
  assert.deepEqual(C.diffLeaves(known, before.filter(n => n.leaf), eff), { add: [], remove: [], rebuild: [], move: [] });
  const after = C.predictEdits(before, [
    { op: "move", id: "e1", section: "main.ldr", position: [0, 0, 40] },
    { op: "recolour", id: "r1", section: "roof.ldr", colour: 2 },
    { op: "delete", id: "r2", section: "roof.ldr" },
  ]);
  const diff = C.diffLeaves(known, after.filter(n => n.leaf), C.effectiveColours(after));
  assert.deepEqual(diff, { add: [], remove: ["e2/r2"], rebuild: ["e2/r1"], move: ["e1"] });
  const grown = C.diffLeaves(known, [...before.filter(n => n.leaf), { ...before[0], key: "new", ref: "3001.dat" }], new Map([...eff, ["new", 4]]));
  assert.deepEqual(grown.add, ["new"]);
});

test("predictEdits moves a sub-model's parts with it, without touching the input", () => {
  const nodes = C.flatten(SUB_MPD, SUB_PLAN).nodes;
  const snapshot = JSON.stringify(nodes);
  const moved = C.predictEdits(nodes, [{ op: "move", id: "e2", section: "main.ldr", position: [100, -24, 60] }]);
  assert.equal(JSON.stringify(nodes), snapshot);
  assert.deepEqual(moved.find(n => n.key === "e2/r2").position, [120, -24, 60]);
  const turned = C.predictEdits(nodes, [{ op: "rotate", id: "e2", section: "main.ldr", axis: "y", degrees: 90 }]);
  near(turned.find(n => n.key === "e2/r2").position, [100, -24, -20]);
  const deleted = C.predictEdits(nodes, [{ op: "delete", id: "e2", section: "main.ldr" }]);
  assert.deepEqual(deleted.map(n => n.key), ["e1"]);
});

// A flat "camera": x and y are the part's x and y over 200, z is the distance.
const flatCamera = point => [point[0] / 200, -point[1] / 200, 1];

test("boxSelect picks the parts whose centre is inside the rectangle", () => {
  const nodes = C.flatten(SUB_MPD, SUB_PLAN).nodes.filter(n => n.leaf);
  const boxes = { "3001.dat": BOX, "3068b.dat": { min: [-10, -4, -10], max: [10, 0, 10] } };
  const keys = rect => C.boxSelect(rect, nodes, boxes, flatCamera);
  assert.deepEqual(keys({ x0: -1, y0: -1, x1: 1, y1: 1 }), ["e1", "e2/r1", "e2/r2"]);
  assert.deepEqual(keys({ x0: 0.4, y0: 1, x1: 0.7, y1: -1 }), ["e2/r1", "e2/r2"]);
  assert.deepEqual(keys({ x0: 0.1, y0: 0.2, x1: -0.1, y1: -0.2 }), ["e1"]);         // corners in any order
  assert.deepEqual(C.boxSelect({ x0: -1, y0: -1, x1: 1, y1: 1 }, nodes, boxes, () => [0, 0, -1]), []);   // behind the camera
  assert.deepEqual(C.boxSelect({ x0: -1, y0: -1, x1: 1, y1: 1 }, nodes, {}, flatCamera), []);            // no boxes known
  const touch = C.boxSelect({ x0: 0.45, y0: 0.5, x1: 0.46, y1: -0.5 }, nodes, boxes, flatCamera, "touch");
  assert.deepEqual(touch, ["e2/r1"]);
  assert.deepEqual(C.mergeSelection(["a"], ["a", "b"], true), ["a", "b"]);
  assert.deepEqual(C.mergeSelection(["a"], ["b"], false), ["b"]);
});

test("parseCommand accepts the contract's commands and refuses the rest", () => {
  const origin = "http://x.test", base = "http://x.test/viewer/editor.html";
  const ok = d => C.parseCommand({ type: "obd:command", ...d }, origin, base);
  assert.deepEqual(ok({ cmd: "undo" }), { cmd: "undo" });
  assert.deepEqual(ok({ cmd: "set-tool", tool: "paint" }), { cmd: "set-tool", tool: "paint" });
  assert.deepEqual(ok({ cmd: "set-colour", colour: "14" }), { cmd: "set-colour", colour: 14 });
  assert.deepEqual(ok({ cmd: "add-part", part: "3001.dat", colour: 4 }), { cmd: "add-part", part: "3001.dat", colour: 4 });
  assert.deepEqual(ok({ cmd: "set-mode", mode: "player" }), { cmd: "set-mode", mode: "player" });
  assert.deepEqual(ok({ cmd: "step", n: 3 }), { cmd: "step", n: 3 });
  assert.deepEqual(ok({ cmd: "view-version", url: "/api/builds/a/proposals/p.mpd" }), { cmd: "view-version", url: "http://x.test/api/builds/a/proposals/p.mpd" });
  for (const bad of [{ cmd: "nope" }, { cmd: "set-tool", tool: "lasso" }, { cmd: "set-colour", colour: -1 }, { cmd: "set-colour", colour: 1.5 },
    { cmd: "add-part", part: "../x", colour: 1 }, { cmd: "add-part", part: "3001" }, { cmd: "set-mode", mode: "x" }, { cmd: "step", n: 0 },
    { cmd: "view-version", url: "https://evil.test/a.mpd" }, { cmd: "view-version", url: "javascript:alert(1)" }, { cmd: "view-version" }]) {
    assert.throws(() => ok(bad), Error, JSON.stringify(bad));
  }
  assert.deepEqual(ok({ cmd: "select", ids: ["main.ldr:e1", "sub.ldr:e1"] }), { cmd: "select", ids: ["main.ldr:e1", "sub.ldr:e1"] });
  for (const bad of [{ cmd: "select" }, { cmd: "select", ids: "e1" }, { cmd: "select", ids: [1] }, { cmd: "select", ids: [""] }, { cmd: "select", ids: Array(501).fill("a") }]) {
    assert.throws(() => ok(bad), Error, JSON.stringify(bad));
  }
  assert.throws(() => C.parseCommand({ type: "other", cmd: "undo" }, origin, base));
  assert.throws(() => C.parseCommand(null, origin, base));
  assert.throws(() => ok({ cmd: { toString: () => "undo" } }));
});

test("trustedMessage needs the same origin and the parent window", () => {
  const parent = {}, other = {};
  const event = (origin, source, data) => ({ origin, source, data });
  assert.equal(C.trustedMessage(event("http://a", parent, { type: "x" }), "http://a", parent), true);
  assert.equal(C.trustedMessage(event("http://evil", parent, { type: "x" }), "http://a", parent), false);
  assert.equal(C.trustedMessage(event("http://a", other, { type: "x" }), "http://a", parent), false);
  assert.equal(C.trustedMessage(event("http://a", parent, "text"), "http://a", parent), false);
  assert.equal(C.trustedMessage(event("http://a", parent, null), "http://a", parent), false);
});

test("add tool: ground placement, snapping target and the edit batch", () => {
  const nodes = C.flatten(SUB_MPD, SUB_PLAN).nodes.filter(n => n.leaf);
  const boxes = { "3001.dat": BOX, "3068b.dat": { min: [-10, -4, -10], max: [10, 0, 10] } };
  const newBox = { min: [-20, -4, -40], max: [20, 24, 40] };
  const far = C.placementFor({ origin: [333, -500, 477], direction: [0, 1, 0] }, nodes, boxes, newBox, 0, "main.ldr");
  assert.deepEqual(far.world, [340, -24, 480]);                       // on the stud grid, body ends on the ground
  assert.equal(far.section, "main.ldr");
  assert.equal(far.to, null);
  const onBase = C.placementFor({ origin: [0, -500, 0], direction: [0, 1, 0] }, nodes, boxes, newBox, 0, "main.ldr");
  assert.equal(onBase.to, "e1");
  assert.equal(C.placementFor({ origin: [0, -5, 0], direction: [1, 0, 0] }, [], {}, newBox, 0, "main.ldr"), null);
  assert.equal(C.nextEntryId({ sections: [{ steps: [[{ id: "e1" }, { id: "e3" }]] }] }), "e2");
  const edits = C.addEdits({ sections: [{ steps: [[{ id: "e1" }]] }] }, "3001", 4, onBase);
  assert.deepEqual(edits, [{ op: "add", section: "main.ldr", part: "3001.dat", colour: 4, position: onBase.local }, { op: "snap", id: "e2", to: "e1" }]);
  assert.equal(C.addEdits({ sections: [{ steps: [[]] }] }, "3001.dat", 4, far, false).length, 1);
});

test("pieceInfo describes a selected part for the parent page", () => {
  const n = C.flatten(SUB_MPD, SUB_PLAN).nodes.find(p => p.key === "e2/r2");
  assert.deepEqual(C.pieceInfo(n, "Tile 1 x 2"), { id: "r2", part: "3068b", name: "Tile 1 x 2", colour: 24, position: [20, 0, 0], rotation: IDENTITY, section: "roof.ldr" });
});

test("keysForIds finds parts by section:id, in every copy of a sub-model, and a bare id only in the root", () => {
  const node = (key, section, entryId, parentKey = "", leaf = true) => ({ key, section, entryId, parentKey, leaf });
  const nodes = [node("e1", "main.ldr", "e1"), node("e2", "main.ldr", "e2"), node("w", "main.ldr", "w", "", false),
    node("w/e1", "wing.ldr", "e1", "w"), node("v/e1", "wing.ldr", "e1", "v"), node("w/e2", "wing.ldr", "e2", "w")];
  assert.deepEqual(C.keysForIds(nodes, ["wing.ldr:e1"]), ["w/e1", "v/e1"]);        // both copies, never the root's e1
  assert.deepEqual(C.keysForIds(nodes, ["Main.ldr:e1", "wing.ldr:e2"]), ["e1", "w/e2"]);
  assert.deepEqual(C.keysForIds(nodes, ["e1"]), ["e1"]);                            // bare: the root section
  assert.deepEqual(C.keysForIds(nodes, ["wing.ldr:zz", "nowhere.ldr:e1", "main.ldr:w"]), []);   // unknown, and an instance line is no part
});
