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
