import init, { Player } from './player-vendor/ldraw_player.js';

const ICONS = { play: 'M8 5v14l11-7z', pause: 'M6 5h4v14H6zM14 5h4v14h-4z',
                replay: 'M12 5V1.5L7 6l5 4.5V7a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z' };
const $ = id => document.getElementById(id);

const { params, modelUrl, knownParts, HINTS, plural, statusSetter, fileNameOf } = ViewerCommon;

const setStatus = statusSetter($('status'));
const clock = s => { s = Math.max(0, Math.round(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };

let player = null;
let steps = new Float32Array();
let duration = 0;
let dragging = null;             // {wasPlaying} while scrubbing
let lastPrev = { at: 0, step: -1 };
let summary = '';                // "N parts in M steps", shown with the camera hint
let problem = null;              // a load problem, shown instead

async function main() {
  if (!modelUrl) { setStatus('No model given: add ?model=<url of an .ldr/.mpd file>', true); return; }
  const fileName = fileNameOf(modelUrl);
  $('file').textContent = fileName;
  document.title = fileName + ' · LDraw Player';
  if (Number.isFinite(knownParts)) $('parts').textContent = ', ' + plural(knownParts, 'part');

  try {
    await init();
    player = await Player.create($('canvas'), {
      libraryUrl: '/ldraw/', resolverUrl: '/ldraw-id/', background: '#f2f2f2',
      backend: params.get('backend') || undefined,
    });
  } catch (e) {
    setStatus('Could not start the 3D player: ' + (e && e.message || e), true);
    return;
  }
  document.documentElement.dataset.backend = player.backend;     // "webgpu" or "gl"
  player.onChange(update);

  let result;
  try {
    result = await player.load(modelUrl, (stage, done, total) => setStatus(
      stage === 'parts' ? `Loading parts… ${done.toLocaleString()} files` : `Preparing parts… ${done} / ${total}`));
  } catch (e) {
    setStatus(`Could not load ${fileName}: ${e && e.message || e}`, true);
    return;
  }
  if (!result) return;                          // superseded by another load
  steps = player.stepTimes();
  duration = player.duration;
  if (!Number.isFinite(knownParts)) $('parts').textContent = ', ' + plural(result.parts, 'part');
  summary = `${plural(result.parts, 'part')} in ${plural(result.steps, 'step')}`;
  if (result.missing.length) {
    const names = result.missing.slice(0, 5).join(', ') + (result.missing.length > 5 ? ', …' : '');
    problem = `${plural(result.missing.length, 'file')} not found, left out: ${names}`;
  }
  showHint();
  for (const id of ['prev', 'play', 'next', 'home']) $(id).disabled = false;
  $('scrub').setAttribute('aria-valuemax', duration.toFixed(2));
  drawMarks();
  update();
}

function showHint() {
  if (problem) setStatus(problem, true);
  else setStatus(`${summary} · ${HINTS[player.cameraMode]}`);
}

// Inspect: orbit, pan, zoom (into the model too). Walk: first person.
const cameraButtons = document.querySelectorAll('button[data-camera]');
cameraButtons.forEach(b => b.addEventListener('click', () => {
  if (!player) return;
  player.cameraMode = b.dataset.camera;
  cameraButtons.forEach(c => c.setAttribute('aria-pressed', String(c === b)));
  b.blur();                      // so Space goes back to play/pause
  if (summary) showHint();
}));

// --- playback controls ---------------------------------------------------------

function update() {
  if (!player) return;
  const t = player.time, playing = player.playing, ended = duration > 0 && t >= duration;
  const step = steps.length ? player.stepAt(t) : 0;
  $('play-icon').setAttribute('d', playing ? ICONS.pause : ended ? ICONS.replay : ICONS.play);
  const label = playing ? 'Pause' : ended ? 'Replay' : 'Play';
  $('play').setAttribute('aria-label', label);
  $('play').title = label + ' (Space)';
  $('prev').disabled = t <= 0;
  $('next').disabled = ended;
  $('time').textContent = `${clock(t)} / ${clock(duration)}`;
  $('step').textContent = steps.length ? `Step ${step + 1} / ${steps.length}` : 'Step – / –';
  const fraction = duration > 0 ? t / duration : 0;
  $('fill').style.width = `${fraction * 100}%`;
  $('thumb').style.left = `${fraction * 100}%`;
  $('scrub').setAttribute('aria-valuenow', t.toFixed(2));
  $('scrub').setAttribute('aria-valuetext', steps.length ? `Step ${step + 1} of ${steps.length}, ${clock(t)}` : clock(t));
}

function togglePlay() {
  if (!player || !duration) return;
  player.playing ? player.pause() : player.play();
}

// Like a video player's chapters: "next" jumps to the next step's start;
// "previous" to the start of the current step, or, when already there (or
// pressed again right away), to the step before.
function nextStep() {
  if (!player) return;
  const t = player.time;
  const next = steps.find(s => s > t + 1e-4);
  player.seek(next ?? duration);
}

function prevStep() {
  if (!player || !steps.length) return;
  const t = player.time, k = player.stepAt(t);
  const again = performance.now() - lastPrev.at < 800 && lastPrev.step === k;
  const target = (t - steps[k] > 1e-3 && !again) || k === 0 ? k : k - 1;
  player.seek(steps[target]);
  lastPrev = { at: performance.now(), step: target };
}

$('play').addEventListener('click', togglePlay);
$('next').addEventListener('click', nextStep);
$('prev').addEventListener('click', prevStep);
$('home').addEventListener('click', () => player && player.resetView());
$('speed').addEventListener('change', e => { if (player) player.speed = parseFloat(e.target.value); });

addEventListener('keydown', e => {
  if (e.target instanceof HTMLSelectElement || e.metaKey || e.ctrlKey || e.altKey) return;
  const keys = { ' ': togglePlay, k: togglePlay, ArrowRight: nextStep, ArrowLeft: prevStep,
                 Home: () => player && player.seek(0), End: () => player && player.seek(duration) };
  const action = keys[e.key];
  if (!action) return;
  e.preventDefault();
  if (e.target instanceof HTMLButtonElement) e.target.blur();   // Space shouldn't also "click" the focused button
  action();
});

// --- the time slider -------------------------------------------------------------

// Gaps at the step marks cut the bar into chapters; marks closer than 3 px are skipped.
function drawMarks() {
  const canvas = $('marks');
  const width = canvas.clientWidth, height = canvas.clientHeight, ratio = devicePixelRatio || 1;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!duration) return;
  ctx.fillStyle = getComputedStyle($('controls')).backgroundColor;
  let last = -Infinity;
  for (const s of steps) {
    const x = s / duration * width;
    if (x < 1 || x - last < 3) continue;
    ctx.fillRect(Math.round((x - 1) * ratio), 0, Math.max(1, Math.round(1.5 * ratio)), canvas.height);
    last = x;
  }
}
new ResizeObserver(drawMarks).observe($('scrub'));
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', drawMarks);

const timeAt = e => {
  const r = $('scrub').getBoundingClientRect();
  return Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) * duration;
};

