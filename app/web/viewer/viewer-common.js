// Code shared by viewer.html, player.html and glb-view.js. Plain script, no
// build step: load it before the page's own script (and before glb-view.js).
/* global THREE */
var ViewerCommon = (function () {
  const params = new URLSearchParams(location.search);
  const modelUrl = params.get('model');
  const knownParts = params.has('parts') ? parseInt(params.get('parts'), 10) : NaN;
  const theme = params.get('theme');
  if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme;

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

  // Poly mode draws flat wireframes on a light background: pull near-white
  // colours down so their lines don't vanish. Mutates and returns the colour.
  function polyColor(color) {
    const hsl = color.getHSL({});
    if (hsl.l > 0.82) color.setHSL(hsl.h, hsl.s, 0.62);
    return color;
  }

  return { params, modelUrl, knownParts, HINTS, plural, statusSetter, fileNameOf, polyColor };
})();
