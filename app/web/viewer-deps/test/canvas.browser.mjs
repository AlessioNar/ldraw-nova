// The embeddable canvas (editor.html?embed=1) in a real browser, driven from a tiny parent page through the obd:*
// postMessage protocol of docs/plans/build-workspace/CONTRACT.md. Run against a THROWAWAY app instance:
//   docker run -d --init --name obd-ws-canvas-tests -p 127.0.0.1:8825:8000 -v obd-ws-canvas-tests-data:/data <image>
//   VIEWER_TEST_URL=http://127.0.0.1:8825 PLAYWRIGHT_CHANNEL=chrome npm run test:canvas
// The parent page is served by the test (page.route) on the app's own origin, so postMessage is same-origin.
// CANVAS_TEST_SHOTS names a screenshot folder.
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, before, test } from 'node:test';
import { chromium } from 'playwright';

const baseURL = process.env.VIEWER_TEST_URL ?? 'http://127.0.0.1:8825';
const shots = process.env.CANVAS_TEST_SHOTS ?? join(tmpdir(), 'canvas-shots');
mkdirSync(shots, { recursive: true });
let browser;
const chats = [];

before(async () => {
  browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
});
after(async () => {
  await browser?.close();
  for (const id of chats) await api('DELETE', `/api/chats/${id}`).catch(() => {});
});

