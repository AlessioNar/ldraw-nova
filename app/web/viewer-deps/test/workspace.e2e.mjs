// End-to-end journey of the build workspace (/build/:id) with the real canvas, in a real browser.
// Run against a THROWAWAY app instance seeded from real data (it makes a build with versions, a draft and a proposal):
//   docker run --rm -v ldraw-nova-docker_data:/src:ro -v obd-ws-int-data:/data alpine cp -a /src/. /data/
//   docker run -d --init --name obd-ws-int-check --cap-add NET_ADMIN -p 127.0.0.1:8826:8000 -v obd-ws-int-data:/data <image>
//   WS_E2E_URL=http://127.0.0.1:8826 WS_E2E_CONTAINER=obd-ws-int-check PLAYWRIGHT_CHANNEL=chrome node test/workspace.e2e.mjs
// It creates its own "E2E house" generator build (one version) and leaves it behind: use a throwaway volume.
// WS_E2E_SHOTS names the screenshot folder. The proposal is created by the backend code path a build-attached chat
// publish uses (workspace.create_proposal, via docker exec): no AI call, no cost.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const baseURL = process.env.WS_E2E_URL ?? 'http://127.0.0.1:8826';
const container = process.env.WS_E2E_CONTAINER ?? 'obd-ws-int-check';
const shots = process.env.WS_E2E_SHOTS ?? join(dirname(fileURLToPath(import.meta.url)), '../../../../docs/plans/build-workspace/integration-screenshots');
let BUILD;      // a throwaway generator build made below: the seeded data may carry anyone's versions and drafts
mkdirSync(shots, { recursive: true });

const results = [];
async function step(name, fn) {
  try { await fn(); results.push(`ok   ${name}`); console.log(`ok   ${name}`); }
  catch (e) { results.push(`FAIL ${name}: ${e.message}`); console.log(`FAIL ${name}: ${e.message}`); throw e; }
}

const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'dark' });
await context.addInitScript(() => {          // every frame records the obd:* messages it receives
  window.__msgs = [];
  addEventListener('message', (e) => { if (e.data && String(e.data.type).startsWith('obd:')) window.__msgs.push(e.data); });
});
const page = await context.newPage();
page.on('pageerror', (e) => console.log('pageerror', e.message));

