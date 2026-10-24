// Code shared by viewer.html, player.html and glb-view.js. Plain script, no
// build step: load it before the page's own script (and before glb-view.js).
/* global THREE */
var ViewerCommon = (function () {
  const params = new URLSearchParams(location.search);
  const modelUrl = params.get('model');
  const knownParts = params.has('parts') ? parseInt(params.get('parts'), 10) : NaN;
  const theme = params.get('theme');
  if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme;

  // The 3D stage's backdrop. It follows the page theme only when one is asked for (?theme=); without it the stage stays
  // light, as it always was. Dark is the app's canvas colour, so a dark page gets a dark stage rather than a grey slab.
  const darkStage = theme === 'dark';
  const STAGE = { light: 0xf2f2f2, dark: 0x0e1a2e };
  const stageColor = darkStage ? STAGE.dark : STAGE.light;
  const stageCss = '#' + stageColor.toString(16).padStart(6, '0');

  // The same two camera modes in both pages.
  const HINTS = {
    inspect: 'Drag to rotate · Shift+drag to pan · Scroll to zoom, and keep scrolling to go inside',
    walk: 'Click, then move the mouse to look around (Esc frees it) · W A S D to move · E / Q up and down · Shift to run · Scroll to fly',
  };

  const plural = (n, word) => `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`;

  // Writes into the #status element; errors get the .error class.
  function statusSetter(element) {
    return (text, isError) => { element.textContent = text; element.classList.toggle('error', !!isError); };
  }

  const fileNameOf = url => decodeURIComponent(url.split('/').pop().split('?')[0]);

  // Poly mode draws flat wireframes on the stage: pull colours that match the backdrop away from it (near-white on the
  // light stage, near-black on the dark one) so their lines don't vanish. Mutates and returns the colour.
  function polyColor(color, dark = darkStage) {
    const hsl = color.getHSL({});
    if (dark) { if (hsl.l < 0.28) color.setHSL(hsl.h, hsl.s, 0.58); }
    else if (hsl.l > 0.82) color.setHSL(hsl.h, hsl.s, 0.62);
    return color;
  }

  return { params, modelUrl, knownParts, HINTS, plural, statusSetter, fileNameOf, polyColor, darkStage, stageColor, stageCss };
})();