async function api(method, path, body) {
  const response = await fetch(baseURL + path, {
    method, headers: { Origin: baseURL, 'content-type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  let json = null; try { json = JSON.parse(text); } catch { /* not JSON */ }
  if (!response.ok) throw Object.assign(new Error(`${method} ${path}: ${response.status} ${text.slice(0, 300)}`), { status: response.status });
  return json;
}

// A chat with an editor session of a few bricks (a row of three 2x4 bricks, and a plate on the first).
async function newModel() {
  const { id } = await api('POST', '/api/chats', {});
  chats.push(id);
  const first = await api('POST', `/api/chats/${id}/editor`, { name: 'canvas' });
  const edits = [
    { op: 'add', section: first.plan.sections[0].name, part: '3001.dat', colour: 4, position: [100, -24, 0] },
    { op: 'add', section: first.plan.sections[0].name, part: '3001.dat', colour: 1, position: [200, -24, 0] },
    { op: 'add', section: first.plan.sections[0].name, part: '3020.dat', colour: 14, position: [0, -32, 0] },
  ];
  await api('POST', `/api/chats/${id}/editor/edits`, { edits, base_revision: first.revision });
  return id;
}
const session = chat => api('GET', `/api/chats/${chat}/editor`);

const PARENT = `<!doctype html><meta charset=utf-8><body style="margin:0"><iframe id=f style="border:0;width:1000px;height:700px"></iframe>
<script>
  window.msgs = [];
  addEventListener('message', e => { if (e.origin === location.origin && e.data && String(e.data.type).startsWith('obd:')) window.msgs.push(e.data); });
  window.send = m => document.getElementById('f').contentWindow.postMessage({ type: 'obd:command', ...m }, location.origin);
  window.load = src => { document.getElementById('f').src = src; };
</script>`;

// Open the canvas in the test parent; resolves with helpers once obd:ready has arrived.
async function openCanvas(t, query, { wait = 'obd:ready' } = {}) {
  const page = await browser.newPage({ viewport: { width: 1000, height: 700 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error' && /shader|WebGL|Uncaught/i.test(m.text())) errors.push(m.text()); });
  await page.route(`${baseURL}/__parent.html`, route => route.fulfill({ contentType: 'text/html', body: PARENT }));
  await page.route(`${baseURL}/__v1.mpd`, route => route.fulfill({ contentType: 'text/plain', body: V1 }));
  t.after(async () => { await page.close(); assert.deepEqual(errors, []); });
  await page.goto(`${baseURL}/__parent.html`);
  await page.evaluate(src => window.load(src), `/viewer/editor.html?${query}`);
  const c = {
    page,
    frame: () => page.frames().find(f => f.url().includes('/viewer/editor.html')),
    send: m => page.evaluate(x => window.send(x), m),
    msgs: (type) => page.evaluate(ty => window.msgs.filter(m => !ty || m.type === ty), type),
    clear: () => page.evaluate(() => { window.msgs.length = 0; }),
    async waitFor(type, pred = () => true, timeout = 60000) {
      const start = Date.now();
      while (Date.now() - start < timeout) {
        const found = (await c.msgs(type)).find(pred);
        if (found) return found;
        await page.waitForTimeout(100);
      }
      throw new Error(`no ${type} message arrived: ${JSON.stringify(await c.msgs())}`);
    },
    state: (fn, arg) => c.frame().evaluate(fn, arg),
    shot: name => page.screenshot({ path: join(shots, name) }),
  };
  if (wait) await c.waitFor(wait, () => true, 120000);
  return c;
}

// Where (page pixels) a part's box centre is, for a click.
const screenOf = (c, key) => c.state(k => {
  const shown = editorState.shown, p = shown.byKey.get(k), box = shown.boxes[p.ref];
  const mid = [0, 1, 2].map(i => (box.min[i] + box.max[i]) / 2);
  const w = EditorCore.mulVec(p.matrix, mid).map((v, i) => v + p.position[i]);
  const s = EditorCore.ldrawToScene(w, shown.offset.toArray());
  const rect = scene.renderer.domElement.getBoundingClientRect(), v = new THREE.Vector3(...s).project(scene.camera);
  return [rect.left + (v.x + 1) / 2 * rect.width, rect.top + (1 - v.y) / 2 * rect.height];
}, key);
const keyOfEntry = (c, id) => c.state(i => editorState.shown.placements.find(p => p.entryId === i && p.leaf).key, id);
const offsetOf = async (c) => { const r = await c.page.locator('#f').boundingBox(); return r; };
async function clickPart(c, key, options) {
  const [x, y] = await screenOf(c, key);
  const f = await offsetOf(c);
  await c.page.mouse.click(f.x + x, f.y + y, options);
}

const V1 = `0 FILE v1.mpd
0 Name: v1.mpd
0 // e1: only brick
1 2 0 -24 0 1 0 0 0 1 0 0 0 1 3001.dat
`;

test('ready, selection, colour, delete, undo and redo through the protocol', async t => {
  const chat = await newModel();
  const c = await openCanvas(t, `space=chats&id=${chat}&embed=1&mode=edit&theme=dark`);
  assert.deepEqual(await c.msgs('obd:ready'), [{ type: 'obd:ready', mode: 'edit' }]);
  assert.equal(await c.page.evaluate(() => getComputedStyle(document.getElementById('f').contentDocument.getElementById('bar')).display), 'none', 'embed hides the toolbar');
  assert.ok(await c.state(() => editorState.shown.leaves.length) >= 4);
  await c.shot('01-dark.png');

  // click the first added brick: obd:selection carries the piece
  await c.clear();
  const key = await keyOfEntry(c, 'e1');
  await clickPart(c, key);
  const selection = await c.waitFor('obd:selection', m => m.pieces.length === 1);
  const piece = selection.pieces[0];
  assert.equal(piece.id, 'e1');
  assert.equal(piece.part, '3001');
  assert.equal(piece.colour, 4);
  assert.deepEqual(piece.position, [100, -24, 0]);
  assert.equal(piece.rotation.length, 9);
  // the selection outline sits ON the selected part: its world box equals the part group's world box
  const boxes = await c.state(() => {
    const shown = editorState.shown, node = shown.byKey.get(editorState.selected[0]);
    const world = o => { o.updateWorldMatrix(true, true); const b = new THREE.Box3().setFromObject(o); return [...b.min.toArray(), ...b.max.toArray()]; };
    const p = node.position, group = scene.baseObject.children[0].children.find(g => {
      const e = g.matrix.elements;
      return Math.abs(e[12] - p[0]) < 1e-3 && Math.abs(e[13] + p[1]) < 1e-3 && Math.abs(e[14] + p[2]) < 1e-3;
    });
    return { outline: world(shown.selectionBoxes), part: group && world(group) };
  });
  assert.ok(boxes.part, 'the part group was found');
  boxes.outline.forEach((v, i) => assert.ok(Math.abs(v - boxes.part[i]) < 1, `selection outline matches the part (${JSON.stringify(boxes)})`));
  assert.ok(piece.name.length > 0);

  // set-colour recolours the selection, once, and reports the change
  await c.clear();
  await c.send({ cmd: 'set-colour', colour: 2 });
  const changed = await c.waitFor('obd:changed');
  assert.ok(changed.undo_depth >= 1 && changed.redo_depth === 0);
  assert.equal((await session(chat)).plan.sections[0].steps.flat().find(e => e.id === 'e1').colour, 2);
  assert.equal((await c.waitFor('obd:selection', m => m.pieces[0]?.colour === 2)).pieces[0].colour, 2);

  // undo, redo (command and keyboard)
  await c.clear();
  await c.send({ cmd: 'undo' });
  const undone = await c.waitFor('obd:changed');
  assert.equal(undone.revision > changed.revision, true);
  assert.equal((await session(chat)).plan.sections[0].steps.flat().find(e => e.id === 'e1').colour, 4);
  await c.clear();
  await c.send({ cmd: 'redo' });
  await c.waitFor('obd:changed');
  assert.equal((await session(chat)).plan.sections[0].steps.flat().find(e => e.id === 'e1').colour, 2);

  // delete with the Delete key
  await c.clear();
  await c.frame().locator('#stage').focus();
  await c.page.keyboard.press('Delete');
  await c.waitFor('obd:changed');
  assert.equal((await session(chat)).plan.sections[0].steps.flat().some(e => e.id === 'e1'), false);
  assert.equal(await c.state(() => editorState.selected.length), 0);
  assert.equal((await c.waitFor('obd:selection', m => m.pieces.length === 0)).pieces.length, 0);
  // Ctrl+Z brings it back
  await c.clear();
  await c.page.keyboard.press('Control+z');
  await c.waitFor('obd:changed');
  assert.equal((await session(chat)).plan.sections[0].steps.flat().some(e => e.id === 'e1'), true);
});

test('tools: shortcuts report obd:tool, paint, add, delete tool, box select', async t => {
  const chat = await newModel();
  const c = await openCanvas(t, `space=chats&id=${chat}&embed=1&mode=edit`);
  await c.frame().locator('#stage').focus();
  await c.clear();
  await c.page.keyboard.press('v');
  assert.deepEqual((await c.waitFor('obd:tool')).tool, 'select');
  await c.page.keyboard.press('p');
  await c.waitFor('obd:tool', m => m.tool === 'paint');
  assert.equal(await c.state(() => editorState.tool), 'paint');

  // paint: set the colour, click a brick
  await c.send({ cmd: 'set-colour', colour: 2 });
  const key = await keyOfEntry(c, 'e3');
  await c.clear();
  await clickPart(c, key);
  await c.waitFor('obd:changed');
  assert.equal((await session(chat)).plan.sections[0].steps.flat().find(e => e.id === 'e3').colour, 2);

  // delete tool: click removes
  await c.send({ cmd: 'set-tool', tool: 'delete' });
  await c.clear();
  await clickPart(c, key);
  await c.waitFor('obd:changed');
  assert.equal((await session(chat)).plan.sections[0].steps.flat().some(e => e.id === 'e3'), false);
  await c.clear();
  await c.send({ cmd: 'undo' });
  await c.waitFor('obd:changed');

  // add: arm a part, click on a brick; the new entry is in the plan and the canvas reports it
  const before = (await session(chat)).plan.sections[0].steps.flat().length;
  await c.send({ cmd: 'add-part', part: '3001', colour: 14 });
  await c.waitFor('obd:tool', m => m.tool === 'add');
  await c.clear();
  const target = await keyOfEntry(c, 'e1');
  await clickPart(c, target);
  await c.waitFor('obd:changed');
  const after = await session(chat);
  assert.equal(after.plan.sections[0].steps.flat().length, before + 1);
  assert.equal(after.plan.sections[0].steps.flat().slice(-1)[0].colour, 14);
  await c.shot('02-added.png');

  // box select: Ctrl + drag over the whole model selects every part
  await c.send({ cmd: 'set-tool', tool: 'select' });
  await c.clear();
  const f = await offsetOf(c);
  await c.page.keyboard.down('Control');
  await c.page.mouse.move(f.x + 5, f.y + 5);
  await c.page.mouse.down();
  await c.page.mouse.move(f.x + 500, f.y + 350, { steps: 6 });
  await c.page.mouse.move(f.x + 995, f.y + 695, { steps: 6 });
  await c.page.mouse.up();
  await c.page.keyboard.up('Control');
  const all = await c.waitFor('obd:selection', m => m.pieces.length > 1);
  assert.equal(all.pieces.length, await c.state(() => editorState.shown.leaves.length));
  // Esc clears
  await c.frame().locator('#stage').focus();
  await c.page.keyboard.press('Escape');
  assert.equal((await c.waitFor('obd:selection', m => m.pieces.length === 0)).pieces.length, 0);
});

test('an edit shows at once and a refused edit rolls back with obd:error', async t => {
  const chat = await newModel();
  const c = await openCanvas(t, `space=chats&id=${chat}&embed=1`);
  const key = await keyOfEntry(c, 'e1');
  await clickPart(c, key);
  await c.waitFor('obd:selection', m => m.pieces.length === 1);
  // Delay the answer: the part must already have moved in the scene.
  await c.page.route('**/editor/edits', async route => { await new Promise(r => setTimeout(r, 1500)); route.continue(); });
  const x0 = await c.state(k => editorState.shown.byKey.get(k).position[0], key);
  await c.frame().locator('#stage').focus();
  await c.page.keyboard.press('ArrowRight');
  await c.page.waitForTimeout(300);
  assert.equal(await c.state(k => editorState.shown.byKey.get(k).position[0], key), x0 + 20, 'moved before the server answered');
  assert.equal(await c.state(k => editorState.shown.leafScene.known.get(k).group.matrix.elements[12], key), x0 + 20, 'scene matrix moved too');
  await c.waitFor('obd:changed');
  await c.page.unroute('**/editor/edits');
  // refuse the next edit: the part goes back
  await c.page.route('**/editor/edits', route => route.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ detail: { detail: 'no way' } }) }));
  await c.clear();
  await c.page.keyboard.press('ArrowRight');
  const error = await c.waitFor('obd:error');
  assert.match(error.message, /no way/);
  assert.equal(await c.state(k => editorState.shown.byKey.get(k).position[0], key), x0 + 20, 'rolled back');
  assert.deepEqual(await c.msgs('obd:changed'), []);
});

test('modes switch without reloading; read-only version view and back to the draft', async t => {
  const chat = await newModel();
  const c = await openCanvas(t, `space=chats&id=${chat}&embed=1&theme=light`);
  await c.state(() => { editorState.shown.marker = 'same'; });
  await c.clear();
  await c.send({ cmd: 'set-mode', mode: 'view' });
  assert.deepEqual((await c.waitFor('obd:mode')).mode, 'view');
  assert.equal(await c.state(() => editorState.shown.marker), 'same', 'same scene');
  // an edit in view mode is refused
  await c.send({ cmd: 'undo' });
  await c.send({ cmd: 'set-tool', tool: 'paint' });
  assert.match((await c.waitFor('obd:error')).message, /Edit mode/);
  await c.send({ cmd: 'set-mode', mode: 'edit' });
  await c.waitFor('obd:mode', m => m.mode === 'edit');

  // a saved version
  const revision = (await session(chat)).revision;
  await c.clear();
  await c.send({ cmd: 'view-version', url: '/__v1.mpd' });
  await c.page.waitForFunction(() => document.getElementById('f').contentWindow.editorState.readonly === true);
  assert.equal(await c.state(() => editorState.shown.leaves.length), 1);
  await c.shot('03-readonly.png');
  await c.send({ cmd: 'delete' });
  assert.match((await c.waitFor('obd:error')).message, /read-only/);
  await c.clear();
  await c.frame().locator('#stage').focus();
  await c.page.keyboard.press('Delete');
  await c.page.waitForTimeout(500);
  assert.deepEqual(await c.msgs('obd:changed'), []);
  assert.equal((await session(chat)).revision, revision, 'nothing was sent');
  await c.send({ cmd: 'back-to-draft' });
  await c.page.waitForFunction(() => document.getElementById('f').contentWindow.editorState.readonly === false);
  assert.ok(await c.state(() => editorState.shown.leaves.length) >= 4);
  // a foreign URL is refused
  await c.clear();
  await c.send({ cmd: 'view-version', url: 'https://example.com/x.mpd' });
  assert.match((await c.waitFor('obd:error')).message, /this site/);
});

test('bad commands answer with obd:error; Ctrl+S is forwarded; step is accepted', async t => {
  const chat = await newModel();
  const c = await openCanvas(t, `space=chats&id=${chat}&embed=1`);
  await c.clear();
  await c.send({ cmd: 'explode' });
  assert.match((await c.waitFor('obd:error')).message, /unknown command/);
  await c.send({ cmd: 'set-tool', tool: 'lasso' });
  await c.waitFor('obd:error', m => /tool/.test(m.message));
  await c.send({ cmd: 'add-part', part: '../etc', colour: 1 });
  await c.waitFor('obd:error', m => /part number/.test(m.message));
  await c.frame().locator('#stage').focus();
  await c.page.keyboard.press('Control+s');
  assert.deepEqual(await c.waitFor('obd:shortcut'), { type: 'obd:shortcut', action: 'save' });
  await c.send({ cmd: 'focus' });
  await c.send({ cmd: 'reload' });
  await c.page.waitForTimeout(500);
  assert.equal((await c.msgs('obd:error')).length, 3);
});

test('player mode steps through the build', async t => {
  const chat = await newModel();
  const c = await openCanvas(t, `space=chats&id=${chat}&embed=1&theme=dark`);
  await c.clear();
  await c.send({ cmd: 'set-mode', mode: 'player' });
  const mode = await c.waitFor('obd:mode', m => m.mode === 'player');
  assert.equal(mode.mode, 'player');
  const steps = await c.waitFor('obd:steps', () => true, 120000);
  assert.ok(steps.total >= 1);
  await c.send({ cmd: 'step', n: 1 });
  await c.waitFor('obd:steps', m => m.current === 1);
  await c.shot('04-player.png');
  await c.send({ cmd: 'set-mode', mode: 'edit' });
  await c.waitFor('obd:mode', m => m.mode === 'edit');
});
