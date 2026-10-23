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