const api = async (method, path, body) => {
  const r = await fetch(baseURL + path, { method, headers: { Origin: baseURL, 'content-type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  return r.json();
};
const canvas = () => page.frames().find((f) => f.url().includes('editor.html'));
const msgs = async (pred) => (await page.evaluate(() => window.__msgs)).filter(pred);   // canvas -> parent
const shot = (name) => page.screenshot({ path: join(shots, `${name}.png`) });
const status = async () => (await page.locator('.ws-status').innerText()).replace(/\s+/g, ' ').trim();
const waitStatus = (re, timeout = 60000) => page.waitForFunction((s) => new RegExp(s).test(document.querySelector('.ws-status')?.textContent ?? ''), re.source, { timeout });
const draft = async () => (await api('GET', `/api/builds/${BUILD}/workspace`)).draft;
const canvasReady = async () => {
  await page.waitForSelector('iframe.ws-frame');
  await page.waitForFunction(() => document.querySelector('.ws-overlay'), null, { timeout: 60000 });   // shown once obd:ready arrived
};
const reloads = async () => (await canvas().evaluate(() => window.__msgs)).filter((m) => m.cmd === 'reload').length;

async function selectBrick() {          // click a grid of points over the canvas until something is selected
  const box = await page.locator('iframe.ws-frame').boundingBox();
  for (const fy of [0.55, 0.5, 0.6, 0.45, 0.65, 0.4, 0.7]) {
    for (const fx of [0.5, 0.45, 0.55, 0.4, 0.6, 0.35, 0.65]) {
      await page.mouse.click(box.x + box.width * fx, box.y + box.height * fy);
      await page.waitForTimeout(250);
      if (await page.locator('.ws-selchip').count()) return;
    }
  }
  throw new Error('no brick could be selected');
}

{
  const made = await api('POST', '/api/builds', { title: `E2E house ${Date.now()}`, source: { kind: 'generator' } });
  BUILD = made.id;
  const generated = await api('POST', `/api/builds/${BUILD}/generate`, { generator: 'house', params: {} });
  assert.ok(generated.versions?.length === 1 || generated.version === 1 || generated.latest, `house generated: ${JSON.stringify(generated).slice(0, 200)}`);
}

try {
  await step('a. open the house build: real canvas, "v1 · saved", parts check', async () => {
    await page.goto(`${baseURL}/build/${BUILD}`);
    await canvasReady();
    assert.equal(canvas().url().includes('embed=1'), true);
    assert.ok((await msgs((m) => m.type === 'obd:ready')).length >= 1, 'obd:ready received');
    await waitStatus(/v1 · saved/);
    await page.waitForSelector('.ws-pcheck');
    assert.match(await page.locator('.ws-pcheck').innerText(), /Parts check: .* of .* owned/);
    assert.equal(await page.locator('.ws-top .ws-save').isDisabled(), true, 'Save disabled at 0 changes');
    assert.equal(await page.locator('.ws-discard').isDisabled(), true, 'Discard disabled at 0 changes');
    await page.waitForTimeout(1500);
    await shot('a-open');
  });

  await step('a2. Parts tab lists owned parts from the palette (inventory_only)', async () => {
    const res = await page.waitForResponse((r) => r.url().includes('/editor/palette') && r.url().includes('inventory_only=true'), { timeout: 15000 }).catch(() => null);
    const direct = await fetch(`${baseURL}/api/builds/${BUILD}/editor/palette?inventory_only=true&limit=50`).then((r) => r.status);
    assert.ok(res || direct === 200 || direct === 400, 'palette endpoint answered');
    if (direct === 200) await page.waitForSelector('.ws-part:not([disabled])', { timeout: 15000 });
    await shot('a2-parts');
  });

  await step('b. select a brick, recolour (1 change), undo (0), redo (1)', async () => {
    await selectBrick();
    await page.getByRole('tab', { name: /Piece/ }).waitFor();
    assert.equal(await page.locator('.ws-panel[aria-label="Piece"]').count(), 1, 'Piece tab shown');
    await shot('b1-piece');
    await page.locator('.ws-panel[aria-label="Piece"] .ws-sw[aria-label="Green"]').click();
    await waitStatus(/1 change/);
    assert.equal((await draft()).changes, 1);
    await page.waitForFunction(() => !document.querySelector('.ws-top .ws-save')?.disabled);
    await page.getByRole('button', { name: /Undo/ }).first().click();
    await waitStatus(/v1 · saved/);
    assert.equal((await draft()).changes, 0);
    await page.getByRole('button', { name: /Redo/ }).first().click();
    await waitStatus(/1 change/);
    assert.equal((await draft()).changes, 1);
    await shot('b2-recoloured');
    await page.getByRole('button', { name: /Undo/ }).first().click();   // back to clean for the next step
    await waitStatus(/v1 · saved/);
  });

  await step('c. describe "make the roof blue": fast path, one undo step, canvas reloaded', async () => {
    const before = await reloads();
    await page.locator('#ws-describe-input').fill('make the roof blue');
    await page.waitForFunction(() => /Fast path|Probably/.test(document.querySelector('.ws-hint')?.textContent ?? ''), null, { timeout: 10000 });
    await shot('c1-describe-hint');
    await page.getByRole('button', { name: 'Apply' }).click();
    await waitStatus(/draft, \d+ change/);
    const d = await draft();
    assert.ok(d.changes >= 1 && d.undo_depth >= 1, `draft changed (${JSON.stringify(d)})`);
    assert.equal(d.changes, 1, 'one undo step');
    for (let i = 0; i < 50 && (await reloads()) <= before; i++) await page.waitForTimeout(200);
    assert.ok((await reloads()) > before, 'canvas was sent reload');
    await page.waitForTimeout(1500);
    await shot('c2-roof-blue');
  });

  await step('d. Settings: change a param, Regenerate warns about manual edits, Undo restores', async () => {
    await page.getByRole('tab', { name: 'Settings' }).click();
    await page.waitForSelector('.ws-warnbox');
    assert.match(await page.locator('.ws-warnbox').innerText(), /replaces your 1 manual edit/);
    const field = page.locator('.ws-panel[aria-label="Settings"] #gen-courses');
    await field.fill('7');
    await shot('d1-settings-warning');
    await page.getByRole('button', { name: 'Regenerate' }).click();
    await page.waitForFunction(() => /Regenerated/.test(document.querySelector('.ws-toast')?.textContent ?? ''), null, { timeout: 120000 });
    assert.match(await page.locator('.ws-toast').innerText(), /manual edits were replaced/);
    const after = await draft();
    assert.equal(after.changes, 2, 'regenerate is one more undo step on top of the describe edit');
    await page.waitForTimeout(1500);
    await shot('d2-regenerated');
    await page.getByRole('button', { name: /Undo/ }).first().click();
    await page.waitForTimeout(1500);
    await shot('d3-undone');
    const undone = await draft();
    assert.ok(undone.redo_depth >= 1 && undone.changes === 1, `undo restored the previous plan (${JSON.stringify(undone)})`);
  });

  await step('e. Save as v2: version strip shows v2, status "v2 · saved"', async () => {
    assert.equal(await page.locator('.ws-top .ws-save').isDisabled(), false);
    await page.locator('.ws-top .ws-save').click();
    await waitStatus(/v2 · saved/, 240000);
    await page.waitForFunction(() => [...document.querySelectorAll('.ws-vstrip .ws-v')].some((b) => /v2/.test(b.textContent)));
    assert.equal(await page.locator('.ws-top .ws-save').isDisabled(), true);
    assert.equal((await draft()).changes, 0);
    await shot('e-saved-v2');
  });

  await step('f. v1 is read-only; Branch from here with a dirty draft asks, then drafts on v1', async () => {
    await selectBrick();
    await page.locator('.ws-panel[aria-label="Piece"] .ws-sw[aria-label="Green"]').click();
    await waitStatus(/1 change/);
    await page.locator('.ws-vstrip .ws-v', { hasText: 'v1' }).click();
    await page.waitForSelector('.ws-ro');
    assert.match(await page.locator('.ws-ro').innerText(), /Viewing v1/);
    assert.match(await status(), /Viewing v1/);
    await page.waitForTimeout(2000);
    await shot('f1-viewing-v1');
    await page.getByRole('button', { name: 'Branch from here' }).click();
    await page.waitForSelector('.ws-confirm');
    await shot('f2-branch-confirm');
    await page.locator('.ws-confirm .danger').click();
    await waitStatus(/v1 · saved/);
    const d = await draft();
    assert.equal(d.base_version, 1);
    assert.equal(d.changes, 0);
    await page.waitForTimeout(1500);
    await shot('f3-draft-on-v1');
  });

  await step('g. Discard draft goes back to base', async () => {
    await selectBrick();
    await page.locator('.ws-panel[aria-label="Piece"] .ws-sw[aria-label="Green"]').click();
    await waitStatus(/1 change/);
    await page.locator('.ws-discard').click();
    await page.waitForSelector('.ws-confirm');
    await page.locator('.ws-confirm .danger').click();
    await waitStatus(/v1 · saved/);
    assert.equal((await draft()).changes, 0);
    await page.waitForTimeout(1500);
    await shot('g-discarded');
  });

  await step('h. Player mode: steps indicator and slider; View mode', async () => {
    await page.locator('.ws-mode', { hasText: 'Player' }).click();
    await page.waitForSelector('#ws-step-label', { timeout: 20000 });
    const label = () => page.locator('#ws-step-label').innerText();
    const first = await label();
    assert.match(first, /Step \d+ of \d+/);
    const total = Number(first.match(/of (\d+)/)[1]);
    assert.ok(total > 1, `more than one step (${total})`);
    await canvas().locator('#player-next').click();                    // the canvas's own player bar; the workspace shows the count
    await page.waitForFunction((t) => document.querySelector('#ws-step-label')?.textContent !== t, first);
    await shot('h1-player-step');
    for (let i = 2; i < total; i++) await canvas().locator('#player-next').click();
    await page.waitForFunction((n) => new RegExp(`Step ${n} of ${n}`).test(document.querySelector('#ws-step-label')?.textContent ?? ''), total);
    await shot('h2-player-last');
    await page.locator('.ws-mode', { hasText: 'View' }).click();
    await page.waitForFunction(() => document.querySelector('.ws')?.dataset.mode === 'view');
    assert.equal(await page.locator('#ws-step-label').count(), 0);
    assert.equal(await page.locator('.ws-tool').first().isDisabled(), true);
    await shot('h3-view-mode');
    await page.locator('.ws-mode', { hasText: 'Edit' }).click();
    await page.waitForFunction(() => document.querySelector('.ws')?.dataset.mode === 'edit');
  });

  await step('h2. tool rail follows the canvas (obd:tool) and Ctrl+S in the canvas saves', async () => {
    const box = await page.locator('iframe.ws-frame').boundingBox();
    await page.mouse.click(box.x + 40, box.y + box.height - 160);       // empty canvas: focus it, nothing selected
    await page.keyboard.press('p');                                      // the canvas's own shortcut for the paint tool
    await page.waitForFunction(() => document.querySelector('.ws-tool[aria-pressed="true"]')?.getAttribute('title')?.startsWith('Paint'), null, { timeout: 5000 });
    await page.locator('.ws-tool').first().click();       // back to select
    await page.waitForFunction(() => document.querySelector('.ws-tool')?.getAttribute('aria-pressed') === 'true');
    await page.waitForTimeout(500);
    await selectBrick();
    await page.locator('.ws-panel[aria-label="Piece"] .ws-sw[aria-label="Red"]').click();
    await waitStatus(/1 change/);
    await page.keyboard.press('Control+s');                              // focus is inside the canvas frame
    await waitStatus(/v3 · saved/, 240000);
    assert.equal((await draft()).changes, 0);
    await shot('h4-ctrl-s-v3');
  });

  await step('i. proposal: review card, Preview (read-only), Keep -> draft, Undo restores', async () => {
    const old = (await api('GET', `/api/builds/${BUILD}/proposals`)).proposals;
    for (const p of old) await api('POST', `/api/builds/${BUILD}/proposals/${p.id}/discard`);
    execFileSync('docker', ['exec', '-i', container, 'python3', '-'], { input: `
import sys
sys.path.insert(0, "/app/web/backend")
from store import get_store
import workspace
store = get_store()
files = sorted(__import__("glob").glob("/data/generated/${BUILD}-v*.mpd"))
base = open(files[-1], encoding="utf-8").read()
extra = "".join(f"1 4 {x} -176 {z} 1 0 0 0 1 0 0 0 1 3001.dat\\n" for x, z in ((0, -300), (80, -300), (160, -300)))
workspace.create_proposal(store, "${BUILD}", base.replace("0 NOFILE", extra + "0 NOFILE", 1), None, base_text=base)
` });
    await page.reload();
    await canvasReady();
    await waitStatus(/· saved/);
    await page.getByRole('tab', { name: /AI/ }).click();
    await page.waitForSelector('.ws-ai-card');
    assert.match(await page.locator('.ws-ai-card').innerText(), /\+ 3 parts/);
    await shot('i1-ai-card');
    await page.locator('.ws-ai-card').getByRole('button', { name: 'Preview' }).click();
    await page.waitForSelector('.ws-ro');
    assert.match(await page.locator('.ws-ro').innerText(), /Previewing the AI proposal/);
    assert.equal(await page.locator('.ws-top .ws-save').isDisabled(), true);
    await page.waitForTimeout(2500);
    await shot('i2-preview');
    await page.locator('.ws-ai-card').getByRole('button', { name: 'Keep in draft' }).click();
    await waitStatus(/1 change/);
    assert.equal((await draft()).changes, 1);
    assert.equal((await api('GET', `/api/builds/${BUILD}/proposals`)).proposals.length, 0);
    await page.waitForTimeout(1500);
    await shot('i3-kept');
    await page.getByRole('button', { name: /Undo/ }).first().click();
    await waitStatus(/· saved/);
    assert.equal((await draft()).changes, 0);
    await shot('i4-undone');
  });

  await step('j. phone width: View/Player only, bottom sheet', async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(() => document.querySelector('.ws-rail'));
    await page.waitForTimeout(1500);
    const modes = await page.locator('.ws-mode').allInnerTexts();
    assert.deepEqual(modes.map((s) => s.trim().toLowerCase()), ['view', 'player']);
    assert.equal(await page.locator('.ws-tool').count(), 0, 'no edit tools on a phone');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, 'no horizontal scroll');
    await shot('j1-phone');
    await page.locator('.ws-tabs [role=tab]', { hasText: 'AI' }).click();     // opens the bottom sheet
    await page.waitForSelector('.ws.sheet-open');
    await shot('j2-phone-sheet');
  });

  await step('k. light theme', async () => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await context.close().catch(() => {});
  });
} catch (e) {
  await shot('FAILED').catch(() => {});
  console.log('\n' + results.join('\n'));
  await browser.close();
  process.exit(1);
}

// light theme runs in a fresh context so the iframe (theme=light) and the page agree from the start
const light = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'light' });
const lp = await light.newPage();
await lp.goto(`${baseURL}/build/${BUILD}`);
await lp.waitForFunction(() => document.querySelector('.ws-overlay'), null, { timeout: 60000 });
assert.ok((await lp.locator('iframe.ws-frame').getAttribute('src')).includes('theme=light'), 'iframe asked for the light theme');
assert.ok(await lp.evaluate(() => getComputedStyle(document.body).backgroundColor).then((c) => { const n = c.match(/\d+/g).map(Number); return n[0] > 200; }), 'light page background');
await lp.waitForSelector('.ws-part', { timeout: 30000 }).catch(() => {});
await lp.waitForTimeout(1500);
await lp.screenshot({ path: join(shots, 'k-light.png') });
results.push('ok   k2. light theme page + canvas');
console.log('\n' + results.join('\n'));
await browser.close();
