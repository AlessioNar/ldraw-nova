// Editor maths and request building: plain functions, no THREE, no DOM, so they
// run (and are tested) under node. Loaded as a classic script by editor.html,
// where it defines the global EditorCore, or with require() in the tests.
//
// Everything here is in LDraw coordinates (Y points down). editor.js converts
// rays from the scene (three.js, Y up) with `sceneToLdraw` before using them.
// A 3x3 matrix is a row-major array of nine numbers, as on an LDraw line.
var EditorCore = (function () {
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
  const scale = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const mulVec = (m, v) => [dot(m.slice(0, 3), v), dot(m.slice(3, 6), v), dot(m.slice(6, 9), v)];
  const AXES = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };

  function invert(m) {
    const [a, b, c, d, e, f, g, h, i] = m;
    const det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);
    if (!Number.isFinite(det) || Math.abs(det) < 1e-12) return null;
    return [(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det,
            (f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det,
            (d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det];
  }

  // Active right-handed rotation on column vectors: the same matrices as ldraw_tools.builder.rotation.
  function rotationMatrix(axis, degrees) {
    const a = degrees * Math.PI / 180, c = Math.cos(a), s = Math.sin(a);
    if (axis === 'x') return [1, 0, 0, 0, c, -s, 0, s, c];
    if (axis === 'y') return [c, 0, s, 0, 1, 0, -s, 0, c];
    if (axis === 'z') return [c, -s, 0, s, c, 0, 0, 0, 1];
    throw new Error('axis must be x, y or z');
  }

  // Scene (three.js: Y up, model recentred by `offset`) -> LDraw. ldbi flips Y and Z, then recentres.
  const sceneToLdraw = (p, offset) => { const q = sub(p, offset); return [q[0], -q[1], -q[2]]; };
  const ldrawToScene = (p, offset) => add([p[0], -p[1], -p[2]], offset);
  const directionToLdraw = d => [d[0], -d[1], -d[2]];

  // ---- reading the MPD the editor preview produced ----------------------------

  const norm = s => String(s).trim().toLowerCase();

  // Placements of one section (the plan's root by default), each with the plan
  // entry id the builder wrote in the comment line just before it:
  //   0 // e3: purpose
  //   1 4 20 -24 0 1 0 0 0 1 0 0 0 1 3001.dat
  function parsePlacements(mpd, sectionName) {
    const sections = [];
    let current = null, id = null;
    for (const raw of String(mpd).split(/\r?\n/)) {
      const line = raw.trim();
      const file = /^0\s+FILE\s+(.+)$/i.exec(line);
      if (file) { current = { name: file[1].trim(), placements: [] }; sections.push(current); id = null; continue; }
      if (!current) continue;
      const comment = /^0\s+\/\/\s*([^:\s]+)\s*:/.exec(line);
      if (comment) { id = comment[1]; continue; }
      const piece = /^1\s+(\S+)\s+((?:\S+\s+){12})(.+)$/.exec(line);
      if (piece) {
        const n = piece[2].trim().split(/\s+/).map(Number);
        if (id && n.every(Number.isFinite)) {
          current.placements.push({ id, section: current.name, colour: piece[1], ref: piece[3].trim(),
                                    position: n.slice(0, 3), matrix: n.slice(3, 12) });
        }
        id = null;
      } else if (/^0\s+STEP\b/i.test(line)) id = null;
    }
    const wanted = sectionName != null ? sections.find(s => norm(s.name) === norm(sectionName)) : null;
    return (wanted || sections[0] || { placements: [] }).placements;
  }

  // Ids of the editable entries of the plan's root section. Repeated placements
  // appear in the MPD as "e1-0", "e1-1" and are not entries of their own.
  function entryIds(plan) {
    const ids = new Set();
    const root = plan && plan.sections && plan.sections[0];
    for (const step of (root && root.steps) || []) for (const entry of step) ids.add(entry.id);
    return ids;
  }

  // ---- picking ---------------------------------------------------------------

  // Distance along the ray at which it enters the placement's bounding box, or null.
  // The box is the part's own (local axes), so a rotated brick is hit where it is, not where its bounding cube is.
  function rayBox(ray, placement, box) {
    const inverse = invert(placement.matrix);
    if (!inverse) return null;
    const origin = mulVec(inverse, sub(ray.origin, placement.position));
    const direction = mulVec(inverse, ray.direction);
    let near = 0, far = Infinity;
    for (let k = 0; k < 3; k++) {
      if (Math.abs(direction[k]) < 1e-12) {
        if (origin[k] < box.min[k] || origin[k] > box.max[k]) return null;
        continue;
      }
      let t1 = (box.min[k] - origin[k]) / direction[k], t2 = (box.max[k] - origin[k]) / direction[k];
      if (t1 > t2) [t1, t2] = [t2, t1];
      near = Math.max(near, t1);
      far = Math.min(far, t2);
      if (near > far) return null;
    }
    return near;     // the local direction is not unit length for a scaled matrix; t is still in ray units
  }

  // The nearest placement under the ray. `boxes` maps a part reference to {min, max}.
  function pick(ray, placements, boxes) {
    let best = null, bestT = Infinity;
    for (const p of placements) {
      const box = boxes[p.ref] || boxes[norm(p.ref)];
      if (!box) continue;
      const t = rayBox(ray, p, box);
      if (t !== null && t < bestT) { best = p; bestT = t; }
    }
    return best;
  }

  // Plain click replaces the selection; shift-click toggles one part; a click on nothing clears (unless shift).
  function nextSelection(selected, hitId, shift) {
    if (hitId == null) return shift ? selected.slice() : [];
    if (!shift) return [hitId];
    return selected.includes(hitId) ? selected.filter(x => x !== hitId) : [...selected, hitId];
  }

  // ---- gizmo drags -----------------------------------------------------------

  // Where on the axis line (origin + s * direction) the pointer ray comes closest: s.
  function axisParameter(ray, axisOrigin, axisDirection) {
    const w = sub(axisOrigin, ray.origin);
    const a = dot(axisDirection, axisDirection), b = dot(axisDirection, ray.direction), c = dot(ray.direction, ray.direction);
    const d = dot(axisDirection, w), e = dot(ray.direction, w);
    const denominator = a * c - b * b;
    if (Math.abs(denominator) < 1e-9) return null;      // looking along the axis: no stable answer
    return (b * e - c * d) / denominator;
  }

  // Signed angle in degrees, about `axis` through `center`, from the first pointer ray to the current one.
  function planeAngle(startRay, ray, center, axis) {
    const hit = r => {
      const denominator = dot(r.direction, axis);
      if (Math.abs(denominator) < 1e-6) return null;
      return sub(add(r.origin, scale(r.direction, dot(sub(center, r.origin), axis) / denominator)), center);
    };
    const v0 = hit(startRay), v1 = hit(ray);
    if (!v0 || !v1 || dot(v0, v0) < 1e-9 || dot(v1, v1) < 1e-9) return null;
    return Math.atan2(dot(axis, cross(v0, v1)), dot(v0, v1)) * 180 / Math.PI;
  }

  const snapTo = (value, step) => (step > 0 ? Math.round(value / step) * step : value);
  const clean = v => Math.round(v * 10000) / 10000 + 0;    // + 0 turns -0 into 0
  // A drag in LDraw units on one axis: x and z snap to half a stud... a stud (20), y to a plate (8).
  const MOVE_STEP = { x: 20, y: 8, z: 20 };
  const ROTATE_STEP = 15;

  // Camera distance that makes a bounding sphere of `radius` fill about `fill` of the smaller viewport side.
  function fitDistance(radius, fovDegrees, aspect, fill = 0.33) {
    const halfV = fovDegrees * Math.PI / 360;
    const half = Math.min(halfV, Math.atan(Math.tan(halfV) * aspect));
    return radius / Math.sin(half) / fill;
  }
  const snapStep = axis => MOVE_STEP[axis];
  const snapMove = (axis, distance, snap = true) => clean(snap ? snapTo(distance, MOVE_STEP[axis]) : distance);
  const snapRotate = (degrees, snap = true) => clean(snap ? snapTo(degrees, ROTATE_STEP) : degrees);

  // ---- edit requests ---------------------------------------------------------

  const chosen = (placements, ids) => ids.map(id => placements.find(p => p.id === id)).filter(Boolean);

  // One "move" per selected part, to its old position plus the drag.
  function moveEdits(placements, ids, delta) {
    if (delta.every(v => v === 0)) return [];
    return chosen(placements, ids).map(p => ({
      op: 'move', id: p.id, section: p.section, position: add(p.position, delta).map(clean),
    }));
  }

  // One part turns in place (the plan's "rotate"). A group turns about the middle of
  // its origins: each part also moves, so the group keeps its shape.
  function rotateEdits(placements, ids, axis, degrees) {
    const parts = chosen(placements, ids);
    if (!parts.length || degrees === 0) return [];
    const edits = [];
    const pivot = scale(parts.reduce((sum, p) => add(sum, p.position), [0, 0, 0]), 1 / parts.length);
    const matrix = rotationMatrix(axis, degrees);
    for (const p of parts) {
      edits.push({ op: 'rotate', id: p.id, section: p.section, axis, degrees });
      if (parts.length > 1) {
        const moved = add(pivot, mulVec(matrix, sub(p.position, pivot))).map(clean);
        if (moved.some((v, k) => v !== clean(p.position[k]))) {
          edits.push({ op: 'move', id: p.id, section: p.section, position: moved });
        }
      }
    }
    return edits;
  }

  const deleteEdits = (placements, ids) => chosen(placements, ids).map(p => ({ op: 'delete', id: p.id, section: p.section }));

  const centroid = (placements, ids) => {
    const parts = chosen(placements, ids);
    return parts.length ? scale(parts.reduce((sum, p) => add(sum, p.position), [0, 0, 0]), 1 / parts.length) : null;
  };

  // The request body the edits endpoint wants.
  const editsBody = (edits, baseRevision) => ({ edits, base_revision: baseRevision });

  // Short text for the status line from a (possibly failed) response.
  function describeParts(parts) {
    if (!parts || !parts.rows) return '';
    const used = parts.rows.reduce((n, r) => n + r.used, 0);
    const text = `${used} part${used === 1 ? '' : 's'} used`;
    return parts.short && parts.short.length ? `${text}, ${parts.short.length} beyond what you own` : text;
  }

  return { AXES, invert, mulVec, rotationMatrix, sceneToLdraw, ldrawToScene, directionToLdraw, parsePlacements, entryIds,
           rayBox, pick, nextSelection, axisParameter, planeAngle, snapMove, snapRotate, fitDistance, snapStep, moveEdits, rotateEdits,
           deleteEdits, centroid, editsBody, describeParts };
})();

if (typeof module !== 'undefined' && module.exports) module.exports = EditorCore;
