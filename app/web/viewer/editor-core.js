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
  function parseSections(mpd) {
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
    return sections;
  }

  // Placements of one section (the plan's root by default).
  function parsePlacements(mpd, sectionName) {
    const sections = parseSections(mpd);
    const wanted = sectionName != null ? sections.find(s => norm(s.name) === norm(sectionName)) : null;
    return (wanted || sections[0] || { placements: [] }).placements;
  }

  // ---- the whole model, sub-models expanded --------------------------------------

  const matMul = (a, b) => {
    const r = [];
    for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) r.push(a[3 * i] * b[j] + a[3 * i + 1] * b[3 + j] + a[3 * i + 2] * b[6 + j]);
    return r;
  };
  const IDENTITY = [1, 0, 0, 0, 1, 0, 0, 0, 1];
  const MAX_NODES = 20000;
  const repeatBase = id => /^(.*)-\d+$/.exec(id);

  // Every placement of the model in the root's frame, walking into sub-models. A node is
  //   key           "hull/a-plate": the ids from the root down, unique per copy on screen
  //   id, section   the MPD id and the PLAN section that holds the entry (edits name these)
  //   entryId       the plan entry it came from (a repeated "e1-2" belongs to "e1")
  //   position, matrix   in the root's frame (what is drawn); localPosition, localMatrix in its own section
  //   parentMatrix, parentPosition   the sub-model instance it sits in (the identity at the root)
  //   leaf, repeated, templateAt, copies (how many times the model draws its section)
  // The root is the plan's first section; the others are matched to the MPD's FILE lines by name.
  function flatten(mpd, plan) {
    const sections = parseSections(mpd);
    const byName = new Map(sections.map(s => [norm(s.name), s]));
    const planSections = new Map();
    for (const [index, ps] of ((plan && plan.sections) || []).entries()) {
      const entries = new Map();
      for (const step of ps.steps || []) for (const entry of step) entries.set(entry.id, entry);
      planSections.set(index === 0 && sections[0] ? norm(sections[0].name) : norm(ps.name), { name: ps.name, entries });
    }
    const uses = new Map();
    const nodes = [];
    let truncated = false;
    function walk(section, parentKey, matrix, position, depth, seen) {
      const known = planSections.get(norm(section.name));
      const sectionName = known ? known.name : section.name;
      uses.set(sectionName, (uses.get(sectionName) || 0) + 1);
      for (const p of section.placements) {
        if (nodes.length >= MAX_NODES) { truncated = true; return; }
        const key = parentKey ? `${parentKey}/${p.id}` : p.id;
        const world = matMul(matrix, p.matrix);
        const where = add(mulVec(matrix, p.position), position);
        const child = byName.get(norm(p.ref));
        let entryId = p.id, entry = known && known.entries.get(p.id), repeated = false;
        if (!entry && known) {
          const base = repeatBase(p.id);
          if (base && known.entries.get(base[1]) && known.entries.get(base[1]).repeat) {
            entryId = base[1]; entry = known.entries.get(entryId); repeated = true;
          }
        }
        nodes.push({
          key, id: p.id, entryId, section: sectionName, ref: p.ref, colour: p.colour,
          position: where, matrix: world, localPosition: p.position, localMatrix: p.matrix,
          parentKey, parentMatrix: matrix, parentPosition: position, depth, leaf: !child,
          editable: Boolean(entry), repeated, templateAt: repeated && entry.at ? entry.at.slice() : null,
          repeatCount: repeated ? entry.repeat.count : 0,
        });
        if (child && !seen.has(norm(child.name)) && depth < 12) {
          walk(child, key, world, where, depth + 1, new Set([...seen, norm(child.name)]));
        }
      }
    }
    if (sections[0]) walk(sections[0], '', IDENTITY, [0, 0, 0], 0, new Set([norm(sections[0].name)]));
    for (const n of nodes) n.copies = uses.get(n.section) || 1;
    return { nodes, truncated };
  }

  // One level up: a part becomes the sub-model instance it sits in; a root part stays.
  function widenSelection(nodes, keys) {
    const result = [];
    for (const key of keys) {
      const node = nodes.find(n => n.key === key);
      const next = node && node.parentKey ? node.parentKey : key;
      if (!result.includes(next)) result.push(next);
    }
    return result;
  }

  // A line for the status bar about what the selection is and what editing it will touch.
  function describeSelection(nodes, keys) {
    const first = keys.map(k => nodes.find(n => n.key === k)).find(Boolean);
    if (!first) return '';
    const notes = [];
    if (first.depth > 0) notes.push(`${first.ref} in sub-model ${first.section}`);
    else if (!first.leaf) notes.push(`sub-model ${first.ref} (the whole group)`);
    if (first.repeated) notes.push(`one of ${first.repeatCount} from a repeated placement: move and delete act on the whole run, it cannot be rotated`);
    else if (first.copies > 1) notes.push(`${first.section} is drawn ${first.copies} times, so an edit changes every copy`);
    return notes.join(' · ');
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
    return pickAll(ray, placements, boxes)[0] || null;
  }

  // Every placement under the ray, nearest first. Boxes of neighbouring parts overlap (a canopy over a wing), so
  // the nearest box is not always the part meant; a second click on the same spot takes the next one (see nextHit).
  function pickAll(ray, placements, boxes) {
    const hits = [];
    for (const p of placements) {
      const box = boxes[p.ref] || boxes[norm(p.ref)];
      if (!box) continue;
      const t = rayBox(ray, p, box);
      if (t !== null) hits.push({ p, t });
    }
    return hits.sort((a, b) => a.t - b.t).map(h => h.p);
  }

  // The hit to select: the nearest, or, when the same spot was clicked before, the one after the previous pick.
  function nextHit(hits, previousKey) {
    if (!hits.length) return null;
    const i = previousKey === undefined ? -1 : hits.findIndex(h => (h.key === undefined ? h.id : h.key) === previousKey);
    return hits[(i + 1) % hits.length];
  }

  // Which parts a delete really removes: the only part of a sub-model cannot be deleted from it (a sub-model
  // needs one), so the sub-model instance it sits in goes instead, and so on up. Returns the keys to delete.
  function deleteTargets(nodes, keys, plan) {
    const counts = new Map();
    for (const section of (plan && plan.sections) || []) {
      counts.set(section.name, (section.steps || []).reduce((n, step) => n + step.length, 0));
    }
    const result = [];
    for (const key of keys) {
      let node = nodes.find(n => (n.key === undefined ? n.id : n.key) === key);
      while (node && node.parentKey && counts.get(node.section) === 1) node = nodes.find(n => n.key === node.parentKey);
      const target = node ? (node.key === undefined ? node.id : node.key) : key;
      if (!result.includes(target)) result.push(target);
    }
    return result;
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
  // A one-brick model is framed small (a third of the view); a bigger model fills more of it, up to 60%.
  const framingFill = radius => Math.min(0.6, Math.max(0.33, 0.33 + (radius - 60) / 300));
  const snapStep = axis => MOVE_STEP[axis];
  const snapMove = (axis, distance, snap = true) => clean(snap ? snapTo(distance, MOVE_STEP[axis]) : distance);
  const snapRotate = (degrees, snap = true) => clean(snap ? snapTo(degrees, ROTATE_STEP) : degrees);

  // ---- edit requests ---------------------------------------------------------

  // Selected nodes by key (a flat placement list's key is its id), one per plan entry: the same sub-model
  // drawn twice, or a whole repeated run, is one edit however many of its copies are selected.
  const keyOf = p => (p.key === undefined ? p.id : p.key);
  function chosen(placements, ids) {
    const seen = new Set(), result = [];
    for (const id of ids) {
      const p = placements.find(x => keyOf(x) === id);
      if (!p) continue;
      const identity = `${p.section}\u0000${p.entryId === undefined ? p.id : p.entryId}`;
      if (!seen.has(identity)) { seen.add(identity); result.push(p); }
    }
    return result;
  }
  const entryOf = p => (p.entryId === undefined ? p.id : p.entryId);
  const localOf = p => p.localPosition || p.position;
  // A world-frame vector or point expressed in the frame of the sub-model instance the part sits in.
  function toLocal(p, vector, isPoint) {
    if (!p.parentMatrix) return vector;
    const inverse = invert(p.parentMatrix);
    return inverse ? mulVec(inverse, isPoint ? sub(vector, p.parentPosition) : vector) : vector;
  }

  // One "move" per selected part, to its old position plus the drag. A part inside a sub-model moves by the
  // drag as seen from inside that sub-model; a repeated run moves by its template.
  function moveEdits(placements, ids, delta) {
    if (delta.every(v => v === 0)) return [];
    return chosen(placements, ids).map(p => ({
      op: 'move', id: entryOf(p), section: p.section,
      position: add(p.repeated && p.templateAt ? p.templateAt : localOf(p), toLocal(p, delta, false)).map(clean),
    }));
  }

  // The turn about the world axis seen from inside the part's sub-model: that sub-model's axis and angle, or null
  // when the sub-model is tilted so that the world axis is not one of its own.
  function localTurn(p, axis, degrees) {
    const v = toLocal(p, AXES[axis], false);
    for (const k of [0, 1, 2]) {
      const others = [0, 1, 2].filter(i => i !== k);
      if (others.every(i => Math.abs(v[i]) < 1e-6) && Math.abs(Math.abs(v[k]) - 1) < 1e-6) {
        return { axis: 'xyz'[k], degrees: v[k] > 0 ? degrees : -degrees };
      }
    }
    return null;
  }

  // One part turns in place (the plan's "rotate"). A group turns about the middle of
  // its origins: each part also moves, so the group keeps its shape. Throws a readable Error when a part cannot turn.
  function rotateEdits(placements, ids, axis, degrees) {
    const parts = chosen(placements, ids);
    if (!parts.length || degrees === 0) return [];
    for (const p of parts) {
      if (p.repeated) throw new Error(`${p.id} comes from a repeated placement (${p.repeatCount} copies) and cannot be rotated; move or delete it instead`);
    }
    const edits = [];
    const pivot = scale(parts.reduce((sum, p) => add(sum, p.position), [0, 0, 0]), 1 / parts.length);
    const matrix = rotationMatrix(axis, degrees);
    for (const p of parts) {
      const turn = localTurn(p, axis, degrees);
      if (!turn) throw new Error(`${p.id} sits in a tilted sub-model (${p.section}); turn the whole sub-model instead`);
      edits.push({ op: 'rotate', id: entryOf(p), section: p.section, axis: turn.axis, degrees: turn.degrees });
      if (parts.length > 1) {
        const world = add(pivot, mulVec(matrix, sub(p.position, pivot)));
        const moved = toLocal(p, world, true).map(clean);
        if (moved.some((v, k) => v !== clean(localOf(p)[k]))) {
          edits.push({ op: 'move', id: entryOf(p), section: p.section, position: moved });
        }
      }
    }
    return edits;
  }

  // Keyboard nudging, as in LeoCAD: arrows move one stud on x and z, Page Up / Page Down one plate up / down
  // (LDraw's y points down, so up is -8); Shift+Left/Right turns the selection a quarter turn about y.
  function nudgeEdits(placements, ids, key, shift) {
    if (shift) {
      if (key !== 'ArrowLeft' && key !== 'ArrowRight') return [];
      return rotateEdits(placements, ids, 'y', key === 'ArrowRight' ? 90 : -90);
    }
    const delta = { ArrowLeft: [-20, 0, 0], ArrowRight: [20, 0, 0], ArrowUp: [0, 0, -20], ArrowDown: [0, 0, 20],
                    PageUp: [0, -8, 0], PageDown: [0, 8, 0] }[key];
    return delta ? moveEdits(placements, ids, delta) : [];
  }

  const deleteEdits = (placements, ids) => chosen(placements, ids).map(p => ({ op: 'delete', id: entryOf(p), section: p.section }));

  // The middle of the selection in the frame that is drawn (the gizmo sits there).
  const centroid = (placements, ids) => {
    const parts = ids.map(id => placements.find(p => keyOf(p) === id)).filter(Boolean);
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

  return { AXES, invert, mulVec, rotationMatrix, sceneToLdraw, ldrawToScene, directionToLdraw, parsePlacements, parseSections, flatten, widenSelection, describeSelection, entryIds,
           rayBox, pick, pickAll, nextHit, deleteTargets, nextSelection, axisParameter, planeAngle, snapMove, snapRotate, fitDistance, snapStep, framingFill, moveEdits, rotateEdits,
           deleteEdits, nudgeEdits, centroid, editsBody, describeParts };
})();

if (typeof module !== 'undefined' && module.exports) module.exports = EditorCore;