function showTip(e) {
  if (!player || !duration) return;
  const t = timeAt(e), r = $('scrub').getBoundingClientRect();
  const tip = $('tip');
  tip.textContent = steps.length ? `Step ${player.stepAt(t) + 1} · ${clock(t)}` : clock(t);
  tip.hidden = false;
  const half = tip.offsetWidth / 2;
  tip.style.left = `${Math.min(r.width - half, Math.max(half, e.clientX - r.left))}px`;
}

$('scrub').addEventListener('pointerdown', e => {
  if (!player || !duration || e.button !== 0) return;
  $('scrub').setPointerCapture(e.pointerId);
  $('scrub').classList.add('dragging');
  dragging = { wasPlaying: player.playing };
  player.pause();
  player.seek(timeAt(e));
  showTip(e);
});
$('scrub').addEventListener('pointermove', e => {
  showTip(e);
  if (dragging) player.seek(timeAt(e));
});
const endDrag = () => {
  if (!dragging) return;
  $('scrub').classList.remove('dragging');
  if (dragging.wasPlaying && player.time < duration) player.play();
  dragging = null;
};
$('scrub').addEventListener('pointerup', endDrag);
$('scrub').addEventListener('pointercancel', endDrag);
$('scrub').addEventListener('pointerleave', () => { if (!dragging) $('tip').hidden = true; });
$('scrub').addEventListener('lostpointercapture', () => { endDrag(); $('tip').hidden = true; });
$('scrub').addEventListener('keydown', e => {
  if (!player) return;
  const small = Math.max(1, duration / 100);
  const moves = { PageUp: small * 10, PageDown: -small * 10, ArrowUp: small, ArrowDown: -small };
  if (e.key in moves) { e.preventDefault(); e.stopPropagation(); player.seek(player.time + moves[e.key]); }
});

main();
