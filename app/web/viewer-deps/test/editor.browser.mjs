// End-to-end checks of the manual 3D editor in a real browser, against a THROWAWAY app instance:
//   docker run -d --init --name ldraw-nova-test-b -p 127.0.0.1:8782:8000 -v ldraw-nova-test-b-data:/data ldraw-nova-test-b
//   VIEWER_TEST_URL=http://127.0.0.1:8782 PLAYWRIGHT_CHANNEL=chrome npm run test:editor
// The tests publish models, kill and restart the container, and create chats (deleted at the end).
// EDITOR_TEST_CONTAINER names the container (default ldraw-nova-test-b); EDITOR_TEST_SHOTS the screenshot folder.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, before, test } from 'node:test';
import { chromium } from 'playwright';

const baseURL = process.env.VIEWER_TEST_URL ?? 'http://127.0.0.1:8782';
const container = process.env.EDITOR_TEST_CONTAINER ?? 'ldraw-nova-test-b';
const shots = process.env.EDITOR_TEST_SHOTS ?? join(tmpdir(), 'editor-shots');
const results = [];                       // printed at the end: timings worth recording
let browser;
const chats = [];

mkdirSync(shots, { recursive: true });
const docker = (...args) => execFileSync('docker', args, { encoding: 'utf8', maxBuffer: 1 << 28 });
const note = (name, value) => { results.push(`${name}: ${value}`); console.log(`RESULT ${name}: ${value}`); };

before(async () => {
  browser = await chromium.launch({
    channel: process.env.PLAYWRIGHT_CHANNEL,
    args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
  });
});
after(async () => {
  await browser?.close();
  for (const id of chats) await api('DELETE', `/api/chats/${id}`).catch(() => {});
  console.log(results.join('\n'));
});

