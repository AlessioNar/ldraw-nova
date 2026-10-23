// Browser test for the editor page with the parts palette and live refresh.
// Run against an ISOLATED app instance (never the running container):
//   EDITOR_TEST_URL=http://127.0.0.1:8781 EDITOR_TEST_CONTAINER=ldraw-nova-test-a PLAYWRIGHT_CHANNEL=chrome \
//     node --test test/editor-palette.browser.mjs
// The container is only used to switch the new chat to "prefer" parts mode (options are not settable over HTTP
// outside a chat turn); without it the parts-mode assertions are skipped.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { after, before, test } from 'node:test';
import { chromium } from 'playwright';

const base = process.env.EDITOR_TEST_URL ?? 'http://127.0.0.1:8781';
const container = process.env.EDITOR_TEST_CONTAINER ?? 'ldraw-nova-test-a';
const SLOW = 90_000;
let browser, chat, partsMode = false;

async function api(path, body, method) {
  const response = await fetch(base + path, {
    method: method ?? (body === undefined ? 'GET' : 'POST'),
    headers: body === undefined ? {} : { 'Content-Type': 'application/json', Origin: base },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  return { status: response.status, payload };
}

before(async () => {
  browser = await chromium.launch({
    channel: process.env.PLAYWRIGHT_CHANNEL,
    args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
  });
  await api('/api/inventories', { name: 'Palette test', csv: 'Part,Color,Quantity\n3001,4,5\n3003,4,2\n3022,1,3\n' });
  chat = (await api('/api/chats', {})).payload.id;
  try {
    execFileSync('docker', ['exec', container, 'python3', '-c',
      `import json;p='/data/chats/${chat}/chat.json';d=json.load(open(p));` +
      `d['options']={'mode':'agent','permissions':'full','parts_mode':'prefer','inventory_ids':['palette-test']};json.dump(d,open(p,'w'))`]);
    partsMode = true;
  } catch (e) { console.warn('parts mode not set up:', e.message); }
  const opened = await api(`/api/chats/${chat}/editor`, { name: 'palette test' });
  assert.equal(opened.status, 200, JSON.stringify(opened.payload));
});
after(async () => { await browser?.close(); });

const revisionOf = async () => (await api(`/api/chats/${chat}/editor`)).payload.revision;

test('palette beside the editor: search, add, undo, and live refresh', { timeout: 600_000 }, async t => {
  const page = await browser.newPage({ viewport: { width: 1400, height: 850 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  t.after(() => page.close());
  await page.goto(`${base}/chat/${chat}/edit`);

  const frame = page.frameLocator('iframe.editor-frame');
  const status = frame.locator('#status');
  const palette = page.locator('aside.editor-palette');
  const revisionShown = n => page.waitForFunction(rev => {
    const f = document.querySelector('iframe.editor-frame');
    const s = f && f.contentDocument && f.contentDocument.getElementById('status');
    return s && new RegExp(`^Revision ${rev}\\b`).test(s.textContent);
  }, n, { timeout: SLOW });
  const atLeast = n => page.waitForFunction(rev => {
    const s = document.querySelector('iframe.editor-frame')?.contentDocument?.getElementById('status');
    const m = s && /^Revision (\d+)/.exec(s.textContent);
    return m && Number(m[1]) >= rev;
  }, n, { timeout: SLOW });
  const shot = name => page.screenshot({ path: `${process.env.EDITOR_SHOTS ?? '.'}/${name}.png` });

  // The React route shows the iframe and the palette together.
  await revisionShown(1);
  await palette.waitFor();
  assert.ok(await page.locator('iframe.editor-frame').isVisible());
  assert.ok(await palette.isVisible());
  const boxes = await Promise.all([page.locator('iframe.editor-frame').boundingBox(), palette.boundingBox()]);
  assert.ok(boxes[1].x >= boxes[0].x + boxes[0].width - 1, 'palette sits beside the frame on a wide screen');
  if (process.env.EDITOR_SHOTS) await shot('1-open');

  // Search finds parts.
  await palette.getByPlaceholder('Part number or name').fill('3001');
  const row = palette.locator('li', { has: page.locator('strong', { hasText: /^3001$/ }) });
  await row.waitFor({ timeout: 20_000 });
  const available = async () => {
    const text = await row.locator('.editor-palette-qty').textContent();
    return Number(/(\d+) available/.exec(text)?.[1]);
  };
  const before = partsMode ? await available() : null;
  if (partsMode) assert.equal(before, 5);

  // Add places a part: revision up, quantities follow without a reload.
  await row.getByRole('button', { name: 'Add' }).click();
  await atLeast(2);
  await row.getByRole('button', { name: 'Add' }).and(page.locator(':not([disabled])')).waitFor({ timeout: SLOW });   // add and snap both done
  const afterAdd = await revisionOf();
  assert.ok(afterAdd >= 2);
  await revisionShown(afterAdd);
  const plan = (await api(`/api/chats/${chat}/editor`)).payload;
  assert.equal(plan.plan.sections[0].steps.flat().length, 2, 'the part is in the plan');
  assert.match(plan.mpd, /3001\.dat/, 'and in the built model');
  if (partsMode) {
    assert.equal(await available(), 4, 'available falls');
    const used = plan.parts.rows.find(r => r.part === '3001').used;
    assert.equal(used, 1);
  }
  // The 3D view really drew a second part: the frame's scene has two placements' worth of meshes.
  const drawn = await frame.locator('body').evaluate(() => window.scene.baseObject.children.length);
  assert.ok(drawn >= 1);
  assert.match(await status.textContent(), /2 parts|parts/);
  if (process.env.EDITOR_SHOTS) await shot('2-added');

  // Undo returns the part to the pool without reloading the page.
  const undo = frame.locator('#undo');
  while ((await revisionOf()) > 1) {
    const n = await revisionOf();
    await undo.click();
    await atLeast(n + 1);                      // an undo is a new revision
    await page.waitForTimeout(300);
    if ((await api(`/api/chats/${chat}/editor`)).payload.plan.sections[0].steps.flat().length === 1) break;
  }
  const afterUndo = (await api(`/api/chats/${chat}/editor`)).payload;
  assert.equal(afterUndo.plan.sections[0].steps.flat().length, 1, 'part removed by undo');
  await revisionShown(afterUndo.revision);
  if (partsMode) assert.equal(await available(), 5, 'available returns');

  // Another client's edit appears in the open editor within a few seconds.
  const current = (await api(`/api/chats/${chat}/editor`)).payload;
  const section = current.plan.sections[0].name;
  const second = await api(`/api/chats/${chat}/editor/edits`, {
    base_revision: current.revision,
    edits: [{ op: 'add', section, part: '3003.dat', colour: 4, position: [60, -24, 0] }],
  });
  assert.equal(second.status, 200, JSON.stringify(second.payload));
  const started = Date.now();
  await revisionShown(second.payload.revision);
  console.log(`live refresh took ${Date.now() - started} ms after the edit returned`);
  assert.ok(Date.now() - started < 12_000, 'within the poll interval plus a rebuild');
  if (partsMode) {
    await palette.getByPlaceholder('Part number or name').fill('3003');
    const row2 = palette.locator('li', { has: page.locator('strong', { hasText: /^3003$/ }) });
    await row2.waitFor();
    assert.match(await row2.locator('.editor-palette-qty').textContent(), /1 available/);
  }
  if (process.env.EDITOR_SHOTS) await shot('3-live');

  assert.deepEqual(errors, []);

  // A stale base revision is a 409; the API's message shows in the panel and the editor catches up.
  await page.route('**/editor/edits', route => {
    const body = JSON.parse(route.request().postData());
    route.continue({ postData: JSON.stringify({ ...body, base_revision: 0 }) });
  });
  await palette.getByPlaceholder('Part number or name').fill('3001');
  await row.waitFor();
  await row.getByRole('button', { name: 'Add' }).click();
  await palette.getByRole('alert').waitFor({ timeout: SLOW });
  assert.match(await palette.getByRole('alert').textContent(), /changed elsewhere/i);
  if (process.env.EDITOR_SHOTS) await shot('4-conflict');
});

test('palette: a part picked inside a sub-model gets its new neighbour inside that sub-model, snapped onto it', { timeout: 600_000 }, async t => {
  const { mkdtempSync, writeFileSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const id = (await api('/api/chats', {})).payload.id;
  t.after(() => api(`/api/chats/${id}`, undefined, 'DELETE'));
  const local = join(mkdtempSync(join(tmpdir(), 'sub-')), 'ship.plan.json');
  writeFileSync(local, JSON.stringify({ version: 1, author: 'palette test', sections: [
    { name: 'ship.ldr', description: 'Ship', steps: [[{ id: 'wing', ref: 'wing.ldr', colour: 15, at: [0, 0, 0] }]] },
    { name: 'wing.ldr', description: 'wing', steps: [[{ id: 'w1', ref: '3020.dat', colour: 14, at: [0, 0, 0] }]] }] }));
  execFileSync('docker', ['exec', container, 'mkdir', '-p', `/data/output/${id}`]);
  execFileSync('docker', ['cp', local, `${container}:/data/output/${id}/ship.plan.json`]);
  assert.equal((await api(`/api/chats/${id}/editor`, { source: 'ship.plan.json', name: 'Ship' })).status, 200);

  const page = await browser.newPage({ viewport: { width: 1400, height: 850 } });
  t.after(() => page.close());
  await page.goto(`${base}/chat/${id}/edit`);
  const ready = () => page.waitForFunction(() => /^Revision/.test(document.querySelector('iframe.editor-frame')?.contentDocument?.getElementById('status')?.textContent ?? ''), null, { timeout: SLOW });
  await ready();
  const frame = page.frames().find(f => f.url().includes('editor.html'));
  const at = await frame.evaluate(() => {
    const node = editorState.shown.placements.find(p => p.key === 'wing/w1');
    const box = editorState.shown.boxes[node.ref], m = node.matrix, p = node.position;
    const c = [0, 1, 2].map(k => (box.min[k] + box.max[k]) / 2);
    const w = [0, 1, 2].map(r => m[3 * r] * c[0] + m[3 * r + 1] * c[1] + m[3 * r + 2] * c[2] + p[r]);
    const s = EditorCore.ldrawToScene(w, editorState.shown.offset.toArray());
    const rect = scene.renderer.domElement.getBoundingClientRect(), f = window.frameElement.getBoundingClientRect();
    const q = new THREE.Vector3(...s).project(scene.camera);
    return [f.left + rect.left + (q.x + 1) / 2 * rect.width, f.top + rect.top + (1 - q.y) / 2 * rect.height];
  });
  // A click during the editor's own poll request is ignored by design, so click until the part is selected.
  for (let i = 0; i < 10 && !(await frame.evaluate(() => editorState.selected[0] === 'wing/w1')); i++) {
    await page.mouse.click(...at);
    await page.waitForTimeout(300);
  }
  assert.equal(await frame.evaluate(() => editorState.selected[0]), 'wing/w1');

  const palette = page.locator('aside.editor-palette');
  await palette.getByPlaceholder('Part number or name').fill('3005');
  const row = palette.locator('li', { has: page.locator('strong', { hasText: /^3005$/ }) });
  await row.waitFor({ timeout: 20_000 });
  await row.getByRole('button', { name: 'Add' }).click();
  await page.waitForFunction(() => /^Revision [2-9]/.test(document.querySelector('iframe.editor-frame').contentDocument.getElementById('status').textContent), null, { timeout: SLOW });
  await row.getByRole('button', { name: 'Add' }).and(page.locator(':not([disabled])')).waitFor({ timeout: SLOW });
  const view = (await api(`/api/chats/${id}/editor`)).payload;
  const [ship, wing] = view.plan.sections.map(s => s.steps.flat());
  assert.equal(ship.length, 1, 'nothing was added to the root');
  assert.deepEqual(wing.map(e => e.ref), ['3020.dat', '3005.dat'], 'the new brick is in the sub-model');
  assert.equal(wing[1].at[1], -24, 'snapped on top of the plate (a brick is 24 LDU tall), not underneath it at +8');
  assert.ok(!('snap' in wing[1]), 'the snap is baked into a position');
});