async function api(method, path, body) {
  const response = await fetch(baseURL + path, {
    method, headers: { Origin: baseURL, 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  let json = null; try { json = JSON.parse(text); } catch { /* not JSON */ }
  if (!response.ok) throw Object.assign(new Error(`${method} ${path}: ${response.status} ${text.slice(0, 300)}`), { status: response.status });
  return json;
}

async function newChat() {
  const { id } = await api('POST', '/api/chats', {});
  chats.push(id);
  return id;
}

// A page on the editor, with its errors and its POST requests (and how long each took) recorded.
async function openEditor(t, chat, { wait = 'Revision' } = {}) {
  const page = await browser.newPage({ viewport: { width: 1200, height: 800 } });
  const errors = [], posts = [], starts = new Map();
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error' && /shader|WebGL/i.test(m.text())) errors.push(m.text()); });
  page.on('request', r => { if (r.method() === 'POST') { starts.set(r, Date.now()); posts.push({ url: new URL(r.url()).pathname, ms: null, r }); } });
  page.on('requestfinished', r => { const p = posts.find(x => x.r === r); if (p) p.ms = Date.now() - starts.get(r); });
  t.after(async () => { await page.close(); assert.deepEqual(errors, []); });
  await page.goto(`${baseURL}/viewer/editor.html?chat=${chat}`);
  await waitStatus(page, new RegExp(`^${wait}`), 180000);
  return { page, posts };
}

const lf = text => text.split(String.fromCharCode(13, 10)).join(String.fromCharCode(10));
const status = page => page.locator('#status').textContent();
const revisionOf = async page => Number(/^Revision (\d+)/.exec(await status(page))?.[1] ?? 0);

async function waitStatus(page, pattern, timeout = 120000) {
  await page.waitForFunction(p => new RegExp(p.source, p.flags).test(document.getElementById('status').textContent),
    { source: pattern.source, flags: pattern.flags }, { timeout });
  assert.equal(await page.locator('#status').evaluate(e => e.classList.contains('error')), false, await status(page));
}

// Run an action that makes the backend rebuild, then wait for the next 'Revision N' line.
async function rebuilt(page, action) {
  const before = await revisionOf(page), started = Date.now();
  await action();
  await page.waitForFunction(n => {
    const s = document.getElementById('status');
    const m = /^Revision (\d+)/.exec(s.textContent);
    return s.classList.contains('error') || (m && Number(m[1]) > n);
  }, before, { timeout: 180000 });
  assert.equal(await page.locator('#status').evaluate(e => e.classList.contains('error')), false, await status(page));
  return Date.now() - started;
}

const shot = (page, name) => page.screenshot({ path: join(shots, name) });
const session = chat => api('GET', `/api/chats/${chat}/editor`);
const entries = view => view.plan.sections.flatMap(s => s.steps.flat());

// Click the middle of the model's bounding box.
async function clickModel(page) {
  const at = await page.evaluate(() => {
    const box = new THREE.Box3().setFromObject(scene.baseObject), c = box.getCenter(new THREE.Vector3());
    const rect = scene.renderer.domElement.getBoundingClientRect(), p = c.project(scene.camera);
    return [rect.left + (p.x + 1) / 2 * rect.width, rect.top + (1 - p.y) / 2 * rect.height];
  });
  await page.mouse.click(...at);
}

// Screen points for dragging the move arrow of `axis` by `ldu` LDU: from its tip to where the tip would then be.
const arrow = (page, axis, ldu) => page.evaluate(({ axis, ldu }) => {
  const gizmo = scene.overlay.children.find(c => c.children.some(m => m.userData && m.userData.axis));
  gizmo.updateMatrixWorld(true);
  const tip = gizmo.children.find(m => m.userData.axis === axis && m.geometry.type === 'ConeGeometry');
  const from = new THREE.Vector3().setFromMatrixPosition(tip.matrixWorld);
  const along = from.clone().sub(gizmo.position).normalize();
  const rect = scene.renderer.domElement.getBoundingClientRect();
  const screen = v => { const p = v.clone().project(scene.camera); return [rect.left + (p.x + 1) / 2 * rect.width, rect.top + (1 - p.y) / 2 * rect.height]; };
  return { from: screen(from), to: screen(from.clone().addScaledVector(along, ldu)) };
}, { axis, ldu });

// Pixels that one 20 LDU stud spans on screen at the current camera.
const studPixels = page => page.evaluate(() => {
  const gizmo = scene.overlay?.children.find(c => c.children.some(m => m.userData && m.userData.axis));
  const origin = new THREE.Vector3(), rect = scene.renderer.domElement.getBoundingClientRect();
  const screen = v => { const p = v.clone().project(scene.camera); return [(p.x + 1) / 2 * rect.width, (1 - p.y) / 2 * rect.height]; };
  const centre = new THREE.Box3().setFromObject(scene.baseObject).getCenter(origin);
  const a = screen(centre), b = screen(centre.clone().add(new THREE.Vector3(20, 0, 0)));
  return Math.hypot(a[0] - b[0], a[1] - b[1]);
});

async function dragArrow(page, axis, ldu, { during } = {}) {
  const { from, to } = await arrow(page, axis, ldu);
  await page.mouse.move(...from);
  await page.mouse.down();
  for (let i = 1; i <= 8; i++) await page.mouse.move(from[0] + (to[0] - from[0]) * i / 8, from[1] + (to[1] - from[1]) * i / 8);
  if (during) await during(to);
  await page.mouse.up();
}

test('open, select, move, undo, redo, delete, publish', async t => {
  const chat = await newChat();
  await api('POST', `/api/chats/${chat}/editor`, { name: 'flow' });
  const { page, posts } = await openEditor(t, chat);
  const edits = () => posts.filter(p => p.url.endsWith('/editor/edits'));
  const x = async () => entries(await session(chat))[0].at[0];

  // open: a 1x1 brick is framed small enough that a stud takes a useful number of pixels
  await shot(page, '01-open.png');
  const stud = await studPixels(page);
  note('pixels per 20 LDU stud at first load (1x1 brick)', stud.toFixed(1));
  assert.ok(stud >= 40 && stud <= 150, `a stud spans ${stud}px`);
  const box = await page.evaluate(() => {
    const b = new THREE.Box3().setFromObject(scene.baseObject), rect = scene.renderer.domElement.getBoundingClientRect();
    const xs = [], ys = [];
    for (const x of [b.min.x, b.max.x]) for (const y of [b.min.y, b.max.y]) for (const z of [b.min.z, b.max.z]) {
      const p = new THREE.Vector3(x, y, z).project(scene.camera);
      xs.push((p.x + 1) / 2 * rect.width); ys.push((1 - p.y) / 2 * rect.height);
    }
    return { w: (Math.max(...xs) - Math.min(...xs)) / rect.width, h: (Math.max(...ys) - Math.min(...ys)) / rect.height };
  });
  note('model box on screen, fraction of viewport width / height', `${box.w.toFixed(2)} / ${box.h.toFixed(2)}`);
  assert.ok(box.h > 0.12 && box.h < 0.6, `the model should fill about a third of the view, got ${box.h}`);

  // zoom: a wheel notch changes the distance by a clearly visible amount
  const distance = () => page.evaluate(() => scene.camera.position.distanceTo(scene.orbitControls.target));
  const d0 = await distance();
  await page.mouse.move(900, 600);
  await page.mouse.wheel(0, -100);
  await page.waitForFunction(d => scene.camera.position.distanceTo(scene.orbitControls.target) < d - 1, d0);
  const d1 = await distance();
  note('one wheel notch zoom', `${((1 - d1 / d0) * 100).toFixed(1)}% closer`);
  assert.ok(d1 < d0 * 0.92, `a notch should zoom at least 8%: ${d0} -> ${d1}`);
  // 'fit' returns to the same framing as the first load
  await page.locator('#home').click();
  await page.waitForFunction(d => Math.abs(scene.camera.position.distanceTo(scene.orbitControls.target) - d) < 0.5, d0);

  // select
  await clickModel(page);
  await waitStatus(page, /1 selected/);
  await shot(page, '02-selected.png');

  // move: one stud east with the gizmo; nothing is sent until release
  let snapText = '', freeText = '';
  await rebuilt(page, () => dragArrow(page, 'x', 40, {
    during: async ([x, y]) => {
      snapText = await status(page);
      await page.keyboard.down('Alt');
      await page.mouse.move(x + 3, y + 1);
      freeText = await status(page);
      await page.keyboard.up('Alt');
      await page.mouse.move(x, y);                                // back onto the snapped target
    },
  }));
  note('status while dragging, snapping on', snapText);
  note('status while dragging, Alt held', freeText);
  assert.match(snapText, /snapping to 20 LDU, hold Alt for free/);
  assert.match(freeText, /Alt held/);
  assert.equal(edits().length, 1, 'one drag, one edit request');
  const movedTo = await x();
  note('x after drag (LDU)', movedTo);
  assert.ok(Number.isFinite(movedTo) && movedTo % 20 === 0 && movedTo !== 0, `snapped to a stud multiple, got ${movedTo}`);
  note('rebuild round trip for the move, as the page saw it (ms)', edits()[0].ms);
  await shot(page, '03-moved.png');

  // undo, redo
  await rebuilt(page, () => page.locator('#undo').click());
  assert.equal(await x(), 0);
  await rebuilt(page, () => page.locator('#redo').click());
  assert.equal(await x(), movedTo);

  // publish
  await page.locator('#publish').click();
  await waitStatus(page, /^Published/);
  note('publish status', await status(page));
  await shot(page, '04-published.png');
});

test('delete a selected part', async t => {
  const chat = await newChat();
  await api('POST', `/api/chats/${chat}/editor`, { name: 'delete' });
  await api('POST', `/api/chats/${chat}/editor/edits`, {
    base_revision: 1, edits: [{ op: 'add', section: 'model.ldr', part: '3001.dat', colour: 1, position: [0, -24, 0] }],
  });
  const { page } = await openEditor(t, chat);
  assert.equal(entries(await session(chat)).length, 2);
  await clickModel(page);
  await waitStatus(page, /1 selected/);
  await rebuilt(page, () => page.locator('#delete').click());
  assert.equal(entries(await session(chat)).length, 1);
  await shot(page, '05-deleted.png');
});

// Run the toolkit's own build on the saved plan, inside the container, and return the MPD it writes.
function rebuildFromPlan(chat) {
  const out = `/tmp/rebuild-${chat}.mpd`;
  docker('exec', container, 'sh', '-c',
    `cd /opt/ldraw-nova && ./ldraw-agent build /data/output/${chat}/editor/plan.json --output ${out} --force >/dev/null && cat ${out}`);
  return docker('exec', container, 'cat', out);
}

test('round trip: hand-edit the plan on disk, rebuild, reload, MPD equals a rebuild of the saved plan', async t => {
  const chat = await newChat();
  await api('POST', `/api/chats/${chat}/editor`, { name: 'roundtrip' });
  const planFile = `/data/output/${chat}/editor/plan.json`;
  const plan = JSON.parse(docker('exec', container, 'cat', planFile));
  const brick = plan.sections[0].steps[0][0];
  brick.colour = 2; brick.at = [40, 0, 20];                      // the hand edit
  plan.sections[0].steps[0].push({ id: 'hand', ref: '3001.dat', colour: 14, at: [40, -24, 20], purpose: 'added by hand' });
  const local = join(mkdtempSync(join(tmpdir(), 'plan-')), 'plan.json');
  writeFileSync(local, JSON.stringify(plan, null, 2));
  docker('cp', local, `${container}:${planFile}`);
  // The next edit rebuilds from the hand-edited plan (a harmless colour change to the new part).
  // The server notices the plan changed behind its back: the first request is a 409 that names the new revision.
  const recolour = base => api('POST', `/api/chats/${chat}/editor/edits`, {
    base_revision: base, edits: [{ op: 'recolour', id: 'hand', colour: 4 }],
  });
  const conflict = await recolour(1).then(() => null, e => e);
  assert.equal(conflict?.status, 409, 'an on-disk edit is reported as a changed plan');
  const view = await recolour(JSON.parse(/\{.*\}$/s.exec(conflict.message)[0]).detail.revision);
  const { page } = await openEditor(t, chat);                     // reload: the page shows what the server saved
  const saved = await session(chat);
  assert.equal(saved.revision, view.revision);
  assert.deepEqual(entries(saved).map(e => [e.id, e.colour]), [['base', 2], ['hand', 4]], 'the hand edit survived');
  assert.deepEqual(entries(saved)[0].at, [40, 0, 20]);
  const rebuilt = rebuildFromPlan(chat);
  assert.equal(saved.mpd, lf(rebuilt), 'the MPD the editor shows equals a rebuild from the saved plan');
  assert.equal(lf(docker('exec', container, 'cat', `/data/output/${chat}/editor/preview.mpd`)), lf(rebuilt), 'and so does preview.mpd on disk');
  assert.match(await status(page), /2 parts/);
  note('round trip', `revision ${saved.revision}, MPD identical to rebuild (${rebuilt.length} bytes)`);
});

const waitUp = async () => {
  for (let i = 0; i < 90; i++) {
    try { if ((await fetch(baseURL + '/')).ok) return; } catch { /* still down */ }
    await new Promise(r => setTimeout(r, 2000));
  }
  throw new Error('the server did not come back');
};

test('fault: kill the server during an edit, the last good plan survives a restart', async () => {
  const chat = await newChat();
  await api('POST', `/api/chats/${chat}/editor`, { name: 'fault' });
  let good = await session(chat);
  for (const delay of [50, 400, 1200]) {
    const request = api('POST', `/api/chats/${chat}/editor/edits`, {
      base_revision: good.revision, edits: [{ op: 'move', id: 'base', position: [20 * good.revision, 0, 0] }],
    }).then(() => 'answered', e => `failed (${e.message.slice(0, 40)})`);
    await new Promise(r => setTimeout(r, delay));
    docker('kill', container);
    const outcome = await request;
    docker('start', container);
    await waitUp();
    const after = await session(chat);
    const planText = docker('exec', container, 'cat', `/data/output/${chat}/editor/plan.json`);
    JSON.parse(planText);                                           // not truncated
    JSON.parse(docker('exec', container, 'cat', `/data/output/${chat}/editor/state.json`));
    assert.ok([good.revision, good.revision + 1].includes(after.revision), `revision ${good.revision} -> ${after.revision}`);
    assert.equal(after.mpd, lf(rebuildFromPlan(chat)), 'preview and plan agree after the restart');
    const x = entries(after)[0].at[0];
    assert.equal(x, after.revision === good.revision ? entries(good)[0].at[0] : 20 * good.revision);
    note(`fault, kill after ${delay} ms`, `request ${outcome}; revision ${good.revision} -> ${after.revision} (${after.revision === good.revision ? 'edit not committed, old plan intact' : 'edit committed whole'})`);
    good = after;
    // The session still takes edits.
    good = await api('POST', `/api/chats/${chat}/editor/edits`, {
      base_revision: good.revision, edits: [{ op: 'move', id: 'base', position: [0, 0, 0] }],
    });
  }
});

test('load: 2000 parts rebuild in a measured time and a drag sends no request per frame', async t => {
  const chat = await newChat();
  const layers = [];
  for (let y = 0; y < 10; y++) {
    const layer = [];
    for (let i = 0; i < 20; i++) for (let k = 0; k < 10; k++) {
      layer.push({ id: `b${y}_${i}_${k}`, ref: '3005.dat', colour: [4, 1, 2, 14, 15][(i + k + y) % 5], at: [i * 40, -24 * y, k * 20] });
    }
    layers.push(layer);
  }
  const plan = { version: 1, author: 'editor load test', sections: [{ name: 'model.ldr', description: '2000 bricks', steps: layers }] };
  const local = join(mkdtempSync(join(tmpdir(), 'big-')), 'big.json');
  writeFileSync(local, JSON.stringify(plan));
  await api('GET', `/api/chats/${chat}`);
  docker('exec', container, 'mkdir', '-p', `/data/output/${chat}`);
  docker('cp', local, `${container}:/data/output/${chat}/big.json`);
  const started = Date.now();
  const opened = await api('POST', `/api/chats/${chat}/editor`, { source: 'big.json' });
  const openMs = Date.now() - started;
  assert.equal(entries(opened).length, 2000);
  note('2000-part model: open (build) via API (ms)', openMs);

  const loadStart = Date.now();
  const { page, posts } = await openEditor(t, chat, { wait: 'Revision' });
  note('2000-part model: page load to first drawn status (ms)', Date.now() - loadStart);
  await shot(page, '06-big.png');

  await clickModel(page);
  await waitStatus(page, /1 selected/);
  let during = 0;
  const ms = await rebuilt(page, () => dragArrow(page, 'x', 20, { during: async () => { during = posts.filter(p => p.url.includes('/editor/')).length; } }));
  note('2000-part model: drag release to redrawn model (ms)', ms);
  assert.equal(during, 0, 'no editor request while the pointer was still down (no request per frame)');
  assert.equal(posts.filter(p => p.url.endsWith('/editor/edits')).length, 1, 'exactly one edit for the drag');
  note('2000-part model: server rebuild for that edit as the page saw the request (ms)', posts.find(p => p.url.endsWith('/editor/edits')).ms);
});

// ---- an agent-built plan: sub-models, repeated placements, choosing the plan --------------

// The wing sub-model is placed twice (the right one turned half a circle), a run of three bricks, and a brick.
const SHIP = { version: 1, author: 'editor test', sections: [
  { name: 'ship.ldr', description: 'Test ship - sub-models and a repeated run', steps: [
    [{ id: 'wl', ref: 'wing.ldr', colour: 15, at: [-80, 0, 0] }, { id: 'wr', ref: 'wing.ldr', colour: 15, at: [80, 0, 0], yaw: 180 }],
    [{ id: 'row', ref: '3005.dat', colour: 4, at: [0, -24, 100], repeat: { count: 3, step: [20, 0, 0] } },
     { id: 'top', ref: '3001.dat', colour: 1, at: [0, -24, -100] }]] },
  { name: 'wing.ldr', description: 'wing', steps: [[
    { id: 'w1', ref: '3020.dat', colour: 14, at: [10, 0, 20] }, { id: 'w2', ref: '3020.dat', colour: 14, at: [10, 0, 80] }]] },
] };

function putPlan(chat, name, plan) {
  const local = join(mkdtempSync(join(tmpdir(), 'ship-')), name);
  writeFileSync(local, JSON.stringify(plan, null, 2));
  docker('exec', container, 'mkdir', '-p', `/data/output/${chat}`);
  docker('cp', local, `${container}:/data/output/${chat}/${name}`);
}

// Click the middle of one part's box, as the editor drew it (key = ids from the root down, "wl/w1").
async function clickPart(page, key) {
  const at = await page.evaluate(key => {
    const node = editorState.shown.placements.find(p => p.key === key);
    const box = editorState.shown.boxes[node.ref], m = node.matrix, p = node.position;
    const c = [0, 1, 2].map(k => (box.min[k] + box.max[k]) / 2);
    const w = [0, 1, 2].map(r => m[3 * r] * c[0] + m[3 * r + 1] * c[1] + m[3 * r + 2] * c[2] + p[r]);
    const s = EditorCore.ldrawToScene(w, editorState.shown.offset.toArray());
    const rect = scene.renderer.domElement.getBoundingClientRect(), q = new THREE.Vector3(...s).project(scene.camera);
    return [rect.left + (q.x + 1) / 2 * rect.width, rect.top + (1 - q.y) / 2 * rect.height];
  }, key);
  await page.mouse.click(...at);
  await page.waitForFunction(k => editorState.selected[0] === k, key, { timeout: 5000 });
}
const selected = page => page.evaluate(() => editorState.selected.slice());
const section = (view, name) => view.plan.sections.find(s => s.name === name).steps.flat();
const entry = (view, name, id) => section(view, name).find(e => e.id === id);

test('an agent-built plan: pick it, edit inside sub-models and a repeated run, undo, publish, reload', async t => {
  const chat = await newChat();
  putPlan(chat, 'ship.plan.json', SHIP);
  const { page } = await openEditor(t, chat, { wait: 'No editor plan' });

  // The chat has a plan but no editor session: the page offers that plan, not just a blank brick.
  await page.locator('#plans button').first().waitFor();
  assert.match(await page.locator('#plans button').first().textContent(), /ship\.plan\.json/);
  assert.match(await page.locator('#start-text').textContent(), /Which model/);
  await shot(page, '10-choose-plan.png');
  await page.locator('#plans button').first().click();
  await waitStatus(page, /^Revision 1/);
  const view0 = await session(chat);
  assert.equal(view0.name, 'Test ship');
  assert.match(await status(page), /8 parts used/);

  // A part inside a sub-model is selectable, and the status says where it lives and that edits hit both copies.
  await clickPart(page, 'wl/w1');
  await waitStatus(page, /1 selected · 3020\.dat in sub-model wing\.ldr · wing\.ldr is drawn 2 times/);
  await shot(page, '11-submodel-part.png');

  // Move it one stud east: the edit lands in wing.ldr, in the wing's own frame.
  await rebuilt(page, () => dragArrow(page, 'x', 20));
  let view = await session(chat);
  assert.deepEqual(entry(view, 'wing.ldr', 'w1').at, [30, 0, 20]);
  assert.deepEqual(entry(view, 'ship.ldr', 'wl').at, [-80, 0, 0], 'the instance did not move');

  // The right wing is turned half a circle, so a world move east is a move west inside it.
  await clickPart(page, 'wr/w1');
  await rebuilt(page, () => dragArrow(page, 'x', 20));
  view = await session(chat);
  assert.deepEqual(entry(view, 'wing.ldr', 'w1').at, [10, 0, 20], 'wr/w1 moved -20 inside the turned wing');

  // Group: one level up selects the wing instance, and a drag moves the whole wing.
  await clickPart(page, 'wl/w2');
  await page.locator('#widen').click();
  assert.deepEqual(await selected(page), ['wl']);
  await waitStatus(page, /sub-model wing\.ldr \(the whole group\)/);
  await rebuilt(page, () => dragArrow(page, 'z', 20));
  view = await session(chat);
  assert.deepEqual(entry(view, 'ship.ldr', 'wl').at, [-80, 0, 20]);

  // A repeated run: one click says what it is, a drag moves the whole run (its template).
  await clickPart(page, 'row-1');
  await waitStatus(page, /one of 3 from a repeated placement/);
  await rebuilt(page, () => dragArrow(page, 'x', 20));
  view = await session(chat);
  assert.deepEqual(entry(view, 'ship.ldr', 'row').at, [20, -24, 100]);
  // Rotating it is refused in words, and nothing is sent.
  await page.locator('button[data-tool="rotate"]').click();
  const sent = view.revision;
  const ring = await page.evaluate(() => {
    const gizmo = scene.overlay.children.find(c => c.children.some(m => m.userData && m.userData.axis));
    gizmo.updateMatrixWorld(true);
    const mesh = gizmo.children.find(m => m.userData.axis === 'y' && m.geometry.type === 'TorusGeometry');
    const rect = scene.renderer.domElement.getBoundingClientRect();
    const pos = mesh.geometry.attributes.position, v = new THREE.Vector3();
    const screen = i => { v.fromBufferAttribute(pos, i).applyMatrix4(mesh.matrixWorld).project(scene.camera); return [rect.left + (v.x + 1) / 2 * rect.width, rect.top + (1 - v.y) / 2 * rect.height]; };
    return { from: screen(0), to: screen(Math.floor(pos.count / 8)) };
  });
  await page.mouse.move(...ring.from);
  await page.mouse.down();
  for (let i = 1; i <= 8; i++) await page.mouse.move(ring.from[0] + (ring.to[0] - ring.from[0]) * i / 8, ring.from[1] + (ring.to[1] - ring.from[1]) * i / 8);
  await page.mouse.up();
  await page.waitForFunction(() => /cannot be rotated/.test(document.getElementById('status').textContent));
  assert.equal((await session(chat)).revision, sent, 'a refused rotate sends nothing');
  await page.locator('button[data-tool="move"]').click();

  // Delete inside a sub-model, then undo puts it back where it was.
  await clickPart(page, 'wr/w2');
  await rebuilt(page, () => page.locator('#delete').click());
  view = await session(chat);
  assert.deepEqual(section(view, 'wing.ldr').map(e => e.id), ['w1']);
  await rebuilt(page, () => page.locator('#undo').click());
  assert.deepEqual(section(await session(chat), 'wing.ldr').map(e => e.id), ['w1', 'w2']);
  // Keyboard nudge (as in LeoCAD): ArrowRight moves the selection one stud east, PageUp one plate up.
  await clickPart(page, 'wl/w1');
  const x0 = entry(await session(chat), 'wing.ldr', 'w1').at;
  await rebuilt(page, () => page.keyboard.press('ArrowRight'));
  assert.deepEqual(entry(await session(chat), 'wing.ldr', 'w1').at, [x0[0] + 20, x0[1], x0[2]]);
  await rebuilt(page, () => page.keyboard.press('PageUp'));
  assert.deepEqual(entry(await session(chat), 'wing.ldr', 'w1').at, [x0[0] + 20, x0[1] - 8, x0[2]]);
  await shot(page, '12-edited.png');

  // Publish, reload: the model is in the chat's models and the page shows the same revision.
  const revision = (await session(chat)).revision;
  await page.locator('#publish').click();
  await waitStatus(page, /^Published/);
  const models = await api('GET', `/api/chats/${chat}`);
  assert.ok(Object.values(models.models || {}).some(m => /Test ship/.test(m.name)), JSON.stringify(models.models));
  const again = await openEditor(t, chat);
  assert.match(await status(again.page), new RegExp(`^Revision ${revision} `));
  assert.equal(await again.page.evaluate(() => editorState.shown.placements.length), 10);

  // "Open plan...": another plan from the chat's folder replaces the session, the old one is kept aside.
  putPlan(chat, 'other.plan.json', { version: 1, author: 'editor test', sections: [{ name: 'other.ldr', description: 'Other', steps: [[
    { id: 'only', ref: '3001.dat', colour: 2, at: [0, 0, 0] }]] }] });
  await again.page.locator('#open-other').click();
  await again.page.locator('#plans button', { hasText: 'other.plan.json' }).click();
  await waitStatus(again.page, /^Revision 1 · 1 part used/);
  assert.deepEqual(entries(await session(chat)).map(e => e.id), ['only']);
  const archived = docker('exec', container, 'sh', '-c', `ls /data/output/${chat}/editor/archive/*/plan.json`);
  assert.match(archived, /archive\/.*\/plan\.json/);
});

// Screen point of a point of the model (LDraw coordinates).
const spotAt = (page, world) => page.evaluate(world => {
  const s = EditorCore.ldrawToScene(world, editorState.shown.offset.toArray());
  const r = scene.renderer.domElement.getBoundingClientRect(), q = new THREE.Vector3(...s).project(scene.camera);
  return [r.left + (q.x + 1) / 2 * r.width, r.top + (1 - q.y) / 2 * r.height];
}, world);

test('overlapping parts: another click on the same spot steps to the next part; a sub-model\'s only part is deleted with its instance', async t => {
  const chat = await newChat();
  putPlan(chat, 'stack.plan.json', { version: 1, author: 'editor test', sections: [
    { name: 'stack.ldr', description: 'Stack', steps: [[
      { id: 'pod', ref: 'pod.ldr', colour: 15, at: [0, 0, 0] },
      { id: 'cap', ref: '3001.dat', colour: 4, at: [0, -24, 0] }]] },
    { name: 'pod.ldr', description: 'pod', steps: [[{ id: 'p1', ref: '3001.dat', colour: 1, at: [0, 0, 0] }]] }] });
  const { page } = await openEditor(t, chat, { wait: 'No editor plan' });
  await page.locator('#plans button').first().click();
  await waitStatus(page, /^Revision 1/);

  // The picking box of a brick is in LDraw's frame, which is ldbi's part geometry as it is: the origin is the top of
  // the body, so the body goes down to +24 LDU and the studs rise a little above 0. It must not be flipped, or the
  // selection outline would sit one brick height off the part (the canvas browser test compares both).
  const brick = await page.evaluate(() => editorState.shown.boxes['3001.dat']);
  assert.ok(brick.min[1] >= -8 && brick.min[1] <= 0 && brick.max[1] >= 20 && brick.max[1] <= 28, `3001 box ${JSON.stringify(brick)}`);

  // The cap sits on the brick: the pod's studs (y -4 to 0) are inside the cap's boxes, so that spot is under both.
  const spot = await spotAt(page, [0, -2, 0]);
  const seen = [];
  for (let i = 0; i < 3; i++) {
    await page.mouse.click(...spot);
    seen.push((await selected(page))[0]);
  }
  assert.deepEqual(seen, [seen[0], seen[1], seen[0]], `clicks cycle through the two parts: ${seen}`);
  assert.deepEqual([...seen.slice(0, 2)].sort(), ['cap', 'pod/p1']);
  assert.match(await status(page), /\d of 2 under the pointer, click again for the next/);

  // pod.ldr has one part: deleting it removes the pod instance (the sub-model stays valid), and undo brings it back.
  if ((await selected(page))[0] !== 'pod/p1') await page.mouse.click(...spot);
  assert.equal((await selected(page))[0], 'pod/p1');
  await rebuilt(page, () => page.locator('#delete').click());
  let view = await session(chat);
  assert.deepEqual(view.plan.sections[0].steps.flat().map(e => e.id), ['cap']);
  await rebuilt(page, () => page.locator('#undo').click());
  view = await session(chat);
  assert.deepEqual(view.plan.sections[0].steps.flat().map(e => e.id), ['pod', 'cap']);
});
